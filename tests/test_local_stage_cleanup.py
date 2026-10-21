"""Real local stage leases, process death and bounded reviewed cleanup."""

import io
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from decision_workbench.platform.owned_staging import OwnedStage, write_json
from decision_workbench.platform import local_stage_cleanup as cleanup
from decision_workbench.platform.store import Store, Conflict

ROOT = Path(__file__).resolve().parents[1]


class LocalStageCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "workspace"
        Store(self.home)

    def stage(self, kind="download"):
        stage = OwnedStage(
            self.home / ("artifacts" if kind == "download" else "incoming"), kind
        )
        (stage.path / "partial.bin").write_bytes(b"fictional transfer bytes")
        return stage

    def test_active_owner_is_preserved_and_abandoned_stage_is_removed(self):
        stage = self.stage()
        self.addCleanup(stage.close)
        active = cleanup.preview(self.home, 0)
        self.assertEqual(active["stages"], 0)
        self.assertEqual(active["busy_stages"], 1)
        self.assertEqual(active["unmanaged_entries"], 0)
        stage.lease.release()
        plan = cleanup.preview(self.home, 0)
        result = cleanup.execute(self.home, plan["plan_sha256"], 0)
        self.assertEqual(result["completed_stages"], 1)
        self.assertFalse(stage.path.exists())
        self.assertFalse(stage.record_path.exists())
        self.assertFalse(stage.lock_path.exists())
        receipt = json.loads(
            (
                self.home / ".dmt-stage-cleanups" / (result["operation_id"] + ".json")
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(receipt["state"], "completed")

    def test_successful_rename_clears_only_ownership_records(self):
        stage = self.stage("intake")
        target = stage.parent / uuid.uuid4().hex
        self.assertEqual(stage.path.resolve().parent, target.resolve().parent)
        stage.path.rename(target)
        stage.close()
        self.assertTrue((target / "partial.bin").is_file())
        self.assertFalse(stage.record_path.exists())

    def test_stale_plan_unmarked_entries_and_registered_cache_paths_are_preserved(self):
        stage = self.stage()
        stage.lease.release()
        self.addCleanup(stage.close)
        parent = stage.parent
        legacy = parent / ".dmt-download-legacy"
        legacy.mkdir()
        (legacy / "keep.bin").write_bytes(b"unknown")
        registered = parent / uuid.uuid4().hex
        registered.mkdir()
        (registered / "model.json").write_text("{}")
        plan = cleanup.preview(self.home, 0)
        self.assertEqual(plan["unmanaged_entries"], 1)
        (stage.path / "partial.bin").write_bytes(b"changed since review")
        with self.assertRaises(Conflict):
            cleanup.execute(self.home, plan["plan_sha256"], 0)
        self.assertTrue((legacy / "keep.bin").is_file())
        self.assertTrue((registered / "model.json").is_file())
        self.assertTrue(stage.path.exists())

    def test_forged_ownership_cannot_select_a_registered_model_directory(self):
        stage = self.stage()
        stage.lease.release()
        target = stage.parent / uuid.uuid4().hex
        target.mkdir()
        (target / "keep.bin").write_bytes(b"registered")
        write_json(stage.record_path, {**stage.record, "directory": target.name})
        with self.assertRaises(ValueError):
            cleanup.preview(self.home, 0)
        self.assertTrue((target / "keep.bin").is_file())
        write_json(stage.record_path, stage.record)
        stage.close()

    def test_replaced_directory_and_link_fences_prevent_unlinking(self):
        stage = self.stage()
        stage.lease.release()
        kept = stage.parent / ("preserved-" + uuid.uuid4().hex)
        self.assertEqual(stage.path.resolve().parent, kept.resolve().parent)
        stage.path.rename(kept)
        stage.path.mkdir()
        (stage.path / "external.bin").write_bytes(b"replacement")
        with self.assertRaises(ValueError):
            cleanup.preview(self.home, 0)
        self.assertTrue((kept / "partial.bin").is_file())
        self.assertTrue((stage.path / "external.bin").is_file())
        # Remove only this fixture-created replacement, then restore its owned inode.
        (stage.path / "external.bin").unlink()
        stage.path.rmdir()
        kept.rename(stage.path)
        from decision_workbench.platform import owned_staging

        original = owned_staging.linked
        with patch.object(
            owned_staging,
            "linked",
            side_effect=lambda p: Path(p).name == "partial.bin" or original(p),
        ):
            with self.assertRaises(ValueError):
                cleanup.preview(self.home, 0)
        self.assertTrue((stage.path / "partial.bin").is_file())
        stage.close()

    def test_partial_failure_keeps_receipt_and_allows_a_fresh_review(self):
        stage = self.stage()
        stage.lease.release()
        self.addCleanup(stage.close)
        (stage.path / "second.bin").write_bytes(b"remaining transfer bytes")
        plan = cleanup.preview(self.home, 0)
        original_unlink = Path.unlink

        def unlink(path, *args, **kwargs):
            if path == stage.path / "second.bin":
                raise OSError("Fictional sharing conflict")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", unlink), self.assertRaises(RuntimeError):
            cleanup.execute(self.home, plan["plan_sha256"], 0)
        records = [
            json.loads(p.read_text(encoding="utf-8"))
            for p in (self.home / ".dmt-stage-cleanups").glob("*.json")
        ]
        self.assertEqual(records[0]["state"], "partial")
        self.assertTrue(stage.path.exists())
        self.assertFalse((stage.path / "partial.bin").exists())
        self.assertTrue((stage.path / "second.bin").is_file())
        plan = cleanup.preview(self.home, 0)
        self.assertEqual(
            cleanup.execute(self.home, plan["plan_sha256"], 0)["state"], "completed"
        )

    def test_actual_cli_requires_a_reviewed_hash_and_obeys_age(self):
        from decision_workbench.platform.cli import main

        stage = self.stage()
        stage.lease.release()
        self.addCleanup(stage.close)
        self.assertEqual(cleanup.preview(self.home)["stages"], 0)

        def command(action, *extra):
            with patch(
                "sys.argv",
                [
                    "domain-platform",
                    action,
                    "--home",
                    str(self.home),
                    "--min-age-seconds",
                    "0",
                    *extra,
                ],
            ), patch("sys.stdout", new_callable=io.StringIO) as out:
                main()
            return json.loads(out.getvalue())

        plan = command("preview-local-stage-cleanup")
        result = command(
            "apply-local-stage-cleanup", "--plan-sha256", plan["plan_sha256"]
        )
        self.assertEqual(result["state"], "completed")

    def test_real_producer_process_death_releases_lease_for_cleanup(self):
        code = (
            "from pathlib import Path;import json,time;from decision_workbench.platform.owned_staging import OwnedStage;s=OwnedStage(Path("
            + repr(str(self.home / "incoming"))
            + "),'intake');(s.path/'partial.bin').write_bytes(b'fictional subprocess transfer');print(json.dumps({'path':str(s.path)}),flush=True);time.sleep(120)"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        def stop():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()

        self.addCleanup(stop)
        messages = queue.Queue()
        thread = threading.Thread(
            target=lambda: messages.put(process.stdout.readline()), daemon=True
        )
        thread.start()
        line = messages.get(timeout=20)
        self.assertTrue(line)
        path = Path(json.loads(line)["path"])
        self.assertEqual(cleanup.preview(self.home, 0)["busy_stages"], 1)
        process.kill()
        process.wait(timeout=10)
        thread.join(timeout=2)
        plan = cleanup.preview(self.home, 0)
        self.assertEqual(plan["stages"], 1)
        cleanup.execute(self.home, plan["plan_sha256"], 0)
        self.assertFalse(path.exists())

    def test_stage_receipts_do_not_change_the_registered_workspace_backup(self):
        from decision_workbench.platform.recovery import backup_workspace, verify_backup

        stage = self.stage()
        stage.lease.release()
        self.addCleanup(stage.close)
        plan = cleanup.preview(self.home, 0)
        cleanup.execute(self.home, plan["plan_sha256"], 0)
        destination = Path(self.temp.name) / "backup"
        result = backup_workspace(self.home, destination)
        verify_backup(destination, result["manifest_sha256"])
        self.assertFalse(
            any(".dmt-stage-" in path.as_posix() for path in destination.rglob("*"))
        )


if __name__ == "__main__":
    unittest.main()
