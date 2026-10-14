"""Collection membership/rejection acceptance through real storage and HTTP routes."""

import copy
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from decision_workbench.data import validate
from decision_workbench.platform.service import Service
from decision_workbench.platform.store import Store, Conflict
from decision_workbench.platform.worker import Worker
import test_platform as platform_fixtures

fixture_rows = platform_fixtures.fixture_rows
QUESTIONS = platform_fixtures.QUESTIONS

OWNER = {"tenant": "A", "actor": "owner", "role": "admin"}


class CollectionTests(unittest.TestCase):
    setUp = platform_fixtures.PlatformTests.setUp
    tearDown = platform_fixtures.PlatformTests.tearDown
    dataset = platform_fixtures.PlatformTests.dataset
    job = platform_fixtures.PlatformTests.job

    def append(self, dataset, examples, **kwargs):
        return self.client.post(
            self.prefix + "/datasets/" + dataset["id"] + "/examples",
            json={"examples": examples, "expected_hash": dataset["payload"]["sha256"]},
            **kwargs,
        )

    def example(self, identifier):
        return {
            "id": identifier,
            "state": "fictional new evidence " + identifier,
            "labels": {},
            "origin": "synthetic",
        }

    def rows(self, dataset):
        return self.app.state.store.verify_dataset(dataset["id"], "A", self.project)

    def review(self, dataset, record, status):
        response = self.client.post(
            self.prefix + "/datasets/" + dataset["id"] + "/reviews",
            json={
                "record_id": record["id"],
                "gold": record["gold"],
                "approved": status == "approved",
                "status": status,
                "expected_hash": dataset["payload"]["sha256"],
                "note": "Fictional acceptance annotation: " + status,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def prepare_job(self, dataset, key):
        job = self.job("prepare", {"datasets": [dataset["id"]]}, key)
        Worker(Store(self.home)).once()
        return self.app.state.store.job(job["id"], "A", self.project)

    def test_batched_membership_history_review_paging_and_restart(self):
        root = self.dataset(6)
        original_rows = self.rows(root)
        response = self.append(root, [self.example("new-" + str(i)) for i in range(50)])
        self.assertEqual(response.status_code, 201, response.text)
        batch = response.json()
        self.assertEqual(batch["payload"]["row_count"], 56)
        self.assertEqual(batch["payload"]["reviewed_count"], 6)
        self.assertEqual(self.rows(root), original_rows)
        annotated = self.review(batch, self.rows(batch)[6], "rejected")
        latest = self.append(annotated, [self.example("last")]).json()
        restarted = Store(self.home)
        self.assertEqual(
            restarted.dataset_head(root["id"], "A", self.project), latest["id"]
        )
        self.assertEqual(
            len(restarted.verify_dataset(batch["id"], "A", self.project)), 56
        )
        self.assertEqual(
            restarted.verify_dataset(batch["id"], "A", self.project)[6]["metadata"][
                "review_status"
            ],
            "pending",
        )
        self.assertEqual(
            restarted.verify_dataset(latest["id"], "A", self.project)[6]["metadata"][
                "review_status"
            ],
            "rejected",
        )
        self.assertEqual(self.rows(root), original_rows)
        page = self.client.get(
            self.prefix + "/datasets/" + latest["id"] + "/rows?offset=54&limit=3"
        ).json()
        self.assertEqual([r["id"] for r in page["rows"]], ["new-48", "new-49", "last"])
        with restarted.transaction() as db:
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM dataset_members WHERE root=?", (root["id"],)
                ).fetchone()[0],
                57,
            )
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM dataset_versions WHERE root=?", (root["id"],)
                ).fetchone()[0],
                4,
            )
            self.assertEqual(
                db.execute("SELECT count(*) FROM dataset_appends").fetchone()[0], 2
            )
            self.assertTrue(
                all(
                    "rows" not in json.loads(r[0])
                    for r in db.execute(
                        "SELECT payload FROM entities WHERE kind='dataset'"
                    )
                )
            )

    def test_racing_appends_stale_hashes_and_authorization(self):
        root = self.dataset(6)

        def append(actor):
            service = Service(Store(self.home))
            try:
                return service.save_examples(
                    {**OWNER, "actor": actor},
                    self.project,
                    [self.example(actor)],
                    root["id"],
                    root["payload"]["sha256"],
                )
            except Conflict:
                return None
            finally:
                service.runtime.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(append, ["one", "two"]))
        self.assertEqual(sum(r is not None for r in results), 1)
        winner = next(r for r in results if r)
        self.assertEqual(len(self.rows(winner)), 7)
        self.assertEqual(self.append(root, [self.example("late")]).status_code, 409)
        bad = self.client.post(
            self.prefix + "/datasets/" + winner["id"] + "/examples",
            json={"examples": [self.example("bad")], "expected_hash": "0" * 64},
        )
        self.assertEqual(bad.status_code, 409)
        for token, status in [
            ("b-secret", 404),
            ("review-secret", 403),
            ("infer-secret", 403),
        ]:
            response = self.append(
                winner,
                [self.example(token)],
                headers={"Authorization": "Bearer " + token},
            )
            self.assertEqual(response.status_code, status, response.text)
        other = self.client.post(
            "/v1/projects",
            json={
                "name": "Other",
                "description": "Fictional other domain",
                "questions": QUESTIONS,
            },
        ).json()["id"]
        response = self.client.post(
            "/v1/projects/" + other + "/datasets/" + winner["id"] + "/examples",
            json={
                "examples": [self.example("cross-project")],
                "expected_hash": winner["payload"]["sha256"],
            },
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(len(self.rows(winner)), 7)

    def test_file_append_duplicate_atomicity_and_collection_limit(self):
        root = self.dataset(6)
        mapping = {
            "state_columns": ["Text"],
            "id_column": "ID",
            "labels": {"team": "Team"},
            "origin": "real",
        }
        response = self.client.post(
            self.prefix + "/datasets",
            files={
                "file": (
                    "more.csv",
                    b"ID,Text,Team\nextra-1,invoice new,billing\nextra-2,crash new,engineering\n",
                )
            },
            data={
                "mapping": json.dumps(mapping),
                "dataset": root["id"],
                "expected_hash": root["payload"]["sha256"],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        current = response.json()
        self.assertEqual(len(self.rows(current)), 8)
        self.assertEqual(self.rows(current)[-1]["metadata"]["review_status"], "pending")
        self.assertEqual(
            current["payload"]["provenance"]["append"]["filename"], "more.csv"
        )
        duplicate = self.append(
            current, [self.example("will-rollback"), self.example("extra-1")]
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(
            self.app.state.store.dataset_head(root["id"], "A", self.project),
            current["id"],
        )
        self.assertEqual(len(self.rows(current)), 8)
        self.assertEqual(self.append(current, [self.example("x")] * 2).status_code, 400)
        self.assertEqual(
            self.append(current, [self.example(str(i)) for i in range(51)]).status_code,
            422,
        )
        missing = self.client.post(
            self.prefix + "/datasets",
            files={"file": ("more.csv", b"state\nnot added\n")},
            data={"dataset": current["id"]},
        )
        self.assertEqual(missing.status_code, 400)
        full = self.app.state.service.save_dataset(
            OWNER, self.project, fixture_rows(20000), {"fictional": True}
        )
        self.assertEqual(self.append(full, [self.example("overflow")]).status_code, 400)
        self.assertEqual(
            self.app.state.store.dataset_head(full["id"], "A", self.project), full["id"]
        )

    def test_pending_blocks_but_explicit_rejection_is_excluded_and_pinned(self):
        root = self.dataset(24)
        original_job = self.prepare_job(root, "original-snapshot")
        self.assertEqual(original_job["status"], "succeeded", original_job["error"])
        original_snapshot = self.app.state.store.get(
            original_job["result"]["snapshot"], "A", self.project, "snapshot"
        )
        draft = self.append(root, [self.example("unknown")]).json()
        failed = self.prepare_job(draft, "pending-blocks")
        self.assertEqual(failed["status"], "failed")
        rejected = self.review(draft, self.rows(draft)[-1], "rejected")
        quality = self.client.get(
            self.prefix + "/datasets/" + rejected["id"] + "/quality"
        ).json()["report"]
        self.assertEqual(
            (quality["rows"], quality["eligible_rows"], quality["rejected_rows"]),
            (25, 24, 1),
        )
        self.assertEqual(quality["rows_with_missing_labels"], 0)
        self.assertEqual(quality["preparation_blockers"], [])
        job = self.prepare_job(rejected, "explicit-exclusions")
        self.assertEqual(job["status"], "succeeded", job["error"])
        self.assertEqual(job["result"]["excluded_count"], 1)
        snapshot = self.app.state.store.get(
            job["result"]["snapshot"], "A", self.project, "snapshot"
        )["payload"]
        self.assertEqual(snapshot["splits"], original_snapshot["payload"]["splits"])
        self.assertEqual(
            snapshot["dataset_hashes"][rejected["id"]], rejected["payload"]["sha256"]
        )
        self.assertEqual(snapshot["excluded_records"][0]["record_id"], "unknown")
        self.assertEqual(snapshot["excluded_records"][0]["reviewer"], "owner")
        self.assertTrue(
            all(
                r["id"] != "unknown"
                for rows in snapshot["splits"].values()
                for r in rows
            )
        )
        self.assertEqual(
            self.app.state.store.get(
                original_snapshot["id"], "A", self.project, "snapshot"
            ),
            original_snapshot,
        )
        with self.assertRaises(ValueError):
            validate(self.rows(rejected)[-1], require_review=True)

    def test_all_rejected_report_reopen_and_imported_status_reset(self):
        response = self.client.post(
            self.prefix + "/examples",
            json={"examples": [self.example("rejected-only")]},
        )
        draft = response.json()
        rejected = self.review(draft, self.rows(draft)[0], "rejected")
        report = self.client.get(
            self.prefix + "/datasets/" + rejected["id"] + "/quality"
        ).json()["report"]
        self.assertEqual((report["eligible_rows"], report["rejected_rows"]), (0, 1))
        self.assertEqual(report["max_state_bytes"], 0)
        failed = self.prepare_job(rejected, "no-eligible")
        self.assertEqual(failed["status"], "failed")
        self.assertIn("No eligible", failed["error"])
        reopened = self.review(rejected, self.rows(rejected)[0], "pending")
        self.assertEqual(reopened["payload"]["rejected_count"], 0)
        self.assertEqual(
            self.rows(rejected)[0]["metadata"]["review_status"], "rejected"
        )
        malformed = copy.deepcopy(self.rows(rejected)[0])
        malformed["metadata"]["reviewed"] = True
        with self.assertRaisesRegex(ValueError, "gold|review_status"):
            validate(malformed)
        imported = self.client.post(
            self.prefix + "/datasets",
            files={"file": ("rejected.json", json.dumps(self.rows(rejected)).encode())},
        )
        self.assertEqual(imported.status_code, 201, imported.text)
        self.assertEqual(
            self.rows(imported.json())[0]["metadata"]["review_status"], "pending"
        )

    def test_actual_v2_upgrade_retains_root_and_review_hashes_before_append(self):
        root = self.dataset(6)
        reviewed = self.review(root, self.rows(root)[0], "approved")
        root_rows, reviewed_rows = self.rows(root), self.rows(reviewed)
        # Recreate the preceding version's physical schema, not just its version number.
        with self.app.state.store.transaction() as db:
            db.execute("DROP INDEX dataset_member_sequence")
            db.execute("ALTER TABLE dataset_members DROP COLUMN added_sequence")
            db.execute("DROP TABLE dataset_appends")
            db.execute("DROP TABLE incident_history")
            db.execute("DROP TABLE operational_incidents")
            db.execute("DROP TABLE worker_heartbeats")
            db.execute("DELETE FROM schema_version WHERE version>=3")
            db.execute("INSERT OR IGNORE INTO schema_version VALUES (2)")
        upgraded = Store(self.home)
        self.assertEqual(
            upgraded.verify_dataset(root["id"], "A", self.project), root_rows
        )
        self.assertEqual(
            upgraded.verify_dataset(reviewed["id"], "A", self.project), reviewed_rows
        )
        service = Service(upgraded)
        try:
            appended = service.save_examples(
                OWNER,
                self.project,
                [self.example("after-upgrade")],
                reviewed["id"],
                reviewed["payload"]["sha256"],
            )
        finally:
            service.runtime.close()
        self.assertEqual(
            upgraded.verify_dataset(root["id"], "A", self.project), root_rows
        )
        self.assertEqual(
            upgraded.verify_dataset(reviewed["id"], "A", self.project), reviewed_rows
        )
        self.assertEqual(
            len(upgraded.verify_dataset(appended["id"], "A", self.project)), 7
        )
        with upgraded.transaction() as db:
            self.assertEqual(
                db.execute("SELECT max(version) FROM schema_version").fetchone()[0], 7
            )

    def test_membership_manifest_and_sequence_tampering_are_refused(self):
        for mutation in ("ordinal", "sequence", "manifest", "unrecorded"):
            with self.subTest(mutation=mutation):
                root = self.app.state.service.save_dataset(
                    OWNER, self.project, fixture_rows(6), {"fictional": True}
                )
                appended = self.append(root, [self.example("new")]).json()
                with self.app.state.store.transaction() as db:
                    if mutation == "ordinal":
                        db.execute(
                            "UPDATE dataset_members SET ordinal=99 WHERE root=? AND record_id=?",
                            (root["id"], "new"),
                        )
                    elif mutation == "sequence":
                        db.execute(
                            "UPDATE dataset_members SET added_sequence=0 WHERE root=? AND record_id=?",
                            (root["id"], "new"),
                        )
                    elif mutation == "manifest":
                        db.execute(
                            "UPDATE dataset_appends SET manifest=? WHERE version=?",
                            ("[]", appended["id"]),
                        )
                    else:
                        checksum = db.execute(
                            "SELECT content_hash FROM dataset_members WHERE root=? AND ordinal=0",
                            (root["id"],),
                        ).fetchone()[0]
                        db.execute(
                            "INSERT INTO dataset_members VALUES (?,?,?,?,?)",
                            (root["id"], 7, "unrecorded", checksum, 1),
                        )
                with self.assertRaisesRegex(ValueError, "integrity"):
                    self.app.state.store.verify_dataset(
                        appended["id"], "A", self.project
                    )


if __name__ == "__main__":
    unittest.main()
