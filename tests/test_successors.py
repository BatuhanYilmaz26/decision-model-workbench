"""Successor HTTP, transaction, immutable-parent and recovery contracts."""

import copy
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from decision_workbench.platform.store import Store
from decision_workbench.platform.service import Service
from decision_workbench.platform.successors import create, verify_receipt
from decision_workbench.platform.worker import Worker
from decision_workbench.platform.recovery import backup_workspace, restore_workspace
import test_platform as fixtures


class SuccessorTests(unittest.TestCase):
    setUp = fixtures.PlatformTests.setUp
    tearDown = fixtures.PlatformTests.tearDown
    dataset = fixtures.PlatformTests.dataset
    snapshot = fixtures.PlatformTests.snapshot
    job = fixtures.PlatformTests.job

    def body(self, key="first-successor", criteria=None):
        parent = self.client.get(self.prefix).json()["payload"]
        policy = self.client.get(self.prefix + "/release-policy").json()["policy"]
        return {
            "name": "Support v2",
            "description": "Review invoice/crash evidence under the successor policy",
            "questions": copy.deepcopy(parent["questions"]),
            "data_requirements": "Real tickets and policy boundary cases",
            "expected_parent_schema_sha256": parent["schema_sha256"],
            "expected_parent_policy_id": policy["id"] if policy else None,
            "criteria": criteria,
            "note": "Owner reviewed this version's policy changes",
            "request_key": key,
        }

    def post(self, body):
        return self.client.post(self.prefix + "/successors", json=body)

    def count(self):
        with self.app.state.store.transaction() as db:
            return db.execute(
                "SELECT count(*) FROM entities WHERE kind='project'"
            ).fetchone()[0]

    def criteria(self):
        return {
            "min_accuracy": 0.6,
            "min_macro_f1": 0.5,
            "max_ece": 0.2,
            "min_test_rows": 30,
            "min_training_examples_per_class": 6,
            "min_calibration_examples_per_class": 2,
        }

    def test_atomic_empty_successor_with_distinct_mutable_criteria_and_immutable_parent(
        self,
    ):
        before = self.client.get(self.prefix).json()
        body = self.body(criteria=self.criteria())
        body["questions"]["refund"][
            "instructions"
        ] = "Refund requests need an explicit request in available evidence"
        result = self.post(body)
        self.assertEqual(result.status_code, 201, result.text)
        child = result.json()
        self.assertEqual(child["payload"]["policy_version"], 2)
        self.assertEqual(child["payload"]["parent_project"], self.project)
        self.assertEqual(self.client.get(self.prefix).json(), before)
        for kind in ("dataset", "snapshot", "model", "gate-dataset"):
            self.assertEqual(self.app.state.store.list("A", child["id"], kind), [])
        policy = self.app.state.store.release_policy("A", child["id"])
        self.assertFalse(policy["locked"])
        self.assertEqual(policy["policy"]["payload"]["criteria"], self.criteria())
        verify_receipt(self.app.state.store, child)
        self.assertTrue(
            any(
                e["action"] == "create.successor"
                for e in self.app.state.store.events("A", self.project)
            )
        )

    def test_retry_returns_original_after_parent_criteria_change_and_content_conflict_refuses(
        self,
    ):
        body = self.body()
        child = self.post(body).json()
        self.client.put(self.prefix + "/release-policy", json=self.criteria())
        self.assertEqual(self.post(body).json(), child)
        changed = copy.deepcopy(body)
        changed["description"] += " changed"
        self.assertEqual(self.post(changed).status_code, 409)
        self.assertEqual(self.count(), 2)

    def test_stale_parent_schema_and_policy_refuse_without_partial_successor(self):
        body = self.body()
        self.client.put(self.prefix + "/release-policy", json=self.criteria())
        self.assertEqual(self.post(body).status_code, 409)
        body = self.body()
        body["expected_parent_schema_sha256"] = "0" * 64
        self.assertEqual(self.post(body).status_code, 409)
        self.assertEqual(self.count(), 1)

    def test_role_and_tenant_refusal(self):
        body = self.body()
        for token, status in (
            ("b-secret", 404),
            ("review-secret", 403),
            ("infer-secret", 403),
        ):
            response = self.client.post(
                self.prefix + "/successors",
                json=body,
                headers={"Authorization": "Bearer " + token},
            )
            self.assertEqual(response.status_code, status, response.text)
        with self.assertRaises(PermissionError):
            create(
                self.app.state.service,
                {"tenant": "A", "actor": "editor", "role": "editor"},
                self.project,
                **body,
            )
        self.assertEqual(self.count(), 1)

    def test_changed_outcomes_require_matching_cost_criteria_and_invalid_input_is_atomic(
        self,
    ):
        from test_error_policy_backends import selected_policy

        body = self.body(
            criteria={**self.criteria(), "error_policy": selected_policy()}
        )
        del body["questions"]["refund"]
        self.assertEqual(self.post(body).status_code, 400)
        body = self.body(criteria=self.criteria())
        body["criteria"]["expected_id"] = "existing-parent"
        self.assertEqual(self.post(body).status_code, 400)
        for field, value in (("note", " "), ("questions", {}), ("name", " ")):
            body = self.body()
            body[field] = value
            self.assertEqual(self.post(body).status_code, 400)
        body = self.body(criteria={**self.criteria(), "min_test_rows": True})
        self.assertEqual(self.post(body).status_code, 422)
        self.assertEqual(self.count(), 1)

    def test_independent_connections_publish_one_successor_for_same_request(self):
        body = self.body(criteria=self.criteria())
        principal = {"tenant": "A", "actor": "owner", "role": "admin"}

        def attempt(_):
            service = Service(Store(self.home), runtime=object(), hosted=object())
            return create(service, principal, self.project, **body)["id"]

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(attempt, range(4)))
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(self.count(), 2)
        with self.app.state.store.transaction() as db:
            count = db.execute(
                "SELECT count(*) FROM entities WHERE kind='release-policy' AND project=?",
                (results[0],),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_storage_failure_rolls_back_project_policy_and_audit_then_retry_succeeds(
        self,
    ):
        body = self.body(criteria=self.criteria())
        store = self.app.state.store
        original = store.audit

        def fail(db, tenant, project, actor, action, target):
            if action == "define.release-policy":
                raise OSError("Fictional disk write failure")
            return original(db, tenant, project, actor, action, target)

        with patch.object(store, "audit", side_effect=fail):
            with self.assertRaises(OSError):
                self.post(body)
        self.assertEqual(self.count(), 1)
        with store.transaction() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM release_policy_heads").fetchone()[0], 0
            )
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM events WHERE action='create.successor'"
                ).fetchone()[0],
                0,
            )
        self.assertEqual(self.post(body).status_code, 201)

    def test_parent_learned_release_predictions_and_cold_recovery_preserved(self):
        snapshot = self.snapshot()
        submitted = self.job(
            "train", {"snapshot": snapshot, "backend": "cpu-baseline"}, "parent-study"
        )
        self.assertTrue(Worker(Store(self.home)).once())
        job = self.app.state.store.job(submitted["id"], "A", self.project)
        self.assertEqual(job["status"], "succeeded", job["error"])
        release = self.client.post(
            self.prefix + "/releases",
            json={
                "model_id": job["result"]["model"],
                "expected_revision": 0,
                "note": "Owner approved fictional parent study",
            },
        )
        self.assertEqual(release.status_code, 200, release.text)
        request = {
            "state": "invoice payment refund requested routine",
            "threshold": 0.0,
        }
        before = self.client.post(self.prefix + "/decisions", json=request).json()
        body = self.body(criteria=self.criteria())
        child = self.post(body).json()
        saved = self.client.put(
            "/v1/projects/" + child["id"] + "/release-policy",
            json={
                **self.criteria(),
                "min_accuracy": 0.7,
                "expected_id": child["payload"]["successor"]["initial_policy_id"],
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(
            self.client.post(self.prefix + "/decisions", json=request).json(), before
        )
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temporary:
            backup, restored = Path(temporary) / "backup", Path(temporary) / "restored"
            receipt = backup_workspace(self.home, backup)
            restore_workspace(backup, restored, receipt["manifest_sha256"])
            from fastapi.testclient import TestClient
            from decision_workbench.platform.api import create_app

            with TestClient(create_app(restored, fixtures.keys())) as client:
                client.headers.update({"Authorization": "Bearer a-secret"})
                self.assertEqual(
                    client.post(self.prefix + "/decisions", json=request).json(), before
                )
                self.assertEqual(
                    client.get("/v1/projects/" + child["id"]).json(), child
                )

    def test_successor_receipt_tamper_refuses_recovery_validation(self):
        child = self.post(self.body(criteria=self.criteria())).json()
        bad = copy.deepcopy(child)
        bad["payload"]["successor"]["note"] = "Different owner history"
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            verify_receipt(self.app.state.store, bad)
        bad = copy.deepcopy(child)
        bad["payload"]["policy_version"] += 1
        with self.assertRaisesRegex(ValueError, "ancestry mismatch"):
            verify_receipt(self.app.state.store, bad)

    def test_ancestor_taxonomy_exposures_remain_training_only_in_successor(self):
        from decision_workbench.platform.taxonomy import training_only_ids

        dataset = self.dataset()
        response = self.client.post(
            self.prefix + "/taxonomy-proposals",
            json={
                "dataset": dataset["id"],
                "record_ids": ["case-0"],
                "question_id": "team",
                "candidates": [
                    {"name": "Fictional policy boundary", "example_ids": ["case-0"]}
                ],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        child = self.post(self.body()).json()
        rows = fixtures.fixture_rows(4)
        self.assertEqual(
            training_only_ids(self.app.state.store, "A", child["id"], rows), {"case-0"}
        )

    def test_ancestry_depth_refuses_unusable_successor(self):
        owner = {"tenant": "A", "actor": "owner", "role": "admin"}
        parent = self.project
        for index in range(63):
            parent = self.app.state.service.create_project(
                owner,
                f"Branch {index}",
                "Fictional depth contract",
                fixtures.QUESTIONS,
                parent_project=parent,
            )["id"]
        spec = self.app.state.service.project(owner, parent)["payload"]
        body = self.body()
        body["expected_parent_schema_sha256"] = spec["schema_sha256"]
        response = self.client.post(f"/v1/projects/{parent}/successors", json=body)
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("supported depth", response.text)
        self.assertEqual(self.count(), 64)
