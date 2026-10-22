"""Independent recovery storage, pinned copies and installed S3 SDK contracts."""

import hashlib
import io
from pathlib import Path
import tempfile
import unittest

from decision_workbench.platform.artifact_storage import (
    DirectoryObjects,
    ArtifactUnavailable,
)
from decision_workbench.platform.shared_recovery_objects import (
    RecoveryObjects,
    independent,
)


class DirectoryRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = RecoveryObjects(DirectoryObjects(self.root / "source"))
        self.target = RecoveryObjects(DirectoryObjects(self.root / "vault"))
        self.body = b"fictional recovery object"
        self.sha = hashlib.sha256(self.body).hexdigest()
        self.key = "a" * 32 + "/blobs/" + self.sha
        path = self.source.objects.path(self.key)
        path.parent.mkdir(parents=True)
        path.write_bytes(self.body)

    def test_copy_survives_source_deletion_and_checks_content(self):
        pinned = self.source.pin(self.key, self.sha, len(self.body))
        copied = self.source.copy_to(pinned, self.target)
        self.source.objects.path(self.key).unlink()
        self.assertEqual(self.target.read(copied, 128), self.body)
        self.target.objects.path(self.key).write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.target.verify(copied)

    def test_overlapping_roots_and_changed_source_are_refused(self):
        nested = DirectoryObjects(self.source.objects.root / "nested")
        with self.assertRaises(ValueError):
            independent(self.source.objects, nested)
        pinned = self.source.pin(self.key, self.sha, len(self.body))
        self.source.objects.path(self.key).write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.source.copy_to(pinned, self.target)
        self.assertFalse(self.target.objects.path(self.key).exists())

    def test_deadline_and_manifest_capacity_are_enforced(self):
        pinned = self.source.pin(self.key, self.sha, len(self.body))
        with self.assertRaises(ValueError):
            self.source.read(pinned, 1)

        def expired():
            raise TimeoutError("fictional deadline")

        with self.assertRaises(TimeoutError):
            self.source.copy_to(pinned, self.target, expired)
        self.assertFalse(self.target.objects.path(self.key).exists())


try:
    import boto3
    from botocore.stub import Stubber
    from botocore.response import StreamingBody
except ImportError:
    boto3 = None


