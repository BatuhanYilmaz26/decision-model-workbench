"""Failure contracts and metric arithmetic for trained-head lifecycle components."""

import copy
import hashlib
import json
import math
import queue
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from decision_workbench.data import dumps
from decision_workbench.training.artifacts import (
    write_manifest,
    verify_manifest,
    compare_predictions,
)
from decision_workbench.training.contracts import validate_splits
from decision_workbench.training.metrics import evaluate_predictions, normalize_answers
from decision_workbench.training.resources import ResourceLease, ResourceBusy

Q = {
    "route": {
        "type": "choice",
        "instructions": "Use the supplied policy",
        "criteria": {"a": "first outcome", "b": "second outcome"},
    }
}


def row(i):
    return {
        "id": str(i),
        "group_id": str(i),
        "state": f"Independent evidence {i}",
        "questions": Q,
        "gold": {"route": {"probabilities": {"a": 1.0, "b": 0.0}}},
        "metadata": {"origin": "real", "reviewed": True},
    }


class ModelContractTests(unittest.TestCase):
    def test_soft_targets_and_calibration_arithmetic(self):
        rows = [row(1), row(2)]
        rows[1]["gold"]["route"]["probabilities"] = {"a": 0.25, "b": 0.75}
        predictions = [
            {"route": {"probabilities": {"a": 0.8, "b": 0.2}}},
            {"route": {"probabilities": {"a": 0.4, "b": 0.6}}},
        ]
        m = evaluate_predictions(rows, predictions, Q)["route"]
        self.assertEqual(m["confusion"], [[1, 0], [0, 1]])
        self.assertEqual(m["soft_gold_rows"], 1)
        self.assertAlmostEqual(m["brier_sum_over_options"], (0.08 + 0.045) / 2)
        self.assertAlmostEqual(
            m["nll"], (-math.log(0.8) - 0.25 * math.log(0.4) - 0.75 * math.log(0.6)) / 2
        )
        self.assertAlmostEqual(m["ece_10_equal_width_bins"], 0.3)
        self.assertLess(m["accuracy_wilson_95"][0], 0.5)
        self.assertTrue(normalize_answers(predictions[1], Q, 0.7)["route"]["abstained"])
        for probabilities in (
            {"a": 0.5},
            {"a": float("nan"), "b": 0.5},
            {"a": 0.3, "b": 0.3},
        ):
            with self.assertRaises(ValueError):
                normalize_answers({"route": {"probabilities": probabilities}}, Q)

    def test_reserved_splits_reject_group_state_and_seed_leakage(self):
        clean = {
            name: [row(i)]
            for i, name in enumerate(("train", "validation", "calibration", "test"))
        }
        validate_splits(clean)
        for field in ("group_id", "state"):
            bad = copy.deepcopy(clean)
            bad["train"][0][field] = bad["test"][0][field]
            with self.assertRaises(ValueError):
                validate_splits(bad)
        bad = copy.deepcopy(clean)
        bad["train"][0]["metadata"]["seed_ids"] = ["3"]
        with self.assertRaisesRegex(ValueError, "lineage"):
            validate_splits(bad)
        bad = copy.deepcopy(clean)
        bad["calibration"][0]["metadata"]["origin"] = "synthetic"
        with self.assertRaises(ValueError):
            validate_splits(bad)

    def test_manifest_covers_nested_manifests_and_refuses_mutations(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "merged").mkdir()
            (root / "merged" / "manifest.json").write_text('{"nested": true}')
            (root / "merged" / "model.safetensors").write_bytes(b"fixture-format-only")
            (root / "report.json").write_text("{}")
            pinned = write_manifest(root, "laya-native", Q)
            manifest = verify_manifest(root, pinned, "laya-native", Q)
            self.assertIn("merged/manifest.json", manifest["files"])
            (root / "merged" / "manifest.json").write_text('{"nested": false}')
            with self.assertRaisesRegex(ValueError, "integrity"):
                verify_manifest(root, pinned, "laya-native", Q)
            pinned = write_manifest(root, "laya-native", Q)
            (root / "extra").write_text("untracked")
            with self.assertRaisesRegex(ValueError, "inventory"):
                verify_manifest(root, pinned, "laya-native", Q)
            (root / "extra").unlink()
            manifest = json.loads((root / "manifest.json").read_text())
            manifest["checkpoint"] = "../"
            (root / "manifest.json").write_text(dumps(manifest))
            forged = hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, "checkpoint"):
                verify_manifest(root, forged, "laya-native", Q)
            (root / "report.json").unlink()
            with self.assertRaises(ValueError):
                verify_manifest(root, forged, "laya-native", Q)

    def test_export_comparison_cannot_hide_a_changed_decision(self):
        before = [{"route": {"probabilities": {"a": 0.5001, "b": 0.4999}}}]
        after = [{"route": {"probabilities": {"a": 0.4999, "b": 0.5001}}}]
        with self.assertRaisesRegex(ValueError, "changes"):
            compare_predictions(before, after, Q, 0.005)
        after = [{"route": {"probabilities": {"a": 0.51, "b": 0.49}}}]
        with self.assertRaisesRegex(ValueError, "tolerance"):
            compare_predictions(before, after, Q, 0.005)
        self.assertEqual(
            compare_predictions(before, before, Q, 1e-6)["max_probability_delta"], 0
        )

    def test_broker_refuses_nonfinite_timeouts_and_invalid_transport(self):
        from decision_workbench.platform.runtime_broker import (
            RuntimeBroker,
            RuntimeUnavailable,
        )
        from decision_workbench.platform.store import Store

        with tempfile.TemporaryDirectory() as name:
            store = Store(name)
            for value in ("nan", "inf", "0", "-1"):
                with patch.dict("os.environ", {"DMT_INFERENCE_TIMEOUT_SECONDS": value}):
                    with self.assertRaises(ValueError):
                        RuntimeBroker(store)
            broker = RuntimeBroker(store)
            try:
                broker.messages = queue.Queue()
                broker.messages.put(["invalid response shape"])
                with self.assertRaises(RuntimeUnavailable):
                    broker._reply("test-id", 0.1)
            finally:
                broker.close()

    def test_real_resource_lease_excludes_a_second_process_and_recovers(self):
        with tempfile.TemporaryDirectory() as name:
            lock = Path(name) / "gpu.lock"
            code = (
                "from decision_workbench.training.resources import ResourceLease,ResourceBusy; "
                "import sys\ntry:\n with ResourceLease(sys.argv[1]).hold(timeout=0.1): pass\n"
                "except ResourceBusy: sys.exit(5)\n"
            )
            with ResourceLease(lock).hold():
                attempt = subprocess.run(
                    [sys.executable, "-c", code, str(lock)],
                    capture_output=True,
                    timeout=20,
                )
                self.assertEqual(attempt.returncode, 5, attempt.stderr.decode())
                with self.assertRaises(ResourceBusy):
                    ResourceLease(lock).acquire(timeout=0)
            recovered = subprocess.run(
                [sys.executable, "-c", code, str(lock)], capture_output=True, timeout=20
            )
            self.assertEqual(recovered.returncode, 0, recovered.stderr.decode())


if __name__ == "__main__":
    unittest.main()
