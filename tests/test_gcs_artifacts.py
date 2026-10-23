"""Native Google SDK contracts and immutable artifacts without an account or network."""

import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from decision_workbench.platform.artifact_gcs import GCSObjects
from decision_workbench.platform.shared_recovery_objects import (
    RecoveryObjects,
    independent,
)

try:
    HAVE_SDK = importlib.util.find_spec("google.cloud.storage") is not None
except ModuleNotFoundError:
    HAVE_SDK = False


@unittest.skipUnless(
    HAVE_SDK, "Install requirements/gcp-artifacts.txt for native GCS contracts"
)
class GCSArtifactTests(unittest.TestCase):
    def setUp(self):
        from gcs_http_fixture import MemoryGCS, client

        self.transport = MemoryGCS()
        self.client = client(self.transport)
        self.objects = GCSObjects(self.client, "fictional-artifacts", "live")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "source.bin"
        self.source.write_bytes(b"fictional domain model")
        self.sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.size = self.source.stat().st_size
        self.key = "a" * 32 + "/model"

    def put(self):
        self.objects.put_file(self.key, self.source, self.sha, self.size)

    def test_actual_sdk_stages_checks_and_conditionally_publishes_immutable_content(
        self,
    ):
        self.put()
        self.put()
        with self.objects.open(self.key) as body:
            self.assertEqual(body.read(), self.source.read_bytes())
        requests = self.transport.requests
        self.assertTrue(
            any(
                x["query"].get("uploadType") == "multipart"
                and x["query"].get("ifGenerationMatch") == "0"
                for x in requests
            )
        )
        copies = [x for x in requests if "/rewriteTo/" in x["url"]]
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0]["query"]["ifGenerationMatch"], "0")
        self.assertEqual(
            copies[0]["query"]["ifSourceGenerationMatch"],
            copies[0]["query"]["sourceGeneration"],
        )
        self.source.write_bytes(b"different same request")
        with self.assertRaisesRegex(ValueError, "differs"):
            self.objects.put_file(
                self.key,
                self.source,
                hashlib.sha256(self.source.read_bytes()).hexdigest(),
                self.source.stat().st_size,
            )

    def test_actual_sdk_resumable_upload_uses_bounded_chunks(self):
        self.source.write_bytes(b"fictional" * (1024 * 1024 + 1))
        self.sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.size = self.source.stat().st_size
        self.put()
        uploads = [r for r in self.transport.requests if r["method"] == "PUT"]
        self.assertEqual(len(uploads), 2)
        self.assertTrue(
            any(
                r["query"].get("uploadType") == "resumable"
                and r["query"].get("ifGenerationMatch") == "0"
                for r in self.transport.requests
            )
        )
        with self.objects.open(self.key) as body:
            digest = hashlib.sha256()
            while chunk := body.read(1024 * 1024):
                digest.update(chunk)
        self.assertEqual(digest.hexdigest(), self.sha)

    def test_generation_pinned_cross_bucket_recovery_and_exact_retention(self):
        self.put()
        recovery = RecoveryObjects(self.objects)
        item = recovery.pin(self.key, self.sha, self.size)
        vault = GCSObjects(self.client, "fictional-backups", "vault")
        target = RecoveryObjects(vault)
        copied = recovery.copy_to(item, target)
        self.assertEqual(target.read(copied, 1024), self.source.read_bytes())
        self.assertNotEqual(copied["version_id"], item["version_id"])
        inventory = self.objects.retention_inventory("a" * 32, 10)
        original = next(x for x in inventory if x["key"] == self.key)
        pinned = self.objects.pin_retention_version(original)
        self.objects.remove_retention_version(pinned)
        self.objects.remove_retention_version(pinned)
        target.verify(copied)
        self.assertTrue(
            any(
                x["method"] == "DELETE"
                and x["query"].get("generation") == item["version_id"]
                and x["query"].get("ifGenerationMatch") == item["version_id"]
                and x["query"].get("ifMetagenerationMatch") == "1"
                for x in self.transport.requests
            )
        )
        with self.assertRaisesRegex(ValueError, "overlap"):
            independent(self.objects, self.objects.scoped("nested"))

    def test_bad_content_cancellation_versioning_and_inventory_bounds_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "before publication"):
            self.objects.put_file(self.key, self.source, "0" * 64, self.size)
        self.assertEqual(len(self.transport.requests), 0)

        def cancel():
            raise TimeoutError("cancelled")

        with self.assertRaises(TimeoutError):
            self.objects.put_file(self.key, self.source, self.sha, self.size, cancel)
        self.put()
        with self.assertRaisesRegex(ValueError, "bound"):
            self.objects.retention_inventory("a" * 32, 1)
        self.transport.versioning = False
        with self.assertRaisesRegex(ValueError, "versioning"):
            RecoveryObjects(self.objects)
        for version in ["0", "latest", "../1001", 1001]:
            with self.assertRaises(ValueError):
                self.objects.blob(self.key, version)

    def test_factory_rejects_mixed_transports_before_credential_discovery(self):
        from decision_workbench.platform.artifact_s3 import repository_from_environment

        with patch.dict(
            os.environ,
            {
                "DMT_ARTIFACT_S3_BUCKET": "fictional-one",
                "DMT_ARTIFACT_GCS_BUCKET": "fictional-two",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "one object transport"):
                repository_from_environment()
