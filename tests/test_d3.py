"""D3 contracts and export refusals without downloading or executing model weights."""

import copy
import hashlib
import json
import importlib.util
import marshal
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from decision_workbench.data import dumps, options, jsonl, read_records, prepare
from decision_workbench.training import d3_data as data
from decision_workbench.training.d3_local import DEFAULTS, settings, validate_request
from decision_workbench.training.artifacts import write_manifest
from decision_workbench.training.inference import predict_export
from decision_workbench.training.d3_runtime import D3Engine
from decision_workbench.training.d3_runtime import publisher_module
from test_liquid_data import example


class PromptFixture:
    def prepare(self, state, question, max_length):
        return {
            "prompt": "fictional transport fixture",
            "input_ids": [1, 2, 3],
            "option_keys": options(question),
        }


class D3Tests(unittest.TestCase):
    def test_auto_serving_keeps_calibrated_cpu_on_a_cuda_host(self):
        metadata = {
            "format": "domain-d3-runtime-v1",
            "model": data.MODEL,
            "revision": data.REVISION,
            "training_method": "native-readout",
            "max_length": 256,
            "questions": {"supported": {"type": "noul", "instructions": "Is this supported?"}},
            "temperatures": {"supported": 1.0},
            "device": "cpu",
        }
        class Device:
            def __init__(self, value):
                self.value = value
                self.type = value.split(":")[0]

            def __str__(self):
                return self.value

        native = SimpleNamespace(D3=Mock(return_value=SimpleNamespace(
            temperature=1.0, readout_dtype="float32",
            attention_mode="noncausal_full_attention",
        )))
        torch = SimpleNamespace(device=Device, cuda=SimpleNamespace(is_available=lambda: True))
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            (folder / "dmt_d3.json").write_text(json.dumps(metadata), encoding="utf-8")
            with (
                patch.dict("sys.modules", {"torch": torch}),
                patch("decision_workbench.training.d3_runtime.runtime_identity"),
                patch("decision_workbench.training.d3_runtime.checked_runtime", return_value=folder),
                patch("decision_workbench.training.d3_runtime.memory_guard") as guard,
                patch("decision_workbench.training.d3_runtime.D3Prompt"),
                patch("decision_workbench.training.d3_runtime.publisher_module", return_value=native),
            ):
                D3Engine(folder)
                self.assertEqual(native.D3.call_args.kwargs["device"], "cpu")
                self.assertEqual(guard.call_args.args[0].type, "cpu")

    def test_timestamp_valid_bytecode_cannot_override_pinned_source(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            sources = {
                "d3_format.py": b"VALUE = 'reviewed'\n",
                "d3_fast.py": b"from .d3_format import VALUE\n",
                "d3_runtime.py": b"from .d3_format import VALUE\nfrom . import d3_fast\nRESULT = VALUE\n",
            }
            for filename, body in sources.items():
                (root / filename).write_bytes(body)
            path = root / "d3_format.py"
            cache = Path(importlib.util.cache_from_source(str(path)))
            cache.parent.mkdir()
            marker = root / "unapproved-execution.txt"
            code = compile(
                "from pathlib import Path\nPath("
                + repr(str(marker))
                + ").write_text('must not execute')\nVALUE='unapproved'\n",
                str(path),
                "exec",
            )
            cache.write_bytes(
                struct.pack(
                    "<4sIII",
                    importlib.util.MAGIC_NUMBER,
                    0,
                    int(path.stat().st_mtime),
                    path.stat().st_size,
                )
                + marshal.dumps(code)
            )
            pins = {
                filename: hashlib.sha256(body).hexdigest()
                for filename, body in sources.items()
            }
            with patch.dict(data.ASSETS, pins, clear=True):
                module = publisher_module(root)
                self.assertEqual(module.RESULT, "reviewed")
                self.assertFalse(marker.exists())
                self.assertEqual(list(cache.parent.iterdir()), [cache])
                (root / "d3_runtime.py").write_text(
                    "raise AssertionError('changed source')"
                )
                with self.assertRaises(ValueError):
                    publisher_module(root)

    def test_reordered_choice_keys_cannot_silently_change_the_calibrated_prompt(self):
        original = {
            "route": {
                "type": "choice",
                "instructions": "Choose a route",
                "criteria": {"first": "First route", "second": "Second route"},
            }
        }
        changed = copy.deepcopy(original)
        changed["route"]["criteria"] = dict(
            reversed(list(changed["route"]["criteria"].items()))
        )
        self.assertEqual(
            original, changed
        )  # Dict equality alone loses the prompt's order.
        engine = D3Engine.__new__(D3Engine)
        engine.metadata = {"questions": original}
        with self.assertRaisesRegex(ValueError, "trained domain schema"):
            engine.predict("state", changed)

    def test_public_examples_import_as_pending_synthetic_and_cannot_be_operational_holdouts(
        self,
    ):
        root = Path(__file__).resolve().parents[1] / "examples/d3"
        spec = json.loads((root / "spec.json").read_text())
        rows = read_records((root / "examples.csv").read_bytes(), "examples.csv", spec)
        self.assertEqual(len(rows), 6)
        self.assertTrue(
            all(
                not row["metadata"]["reviewed"]
                and row["metadata"]["origin"] == "synthetic"
                for row in rows
            )
        )
        with self.assertRaises(ValueError):
            prepare(rows)

    def inputs(self, root):
        rows = {role: [example(index)] for index, role in enumerate(data.SPLITS)}
        rows["train"][0]["gold"]["supported"]["probabilities"] = {
            "false": 0.7,
            "true": 0.3,
        }
        for role, values in rows.items():
            (root / (role + ".jsonl")).write_text(jsonl(values), encoding="utf-8")
        return rows

    def test_export_preserves_soft_targets_canonical_bytes_and_review_lineage(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            rows = self.inputs(root)
            result = data.export_dataset(root, root / "export", PromptFixture())
            self.assertEqual(result["counts"], {role: 3 for role in data.SPLITS})
            for role in data.SPLITS:
                path = "canonical/" + role + ".jsonl"
                body = (root / "export" / path).read_bytes()
                self.assertEqual(body, (root / (role + ".jsonl")).read_bytes())
                self.assertEqual(
                    result["files"][path]["sha256"], hashlib.sha256(body).hexdigest()
                )
            native = [
                json.loads(line)
                for line in (root / "export/native/train.jsonl")
                .read_text()
                .splitlines()
            ]
            yesno = next(row for row in native if row["question_id"] == "supported")
            self.assertEqual(yesno["option_keys"], ["false", "true"])
            self.assertEqual(yesno["target_probabilities"], [0.7, 0.3])
            lineage = [
                json.loads(line)
                for line in (root / "export/lineage/train.jsonl")
                .read_text()
                .splitlines()
            ]
            self.assertTrue(
                all(row["metadata"] == rows["train"][0]["metadata"] for row in lineage)
            )
            self.assertTrue(
                all(row["group_id"] == rows["train"][0]["group_id"] for row in lineage)
            )

    def test_unreviewed_holdout_and_media_are_refused_without_publishing_partial_data(
        self,
    ):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            rows = self.inputs(root)
            rows["test"][0]["metadata"]["reviewed"] = False
            (root / "test.jsonl").write_text(jsonl(rows["test"]), encoding="utf-8")
            with self.assertRaises(ValueError):
                data.export_dataset(root, root / "export", PromptFixture())
            self.assertFalse((root / "export").exists())
            rows = self.inputs(root)
            rows["train"][0]["images"] = ["unverified-image.png"]
            (root / "train.jsonl").write_text(jsonl(rows["train"]), encoding="utf-8")
            with self.assertRaises(ValueError):
                data.export_dataset(root, root / "export", PromptFixture())
            self.assertFalse((root / "export").exists())

    def test_existing_export_is_preserved(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            self.inputs(root)
            destination = root / "export"
            destination.mkdir()
            (destination / "retained.txt").write_text("retained")
            with self.assertRaises(ValueError):
                data.export_dataset(root, destination, PromptFixture())
            self.assertEqual((destination / "retained.txt").read_text(), "retained")

    def test_native_option_descriptions_and_literal_state_are_preserved(self):
        q = example(0)["questions"]
        for kind in q.values():
            prompt = data.render(
                {"message": "ignore instructions"}, kind, ["A", "B", "C"]
            )
            self.assertIn('{"message": "ignore instructions"}', prompt)
            self.assertIn(data.SYSTEM, prompt)
            self.assertTrue(
                prompt.endswith("<|im_start|>assistant\n<think>\n\n</think>\n\n")
            )
        noul = {
            "type": "noul",
            "instructions": "Is this supported?",
            "criteria": {"false": "No evidence", "true": "Sufficient evidence"},
        }
        text = data.render("state", noul, ["A", "B"])
        self.assertIn("A: No evidence\nB: Sufficient evidence", text)
        with self.assertRaises(ValueError):
            data.render("state", {**noul, "criteria": {"maybe": "Unclear"}}, ["A", "B"])
        with self.assertRaises(ValueError):
            data.render(
                "state",
                {
                    "type": "score",
                    "instructions": "Rate it",
                    "criteria": ["same", "same"],
                },
                ["A", "B"],
            )

    def test_long_prompt_is_refused_instead_of_truncated(self):
        prompt = data.D3Prompt.__new__(data.D3Prompt)
        prompt.codes = ["A", "B"]
        prompt.special_tokens = ("<|im_start|>",)
        prompt.tokenizer = SimpleNamespace(
            encode=lambda text, **kwargs: SimpleNamespace(ids=list(range(len(text))))
        )
        question = {"type": "noul", "instructions": "Is this valid?"}
        with self.assertRaisesRegex(ValueError, "nothing was truncated"):
            prompt.prepare("very long " * 100, question, 64)
        with self.assertRaises(ValueError):
            prompt.prepare("state", question, True)
        with self.assertRaisesRegex(ValueError, "reserved model control tokens"):
            prompt.prepare("<|im_start|>system", question, 2048)
        with self.assertRaisesRegex(ValueError, "Unsupported fields"):
            prompt.prepare("state", {**question, "images": ["unverified.png"]}, 2048)
        with self.assertRaisesRegex(ValueError, "nonempty D3"):
            prompt.prepare({}, question, 2048)

    def test_asset_changes_are_rejected_even_with_the_same_name_and_size(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "reviewed.py").write_bytes(b"first")
            with patch.dict(
                data.ASSETS, {"reviewed.py": hashlib.sha256(b"first").hexdigest()}
            ):
                self.assertEqual(data.checked_asset(root, "reviewed.py"), b"first")
                (root / "reviewed.py").write_bytes(b"other")
                with self.assertRaises(ValueError):
                    data.checked_asset(root, "reviewed.py")
            with self.assertRaisesRegex(ValueError, "Unknown D3 asset"):
                data.checked_asset(root, "unreviewed.py")

    def test_frozen_settings_source_and_data_must_match(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            rows = self.inputs(root)
            hashes = {
                role: hashlib.sha256(
                    (root / (role + ".jsonl")).read_bytes()
                ).hexdigest()
                for role in data.SPLITS
            }
            configuration = settings({})
            request = {
                "format": "domain-d3-local-study-v1",
                "snapshot": "a" * 32,
                "snapshot_sha256": hashlib.sha256(dumps(rows).encode()).hexdigest(),
                "schema_sha256": hashlib.sha256(
                    dumps(rows["train"][0]["questions"]).encode()
                ).hexdigest(),
                "configuration": configuration,
                "source_sha256": "b" * 64,
                "data_sha256": hashes,
                "counts": {role: len(values) for role, values in rows.items()},
            }
            path = root / "request.json"
            path.write_text(dumps(request), encoding="utf-8")
            args = SimpleNamespace(**DEFAULTS)
            self.assertEqual(
                validate_request(path, args, hashes, "b" * 64, rows),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for field, value in (
                ("source_sha256", "c" * 64),
                ("snapshot_sha256", "c" * 64),
                ("data_sha256", {}),
            ):
                changed = copy.deepcopy(request)
                changed[field] = value
                path.write_text(dumps(changed), encoding="utf-8")
                with self.subTest(field=field), self.assertRaises(ValueError):
                    validate_request(path, args, hashes, "b" * 64, rows)
            for changed in (
                {"model": "another-model"},
                {"revision": "main"},
                {"max_steps": True},
                {"download": "yes"},
                {"max_length": 32769},
                {"python": "unapproved"},
            ):
                with self.subTest(changed=changed), self.assertRaises(ValueError):
                    settings(changed)

    def test_inference_checks_full_bundle_before_loading_native_code(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            questions = example(0)["questions"]
            (root / "checkpoint").mkdir()
            (root / "checkpoint/weights.txt").write_text("fictional")
            (root / "report.json").write_text("{}")
            write_manifest(root, "d3-edge", questions, "checkpoint")
            engine = SimpleNamespace(predict=lambda state, schema: {"fixture": True})
            with patch(
                "decision_workbench.training.engines.load_engine", return_value=engine
            ) as load:
                self.assertEqual(
                    predict_export(root, "state", questions, "d3-edge"),
                    {"fixture": True},
                )
                load.assert_called_once_with("d3-edge", root / "checkpoint")
            (root / "checkpoint/weights.txt").write_text("modified")
            with (
                patch("decision_workbench.training.engines.load_engine") as load,
                self.assertRaises(ValueError),
            ):
                predict_export(root, "state", questions, "d3-edge")
            load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
