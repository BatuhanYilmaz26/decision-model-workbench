"""Real annotation lifecycle, cross-connection races and backup integrity."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import unittest
import tempfile
import sqlite3
from unittest.mock import patch

from decision_workbench.data import jsonl
from decision_workbench.platform.recovery import backup_workspace, restore_workspace
from decision_workbench.platform.service import Service
from decision_workbench.platform.store import Store, Conflict
import test_platform as fixtures


class AssignmentTests(unittest.TestCase):
    setUp = fixtures.PlatformTests.setUp
    tearDown = fixtures.PlatformTests.tearDown

    def test_oversized_pages_and_missing_assignee_refuse_without_changing_work(self):
        dataset = self.upload()
        record = fixtures.fixture_rows(12)[0]["id"]
        self.assertEqual(self.assign(dataset, [record]).status_code, 201)
        prefix = self.prefix + "/datasets/" + dataset["id"] + "/review-assignments"
        body = {
            "targets": [{"record_id": record, "expected_revision": 1}],
            "expected_hash": dataset["payload"]["sha256"],
            "note": "Missing intended assignee",
        }
        self.assertEqual(self.client.post(prefix, json=body).status_code, 422)
        for path in (
            self.prefix + "/review-assignments",
            prefix,
            prefix + "/history?record_id=" + record,
        ):
            self.assertEqual(
                self.client.get(path, params={"offset": 2**63}).status_code, 422
            )
            self.assertEqual(
                self.client.get(path, params={"offset": -1}).status_code, 422
            )
        self.assertEqual(self.queue()[0]["payload"]["revision"], 1)
        principal = {"tenant": "A", "actor": "owner", "role": "admin"}
        with self.assertRaisesRegex(ValueError, "assignment page"):
            self.app.state.service.review_assignments.queue(
                principal, self.project, offset=2**63
            )
        with self.assertRaisesRegex(ValueError, "history page"):
            self.app.state.service.review_assignments.history(
                principal, self.project, dataset["id"], record, offset=2**63
            )

    def upload(self, count=12):
        response = self.client.post(
            self.prefix + "/datasets",
            files={
                "file": (
                    "fictional.jsonl",
                    jsonl(fixtures.fixture_rows(count)).encode(),
                )
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def assign(self, dataset, records, actor="reviewer", revisions=None):
        return self.client.post(
            self.prefix + "/datasets/" + dataset["id"] + "/review-assignments",
            json={
                "targets": [
                    {
                        "record_id": record,
                        "expected_revision": (revisions or {}).get(record, 0),
                    }
                    for record in records
                ],
                "assignee": actor,
                "expected_hash": dataset["payload"]["sha256"],
                "note": "Fictional annotation workload",
            },
        )

    def review(
        self, dataset, row, revision=None, status="approved", key="review-secret"
    ):
        return self.client.post(
            self.prefix + "/datasets/" + dataset["id"] + "/reviews",
            headers={"Authorization": "Bearer " + key},
            json={
                "record_id": row["id"],
                "gold": row["gold"],
                "approved": status == "approved",
                "status": status,
                "expected_hash": dataset["payload"]["sha256"],
                "assignment_revision": revision,
                "note": "Fictional labels examined",
            },
        )

    def queue(self, **kwargs):
        response = self.client.get(self.prefix + "/review-assignments", params=kwargs)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["items"]

    def test_draft_approval_and_rejection_finish_only_the_selected_assignment(self):
        dataset = self.upload()
        rows = fixtures.fixture_rows(12)
        assigned = self.assign(dataset, [r["id"] for r in rows[:2]])
        self.assertEqual(assigned.status_code, 201, assigned.text)
        stored = self.app.state.store.get(dataset["id"], "A", self.project)
        self.assertEqual(stored["payload"]["sha256"], dataset["payload"]["sha256"])
        self.assertFalse(
            any(r["metadata"]["reviewed"] for r in stored["payload"]["rows"])
        )
        self.assertEqual(self.review(dataset, rows[0]).status_code, 409)
        drafted = self.review(dataset, rows[0], 1, "pending")
        self.assertEqual(drafted.status_code, 201, drafted.text)
        dataset = drafted.json()
        active = self.queue(status="assigned")
        self.assertEqual(len(active), 2)
        selected = next(t for t in active if t["payload"]["record_id"] == rows[0]["id"])
        self.assertEqual(selected["payload"]["revision"], 2)
        self.assertEqual(selected["dataset_head"], dataset["id"])
        self.assertEqual(self.review(dataset, rows[0], 1).status_code, 409)
        approved = self.review(dataset, rows[0], 2)
        self.assertEqual(approved.status_code, 201, approved.text)
        dataset = approved.json()
        rejected = self.review(dataset, rows[1], 1, "rejected")
        self.assertEqual(rejected.status_code, 201, rejected.text)
        dataset = rejected.json()
        self.assertEqual(self.queue(status="assigned"), [])
        completed = self.queue(status="completed")
        self.assertEqual(len(completed), 2)
        self.assertTrue(
            all(t["payload"]["updated_by"] == "reviewer" for t in completed)
        )
        history = self.client.get(
            self.prefix + "/datasets/" + dataset["id"] + "/review-assignments/history",
            params={"record_id": rows[0]["id"]},
        ).json()["items"]
        self.assertEqual(
            [r["payload"]["event"] for r in history], ["complete", "draft", "assign"]
        )
        self.assertEqual(dataset["payload"]["reviewed_count"], 1)
        self.assertEqual(dataset["payload"]["rejected_count"], 1)

    def test_cross_tenant_roles_cancel_and_admin_override(self):
        dataset = self.upload()
        row = fixtures.fixture_rows(12)[0]
        response = self.assign(dataset, [row["id"]], "another-reviewer")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.review(dataset, row, 1).status_code, 403)
        self.assertEqual(
            self.review(dataset, row, None, key="a-secret").status_code, 409
        )
        for token in ("review-secret", "infer-secret"):
            response = self.client.post(
                self.prefix + "/datasets/" + dataset["id"] + "/review-assignments",
                headers={"Authorization": "Bearer " + token},
                json={
                    "targets": [{"record_id": row["id"], "expected_revision": 1}],
                    "assignee": "reviewer",
                    "expected_hash": dataset["payload"]["sha256"],
                    "note": "Fictional unauthorized reassignment",
                },
            )
            self.assertEqual(response.status_code, 403)
        for suffix in (
            "/review-assignments",
            "/datasets/" + dataset["id"] + "/review-assignments",
            "/datasets/"
            + dataset["id"]
            + "/review-assignments/history?record_id="
            + row["id"],
        ):
            response = self.client.get(
                self.prefix + suffix, headers={"Authorization": "Bearer b-secret"}
            )
            self.assertEqual(response.status_code, 404)
        self.assertEqual(
            self.client.get(
                self.prefix + "/review-assignments",
                headers={"Authorization": "Bearer infer-secret"},
            ).status_code,
            403,
        )
        overridden = self.review(dataset, row, 1, key="a-secret")
        self.assertEqual(overridden.status_code, 201, overridden.text)
        self.assertEqual(
            self.queue(status="completed")[0]["payload"]["updated_by"], "owner"
        )
        dataset = overridden.json()
        self.assertEqual(
            self.assign(dataset, [row["id"]], revisions={row["id"]: 2}).status_code, 201
        )
        self.assertEqual(
            self.assign(dataset, [row["id"]], None, {row["id"]: 3}).status_code, 201
        )
        self.assertEqual(self.queue(status="cancelled")[0]["payload"]["revision"], 4)
        self.assertEqual(self.review(dataset, row, 4).status_code, 201)

    def test_batch_failure_is_atomic_and_rejects_bad_targets(self):
        dataset = self.upload()
        row = fixtures.fixture_rows(12)[0]
        for targets in ([row["id"], "missing"], [row["id"], row["id"]]):
            result = self.assign(dataset, targets)
            self.assertIn(result.status_code, (400, 404))
            self.assertEqual(self.queue(), [])
        self.assertEqual(self.assign(dataset, [row["id"]], None).status_code, 400)
        self.assertEqual(self.assign(dataset, [row["id"]]).status_code, 201)
        result = self.assign(dataset, [fixtures.fixture_rows(12)[1]["id"], row["id"]])
        self.assertEqual(result.status_code, 409)
        self.assertEqual(len(self.queue()), 1)
        body = {
            "targets": [{"record_id": row["id"], "expected_revision": True}],
            "assignee": "reviewer",
            "expected_hash": dataset["payload"]["sha256"],
            "note": "Invalid revision",
        }
        self.assertEqual(
            self.client.post(
                self.prefix + "/datasets/" + dataset["id"] + "/review-assignments",
                json=body,
            ).status_code,
            422,
        )

    def test_independent_connection_reassignment_race_has_one_winner(self):
        dataset = self.upload()
        row = fixtures.fixture_rows(12)[0]
        owner = {"tenant": "A", "actor": "owner", "role": "admin"}

        def assign(actor):
            service = Service(Store(self.home), runtime=object())
            try:
                service.review_assignments.assign(
                    owner,
                    self.project,
                    dataset["id"],
                    [{"record_id": row["id"], "expected_revision": 0}],
                    actor,
                    dataset["payload"]["sha256"],
                    "Fictional competing owner assignment",
                )
                return "assigned"
            except Conflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(assign, ("reviewer", "another-reviewer")))
        self.assertEqual(sorted(results), ["assigned", "conflict"])
        self.assertEqual(len(self.queue()), 1)

    def test_review_and_reassignment_race_cannot_commit_stale_authorization(self):
        dataset = self.upload()
        row = fixtures.fixture_rows(12)[0]
        self.assertEqual(self.assign(dataset, [row["id"]]).status_code, 201)

        def operate(action):
            service = Service(Store(self.home), runtime=object())
            try:
                if action == "assign":
                    service.review_assignments.assign(
                        {"tenant": "A", "actor": "owner", "role": "admin"},
                        self.project,
                        dataset["id"],
                        [{"record_id": row["id"], "expected_revision": 1}],
                        "another-reviewer",
                        dataset["payload"]["sha256"],
                        "Fictional reassignment",
                    )
                else:
                    service.review(
                        {"tenant": "A", "actor": "reviewer", "role": "reviewer"},
                        self.project,
                        dataset["id"],
                        row["id"],
                        row["gold"],
                        True,
                        dataset["payload"]["sha256"],
                        "Fictional concurrent review",
                        assignment_revision=1,
                    )
                return action
            except (Conflict, PermissionError):
                return "fenced"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(operate, ("assign", "review")))
        self.assertEqual(results.count("fenced"), 1)
        self.assertEqual(self.queue()[0]["payload"]["revision"], 2)

    def test_live_queue_paging_and_historical_membership(self):
        dataset = self.upload(210)
        rows = fixtures.fixture_rows(210)
        for start in range(0, len(rows), 100):
            self.assertEqual(
                self.assign(
                    dataset, [r["id"] for r in rows[start : start + 100]]
                ).status_code,
                201,
            )
        page1 = self.queue(limit=100)
        page2 = self.queue(limit=100, offset=100)
        self.assertFalse({r["id"] for r in page1} & {r["id"] for r in page2})
        self.assertEqual(len(self.queue(limit=100, offset=200)), 10)
        self.assertEqual(self.queue(mine=True), [])
        mine = self.client.get(
            self.prefix + "/review-assignments?mine=true&limit=200",
            headers={"Authorization": "Bearer review-secret"},
        ).json()["items"]
        self.assertEqual(len(mine), 200)
        response = self.client.get(
            self.prefix + "/datasets/" + dataset["id"] + "/rows",
            params={"record_id": rows[-1]["id"]},
        )
        self.assertEqual([r["id"] for r in response.json()["rows"]], [rows[-1]["id"]])
        appended_response = self.client.post(
            self.prefix + "/datasets/" + dataset["id"] + "/examples",
            json={
                "expected_hash": dataset["payload"]["sha256"],
                "examples": [
                    {
                        "state": "Fictional extra case",
                        "group_id": "new-family",
                        "labels": {},
                        "origin": "real",
                    }
                ],
            },
        )
        self.assertEqual(appended_response.status_code, 201, appended_response.text)
        appended = appended_response.json()
        new_rows = self.app.state.store.dataset_rows(appended["id"], "A", self.project)
        new_id = new_rows[-1]["id"]
        self.assertEqual(self.assign(appended, [new_id]).status_code, 201)
        old = self.client.get(
            self.prefix + "/datasets/" + dataset["id"] + "/review-assignments",
            params={"record_id": new_id},
        ).json()
        self.assertEqual(old["items"], [])
        self.assertEqual(
            self.client.get(
                self.prefix
                + "/datasets/"
                + dataset["id"]
                + "/review-assignments/history",
                params={"record_id": new_id},
            ).status_code,
            404,
        )

    def test_restart_migration_backup_restore_and_journal_tamper(self):
        recovery_temp = tempfile.TemporaryDirectory()
        self.addCleanup(recovery_temp.cleanup)
        recovery_root = Path(recovery_temp.name)
        dataset = self.upload()
        row = fixtures.fixture_rows(12)[0]
        with self.app.state.store.transaction() as db:
            db.execute("DROP INDEX annotation_assignment_revision")
            db.execute("DELETE FROM schema_version WHERE version>=5")
        reopened = Store(self.home)
        with reopened.transaction() as db:
            self.assertEqual(
                db.execute("SELECT max(version) FROM schema_version").fetchone()[0], 8
            )
        self.assertEqual(self.assign(dataset, [row["id"]]).status_code, 201)
        result = self.review(dataset, row, 1, "pending")
        self.assertEqual(result.status_code, 201)
        before = self.queue()
        backup = backup_workspace(self.home, recovery_root / "assignment-backup")
        restored = restore_workspace(
            backup["destination"],
            recovery_root / "assignment-restored",
            backup["manifest_sha256"],
        )
        service = Service(Store(restored["destination"]), runtime=object())
        after = service.review_assignments.queue(
            {"tenant": "A", "actor": "owner", "role": "admin"}, self.project
        )["items"]
        self.assertEqual(before, after)
        self.assertEqual(restored["restored_schema"], 8)
        with reopened.transaction() as db:
            db.execute(
                "DELETE FROM entities WHERE kind='review-assignment' AND json_extract(payload,'$.revision')=1"
            )
        with self.assertRaisesRegex(ValueError, "assignment history"):
            backup_workspace(self.home, recovery_root / "invalid-backup")

    def test_schema4_backup_upgrades_without_rewriting_data_or_source_backup(self):
        dataset = self.upload()
        with self.app.state.store.transaction() as db:
            db.execute("DELETE FROM schema_version WHERE version>=5")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = backup_workspace(self.home, root / "backup4")
            manifest = json.loads((root / "backup4/backup-manifest.json").read_text())
            self.assertEqual(manifest["schema_version"], 4)
            restored = restore_workspace(
                backup["destination"], root / "restored", backup["manifest_sha256"]
            )
            self.assertEqual(restored["source_schema"], 4)
            self.assertEqual(restored["restored_schema"], 8)
            stored = Store(restored["destination"]).get(
                dataset["id"], "A", self.project
            )
            self.assertEqual(stored["payload"]["sha256"], dataset["payload"]["sha256"])
            with self.app.state.store.transaction() as db:
                self.assertEqual(
                    db.execute("SELECT max(version) FROM schema_version").fetchone()[0],
                    4,
                )

    def test_assignment_completion_storage_failure_rolls_back_labels_and_audit(self):
        dataset = self.upload()
        row = fixtures.fixture_rows(12)[0]
        self.assertEqual(self.assign(dataset, [row["id"]]).status_code, 201)
        store = self.app.state.store
        with store.transaction() as db:
            before = tuple(
                db.execute(
                    "SELECT (SELECT count(*) FROM entities),(SELECT count(*) FROM record_contents),(SELECT count(*) FROM events)"
                ).fetchone()
            )
        with patch(
            "decision_workbench.platform.review_assignments.append",
            side_effect=sqlite3.OperationalError("Fictional storage interruption"),
        ):
            with self.assertRaises(sqlite3.OperationalError):
                self.app.state.service.review(
                    {"tenant": "A", "actor": "reviewer", "role": "reviewer"},
                    self.project,
                    dataset["id"],
                    row["id"],
                    row["gold"],
                    True,
                    dataset["payload"]["sha256"],
                    "Fictional annotation",
                    assignment_revision=1,
                )
        with store.transaction() as db:
            self.assertEqual(
                tuple(
                    db.execute(
                        "SELECT (SELECT count(*) FROM entities),(SELECT count(*) FROM record_contents),(SELECT count(*) FROM events)"
                    ).fetchone()
                ),
                before,
            )
        self.assertEqual(
            store.dataset_head(dataset["id"], "A", self.project), dataset["id"]
        )
        self.assertEqual(self.queue()[0]["payload"]["revision"], 1)
        self.assertFalse(
            store.dataset_rows(dataset["id"], "A", self.project, record_id=row["id"])[
                0
            ]["metadata"]["reviewed"]
        )


if __name__ == "__main__":
    unittest.main()
