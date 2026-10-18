"""Native orchestration, integrity and prediction contracts without real weights."""

from contextlib import ExitStack
import contextlib
import copy
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from decision_workbench.training import perplexity_checkpoint as checkpoint
from decision_workbench.training import perplexity_runtime as runtime
from decision_workbench.training import perplexity_runner as runner
from decision_workbench.training import perplexity_predict as prediction
from decision_workbench.training.artifacts import verify_manifest
from perplexity_native_fixture import NativeFixture, FakeEngine, QUESTIONS, tensor
from test_perplexity_data import PromptFixture

INVOKE_PUBLISHER = runner.invoke


class NativePerplexityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = NativeFixture(self.temp.name)
        self.stack = ExitStack()
        self.fixture.patches(self.stack)

    def tearDown(self):
        self.stack.close()
        for finder in list(sys.meta_path):
            if (
                isinstance(finder, checkpoint._PublisherFinder)
                and finder.folder == self.fixture.base
            ):
                sys.meta_path.remove(finder)
                for name in list(sys.modules):
                    if name == finder.prefix or name.startswith(finder.prefix + "."):
                        del sys.modules[name]
        self.temp.cleanup()

    def run_training(self):
        return runner.train_perplexity(self.fixture.args)

    def test_full_pipeline_preserves_roles_calibration_reference_and_portable_export(
        self,
    ):
        result = self.run_training()
        report = result["report"]
        self.assertEqual(report["training"]["method"], "publisher-full-weight")
        self.assertEqual(report["training"]["checkpoint_policy"], "final_only")
        self.assertTrue(report["training"]["backbone_updated"])
        self.assertTrue(report["training"]["readout_updated"])
        self.assertTrue(report["reload_equivalence"])
        self.assertEqual(report["reference"]["kind"], "original-local-checkpoint")
        self.assertEqual(report["reference"]["paid_api_calls"], 0)
        for name in QUESTIONS:
            self.assertEqual(report["calibration"][name]["temperature"], 2.0)
            self.assertEqual(
                report["calibration"][name]["decision_fit_records"],
                len(self.fixture.rows["calibration"]) * 2,
            )
            self.assertIn(name, report["calibration_metrics"])
        manifest = verify_manifest(
            Path(result["export"]),
            result["manifest_sha256"],
            "perplexity-native",
            QUESTIONS,
        )
        self.assertIn("publisher/NOTICE", manifest["files"])
        self.assertIn("checkpoint/dmt_perplexity.json", manifest["files"])
        self.assertEqual(
            [phase for phase, _ in self.fixture.calls], ["evaluate", "train"]
        )
        train_args = self.fixture.calls[-1][1]
        for flag in ("--public", "--jev", "--resume", "--stop-after"):
            self.assertNotIn(flag, train_args)
        self.assertIn("--save-final-only", train_args)
        self.assertEqual(
            Path(train_args[train_args.index("--temperature") + 1]).parts[-2:],
            ("native", "calibration.jsonl"),
        )
        self.assertFalse(any("test.jsonl" in value for value in train_args))
        with self.assertRaisesRegex(ValueError, "new run"):
            self.run_training()

    def test_changed_inputs_refused_before_manifest_publication(self):
        invoke = self.fixture.invoke

        def changed(*args):
            invoke(*args)
            if args[1] == "train":
                path = self.fixture.data / "train.jsonl"
                path.write_bytes(path.read_bytes() + b"\n")

        with (
            patch.object(runner, "invoke", side_effect=changed),
            self.assertRaisesRegex(ValueError, "changed during training"),
        ):
            self.run_training()
        self.assertFalse((self.fixture.out / "export/manifest.json").exists())

    def test_unchanged_payload_or_incomplete_training_cannot_publish(self):
        invoke = self.fixture.invoke

        def unchanged(*args):
            invoke(*args)
            if args[1] == "train":
                path = self.fixture.out / "checkpoints/selected/readout.safetensors"
                path.write_bytes(
                    (self.fixture.base / "readout.safetensors").read_bytes()
                )

        with (
            patch.object(runner, "invoke", side_effect=unchanged),
            self.assertRaisesRegex(ValueError, "change both"),
        ):
            self.run_training()
        self.assertFalse((self.fixture.out / "export/manifest.json").exists())

    def test_saved_validation_requires_complete_typed_noul_and_same_temperature(self):
        self.run_training()
        path = self.fixture.out / "study/development-00001.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        for mutate in (
            lambda r: r.pop(),
            lambda r: r.append(copy.deepcopy(r[0])),
            lambda r: r[0].update(temperature=1.0),
            lambda r: next(x for x in r if x["suite"] == "ready").update(
                options=["false", "true"]
            ),
        ):
            changed = copy.deepcopy(rows)
            mutate(changed)
            path.write_text("".join(json.dumps(row) + "\n" for row in changed))
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                runner.saved_validation(
                    path, self.fixture.rows["validation"], QUESTIONS, 2.0
                )

    def test_isolated_source_loader_ignores_installed_package_and_bytecode(self):
        fake = SimpleNamespace(TOKEN="untrusted package")
        cache = self.fixture.base / "source/src/autojev/__pycache__"
        cache.mkdir()
        (cache / "types.cpython-313.pyc").write_bytes(b"untrusted cache")
        with patch.dict(sys.modules, {"autojev": fake, "autojev.types": fake}):
            module = checkpoint.publisher_module(self.fixture.base, "model")
        self.assertEqual(module.VALUE, 17)
        self.assertTrue(module.__name__.startswith("_dmt_perplexity_"))
        (self.fixture.base / "source/src/autojev/types.py").write_text("TOKEN = 99\n")
        with self.assertRaisesRegex(ValueError, "asset"):
            checkpoint.publisher_module(self.fixture.base, "model")

    def test_payload_hash_ignores_headers_and_rejects_shape_offset_and_inventory_drift(
        self,
    ):
        path = self.fixture.base / "readout.safetensors"
        original = checkpoint.tensor_digest(
            self.fixture.base, self.fixture.mapping, readout=True
        )
        tensor(
            path,
            {"weight": ([255, 5120], 0)},
            metadata={"test": "Different serialization metadata"},
        )
        self.assertEqual(
            checkpoint.tensor_digest(
                self.fixture.base, self.fixture.mapping, readout=True
            ),
            original,
        )
        tensor(path, {"weight": ([2], 0)})
        with self.assertRaisesRegex(ValueError, "shape"):
            checkpoint.tensor_digest(
                self.fixture.base, self.fixture.mapping, readout=True
            )
        path.write_bytes(struct.pack("<Q", checkpoint.MAX_HEADER + 1))
        with self.assertRaisesRegex(ValueError, "header"):
            checkpoint.tensor_digest(
                self.fixture.base, self.fixture.mapping, readout=True
            )
        index = self.fixture.base / "model.safetensors.index.json"
        # The original asset is hash-bound, so tampering is refused even before
        # the untrusted index could redirect a tensor to another path.
        index.write_text(
            json.dumps({"weight_map": {"foreign": "../outside.safetensors"}})
        )
        with self.assertRaises(ValueError):
            checkpoint.native_files(self.fixture.base, self.fixture.base)

    def test_real_engine_path_uses_native_temperature_once_and_refuses_schema_drift(
        self,
    ):
        result = self.run_training()
        NativeEngine = runtime.PerplexityEngine
        calls = []

        class NativeModel:
            def __init__(self, folder, **kwargs):
                calls.append(kwargs)

            def predict(self, records, **kwargs):
                self_records = records
                self_kwargs = kwargs
                calls.append((self_records, self_kwargs))
                return [[0.2, 0.8] for _ in records]

        fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True))
        with (
            patch.object(runtime, "runtime_identity", return_value={}),
            patch.object(runtime, "memory_guard"),
            patch.object(runtime, "PerplexityPrompt", return_value=PromptFixture()),
            patch.object(
                runtime,
                "publisher_module",
                return_value=SimpleNamespace(DecisionModel=NativeModel),
            ),
            patch.dict(sys.modules, {"torch": fake_torch}),
        ):
            engine = NativeEngine(Path(result["export"]) / "checkpoint", device="cuda")
            answers = engine.predict({"ready": True}, QUESTIONS)
        self.assertEqual(answers["ready"]["probabilities"]["true"], 0.8)
        self.assertEqual(calls[-1][1], {"batch_size": 1})
        changed = copy.deepcopy(QUESTIONS)
        changed["action"]["criteria"] = dict(
            reversed(list(changed["action"]["criteria"].items()))
        )
        with self.assertRaisesRegex(ValueError, "frozen"):
            engine.predict({"ready": True}, changed)
        engine.model.predict = lambda *a, **kw: [[float("nan"), 1.0]] * 2
        with self.assertRaisesRegex(ValueError, "probabilities"):
            engine.predict({"ready": True}, QUESTIONS)

    def test_prediction_checks_manifest_request_and_threshold_before_loading_model(
        self,
    ):
        result = self.run_training()
        request = Path(self.temp.name) / "request.json"
        request.write_text(json.dumps({"state": {"ready": True}}))
        with patch.object(prediction, "PerplexityEngine", FakeEngine):
            value = prediction.predict(
                result["export"], result["manifest_sha256"], request, "cuda", 0.99
            )
        self.assertTrue(value["answers"]["ready"]["abstained"])
        with patch.object(prediction, "PerplexityEngine") as load:
            for threshold in (True, -1, float("inf")):
                with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                    prediction.predict(
                        result["export"],
                        result["manifest_sha256"],
                        request,
                        "cuda",
                        threshold,
                    )
            with self.assertRaises(ValueError):
                prediction.predict(result["export"], "0" * 64, request)
            request.write_text(json.dumps({"state": "x", "questions": QUESTIONS}))
            with self.assertRaises(ValueError):
                prediction.predict(result["export"], result["manifest_sha256"], request)
            load.assert_not_called()

    def test_settings_platform_and_resource_guards_fail_before_execution(self):
        for key, value in (
            ("epochs", True),
            ("batch_size", 100),
            ("max_length", 8193),
            ("learning_rate", float("nan")),
        ):
            changed = copy.copy(self.fixture.args)
            setattr(changed, key, value)
            with self.subTest(key=key), self.assertRaises(ValueError):
                runner.settings(changed)
        with (
            patch.object(runner.sys, "platform", "win32"),
            self.assertRaisesRegex(ValueError, "Linux"),
        ):
            self.run_training()
        self.assertFalse(self.fixture.out.exists())
        fake_torch = SimpleNamespace(
            cuda=SimpleNamespace(
                is_available=lambda: True, mem_get_info=lambda _: (1, 1)
            )
        )
        with (
            patch.dict(sys.modules, {"torch": fake_torch}),
            patch.object(runtime, "available_ram", return_value=10**15),
        ):
            with self.assertRaisesRegex(ValueError, "GPU memory"):
                runtime.memory_guard("cuda", training=True)
            with self.assertRaisesRegex(ValueError, "requires CUDA"):
                runtime.memory_guard("cpu", training=True)

    def test_large_download_needs_explicit_flag_and_space_before_hub_import(self):
        with (
            patch.object(checkpoint, "download_checkpoint") as download,
            patch.object(sys, "argv", ["checkpoint", "--cache", "some-cache"]),
        ):
            with (
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as error,
            ):
                checkpoint.main()
            self.assertEqual(error.exception.code, 1)
            download.assert_not_called()
        with (
            patch.object(
                checkpoint, "download_checkpoint", return_value=self.fixture.base
            ) as download,
            patch.object(
                sys,
                "argv",
                ["checkpoint", "--cache", "some-cache", "--download-weights"],
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            checkpoint.main()
            download.assert_called_once_with("some-cache")
        with (
            patch.object(
                checkpoint.shutil, "disk_usage", return_value=SimpleNamespace(free=1)
            ),
            self.assertRaisesRegex(ValueError, "cache storage"),
        ):
            checkpoint.download_checkpoint(Path(self.temp.name) / "empty-cache")

    def test_incomplete_stage_refuses_export(self):
        invoke = self.fixture.invoke

        def incomplete(*args):
            invoke(*args)
            if args[1] == "train":
                path = self.fixture.out / "study/summary.json"
                value = json.loads(path.read_text())
                value["complete"] = False
                path.write_text(json.dumps(value))

        with (
            patch.object(runner, "invoke", side_effect=incomplete),
            self.assertRaisesRegex(ValueError, "did not complete"),
        ):
            self.run_training()
        self.assertFalse((self.fixture.out / "export/manifest.json").exists())

    def test_mutated_original_weights_refused_before_manifest_publication(self):
        original = self.fixture.base / "readout.safetensors"

        class MutatingEngine(FakeEngine):
            def predict(self, state, questions):
                original.write_bytes(b"tampered original")
                return super().predict(state, questions)

        with (
            patch.object(runner, "PerplexityEngine", MutatingEngine),
            self.assertRaisesRegex(ValueError, "Base weight identity"),
        ):
            self.run_training()
        self.assertFalse((self.fixture.out / "export/manifest.json").exists())

    def test_publisher_invocation_captures_logs_restores_arguments_and_cleans_allocator(
        self,
    ):
        previous = sys.argv
        calls = []
        fake_torch = SimpleNamespace(
            cuda=SimpleNamespace(
                is_available=lambda: True, empty_cache=lambda: calls.append("cleaned")
            )
        )
        log = Path(self.temp.name) / "invocation.log"

        def main():
            calls.append(list(sys.argv))
            print("fictional publisher output")

        with (
            patch.object(
                runner, "publisher_module", return_value=SimpleNamespace(main=main)
            ),
            patch.dict(sys.modules, {"torch": fake_torch}),
        ):
            INVOKE_PUBLISHER(self.fixture.base, "train", ["--epochs", 1], log)
        self.assertIs(sys.argv, previous)
        self.assertEqual(calls, [["autojev-train", "--epochs", "1"], "cleaned"])
        self.assertIn("fictional publisher output", log.read_text())

        def failed():
            raise RuntimeError("fictional interrupted phase")

        with (
            patch.object(
                runner, "publisher_module", return_value=SimpleNamespace(main=failed)
            ),
            patch.dict(sys.modules, {"torch": fake_torch}),
            self.assertRaisesRegex(RuntimeError, "interrupted"),
        ):
            INVOKE_PUBLISHER(self.fixture.base, "train", [], log)
        self.assertIs(sys.argv, previous)
        self.assertEqual(calls[-1], "cleaned")


if __name__ == "__main__":
    unittest.main()
