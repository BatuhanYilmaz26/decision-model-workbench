"""Project archival across API, concurrent writes, recovery and real browser UI."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from decision_workbench.data import jsonl
from decision_workbench.platform.api import create_app
from decision_workbench.platform.project_lifecycle import validate_history
from decision_workbench.platform.recovery import backup_workspace, restore_workspace
from decision_workbench.platform.store import Conflict, Store, encode
import test_platform as fixtures
import test_postgres as pg


class ProjectLifecycleTests(unittest.TestCase):
    dataset = fixtures.PlatformTests.dataset
    job = fixtures.PlatformTests.job
    snapshot = fixtures.PlatformTests.snapshot

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "workspace"
        self.app = create_app(self.home, fixtures.keys())
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.client.headers["Authorization"] = "Bearer a-secret"
        self.project = self.client.post(
            "/v1/projects",
            json={
                "name": "Fictional lifecycle",
                "description": "A reviewed domain decision policy",
                "questions": fixtures.QUESTIONS,
            },
        ).json()["id"]
        self.prefix = "/v1/projects/" + self.project
        self.store = self.app.state.store

    def change(self, state="archived", revision=0, **kwargs):
        return self.client.put(
            self.prefix + "/lifecycle",
            json={
                "state": state,
                "expected_revision": revision,
                "confirmation": self.project,
            },
            **kwargs
        )

    def test_archive_blocks_api_work_and_restore_preserves_records(self):
        original = self.client.post(
            self.prefix + "/datasets",
            files={
                "file": ("fictional.jsonl", jsonl(fixtures.fixture_rows(6)).encode())
            },
        ).json()
        self.assertEqual(self.change().status_code, 200)
        for method, path, body in [
            ("GET", "/datasets/" + original["id"] + "/rows", None),
            ("GET", "/audit", None),
            ("GET", "/active", None),
            ("POST", "/decisions", {"state": "fictional", "threshold": 0.5}),
            (
                "POST",
                "/jobs",
                {"kind": "prepare", "config": {}, "request_key": "archived-job"},
            ),
        ]:
            with self.subTest(path=path):
                response = self.client.request(method, self.prefix + path, json=body)
                self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.client.get("/v1/projects").json()[0]["id"], self.project)
        self.assertEqual(
            self.client.get(self.prefix + "/lifecycle").json()["state"], "archived"
        )
        self.assertEqual(self.change("active", 1).status_code, 200)
        self.assertEqual(
            self.store.get(original["id"], "A", self.project, "dataset")["payload"][
                "sha256"
            ],
            original["payload"]["sha256"],
        )
        validate_history(self.store)

    def test_confirmation_roles_tenant_and_stale_revision(self):
        for token, expected in [
            ("review-secret", 403),
            ("infer-secret", 403),
            ("b-secret", 404),
        ]:
            with self.subTest(token=token):
                self.assertEqual(
                    self.change(
                        headers={"Authorization": "Bearer " + token}
                    ).status_code,
                    expected,
                )
        response = self.client.put(
            self.prefix + "/lifecycle",
            json={
                "state": "archived",
                "expected_revision": 0,
                "confirmation": "0" * 32,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.change().status_code, 200)
        self.assertEqual(self.change("active", 0).status_code, 409)
        self.assertEqual(
            self.client.get(
                self.prefix + "/lifecycle", headers={"Authorization": "Bearer b-secret"}
            ).status_code,
            404,
        )

    def test_proxy_prefix_can_view_and_restore_an_archived_project(self):
        self.app.root_path = "/proxy"
        with TestClient(
            self.app, base_url="http://testserver/proxy", root_path="/proxy"
        ) as client:
            client.headers["Authorization"] = "Bearer a-secret"
            response = client.put(
                self.prefix + "/lifecycle",
                json={
                    "state": "archived",
                    "expected_revision": 0,
                    "confirmation": self.project,
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(client.get(self.prefix + "/lifecycle").status_code, 200)
            self.assertEqual(client.get(self.prefix + "/active").status_code, 409)
            response = client.put(
                self.prefix + "/lifecycle",
                json={
                    "state": "active",
                    "expected_revision": 1,
                    "confirmation": self.project,
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(client.get(self.prefix).status_code, 200)

    def test_queued_work_must_be_cancelled_and_direct_writes_roll_back(self):
        job = self.store.submit(
            "A", self.project, "owner", "prepare", {}, "pending-fixture"
        )
        self.assertEqual(self.change().status_code, 409)
        self.store.cancel(job["id"], "A", self.project, "owner")
        self.assertEqual(self.change().status_code, 200)
        with self.assertRaises(Conflict):
            self.store.submit(
                "A", self.project, "owner", "prepare", {}, "after-archive-fixture"
            )
        with self.assertRaises(Conflict):
            self.store.add_dataset(
                "A", self.project, "owner", fixtures.fixture_rows(2), {}
            )
        self.assertEqual(len(self.store.jobs("A", self.project)), 1)
        self.assertEqual(self.store.list("A", self.project, "dataset"), [])
        with self.store.transaction() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM record_contents").fetchone()[0], 0
            )

    def test_inflight_http_import_cannot_commit_after_archival(self):
        ready, proceed = threading.Event(), threading.Event()
        original = self.store.add_dataset

        def delayed(*args, **kwargs):
            ready.set()
            if not proceed.wait(15):
                raise RuntimeError("Fixture synchronization timed out")
            return original(*args, **kwargs)

        with ThreadPoolExecutor(max_workers=1) as pool, patch.object(
            self.store, "add_dataset", side_effect=delayed
        ):
            pending = pool.submit(
                lambda: self.client.post(
                    self.prefix + "/datasets",
                    files={
                        "file": (
                            "fictional.jsonl",
                            jsonl(fixtures.fixture_rows(2)).encode(),
                        )
                    },
                )
            )
            try:
                self.assertTrue(ready.wait(15))
                self.assertEqual(self.change().status_code, 200)
            finally:
                proceed.set()
            self.assertEqual(pending.result(timeout=15).status_code, 409)
        self.assertEqual(self.store.list("A", self.project, "dataset"), [])

    def test_cloud_and_paid_call_uncertainty_must_be_resolved(self):
        run = self.store.add("cloud-run", "A", self.project, "owner", {})
        event = self.store.add(
            "cloud-event",
            "A",
            self.project,
            "owner",
            {"run_id": run["id"], "revision": 1, "phase": "unknown"},
        )
        self.assertEqual(self.change().status_code, 409)
        self.store.add(
            "cloud-event",
            "A",
            self.project,
            "owner",
            {"run_id": run["id"], "revision": 2, "phase": "Stopped"},
        )
        call = self.store.add(
            "provider-call",
            "A",
            self.project,
            "owner",
            {"profile_id": "fictional", "result_id": "a" * 32},
        )
        self.assertEqual(self.change().status_code, 409)
        self.store.add(
            "provider-call-reconciliation",
            "A",
            self.project,
            "owner",
            {"call_id": call["id"]},
        )
        plan = self.store.add("generation-plan", "A", self.project, "owner", {})
        self.store.add(
            "generation-process", "A", self.project, "owner", {"plan_id": plan["id"]}
        )
        self.assertEqual(self.change().status_code, 409)
        self.store.add(
            "generation-settlement", "A", self.project, "owner", {"plan_id": plan["id"]}
        )
        self.assertEqual(self.change().status_code, 200)
        self.assertEqual(
            self.store.get(call["id"], "A", self.project)["payload"]["profile_id"],
            "fictional",
        )

    def test_concurrent_owners_cannot_append_two_identical_revisions(self):
        from decision_workbench.platform.project_lifecycle import transition

        principal = {"tenant": "A", "actor": "owner", "role": "admin"}
        barrier = threading.Barrier(2)

        def change():
            barrier.wait(timeout=10)
            try:
                return transition(
                    Store(self.home),
                    principal,
                    self.project,
                    "archived",
                    0,
                    self.project,
                )["revision"]
            except Conflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(
                list(pool.map(lambda _: change(), range(2))), [1, "conflict"]
            )
        validate_history(self.store)

    def test_recovery_preserves_archival_and_rejects_history_tampering(self):
        self.assertEqual(self.change().status_code, 200)
        target = Path(self.temp.name) / "backup"
        result = backup_workspace(self.home, target)
        restored = Path(self.temp.name) / "restored"
        restore_workspace(target, restored, result["manifest_sha256"])
        reopened = create_app(restored, fixtures.keys())
        with TestClient(reopened) as client:
            self.assertEqual(
                client.get(
                    self.prefix, headers={"Authorization": "Bearer a-secret"}
                ).status_code,
                409,
            )
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT id,payload FROM entities WHERE kind='project-lifecycle'"
            ).fetchone()
            value = json.loads(row["payload"])
            value["state"] = "active"
            db.execute(
                "UPDATE entities SET payload=? WHERE id=?", (encode(value), row["id"])
            )
        with self.assertRaises(ValueError):
            backup_workspace(self.home, Path(self.temp.name) / "tampered")

    def test_inflight_cpu_prediction_is_refused_and_restore_resumes_same_release(self):
        from decision_workbench.platform.worker import Worker
        from decision_workbench.platform import baseline

        snapshot = self.snapshot()
        job = self.job(
            "train",
            {"snapshot": snapshot, "backend": "cpu-baseline"},
            "lifecycle-train",
        )
        Worker(Store(self.home)).once()
        model = self.store.job(job["id"], "A", self.project)["result"]["model"]
        released = self.client.post(
            self.prefix + "/releases",
            json={
                "model_id": model,
                "expected_revision": 0,
                "note": "Fictional lifecycle release",
            },
        )
        self.assertEqual(released.status_code, 200, released.text)
        ready, proceed = threading.Event(), threading.Event()
        original = baseline.predict

        def delayed(*args, **kwargs):
            result = original(*args, **kwargs)
            ready.set()
            if not proceed.wait(15):
                raise RuntimeError("Fixture synchronization timed out")
            return result

        request = {"state": "invoice payment refund requested urgent", "threshold": 0.5}
        with ThreadPoolExecutor(max_workers=1) as pool, patch.object(
            baseline, "predict", side_effect=delayed
        ):
            pending = pool.submit(
                lambda: self.client.post(self.prefix + "/decisions", json=request)
            )
            try:
                self.assertTrue(ready.wait(15))
                self.assertEqual(self.change().status_code, 200)
            finally:
                proceed.set()
            self.assertEqual(pending.result(timeout=15).status_code, 409)
        self.assertEqual(self.change("active", 1).status_code, 200)
        resumed = self.client.post(self.prefix + "/decisions", json=request)
        self.assertEqual(resumed.status_code, 200, resumed.text)
        self.assertEqual(resumed.json()["model_id"], model)
        self.assertEqual(resumed.json()["release_revision"], 1)


@unittest.skipUnless(
    pg.URL, "Set DMT_TEST_POSTGRES_URL for cross-node lifecycle checks"
)
class SharedProjectLifecycleTests(pg.DatabaseFixture, unittest.TestCase):
    def test_archival_is_visible_on_another_node_and_guards_its_writes(self):
        from decision_workbench.platform.project_lifecycle import transition, view

        self.database_setup()
        with tempfile.TemporaryDirectory() as folder:
            first = self.store(Path(folder) / "first")
            second = self.store(Path(folder) / "second")
            principal = {"tenant": "A", "actor": "owner", "role": "admin"}
            from decision_workbench.platform.service import Service

            project = Service(first).create_project(
                principal,
                "Fictional shared project",
                "A reviewed policy",
                fixtures.QUESTIONS,
            )["id"]
            transition(first, principal, project, "archived", 0, project)
            self.assertEqual(view(second, principal, project)["state"], "archived")
            with self.assertRaises(Conflict):
                second.add_dataset("A", project, "owner", fixtures.fixture_rows(2), {})
            transition(second, principal, project, "active", 1, project)
            self.assertEqual(view(first, principal, project)["revision"], 2)
            validate_history(first)


class ProjectLifecycleBrowserTests(unittest.TestCase):
    def test_browser_archive_and_restore_against_real_localhost_api(self):
        from http_server_fixture import LiveServer
        from streamlit.testing.v1 import AppTest

        with tempfile.TemporaryDirectory() as folder, LiveServer(
            folder, fixtures.keys()
        ) as server:
            principal = {"tenant": "A", "actor": "owner", "role": "admin"}
            project = server.app.state.service.create_project(
                principal,
                "Fictional UI project",
                "A reviewed policy",
                fixtures.QUESTIONS,
            )["id"]
            app = AppTest.from_file(
                str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30
            )
            app.session_state["credential"] = "a-secret"
            app.session_state["api_url"] = server.url
            app.session_state["principal"] = principal
            app.run()
            self.assertFalse(app.exception)

            def widget(kind, label):
                return next(w for w in getattr(app, kind) if w.label == label)

            widget("text_input", "Confirm project ID").set_value(project)
            widget(
                "checkbox",
                "I understand that data is retained and restoration resumes the existing release.",
            ).check().run()
            widget("button", "Archive project").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("archived" in w.value for w in app.warning))
            self.assertFalse(any(b.label == "Run training" for b in app.button))
            widget("button", "Restore project").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(
                server.app.state.store.list("A", project, "project-lifecycle")[0][
                    "payload"
                ]["state"],
                "active",
            )


if __name__ == "__main__":
    unittest.main()
