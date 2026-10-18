"""Native data contracts without a model download or GPU dependency."""

import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from decision_workbench.data import prepare
from decision_workbench.training import perplexity_data as native
from decision_workbench.training.perplexity_assets import ASSETS, MODEL, REVISION
import test_platform as fixture


class PromptFixture:
    config = {
        "temperature": 1.0087417621345625,
        "attention_mode": "noncausal_full_attention",
    }

    def prepare(self, state, question, maximum):
        native.messages(state, question, ["A", "B"])
        return {
            "prompt": "Fictional native prompt",
            "input_ids": [1, 2],
            "option_keys": (
                list(question["criteria"])
                if question["type"] == "choice"
                else ["false", "true"]
            ),
        }


class PerplexityDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "canonical"
        self.data.mkdir()
        rows = fixture.fixture_rows(80)
        for row in rows:
            row["questions"] = copy.deepcopy(
                {k: q for k, q in row["questions"].items() if k != "urgency"}
            )
            row["gold"].pop("urgency")
        self.rows = prepare(rows)
        self.write()

    def tearDown(self):
        self.temp.cleanup()

    def write(self):
        for role, rows in self.rows.items():
            (self.data / (role + ".jsonl")).write_text(
                "".join(native.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )

    def test_complete_native_example_labels_targets_lineage_and_hashes(self):
        target = self.root / "export"
        report = native.export_dataset(self.data, target, PromptFixture())
        self.assertEqual(report["model"], MODEL)
        self.assertEqual(report["revision"], REVISION)
        for role, original in self.rows.items():
            self.assertEqual(
                (target / "canonical" / (role + ".jsonl")).read_bytes(),
                (self.data / (role + ".jsonl")).read_bytes(),
            )
            converted = [
                json.loads(line)
                for line in (target / "native" / (role + ".jsonl"))
                .read_text()
                .splitlines()
            ]
            lineage = [
                json.loads(line)
                for line in (target / "lineage" / (role + ".jsonl"))
                .read_text()
                .splitlines()
            ]
            self.assertEqual(len(converted), len(original) * 2)
            for row, trace in zip(converted, lineage):
                self.assertEqual(row["id"], trace["native_id"])
                self.assertEqual(row["family"], trace["group_id"])
                self.assertEqual(trace["split"], role)
                self.assertEqual(
                    hashlib.sha256(native.dumps(row).encode()).hexdigest(),
                    trace["native_sha256"],
                )
                self.assertEqual(
                    type(row["label"]),
                    bool if row["question"]["type"] == "noul" else str,
                )
                self.assertEqual(sum(row["target"]), 1)
        self.assertTrue(
            all(not name.endswith((".safetensors", ".pt")) for name in report["files"])
        )

    def test_training_soft_targets_preserved_and_heldout_soft_gold_refused(self):
        self.rows["train"][0]["gold"]["team"]["probabilities"] = {
            "billing": 0.3,
            "engineering": 0.7,
        }
        self.write()
        target = self.root / "soft-export"
        native.export_dataset(self.data, target, PromptFixture())
        row = json.loads((target / "native/train.jsonl").read_text().splitlines()[0])
        self.assertEqual(row["target"], [0.3, 0.7])
        self.assertEqual(
            row["source"]["training_label_basis"],
            "diagnostic-argmax-of-preserved-soft-target",
        )
        self.rows["calibration"][0]["gold"]["team"]["probabilities"] = {
            "billing": 0.3,
            "engineering": 0.7,
        }
        self.write()
        with self.assertRaisesRegex(ValueError, "heldout soft"):
            native.export_dataset(self.data, self.root / "refused", PromptFixture())
        self.assertFalse((self.root / "refused").exists())

    def test_score_ordering_media_and_unknown_fields_never_publish_partial_output(self):
        source = copy.deepcopy(self.rows)
        self.rows["train"][0]["questions"]["team"]["criteria"] = {
            "engineering": "software crash and bug",
            "billing": "invoice and payment",
        }
        # Freeze the first record's reversed order; later records disagree.
        self.write()
        with self.assertRaisesRegex(ValueError, "ordering"):
            native.export_dataset(self.data, self.root / "order", PromptFixture())
        self.rows = source
        for role in self.rows:
            for row in self.rows[role]:
                row["questions"]["rating"] = fixture.QUESTIONS["urgency"]
                row["gold"]["rating"] = {
                    "probabilities": {"0": 1.0, "1": 0.0, "2": 0.0}
                }
        self.write()
        with self.assertRaisesRegex(ValueError, "Choice and Noul"):
            native.export_dataset(self.data, self.root / "score", PromptFixture())
        self.assertFalse((self.root / "score").exists())
        for role in self.rows:
            for row in self.rows[role]:
                row["questions"].pop("rating")
                row["gold"].pop("rating")
        self.rows["train"][0]["images"] = ["fictional.png"]
        self.write()
        with self.assertRaisesRegex(ValueError, "Unsupported fields"):
            native.export_dataset(self.data, self.root / "media", PromptFixture())
        self.assertFalse((self.root / "media").exists())

    def test_review_roles_seed_leakage_and_overwrite_budget_guards(self):
        existing = self.root / "existing"
        existing.mkdir()
        (existing / "keep.txt").write_text("preserve")
        with self.assertRaises(ValueError):
            native.export_dataset(self.data, existing, PromptFixture())
        self.assertEqual((existing / "keep.txt").read_text(), "preserve")
        with (
            patch.object(native, "MAX_OUTPUT_BYTES", 1),
            self.assertRaisesRegex(ValueError, "output budget"),
        ):
            native.export_dataset(self.data, self.root / "budget", PromptFixture())
        for maximum in (True, 0, 8193):
            with self.subTest(maximum=maximum), self.assertRaises(ValueError):
                native.export_dataset(
                    self.data, self.root / "bad-limit", PromptFixture(), maximum
                )
        self.rows["test"][0]["metadata"]["origin"] = "synthetic"
        self.write()
        with self.assertRaisesRegex(ValueError, "real"):
            native.export_dataset(
                self.data, self.root / "synthetic-test", PromptFixture()
            )

    def test_prompt_contract_is_single_question_and_does_not_generate_chat_labels(self):
        question = fixture.QUESTIONS["refund"]
        value = native.messages(
            {"evidence": "A fictional record"}, question, ["A", "B"]
        )
        self.assertEqual(value[0]["content"], native.SYSTEM)
        self.assertIn("A: No / false\nB: Yes / true", value[1]["content"][0]["text"])
        with self.assertRaises(ValueError):
            native.messages("state", {**question, "images": []}, ["A", "B"])
        self.assertTrue(
            all(not name.endswith((".safetensors", ".bin", ".pt")) for name in ASSETS)
        )
        self.assertEqual(len(REVISION), 40)

    def test_asset_download_is_explicit_bounded_and_checks_existing_bytes(self):
        bodies = {
            "tokenizer.json": b"fictional tokenizer",
            "source/prompt.py": b"fictional source, never executed",
        }
        identities = {
            name: hashlib.sha256(body).hexdigest() for name, body in bodies.items()
        }
        seen = []

        def fetch(url, timeout):
            self.assertEqual(timeout, 60)
            self.assertTrue(
                url.startswith(f"https://huggingface.co/{MODEL}/resolve/{REVISION}/")
            )
            name = url.split(REVISION + "/", 1)[1]
            seen.append(name)
            return io.BytesIO(bodies[name])

        folder = self.root / "assets"
        with (
            patch.object(native, "ASSETS", identities),
            patch.object(native, "urlopen", side_effect=fetch),
        ):
            native.download_assets(folder)
            self.assertEqual(set(seen), set(bodies))
            native.download_assets(folder)
            self.assertEqual(len(seen), len(bodies))
            (folder / "tokenizer.json").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "pinned revision"):
                native.download_assets(folder)
            self.assertEqual(len(seen), len(bodies))
        with (
            patch.object(native, "ASSETS", identities),
            patch.object(
                native, "urlopen", return_value=io.BytesIO(b"invalid remote bytes")
            ),
        ):
            target = self.root / "refused-assets"
            with self.assertRaisesRegex(ValueError, "identity checks"):
                native.download_assets(target)
            self.assertFalse((target / "tokenizer.json").exists())


if __name__ == "__main__":
    unittest.main()
