"""Guided configuration preserves exact contracts and rejects ambiguous table input."""

import copy
import json
import unittest

from decision_workbench.ui.error_policy_editor import editor_rows, policy_from_editor
from decision_workbench.training.error_policy import normalize_policy
from test_platform import QUESTIONS


class ErrorPolicyEditorTests(unittest.TestCase):
    def chosen(self):
        return {
            "format": "domain-error-policy-v1",
            "questions": {
                "refund": {
                    "cost_unit": "loss_points",
                    "max_mean_cost": 2,
                    "costs": {
                        "false": {"false": 0, "true": 100},
                        "true": {"false": 5, "true": 0},
                    },
                },
                "urgency": {
                    "critical_labels": {
                        "2": {
                            "max_false_negative_rate": 0.05,
                            "max_false_positive_rate": 0.1,
                            "min_positive_rows": 100,
                            "min_negative_rows": 200,
                        }
                    }
                },
            },
        }

    def test_existing_asymmetric_and_critical_only_policies_roundtrip_exactly(self):
        selected = normalize_policy(self.chosen(), QUESTIONS)
        tables = editor_rows(QUESTIONS, selected)
        self.assertEqual(policy_from_editor(tables, QUESTIONS), selected)
        self.assertEqual(policy_from_editor(json.dumps(selected), QUESTIONS), selected)
        self.assertEqual(tables["overrides"][0]["cost"], 5)

    def test_guided_shared_cost_override_and_critical_rates(self):
        tables = editor_rows(QUESTIONS)
        rule = next(r for r in tables["costs"] if r["question"] == "refund")
        rule.update(include_costs=True, default_error_cost=5.0, max_mean_cost=2.0)
        tables["overrides"] = [
            {
                "question": "refund",
                "actual": "false",
                "predicted": "true",
                "cost": 100.0,
            }
        ]
        tables["critical"][0].update(question="urgency", outcome="2")
        result = policy_from_editor(tables, QUESTIONS)
        self.assertEqual(result["questions"]["refund"]["costs"]["false"]["true"], 100.0)
        self.assertEqual(result["questions"]["refund"]["costs"]["true"]["false"], 5.0)
        self.assertEqual(
            result["questions"]["urgency"]["critical_labels"]["2"]["rate_bound"],
            "wilson_95_upper",
        )

    def test_unchecked_costs_omit_previous_exceptions_but_preserve_critical_gates(self):
        tables = editor_rows(QUESTIONS, self.chosen())
        next(r for r in tables["costs"] if r["question"] == "refund")[
            "include_costs"
        ] = False
        self.assertEqual(
            set(policy_from_editor(tables, QUESTIONS)["questions"]), {"urgency"}
        )
        self.assertIsNone(policy_from_editor(None, QUESTIONS))

    def test_empty_and_incomplete_tables_fail_instead_of_saving_default_policy(self):
        with self.assertRaisesRegex(ValueError, "Select at least"):
            policy_from_editor(editor_rows(QUESTIONS), QUESTIONS)
        tables = editor_rows(QUESTIONS)
        tables["critical"][0]["max_false_positive_rate"] = 0.1
        with self.assertRaises(ValueError):
            policy_from_editor(tables, QUESTIONS)
        tables["critical"][0].update(
            question="refund", outcome="true", min_positive_rows=1.5
        )
        with self.assertRaises(ValueError):
            policy_from_editor(tables, QUESTIONS)

    def test_duplicate_unknown_and_diagonal_exceptions_fail(self):
        original = editor_rows(QUESTIONS, self.chosen())
        for mutation in ("duplicate", "unknown", "diagonal", "negative", "nan"):
            tables = copy.deepcopy(original)
            if mutation == "duplicate":
                tables["overrides"] *= 2
            elif mutation == "unknown":
                tables["overrides"][0]["actual"] = "unknown"
            elif mutation == "diagonal":
                tables["overrides"][0]["actual"] = tables["overrides"][0]["predicted"]
            else:
                tables["overrides"][0]["cost"] = (
                    -1 if mutation == "negative" else float("nan")
                )
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                policy_from_editor(tables, QUESTIONS)
        tables = copy.deepcopy(original)
        tables["critical"] *= 2
        with self.assertRaisesRegex(ValueError, "duplicate critical"):
            policy_from_editor(tables, QUESTIONS)

    def test_schema_question_changes_and_boolean_costs_fail(self):
        tables = editor_rows(QUESTIONS, self.chosen())
        tables["costs"].pop()
        with self.assertRaisesRegex(ValueError, "complete project"):
            policy_from_editor(tables, QUESTIONS)
        tables = editor_rows(QUESTIONS, self.chosen())
        tables["overrides"][0]["cost"] = True
        with self.assertRaises(ValueError):
            policy_from_editor(tables, QUESTIONS)

    def test_large_outcome_critical_only_policy_needs_no_matrix(self):
        questions = {
            "route": {
                "type": "choice",
                "instructions": "Route the fictional case",
                "criteria": {str(i): f"Queue {i}" for i in range(255)},
            }
        }
        tables = editor_rows(questions)
        tables["critical"][0].update(question="route", outcome="254")
        selected = policy_from_editor(tables, questions)
        self.assertNotIn("costs", selected["questions"]["route"])

    def test_oversized_matrix_is_refused_before_expansion(self):
        questions = {
            "route": {
                "type": "choice",
                "instructions": "Route the fictional case",
                "criteria": {str(i): f"Queue {i}" for i in range(255)},
            }
        }
        tables = editor_rows(questions)
        tables["costs"][0]["include_costs"] = True
        with self.assertRaisesRegex(ValueError, "64 KB"):
            policy_from_editor(tables, questions)
