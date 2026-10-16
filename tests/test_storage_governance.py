"""Behavioral acceptance for storage upgrades, review races and frozen policy."""

from decision_workbench.platform.store import WORKSPACE_SCHEMA_VERSION

import copy
import json
import sqlite3
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
import test_platform as platform_fixtures

from decision_workbench.platform.store import Store, Conflict, digest
from decision_workbench.platform.service import Service
from decision_workbench.platform.worker import Worker

fixture_rows = platform_fixtures.fixture_rows
QUESTIONS = platform_fixtures.QUESTIONS


class StorageGovernanceTests(unittest.TestCase):
    setUp = platform_fixtures.PlatformTests.setUp
    tearDown = platform_fixtures.PlatformTests.tearDown
    dataset = platform_fixtures.PlatformTests.dataset
    snapshot = platform_fixtures.PlatformTests.snapshot
    job = platform_fixtures.PlatformTests.job

    def test_twenty_thousand_rows_use_constant_size_review_revisions_and_pages(self):
        principal = {"tenant": "A", "actor": "owner", "role": "admin"}
        rows = fixture_rows(20000)
        dataset = self.app.state.service.save_dataset(
            principal, self.project, rows, {"fixture": True}
        )
        root = dataset["id"]
        for row in rows[:100]:
            dataset = self.app.state.service.review(
                principal,
                self.project,
                dataset["id"],
                row["id"],
                row["gold"],
                True,
                dataset["payload"]["sha256"],
                "Fictional fixture review",
            )
        page = self.client.get(
            self.prefix + "/datasets/" + dataset["id"] + "/rows?offset=19990&limit=10"
        )
        self.assertEqual(page.status_code, 200, page.text)
        self.assertEqual(page.json()["total"], 20000)
        self.assertEqual(
            [r["id"] for r in page.json()["rows"]], [r["id"] for r in rows[-10:]]
        )
        self.assertLess(len(page.content), 25000)
        summaries = self.client.get(
            self.prefix + "/entities/dataset?limit=20&offset=20"
        ).json()
        self.assertEqual(len(summaries), 20)
        self.assertTrue(all("rows" not in d["payload"] for d in summaries))
        with self.app.state.store.transaction() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM dataset_members").fetchone()[0], 20000
            )
            self.assertEqual(
                db.execute("SELECT count(*) FROM record_contents").fetchone()[0], 20100
            )
            self.assertEqual(
                db.execute("SELECT count(*) FROM dataset_versions").fetchone()[0], 101
            )
            self.assertLess(
                db.execute(
                    "SELECT sum(length(payload)) FROM entities WHERE kind='dataset'"
                ).fetchone()[0],
                150000,
            )
        loaded = self.app.state.store.verify_dataset(dataset["id"], "A", self.project)
        self.assertEqual(len(loaded), 20000)
        self.assertEqual(loaded[0]["metadata"]["reviewer"], "owner")
        self.assertNotIn(
            "reviewer",
            self.app.state.store.dataset_rows(root, "A", self.project, limit=1)[0][
                "metadata"
            ],
        )
        with self.app.state.store.transaction() as db:
            checksum = db.execute(
                "SELECT content_hash FROM dataset_members WHERE root=? AND ordinal=19999",
                (root,),
            ).fetchone()[0]
            db.execute(
                "UPDATE record_contents SET payload=? WHERE hash=?", ("{}", checksum)
            )
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.app.state.store.verify_dataset(dataset["id"], "A", self.project)

    def test_concurrent_reviews_cannot_silently_branch_and_tenants_cannot_page(self):
        dataset = self.dataset(12)
        row = self.app.state.store.dataset_rows(
            dataset["id"], "A", self.project, limit=1
        )[0]

        def review(actor):
            service = Service(Store(self.home))
            try:
                return service.review(
                    {"tenant": "A", "actor": actor, "role": "reviewer"},
                    self.project,
                    dataset["id"],
                    row["id"],
                    row["gold"],
                    True,
                    dataset["payload"]["sha256"],
                    actor,
                )
            except Conflict:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(review, ["reviewer-one", "reviewer-two"]))
        self.assertEqual(sum(r is not None for r in results), 1)
        winner = next(r for r in results if r)
        self.assertEqual(
            self.app.state.store.dataset_head(dataset["id"], "A", self.project),
            winner["id"],
        )
        path = self.prefix + "/datasets/" + winner["id"] + "/rows"
        self.assertEqual(
            self.client.get(
                path, headers={"Authorization": "Bearer b-secret"}
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                path, headers={"Authorization": "Bearer infer-secret"}
            ).status_code,
            403,
        )
        self.assertEqual(self.client.get(path + "?limit=201").status_code, 422)
        self.assertEqual(self.client.get(path + "?offset=-1").status_code, 422)

    def test_v1_database_migrates_without_changing_pinned_dataset_hashes(self):
        legacy_home = self.home / "old-workspace"
        legacy_home.mkdir()
        rows = fixture_rows(12)
        with sqlite3.connect(legacy_home / "workspace.sqlite3") as db:
            db.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
            db.execute("INSERT INTO schema_version VALUES (1)")
            db.execute(
                "CREATE TABLE entities (id TEXT PRIMARY KEY,kind TEXT NOT NULL,tenant TEXT NOT NULL,project TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL)"
            )
            db.execute(
                "INSERT INTO entities VALUES (?,?,?,?,?,?)",
                (
                    "legacy-data",
                    "dataset",
                    "A",
                    "legacy-project",
                    json.dumps(
                        {
                            "rows": rows,
                            "sha256": digest(rows),
                            "provenance": {"fixture": True},
                        }
                    ),
                    time.time(),
                ),
            )
        db.close()
        upgraded = Store(legacy_home)
        legacy = upgraded.get("legacy-data", "A", "legacy-project", include_rows=False)
        self.assertEqual(legacy["payload"]["sha256"], digest(rows))
        self.assertEqual(
            upgraded.verify_dataset("legacy-data", "A", "legacy-project"), rows
        )
        new = upgraded.review_dataset(
            "A",
            "legacy-project",
            "reviewer",
            "legacy-data",
            {**rows[0], "metadata": {**rows[0]["metadata"], "reviewer": "reviewer"}},
            digest(rows),
            "Migration review",
        )
        self.assertEqual(
            upgraded.verify_dataset(new["id"], "A", "legacy-project")[0]["metadata"][
                "reviewer"
            ],
            "reviewer",
        )
        appended_row = copy.deepcopy(rows[0])
        appended_row.update(
            id="legacy-appended",
            group_id="legacy-appended",
            state="Fictional additional legacy-workspace example",
        )
        appended_row["metadata"].update(reviewed=False, review_status="pending")
        appended = upgraded.append_dataset(
            "A",
            "legacy-project",
            "editor",
            new["id"],
            [appended_row],
            new["payload"]["sha256"],
            {"fictional": True},
        )
        self.assertEqual(
            upgraded.verify_dataset("legacy-data", "A", "legacy-project"), rows
        )
        self.assertEqual(
            len(upgraded.verify_dataset(new["id"], "A", "legacy-project")), 12
        )
        self.assertEqual(
            upgraded.verify_dataset(appended["id"], "A", "legacy-project")[-1],
            appended_row,
        )
        restarted = Store(legacy_home)
        self.assertEqual(
            restarted.dataset_head("legacy-data", "A", "legacy-project"), appended["id"]
        )
        with restarted.transaction() as db:
            self.assertEqual(
                db.execute("SELECT max(version) FROM schema_version").fetchone()[0],
                WORKSPACE_SCHEMA_VERSION,
            )
            self.assertEqual(
                db.execute("SELECT count(*) FROM dataset_members").fetchone()[0], 13
            )

    def test_frozen_owner_gates_cannot_be_relaxed_after_training_and_successors_preserve_contracts(
        self,
    ):
        snapshot = self.snapshot()
        config = {"snapshot": snapshot, "backend": "cpu-baseline"}
        missing = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "train",
                "config": config,
                "request_key": "missing-gate-policy",
            },
        )
        self.assertEqual(missing.status_code, 409)
        gates = {
            "min_accuracy": 0.0,
            "min_macro_f1": 0.0,
            "max_ece": 1.0,
            "min_test_rows": 100,
        }
        self.assertEqual(
            self.client.put(
                self.prefix + "/release-policy",
                json=gates,
                headers={"Authorization": "Bearer review-secret"},
            ).status_code,
            403,
        )
        policy = self.client.put(self.prefix + "/release-policy", json=gates)
        self.assertEqual(policy.status_code, 200, policy.text)
        policy_id = policy.json()["policy"]["id"]
        self.assertEqual(
            self.client.put(self.prefix + "/release-policy", json=gates).status_code,
            409,
        )
        job = self.job("train", config, "frozen-gate-training")
        self.assertEqual(job["config"]["release_policy_id"], policy_id)
        # Criteria freeze at submission, before the worker produces any result.
        relaxed = self.client.put(
            self.prefix + "/release-policy",
            json={**gates, "min_test_rows": 10, "expected_id": policy_id},
        )
        self.assertEqual(relaxed.status_code, 409)
        Worker(Store(self.home)).once()
        trained = self.app.state.store.job(job["id"], "A", self.project)
        self.assertEqual(trained["status"], "succeeded", trained["error"])
        release = self.client.post(
            self.prefix + "/releases",
            json={
                "model_id": trained["result"]["model"],
                "expected_revision": 0,
                "note": "Must fail frozen count gate",
            },
        )
        self.assertEqual(release.status_code, 400, release.text)
        self.assertIn("frozen", release.text)
        successor = self.client.post(
            "/v1/projects",
            json={
                "name": "Policy v2",
                "description": "Updated domain policy",
                "questions": QUESTIONS,
                "parent_project": self.project,
            },
        )
        self.assertEqual(successor.status_code, 201, successor.text)
        self.assertEqual(successor.json()["payload"]["policy_version"], 2)
        self.assertEqual(successor.json()["payload"]["parent_project"], self.project)
        successor_path = "/v1/projects/" + successor.json()["id"]
        self.assertEqual(
            self.client.get(self.prefix).json()["payload"]["policy_version"], 1
        )
        self.assertEqual(
            self.client.get(successor_path + "/entities/dataset").json(), []
        )
        self.assertEqual(
            self.client.get(successor_path + "/release-policy").json()["policy"], None
        )
        self.assertEqual(
            self.client.get(self.prefix + "/release-policy").json()["policy"]["id"],
            policy_id,
        )
        cross_tenant = self.client.post(
            "/v1/projects",
            json={
                "name": "Bad parent",
                "description": "Cannot use another tenant",
                "questions": QUESTIONS,
                "parent_project": self.project,
            },
            headers={"Authorization": "Bearer b-secret"},
        )
        self.assertEqual(cross_tenant.status_code, 404)


if __name__ == "__main__":
    unittest.main()
