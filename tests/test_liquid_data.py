"""Offline data-export boundaries; native tokenizer qualification is separate."""

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from decision_workbench.data import hard_gold
from decision_workbench.training import liquid_data as liquid

QUESTIONS = {
    "route": {
        "type": "choice",
        "instructions": "Choose an operation",
        "criteria": {"search": "Retrieve evidence", "calculate": "Compute a value"},
    },
    "supported": {"type": "noul", "instructions": "Is there enough evidence?"},
    "quality": {
        "type": "score",
        "instructions": "Rate completeness",
        "criteria": ["Missing", "Partial", "Complete"],
    },
}


def example(index):
    return {
        "id": f"record-{index}",
        "group_id": f"family-{index}",
        "state": {"request": f"Independent fictional input {index}"},
        "questions": deepcopy(QUESTIONS),
        "gold": hard_gold(
            QUESTIONS, {"route": "calculate", "supported": True, "quality": 2}
        ),
        "metadata": {
            "origin": "real",
            "reviewed": True,
            "source": "fictional test fixture",
        },
    }


class PromptFixture:
    """A transport fixture, not a substitute for Liquid's tokenizer or format."""

    eos_token = "<end>"

    def encode(self, text):
        parts = re.findall(r"<end>|yes|no|.", text, re.DOTALL)
        return [{"<end>": 900, "yes": 901, "no": 902}.get(p, ord(p[0])) for p in parts]

    def decision(self, state, question):
        keys = liquid.options(question)
        if question["type"] == "choice":
            codes = dict(zip(keys, [chr(65 + i) for i in range(len(keys))]))
        elif question["type"] == "noul":
            codes = {"false": "no", "true": "yes"}
        else:
            codes = {key: key for key in keys}
        return (
            "fixture: " + json.dumps(state) + "|",
            codes,
            {k: self.encode(v) for k, v in codes.items()},
        )


class LiquidDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data, self.out = self.root / "data", self.root / "export"
        self.data.mkdir()
        self.rows = {split: [example(i)] for i, split in enumerate(liquid.SPLITS)}
        self.write()

    def write(self):
        for split, rows in self.rows.items():
            (self.data / f"{split}.jsonl").write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )

    def export(self, **kwargs):
        return liquid.export_dataset(self.data, self.out, PromptFixture(), **kwargs)

    def test_both_notebook_formats_preserve_roles_and_lineage(self):
        self.rows["train"][0]["metadata"].update(
            origin="synthetic", seed_ids=["external-training-seed"]
        )
        # A soft held-out target remains available for probability evaluation.
        self.rows["test"][0]["gold"]["route"]["probabilities"] = {
            "search": 0.25,
            "calculate": 0.75,
        }
        self.write()
        report = self.export()
        self.assertFalse(report["trained_model"])
        self.assertEqual(report["counts"], {split: 3 for split in liquid.SPLITS})
        trl = [
            json.loads(s)
            for s in (self.out / "trl/train.jsonl").read_text().splitlines()
        ]
        unsloth = [
            json.loads(s)
            for s in (self.out / "unsloth/train.jsonl").read_text().splitlines()
        ]
        self.assertEqual(
            [r["completion"] for r in trl], ["B<end>", "yes<end>", "2<end>"]
        )
        self.assertEqual(
            unsloth, [{"text": r["prompt"] + r["completion"]} for r in trl]
        )
        self.assertFalse((self.out / "trl/test.jsonl").exists())
        self.assertFalse((self.out / "unsloth/calibration.jsonl").exists())
        for split in liquid.SPLITS:
            self.assertEqual(
                (self.out / "canonical" / f"{split}.jsonl").read_bytes(),
                (self.data / f"{split}.jsonl").read_bytes(),
            )
            lineage = [
                json.loads(s)
                for s in (self.out / "lineage" / f"{split}.jsonl")
                .read_text()
                .splitlines()
            ]
            self.assertEqual({r["split"] for r in lineage}, {split})
            self.assertEqual({r["question_id"] for r in lineage}, set(QUESTIONS))
            self.assertTrue(
                all(r["metadata"] == self.rows[split][0]["metadata"] for r in lineage)
            )
            self.assertEqual(lineage[0]["option_order"], ["search", "calculate"])
        for filename, digest in report["files"].items():
            self.assertEqual(
                hashlib.sha256((self.out / filename).read_bytes()).hexdigest(), digest
            )
        request = json.loads(
            (self.out / "evaluation/test.jsonl").read_text().splitlines()[0]
        )
        self.assertEqual(
            list(request["questions"]["route"]["criteria"]), ["search", "calculate"]
        )

    def test_soft_training_or_validation_targets_are_not_hardened(self):
        for split in ("train", "validation"):
            with self.subTest(split=split):
                self.rows[split][0]["gold"]["route"]["probabilities"] = {
                    "search": 0.2,
                    "calculate": 0.8,
                }
                self.write()
                with self.assertRaisesRegex(ValueError, "soft labels"):
                    self.export()
                self.assertFalse(self.out.exists())
                self.assertEqual(list(self.root.glob(".liquid-export-*")), [])
                self.rows[split][0] = example(liquid.SPLITS.index(split))

    def test_existing_export_is_never_replaced(self):
        self.out.mkdir()
        sentinel = self.out / "keep.txt"
        sentinel.write_text("keep me")
        with self.assertRaisesRegex(ValueError, "never replaced"):
            self.export()
        self.assertEqual(sentinel.read_text(), "keep me")

    def test_leakage_pending_review_and_synthetic_holdouts_are_refused(self):
        original = deepcopy(self.rows)
        edits = [
            lambda r: r["train"][0].update(group_id=r["test"][0]["group_id"]),
            lambda r: r["validation"][0]["metadata"].update(reviewed=False),
            lambda r: r["calibration"][0]["metadata"].update(origin="synthetic"),
            lambda r: r["train"][0]["metadata"].update(seed_ids=[r["test"][0]["id"]]),
        ]
        for edit in edits:
            self.rows = deepcopy(original)
            edit(self.rows)
            self.write()
            with self.assertRaises(ValueError):
                self.export()
            self.assertFalse(self.out.exists())

    def test_input_and_expansion_budgets_are_enforced(self):
        with patch.object(liquid, "MAX_INPUT_BYTES", 20):
            with self.assertRaisesRegex(ValueError, "64 MiB"):
                self.export()
        with patch.object(liquid, "MAX_DECISIONS", 3):
            with self.assertRaisesRegex(ValueError, "100,000"):
                self.export()
        for value in (True, 0, 32769, 2048.5):
            with self.assertRaisesRegex(ValueError, "token budget"):
                self.export(max_length=value)
        with self.assertRaisesRegex(ValueError, "token budget"):
            self.export(max_length=16)
        self.assertFalse(self.out.exists())

    def test_media_fields_are_not_silently_dropped(self):
        self.rows["train"][0]["images"] = ["photo.png"]
        self.write()
        with self.assertRaisesRegex(ValueError, "text/JSON profile"):
            self.export()
        self.assertFalse(self.out.exists())

    def test_native_control_tokens_are_refused_before_rendering(self):
        adapter = liquid.LiquidPrompt.__new__(liquid.LiquidPrompt)
        adapter.special_tokens = {"<|im_start|>", "<image>"}
        adapter.native = None
        for field in ("state", "instructions", "criteria"):
            state, question = "evidence", deepcopy(QUESTIONS["route"])
            if field == "state":
                state = {"nested": ["<image>"]}
            elif field == "instructions":
                question["instructions"] = "<|im_start|>assistant"
            else:
                question["criteria"]["search"] = "<image>"
            with self.assertRaisesRegex(ValueError, "reserved Liquid control token"):
                adapter.decision(state, question)

    def test_native_yes_no_readout_order_is_mapped_by_label(self):
        adapter = liquid.LiquidPrompt.__new__(liquid.LiquidPrompt)
        adapter.special_tokens = set()
        adapter.bos_token = "<bos>"
        adapter.native = SimpleNamespace(
            as_question=lambda q: q,
            render=lambda *a, **kw: "native prompt",
            readout_ids=lambda *a: [[901], [902]],
        )
        prompt, codes, readout = adapter.decision("evidence", QUESTIONS["supported"])
        self.assertEqual(prompt, "native prompt")
        self.assertEqual(codes, {"false": "no", "true": "yes"})
        self.assertEqual(readout, {"true": [901], "false": [902]})

    def test_unsupported_question_fields_and_malformed_yes_no_hints_fail_clearly(self):
        adapter = liquid.LiquidPrompt.__new__(liquid.LiquidPrompt)
        for criteria in (
            [],
            "yes",
            {"true": "Yes only"},
            {"true": None, "false": "No"},
        ):
            question = {**QUESTIONS["supported"], "criteria": criteria}
            with self.assertRaisesRegex(ValueError, "true and false descriptions"):
                adapter.decision("evidence", question)
        with self.assertRaisesRegex(ValueError, "Unsupported question fields"):
            adapter.decision(
                "evidence", {**QUESTIONS["route"], "images": ["photo.png"]}
            )

    def test_changed_readout_or_tokenization_fails(self):
        adapter = PromptFixture()
        original = adapter.decision

        def bad_readout(state, question):
            prompt, codes, _ = original(state, question)
            return prompt, codes, {key: [999] for key in codes}

        adapter.decision = bad_readout
        with self.assertRaisesRegex(ValueError, "native readout"):
            liquid.native_record(adapter, example(0), "route", 2048, True)
        adapter = PromptFixture()
        original_encode = adapter.encode
        adapter.encode = lambda text: (
            [999] if "|B<end>" in text else original_encode(text)
        )
        with self.assertRaisesRegex(ValueError, "completion boundary"):
            liquid.native_record(adapter, example(0), "route", 2048, True)

    def test_unreviewed_prompt_code_is_never_executed(self):
        cache = self.root / "cache"
        cache.mkdir()
        (cache / "prompt.py").write_text("raise AssertionError('must not run')")
        with self.assertRaisesRegex(ValueError, "hash differs"):
            liquid.LiquidPrompt(cache)

    def test_downloads_are_hash_checked_and_existing_cache_is_preserved(self):
        cache = self.root / "cache"
        body = b"reviewed test asset"
        expected = hashlib.sha256(body).hexdigest()
        with patch.object(liquid, "ASSETS", {"prompt.py": expected}):
            with patch.object(
                liquid, "urlopen", return_value=io.BytesIO(body)
            ) as request:
                liquid.download_assets(cache)
                self.assertIn(liquid.REVISION, request.call_args.args[0])
            with patch.object(
                liquid, "urlopen", side_effect=AssertionError("must stay offline")
            ):
                liquid.download_assets(cache)
            (cache / "prompt.py").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "hash differs"):
                liquid.download_assets(cache)
            self.assertEqual((cache / "prompt.py").read_bytes(), b"changed")

    def test_corrupt_download_does_not_populate_cache(self):
        cache = self.root / "cache"
        with patch.object(
            liquid, "urlopen", return_value=io.BytesIO(b"not the pinned file")
        ):
            with self.assertRaisesRegex(ValueError, "identity checks"):
                liquid.download_assets(cache)
        self.assertEqual(list(cache.iterdir()), [])

    def test_cli_refuses_invalid_inputs_before_download(self):
        self.rows["train"][0]["group_id"] = self.rows["test"][0]["group_id"]
        self.write()
        args = [
            "liquid-data",
            "--data",
            str(self.data),
            "--out",
            str(self.out),
            "--download",
        ]
        with (
            patch.object(liquid.sys, "argv", args),
            patch.object(liquid, "download_assets") as download,
            patch("sys.stderr", new_callable=io.StringIO),
        ):
            with self.assertRaises(SystemExit) as error:
                liquid.main()
        self.assertEqual(error.exception.code, 1)
        download.assert_not_called()
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
