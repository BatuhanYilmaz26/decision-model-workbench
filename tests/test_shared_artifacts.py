"""Immutable object contracts and PostgreSQL models across independent caches.

Directory objects are a local conformance transport. S3 calls use the actual SDK
with explicit fictional credentials and validated stubbed service responses.
"""

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from decision_workbench.platform.api import create_app
from decision_workbench.platform.artifact_storage import (
    ArtifactRepository,
    ArtifactUnavailable,
    DirectoryObjects,
    safe_name,
    validate_names,
)
from decision_workbench.platform.artifact_s3 import S3Objects
from decision_workbench.platform.store import Store, Conflict
from decision_workbench.platform.worker import Worker
import test_platform as fixtures
import test_postgres as pg


class ArtifactObjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.content = b'{"fictional":"model fixture"}'
        (self.source / "model.json").write_bytes(self.content)
        (self.source / "empty").write_bytes(b"")
        self.model = {
            "artifact_id": "a" * 32,
            "backend": "cpu-baseline",
            "model_sha256": hashlib.sha256(self.content).hexdigest(),
            "schema_sha256": "b" * 64,
        }
        self.objects = DirectoryObjects(self.root / "objects")
        self.repo = ArtifactRepository(self.objects)
        self.namespace, self.scope = "c" * 32, "d" * 64

    def publish(self):
        return self.repo.publish(self.namespace, self.scope, self.source, self.model)

    def test_complete_publish_retrieve_and_identical_republication(self):
        reference = self.publish()
        self.assertEqual(self.publish(), reference)
        dest = self.root / "cache" / self.model["artifact_id"]
        self.repo.restore(self.namespace, reference, self.model, dest)
        self.assertEqual((dest / "model.json").read_bytes(), self.content)
        self.assertEqual((dest / "empty").read_bytes(), b"")
        with patch.object(
            self.objects,
            "open",
            side_effect=AssertionError(
                "Warm prediction must not contact object storage"
            ),
        ):
            self.assertEqual(
                self.repo.restore(self.namespace, reference, self.model, dest), dest
            )
        (dest / "model.json").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.repo.restore(self.namespace, reference, self.model, dest)

    def test_corrupt_blob_or_manifest_never_publishes_a_cache(self):
        reference = self.publish()
        blob = self.objects.path(
            self.namespace + "/blobs/" + self.model["model_sha256"]
        )
        blob.write_bytes(b"corrupt")
        dest = self.root / "cache" / self.model["artifact_id"]
        with self.assertRaises(ValueError):
            self.repo.restore(self.namespace, reference, self.model, dest)
        self.assertFalse(dest.exists())
        self.assertFalse(list(dest.parent.glob(".dmt-download-*")))
        with self.assertRaises(ValueError):
            self.publish()

    def test_scope_model_identity_and_portable_names_are_checked(self):
        reference = self.publish()
        for changed in (
            {**reference, "namespace": "e" * 32},
            {**reference, "sha256": None},
            {**reference, "bytes": True},
        ):
            with self.assertRaises(ValueError):
                self.repo.restore(
                    self.namespace, changed, self.model, self.root / "bad"
                )
        with self.assertRaises(ValueError):
            self.repo.restore(
                self.namespace,
                reference,
                {**self.model, "schema_sha256": "f" * 64},
                self.root / "bad",
            )
        for name in (
            ".",
            "../private",
            "/root",
            "a//b",
            "a\\b",
            "C:private",
            "NUL.txt",
            "dir/name.",
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                safe_name(name)
        for names in (("Case/a", "case/b"), ("entry", "entry/child")):
            with self.assertRaises(ValueError):
                validate_names(names)

    def test_large_cache_miss_requires_explicit_warming(self):
        reference = self.publish()
        dest = self.root / "cache" / self.model["artifact_id"]
        with self.assertRaises(ArtifactUnavailable):
            self.repo.restore(
                self.namespace, reference, self.model, dest, max_download_bytes=1
            )
        self.assertFalse(dest.exists())
        self.repo.restore(
            self.namespace, reference, self.model, dest, max_download_bytes=None
        )
        self.assertTrue(dest.is_dir())

    def test_cancellation_does_not_publish_a_manifest(self):
        def cancelled():
            raise Conflict("Fictional cancellation")

        with self.assertRaises(Conflict):
            self.repo.publish(
                self.namespace, self.scope, self.source, self.model, cancelled
            )
        self.assertFalse(list(self.objects.root.rglob("manifests/*")))

    def test_unexpected_external_staging_is_never_written_or_deleted(self):
        reference = self.publish()
        outside = self.root / "unrelated" / ".dmt-download-unowned"
        outside.mkdir(parents=True)
        marker = outside / "keep.txt"
        marker.write_text("unrelated data", encoding="utf-8")
        with patch(
            "decision_workbench.platform.artifact_storage.tempfile.mkdtemp",
            return_value=str(outside),
        ):
            with self.assertRaises(ValueError):
                self.repo.restore(
                    self.namespace,
                    reference,
                    self.model,
                    self.root / "cache" / self.model["artifact_id"],
                )
        self.assertEqual(marker.read_text(encoding="utf-8"), "unrelated data")

    def test_disk_preflight_refuses_before_downloading_files(self):
        reference = self.publish()
        dest = self.root / "cache" / self.model["artifact_id"]
        with patch(
            "decision_workbench.platform.artifact_storage.shutil.disk_usage",
            return_value=SimpleNamespace(free=1),
        ):
            with self.assertRaisesRegex(ArtifactUnavailable, "disk space"):
                self.repo.restore(self.namespace, reference, self.model, dest)
        self.assertFalse(dest.exists())


@unittest.skipUnless(
    __import__("importlib.util", fromlist=["find_spec"]).find_spec("boto3"),
    "Install requirements/aws-sdk.txt for S3 SDK contracts",
)
class S3ArtifactTests(unittest.TestCase):
    def setUp(self):
        import boto3
        from botocore.stub import Stubber

        self.client = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="fictional-key",
            aws_secret_access_key="fictional-secret",
        )
        self.stub = Stubber(self.client)
        self.stub.activate()
        self.addCleanup(self.client.close)
        self.addCleanup(self.stub.deactivate)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "fictional-bytes"
        self.data = b"fictional artifact"
        self.source.write_bytes(self.data)
        self.sha = hashlib.sha256(self.data).hexdigest()
        self.objects = S3Objects(self.client, "fixture-bucket", part_size=5 * 1024**2)
        self.args = {"Bucket": "fixture-bucket", "Key": "decision-workbench/a/blobs/b"}

    def put_params(self):
        from botocore.stub import ANY

        return {
            **self.args,
            "Body": ANY,
            "ContentLength": len(self.data),
            "ChecksumSHA256": base64.b64encode(bytes.fromhex(self.sha)).decode(),
            "IfNoneMatch": "*",
        }

    def test_single_put_is_conditional_and_existing_bytes_are_verified(self):
        self.stub.add_client_error(
            "head_object",
            service_error_code="NotFound",
            http_status_code=404,
            expected_params=self.args,
        )
        self.stub.add_response("put_object", {"ETag": "fictional"}, self.put_params())
        self.objects.put_file("a/blobs/b", self.source, self.sha, len(self.data))
        self.stub.add_client_error(
            "head_object",
            service_error_code="NotFound",
            http_status_code=404,
            expected_params=self.args,
        )
        self.stub.add_client_error(
            "put_object",
            service_error_code="PreconditionFailed",
            http_status_code=412,
            expected_params=self.put_params(),
        )
        self.stub.add_response(
            "get_object",
            {"Body": io.BytesIO(self.data), "ContentLength": len(self.data)},
            self.args,
        )
        self.objects.put_file("a/blobs/b", self.source, self.sha, len(self.data))
        self.stub.assert_no_pending_responses()

    def test_storage_errors_do_not_expose_credentials(self):
        self.stub.add_client_error(
            "head_object",
            service_error_code="AccessDenied",
            http_status_code=403,
            expected_params=self.args,
        )
        self.stub.add_client_error(
            "put_object",
            service_error_code="AccessDenied",
            service_message="fictional-secret",
            http_status_code=403,
            expected_params=self.put_params(),
        )
        with self.assertRaises(ArtifactUnavailable) as raised:
            self.objects.put_file("a/blobs/b", self.source, self.sha, len(self.data))
        self.assertNotIn("fictional-secret", str(raised.exception))

    def test_verified_existing_object_does_not_upload_again(self):
        self.stub.add_response(
            "head_object", {"ContentLength": len(self.data)}, self.args
        )
        self.stub.add_response(
            "get_object",
            {"Body": io.BytesIO(self.data), "ContentLength": len(self.data)},
            self.args,
        )
        self.source.unlink()
        self.objects.put_file("a/blobs/b", self.source, self.sha, len(self.data))
        self.stub.add_response(
            "head_object", {"ContentLength": len(self.data) + 1}, self.args
        )
        with self.assertRaises(ValueError):
            self.objects.put_file("a/blobs/b", self.source, self.sha, len(self.data))
        self.stub.assert_no_pending_responses()

    def test_multipart_completion_remains_conditional(self):
        self.data = b"x" * (6 * 1024**2)
        self.source.write_bytes(self.data)
        sha = hashlib.sha256(self.data).hexdigest()
        self.stub.add_client_error(
            "head_object",
            service_error_code="NotFound",
            http_status_code=404,
            expected_params=self.args,
        )
        self.stub.add_response(
            "create_multipart_upload",
            {"UploadId": "owned-upload"},
            {**self.args, "ChecksumAlgorithm": "SHA256"},
        )
        parts = []
        for number, data in enumerate(
            (self.data[: 5 * 1024**2], self.data[5 * 1024**2 :]), 1
        ):
            checksum = base64.b64encode(hashlib.sha256(data).digest()).decode()
            etag = "part-" + str(number)
            self.stub.add_response(
                "upload_part",
                {"ETag": etag},
                {
                    **self.args,
                    "UploadId": "owned-upload",
                    "PartNumber": number,
                    "Body": data,
                    "ChecksumSHA256": checksum,
                },
            )
            parts.append(
                {"PartNumber": number, "ETag": etag, "ChecksumSHA256": checksum}
            )
        self.stub.add_response(
            "complete_multipart_upload",
            {"ETag": "completed"},
            {
                **self.args,
                "UploadId": "owned-upload",
                "MultipartUpload": {"Parts": parts},
                "IfNoneMatch": "*",
            },
        )
        self.objects.put_file("a/blobs/b", self.source, sha, len(self.data))
        self.stub.assert_no_pending_responses()

    def test_cancelled_multipart_aborts_only_its_own_upload(self):
        self.data = b"x" * (6 * 1024**2)
        self.source.write_bytes(self.data)
        sha = hashlib.sha256(self.data).hexdigest()
        part = self.data[: 5 * 1024**2]
        self.stub.add_client_error(
            "head_object",
            service_error_code="NotFound",
            http_status_code=404,
            expected_params=self.args,
        )
        self.stub.add_response(
            "create_multipart_upload",
            {"UploadId": "owned-upload"},
            {**self.args, "ChecksumAlgorithm": "SHA256"},
        )
        self.stub.add_response(
            "upload_part",
            {"ETag": "part-one"},
            {
                **self.args,
                "UploadId": "owned-upload",
                "PartNumber": 1,
                "Body": part,
                "ChecksumSHA256": base64.b64encode(
                    hashlib.sha256(part).digest()
                ).decode(),
            },
        )
        self.stub.add_response(
            "abort_multipart_upload", {}, {**self.args, "UploadId": "owned-upload"}
        )
        calls = [0]

        def cancelled():
            calls[0] += 1
            if calls[0] == 3:
                raise Conflict("Fictional cancellation")

        with self.assertRaises(Conflict):
            self.objects.put_file(
                "a/blobs/b", self.source, sha, len(self.data), cancelled
            )
        self.stub.assert_no_pending_responses()


