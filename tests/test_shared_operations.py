"""A declared two-node workload with quotas, CPU studies and failure recovery."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from decision_workbench.platform.api import create_app
from decision_workbench.platform.artifact_storage import (
    ArtifactRepository,
    ArtifactUnavailable,
    DirectoryObjects,
)
from decision_workbench.platform.store import Store, encode
from decision_workbench.platform.worker import Worker
import test_platform as fixtures
import test_postgres as pg


@unittest.skipUnless(pg.URL, "Set DMT_TEST_POSTGRES_URL for shared workload checks")
class SharedOperationsTests(pg.DatabaseFixture, unittest.TestCase):
    def setUp(self):
        self.database_setup()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.repo = ArtifactRepository(DirectoryObjects(self.home / "objects"))
        self.clients = []
        for i in range(2):
            app = create_app(
                self.home / ("api-" + str(i)),
                fixtures.keys(),
                database_url=pg.URL,
                database_schema=self.schema,
                artifact_store=self.repo,
            )
            client = TestClient(app)
            client.__enter__()
            self.addCleanup(client.__exit__, None, None, None)
            self.clients.append(client)
        self.store = self.clients[0].app.state.store
        self.projects = {"A": [], "B": []}
        self.tokens = {"A": "a-secret", "B": "b-secret"}
        for tenant in self.projects:
            for index in range(2):
                response = self.clients[index].post(
                    "/v1/projects",
                    json={
                        "name": "Fictional shared workload",
                        "description": "Fictional decision policy",
                        "questions": fixtures.QUESTIONS,
                    },
                    headers=self.headers(tenant),
                )
                self.assertEqual(response.status_code, 201, response.text)
                self.projects[tenant].append(response.json()["id"])

    def headers(self, tenant):
        return {"Authorization": "Bearer " + self.tokens[tenant]}

    def workers(self):
        result = []
        for index in range(2):
            store = Store(
                self.home / ("worker-" + str(index)),
                database_url=pg.URL,
                database_schema=self.schema,
                artifact_store=self.repo,
            )
            worker = Worker(store)
            self.addCleanup(worker.service.runtime.close)
            result.append(worker)
        return result

    def test_declared_workload_quota_replay_two_workers_releases_and_storage_recovery(
        self,
    ):
        datasets = {}
        for tenant, projects in self.projects.items():
            for project in projects:
                rows = fixtures.fixture_rows()
                for row in rows:
                    row["metadata"].update(
                        review_status="approved",
                        reviewer="owner",
                        review_note="Fictional fixture labels checked",
                    )
                datasets[project] = self.clients[0].app.state.service.save_dataset(
                    {"tenant": tenant, "actor": "owner", "role": "admin"},
                    project,
                    rows,
                    {"fixture_review": True},
                )["id"]

        def submit(index):
            tenant = "A" if index < 8 else "B"
            project = self.projects[tenant][index % 2]
            body = {
                "kind": "prepare",
                "config": {"datasets": [datasets[project]]},
                "request_key": "shared-work-" + str(index),
            }
            response = self.clients[index % 2].post(
                "/v1/projects/" + project + "/jobs",
                json=body,
                headers=self.headers(tenant),
            )
            return tenant, project, body, response

        began = time.monotonic()
        with ThreadPoolExecutor(max_workers=8) as executor:
            submitted = list(executor.map(submit, range(16)))
        elapsed = time.monotonic() - began
        self.assertLess(elapsed, 60, "Declared local submission window exceeded")
        accepted = []
        for tenant in self.projects:
            outcomes = [row for row in submitted if row[0] == tenant]
            self.assertEqual(
                sorted(row[3].status_code for row in outcomes), [202] * 4 + [409] * 4
            )
            accepted.extend(row for row in outcomes if row[3].status_code == 202)
        for tenant, project, body, response in accepted:
            replay = self.clients[1].post(
                "/v1/projects/" + project + "/jobs",
                json=body,
                headers=self.headers(tenant),
            )
            self.assertEqual(replay.status_code, 202, replay.text)
            self.assertEqual(replay.json()["id"], response.json()["id"])
        workers = self.workers()
        with ThreadPoolExecutor(max_workers=2) as executor:
            for _ in range(4):
                self.assertEqual(
                    list(executor.map(lambda worker: worker.once(), workers)),
                    [True, True],
                )
        chosen = {}
        for tenant, project, _, response in accepted:
            result = self.store.job(response.json()["id"], tenant, project)
            self.assertEqual(result["status"], "succeeded", result["error"])
            chosen.setdefault(tenant, (project, result["result"]["snapshot"]))
        train_jobs = []
        for tenant, (project, snapshot) in chosen.items():
            path = "/v1/projects/" + project
            policy = self.clients[0].put(
                path + "/release-policy",
                json={
                    "min_accuracy": 0.0,
                    "min_macro_f1": 0.0,
                    "max_ece": 1.0,
                    "min_test_rows": 10,
                },
                headers=self.headers(tenant),
            )
            self.assertEqual(policy.status_code, 200, policy.text)
            job = self.clients[1].post(
                path + "/jobs",
                json={
                    "kind": "train",
                    "config": {"snapshot": snapshot, "backend": "cpu-baseline"},
                    "request_key": "shared-train-" + tenant,
                },
                headers=self.headers(tenant),
            )
            self.assertEqual(job.status_code, 202, job.text)
            train_jobs.append((tenant, project, job.json()["id"]))
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(
                list(executor.map(lambda worker: worker.once(), workers)), [True, True]
            )
        for tenant, project, identifier in train_jobs:
            job = self.store.job(identifier, tenant, project)
            self.assertEqual(job["status"], "succeeded", job["error"])
            path = "/v1/projects/" + project
            release = self.clients[1].post(
                path + "/releases",
                json={
                    "model_id": job["result"]["model"],
                    "expected_revision": 0,
                    "note": "Fictional two-worker check",
                },
                headers=self.headers(tenant),
            )
            self.assertEqual(release.status_code, 200, release.text)
            decision = self.clients[0].post(
                path + "/decisions",
                json={
                    "state": "invoice payment refund requested urgent",
                    "threshold": 0.5,
                },
                headers=self.headers(tenant),
            )
            self.assertEqual(decision.status_code, 200, decision.text)
            self.assertEqual(decision.json()["answers"]["team"]["answer"], "billing")
        monitor = self.clients[0].app.state.monitor
        monitor.check()
        ready = self.clients[0].get("/ready")
        self.assertEqual(ready.status_code, 200, ready.text)
        self.assertEqual(ready.json()["storage"], "postgresql")
        self.assertEqual(
            ready.json()["artifact_storage"]["probe"], "one-registered-manifest-head"
        )
        with patch.object(
            self.repo.objects,
            "available",
            side_effect=ArtifactUnavailable("fictional private storage outage"),
        ):
            monitor.check()
            unavailable = self.clients[0].get("/ready")
            self.assertEqual(unavailable.status_code, 503)
            self.assertNotIn("private", unavailable.text)
        monitor.check()
        self.assertEqual(self.clients[0].get("/ready").status_code, 200)

    def test_database_and_binding_faults_fail_readiness_without_leaking_details(self):
        client = self.clients[0]
        self.assertEqual(
            client.get("/ready").json()["artifact_storage"]["probe"], "none-no-model"
        )
        with patch.object(
            self.store,
            "transaction",
            side_effect=sqlite3.DatabaseError("fictional private DSN"),
        ):
            result = client.get("/ready")
            self.assertEqual(result.status_code, 503)
            self.assertNotIn("DSN", result.text)
        with self.store.transaction() as db:
            db.execute(
                "UPDATE workspace_artifact_storage SET descriptor='{}' WHERE slot=1"
            )
        self.assertEqual(client.get("/ready").status_code, 503)
        with self.store.transaction() as db:
            db.execute(
                "UPDATE workspace_artifact_storage SET descriptor=? WHERE slot=1",
                (encode(self.repo.descriptor),),
            )
        self.assertEqual(client.get("/ready").status_code, 200)

    def test_worker_failure_and_stale_queue_incidents_are_scoped_and_recover(self):
        project = self.projects["A"][0]
        job = self.store.submit(
            "A",
            project,
            "owner",
            "prepare",
            {"datasets": ["missing-fixture"]},
            "shared-failure",
        )
        worker = self.workers()[0]
        self.assertTrue(worker.once())
        failed = self.store.job(job["id"], "A", project)
        self.assertEqual(failed["status"], "failed")
        monitor = self.clients[1].app.state.monitor
        monitor.check()
        path = "/v1/projects/" + project + "/operations"
        visible = self.clients[1].get(path, headers=self.headers("A"))
        self.assertEqual(visible.status_code, 200, visible.text)
        self.assertIn(
            "job_failed", [row["kind"] for row in visible.json()["incidents"]]
        )
        self.assertEqual(
            self.clients[1].get(path, headers=self.headers("B")).status_code, 404
        )
        queued = self.store.submit(
            "A", project, "owner", "prepare", {"datasets": []}, "shared-stale-queue"
        )
        with self.store.transaction() as db:
            db.execute(
                "UPDATE jobs SET created=? WHERE id=?",
                (time.time() - 600, queued["id"]),
            )
            db.execute("UPDATE worker_heartbeats SET updated=0")
        monitor.check()
        incidents = (
            self.clients[1].get(path, headers=self.headers("A")).json()["incidents"]
        )
        self.assertIn(
            "worker_unavailable",
            [row["kind"] for row in incidents if row["status"] == "open"],
        )
        with self.store.transaction() as db:
            db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (queued["id"],))
        monitor.check()
        incidents = (
            self.clients[1].get(path, headers=self.headers("A")).json()["incidents"]
        )
        self.assertFalse(
            any(
                row["kind"] in {"worker_unavailable", "queue_delayed"}
                and row["status"] == "open"
                for row in incidents
            )
        )


if __name__ == "__main__":
    unittest.main()
