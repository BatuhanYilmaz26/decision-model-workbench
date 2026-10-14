"""Durable incidents, real worker process death, migrations, recovery and browser operations."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

import httpx
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest
from http_server_fixture import LiveServer
import test_platform as fixtures
from decision_workbench.platform.api import create_app
from decision_workbench.platform.store import Store, Conflict
from decision_workbench.platform.service import Service
from decision_workbench.platform.worker import Worker
from decision_workbench.platform.recovery import (
    backup_workspace,
    verify_backup,
    restore_workspace,
)


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.home = self.folder / "workspace"
        self.settings = patch.dict(
            os.environ,
            {
                "DMT_MONITOR_INTERVAL_SECONDS": "60",
                "DMT_WORKER_MAX_AGE_SECONDS": "5",
                "DMT_WORKER_ALERT_AFTER_SECONDS": "1",
                "DMT_QUEUE_ALERT_AFTER_SECONDS": "2",
            },
        )
        self.settings.start()
        keys = fixtures.keys() + [
            {
                "key_sha256": __import__("hashlib")
                .sha256(b"editor-secret")
                .hexdigest(),
                "tenant": "A",
                "actor": "editor",
                "role": "editor",
            }
        ]
        self.keys = keys
        self.app = create_app(self.home, keys)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.store = self.app.state.store
        self.service = self.app.state.service
        self.owner = {"tenant": "A", "actor": "owner", "role": "admin"}
        self.project = self.service.create_project(
            self.owner,
            "Operations fixture",
            "Fictional support routing",
            fixtures.QUESTIONS,
        )["id"]
        self.prefix = f"/v1/projects/{self.project}"
        self.root = self.service.save_dataset(
            self.owner, self.project, fixtures.fixture_rows(), "fixture"
        )

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.settings.stop()
        self.temp.cleanup()

    def call(self, method, path, token="a-secret", **kwargs):
        return self.client.request(
            method, path, headers={"Authorization": "Bearer " + token}, **kwargs
        )

    def health(self):
        reply = self.call("GET", self.prefix + "/operations")
        self.assertEqual(reply.status_code, 200, reply.text)
        return reply.json()

    def collect(self):
        self.app.state.monitor.check()
        self.assertIsNone(self.app.state.monitor.last_error)

    def queue(self, key="ops-prepare"):
        return self.service.submit(
            self.owner, self.project, "prepare", {"datasets": [self.root["id"]]}, key
        )

    def failure_job(self):
        draft = self.service.save_examples(
            self.owner,
            self.project,
            [{"id": "unknown-fixture", "state": "unsupported example"}],
        )
        job = self.service.submit(
            self.owner,
            self.project,
            "prepare",
            {"datasets": [draft["id"]]},
            "ops-failure",
        )
        worker = Worker(self.store)
        try:
            worker.once()
        finally:
            worker.service.runtime.close()
        self.assertEqual(
            self.store.job(job["id"], "A", self.project)["status"], "failed"
        )
        return job

    def test_real_failure_ack_resolution_history_scope_and_restart(self):
        job = self.failure_job()
        health = self.health()
        self.assertEqual(health["job_counts"], {"failed": 1})
        self.assertEqual(
            health["incident_counts"], {"total": 1, "open": 1, "unacknowledged": 1}
        )
        incident = health["incidents"][0]
        self.assertEqual(incident["target"], job["id"])
        self.assertNotIn("error", incident)
        path = self.prefix + "/operations/incidents/" + incident["id"]
        acknowledged = self.call(
            "POST",
            path + "/actions",
            json={
                "revision": 1,
                "action": "acknowledge",
                "note": "Inspected private fixture failure log",
            },
        )
        self.assertEqual(acknowledged.status_code, 200, acknowledged.text)
        self.assertEqual(acknowledged.json()["acknowledged_by"], "owner")
        self.assertEqual(
            self.call(
                "POST",
                path + "/actions",
                json={"revision": 1, "action": "acknowledge", "note": "Stale"},
            ).status_code,
            409,
        )
        self.assertEqual(
            self.call("GET", self.prefix + "/operations", token="b-secret").status_code,
            404,
        )
        self.assertEqual(
            self.call(
                "GET", self.prefix + "/operations", token="infer-secret"
            ).status_code,
            403,
        )
        self.assertEqual(
            self.call(
                "GET", self.prefix + "/operations", token="review-secret"
            ).status_code,
            403,
        )
        self.assertEqual(
            self.call(
                "GET", self.prefix + "/operations", token="editor-secret"
            ).status_code,
            200,
        )
        self.assertEqual(
            self.call(
                "POST",
                path + "/actions",
                token="editor-secret",
                json={"revision": 2, "action": "resolve", "note": "Not owner"},
            ).status_code,
            403,
        )
        history = self.call("GET", path + "/history").json()
        self.assertEqual([row["action"] for row in history], ["acknowledged", "opened"])
        resolved = self.call(
            "POST",
            path + "/actions",
            json={
                "revision": 2,
                "action": "resolve",
                "note": "Reviewed terminal failure; no replay authorized",
            },
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)
        self.collect()
        self.collect()
        self.assertEqual(self.health()["incidents"][0]["status"], "resolved")
        with TestClient(create_app(self.home, self.keys)) as restarted:
            reply = restarted.get(
                path + "/history", headers={"Authorization": "Bearer a-secret"}
            )
            self.assertEqual(len(reply.json()), 3)
            self.assertEqual(reply.json()[0]["actor"], "owner")
        self.assertIsNone(self.store.claim())

    def test_transient_recovery_recurrence_and_acknowledgement_is_not_repair(self):
        job = self.queue()
        with self.store.transaction() as db:
            db.execute(
                "UPDATE jobs SET created=? WHERE id=?", (time.time() - 10, job["id"])
            )
        self.collect()
        items = {row["kind"]: row for row in self.health()["incidents"]}
        self.assertEqual(set(items), {"worker_unavailable", "queue_delayed"})
        missing = items["worker_unavailable"]
        path = self.prefix + "/operations/incidents/" + missing["id"] + "/actions"
        ack = self.call(
            "POST",
            path,
            json={
                "revision": 1,
                "action": "acknowledge",
                "note": "Operator is checking worker launch",
            },
        )
        self.assertEqual(ack.status_code, 200, ack.text)
        self.assertEqual(
            self.call(
                "POST",
                path,
                json={"revision": 2, "action": "resolve", "note": "No false repair"},
            ).status_code,
            400,
        )
        worker_id = uuid.uuid4().hex
        self.store.worker_presence(worker_id, "idle")
        self.collect()
        recovered = {row["kind"]: row for row in self.health()["incidents"]}[
            "worker_unavailable"
        ]
        self.assertEqual(recovered["status"], "resolved")
        self.assertTrue(self.health()["worker_alive"])
        self.store.worker_presence(worker_id, "stopped")
        self.collect()
        recurring = {row["kind"]: row for row in self.health()["incidents"]}[
            "worker_unavailable"
        ]
        self.assertEqual(recurring["id"], missing["id"])
        self.assertEqual(recurring["occurrences"], 2)
        self.assertEqual(recurring["revision"], 4)
        self.assertIsNone(recurring["acknowledged_at"])
        self.store.cancel(job["id"], "A", self.project, "owner")
        self.collect()
        self.assertEqual(self.health()["incident_counts"]["open"], 0)
        history = self.call(
            "GET", self.prefix + "/operations/incidents/" + missing["id"] + "/history"
        ).json()
        self.assertEqual(
            [r["action"] for r in history],
            ["resolved", "reopened", "resolved", "acknowledged", "opened"],
        )

    def test_expired_job_ownership_alert_and_actual_claim_recovery(self):
        self.queue()
        owned = self.store.claim(lease_seconds=0.01)
        time.sleep(0.03)
        self.collect()
        incident = self.health()["incidents"][0]
        self.assertEqual(incident["kind"], "job_lease_expired")
        with self.assertRaises(Conflict):
            self.store.heartbeat(owned)
        self.assertIsNone(self.store.claim())
        self.collect()
        kinds = {item["kind"]: item for item in self.health()["incidents"]}
        self.assertEqual(kinds["job_lease_expired"]["status"], "resolved")
        self.assertEqual(kinds["job_interrupted"]["status"], "open")
        self.assertEqual(
            self.store.job(owned["id"], "A", self.project)["status"], "interrupted"
        )
        self.assertIsNone(self.store.job(owned["id"], "A", self.project)["token"])
        self.assertIsNone(self.store.job(owned["id"], "A", self.project)["lease"])

    def test_actual_idle_worker_death_observation_and_new_worker_execution(self):
        with patch.dict(os.environ, {"DMT_MONITOR_INTERVAL_SECONDS": "1"}):
            with LiveServer(self.home, self.keys) as server:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "decision_workbench.platform.worker",
                        "--home",
                        str(self.home),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                try:
                    with httpx.Client(
                        base_url=server.url,
                        headers={"Authorization": "Bearer a-secret"},
                        timeout=5,
                    ) as client:
                        deadline = time.monotonic() + 12
                        while True:
                            response = client.get(self.prefix + "/operations")
                            self.assertEqual(response.status_code, 200, response.text)
                            if response.json()["worker_alive"]:
                                break
                            if (
                                process.poll() is not None
                                or time.monotonic() > deadline
                            ):
                                self.fail("Real worker did not establish idle presence")
                            time.sleep(0.1)
                        process.terminate()
                        process.wait(timeout=15)
                        queued = client.post(
                            self.prefix + "/jobs",
                            json={
                                "kind": "prepare",
                                "config": {"datasets": [self.root["id"]]},
                                "request_key": "after-real-worker-death",
                            },
                        )
                        self.assertEqual(queued.status_code, 202, queued.text)
                        deadline = time.monotonic() + 12
                        while True:
                            health = client.get(self.prefix + "/operations").json()
                            if not health["worker_alive"] and any(
                                i["kind"] == "worker_unavailable"
                                and i["status"] == "open"
                                for i in health["incidents"]
                            ):
                                break
                            if time.monotonic() > deadline:
                                self.fail(
                                    "Worker death was not observed by the actual collector"
                                )
                            time.sleep(0.15)
                        self.assertEqual(client.get("/ready").status_code, 200)
                        restarted = subprocess.run(
                            [
                                sys.executable,
                                "-m",
                                "decision_workbench.platform.worker",
                                "--home",
                                str(self.home),
                                "--once",
                            ],
                            capture_output=True,
                            text=True,
                            timeout=45,
                        )
                        self.assertEqual(restarted.returncode, 0, restarted.stderr)
                        self.assertEqual(
                            self.store.job(queued.json()["id"], "A", self.project)[
                                "status"
                            ],
                            "succeeded",
                        )
                        deadline = time.monotonic() + 8
                        while True:
                            health = client.get(self.prefix + "/operations").json()
                            if health["incident_counts"]["open"] == 0:
                                break
                            if time.monotonic() > deadline:
                                self.fail("Recovered queue incidents did not resolve")
                            time.sleep(0.15)
                finally:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=15)
                    if process.stderr:
                        process.stderr.close()

    def test_collector_failure_readiness_config_validation_and_recovery(self):
        with patch.object(
            self.store,
            "collect_operations",
            side_effect=sqlite3.OperationalError(
                "private path and secret must not escape"
            ),
        ):
            self.app.state.monitor.check()
        self.assertEqual(self.client.get("/ready").status_code, 503)
        health = self.health()
        self.assertEqual(health["collector"]["status"], "unavailable")
        self.assertEqual(health["collector"]["last_error"], "OperationalError")
        self.assertNotIn("secret", json.dumps(health))
        self.collect()
        self.assertEqual(self.client.get("/ready").status_code, 200)
        for key, value in [
            ("DMT_MONITOR_INTERVAL_SECONDS", "nan"),
            ("DMT_WORKER_MAX_AGE_SECONDS", "0"),
            ("DMT_QUEUE_ALERT_AFTER_SECONDS", "inf"),
        ]:
            with patch.dict(os.environ, {key: value}):
                with self.assertRaises(ValueError):
                    create_app(self.home, self.keys)

    def test_schema3_and_schema7_recovery_preserve_history_without_worker_ghosts(self):
        self.failure_job()
        incident = self.health()["incidents"][0]
        self.store.incident_action(
            "A",
            self.project,
            incident["id"],
            "owner",
            1,
            "acknowledge",
            "Private failure reviewed",
        )
        self.store.worker_presence(uuid.uuid4().hex, "idle")
        with self.store.transaction() as db:
            entities = [
                tuple(row) for row in db.execute("SELECT * FROM entities ORDER BY id")
            ]
        backup = backup_workspace(self.home, self.folder / "backup4")
        manifest = verify_backup(backup["destination"], backup["manifest_sha256"])
        self.assertEqual(manifest["schema_version"], 7)
        receipt = restore_workspace(
            backup["destination"], self.folder / "restored4", backup["manifest_sha256"]
        )
        self.assertGreaterEqual(receipt["cleared_worker_heartbeats"], 1)
        recovered = Store(receipt["destination"])
        self.assertFalse(recovered.operations("A", self.project, 5)["worker_alive"])
        self.assertEqual(
            recovered.incident_history("A", self.project, incident["id"]),
            self.store.incident_history("A", self.project, incident["id"]),
        )
        with recovered.transaction() as db:
            self.assertEqual(
                [
                    tuple(row)
                    for row in db.execute("SELECT * FROM entities ORDER BY id")
                ],
                entities,
            )
        old = Store(self.folder / "schema3")
        old_service = Service(old, runtime=object())
        old_project = old_service.create_project(
            self.owner, "Old schema fixture", "Fictional policy", fixtures.QUESTIONS
        )["id"]
        old_service.save_dataset(
            self.owner, old_project, fixtures.fixture_rows(), "fixture"
        )
        with old.transaction() as db:
            db.execute("DROP TABLE incident_history")
            db.execute("DROP TABLE operational_incidents")
            db.execute("DROP TABLE worker_heartbeats")
            db.execute("DELETE FROM schema_version WHERE version>=4")
            original = [
                tuple(row) for row in db.execute("SELECT * FROM entities ORDER BY id")
            ]
        prior = backup_workspace(old.folder, self.folder / "backup3")
        self.assertEqual(
            verify_backup(prior["destination"], prior["manifest_sha256"])[
                "schema_version"
            ],
            3,
        )
        restored = restore_workspace(
            prior["destination"], self.folder / "restored3", prior["manifest_sha256"]
        )
        self.assertEqual(restored["source_schema"], 3)
        self.assertEqual(restored["restored_schema"], 7)
        upgraded = Store(restored["destination"])
        with upgraded.transaction() as db:
            self.assertEqual(
                db.execute("SELECT max(version) FROM schema_version").fetchone()[0], 7
            )
            self.assertEqual(
                [
                    tuple(row)
                    for row in db.execute("SELECT * FROM entities ORDER BY id")
                ],
                original,
            )
        with old.transaction() as db:
            self.assertEqual(
                db.execute("SELECT max(version) FROM schema_version").fetchone()[0], 3
            )

    def test_browser_scoped_health_alert_acknowledgement_and_history(self):
        self.failure_job()
        with LiveServer(self.home, self.keys) as server:
            app = AppTest.from_file(
                str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30
            ).run()
            app.text_input[0].set_value(server.url)
            app.text_input[1].set_value("a-secret")
            next(b for b in app.button if b.label == "Connect").click().run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any("Job failed" in error.value for error in app.error))
            next(t for t in app.text_input if t.label == "Action note").set_value(
                "Reviewed failure from the browser"
            )
            next(b for b in app.button if b.label == "Save alert action").click().run(
                timeout=30
            )
            self.assertEqual(len(app.exception), 0)
            health = self.health()
            self.assertEqual(health["incident_counts"]["unacknowledged"], 0)
            self.assertEqual(health["incidents"][0]["acknowledged_by"], "owner")
            next(
                c for c in app.checkbox if c.label == "Show alert history"
            ).check().run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(
                any("Acknowledged by owner" in c.value for c in app.caption)
            )

    def test_bounded_collector_catchup_and_corrupted_incident_history(self):
        template = self.store.get(self.project, "A", self.project, "project")["payload"]
        with self.store.transaction() as db:
            for number in range(501):
                identifier = f"{number:032x}"
                db.execute(
                    "INSERT INTO entities VALUES (?,?,?,?,?,?)",
                    (
                        identifier,
                        "project",
                        "many",
                        identifier,
                        json.dumps(template),
                        time.time(),
                    ),
                )
                self.store._condition(
                    db,
                    "many",
                    identifier,
                    "worker_unavailable",
                    "worker",
                    True,
                    time.time(),
                )
        cursor = self.store.collect_operations(5, 1, 2)
        with self.store.transaction() as db:
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM operational_incidents WHERE tenant='many' AND status='resolved'"
                ).fetchone()[0],
                500,
            )
        self.store.collect_operations(5, 1, 2, cursor)
        with self.store.transaction() as db:
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM operational_incidents WHERE tenant='many' AND status='resolved'"
                ).fetchone()[0],
                501,
            )
            db.execute(
                "DELETE FROM incident_history WHERE id=(SELECT min(id) FROM incident_history)"
            )
        with self.assertRaisesRegex(ValueError, "history integrity"):
            backup_workspace(self.home, self.folder / "corrupt-history")
        self.assertFalse((self.folder / "corrupt-history").exists())


if __name__ == "__main__":
    unittest.main()
