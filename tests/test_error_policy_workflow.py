"""Frozen owner policy through real CPU jobs, release and recovery."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from decision_workbench.platform.worker import Worker
from decision_workbench.platform.store import Store
from decision_workbench.platform.recovery import backup_workspace, restore_workspace
from decision_workbench.platform.service import Service
from test_error_policy import policy
import test_platform as fixtures


class ErrorPolicyWorkflowTests(unittest.TestCase):
    setUp = fixtures.PlatformTests.setUp
    tearDown = fixtures.PlatformTests.tearDown
    dataset = fixtures.PlatformTests.dataset
    snapshot = fixtures.PlatformTests.snapshot
    job = fixtures.PlatformTests.job

    def configure(self, snapshot, *, strict=False):
        selected = policy()
        selected["questions"]["refund"] = selected["questions"].pop("flag")
        limits = selected["questions"]["refund"]["critical_labels"]["true"]
        limits.update(
            max_false_negative_rate=0.0 if strict else 1.0,
            max_false_positive_rate=1.0,
            rate_bound="wilson_95_upper",
        )
        response = self.client.put(
            self.prefix + "/release-policy",
            json={
                "min_accuracy": 0,
                "min_macro_f1": 0,
                "max_ece": 1,
                "min_test_rows": 10,
                "error_policy": selected,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def train(self, snapshot):
        submitted = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "train",
                "config": {"snapshot": snapshot, "backend": "cpu-baseline"},
                "request_key": "risk-cpu-study",
            },
        )
        self.assertEqual(submitted.status_code, 202, submitted.text)
        self.assertTrue(Worker(Store(self.home)).once())
        job = self.app.state.store.job(submitted.json()["id"], "A", self.project)
        self.assertEqual(job["status"], "succeeded", job["error"])
        return job, self.app.state.store.get(
            job["result"]["model"], "A", self.project, "model"
        )

    def test_frozen_cpu_error_report_release_inference_and_backup(self):
        snapshot = self.snapshot()
        saved = self.configure(snapshot)
        job, model = self.train(snapshot)
        criterion = saved["policy"]["payload"]["criteria"]["error_policy"]
        self.assertEqual(job["config"]["error_policy"], criterion)
        self.assertIn("error_evaluation", model["payload"]["report"])
        frozen = self.client.put(
            self.prefix + "/release-policy",
            json={
                "min_accuracy": 0,
                "min_macro_f1": 0,
                "max_ece": 1,
                "min_test_rows": 10,
                "expected_id": saved["policy"]["id"],
                "error_policy": criterion,
            },
        )
        self.assertEqual(frozen.status_code, 409)
        activated = self.client.post(
            self.prefix + "/releases",
            json={
                "model_id": model["id"],
                "expected_revision": 0,
                "note": "Fictional criterion and runtime examined",
            },
        )
        self.assertEqual(activated.status_code, 200, activated.text)
        self.assertEqual(
            self.client.post(
                self.prefix + "/decisions",
                json={"state": "A fictional invoice refund request", "threshold": 0.8},
            ).status_code,
            200,
        )
        with tempfile.TemporaryDirectory() as folder:
            backup = backup_workspace(self.home, Path(folder) / "backup")
            restored = restore_workspace(
                backup["destination"],
                Path(folder) / "restored",
                backup["manifest_sha256"],
            )
            recovered = Service(Store(restored["destination"]), runtime=object())
            actual = recovered.store.get(model["id"], "A", self.project, "model")[
                "payload"
            ]
            self.assertEqual(
                actual["report"]["error_evaluation"],
                model["payload"]["report"]["error_evaluation"],
            )
            self.assertEqual(restored["restored_schema"], 7)

    def test_good_average_metrics_do_not_bypass_strict_critical_release_limit(self):
        snapshot = self.snapshot()
        self.configure(snapshot, strict=True)
        _, model = self.train(snapshot)
        self.assertGreater(
            model["payload"]["report"]["test"]["refund"]["accuracy"], 0.8
        )
        result = self.client.post(
            self.prefix + "/releases",
            json={
                "model_id": model["id"],
                "expected_revision": 0,
                "note": "Fictional strict critical limit",
            },
        )
        self.assertEqual(result.status_code, 400, result.text)
        self.assertIn("false negative wilson", result.json()["detail"])
        self.assertIsNone(self.app.state.store.active("A", self.project)["model_id"])

    def test_training_cannot_supply_or_redefine_owner_error_policy(self):
        snapshot = self.snapshot()
        self.configure(snapshot)
        response = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "train",
                "config": {
                    "snapshot": snapshot,
                    "backend": "cpu-baseline",
                    "error_policy": {},
                },
                "request_key": "attempt-risk-override",
            },
        )
        self.assertEqual(response.status_code, 400)
        for token in ("review-secret", "infer-secret"):
            response = self.client.put(
                self.prefix + "/release-policy",
                headers={"Authorization": "Bearer " + token},
                json={"error_policy": {}},
            )
            self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
