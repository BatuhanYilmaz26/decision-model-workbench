"""Scheduled captures, trusted receipts and retention over real fictional workspaces."""

import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from decision_workbench.platform import backup_schedule as schedule
from decision_workbench.platform import encrypted_recovery as encrypted
from decision_workbench.platform.store import encode
import test_encrypted_recovery as fixtures


class BackupScheduleTests(unittest.TestCase):
    setUp = fixtures.EncryptedRecoveryTests.setUp
    tearDown = fixtures.EncryptedRecoveryTests.tearDown

    def configure(self, **overrides):
        self.config = self.folder / "policy.dmtplan"
        self.vault = self.folder / "vault"
        self.catalog = self.folder / "catalog.sqlite3"
        return schedule.configure(
            self.home,
            self.vault,
            self.catalog,
            self.config,
            self.key,
            interval=10,
            retry=10,
            **overrides,
        )

    def force_due(self):
        state = schedule.status(self.config, self.key)
        latest = next((r for r in state["runs"] if "finished" in r), None)
        future = latest["finished"] + 11 if latest else time.time()
        with patch.object(schedule.time, "time", return_value=future):
            return schedule.run_due(self.config, self.key)

    def test_due_capture_receipts_and_restart_do_not_duplicate_before_interval(self):
        self.configure()
        first = schedule.run_due(self.config, self.key)
        self.assertTrue(first["captured"])
        self.assertFalse(schedule.run_due(self.config, self.key)["captured"])
        status = schedule.status(self.config, self.key)
        self.assertEqual(len(status["runs"]), 1)
        self.assertEqual(status["runs"][0]["status"], "succeeded")
        receipt = status["runs"][0]["receipt"]
        self.assertTrue(
            encrypted.verify_encrypted(
                first["destination"],
                self.key,
                receipt["encrypted_manifest_sha256"],
                self.folder,
            )["verified"]
        )
        second = self.force_due()
        self.assertNotEqual(first["run_id"], second["run_id"])

    def test_keep_last_prunes_owned_old_bundles_only_after_plan_confirmation(self):
        self.configure(keep_last=1)
        first = schedule.run_due(self.config, self.key)
        second = self.force_due()
        plan = schedule.status(self.config, self.key)["retention"]
        self.assertEqual(plan["keep"], [second["run_id"]])
        self.assertEqual([r["id"] for r in plan["remove"]], [first["run_id"]])
        with self.assertRaisesRegex(ValueError, "plan changed"):
            schedule.prune(self.config, self.key, "0" * 64)
        self.assertTrue(Path(first["destination"]).exists())
        result = schedule.prune(self.config, self.key, plan["plan_sha256"])
        self.assertEqual(result["pruned"], [first["run_id"]])
        self.assertFalse(Path(first["destination"]).exists())
        self.assertTrue(Path(second["destination"]).is_dir())
        self.assertEqual(
            schedule.status(self.config, self.key)["runs"][-1]["status"], "deleted"
        )

    def test_new_capture_invalidates_previous_retention_plan(self):
        self.configure(keep_last=1)
        first = schedule.run_due(self.config, self.key)
        self.force_due()
        plan = schedule.status(self.config, self.key)["retention"]
        self.force_due()
        with self.assertRaisesRegex(ValueError, "plan changed"):
            schedule.prune(self.config, self.key, plan["plan_sha256"])
        self.assertTrue(Path(first["destination"]).is_dir())

    def test_corrupted_keeper_blocks_every_deletion(self):
        self.configure(keep_last=1)
        first = schedule.run_due(self.config, self.key)
        last = self.force_due()
        payload = Path(last["destination"]) / encrypted.PAYLOAD
        with payload.open("r+b") as stream:
            stream.seek(100)
            value = stream.read(1)
            stream.seek(100)
            stream.write(bytes([value[0] ^ 1]))
        plan = schedule.status(self.config, self.key)["retention"]
        with self.assertRaises(ValueError):
            schedule.prune(self.config, self.key, plan["plan_sha256"])
        self.assertTrue(Path(first["destination"]).is_dir())

    def test_unmanaged_or_extra_files_are_preserved_and_block_pruning(self):
        self.configure(keep_last=1)
        first = schedule.run_due(self.config, self.key)
        self.force_due()
        unmanaged = self.vault / "user-files"
        unmanaged.mkdir()
        (unmanaged / "keep.txt").write_text("Unmanaged user file")
        plan = schedule.status(self.config, self.key)["retention"]
        self.assertEqual(plan["unmanaged_entries"], ["user-files"])
        with self.assertRaises(ValueError):
            schedule.prune(self.config, self.key, plan["plan_sha256"])
        self.assertEqual((unmanaged / "keep.txt").read_text(), "Unmanaged user file")
        (unmanaged / "keep.txt").unlink()
        unmanaged.rmdir()
        extra = Path(first["destination"]) / "keep.txt"
        extra.write_text("Unexpected content")
        plan = schedule.status(self.config, self.key)["retention"]
        with self.assertRaises(ValueError):
            schedule.prune(self.config, self.key, plan["plan_sha256"])
        self.assertEqual(extra.read_text(), "Unexpected content")

    def test_authenticated_policy_and_catalog_tampering_are_refused(self):
        self.configure()
        schedule.run_due(self.config, self.key)
        original = self.config.read_bytes()
        policy = json.loads(original)
        policy["keep_last"] = 100
        self.config.write_text(encode(policy))
        with self.assertRaisesRegex(ValueError, "authentication"):
            schedule.status(self.config, self.key)
        self.config.write_bytes(original)
        with closing(sqlite3.connect(self.catalog)) as db:
            raw = db.execute("SELECT body FROM events WHERE sequence=1").fetchone()[0]
            changed = json.loads(raw)
            changed["details"] = {"forged": True}
            body = encode(changed)
            db.execute(
                "UPDATE events SET body=?,sha256=? WHERE sequence=1",
                (body, hashlib.sha256(body.encode()).hexdigest()),
            )
            db.commit()
        with self.assertRaisesRegex(ValueError, "authentication"):
            schedule.status(self.config, self.key)

    def test_prepared_published_capture_is_recovered_using_catalog_receipt(self):
        self.configure()
        original = schedule._append

        def stopped(policy, key, state, operation, action, details=None):
            if action == "capture.succeeded":
                raise SystemExit("Fictional process exit after publication")
            return original(policy, key, state, operation, action, details)

        with patch.object(schedule, "_append", stopped):
            with self.assertRaises(SystemExit):
                schedule.run_due(self.config, self.key)
        pending = schedule.status(self.config, self.key)["runs"][0]
        self.assertEqual(pending["status"], "running")
        self.assertIsNotNone(pending["receipt"])
        recovered = schedule.run_due(self.config, self.key)
        self.assertFalse(recovered["captured"])
        result = schedule.status(self.config, self.key)["runs"]
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "succeeded")

    def test_unpublished_crash_is_interrupted_and_failure_retries_are_bounded(self):
        self.configure()
        with patch.object(
            schedule.encrypted,
            "backup_encrypted",
            side_effect=SystemExit("Stopped capture"),
        ):
            with self.assertRaises(SystemExit):
                schedule.run_due(self.config, self.key)
        self.assertFalse(schedule.run_due(self.config, self.key)["captured"])
        self.assertEqual(
            schedule.status(self.config, self.key)["runs"][0]["status"], "interrupted"
        )
        with patch.object(
            schedule.encrypted,
            "backup_encrypted",
            side_effect=OSError("Fictional disk failure"),
        ):
            with self.assertRaises(OSError):
                self.force_due()
        self.assertFalse(schedule.run_due(self.config, self.key)["captured"])
        self.assertEqual(
            schedule.status(self.config, self.key)["runs"][0]["status"], "failed"
        )

    def test_partially_deleted_bundle_requires_new_plan_and_resumes_known_files_only(
        self,
    ):
        self.configure(keep_last=1)
        first = schedule.run_due(self.config, self.key)
        self.force_due()
        plan = schedule.status(self.config, self.key)["retention"]
        original = Path.unlink
        target = Path(first["destination"]) / encrypted.MARKER

        def failed(path, *args, **kwargs):
            if path == target:
                raise OSError("Fictional interrupted marker unlink")
            return original(path, *args, **kwargs)

        with patch.object(Path, "unlink", failed):
            with self.assertRaises(OSError):
                schedule.prune(self.config, self.key, plan["plan_sha256"])
        current = schedule.status(self.config, self.key)
        self.assertEqual(current["runs"][-1]["status"], "delete_failed")
        self.assertNotEqual(current["retention"]["plan_sha256"], plan["plan_sha256"])
        schedule.prune(self.config, self.key, current["retention"]["plan_sha256"])
        self.assertFalse(Path(first["destination"]).exists())

    def test_auto_prune_requires_explicit_policy_and_keeps_latest(self):
        self.configure(keep_last=1, auto_prune=True)
        first = schedule.run_due(self.config, self.key)
        self.assertEqual(first["retention"]["pruned"], [])
        second = self.force_due()
        self.assertEqual(second["retention"]["pruned"], [first["run_id"]])
        self.assertTrue(Path(second["destination"]).is_dir())

    def test_invalid_controls_existing_paths_and_overlap_never_change_source(self):
        before = hashlib.sha256(self.store.database.read_bytes()).hexdigest()
        with self.assertRaises(ValueError):
            self.configure(keep_last=0)
        self.configure()
        with self.assertRaises(ValueError):
            self.configure()
        self.assertEqual(
            hashlib.sha256(self.store.database.read_bytes()).hexdigest(), before
        )

    def test_cli_configuration_once_status_and_confirmed_retention(self):
        prefix = [sys.executable, "-m", "decision_workbench.platform.cli"]
        self.config, self.vault, self.catalog = (
            self.folder / "policy.dmtplan",
            self.folder / "vault",
            self.folder / "catalog.sqlite3",
        )
        configured = subprocess.run(
            prefix
            + [
                "configure-backups",
                "--home",
                str(self.home),
                "--vault",
                str(self.vault),
                "--catalog",
                str(self.catalog),
                "--config",
                str(self.config),
                "--key-file",
                str(self.key),
                "--interval-seconds",
                "10",
                "--retry-seconds",
                "10",
                "--keep-last",
                "1",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        common = ["--config", str(self.config), "--key-file", str(self.key)]
        captured = subprocess.run(
            prefix + ["backup-run", *common], capture_output=True, text=True, timeout=30
        )
        self.assertEqual(captured.returncode, 0, captured.stderr)
        first = json.loads(captured.stdout)
        self.assertIn("encrypted_manifest_sha256", first["receipt"])
        self.force_due()
        status = subprocess.run(
            prefix + ["backups-status", *common],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(status.returncode, 0, status.stderr)
        plan = json.loads(status.stdout)["retention"]["plan_sha256"]
        pruned = subprocess.run(
            prefix + ["prune-backups", *common, "--expected-plan-sha256", plan],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(pruned.returncode, 0, pruned.stderr)
        self.assertFalse(Path(first["destination"]).exists())

    def spawn(self, mode):
        ready = self.folder / (mode + "-ready.txt")
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).with_name("backup_process_fixture.py")),
                mode,
                str(self.config),
                str(self.key),
                str(ready),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            ),
        )
        deadline = time.monotonic() + 30
        try:
            while not ready.exists():
                if process.poll() is not None:
                    raise AssertionError(process.communicate()[1].decode())
                if time.monotonic() > deadline:
                    raise AssertionError(
                        "Backup fixture did not reach its process barrier"
                    )
                time.sleep(0.05)
        except BaseException:
            self.stop_process(process)
            raise
        return process

    def stop_process(self, process):
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)

    def test_independent_operation_lock_refuses_duplicate_capture_and_releases_on_death(
        self,
    ):
        self.configure()
        process = self.spawn("hold")
        try:
            with self.assertRaisesRegex(ValueError, "owns this local lock"):
                schedule.run_due(self.config, self.key)
            self.assertEqual(schedule.status(self.config, self.key)["runs"], [])
        finally:
            self.stop_process(process)
        self.assertTrue(schedule.run_due(self.config, self.key)["captured"])

    def test_actual_process_death_after_publication_recovers_prepared_receipt(self):
        self.configure()
        process = self.spawn("published")
        try:
            state = schedule.status(self.config, self.key)
            self.assertEqual(len(state["runs"]), 1)
            run = state["runs"][0]
            self.assertEqual(run["status"], "running")
            self.assertIsNotNone(run["receipt"])
            self.assertTrue(
                (self.vault / ("run-" + run["id"]) / encrypted.MARKER).is_file()
            )
        finally:
            self.stop_process(process)
        self.assertFalse(schedule.run_due(self.config, self.key)["captured"])
        self.assertEqual(
            schedule.status(self.config, self.key)["runs"][0]["status"], "succeeded"
        )

    def test_actual_process_death_before_preparation_marks_interrupted_without_replay(
        self,
    ):
        self.configure()
        process = self.spawn("unprepared")
        self.stop_process(process)
        self.assertFalse(schedule.run_due(self.config, self.key)["captured"])
        runs = schedule.status(self.config, self.key)["runs"]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "interrupted")

    def test_actual_service_timing_restart_and_single_workspace_ownership(self):
        self.configure(keep_last=1, auto_prune=True)
        args = [
            sys.executable,
            "-m",
            "decision_workbench.platform.cli",
            "backup-service",
            "--config",
            str(self.config),
            "--key-file",
            str(self.key),
        ]
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            ),
        )
        try:
            deadline = time.monotonic() + 40
            while True:
                if process.poll() is not None:
                    raise AssertionError(process.communicate()[1].decode())
                runs = schedule.status(self.config, self.key)["runs"]
                if (
                    len(runs) >= 2
                    and runs[0]["status"] == "succeeded"
                    and runs[1]["status"] == "deleted"
                ):
                    break
                if time.monotonic() > deadline:
                    raise AssertionError(
                        "Timed backup service did not complete two scheduled captures"
                    )
                time.sleep(0.1)
            duplicate = subprocess.run(args, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(duplicate.returncode, 0)
            self.assertIn("owns this local lock", duplicate.stderr)
            self.assertTrue((self.vault / ("run-" + runs[0]["id"])).is_dir())
        finally:
            self.stop_process(process)
        self.assertFalse(schedule.run_due(self.config, self.key)["captured"])

    def test_event_capacity_refuses_capture_and_deletion_before_filesystem_work(self):
        self.configure(keep_last=1)
        with patch.object(schedule, "MAX_EVENTS", 2):
            with self.assertRaisesRegex(ValueError, "complete capture"):
                schedule.run_due(self.config, self.key)
        self.assertEqual(schedule.status(self.config, self.key)["runs"], [])
        first = schedule.run_due(self.config, self.key)
        self.force_due()
        plan = schedule.status(self.config, self.key)["retention"]
        with patch.object(schedule, "MAX_EVENTS", 6):
            with self.assertRaisesRegex(ValueError, "record retention"):
                schedule.prune(self.config, self.key, plan["plan_sha256"])
        self.assertTrue(Path(first["destination"]).is_dir())

    def test_invalid_prepared_bundle_is_preserved_failed_and_never_adopted(self):
        self.configure()
        original = schedule._append

        def stopped(policy, key, state, operation, action, details=None):
            if action == "capture.succeeded":
                raise SystemExit("Fictional stopped completion")
            return original(policy, key, state, operation, action, details)

        with patch.object(schedule, "_append", stopped):
            with self.assertRaises(SystemExit):
                schedule.run_due(self.config, self.key)
        run = schedule.status(self.config, self.key)["runs"][0]
        folder = self.vault / ("run-" + run["id"])
        payload = folder / encrypted.PAYLOAD
        with payload.open("r+b") as stream:
            stream.seek(100)
            value = stream.read(1)
            stream.seek(100)
            stream.write(bytes([value[0] ^ 1]))
        self.assertFalse(schedule.run_due(self.config, self.key)["captured"])
        result = schedule.status(self.config, self.key)
        self.assertEqual(result["runs"][0]["status"], "failed")
        self.assertIn(folder.name, result["retention"]["unmanaged_entries"])
        self.assertTrue(payload.exists())
