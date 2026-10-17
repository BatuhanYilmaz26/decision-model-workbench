"""Training-contract checks without downloading weights or requiring Torch."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from decision_workbench.data import dumps, hard_gold
from decision_workbench.training import liquid_runtime as runtime
from decision_workbench.training.liquid_runner import (
    CompletionCollator,
    encoded_completion,
    optimizer_counter,
    train_liquid,
    validate_settings,
)

QUESTION = {
    "flag": {"type": "noul", "instructions": "Does the evidence satisfy the policy?"}
}


def calibration_rows():
    return [
        {
            "id": str(i),
            "group_id": str(i),
            "state": f"Independent evidence {i}",
            "questions": QUESTION,
            "gold": hard_gold(QUESTION, {"flag": i % 2 == 0}),
            "metadata": {"origin": "real", "reviewed": True},
        }
        for i in range(8)
    ]


class TokenizerFixture:
    def encode(self, text, **kwargs):
        return {
            "prompt": [11, 12, 13],
            "A<end>": [21, 22],
            "promptA<end>": [11, 12, 13, 21, 22],
        }[text]


class LiquidTrainingTests(unittest.TestCase):
    def test_overflow_skips_are_not_counted_as_optimizer_updates(self):
        with patch.dict(
            "sys.modules", {"transformers": SimpleNamespace(TrainerCallback=object)}
        ):
            counter = optimizer_counter()
        control = object()
        for skipped in (True, True, False, False, True):
            self.assertIs(
                counter.on_optimizer_step(
                    None,
                    None,
                    control,
                    optimizer=SimpleNamespace(step_was_skipped=skipped),
                ),
                control,
            )
        self.assertEqual(counter.updates, 2)
        self.assertEqual(counter.skipped, 3)

    def test_only_completion_and_end_of_turn_bear_loss(self):
        encoded = encoded_completion("prompt", "A<end>", TokenizerFixture())
        self.assertEqual(encoded["labels"], [-100, -100, -100, 21, 22])
        self.assertEqual(encoded["input_ids"], [11, 12, 13, 21, 22])
        self.assertEqual(encoded["attention_mask"], [1] * 5)
        shorter = {
            "input_ids": [11, 21, 22],
            "attention_mask": [1] * 3,
            "labels": [-100, 21, 22],
        }
        with patch.dict(
            "sys.modules",
            {
                "torch": SimpleNamespace(
                    tensor=lambda values, dtype: values, long="int64"
                )
            },
        ):
            batch = CompletionCollator(0)([encoded, shorter])
        self.assertEqual(batch["input_ids"][1], [11, 21, 22, 0, 0])
        self.assertEqual(batch["attention_mask"][1], [1, 1, 1, 0, 0])
        self.assertEqual(batch["labels"][1], [-100, 21, 22, -100, -100])

    def test_token_boundary_changes_or_multitoken_decisions_are_refused(self):
        tokenizer = TokenizerFixture()
        original = tokenizer.encode
        for changed in ([21, 22, 23], [99]):
            tokenizer.encode = lambda text, **kw: (
                changed if text == "A<end>" else original(text, **kw)
            )
            with self.assertRaisesRegex(ValueError, "single decision token"):
                encoded_completion("prompt", "A<end>", tokenizer)
        tokenizer.encode = lambda text, **kw: (
            [99] if text == "promptA<end>" else original(text, **kw)
        )
        with self.assertRaisesRegex(ValueError, "single decision token"):
            encoded_completion("prompt", "A<end>", tokenizer)

    def test_temperature_fit_reduces_overconfident_calibration_loss(self):
        rows = calibration_rows()
        predictions = [
            {"flag": {"probabilities": {"false": 0.1, "true": 0.9}}} for _ in rows
        ]
        fitted = runtime.fit_temperatures(rows, predictions, QUESTION)["flag"]
        self.assertEqual(fitted["temperature"], 10.0)
        self.assertEqual(fitted["records"], 8)
        self.assertLess(fitted["nll"], 1.0)
        self.assertEqual(runtime.apply_temperature([0.5, 0.5], 10), [0.5, 0.5])
        self.assertEqual(runtime.apply_temperature([0.2, 0.8], 1), [0.2, 0.8])

    def test_calibration_preserves_soft_gold_and_refuses_nonoperational_gold(self):
        rows = calibration_rows()
        for row in rows:
            row["gold"]["flag"]["probabilities"] = {"false": 0.25, "true": 0.75}
        predictions = [
            {"flag": {"probabilities": {"false": 0.25, "true": 0.75}}} for _ in rows
        ]
        self.assertEqual(
            runtime.fit_temperatures(rows, predictions, QUESTION)["flag"][
                "temperature"
            ],
            1.0,
        )
        original = deepcopy(rows)
        for edit in (
            lambda r: r[0]["metadata"].update(reviewed=False),
            lambda r: r[0]["metadata"].update(origin="synthetic"),
        ):
            rows = deepcopy(original)
            edit(rows)
            with self.assertRaises(ValueError):
                runtime.fit_temperatures(rows, predictions, QUESTION)
        with self.assertRaisesRegex(ValueError, "eight"):
            runtime.fit_temperatures(original[:7], predictions[:7], QUESTION)

    def test_invalid_temperatures_and_probabilities_cannot_reach_serving(self):
        for value in (True, float("nan"), float("inf"), 0, 11, "1"):
            with self.assertRaises(ValueError):
                runtime.apply_temperature([0.5, 0.5], value)
        for values in ([], [True, False], [0.2, 0.3], [float("nan"), 0.5], [-1, 2]):
            with self.assertRaises(ValueError):
                runtime.apply_temperature(values, 1)

    def test_insufficient_cpu_memory_is_refused_before_loading_weights(self):
        with patch.dict(
            "sys.modules",
            {
                "psutil": SimpleNamespace(
                    virtual_memory=lambda: SimpleNamespace(available=3 * 1024**3)
                )
            },
        ):
            for training in (False, True):
                with self.assertRaisesRegex(ValueError, "available RAM"):
                    runtime.require_cpu_memory(training=training)

    def test_training_counts_and_learning_rate_are_bounded(self):
        args = dict(
            epochs=1,
            rank=4,
            batch_size=1,
            grad_accum=4,
            max_length=512,
            max_steps=2,
            learning_rate=2e-4,
        )
        validate_settings(SimpleNamespace(**args))
        for key, value in [
            ("epochs", True),
            ("rank", 0),
            ("batch_size", 33),
            ("grad_accum", 0),
            ("max_length", 32769),
            ("max_steps", 0),
            ("learning_rate", float("nan")),
            ("learning_rate", 0.1),
        ]:
            with self.assertRaises(ValueError):
                validate_settings(SimpleNamespace(**{**args, key: value}))

    def test_reviewed_code_hash_and_architecture_are_checked_before_import(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "prompt.py").write_bytes(b"raise AssertionError('do not execute')")
            config = {
                "model_type": "known",
                "auto_map": {"AutoModel": "known.Class"},
                "text_config": {},
                "vision_config": {},
            }
            (root / "config.json").write_text(json.dumps(config))
            asset = hashlib.sha256((root / "prompt.py").read_bytes()).hexdigest()
            structure = hashlib.sha256(dumps(config).encode()).hexdigest()
            with (
                patch.object(runtime, "RUNTIME_ASSETS", {"prompt.py": asset}),
                patch.object(runtime, "CONFIG_STRUCTURE", structure),
            ):
                self.assertEqual(runtime.checked_runtime(root), config)
                config["dtype"] = "float16"
                (root / "config.json").write_text(json.dumps(config))
                runtime.checked_runtime(root)
                config["auto_map"]["AutoModel"] = "unreviewed.Attacker"
                (root / "config.json").write_text(json.dumps(config))
                with self.assertRaisesRegex(ValueError, "architecture or code mapping"):
                    runtime.checked_runtime(root)
                (root / "prompt.py").write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "asset differs"):
                    runtime.checked_runtime(root)

    def test_fine_tuned_engine_dispatch_uses_the_separate_adapter(self):
        from decision_workbench.training.engines import load_engine

        with (
            tempfile.TemporaryDirectory() as name,
            patch.object(runtime, "LiquidEngine", return_value="selected") as loader,
        ):
            self.assertEqual(load_engine("liquid-trl", name), "selected")
            loader.assert_called_once_with(Path(name))

    def test_input_change_during_checkpoint_resolution_is_refused_before_training(self):
        from decision_workbench.training import liquid_runner as runner

        with tempfile.TemporaryDirectory() as folder:
            args = SimpleNamespace(
                epochs=1,
                rank=4,
                batch_size=1,
                grad_accum=2,
                max_length=256,
                max_steps=2,
                learning_rate=2e-4,
                data="unused",
                out=str(Path(folder) / "export"),
                cache="unused",
                download=False,
                device="cuda",
            )
            rows = {"train": [{"questions": QUESTION}], "calibration": [None] * 8}
            fake_torch = SimpleNamespace(
                cuda=SimpleNamespace(is_available=lambda: True)
            )
            with (
                patch.dict("sys.modules", {"torch": fake_torch}),
                patch.object(runner, "runtime_identity", return_value={}),
                patch.object(
                    runner, "load_inputs", return_value=(rows, {"train": b"original"})
                ),
                patch.object(runner, "resolve_checkpoint", return_value=Path(folder)),
                patch.object(runner, "LiquidPrompt"),
                patch.object(
                    runner,
                    "export_dataset",
                    return_value={"input_sha256": {"train": "changed"}},
                ),
            ):
                with self.assertRaisesRegex(ValueError, "Reserved inputs changed"):
                    train_liquid(args)


if __name__ == "__main__":
    unittest.main()
