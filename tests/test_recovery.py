"""Actual workspace recovery, learned inference, fencing and corrupted-bundle refusal."""

import hashlib
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import uvicorn
import test_platform as fixtures
from decision_workbench.platform.api import create_app
from decision_workbench.platform.recovery import (
    backup_workspace,
    verify_backup,
    restore_workspace,
)
from decision_workbench.platform.service import Service
from decision_workbench.platform.store import Store, Conflict
from decision_workbench.platform.worker import Worker


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        # Hosted Windows temporary roots can be aliases; fixtures use the real path.
        self.folder = Path(self.temporary.name).resolve()
        self.home = self.folder / "source"
        self.store = Store(self.home)
        self.service = Service(self.store, runtime=object())
        self.owner = {"tenant": "A", "actor": "owner", "role": "admin"}
        self.project = self.service.create_project(
            self.owner, "recovery", "Fictional routing policy only", fixtures.QUESTIONS
        )["id"]
        self.root = self.service.save_dataset(
            self.owner, self.project, fixtures.fixture_rows(), {"fixture": True}
        )

    def tearDown(self):
        self.temporary.cleanup()

    def train(self):
        prepare = self.service.submit(
            self.owner,
            self.project,
            "prepare",
            {"datasets": [self.root["id"]]},
            "recovery-prepare",
        )
        Worker(self.store).once()
        snapshot = self.store.job(prepare["id"], "A", self.project)["result"][
            "snapshot"
        ]
        self.service.configure_release_policy(
            self.owner, self.project, 0, 0, 1, 10, None
        )
        trained = self.service.submit(
            self.owner,
            self.project,
            "train",
            {"snapshot": snapshot, "backend": "cpu-baseline"},
            "recovery-train",
        )
        Worker(self.store).once()
        job = self.store.job(trained["id"], "A", self.project)
        self.assertEqual(job["status"], "succeeded", job["error"])
        model = job["result"]["model"]
        self.service.activate(
            self.owner, self.project, model, 0, "Fictional recovery acceptance"
        )
        return model

    def backup(self, name="backup"):
        result = backup_workspace(self.home, self.folder / name)
        self.assertTrue(verify_backup(result["destination"], result["manifest_sha256"]))
        return result

    def test_learned_backup_validation_never_creates_cache_controls_in_snapshot(self):
        self.train()
        captured = backup_workspace(self.home, self.folder / "readonly-backup")
        folder = Path(captured["destination"])
        expected = json.loads((folder / "backup-manifest.json").read_text())["files"]
        before = {
            p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.rglob("*")
            if p.is_file()
        }
        self.assertEqual(set(before), set(expected) | {"backup-manifest.json"})
        for _ in range(2):
            verify_backup(folder, captured["manifest_sha256"])
        after = {
            p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.rglob("*")
            if p.is_file()
        }
        self.assertEqual(before, after)
        self.assertFalse(list(folder.glob(".dmt-cache*")))
        restore_workspace(
            folder, self.folder / "readonly-restored", captured["manifest_sha256"]
        )

    def test_restore_live_http_predictions_history_scope_and_job_fencing(self):
        model = self.train()
        appended = self.service.save_examples(
            self.owner,
            self.project,
            [
                {
                    "id": "unknown-after-training",
                    "state": "unsupported fictional evidence",
                }
            ],
            self.root["id"],
            self.root["payload"]["sha256"],
        )
        rejected = self.service.review(
            self.owner,
            self.project,
            appended["id"],
            "unknown-after-training",
            {},
            False,
            appended["payload"]["sha256"],
            "Unsupported fixture",
            "rejected",
        )
        evidence = "invoice payment routine refund requested"
        expected = self.service.predict(self.owner, self.project, evidence, 0.8)
        # Two claims plus a queued job; none may silently execute after recovery.
        first = self.service.submit(
            self.owner,
            self.project,
            "prepare",
            {"datasets": [self.root["id"]]},
            "copied-running",
        )
        old_owner = self.store.claim()
        self.assertEqual(old_owner["id"], first["id"])
        second = self.service.submit(
            self.owner,
            self.project,
            "prepare",
            {"datasets": [self.root["id"]]},
            "copied-cancelling",
        )
        self.store.claim()
        self.store.cancel(second["id"], "A", self.project, "owner")
        queued = self.service.submit(
            self.owner,
            self.project,
            "prepare",
            {"datasets": [self.root["id"]]},
            "copied-queued",
        )
        (self.home / "operator-keys.json").write_text(
            "fictional-secret-excluded", encoding="utf-8"
        )
        (self.home / "jobs" / "unfinished").mkdir(parents=True)
        (self.home / "jobs" / "unfinished" / "checkpoint.bin").write_bytes(b"partial")
        orphan = self.home / "artifacts" / ("a" * 32)
        orphan.mkdir()
        (orphan / "unfinished.bin").write_bytes(b"not registered")
        with self.store.transaction() as db:
            original_entities = [
                tuple(row) for row in db.execute("SELECT * FROM entities ORDER BY id")
            ]
        backed = self.backup()
        self.assertFalse((Path(backed["destination"]) / "operator-keys.json").exists())
        self.assertFalse((Path(backed["destination"]) / "jobs").exists())
        self.assertFalse(
            (Path(backed["destination"]) / "artifacts" / ("a" * 32)).exists()
        )
        self.store.heartbeat(old_owner)  # Backup did not fence the live source.
        restored = restore_workspace(
            backed["destination"], self.folder / "restored", backed["manifest_sha256"]
        )
        recovered = Store(restored["destination"])
        self.assertEqual(
            recovered.active("A", self.project), self.store.active("A", self.project)
        )
        self.assertEqual(recovered.active("A", self.project)["model_id"], model)
        self.assertEqual(
            len(recovered.verify_dataset(self.root["id"], "A", self.project)), 180
        )
        head = recovered.dataset_head(self.root["id"], "A", self.project)
        self.assertEqual(head, rejected["id"])
        rows = recovered.verify_dataset(head, "A", self.project)
        self.assertEqual(rows[-1]["metadata"]["review_status"], "rejected")
        self.assertEqual(rows[-1]["gold"], {})
        with recovered.transaction() as db:
            self.assertEqual(
                [
                    tuple(row)
                    for row in db.execute("SELECT * FROM entities ORDER BY id")
                ],
                original_entities,
            )
        self.assertEqual(
            recovered.job(first["id"], "A", self.project)["status"], "interrupted"
        )
        self.assertEqual(
            recovered.job(queued["id"], "A", self.project)["status"], "interrupted"
        )
        self.assertEqual(
            recovered.job(second["id"], "A", self.project)["status"], "cancelled"
        )
        self.assertIsNone(recovered.claim())
        with self.assertRaises(Conflict):
            recovered.heartbeat(old_owner)
        self.assertEqual(len(restored["interrupted_jobs"]), 3)
        self.assertTrue(
            any(
                event["action"] == "restore.workspace"
                for event in recovered.events("A", self.project)
            )
        )

        # Serve the recovered location over actual localhost HTTP, rather than an in-memory client.
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(restored["destination"], fixtures.keys()),
                log_level="error",
                access_log=False,
            )
        )
        thread = threading.Thread(
            target=lambda: server.run(sockets=[listener]), daemon=True
        )
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if time.monotonic() > deadline:
                    self.fail("Recovered HTTP service failed to start")
                time.sleep(0.02)
            with httpx.Client(
                base_url=f"http://127.0.0.1:{listener.getsockname()[1]}",
                headers={"Authorization": "Bearer a-secret"},
                timeout=10,
            ) as client:
                self.assertEqual(client.get("/ready").status_code, 200)
                response = client.post(
                    f"/v1/projects/{self.project}/decisions",
                    json={"state": evidence, "threshold": 0.8},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), expected)
                self.assertEqual(
                    client.get(
                        f"/v1/projects/{self.project}",
                        headers={"Authorization": "Bearer b-secret"},
                    ).status_code,
                    404,
                )
                self.assertEqual(
                    client.get(
                        f"/v1/projects/{self.project}/datasets/{head}/rows",
                        headers={"Authorization": "Bearer infer-secret"},
                    ).status_code,
                    403,
                )
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            listener.close()
            self.assertFalse(thread.is_alive())

    def test_snapshot_is_stable_when_source_advances_during_artifact_copy(self):
        self.train()
        original_copy = shutil.copyfile
        advanced = []

        def copy_after_source_write(source, destination, *args, **kwargs):
            if not advanced:
                advanced.append(
                    self.service.save_examples(
                        self.owner,
                        self.project,
                        [{"id": "after-snapshot", "state": "later fixture"}],
                        self.root["id"],
                        self.root["payload"]["sha256"],
                    )
                )
            return original_copy(source, destination, *args, **kwargs)

        with patch(
            "decision_workbench.platform.recovery.shutil.copyfile",
            side_effect=copy_after_source_write,
        ):
            result = self.backup()
        restored = restore_workspace(
            result["destination"], self.folder / "recovered", result["manifest_sha256"]
        )
        recovered = Store(restored["destination"])
        self.assertEqual(
            recovered.dataset_head(self.root["id"], "A", self.project), self.root["id"]
        )
        self.assertEqual(
            len(recovered.verify_dataset(self.root["id"], "A", self.project)), 180
        )
        self.assertEqual(
            len(self.store.verify_dataset(advanced[0]["id"], "A", self.project)), 181
        )

    def test_corruption_missing_extra_files_and_wrong_manifest_are_refused(self):
        result = self.backup()
        folder = Path(result["destination"])
        checksum = result["manifest_sha256"]
        with self.assertRaisesRegex(ValueError, "checksum"):
            restore_workspace(folder, self.folder / "wrong", "0" * 64)
        self.assertFalse((self.folder / "wrong").exists())
        database = folder / "workspace.sqlite3"
        original = database.read_bytes()
        database.write_bytes(original + b"tamper")
        with self.assertRaisesRegex(ValueError, "checksum/size"):
            restore_workspace(folder, self.folder / "tampered", checksum)
        self.assertFalse((self.folder / "tampered").exists())
        database.write_bytes(original)
        extra = folder / "operator-keys.json"
        extra.write_text("unexpected", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "inventory"):
            verify_backup(folder, checksum)
        extra.unlink()
        database.unlink()
        with self.assertRaisesRegex(ValueError, "inventory"):
            verify_backup(folder, checksum)

    def test_targets_incomplete_bundles_unsafe_names_and_deadline_are_refused(self):
        from decision_workbench.platform.recovery import _safe_name

        for name in [
            "../outside",
            "/absolute",
            "artifacts/" + "a" * 32 + "/../outside",
            "artifacts/" + "a" * 32 + "/a:b",
            "artifacts/" + "a" * 32 + "/CON.json",
            "artifacts/" + "a" * 32 + "/linked\\escape",
        ]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                _safe_name(name)
        existing = self.folder / "existing"
        existing.mkdir()
        marker = existing / "keep.txt"
        marker.write_text("preserve", encoding="utf-8")
        with self.assertRaises(ValueError):
            backup_workspace(self.home, existing)
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
        with self.assertRaises(ValueError):
            backup_workspace(self.home, self.home / "backup")
        with self.assertRaises(ValueError):
            backup_workspace(self.home, self.folder / "expired", timeout=1e-9)
        self.assertFalse((self.folder / "expired").exists())
        self.assertEqual(list(self.folder.glob(".dmt-backup-*")), [])
        incomplete = self.folder / "incomplete"
        incomplete.mkdir()
        with self.assertRaises(ValueError):
            restore_workspace(incomplete, self.folder / "restore-incomplete", "0" * 64)
        self.assertFalse((self.folder / "restore-incomplete").exists())

    def test_registered_artifact_and_historical_content_tampering_blocks_backup(self):
        model = self.train()
        value = self.store.get(model, "A", self.project, "model")["payload"]
        file = self.home / "artifacts" / value["artifact_id"] / "model.json"
        original = file.read_bytes()
        file.write_bytes(original + b"tamper")
        with self.assertRaisesRegex(ValueError, "Model integrity"):
            backup_workspace(self.home, self.folder / "broken-model")
        self.assertFalse((self.folder / "broken-model").exists())
        file.write_bytes(original)
        malformed = {**value, "job_id": "0" * 32}
        with self.store.transaction() as db:
            db.execute(
                "UPDATE entities SET payload=? WHERE id=?",
                (json.dumps(malformed), model),
            )
        with self.assertRaisesRegex(ValueError, "malformed"):
            backup_workspace(self.home, self.folder / "missing-job")
        self.assertFalse((self.folder / "missing-job").exists())
        with self.store.transaction() as db:
            db.execute(
                "UPDATE entities SET payload=? WHERE id=?", (json.dumps(value), model)
            )
            db.execute(
                "UPDATE record_contents SET payload=? WHERE hash=(SELECT hash FROM record_contents LIMIT 1)",
                ('{"state":"tampered"}',),
            )
        with self.assertRaisesRegex(ValueError, "Historical record"):
            backup_workspace(self.home, self.folder / "broken-data")
        self.assertFalse((self.folder / "broken-data").exists())

    def test_actual_operator_cli_and_existing_restore_target(self):
        destination = self.folder / "cli-backup"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "decision_workbench.platform.cli",
                "backup",
                "--home",
                str(self.home),
                "--output",
                str(destination),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        checksum = json.loads(result.stdout)["manifest_sha256"]
        verified = subprocess.run(
            [
                sys.executable,
                "-m",
                "decision_workbench.platform.cli",
                "verify-backup",
                "--backup",
                str(destination),
                "--manifest-sha256",
                checksum,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        recovered = self.folder / "cli-restore"
        argv = [
            sys.executable,
            "-m",
            "decision_workbench.platform.cli",
            "restore",
            "--backup",
            str(destination),
            "--home",
            str(recovered),
            "--manifest-sha256",
            checksum,
        ]
        restored = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        self.assertEqual(restored.returncode, 0, restored.stderr)
        preserved = (recovered / "workspace.sqlite3").read_bytes()
        again = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("must not already exist", again.stderr)
        self.assertEqual((recovered / "workspace.sqlite3").read_bytes(), preserved)


if __name__ == "__main__":
    unittest.main()
