"""Resumable version retention with real PostgreSQL and fictional object versions."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import sys
import unittest
from unittest.mock import patch
import uuid

from decision_workbench.platform import artifact_retention as retention
from decision_workbench.platform.artifact_storage import (
    ArtifactRepository,
    ArtifactUnavailable,
)
from decision_workbench.platform.artifact_s3 import S3Objects
from decision_workbench.platform.store import Store, Conflict
import test_postgres as pg


class VersionedFixtureObjects:
    """Fictional transport with immutable version identities, never a live S3 call."""

    descriptor = {
        "provider": "s3",
        "bucket": "fictional-local-only",
        "prefix": "test",
        "endpoint": "https://s3.invalid",
        "region": "us-east-1",
    }

    def __init__(self):
        self.versions = {}

    def add(self, key, body, age=0):
        item = {
            "key": key,
            "body": body,
            "bytes": len(body),
            "etag": '"' + hashlib.md5(body).hexdigest() + '"',
            "modified": time.time() - age,
            "version_id": uuid.uuid4().hex,
        }
        self.versions.setdefault(key, []).append(item)
        return item

    def current(self, key):
        if not self.versions.get(key):
            raise ArtifactUnavailable("Fictional missing object")
        return self.versions[key][-1]

    def open(self, key):
        return io.BytesIO(self.current(key)["body"])

    def put_file(self, key, source, sha, size, check=lambda: None):
        check()
        body = source.read_bytes()
        if len(body) != size or hashlib.sha256(body).hexdigest() != sha:
            raise ValueError("Fictional checksum mismatch")
        if self.versions.get(key):
            if self.current(key)["body"] != body:
                raise ValueError("Immutable key changed")
        else:
            self.add(key, body)

    def retention_inventory(self, namespace, limit):
        items = [
            {k: v for k, v in values[-1].items() if k not in {"body", "version_id"}}
            for key, values in sorted(self.versions.items())
            if values and key.startswith(namespace + "/")
        ]
        if len(items) > limit:
            raise ValueError("Fictional inventory exceeds limit")
        return items

    def pin_retention_version(self, item):
        return {**item, "version_id": self.current(item["key"])["version_id"]}

    def remove_retention_version(self, item):
        self.versions[item["key"]] = [
            v
            for v in self.versions.get(item["key"], [])
            if v["version_id"] != item["version_id"]
        ]


@unittest.skipUnless(pg.URL, "Set DMT_TEST_POSTGRES_URL for shared retention contracts")
class RetentionWorkflowTests(pg.DatabaseFixture, unittest.TestCase):
    def setUp(self):
        self.database_setup()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.objects = VersionedFixtureObjects()
        self.repository = ArtifactRepository(self.objects)
        self.store = self.node("first")

    def node(self, name):
        return Store(
            self.root / name,
            database_url=pg.URL,
            database_schema=self.schema,
            artifact_store=self.repository,
        )

    def candidate(self, body=b'{"fictional":true}'):
        identifier = uuid.uuid4().hex
        folder = self.store.folder / "artifacts" / identifier
        folder.mkdir(parents=True)
        (folder / "model.json").write_bytes(body)
        return {
            "artifact_id": identifier,
            "backend": "cpu-baseline",
            "model_sha256": hashlib.sha256(body).hexdigest(),
            "schema_sha256": "f" * 64,
        }

    def register(self, model):
        return self.store.add("model", "A", "project-a", "operator", model)

    def orphan(self, body=b"fictional orphan"):
        key = self.store.workspace_id + "/blobs/" + hashlib.sha256(body).hexdigest()
        return self.objects.add(key, body, age=2 * 86400)

    def test_registered_models_and_historical_inputs_and_young_manifests_are_retained(
        self,
    ):
        model = self.candidate()
        record = self.register(model)
        orphan = self.orphan()
        # A young uncommitted manifest protects old deduplicated files too.
        young = self.candidate(b'{"fictional":"young"}')
        with self.store.artifact_guard():
            ref = self.repository.publish(
                self.store.workspace_id,
                "b" * 64,
                self.store.folder / "artifacts" / young["artifact_id"],
                young,
            )
        blob = self.objects.current(
            self.store.workspace_id + "/blobs/" + young["model_sha256"]
        )
        blob["modified"] = time.time() - 3 * 86400
        incoming = self.candidate(b'{"fictional":"incoming"}')
        ref_incoming = self.repository.publish(
            self.store.workspace_id,
            "c" * 64,
            self.store.folder / "artifacts" / incoming["artifact_id"],
            incoming,
        )
        job = self.store.submit(
            "A",
            "project-a",
            "operator",
            "remote-import",
            {"incoming_reference": ref_incoming},
            "historical-input-001",
        )
        self.store.cancel(job["id"], "A", "project-a", "operator")
        for key, versions in self.objects.versions.items():
            if (
                key
                != self.store.workspace_id
                + "/manifests/"
                + ref["scope"]
                + "/"
                + ref["sha256"]
            ):
                for version in versions:
                    version["modified"] = time.time() - 3 * 86400
        expected = retention.preview(self.store)
        self.assertEqual(expected["objects"], 1)
        result = retention.execute(self.node("operator"), expected["plan_sha256"])
        self.assertEqual(result["state"], "completed")
        self.assertFalse(self.objects.versions[orphan["key"]])
        restored = self.node("reader").artifact_path(record["payload"])
        self.assertTrue((restored / "model.json").is_file())
        self.assertTrue(
            self.objects.current(
                self.store.workspace_id + "/blobs/" + young["model_sha256"]
            )
        )
        self.repository.inspect(self.store.workspace_id, ref_incoming)

    def test_uncertain_delete_blocks_publication_and_resume_preserves_replacement_version(
        self,
    ):
        item = self.orphan(b'{"fictional":"replacement"}')
        expected = retention.preview(self.store)
        original = self.objects.remove_retention_version

        def uncertain(candidate):
            original(candidate)
            self.replacement = self.objects.add(candidate["key"], item["body"])
            raise ArtifactUnavailable("Fictional lost deletion response")

        with patch.object(
            self.objects, "remove_retention_version", side_effect=uncertain
        ), self.assertRaises(RuntimeError):
            retention.execute(self.store, expected["plan_sha256"])
        pending = retention.status(self.store)
        self.assertEqual(pending["state"], "pending")
        model = self.candidate(item["body"])
        with self.assertRaises(ArtifactUnavailable):
            self.register(model)
        with self.assertRaises(ArtifactUnavailable):
            self.store.submit(
                "A", "project-a", "operator", "remote-import", {}, "blocked-input-001"
            )
        complete = retention.execute(
            self.node("reconciler"),
            expected["plan_sha256"],
            operation_id=pending["operation_id"],
        )
        self.assertEqual(complete["state"], "completed")
        self.assertEqual(
            self.objects.current(item["key"])["version_id"],
            self.replacement["version_id"],
        )
        registered = self.register(model)
        self.assertTrue(
            (
                self.node("api").artifact_path(registered["payload"]) / "model.json"
            ).is_file()
        )

    def test_stale_preview_unknown_objects_and_inventory_limits_fail_closed(self):
        item = self.orphan()
        expected = retention.preview(self.store)
        self.orphan(b"new old orphan")
        with self.assertRaises(Conflict):
            retention.execute(self.store, expected["plan_sha256"])
        self.assertTrue(self.objects.current(item["key"]))
        unknown = self.objects.add(
            self.store.workspace_id + "/operator-note", b"keep unknown", age=3 * 86400
        )
        plan = retention.preview(self.store)
        self.assertEqual(plan["unknown_objects"], 1)
        retention.execute(self.store, plan["plan_sha256"])
        self.assertTrue(self.objects.current(unknown["key"]))
        for i in range(3):
            self.orphan(str(i).encode())
        with self.assertRaises(ValueError):
            retention.preview(self.store, max_objects=1)

    def test_pending_jobs_prevent_cleanup_and_nested_publication_guards_do_not_deadlock(
        self,
    ):
        self.orphan()
        expected = retention.preview(self.store)
        queued = self.store.submit(
            "A", "project-a", "operator", "remote-import", {}, "queued-input-001"
        )
        with self.assertRaises(Conflict):
            retention.execute(self.store, expected["plan_sha256"])
        self.store.cancel(queued["id"], "A", "project-a", "operator")
        with self.store.artifact_guard():
            with self.store.artifact_guard():
                self.assertTrue(True)
            other = self.node("contender")
            other._postgres.timeout = 1
            with ThreadPoolExecutor(max_workers=1) as pool:

                def exclusive():
                    with other.artifact_guard(exclusive=True):
                        pass

                with self.assertRaises(ArtifactUnavailable):
                    pool.submit(exclusive).result(timeout=5)

    def test_lost_publication_guard_cannot_commit_a_model_reference(self):
        model = self.candidate()
        original_connect = self.store._postgres._driver.connect
        connections = []

        def connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            connections.append(connection)
            return connection

        original_put = self.objects.put_file

        def kill_after_manifest(key, *args, **kwargs):
            original_put(key, *args, **kwargs)
            if "/manifests/" in key:
                with original_connect(pg.URL, autocommit=True) as admin:
                    admin.execute(
                        "SELECT pg_terminate_backend(%s)",
                        (connections[0].info.backend_pid,),
                    )

        with patch.object(
            self.store._postgres._driver, "connect", side_effect=connect
        ), patch.object(self.objects, "put_file", side_effect=kill_after_manifest):
            with self.assertRaises(ArtifactUnavailable):
                self.register(model)
        self.assertEqual(self.store.list("A", "project-a", "model"), [])

    def test_corrupt_referenced_manifest_stops_cleanup_without_deleting_orphans(self):
        registered = self.register(self.candidate())
        item = self.orphan()
        reference = registered["payload"]["artifact_reference"]
        key = (
            self.store.workspace_id
            + "/manifests/"
            + reference["scope"]
            + "/"
            + reference["sha256"]
        )
        self.objects.current(key)["body"] = b"fictional tampering"
        with self.assertRaises(ValueError):
            retention.preview(self.store)
        self.assertTrue(self.objects.current(item["key"]))
        self.assertEqual(retention.status(self.store)["state"], "idle")

    def test_operator_cli_previews_applies_and_reports_the_recorded_result(self):
        from decision_workbench.platform.cli import main

        self.orphan()
        common = ["--home", str(self.store.folder), "--database-schema", self.schema]

        def command(action, *args):
            with patch.dict(os.environ, {"DMT_DATABASE_URL": pg.URL}), patch(
                "sys.argv", ["domain-platform", action, *common, *args]
            ), patch(
                "decision_workbench.platform.artifact_s3.repository_from_environment",
                return_value=self.repository,
            ), patch(
                "sys.stdout", new_callable=io.StringIO
            ) as out:
                main()
            return json.loads(out.getvalue())

        plan = command("preview-storage-cleanup")
        self.assertEqual(len(plan["candidates"]), 1)
        complete = command(
            "apply-storage-cleanup", "--plan-sha256", plan["plan_sha256"]
        )
        self.assertEqual(complete["state"], "completed")
        self.assertEqual(command("storage-cleanup-status"), complete)
        again = command(
            "resume-storage-cleanup",
            "--operation-id",
            complete["operation_id"],
            "--plan-sha256",
            plan["plan_sha256"],
        )
        self.assertEqual(again, complete)
        second = command("preview-storage-cleanup")
        command("apply-storage-cleanup", "--plan-sha256", second["plan_sha256"])
        history = command(
            "storage-cleanup-status", "--operation-id", complete["operation_id"]
        )
        self.assertEqual(history["plan_sha256"], plan["plan_sha256"])
        self.assertEqual(history["state"], "completed")
        self.assertEqual(len(history["candidates"]), 1)
        self.assertEqual(len(history["events"]), 2)

    def test_process_style_interruption_retains_the_plan_and_can_resume(self):
        self.orphan()
        plan = retention.preview(self.store)
        with patch.object(
            self.objects, "remove_retention_version", side_effect=KeyboardInterrupt
        ), self.assertRaises(KeyboardInterrupt):
            retention.execute(self.store, plan["plan_sha256"])
        pending = retention.status(self.store)
        self.assertEqual(pending["state"], "running")
        with self.assertRaises(ArtifactUnavailable):
            self.register(self.candidate())
        result = retention.execute(
            self.node("after-interruption"),
            plan["plan_sha256"],
            operation_id=pending["operation_id"],
        )
        self.assertEqual(result["state"], "completed")


class S3VersionRetentionTests(unittest.TestCase):
    def setUp(self):
        import boto3
        from botocore.stub import Stubber

        self.client = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="fictional",
            aws_secret_access_key="fictional",
        )
        self.stub = Stubber(self.client)
        self.stub.activate()
        self.addCleanup(self.stub.deactivate)
        self.objects = S3Objects(self.client, "fictional-bucket", prefix="test")
        self.namespace = "a" * 32
        self.key = self.namespace + "/blobs/" + "b" * 64
        self.stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.args = {"Bucket": "fictional-bucket", "Key": "test/" + self.key}

    def test_listing_pins_version_and_deletes_only_that_version_with_reconciliation(
        self,
    ):
        listed = {
            "Key": self.args["Key"],
            "Size": 10,
            "ETag": '"fictional-etag"',
            "LastModified": self.stamp,
        }
        self.stub.add_response(
            "get_bucket_versioning",
            {"Status": "Enabled"},
            {"Bucket": "fictional-bucket"},
        )
        self.stub.add_response(
            "list_objects_v2",
            {"IsTruncated": False, "Contents": [listed]},
            {
                "Bucket": "fictional-bucket",
                "Prefix": "test/" + self.namespace + "/",
                "MaxKeys": 11,
            },
        )
        metadata = {
            "ContentLength": 10,
            "ETag": listed["ETag"],
            "LastModified": self.stamp,
            "VersionId": "original-version",
        }
        self.stub.add_response("head_object", metadata, self.args)
        version_args = {**self.args, "VersionId": "original-version"}
        self.stub.add_response("head_object", metadata, version_args)
        self.stub.add_response(
            "delete_object", {}, {**version_args, "IfMatch": listed["ETag"]}
        )
        self.stub.add_client_error(
            "head_object",
            service_error_code="NoSuchVersion",
            http_status_code=404,
            expected_params=version_args,
        )
        inventory = self.objects.retention_inventory(self.namespace, 10)
        selected = self.objects.pin_retention_version(inventory[0])
        self.objects.remove_retention_version(selected)
        self.stub.assert_no_pending_responses()

    def test_suspended_versioning_and_ambiguous_absence_are_refused(self):
        self.stub.add_response(
            "get_bucket_versioning",
            {"Status": "Suspended"},
            {"Bucket": "fictional-bucket"},
        )
        with self.assertRaises(ValueError):
            self.objects.retention_inventory(self.namespace, 10)
        self.stub.add_client_error(
            "head_object",
            service_error_code="AccessDenied",
            http_status_code=403,
            expected_params={**self.args, "VersionId": "selected-version"},
        )
        with self.assertRaises(ArtifactUnavailable):
            self.objects.remove_retention_version(
                {"key": self.key, "version_id": "selected-version", "etag": '"etag"'}
            )
        self.stub.assert_no_pending_responses()


if __name__ == "__main__":
    unittest.main()
