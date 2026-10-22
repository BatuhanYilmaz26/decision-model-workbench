"""Offline cache ownership, reference preservation, cold reload and recovery."""

import hashlib
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient
from decision_workbench.platform import cache_retention as cleanup
from decision_workbench.platform import cache_usage
from decision_workbench.platform.artifact_storage import (
    ArtifactRepository,
    ArtifactUnavailable,
    DirectoryObjects,
)
from decision_workbench.platform.api import create_app
from decision_workbench.platform.cache_records import record_path
from decision_workbench.platform.project_lifecycle import transition
from decision_workbench.platform import project_deletion
from decision_workbench.platform.service import Service
from decision_workbench.platform.store import Store, Conflict
from decision_workbench.platform.worker import Worker
from decision_workbench.training.resources import ResourceBusy
import test_platform as fixtures
import test_postgres as pg

OWNER = {"tenant": "A", "actor": "owner", "role": "admin"}


class CacheLeaseTests(unittest.TestCase):
    def test_unknown_lease_control_files_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            users, _ = cache_usage.paths(folder)
            for name, body in (
                ("operator-notes.txt", b"fictional note"),
                ("a" * 32 + ".lock", b"unmanaged content"),
            ):
                path = users / name
                path.write_bytes(body)
                with self.assertRaises(ValueError):
                    with cache_usage.maintenance(folder):
                        pass
                self.assertEqual(path.read_bytes(), body)
                path.unlink()

    def test_process_death_releases_node_user_but_live_process_blocks_maintenance(self):
        with tempfile.TemporaryDirectory() as folder:
            env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
            code = "from decision_workbench.platform.cache_usage import use;import sys,time;\nwith use(sys.argv[1]):\n print('ready',flush=True);time.sleep(120)"
            child = subprocess.Popen(
                [sys.executable, "-c", code, folder],
                env=env,
                stdout=subprocess.PIPE,
                text=True,
            )
            try:
                self.assertEqual(child.stdout.readline().strip(), "ready")
                with self.assertRaises(ResourceBusy):
                    with cache_usage.maintenance(folder):
                        pass
            finally:
                child.terminate()
                child.wait(timeout=10)
                child.stdout.close()
            with cache_usage.maintenance(folder):
                pass

    def test_local_only_workspace_cannot_evict_its_only_model_copies(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "shared artifact"):
                cleanup.preview(Store(folder), 0)

    def test_s3_availability_uses_head_without_download_and_refuses_size_or_access_errors(
        self,
    ):
        try:
            import boto3
            from botocore.stub import Stubber
        except ImportError:
            self.skipTest("Install the optional AWS SDK profile for S3 contract checks")
        from decision_workbench.platform.artifact_s3 import S3Objects

        client = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="fictional",
            aws_secret_access_key="fictional",
        )
        self.addCleanup(client.close)
        objects = S3Objects(client, "fictional-bucket")
        args = {"Bucket": "fictional-bucket", "Key": "decision-workbench/a/blobs/b"}
        with Stubber(client) as stub:
            stub.add_response("head_object", {"ContentLength": 3}, args)
            objects.available("a/blobs/b", 3)
            stub.add_response("head_object", {"ContentLength": 4}, args)
            with self.assertRaises(ArtifactUnavailable):
                objects.available("a/blobs/b", 3)
            stub.add_client_error(
                "head_object",
                service_error_code="AccessDenied",
                http_status_code=403,
                expected_params=args,
            )
            with self.assertRaises(ArtifactUnavailable):
                objects.available("a/blobs/b", 3)
            stub.assert_no_pending_responses()


