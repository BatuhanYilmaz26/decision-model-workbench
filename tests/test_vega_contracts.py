"""Independent applicability roles and model-originated abstention contracts."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
import test_platform as fixtures
from decision_workbench.training.contracts import SPLITS
from decision_workbench.training.vega_contracts import (
    validate_gate_splits,
    load_gate_splits,
)
from decision_workbench.training.metrics import normalize_answers


class VegaContractsTests(unittest.TestCase):
    def setUp(self):
        rows = fixtures.fixture_rows(40)
        self.positive = {
            role: rows[i * 10 : (i + 1) * 10] for i, role in enumerate(SPLITS)
        }
        self.negative = {
            role: [
                {
                    "id": "negative-" + role,
                    "group_id": "negative-family-" + role,
                    "state": "Fictional weather-only source " + role,
                    "negative_for": list(fixtures.QUESTIONS),
                    "metadata": {"origin": "real", "reviewed": True},
                    "reason": "Reviewed source concerns a separate task",
                }
            ]
            for role in SPLITS
        }

    def test_four_role_explicit_applicability_contract_and_disk_hashes(self):
        self.assertEqual(
            validate_gate_splits(self.positive, self.negative), fixtures.QUESTIONS
        )
        with tempfile.TemporaryDirectory() as home:
            for role, rows in self.negative.items():
                Path(home, "gate_" + role + ".jsonl").write_text(
                    "\n".join(json.dumps(r) for r in rows), encoding="utf-8"
                )
            rows, hashes = load_gate_splits(home, self.positive)
            self.assertEqual(rows, self.negative)
            self.assertTrue(all(len(h) == 64 for h in hashes.values()))

    def test_no_synthetic_unreviewed_unknown_question_or_fake_gold_negatives(self):
        changes = [
            lambda r: r["metadata"].update(origin="synthetic"),
            lambda r: r["metadata"].update(reviewed=False),
            lambda r: r["metadata"].update(review_status="rejected"),
            lambda r: r.update(negative_for=["unknown"]),
            lambda r: r.update(gold={}),
            lambda r: r.update(reason=""),
        ]
        for change in changes:
            with self.subTest(change=change):
                rows = copy.deepcopy(self.negative)
                change(rows["train"][0])
                with self.assertRaises(ValueError):
                    validate_gate_splits(self.positive, rows)

    def test_source_groups_exact_states_and_bidirectional_relatives_cannot_cross_roles(
        self,
    ):
        changes = [
            lambda r: r.update(id="case-20"),
            lambda r: r.update(group_id="group-20"),
            lambda r: r.update(state=self.positive["calibration"][0]["state"]),
            lambda r: r["metadata"].update(semantic_links=["case-20"]),
            lambda r: r["metadata"].update(seed_ids=["case-20"]),
        ]
        for change in changes:
            rows = copy.deepcopy(self.negative)
            change(rows["train"][0])
            with self.assertRaises(ValueError):
                validate_gate_splits(self.positive, rows)
        positive = copy.deepcopy(self.positive)
        positive["test"][0]["metadata"]["semantic_links"] = ["negative-train"]
        with self.assertRaises(ValueError):
            validate_gate_splits(positive, self.negative)

    def test_every_question_has_negatives_in_every_role_and_no_contradiction(self):
        rows = copy.deepcopy(self.negative)
        rows["test"][0]["negative_for"] = ["team"]
        with self.assertRaises(ValueError):
            validate_gate_splits(self.positive, rows)
        rows = copy.deepcopy(self.negative)
        rows["train"][0]["state"] = self.positive["train"][0]["state"]
        with self.assertRaisesRegex(ValueError, "contradicts"):
            validate_gate_splits(self.positive, rows)

    def test_gate_evidence_bound_and_declared_links_are_validated(self):
        for change in [
            lambda r: r.update(state="x" * 64001),
            lambda r: r["metadata"].update(semantic_links="case-1"),
            lambda r: r["metadata"].update(seed_ids=["x", "x"]),
            lambda r: r.update(negative_for=["team", "team"]),
        ]:
            rows = copy.deepcopy(self.negative)
            change(rows["train"][0])
            with self.assertRaises(ValueError):
                validate_gate_splits(self.positive, rows)

    def test_model_abstention_survives_repeated_normalization_and_thresholds(self):
        questions = {"team": fixtures.QUESTIONS["team"]}
        value = {
            "team": {
                "probabilities": {"billing": 0.99, "engineering": 0.01},
                "model_abstained": True,
            }
        }
        first = normalize_answers(value, questions)
        self.assertTrue(first["team"]["abstained"])
        second = normalize_answers(first, questions, threshold=0.5)
        self.assertEqual(first, second)
        value["team"]["model_abstained"] = False
        self.assertFalse(normalize_answers(value, questions)["team"]["abstained"])
        self.assertTrue(
            normalize_answers(value, questions, threshold=1)["team"]["abstained"]
        )
        for bad in [1, None, "true"]:
            value["team"]["model_abstained"] = bad
            with self.assertRaises(ValueError):
                normalize_answers(value, questions)

    def test_existing_outputs_keep_the_original_shape(self):
        questions = {"team": fixtures.QUESTIONS["team"]}
        value = {"team": {"probabilities": {"billing": 0.6, "engineering": 0.4}}}
        result = normalize_answers(value, questions)
        self.assertEqual(
            set(result["team"]), {"answer", "probabilities", "confidence", "abstained"}
        )


if __name__ == "__main__":
    unittest.main()
