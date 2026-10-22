"""Version-pinned recovery copies without downloading S3 model weights."""

from contextlib import closing
import hashlib
import re

from .artifact_s3 import S3Objects
from .artifact_storage import (
    ArtifactUnavailable,
    DirectoryObjects,
    checksum,
)

HEX = re.compile(r"[a-f0-9]{64}\Z")
KEY = re.compile(r"[a-z0-9][a-z0-9/_-]*\Z")


def validate_item(item):
    if (
        not isinstance(item, dict)
        or set(item) != {"key", "sha256", "bytes", "version_id", "etag"}
        or not isinstance(item["key"], str)
        or not KEY.fullmatch(item["key"])
        or any(p in {"", ".", ".."} for p in item["key"].split("/"))
        or not isinstance(item["sha256"], str)
        or not HEX.fullmatch(item["sha256"])
        or type(item["bytes"]) is not int
        or not 0 <= item["bytes"] <= 500 * 1024**3
        or any(
            not isinstance(item[k], str) or not 1 <= len(item[k]) <= 2048
            for k in ("version_id", "etag")
        )
        or item["version_id"] == "null"
    ):
        raise ValueError("Invalid recovery object version")


def independent(left, right):
    """Refuse an overlapping vault/restore scope before creating any objects."""
    if isinstance(left, DirectoryObjects) and isinstance(right, DirectoryObjects):
        if (
            left.root in (right.root, *right.root.parents)
            or right.root in left.root.parents
        ):
            raise ValueError("Recovery storage must be independent of live storage")
        return
    if not isinstance(left, S3Objects) or not isinstance(right, S3Objects):
        raise ValueError("Recovery copies require matching object transports")
    a, b = left.descriptor, right.descriptor
    # AWS regional endpoints share a service; arbitrary gateways must match exactly.
    aws = re.compile(r"https://s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com(?:\.cn)?/?\Z")
    if a["endpoint"] != b["endpoint"] and not (
        aws.fullmatch(a["endpoint"]) and aws.fullmatch(b["endpoint"])
    ):
        raise ValueError("Server-side recovery copy requires the same S3 service")
    if a["bucket"] == b["bucket"] and (
        a["prefix"] == b["prefix"]
        or a["prefix"].startswith(b["prefix"] + "/")
        or b["prefix"].startswith(a["prefix"] + "/")
    ):
        raise ValueError("Recovery storage prefixes must not overlap")


class RecoveryObjects:
    """A single recovery operation on versioned S3 or local conformance storage."""

    def __init__(self, objects):
        self.objects = objects
        if isinstance(objects, S3Objects):
            try:
                status = objects.client.get_bucket_versioning(Bucket=objects.bucket)
                if status.get("Status") != "Enabled":
                    raise ValueError("Recovery requires enabled S3 bucket versioning")
            except ValueError:
                raise
            except Exception:
                raise ArtifactUnavailable(
                    "Cannot inspect recovery bucket versioning"
                ) from None
        elif not isinstance(objects, DirectoryObjects):
            raise ValueError("Unsupported recovery object transport")

    def pin(self, key, sha, size, *, version=None):
        item = {
            "key": key,
            "sha256": sha,
            "bytes": size,
            "version_id": version or sha,
            "etag": sha,
        }
        validate_item(item)
        objects = self.objects
        if isinstance(objects, DirectoryObjects):
            if version is not None and version != sha:
                raise ValueError("Local recovery object identity differs")
            if checksum(objects.path(key)) != (sha, size):
                raise ValueError("Local recovery object content differs")
            return item
        try:
            args = {"Bucket": objects.bucket, "Key": objects.key(key)}
            if version is not None:
                args["VersionId"] = version
            value = objects.client.head_object(**args)
            item.update(version_id=value.get("VersionId"), etag=value.get("ETag"))
            validate_item(item)
            if value.get("ContentLength") != size or (
                version and item["version_id"] != version
            ):
                raise ValueError("Recovery object size/version differs")
            return item
        except (ValueError, TypeError):
            raise ValueError("Recovery object size/version is invalid") from None
        except Exception:
            raise ArtifactUnavailable("Cannot pin recovery object version") from None

    def verify(self, item):
        validate_item(item)
        found = self.pin(
            item["key"], item["sha256"], item["bytes"], version=item["version_id"]
        )
        if found != item:
            raise ValueError("Recovery object version metadata differs")

    def read(self, item, limit):
        validate_item(item)
        if item["bytes"] > limit:
            raise ValueError("Recovery manifest exceeds its bounded size")
        objects = self.objects
        try:
            if isinstance(objects, DirectoryObjects):
                stream = objects.open(item["key"])
            else:
                stream = objects.client.get_object(
                    Bucket=objects.bucket,
                    Key=objects.key(item["key"]),
                    VersionId=item["version_id"],
                    IfMatch=item["etag"],
                )["Body"]
            with closing(stream):
                body = stream.read(limit + 1)
        except Exception:
            raise ArtifactUnavailable("Cannot read pinned recovery manifest") from None
        if (
            len(body) != item["bytes"]
            or hashlib.sha256(body).hexdigest() != item["sha256"]
        ):
            raise ValueError("Pinned recovery manifest content differs")
        return body

    def copy_to(self, item, target, check=lambda: None):
        validate_item(item)
        independent(self.objects, target.objects)
        check()
        self.verify(item)
        source, destination = self.objects, target.objects
        if isinstance(source, DirectoryObjects):
            destination.put_file(
                item["key"],
                source.path(item["key"]),
                item["sha256"],
                item["bytes"],
                check,
            )
        else:
            from boto3.s3.transfer import TransferConfig

            try:
                destination.client.copy(
                    {
                        "Bucket": source.bucket,
                        "Key": source.key(item["key"]),
                        "VersionId": item["version_id"],
                    },
                    destination.bucket,
                    destination.key(item["key"]),
                    ExtraArgs={"CopySourceIfMatch": item["etag"]},
                    SourceClient=source.client,
                    Config=TransferConfig(
                        multipart_threshold=64 * 1024**2,
                        multipart_chunksize=64 * 1024**2,
                        max_concurrency=1,
                        use_threads=False,
                    ),
                    Callback=lambda count: check(),
                )
            except Exception:
                raise ArtifactUnavailable(
                    "Server-side recovery object copy failed"
                ) from None
        check()
        return target.pin(item["key"], item["sha256"], item["bytes"])