@unittest.skipUnless(pg.URL, "Set DMT_TEST_POSTGRES_URL for cache lifecycle checks")
class CacheRetentionTests(pg.DatabaseFixture, unittest.TestCase):
    def setUp(self):
        self.database_setup()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = ArtifactRepository(DirectoryObjects(self.root / "objects"))
        self.store = self.node(self.root / "node")
        self.service = Service(self.store)
        self.addCleanup(self.service.runtime.close)
        self.project = self.service.create_project(
            OWNER, "Fictional cache", "A fictional decision policy", fixtures.QUESTIONS
        )["id"]

    def node(self, home):
        return Store(
            home,
            database_url=pg.URL,
            database_schema=self.schema,
            artifact_store=self.repo,
        )

    def model(self):
        identifier = uuid.uuid4().hex
        path = self.store.folder / "artifacts" / identifier
        path.mkdir(parents=True)
        content = b'{"fictional":"cache fixture"}'
        (path / "model.json").write_bytes(content)
        (path / "a.bin").write_bytes(b"fictional extra bytes")
        payload = {
            "artifact_id": identifier,
            "backend": "cpu-baseline",
            "model_sha256": hashlib.sha256(content).hexdigest(),
            "schema_sha256": self.service.project(OWNER, self.project)["payload"][
                "schema_sha256"
            ],
            "report": {},
        }
        entity = self.store.add("model", "A", self.project, "owner", payload)
        return entity, path

    def test_inactive_cache_eviction_preserves_registry_objects_and_cold_reload(self):
        model, path = self.model()
        before = self.store.get(model["id"], "A", self.project, "model")
        preview = cleanup.preview(self.store, 0)
        self.assertEqual(preview["caches"], 1)
        result = cleanup.execute(self.store, preview["plan_sha256"], 0)
        self.assertEqual(result["completed_caches"], 1)
        self.assertFalse(path.exists())
        self.assertEqual(
            self.store.get(model["id"], "A", self.project, "model"), before
        )
        self.assertEqual(self.store.artifact_path(model["payload"]), path)
        self.assertEqual(
            (path / "model.json").read_bytes(), b'{"fictional":"cache fixture"}'
        )
        self.assertTrue(record_path(path).is_file())

    def test_learned_model_can_be_released_and_predict_after_cache_eviction(self):
        app = create_app(
            self.store.folder,
            fixtures.keys(),
            database_url=pg.URL,
            database_schema=self.schema,
            artifact_store=self.repo,
        )
        client = TestClient(app)
        self.addCleanup(client.close)
        self.addCleanup(app.state.service.runtime.close)
        client.headers["Authorization"] = "Bearer a-secret"
        case = fixtures.PlatformTests()
        case.home, case.app, case.client, case.project = (
            self.store.folder,
            app,
            client,
            self.project,
        )
        case.prefix = "/v1/projects/" + self.project
        with patch.object(fixtures, "Store", side_effect=self.node):
            snapshot = case.snapshot()
        job = case.job(
            "train",
            {"snapshot": snapshot, "backend": "cpu-baseline"},
            "learned-cache-training",
        )
        worker = Worker(self.store)
        self.addCleanup(worker.service.runtime.close)
        self.assertTrue(worker.once())
        finished = self.store.job(job["id"], "A", self.project)
        self.assertEqual(finished["status"], "succeeded", finished["error"])
        identifier = finished["result"]["model"]
        model = self.store.get(identifier, "A", self.project, "model")["payload"]
        path = self.store.folder / "artifacts" / model["artifact_id"]
        preview = cleanup.preview(self.store, 0)
        self.assertEqual(preview["caches"], 1)
        cleanup.execute(self.store, preview["plan_sha256"], 0)
        self.assertFalse(path.exists())
        released = client.post(
            case.prefix + "/releases",
            json={
                "model_id": identifier,
                "expected_revision": 0,
                "note": "Release verified model after cache eviction",
            },
        )
        self.assertEqual(released.status_code, 200, released.text)
        predicted = client.post(
            case.prefix + "/decisions",
            json={"state": "invoice payment refund requested urgent", "threshold": 0.5},
        )
        self.assertEqual(predicted.status_code, 200, predicted.text)
        self.assertEqual(predicted.json()["answers"]["team"]["answer"], "billing")
        self.assertEqual(
            hashlib.sha256((path / "model.json").read_bytes()).hexdigest(),
            model["model_sha256"],
        )

    def test_released_cache_and_pending_intake_are_preserved(self):
        model, path = self.model()
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO active_models VALUES (?,?,?,?)",
                ("A", self.project, model["id"], 1),
            )
        identifier = uuid.uuid4().hex
        incoming = self.store.folder / "incoming" / identifier
        incoming.mkdir(parents=True)
        (incoming / "model.json").write_bytes((path / "model.json").read_bytes())
        payload = {**model["payload"], "artifact_id": identifier}
        reference = self.repo.publish(
            self.store.workspace_id, "d" * 64, incoming, payload
        )
        self.store.submit(
            "A",
            self.project,
            "owner",
            "remote-import",
            {"incoming_id": identifier, "incoming_reference": reference},
            "pending-intake",
        )
        preview = cleanup.preview(self.store, 0)
        self.assertEqual(preview["caches"], 0)
        self.assertTrue(path.is_dir() and incoming.is_dir())

    def test_live_api_worker_and_direct_reader_refuse_offline_maintenance(self):
        app = create_app(
            self.store.folder,
            fixtures.keys(),
            database_url=pg.URL,
            database_schema=self.schema,
            artifact_store=self.repo,
        )
        with TestClient(app):
            with self.assertRaises(ResourceBusy):
                cleanup.preview(self.store, 0)
        worker = Worker(self.store)
        with worker.presence():
            with self.assertRaises(ResourceBusy):
                cleanup.preview(self.store, 0)
        worker.service.runtime.close()
        with cache_usage.use(self.store.folder):
            with self.assertRaises(ResourceBusy):
                cleanup.preview(self.store, 0)
        self.assertEqual(cleanup.preview(self.store, 0)["caches"], 0)

    def test_stale_plan_refuses_new_release_without_removing_files(self):
        model, path = self.model()
        preview = cleanup.preview(self.store, 0)
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO active_models VALUES (?,?,?,?)",
                ("A", self.project, model["id"], 1),
            )
        with self.assertRaises(Conflict):
            cleanup.execute(self.store, preview["plan_sha256"], 0)
        self.assertTrue(path.is_dir())

    def test_recent_and_unmanaged_directories_are_preserved(self):
        model, path = self.model()
        unmanaged = self.store.folder / "artifacts" / uuid.uuid4().hex
        unmanaged.mkdir()
        (unmanaged / "keep.txt").write_text("fictional user file")
        self.assertEqual(cleanup.preview(self.store)["caches"], 0)
        self.assertTrue(path.is_dir() and unmanaged.is_dir())
        record_path(path).unlink()
        preview = cleanup.preview(self.store, 0)
        self.assertEqual(preview["caches"], 1)
        self.assertEqual(preview["candidates"][0]["source"], "registry")
        cleanup.execute(self.store, preview["plan_sha256"], 0)
        self.assertTrue(unmanaged.is_dir())

    def test_missing_recovery_blob_or_changed_local_file_refuses_eviction(self):
        model, path = self.model()
        blob = self.repo.objects.path(
            self.store.workspace_id + "/blobs/" + model["payload"]["model_sha256"]
        )
        content = blob.read_bytes()
        blob.unlink()
        with self.assertRaises(ArtifactUnavailable):
            cleanup.preview(self.store, 0)
        blob.write_bytes(content)
        (path / "a.bin").write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            cleanup.preview(self.store, 0)
        self.assertTrue(path.is_dir())

    def test_deleted_project_orphan_can_be_reclaimed_after_remote_object_gc(self):
        model, path = self.model()
        transition(self.store, OWNER, self.project, "archived", 0, self.project)
        preview = project_deletion.preview(
            self.store, OWNER, self.project, min_archived_days=0
        )
        project_deletion.execute(
            self.store,
            OWNER,
            self.project,
            preview["plan_sha256"],
            self.project,
            min_archived_days=0,
        )
        blob = self.repo.objects.path(
            self.store.workspace_id + "/blobs/" + model["payload"]["model_sha256"]
        )
        blob.unlink()
        preview = cleanup.preview(self.store, 0)
        self.assertEqual(preview["caches"], 1)
        cleanup.execute(self.store, preview["plan_sha256"], 0)
        self.assertFalse(path.exists())
        self.assertEqual(
            project_deletion.receipt(self.store, OWNER, self.project)["project"],
            self.project,
        )

    def test_partial_cleanup_resumes_without_removing_a_newly_warmed_cache(self):
        model, path = self.model()
        preview = cleanup.preview(self.store, 0)
        original = Path.unlink

        def fail_one(target, *args, **kwargs):
            if target.name == "model.json" and ".dmt-cache-evict-" in str(target):
                raise OSError("fictional interruption")
            return original(target, *args, **kwargs)

        with patch.object(Path, "unlink", fail_one), self.assertRaisesRegex(
            RuntimeError, "resume operation"
        ):
            cleanup.execute(self.store, preview["plan_sha256"], 0)
        receipts = list((self.store.folder / ".dmt-cache-cleanups").glob("*.json"))
        value = json.loads(receipts[0].read_text())
        operation = value["operation_id"]
        self.assertFalse(path.exists())
        self.store.artifact_path(model["payload"])
        result = cleanup.execute(
            self.store, preview["plan_sha256"], operation_id=operation
        )
        self.assertEqual(result["state"], "completed")
        self.assertTrue((path / "model.json").is_file())
        self.assertEqual(
            cleanup.execute(self.store, preview["plan_sha256"], operation_id=operation),
            result,
        )
        self.assertFalse(list(path.parent.glob(".dmt-cache-evict-*")))

    def test_replaced_marker_is_not_deleted(self):
        model, path = self.model()
        marker = record_path(path)
        value = json.loads(marker.read_text())
        value["namespace"] = "f" * 32
        marker.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            cleanup.preview(self.store, 0)
        self.assertTrue(path.is_dir())

    def test_unexpected_quarantine_file_refuses_resume_without_removing_it(self):
        model, path = self.model()
        preview = cleanup.preview(self.store, 0)
        with patch.object(
            cleanup, "remove_quarantine", side_effect=OSError("fictional interruption")
        ):
            with self.assertRaises(RuntimeError):
                cleanup.execute(self.store, preview["plan_sha256"], 0)
        value = json.loads(
            next((self.store.folder / ".dmt-cache-cleanups").glob("*.json")).read_text()
        )
        quarantined = next(path.parent.glob(".dmt-cache-evict-*"))
        unexpected = quarantined / "unexpected.txt"
        unexpected.write_text("fictional operator file")
        with self.assertRaises(RuntimeError):
            cleanup.execute(
                self.store, preview["plan_sha256"], operation_id=value["operation_id"]
            )
        self.assertTrue(unexpected.is_file())

    def test_other_node_reader_and_cache_are_unchanged_by_selected_node_eviction(self):
        model, path = self.model()
        other = self.node(self.root / "other-node")
        other_path = other.artifact_path(model["payload"])
        with cache_usage.use(other.folder):
            preview = cleanup.preview(self.store, 0)
            cleanup.execute(self.store, preview["plan_sha256"], 0)
        self.assertFalse(path.exists())
        self.assertTrue((other_path / "model.json").is_file())

    def test_other_node_release_waits_for_cache_maintenance_fence(self):
        model, path = self.model()
        other = self.node(self.root / "release-node")
        preview = cleanup.preview(self.store, 0)
        started, done = threading.Event(), threading.Event()
        errors, threads = [], []
        original = cleanup.remove_quarantine

        def release():
            started.set()
            try:
                other.activate(
                    "A", self.project, "owner", model["id"], 0, "fictional release"
                )
            except Exception as exc:
                errors.append(exc)
            finally:
                done.set()

        def observe(*args):
            thread = threading.Thread(target=release)
            threads.append(thread)
            thread.start()
            self.assertTrue(started.wait(2))
            self.assertFalse(
                done.wait(0.2),
                "Release committed while maintenance held the workspace guard",
            )
            return original(*args)

        try:
            with patch.object(cleanup, "remove_quarantine", side_effect=observe):
                cleanup.execute(self.store, preview["plan_sha256"], 0)
        finally:
            for thread in threads:
                thread.join(timeout=15)
        self.assertFalse(errors, errors)
        self.assertTrue(done.is_set())
        self.assertFalse(path.exists())
        self.assertEqual(other.active("A", self.project)["model_id"], model["id"])

    def test_operator_cli_previews_and_applies_the_same_reviewed_policy(self):
        from decision_workbench.platform.cli import main

        model, path = self.model()
        base = [
            "domain-platform",
            "preview-cache-cleanup",
            "--home",
            str(self.store.folder),
            "--database-schema",
            self.schema,
            "--min-idle-seconds",
            "0",
        ]
        with patch.dict(os.environ, {"DMT_DATABASE_URL": pg.URL}), patch(
            "decision_workbench.platform.artifact_s3.repository_from_environment",
            return_value=self.repo,
        ):
            output = io.StringIO()
            with patch.object(sys, "argv", base), redirect_stdout(output):
                main()
            preview = json.loads(output.getvalue())
            base[1] = "apply-cache-cleanup"
            output = io.StringIO()
            with patch.object(
                sys, "argv", base + ["--plan-sha256", preview["plan_sha256"]]
            ), redirect_stdout(output):
                main()
            self.assertEqual(json.loads(output.getvalue())["completed_caches"], 1)
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