@unittest.skipUnless(boto3, "Install requirements/aws-sdk.txt for S3 copy contracts")
class S3RecoveryTests(unittest.TestCase):
    def client(self):
        return boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="fictional",
            aws_secret_access_key="fictional",
        )

    def transport(self, client, bucket, prefix):
        from decision_workbench.platform.artifact_s3 import S3Objects

        return S3Objects(client, bucket, prefix)

    def test_managed_copy_uses_source_version_and_etag_without_getting_weights(self):
        source_client, target_client = self.client(), self.client()
        source = self.transport(source_client, "fictional-source", "live")
        target = self.transport(target_client, "fictional-vault", "backup/snapshot")
        body = b"fictional model"
        sha = hashlib.sha256(body).hexdigest()
        key = "a" * 32 + "/blobs/" + sha
        pin = {
            "key": key,
            "sha256": sha,
            "bytes": len(body),
            "version_id": "source-version",
            "etag": '"source-etag"',
        }
        head = {
            "ContentLength": len(body),
            "VersionId": pin["version_id"],
            "ETag": pin["etag"],
        }
        with Stubber(source_client) as left, Stubber(target_client) as right:
            left.add_response(
                "get_bucket_versioning",
                {"Status": "Enabled"},
                {"Bucket": source.bucket},
            )
            right.add_response(
                "get_bucket_versioning",
                {"Status": "Enabled"},
                {"Bucket": target.bucket},
            )
            expected_head = {
                "Bucket": source.bucket,
                "Key": source.key(key),
                "VersionId": pin["version_id"],
            }
            left.add_response("head_object", head, expected_head)
            # Boto3's managed copy independently obtains source length/ETag.
            left.add_response(
                "head_object", head, {**expected_head, "IfMatch": pin["etag"]}
            )
            right.add_response(
                "copy_object",
                {
                    "CopyObjectResult": {"ETag": '"copied-etag"'},
                    "VersionId": "vault-version",
                },
                {
                    "Bucket": target.bucket,
                    "Key": target.key(key),
                    "CopySource": {
                        "Bucket": source.bucket,
                        "Key": source.key(key),
                        "VersionId": pin["version_id"],
                    },
                    "CopySourceIfMatch": pin["etag"],
                },
            )
            right.add_response(
                "head_object",
                {
                    "ContentLength": len(body),
                    "VersionId": "vault-version",
                    "ETag": '"copied-etag"',
                },
                {"Bucket": target.bucket, "Key": target.key(key)},
            )
            result = RecoveryObjects(source).copy_to(pin, RecoveryObjects(target))
            self.assertEqual(result["version_id"], "vault-version")
            left.assert_no_pending_responses()
            right.assert_no_pending_responses()

    def test_disabled_versioning_and_unversioned_head_are_refused(self):
        client = self.client()
        objects = self.transport(client, "fictional-source", "live")
        with Stubber(client) as stub:
            stub.add_response("get_bucket_versioning", {}, {"Bucket": objects.bucket})
            with self.assertRaises(ValueError):
                RecoveryObjects(objects)
        with Stubber(client) as stub:
            stub.add_response(
                "get_bucket_versioning",
                {"Status": "Enabled"},
                {"Bucket": objects.bucket},
            )
            stub.add_response(
                "head_object",
                {"ContentLength": 12, "ETag": '"etag"'},
                {"Bucket": objects.bucket, "Key": objects.key("a/blobs/" + "b" * 64)},
            )
            with self.assertRaises(ValueError):
                RecoveryObjects(objects).pin("a/blobs/" + "b" * 64, "b" * 64, 12)

    def test_large_managed_copy_uses_multipart_ranges_without_local_payload(self):
        source_client, target_client = self.client(), self.client()
        source = self.transport(source_client, "fictional-source", "live")
        target = self.transport(target_client, "fictional-vault", "backup/snapshot")
        size = 129 * 1024**2
        key = "a" * 32 + "/blobs/" + "b" * 64
        item = {
            "key": key,
            "sha256": "b" * 64,
            "bytes": size,
            "version_id": "source-version",
            "etag": '"source-etag"',
        }
        head = {
            "ContentLength": size,
            "VersionId": item["version_id"],
            "ETag": item["etag"],
        }
        args = {"Bucket": target.bucket, "Key": target.key(key)}
        copy_source = {
            "Bucket": source.bucket,
            "Key": source.key(key),
            "VersionId": item["version_id"],
        }
        with Stubber(source_client) as left, Stubber(target_client) as right:
            left.add_response(
                "get_bucket_versioning",
                {"Status": "Enabled"},
                {"Bucket": source.bucket},
            )
            right.add_response(
                "get_bucket_versioning",
                {"Status": "Enabled"},
                {"Bucket": target.bucket},
            )
            left.add_response("head_object", head, copy_source)
            left.add_response(
                "head_object", head, {**copy_source, "IfMatch": item["etag"]}
            )
            right.add_response(
                "create_multipart_upload", {"UploadId": "owned-copy"}, args
            )
            parts = []
            for index, start in enumerate(range(0, size, 64 * 1024**2), 1):
                etag = '"part-' + str(index) + '"'
                end = min(size, start + 64 * 1024**2) - 1
                right.add_response(
                    "upload_part_copy",
                    {"CopyPartResult": {"ETag": etag}},
                    {
                        **args,
                        "UploadId": "owned-copy",
                        "PartNumber": index,
                        "CopySource": copy_source,
                        "CopySourceIfMatch": item["etag"],
                        "CopySourceRange": f"bytes={start}-{end}",
                    },
                )
                parts.append({"PartNumber": index, "ETag": etag})
            right.add_response(
                "complete_multipart_upload",
                {"VersionId": "copied-version"},
                {**args, "UploadId": "owned-copy", "MultipartUpload": {"Parts": parts}},
            )
            right.add_response(
                "head_object",
                {
                    "ContentLength": size,
                    "VersionId": "copied-version",
                    "ETag": '"final-etag"',
                },
                args,
            )
            copied = RecoveryObjects(source).copy_to(item, RecoveryObjects(target))
            self.assertEqual(copied["version_id"], "copied-version")
            left.assert_no_pending_responses()
            right.assert_no_pending_responses()

    def test_manifest_read_uses_exact_version_and_bounded_hash_check(self):
        client = self.client()
        objects = self.transport(client, "fictional-vault", "backup/snapshot")
        body = b"fictional manifest"
        item = {
            "key": "a/manifests/b/c",
            "sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body),
            "version_id": "retained-version",
            "etag": '"etag"',
        }
        with Stubber(client) as stub:
            stub.add_response(
                "get_bucket_versioning",
                {"Status": "Enabled"},
                {"Bucket": objects.bucket},
            )
            stub.add_response(
                "get_object",
                {"Body": StreamingBody(io.BytesIO(body), len(body))},
                {
                    "Bucket": objects.bucket,
                    "Key": objects.key(item["key"]),
                    "VersionId": item["version_id"],
                    "IfMatch": item["etag"],
                },
            )
            self.assertEqual(RecoveryObjects(objects).read(item, 1024), body)
            stub.assert_no_pending_responses()

    def test_overlap_and_different_service_refused(self):
        client = self.client()
        a = self.transport(client, "fictional-source", "live")
        for prefix in ("live", "live/backup"):
            with self.assertRaises(ValueError):
                independent(a, self.transport(client, a.bucket, prefix))
        other = boto3.client(
            "s3",
            region_name="us-east-1",
            endpoint_url="https://storage.example.invalid",
            aws_access_key_id="fictional",
            aws_secret_access_key="fictional",
        )
        with self.assertRaises(ValueError):
            independent(a, self.transport(other, "fictional-vault", "backup"))


if __name__ == "__main__":
    unittest.main()
