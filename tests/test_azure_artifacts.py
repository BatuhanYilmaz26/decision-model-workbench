"""Azure immutable objects, recovery and actual SDK request contracts; no cloud calls."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from decision_workbench.platform.artifact_azure import AzureBlobObjects
from decision_workbench.platform.artifact_storage import (
    ArtifactRepository,
    ArtifactUnavailable,
)
from decision_workbench.platform.shared_recovery_objects import (
    RecoveryObjects,
    independent,
)
from azure_blob_fixture import MemoryAccount

try:
    HAVE_SDK = importlib.util.find_spec("azure.storage.blob") is not None
except ModuleNotFoundError:
    HAVE_SDK = False


@unittest.skipUnless(
    HAVE_SDK, "Install requirements/azure-artifacts.txt for Azure SDK checks"
)
class AzureArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.account = MemoryAccount()
        self.credential = SimpleNamespace(
            get_token=lambda *args: SimpleNamespace(token="fictional-copy-token")
        )
        self.objects = AzureBlobObjects(
            self.account, "artifacts", "live", credential=self.credential
        )
        self.repo = ArtifactRepository(self.objects)
        self.source = self.root / "source"
        self.source.mkdir()
        self.body = b'{"fictional":"azure model"}'
        (self.source / "model.json").write_bytes(self.body)
        (self.source / "empty").write_bytes(b"")
        self.sha = hashlib.sha256(self.body).hexdigest()
        self.namespace, self.scope = "a" * 32, "b" * 64
        self.model = dict(
            artifact_id="c" * 32,
            backend="cpu-baseline",
            model_sha256=self.sha,
            schema_sha256="d" * 64,
        )

    def test_full_repository_publish_reload_and_conflicting_concurrent_writers(self):
        def publish():
            return self.repo.publish(
                self.namespace, self.scope, self.source, self.model
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            refs = list(pool.map(lambda _: publish(), range(2)))
        self.assertEqual(refs[0], refs[1])
        target = self.root / "cache" / self.model["artifact_id"]
        self.repo.restore(self.namespace, refs[0], self.model, target)
        self.assertEqual((target / "model.json").read_bytes(), self.body)
        self.assertEqual((target / "empty").read_bytes(), b"")
        changed = self.root / "changed"
        changed.write_bytes(b"different")
        key = self.namespace + "/blobs/" + self.sha
        with self.assertRaisesRegex(ValueError, "differs"):
            self.objects.put_file(
                key, changed, hashlib.sha256(b"different").hexdigest(), 9
            )
        self.assertEqual(self.objects.open(key).read(), self.body)

    def test_hash_failure_and_cancellation_never_commit_an_object(self):
        source = self.source / "model.json"
        with self.assertRaises(ValueError):
            self.objects.put_file("wrong", source, "e" * 64, len(self.body))
        self.assertNotIn(("artifacts", "live/wrong"), self.account.current)

        class Cancelled(RuntimeError):
            pass

        def cancel():
            raise Cancelled("Fictional cancellation")

        with self.assertRaises(Cancelled):
            self.objects.put_file("cancelled", source, self.sha, len(self.body), cancel)
        self.assertNotIn(("artifacts", "live/cancelled"), self.account.current)

    def test_inventory_bounds_version_pinning_and_deletion_preserve_replacement(self):
        self.repo.publish(self.namespace, self.scope, self.source, self.model)
        with self.assertRaisesRegex(ValueError, "bound"):
            self.objects.retention_inventory(self.namespace, 1)
        items = self.objects.retention_inventory(self.namespace, 10)
        self.assertEqual(len(items), 3)
        item = next(x for x in items if x["key"].endswith(self.sha))
        pinned = self.objects.pin_retention_version(item)
        key = ("artifacts", self.objects.key(item["key"]))
        old = self.account.current.pop(key)
        self.objects.put_file(
            item["key"], self.source / "model.json", self.sha, len(self.body)
        )
        replacement = self.account.current[key]
        self.objects.remove_retention_version(pinned)
        self.assertEqual(self.account.current[key], replacement)
        self.assertNotIn((*key, old), self.account.versions)
        self.objects.remove_retention_version(pinned)
        self.account.versioning = False
        with self.assertRaisesRegex(ValueError, "versioning"):
            self.objects.retention_inventory(self.namespace, 10)

    def test_server_side_recovery_copies_exact_version_and_refuses_overlap(self):
        key = self.namespace + "/blobs/" + self.sha
        self.objects.put_file(key, self.source / "model.json", self.sha, len(self.body))
        source = RecoveryObjects(self.objects)
        pinned = source.pin(key, self.sha, len(self.body))
        target_objects = self.objects.scoped("vault")
        with self.assertRaisesRegex(ValueError, "overlap"):
            independent(self.objects, target_objects)
        target_objects = AzureBlobObjects(
            self.account, "backups", "vault", credential=self.credential
        )
        target = RecoveryObjects(target_objects)
        copied = source.copy_to(pinned, target)
        self.assertEqual(target.read(copied, 100), self.body)
        self.assertTrue(any(c[0] == "server-copy" for c in self.account.calls))
        # Retrying a completed copy verifies the existing destination without overwriting it.
        self.assertEqual(source.copy_to(pinned, target), copied)
        self.assertEqual(source.read(pinned, 100), self.body)
        with self.assertRaisesRegex(ValueError, "bound"):
            source.read(pinned, 1)

    def test_endpoint_keys_and_transport_selection_fail_closed(self):
        for url in [
            self.account.url + "?sig=private",
            "http://fictionalaccount.blob.core.windows.net",
            "https://elsewhere.example",
        ]:
            with self.assertRaises(ValueError):
                AzureBlobObjects(SimpleNamespace(url=url), "artifacts")
        for key in ["../outside", "a//b", "/absolute", "A", "x" * 1024]:
            with self.assertRaises(ValueError):
                self.objects.key(key)
        from decision_workbench.platform.artifact_s3 import repository_from_environment

        with patch.dict(
            os.environ,
            {
                "DMT_ARTIFACT_S3_BUCKET": "fictional-bucket",
                "DMT_ARTIFACT_AZURE_ACCOUNT_URL": self.account.url,
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "one object transport"):
                repository_from_environment()

    def test_installed_sdk_emits_conditional_commit_and_versioned_delete(self):
        from azure.core import MatchConditions
        from azure.core.credentials import AccessToken
        from azure.core.pipeline.transport import HttpTransport, HttpResponse
        from azure.storage.blob import BlobServiceClient
        from requests.structures import CaseInsensitiveDict

        requests = []

        class Response(HttpResponse):
            def __init__(self, request):
                super().__init__(request, None)
                self.status_code = 202 if request.method == "DELETE" else 201
                self.headers = CaseInsensitiveDict(
                    {
                        "etag": '"fictional-etag"',
                        "x-ms-version-id": "v1",
                        "content-length": "0",
                    }
                )
                self.reason = "Fictional"

            def body(self):
                return b""

        class Transport(HttpTransport):
            def open(self):
                pass

            def close(self):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

            def send(self, request, **kwargs):
                assert (
                    "source_etag" not in kwargs
                    and "source_match_condition" not in kwargs
                )
                requests.append(request)
                return Response(request)

        credential = SimpleNamespace(
            get_token=lambda *a, **k: AccessToken(
                "fictional-token", int(time.time()) + 3600
            )
        )
        client = BlobServiceClient(
            self.account.url,
            credential=credential,
            transport=Transport(),
            retry_total=0,
        )
        blob = client.get_blob_client("artifacts", "live/object")
        blob.commit_block_list(
            [],
            metadata={"sha256": "a" * 64},
            etag="*",
            match_condition=MatchConditions.IfMissing,
        )
        version = client.get_blob_client("artifacts", "live/object", version_id="v1")
        version.delete_blob(
            etag='"fictional-etag"', match_condition=MatchConditions.IfNotModified
        )
        objects = AzureBlobObjects(client, "artifacts", "live", credential=credential)
        destination = AzureBlobObjects(
            client, "backups", "vault", credential=credential
        )
        objects.recovery_copy_to(
            {
                "key": "object",
                "sha256": "a" * 64,
                "bytes": 9,
                "version_id": "v1",
                "etag": '"fictional-etag"',
            },
            destination,
            lambda: None,
        )
        blob.stage_block(
            "fictional-block", b"fictional", length=9, validate_content="md5"
        )
        self.assertEqual(requests[0].headers["If-None-Match"], "*")
        self.assertEqual(requests[0].headers["x-ms-meta-sha256"], "a" * 64)
        self.assertIn("versionid=v1", requests[1].url.lower())
        self.assertEqual(requests[1].headers["If-Match"], '"fictional-etag"')
        self.assertIn("versionid=v1", requests[2].headers["x-ms-copy-source"].lower())
        self.assertEqual(
            requests[2].headers["x-ms-copy-source-authorization"],
            "Bearer fictional-token",
        )
        self.assertEqual(
            requests[2].headers["x-ms-source-if-match"], '"fictional-etag"'
        )
        self.assertEqual(requests[2].headers["x-ms-source-range"], "bytes=0-8")
        self.assertIn("Content-MD5", requests[4].headers)


if __name__ == "__main__":
    unittest.main()
