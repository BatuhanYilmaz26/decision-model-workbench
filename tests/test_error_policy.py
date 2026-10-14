"""Cost arithmetic, critical-support gates and malformed-report refusal."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from decision_workbench.data import hard_gold
from decision_workbench.training.metrics import evaluate_predictions
from decision_workbench.training.error_policy import (
    normalize_policy,
    attach_evaluation,
    release_failures,
    evaluate_report,
)

QUESTIONS = {
    "flag": {"type": "noul", "instructions": "Detect the declared critical event"}
}


def policy(cost_limit=100, bound="observed", support=1):
    return {
        "format": "domain-error-policy-v1",
        "questions": {
            "flag": {
                "cost_unit": "loss_points",
                "costs": {
                    "false": {"false": 0, "true": 10},
                    "true": {"false": 100, "true": 0},
                },
                "max_mean_cost": cost_limit,
                "critical_labels": {
                    "true": {
                        "max_false_negative_rate": 0.5,
                        "max_false_positive_rate": 0.5,
                        "min_positive_rows": support,
                        "min_negative_rows": support,
                        "rate_bound": bound,
                    }
                },
            }
        },
    }


def report(targets=(False, False, True, True), predictions=(False, True, False, True)):
    rows = [{"gold": hard_gold(QUESTIONS, {"flag": target})} for target in targets]
    outputs = [
        {
            "flag": {
                "probabilities": {
                    "false": 0.05 if label else 0.95,
                    "true": 0.95 if label else 0.05,
                }
            }
        }
        for label in predictions
    ]
    metrics = evaluate_predictions(rows, outputs, QUESTIONS)
    return {"validation": copy.deepcopy(metrics), "test": metrics}


class ErrorPolicyTests(unittest.TestCase):
    def test_asymmetric_cost_orientation_and_critical_denominators(self):
        result = report()
        attach_evaluation(result, QUESTIONS, policy())
        measured = result["error_evaluation"]["splits"]["test"]["flag"]
        self.assertEqual(measured["total_policy_cost"], 110)
        self.assertEqual(measured["mean_policy_cost"], 27.5)
        critical = measured["critical_labels"]["true"]
        self.assertEqual(
            [
                critical[k]
                for k in (
                    "true_positive",
                    "false_negative",
                    "false_positive",
                    "true_negative",
                )
            ],
            [1, 1, 1, 1],
        )
        self.assertEqual(critical["false_negative_rate"], 0.5)
        self.assertEqual(release_failures(result, QUESTIONS, policy()), [])
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            release_failures(result, QUESTIONS, policy(cost_limit=1))
        attach_evaluation(result, QUESTIONS, policy(cost_limit=1))
        self.assertTrue(
            any(
                "mean error cost" in failure
                for failure in release_failures(result, QUESTIONS, policy(cost_limit=1))
            )
        )

    def test_soft_gold_costs_are_mass_weighted_and_critical_gates_refuse_ambiguity(
        self,
    ):
        rows = [{"gold": {"flag": {"probabilities": {"false": 0.25, "true": 0.75}}}}]
        predictions = [{"flag": {"probabilities": {"false": 0.9, "true": 0.1}}}]
        metrics = evaluate_predictions(rows, predictions, QUESTIONS)
        source = {"validation": metrics, "test": copy.deepcopy(metrics)}
        attach_evaluation(source, QUESTIONS, policy())
        self.assertEqual(
            source["error_evaluation"]["splits"]["test"]["flag"]["mean_policy_cost"], 75
        )
        self.assertIn(
            "hard-labeled", ";".join(release_failures(source, QUESTIONS, policy()))
        )
        costs_only = policy()
        del costs_only["questions"]["flag"]["critical_labels"]
        attach_evaluation(source, QUESTIONS, costs_only)
        self.assertEqual(release_failures(source, QUESTIONS, costs_only), [])

    def test_missing_positive_or_negative_support_cannot_pass(self):
        for targets in ((False, False), (True, True)):
            source = report(targets, targets)
            attach_evaluation(source, QUESTIONS, policy())
            self.assertIn(
                "insufficient", ";".join(release_failures(source, QUESTIONS, policy()))
            )

    def test_wilson_upper_is_conservative_and_matches_known_interval(self):
        source = report()
        selected = policy(bound="wilson_95_upper")
        attach_evaluation(source, QUESTIONS, selected)
        measured = source["error_evaluation"]["splits"]["test"]["flag"][
            "critical_labels"
        ]["true"]
        self.assertAlmostEqual(
            measured["false_negative_wilson_95"][1], 0.9054687942657693
        )
        self.assertEqual(len(release_failures(source, QUESTIONS, selected)), 2)
        perfect = report((False,) * 100 + (True,) * 100, (False,) * 100 + (True,) * 100)
        selected["questions"]["flag"]["critical_labels"]["true"][
            "max_false_negative_rate"
        ] = 0.05
        selected["questions"]["flag"]["critical_labels"]["true"][
            "max_false_positive_rate"
        ] = 0.05
        attach_evaluation(perfect, QUESTIONS, selected)
        self.assertEqual(release_failures(perfect, QUESTIONS, selected), [])

    def test_abstention_does_not_erase_error_exposure(self):
        rows = [{"gold": hard_gold(QUESTIONS, {"flag": True})}]
        metrics = evaluate_predictions(
            rows,
            [
                {
                    "flag": {
                        "probabilities": {"false": 0.9, "true": 0.1},
                        "model_abstained": True,
                    }
                }
            ],
            QUESTIONS,
        )
        source = {"validation": metrics, "test": metrics}
        attach_evaluation(source, QUESTIONS, policy())
        self.assertEqual(
            source["error_evaluation"]["splits"]["test"]["flag"]["mean_policy_cost"],
            100,
        )

    def test_invalid_policy_and_missing_report_metadata_refuse(self):
        candidates = []
        for field, value in (
            ("max_mean_cost", True),
            ("max_mean_cost", float("nan")),
            ("max_mean_cost", 10**400),
        ):
            invalid = policy()
            invalid["questions"]["flag"][field] = value
            candidates.append(invalid)
        invalid = policy()
        invalid["questions"]["flag"]["costs"]["true"]["true"] = 1
        candidates.append(invalid)
        invalid = policy()
        invalid["questions"]["flag"]["costs"]["true"].pop("false")
        candidates.append(invalid)
        invalid = policy()
        invalid["questions"]["flag"]["critical_labels"]["true"]["min_positive_rows"] = 0
        candidates.append(invalid)
        for candidate in candidates:
            with (
                self.subTest(candidate=str(candidate)[:80]),
                self.assertRaises(ValueError),
            ):
                normalize_policy(candidate, QUESTIONS)
        for field in ("gold_mass_confusion", "soft_gold_rows"):
            source = report()
            del source["test"]["flag"][field]
            with self.assertRaises(ValueError):
                evaluate_report(source, QUESTIONS, policy())
        source = report()
        source["test"]["flag"]["gold_mass_confusion"][0][0] += 1
        with self.assertRaisesRegex(ValueError, "totals"):
            evaluate_report(source, QUESTIONS, policy())

    def test_cpu_ties_and_ordinal_distances_match_serving_class_order(self):
        from decision_workbench.platform.baseline import evaluate, predict

        questions = {
            "q": {
                "type": "choice",
                "instructions": "Choose",
                "criteria": {"z": "Z", "a": "A"},
            }
        }
        bundle = {
            "questions": questions,
            "vocabulary": {"word": 0},
            "idf": [1.0],
            "heads": {
                "q": {
                    "classes": ["a", "z"],
                    "coef": [[0.0]],
                    "intercept": [0.0],
                    "temperature": 1.0,
                }
            },
        }
        rows = [{"state": "word", "gold": hard_gold(questions, {"q": "a"})}]
        self.assertEqual(predict(bundle, "word", 0)["q"]["answer"], "a")
        self.assertEqual(evaluate(bundle, rows)["q"]["accuracy"], 1)
        score = {
            "q": {
                "type": "score",
                "instructions": "Rate",
                "criteria": ["low", "medium", "high"],
            }
        }
        order = ["0", "2", "1"]
        probabilities = {key: float(key == "2") for key in order}
        metrics = evaluate_predictions(
            [{"gold": hard_gold(score, {"q": 0})}],
            [{"q": {"probabilities": probabilities}}],
            score,
            label_order={"q": order},
        )
        self.assertEqual(metrics["q"]["ordinal_mae"], 2)
        self.assertEqual(metrics["q"]["expected_score_mae"], 2)

    def test_diagnostic_cli_is_read_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            contents = {
                "report": report(),
                "schema": {"questions": QUESTIONS},
                "policy": policy(),
            }
            for name, value in contents.items():
                (root / (name + ".json")).write_text(
                    json.dumps(value), encoding="utf-8"
                )
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "decision_workbench.training.error_policy",
                    "--report",
                    str(root / "report.json"),
                    "--schema",
                    str(root / "schema.json"),
                    "--policy",
                    str(root / "policy.json"),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["release_failures"], [])
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)
