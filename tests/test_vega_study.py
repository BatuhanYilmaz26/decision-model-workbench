"""Failure and policy contracts independent of downloading a backbone."""

import copy
import tempfile
import types
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from decision_workbench.training.artifacts import compare_predictions
from decision_workbench.training.vega_runner import (
    main,
    pinned_assets,
    train_vega,
    MODEL,
    REVISION,
    BACKBONE,
    BACKBONE_REVISION,
)
from decision_workbench.training.vega_study import (
    calibration_partition,
    select_gate_threshold,
    validate_settings,
)


class VegaStudyTests(unittest.TestCase):
    def test_offline_engine_requests_only_the_consumed_assets(self):
        download = Mock(side_effect=["engine-cache", "backbone-cache"])
        args = Namespace(
            model=MODEL,
            revision=REVISION,
            backbone_model=BACKBONE,
            backbone_revision=BACKBONE_REVISION,
            offline=True,
        )
        with patch.dict(
            "sys.modules",
            {"huggingface_hub": types.SimpleNamespace(snapshot_download=download)},
        ):
            engine, backbone = pinned_assets(args)
        self.assertEqual(engine, Path("engine-cache"))
        self.assertEqual(backbone, Path("backbone-cache"))
        self.assertEqual(
            download.call_args_list[0].kwargs["allow_patterns"],
            ["engine.safetensors", "vega_config.json"],
        )
        self.assertTrue(
            all(
                call.kwargs["local_files_only"] and call.kwargs["token"] is False
                for call in download.call_args_list
            )
        )

    def test_cli_uses_the_shared_and_platform_home_resource_lease(self):
        with (
            patch.dict("os.environ", {"DMT_HOME": "custom-workspace"}),
            patch("sys.argv", ["vega_runner", "--data", "inputs", "--out", "outputs"]),
            patch(
                "decision_workbench.training.vega_runner.ModelResourceLease"
            ) as lease,
            patch(
                "decision_workbench.training.vega_runner.train_vega", return_value={}
            ),
            patch("builtins.print"),
        ):
            main()
        lease.assert_called_once_with(Path("custom-workspace"))
        lease.return_value.hold.assert_called_once_with(timeout=0)

    def test_cli_keeps_an_explicit_standalone_resource_lock(self):
        with (
            patch(
                "sys.argv",
                [
                    "vega_runner",
                    "--data",
                    "inputs",
                    "--out",
                    "outputs",
                    "--resource-lock",
                    "selected.lock",
                ],
            ),
            patch("decision_workbench.training.vega_runner.ResourceLease") as lease,
            patch(
                "decision_workbench.training.vega_runner.ModelResourceLease"
            ) as shared,
            patch(
                "decision_workbench.training.vega_runner.train_vega", return_value={}
            ),
            patch("builtins.print"),
        ):
            main()
        lease.assert_called_once_with("selected.lock")
        lease.return_value.hold.assert_called_once_with(timeout=0)
        shared.assert_not_called()

    def test_threshold_uses_both_classes_and_refuses_bad_scores(self):
        policy = select_gate_threshold([0.9, 0.8], [0.2, 0.3])
        self.assertEqual(policy["selection_role"], "validation")
        self.assertEqual(policy["balanced_accuracy"], 1)
        self.assertEqual(policy["negative_false_open_rate"], 0)
        self.assertGreater(policy["threshold"], 0.3)
        self.assertLessEqual(policy["threshold"], 0.8)
        for pos, neg in (
            ([], [0.3]),
            ([float("nan")], [0.3]),
            ([0.8], [-1]),
            ([[0.8]], [0.3]),
            ([True], [float("inf")]),
        ):
            with self.subTest(pos=pos, neg=neg), self.assertRaises(ValueError):
                select_gate_threshold(pos, neg)

    def test_gate_ties_prefer_more_refusal(self):
        policy = select_gate_threshold([0.5, 0.5], [0.5, 0.5])
        self.assertEqual(policy["balanced_accuracy"], 0.5)
        self.assertEqual(policy["threshold"], 1)
        self.assertEqual(policy["positive_false_closed_rate"], 1)

    def test_calibration_counts_independent_families_not_repeated_rows(self):
        args = Namespace(
            epochs=1,
            batch_size=3,
            rank=2,
            max_steps=0,
            learning_rate=0.001,
            gate_weight=1.0,
        )
        questions = {
            "domain": {
                "type": "choice",
                "instructions": "Choose",
                "criteria": {"a": "A", "b": "B"},
            }
        }
        positive = {
            role: [
                {
                    "id": role + str(i),
                    "group_id": role + str(i),
                    "state": role + str(i),
                    "metadata": {},
                }
                for i in range(60)
            ]
            for role in ("train", "validation", "calibration", "test")
        }
        negative = {
            role: [
                {
                    "id": role + "negative",
                    "group_id": role + "negative",
                    "state": role + "negative",
                    "metadata": {},
                    "negative_for": ["domain"],
                }
            ]
            for role in positive
        }
        self.assertGreater(
            validate_settings(args, positive, negative, questions)[
                "estimated_feature_bytes"
            ],
            0,
        )
        repeated = copy.deepcopy(positive)
        repeated["calibration"] = [
            dict(positive["calibration"][0], id=str(i), group_id="same")
            for i in range(100)
        ]
        with self.assertRaisesRegex(ValueError, "independent calibration"):
            validate_settings(args, repeated, negative, questions)
        linked = copy.deepcopy(positive)
        linked["calibration"][0]["metadata"]["semantic_links"] = [
            linked["calibration"][1]["id"]
        ]
        with self.assertRaisesRegex(ValueError, "merging known relatives"):
            validate_settings(args, linked, negative, questions)
        siblings = copy.deepcopy(positive)
        for row in siblings["calibration"][:2]:
            row["metadata"]["seed_ids"] = ["shared-source"]
        with self.assertRaisesRegex(ValueError, "merging known relatives"):
            validate_settings(args, siblings, negative, questions)
        for key, value in (
            ("epochs", True),
            ("max_steps", -1),
            ("batch_size", 65),
            ("rank", 0),
            ("learning_rate", np.nan),
            ("gate_weight", 0),
        ):
            bad = copy.copy(args)
            setattr(bad, key, value)
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_settings(bad, positive, negative, questions)

    def test_missing_negative_files_fail_before_any_model_or_output(self):
        with tempfile.TemporaryDirectory() as folder:
            args = Namespace(data=folder, out=str(Path(folder) / "export"))
            with (
                patch(
                    "decision_workbench.training.vega_runner.load_splits",
                    return_value=({}, {}),
                ),
                patch(
                    "decision_workbench.training.vega_runner.pinned_assets"
                ) as download,
            ):
                with self.assertRaises(FileNotFoundError):
                    train_vega(args)
                download.assert_not_called()
            self.assertFalse(Path(args.out).exists())

    def test_calibration_keeps_negative_bridged_relatives_in_one_partition(self):
        rows = [
            {"id": str(i), "group_id": str(i), "state": str(i), "metadata": {}}
            for i in range(61)
        ]
        negative = [
            {
                "id": "negative",
                "group_id": "negative",
                "state": "negative",
                "metadata": {"semantic_links": ["0", "1"]},
            }
        ]
        partition = calibration_partition(rows, negative)
        self.assertEqual(
            partition["family_counts"], {"probability_fit": 30, "conformal": 30}
        )
        fit, conf = set(partition["probability_fit"]), set(partition["conformal"])
        self.assertEqual(fit & conf, set())
        self.assertEqual(fit | conf, set(range(61)))
        self.assertEqual(0 in fit, 1 in fit)

    def test_equivalence_cannot_lose_model_abstention(self):
        q = {
            "domain": {
                "type": "choice",
                "instructions": "Choose",
                "criteria": {"a": "A", "b": "B"},
            }
        }
        before = [
            {"domain": {"probabilities": {"a": 0.9, "b": 0.1}, "model_abstained": True}}
        ]
        after = copy.deepcopy(before)
        after[0]["domain"]["model_abstained"] = False
        with self.assertRaisesRegex(ValueError, "model abstention"):
            compare_predictions(before, after, q, 0.005)
        after = copy.deepcopy(before)
        del after[0]["domain"]["model_abstained"]
        with self.assertRaisesRegex(ValueError, "model abstention"):
            compare_predictions(before, after, q, 0.005)
