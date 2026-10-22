"""Cold learned-model recovery through actual PostgreSQL and private object copies."""

import hashlib
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
import sys

from fastapi.testclient import TestClient
from decision_workbench.platform.api import create_app
from decision_workbench.platform.artifact_storage import (
    ArtifactRepository,
    DirectoryObjects,
)
from decision_workbench.platform import shared_recovery as recovery
from decision_workbench.platform.store import Store
from decision_workbench.platform.worker import Worker
import test_platform as fixtures
import test_postgres as pg


@unittest.skipUnless(pg.URL, "Set DMT_TEST_POSTGRES_URL for coordinated recovery")
class SharedRecoveryTests(pg.DatabaseFixture, unittest.TestCase):
    dataset = fixtures.PlatformTests.dataset
    job = fixtures.PlatformTests.job

    def setUp(self):
        self.database_setup()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "live"
        self.repo = ArtifactRepository(DirectoryObjects(self.root / "objects"))
        self.vault = ArtifactRepository(DirectoryObjects(self.root / "vault"))
        self.target = ArtifactRepository(DirectoryObjects(self.root / "target-objects"))
        self.app = create_app(
            self.home,
            fixtures.keys(),
            database_url=pg.URL,
            database_schema=self.schema,
            artifact_store=self.repo,
        )
        self.addCleanup(self.app.state.service.runtime.close)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.client.headers.update({"Authorization": "Bearer a-secret"})
        self.store = self.app.state.store
        response = self.client.post(
            "/v1/projects",
            json={
                "name": "Fictional recovery",
                "description": "Fictional decision policy",
                "questions": fixtures.QUESTIONS,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.project = response.json()["id"]
        self.prefix = "/v1/projects/" + self.project

    def fresh_schema(self):
        import psycopg
        from psycopg import sql

        schema = "dmt_test_" + uuid.uuid4().hex
        with psycopg.connect(pg.URL, autocommit=True) as db:
            db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

        def cleanup():
            assert schema.startswith("dmt_test_") and len(schema) == 41
            with psycopg.connect(pg.URL, autocommit=True) as db:
                db.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
                )

        self.addCleanup(cleanup)
        return schema

    def backup(self):
        result = recovery.backup_shared(self.store, self.root / "captured", self.vault)
        return self.root / "captured", result["manifest_sha256"]

    def test_cold_model_prediction_history_and_pending_jobs_survive_source_loss(self):
        dataset = self.dataset()
        prepared = self.job(
            "prepare", {"datasets": [dataset["id"]]}, "prepare-recovery"
        )
        worker = Worker(self.store)
        self.addCleanup(worker.service.runtime.close)
        self.assertTrue(worker.once())
        prepared = self.store.job(prepared["id"], "A", self.project)
        self.assertEqual(prepared["status"], "succeeded", prepared["error"])
        job = self.job(
            "train",
            {"snapshot": prepared["result"]["snapshot"], "backend": "cpu-baseline"},
            "train-recovery",
        )
        self.assertTrue(worker.once())
        result = self.store.job(job["id"], "A", self.project)
        self.assertEqual(result["status"], "succeeded", result["error"])
        released = self.client.post(
            self.prefix + "/releases",
            json={
                "model_id": result["result"]["model"],
                "expected_revision": 0,
                "note": "Fictional recovery check",
            },
        )
        self.assertEqual(released.status_code, 200, released.text)
        request = {"state": "invoice payment refund requested urgent", "threshold": 0.5}
        original = self.client.post(self.prefix + "/decisions", json=request)
        self.assertEqual(original.status_code, 200, original.text)
        pending = self.job("prepare", {"datasets": [dataset["id"]]}, "pending-recovery")
        captured, sha = self.backup()
        manifest = recovery.verify_shared(captured, sha, self.vault)
        self.assertGreater(len(manifest["objects"]), 1)
        # Simulate loss of only this test's live objects; the independent vault remains.
        for path in self.repo.objects.root.rglob("*"):
            if path.is_file():
                path.unlink()
        schema = self.fresh_schema()
        receipt = recovery.restore_shared(
            captured,
            self.root / "recovered",
            sha,
            self.vault,
            self.target,
            database_url=pg.URL,
            database_schema=schema,
        )
        self.assertEqual(receipt["workspace"], self.store.workspace_id)
        self.assertEqual(
            receipt["interrupted_jobs"],
            [
                {
                    "id": pending["id"],
                    "previous_status": "queued",
                    "status": "interrupted",
                }
            ],
        )
        repository = ArtifactRepository(
            DirectoryObjects(receipt["artifact_storage"]["root"])
        )
        with TestClient(
            create_app(
                self.root / "recovered",
                fixtures.keys(),
                database_url=pg.URL,
                database_schema=schema,
                artifact_store=repository,
            )
        ) as restored:
            self.addCleanup(restored.app.state.service.runtime.close)
            current = restored.post(
                self.prefix + "/decisions",
                json=request,
                headers={"Authorization": "Bearer a-secret"},
            )
            self.assertEqual(current.status_code, 200, current.text)
            self.assertEqual(current.json()["answers"], original.json()["answers"])
            self.assertEqual(
                restored.app.state.store.dataset_rows(dataset["id"], "A", self.project),
                self.store.dataset_rows(dataset["id"], "A", self.project),
            )
            self.assertEqual(
                restored.get(
                    self.prefix, headers={"Authorization": "Bearer b-secret"}
                ).status_code,
                404,
            )
            with restored.app.state.store.transaction() as db:
                self.assertEqual(
                    db.execute("SELECT count(*) FROM worker_heartbeats").fetchone()[0],
                    0,
                )
                actions = [
                    r[0]
                    for r in db.execute(
                        "SELECT action FROM events ORDER BY id"
                    ).fetchall()
                ]
                self.assertIn("restore.shared-workspace", actions)
        # Fresh process/cache binding can reopen after the restored service stops.
        reopened = Store(
            self.root / "reopened",
            database_url=pg.URL,
            database_schema=schema,
            artifact_store=repository,
        )
        self.assertEqual(reopened.workspace_id, self.store.workspace_id)

    def test_corrupt_archive_import_rolls_back_pristine_target(self):
        captured, sha = self.backup()
        manifest = recovery.verify_shared(captured, sha, self.vault)
        target = Store(
            self.root / "import-target",
            database_url=pg.URL,
            database_schema=self.fresh_schema(),
            artifact_store=self.target,
        )
        identity = target.workspace_id
        with self.assertRaises(ValueError):
            recovery.import_metadata(
                target,
                captured / "shared-metadata.jsonl",
                {**manifest["metadata"], "sha256": "0" * 64},
                sha,
                lambda: None,
            )
        with target.transaction() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM entities").fetchone()[0], 0
            )
            self.assertEqual(
                db.execute("SELECT id FROM workspace_metadata_identity").fetchone()[0],
                identity,
            )

    def test_nonempty_target_and_overlapping_vault_are_refused(self):
        with self.assertRaises(ValueError):
            recovery.backup_shared(self.store, self.root / "refused", self.repo)
        self.assertFalse((self.root / "refused").exists())
        captured, sha = self.backup()
        with self.assertRaises(ValueError):
            recovery.restore_shared(
                captured,
                self.root / "refused",
                sha,
                self.vault,
                self.repo,
                database_url=pg.URL,
                database_schema=self.schema,
            )
        self.assertFalse((self.root / "refused").exists())
        self.assertEqual(
            self.store.get(self.project, "A", self.project)["id"], self.project
        )

    def test_failed_copy_never_publishes_completion_manifest(self):
        # One deliberately tiny registered artifact exercises the failure boundary.
        identifier = uuid.uuid4().hex
        folder = self.home / "artifacts" / identifier
        folder.mkdir(parents=True)
        body = b"fictional model bytes"
        (folder / "model.json").write_bytes(body)
        model = {
            "artifact_id": identifier,
            "backend": "cpu-baseline",
            "model_sha256": hashlib.sha256(body).hexdigest(),
            "schema_sha256": "b" * 64,
        }
        self.store.add("model", "A", self.project, "owner", model)
        with patch.object(
            recovery.RecoveryObjects,
            "copy_to",
            side_effect=OSError("fictional interrupted copy"),
        ):
            with self.assertRaises(OSError):
                recovery.backup_shared(self.store, self.root / "captured", self.vault)
        self.assertFalse((self.root / "captured").exists())

    def test_wrong_checksum_extra_files_and_wrong_vault_are_refused(self):
        captured, sha = self.backup()
        with self.assertRaises(ValueError):
            recovery.verify_shared(captured, "0" * 64, self.vault)
        (captured / "unexpected").write_text("keep")
        with self.assertRaises(ValueError):
            recovery.verify_shared(captured, sha, self.vault)
        (captured / "unexpected").unlink()
        with self.assertRaises(ValueError):
            recovery.verify_shared(captured, sha, self.target)

    def test_terminal_incoming_artifacts_remain_in_the_recovery_inventory(self):
        folder = self.root / "incoming-fixture"
        folder.mkdir()
        body = b"fictional incoming artifact"
        (folder / "model.json").write_bytes(body)
        model = {
            "artifact_id": uuid.uuid4().hex,
            "backend": "cpu-baseline",
            "model_sha256": hashlib.sha256(body).hexdigest(),
            "schema_sha256": "b" * 64,
        }
        ref = self.repo.publish(self.store.workspace_id, "a" * 64, folder, model)
        job = self.store.submit(
            "A",
            self.project,
            "owner",
            "remote-import",
            {"incoming_reference": ref},
            "retained-incoming",
        )
        with self.store.transaction() as db:
            db.execute("UPDATE jobs SET status='failed' WHERE id=?", (job["id"],))
        captured, sha = self.backup()
        manifest = recovery.verify_shared(captured, sha, self.vault)
        self.assertEqual(len(manifest["metadata"]["references"]), 1)
        self.assertEqual(len(manifest["objects"]), 2)
        # Duplicate entries cannot hide behind dictionary normalization.
        manifest["objects"].append(manifest["objects"][0])
        data = json.dumps(manifest).encode()
        (captured / recovery.MANIFEST).write_bytes(data)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            recovery.verify_shared(
                captured, hashlib.sha256(data).hexdigest(), self.vault
            )

    def test_operator_commands_capture_verify_and_restore_without_exposing_dsn(self):
        from decision_workbench.platform.cli import main

        def repository(prefix="DMT_ARTIFACT_S3_"):
            return self.vault if prefix == "DMT_BACKUP_S3_" else self.repo

        def command(*args):
            output = io.StringIO()
            with patch.object(sys, "argv", ["domain-platform", *args]), patch(
                "decision_workbench.platform.artifact_s3.repository_from_environment",
                side_effect=repository,
            ), patch.dict(os.environ, {"DMT_DATABASE_URL": pg.URL}), redirect_stdout(
                output
            ):
                main()
            self.assertNotIn(pg.URL, output.getvalue())
            return json.loads(output.getvalue())

        captured = self.root / "cli-backup"
        result = command(
            "backup-shared",
            "--home",
            str(self.home),
            "--database-schema",
            self.schema,
            "--output",
            str(captured),
        )
        verified = command(
            "verify-shared-backup",
            "--backup",
            str(captured),
            "--manifest-sha256",
            result["manifest_sha256"],
        )
        self.assertTrue(verified["verified"])
        # Restore uses an independent configured target base.
        self.repo = self.target
        restored = command(
            "restore-shared",
            "--backup",
            str(captured),
            "--manifest-sha256",
            result["manifest_sha256"],
            "--home",
            str(self.root / "cli-restored"),
            "--database-schema",
            self.fresh_schema(),
        )
        self.assertEqual(restored["workspace"], self.store.workspace_id)


if __name__ == "__main__":
    unittest.main()