@unittest.skipUnless(pg.URL, "Set DMT_TEST_POSTGRES_URL for shared artifact workflows")
class SharedArtifactWorkflowTests(pg.DatabaseFixture, fixtures.PlatformTests):
    def setUp(self):
        self.database_setup()
        self.object_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.object_temp.cleanup)
        self.repository = ArtifactRepository(
            DirectoryObjects(Path(self.object_temp.name) / "objects")
        )
        ap = patch.object(
            fixtures,
            "create_app",
            side_effect=lambda *a, **kw: create_app(
                *a,
                **kw,
                database_url=pg.URL,
                database_schema=self.schema,
                artifact_store=self.repository
            ),
        )
        sp = patch.object(fixtures, "Store", side_effect=self.node)
        ap.start()
        sp.start()
        self.addCleanup(ap.stop)
        self.addCleanup(sp.stop)
        super().setUp()
        self.addCleanup(self.app.state.service.runtime.close)

    def node(self, home):
        return Store(
            home,
            database_url=pg.URL,
            database_schema=self.schema,
            artifact_store=self.repository,
        )

    def test_separate_worker_and_api_caches_release_and_predict(self):
        snapshot = self.snapshot()
        job = self.job(
            "train",
            {"snapshot": snapshot, "backend": "cpu-baseline"},
            "independent-node",
        )
        worker_store = self.node(self.home / "worker-node")
        worker = Worker(worker_store)
        self.addCleanup(worker.service.runtime.close)
        published = []
        original = self.repository.objects.put_file

        def observe_publication(key, *args, **kwargs):
            self.assertEqual(self.app.state.store.list("A", self.project, "model"), [])
            published.append(key)
            return original(key, *args, **kwargs)

        with patch.object(
            self.repository.objects, "put_file", side_effect=observe_publication
        ):
            self.assertTrue(worker.once())
        self.assertIn("/manifests/", published[-1])
        result = worker_store.job(job["id"], "A", self.project)
        self.assertEqual(result["status"], "succeeded", result["error"])
        identifier = result["result"]["model"]
        model = worker_store.get(identifier, "A", self.project, "model")["payload"]
        self.assertFalse((self.home / "artifacts" / model["artifact_id"]).exists())
        response = self.client.post(
            self.prefix + "/releases",
            json={
                "model_id": identifier,
                "expected_revision": 0,
                "note": "Independent artifact-cache release",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(
            (self.home / "artifacts" / model["artifact_id"] / "model.json").is_file()
        )
        with TestClient(
            create_app(
                self.home / "third-node",
                fixtures.keys(),
                database_url=pg.URL,
                database_schema=self.schema,
                artifact_store=self.repository,
            )
        ) as third:
            self.addCleanup(third.app.state.service.runtime.close)
            response = third.post(
                self.prefix + "/decisions",
                json={
                    "state": "invoice payment refund requested urgent",
                    "threshold": 0.5,
                },
                headers={"Authorization": "Bearer infer-secret"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["answers"]["team"]["answer"], "billing")
            denied = third.get(
                self.prefix, headers={"Authorization": "Bearer b-secret"}
            )
            self.assertEqual(denied.status_code, 404)

    def test_storage_configuration_mismatch_is_refused(self):
        with self.assertRaises(ValueError):
            Store(
                self.home / "unconfigured",
                database_url=pg.URL,
                database_schema=self.schema,
            )
        other = ArtifactRepository(
            DirectoryObjects(Path(self.object_temp.name) / "other")
        )
        with self.assertRaises(ValueError):
            Store(
                self.home / "other-config",
                database_url=pg.URL,
                database_schema=self.schema,
                artifact_store=other,
            )

    def test_failed_manifest_publication_never_creates_a_model_record(self):
        snapshot = self.snapshot()
        job = self.job(
            "train",
            {"snapshot": snapshot, "backend": "cpu-baseline"},
            "publication-refusal",
        )
        worker = Worker(self.node(self.home / "failed-worker"))
        self.addCleanup(worker.service.runtime.close)
        original = self.repository.objects.put_file

        def unavailable(key, *args, **kwargs):
            if "/manifests/" in key:
                raise ArtifactUnavailable("Fictional storage outage")
            return original(key, *args, **kwargs)

        with patch.object(self.repository.objects, "put_file", side_effect=unavailable):
            self.assertTrue(worker.once())
        result = self.app.state.store.job(job["id"], "A", self.project)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.app.state.store.list("A", self.project, "model"), [])


if __name__ == "__main__":
    unittest.main()
