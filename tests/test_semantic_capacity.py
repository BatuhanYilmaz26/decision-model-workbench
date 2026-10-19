"""New-audit row budgets are checked from metadata before row materialization."""

import unittest
from unittest.mock import Mock

from decision_workbench.platform.semantic import audit_source_capacity


class SemanticCapacityTests(unittest.TestCase):
    def store(self, counts, base=None):
        store = Mock()

        def get(identifier, *args, **kwargs):
            if identifier == "base":
                return {
                    "payload": {
                        "splits": {
                            "train": [None] * (base or 0),
                            "validation": [],
                            "calibration": [],
                            "test": [],
                        }
                    }
                }
            self.assertIs(kwargs.get("include_rows"), False)
            return {"payload": {"row_count": counts[identifier]}}

        store.get.side_effect = get
        store.verify_dataset.side_effect = AssertionError(
            "Capacity preflight cannot read row bodies"
        )
        return store

    def test_all_source_collections_and_base_count_toward_one_budget(self):
        config = {"datasets": ["one", "two"], "base_snapshot": "base"}
        self.assertEqual(
            audit_source_capacity(
                self.store({"one": 9000, "two": 10000}, 1000), "A", "P", config
            ),
            20000,
        )
        with self.assertRaisesRegex(ValueError, "Nothing was sampled"):
            audit_source_capacity(
                self.store({"one": 9000, "two": 10001}, 1000), "A", "P", config
            )

    def test_overlarge_collections_refuse_without_loading_base_or_rows(self):
        store = self.store({"one": 20001}, 1)
        with self.assertRaisesRegex(ValueError, "20,000"):
            audit_source_capacity(
                store, "A", "P", {"datasets": ["one"], "base_snapshot": "base"}
            )
        self.assertEqual(store.get.call_count, 1)

    def test_applicability_uses_scoped_gate_collection_metadata(self):
        store = self.store({"one": 4}, 5)
        self.assertEqual(
            audit_source_capacity(
                store,
                "A",
                "P",
                {"datasets": ["one"], "applicability": True, "base_snapshot": "base"},
            ),
            9,
        )
        self.assertEqual(
            store.get.call_args_list[0].args, ("one", "A", "P", "gate-dataset")
        )

    def test_invalid_row_count_metadata_refuses(self):
        for value in (True, -1, "100"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                audit_source_capacity(
                    self.store({"one": value}), "A", "P", {"datasets": ["one"]}
                )
