"""Transactional deletion, retained quotas, reference safety and owner controls."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from decision_workbench.platform.api import create_app
from decision_workbench.platform import project_deletion as deletion
from decision_workbench.platform import retained_usage
from decision_workbench.platform.project_lifecycle import transition
from decision_workbench.platform.recovery import backup_workspace, restore_workspace
from decision_workbench.platform.store import Store, Conflict, digest
import test_platform as fixtures
import test_postgres as pg

OWNER = {"tenant": "A", "actor": "owner", "role": "admin"}


class ProjectDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "workspace"
        self.app = create_app(self.home, fixtures.keys())
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.client.headers["Authorization"] = "Bearer a-secret"
        self.store = self.app.state.store
        self.project = self.app.state.service.create_project(
            OWNER,
            "Fictional deletion",
            "A fictional reviewed domain",
            fixtures.QUESTIONS,
        )["id"]
        self.prefix = "/v1/projects/" + self.project

    def archive(self):
        transition(self.store, OWNER, self.project, "archived", 0, self.project)

    def preview(self, mode="erase", days=0):
        result = self.client.post(
            self.prefix + "/deletion/preview",
            json={"audit_mode": mode, "min_archived_days": days},
        )
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def apply(self, preview, **kwargs):
        body = {
            "plan_sha256": preview["plan_sha256"],
            "confirmation": self.project,
            "audit_mode": preview["plan"]["audit_mode"],
            "min_archived_days": preview["plan"]["min_archived_days"],
            **kwargs,
        }
        return self.client.post(self.prefix + "/deletion", json=body)

    def test_erases_owned_records_and_fences_stale_writes_with_replayable_receipt(self):
        self.store.add_dataset("A", self.project, "owner", fixtures.fixture_rows(3), {})
        self.archive()
        plan = self.preview()
        response = self.apply(plan)
        self.assertEqual(response.status_code, 200, response.text)
        value = response.json()
        self.assertEqual(value["records_removed"], 3)
        self.assertEqual(self.client.get(self.prefix).status_code, 404)
        self.assertEqual(self.client.get("/v1/projects").json(), [])
        self.assertEqual(self.apply(plan).json(), value)
        self.assertEqual(self.apply(plan, audit_mode="retain").status_code, 409)
        for token in ("review-secret", "infer-secret"):
            self.assertEqual(
                self.client.get(
                    self.prefix + "/deletion",
                    headers={"Authorization": "Bearer " + token},
                ).status_code,
                403,
            )
        self.assertEqual(
            self.client.get(
                self.prefix + "/deletion", headers={"Authorization": "Bearer b-secret"}
            ).status_code,
            404,
        )
        with self.assertRaises(LookupError):
            self.store.add_dataset(
                "A", self.project, "owner", fixtures.fixture_rows(3), {}
            )
        with self.assertRaises(LookupError):
            self.store.submit(
                "A", self.project, "owner", "prepare", {}, "stale-after-delete"
            )
        with self.store.transaction() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM record_contents").fetchone()[0], 0
            )
            deletion.validate_history(db)
        backup = Path(self.temp.name) / "backup"
        captured = backup_workspace(self.home, backup)
        restored = Path(self.temp.name) / "restored"
        restore_workspace(backup, restored, captured["manifest_sha256"])
        self.assertEqual(deletion.receipt(Store(restored), OWNER, self.project), value)

    def test_shared_content_and_other_project_history_are_preserved(self):
        other = self.app.state.service.create_project(
            OWNER, "Other domain", "Independent project", fixtures.QUESTIONS
        )["id"]
        self.store.add_dataset("A", self.project, "owner", fixtures.fixture_rows(3), {})
        kept = self.store.add_dataset("A", other, "owner", fixtures.fixture_rows(2), {})
        self.archive()
        plan = self.preview()
        self.assertEqual(plan["plan"]["records_to_remove"], 3)
        self.assertEqual(plan["plan"]["shared_records_retained"], 2)
        self.assertEqual(self.apply(plan).status_code, 200)
        self.assertEqual(len(self.store.verify_dataset(kept["id"], "A", other)), 2)
        backup_workspace(self.home, Path(self.temp.name) / "shared-backup")

    def test_requires_archival_retention_confirmation_and_fresh_plan(self):
        self.assertEqual(
            self.client.post(self.prefix + "/deletion/preview", json={}).status_code,
            409,
        )
        self.archive()
        plan = self.preview(days=30)
        self.assertFalse(plan["eligible"])
        self.assertEqual(self.apply(plan).status_code, 409)
        plan = self.preview()
        self.assertEqual(self.apply(plan, confirmation="0" * 32).status_code, 400)
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO events(tenant,project,actor,action,target,created) VALUES (?,?,?,?,?,?)",
                ("A", self.project, "operator", "restore.workspace", "fictional", 1),
            )
        self.assertEqual(self.apply(plan).status_code, 409)
        self.assertEqual(
            self.store.get(self.project, "A", self.project, "project")["id"],
            self.project,
        )

    def test_other_tenant_deduplication_is_preserved_without_a_preview_oracle(self):
        self.store.add_dataset("A", self.project, "owner", fixtures.fixture_rows(2), {})
        self.archive()
        before = self.preview()
        other_owner = {"tenant": "B", "actor": "owner", "role": "admin"}
        other = self.app.state.service.create_project(
            other_owner, "Other tenant", "Other tenant policy", fixtures.QUESTIONS
        )["id"]
        kept = self.store.add_dataset("B", other, "owner", fixtures.fixture_rows(2), {})
        after = self.preview()
        self.assertEqual(before, after)
        self.assertEqual(self.apply(before).status_code, 200)
        self.assertEqual(len(self.store.verify_dataset(kept["id"], "B", other)), 2)

    def test_new_shared_reference_invalidates_the_reviewed_plan(self):
        self.store.add_dataset("A", self.project, "owner", fixtures.fixture_rows(2), {})
        other = self.app.state.service.create_project(
            OWNER, "Other", "Other policy", fixtures.QUESTIONS
        )["id"]
        self.archive()
        plan = self.preview()
        self.store.add_dataset("A", other, "owner", fixtures.fixture_rows(2), {})
        self.assertEqual(self.apply(plan).status_code, 409)
        self.assertEqual(self.preview()["plan"]["shared_records_retained"], 2)

    def test_successor_dependency_refuses_parent_deletion(self):
        self.app.state.service.create_project(
            OWNER,
            "Child",
            "Child policy",
            fixtures.QUESTIONS,
            parent_project=self.project,
        )
        self.archive()
        response = self.client.post(
            self.prefix + "/deletion/preview", json={"min_archived_days": 0}
        )
        self.assertEqual(response.status_code, 409, response.text)

    def test_failure_after_erasure_rolls_back_data_usage_and_receipt(self):
        self.store.add_dataset("A", self.project, "owner", fixtures.fixture_rows(2), {})
        self.archive()
        plan = self.preview()
        calls = []

        def authorize():
            calls.append(True)
            if len(calls) == 2:
                raise PermissionError("Fictional revoked owner")

        with self.assertRaises(PermissionError):
            deletion.execute(
                self.store,
                OWNER,
                self.project,
                plan["plan_sha256"],
                self.project,
                "erase",
                0,
                authorize,
            )
        self.assertEqual(
            self.store.get(self.project, "A", self.project, "project")["id"],
            self.project,
        )
        with self.store.transaction() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM record_contents").fetchone()[0], 2
            )
            self.assertIsNone(deletion.deleted(db, "A", self.project))

    def test_retained_audit_can_be_paged_and_erased_audit_keeps_only_deletion_event(
        self,
    ):
        self.archive()
        before = len(self.store.events("A", self.project))
        response = self.apply(self.preview(mode="retain"))
        self.assertEqual(response.status_code, 200, response.text)
        cursor = 0
        events = []
        while True:
            page = self.client.get(
                self.prefix + f"/deletion/audit?limit=1&after_id={cursor}"
            ).json()
            events.extend(page["events"])
            if not page["has_more"]:
                break
            cursor = page["next_after_id"]
        self.assertEqual(len(events), before + 1)
        self.assertEqual(events[-1]["action"], "delete.project")

    def test_hosted_and_generation_quotas_survive_source_erasure(self):
        import test_hosted as hosted_fixtures
        from decision_workbench.platform.hosted import reserve, HostedQuota
        from decision_workbench.platform.generation_plans import admission

        p = hosted_fixtures.profile()
        p["max_calls_day"] = 1
        call, _ = reserve(
            self.store,
            OWNER,
            self.project,
            p,
            "fictional-reservation",
            "a" * 64,
            {"kind": "prediction"},
        )
        self.store.add(
            "provider-call-result",
            "A",
            self.project,
            "owner",
            {"status": "succeeded", "result": {}},
            entity_id=digest({"call": call})[:32],
        )
        teacher = "https://fictional-teacher.example/v1"
        request = self.store.add(
            "generation-plan",
            "A",
            self.project,
            "owner",
            {
                "profile": {"base_url": teacher},
                "estimate": {"usd": "0.25"},
                "request_body": "Fictional private training seeds",
            },
        )
        self.store.add(
            "generation-response",
            "A",
            self.project,
            "owner",
            {"plan_id": request["id"], "reported_cost_usd": 0.5},
        )
        self.archive()
        response = self.apply(self.preview())
        self.assertEqual(response.status_code, 200, response.text)
        other = self.app.state.service.create_project(
            OWNER, "New project", "Same account", fixtures.QUESTIONS
        )["id"]
        with self.assertRaises(HostedQuota):
            reserve(
                self.store,
                OWNER,
                other,
                p,
                "another-reservation",
                "b" * 64,
                {"kind": "prediction"},
            )
        budget = {
            "base_url": teacher,
            "max_calls_day": 100,
            "max_estimated_request_usd": None,
            "max_estimated_day_usd": "0.60",
            "max_inflight": 1,
        }
        with self.store.transaction() as db:
            with self.assertRaises(Conflict):
                admission(db, "A", budget, {"usd": "0.11"})
            rows = db.execute(
                "SELECT payload FROM entities WHERE kind='retained-usage'"
            ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertNotIn(
                "private training seeds", json.dumps([dict(r) for r in rows])
            )
            self.assertNotIn(teacher, json.dumps([dict(r) for r in rows]))
        backup_workspace(self.home, Path(self.temp.name) / "accounting-backup")

    def test_tampered_retained_usage_refuses_backup(self):
        self.store.add(
            "generation-plan",
            "A",
            self.project,
            "owner",
            {
                "profile": {"base_url": "https://teacher.example/v1"},
                "estimate": {"usd": "0.1"},
            },
        )
        self.archive()
        self.assertEqual(self.apply(self.preview()).status_code, 200)
        with self.store.transaction() as db:
            db.execute("UPDATE entities SET created=0 WHERE kind='retained-usage'")
        with self.assertRaises(ValueError):
            backup_workspace(self.home, Path(self.temp.name) / "tampered")


class RelatedSourceDeletionTests(unittest.TestCase):
    def test_shared_cloud_account_daily_quota_survives_other_tenant_deletion(self):
        import test_cloud as cloud_fixtures
        from decision_workbench.platform.service import Service

        f = cloud_fixtures.CloudTests("runTest")
        f.setUp()
        self.addCleanup(f.tearDown)
        f.p["max_runs_day"] = 1
        f.p["tenants"] = ["A", "B"]
        f.write_profiles()
        other = {"tenant": "B", "actor": "owner", "role": "admin"}
        store = f.f.service.store
        project = Service(store).create_project(
            other, "Other tenant", "Fictional cloud policy", fixtures.QUESTIONS
        )["id"]
        run = store.add("cloud-run", "B", project, "owner", {"profile": f.p})
        store.add(
            "cloud-event",
            "B",
            project,
            "owner",
            {
                "run_id": run["id"],
                "revision": 1,
                "phase": "ready",
                "create_attempted": False,
                "upload_attempted": False,
            },
        )
        transition(store, other, project, "archived", 0, project)
        plan = deletion.preview(store, other, project, "erase", 0)
        deletion.execute(
            store, other, project, plan["plan_sha256"], project, "erase", 0
        )
        with self.assertRaises(Conflict) as raised:
            f.create()
        self.assertIn("rolling run quota", str(raised.exception))

    def test_confirmed_schema_intake_is_removed_without_resetting_teacher_quota(self):
        import test_schema_intake as intake_fixtures

        f = intake_fixtures.SchemaIntakeTests("runTest")
        f.setUp()
        self.addCleanup(f.tearDown)
        with patch.dict(os.environ, {"DMT_SCHEMA_CALLS_PER_DAY": "1"}):
            submitted = f.submit()
            self.assertEqual(submitted.status_code, 202, submitted.text)
            intake = submitted.json()["id"]
            self.assertTrue(f.worker.once())
            created = f.confirm(intake)
            self.assertEqual(created.status_code, 201, created.text)
            project = created.json()["id"]
            store = f.app.state.store
            transition(store, OWNER, project, "archived", 0, project)
            plan = deletion.preview(store, OWNER, project, "erase", 0)
            result = deletion.execute(
                store, OWNER, project, plan["plan_sha256"], project, "erase", 0
            )
            self.assertEqual(result["intake"], intake)
            self.assertEqual(
                f.client.get("/v1/schema-intakes/" + intake).status_code, 404
            )
            self.assertEqual(f.submit().status_code, 404)
            self.assertEqual(f.submit(key="after-deletion").status_code, 409)
            self.assertEqual(len(f.teacher.calls), 1)
            backup_workspace(f.home, Path(f.temp.name) / "post-deletion-backup")


@unittest.skipUnless(pg.URL, "Set DMT_TEST_POSTGRES_URL for shared project deletion")
class SharedProjectDeletionTests(pg.DatabaseFixture, unittest.TestCase):
    def test_another_node_observes_deletion_and_cannot_recreate_records(self):
        from decision_workbench.platform.service import Service

        self.database_setup()
        with tempfile.TemporaryDirectory() as folder:
            first = self.store(Path(folder) / "first")
            second = self.store(Path(folder) / "second")
            project = Service(first).create_project(
                OWNER,
                "Fictional shared deletion",
                "Reviewed policy",
                fixtures.QUESTIONS,
            )["id"]
            first.add_dataset("A", project, "owner", fixtures.fixture_rows(2), {})
            transition(first, OWNER, project, "archived", 0, project)
            plan = deletion.preview(first, OWNER, project, "erase", 0)
            deletion.execute(
                second, OWNER, project, plan["plan_sha256"], project, "erase", 0
            )
            with self.assertRaises(LookupError):
                first.add_dataset("A", project, "owner", fixtures.fixture_rows(2), {})
            self.assertEqual(
                deletion.receipt(first, OWNER, project)["records_removed"], 2
            )
            with first.transaction() as db:
                deletion.validate_history(db)


class ProjectDeletionBrowserTests(unittest.TestCase):
    def test_browser_previews_confirms_deletion_and_downloads_private_receipt(self):
        from http_server_fixture import LiveServer
        from streamlit.testing.v1 import AppTest

        with tempfile.TemporaryDirectory() as folder, LiveServer(
            folder, fixtures.keys()
        ) as server:
            store = server.app.state.store
            project = server.app.state.service.create_project(
                OWNER,
                "Fictional browser deletion",
                "Reviewed policy",
                fixtures.QUESTIONS,
            )["id"]
            store.add_dataset("A", project, "owner", fixtures.fixture_rows(2), {})
            transition(store, OWNER, project, "archived", 0, project)
            app = AppTest.from_file(
                str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30
            )
            app.session_state["credential"] = "a-secret"
            app.session_state["api_url"] = server.url
            app.session_state["principal"] = OWNER
            app.run()
            self.assertFalse(app.exception)

            def widget(kind, label):
                return next(w for w in getattr(app, kind) if w.label == label)

            widget("number_input", "Minimum days archived before deletion").set_value(
                0
            ).run()
            widget("selectbox", "Historical audit events").select("erase").run()
            widget("button", "Preview permanent deletion").click().run()
            self.assertFalse(app.exception)
            widget("text_input", "Type project ID to delete").set_value(project)
            widget(
                "checkbox",
                "I reviewed this plan and audit choice, and recorded any external copies needing separate cleanup.",
            ).check().run()
            widget("button", "Delete project data permanently").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(
                any("Project records deleted" in s.value for s in app.success)
            )
            self.assertEqual(
                deletion.receipt(store, OWNER, project)["records_removed"], 2
            )
            self.assertTrue(
                any(
                    b.label == "Save deletion receipt"
                    for b in app.get("download_button")
                )
            )


if __name__ == "__main__":
    unittest.main()
