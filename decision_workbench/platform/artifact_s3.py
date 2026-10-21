"""Conditional S3 model-object writes with bounded multipart transfers.

The operator supplies a general-purpose bucket and an IAM identity. Credentials
are resolved by the AWS SDK; none are stored in model records or browser state.
"""

import base64
from contextlib import closing
import hashlib
import ipaddress
import math
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from .artifact_storage import ArtifactRepository, ArtifactUnavailable, CHUNK


class _CheckedBody:
    def __init__(self, stream, check):
        self.stream, self.check = stream, check

    def read(self, *args):
        self.check()
        return self.stream.read(*args)

    def __getattr__(self, name):
        return getattr(self.stream, name)


class _ObjectBody:
    def __init__(self, stream):
        self.stream = stream

    def read(self, *args):
        from botocore.exceptions import BotoCoreError

        try:
            return self.stream.read(*args)
        except (BotoCoreError, OSError):
            raise ArtifactUnavailable(
                "Artifact object download was interrupted"
            ) from None

    def close(self):
        self.stream.close()


class S3Objects:
    def __init__(
        self, client, bucket, prefix="decision-workbench", part_size=64 * 1024**2
    ):
        if not isinstance(bucket, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket
        ):
            raise ValueError("Use a general-purpose S3 bucket name")
        if (
            not isinstance(prefix, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9/_-]*", prefix)
            or any(p in {"", ".", ".."} for p in prefix.split("/"))
        ):
            raise ValueError("Use a canonical lowercase S3 object prefix")
        if type(part_size) is not int or not 5 * 1024**2 <= part_size <= 256 * 1024**2:
            raise ValueError("Multipart part size must be between 5 and 256 MiB")
        self.client, self.bucket, self.prefix, self.part_size = (
            client,
            bucket,
            prefix,
            part_size,
        )
        endpoint = client.meta.endpoint_url
        parsed = urlsplit(endpoint)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "S3 endpoint must not contain credentials or query parameters"
            )
        if parsed.scheme != "https":
            try:
                local = ipaddress.ip_address(parsed.hostname or "").is_loopback
            except ValueError:
                local = False
            if parsed.scheme != "http" or not local:
                raise ValueError(
                    "S3 requires HTTPS except for an explicit loopback test endpoint"
                )
        self.descriptor = {
            "provider": "s3",
            "bucket": bucket,
            "prefix": prefix,
            "endpoint": endpoint,
            "region": client.meta.region_name,
        }

    def key(self, key):
        if not re.fullmatch(r"[a-z0-9][a-z0-9/_-]*", key) or any(
            p in {"", ".", ".."} for p in key.split("/")
        ):
            raise ValueError("Invalid artifact object key")
        return self.prefix + "/" + key

    def open(self, key):
        try:
            return _ObjectBody(
                self.client.get_object(Bucket=self.bucket, Key=self.key(key))["Body"]
            )
        except Exception:
            raise ArtifactUnavailable(
                "Artifact object storage is unavailable"
            ) from None

    def retention_inventory(self, namespace, limit):
        """List one workspace only; permanent cleanup requires enabled versioning."""
        if (
            not re.fullmatch(r"[a-f0-9]{32}", namespace)
            or type(limit) is not int
            or not 1 <= limit <= 100000
        ):
            raise ValueError("Invalid workspace inventory scope or object limit")
        try:
            if (
                self.client.get_bucket_versioning(Bucket=self.bucket).get("Status")
                != "Enabled"
            ):
                raise ValueError(
                    "Shared-object cleanup requires S3 versioning Enabled; suspended/unversioned buckets are refused"
                )
            prefix = self.key(namespace) + "/"
            result, keys, tokens, token = [], set(), set(), None
            while True:
                args = {
                    "Bucket": self.bucket,
                    "Prefix": prefix,
                    "MaxKeys": min(1000, limit + 1 - len(result)),
                }
                if token:
                    args["ContinuationToken"] = token
                page = self.client.list_objects_v2(**args)
                for item in page.get("Contents", []):
                    key, size, etag, stamp = (
                        item.get(k) for k in ("Key", "Size", "ETag", "LastModified")
                    )
                    if (
                        not isinstance(key, str)
                        or not key.startswith(prefix)
                        or len(key.encode()) > 1024
                        or key in keys
                        or type(size) is not int
                        or size < 0
                        or not isinstance(etag, str)
                        or not 1 <= len(etag) <= 256
                        or etag == "*"
                        or getattr(stamp, "tzinfo", None) is None
                        or not math.isfinite(stamp.timestamp())
                    ):
                        raise ValueError(
                            "Object storage returned an invalid or duplicate inventory record"
                        )
                    keys.add(key)
                    result.append(
                        {
                            "key": key[len(self.prefix) + 1 :],
                            "bytes": size,
                            "etag": etag,
                            "modified": stamp.timestamp(),
                        }
                    )
                    if len(result) > limit:
                        raise ValueError(
                            "Object inventory exceeds the selected limit; no objects deleted"
                        )
                if page.get("IsTruncated") is False:
                    return result
                token = page.get("NextContinuationToken")
                if (
                    not isinstance(token, str)
                    or not 1 <= len(token) <= 4096
                    or token in tokens
                    or len(result) >= limit
                ):
                    raise ValueError(
                        "Object listing is incomplete or exceeds its limit; no objects deleted"
                    )
                tokens.add(token)
        except (ValueError, ArtifactUnavailable):
            raise
        except Exception:
            raise ArtifactUnavailable(
                "Versioned object inventory is unavailable; check operator storage permissions"
            ) from None

    def pin_retention_version(self, item):
        try:
            value = self.client.head_object(
                Bucket=self.bucket, Key=self.key(item["key"])
            )
        except Exception:
            raise ArtifactUnavailable(
                "Cleanup candidate changed or its version is unavailable; preview again"
            ) from None
        identifier = value.get("VersionId")
        stamp = value.get("LastModified")
        if (
            not isinstance(identifier, str)
            or not identifier
            or identifier == "null"
            or len(identifier) > 1024
            or value.get("ETag") != item["etag"]
            or value.get("ContentLength") != item["bytes"]
            or getattr(stamp, "tzinfo", None) is None
            or stamp.timestamp() != item["modified"]
        ):
            raise ValueError(
                "Cleanup candidate differs from its listed immutable version; preview again"
            )
        return {**item, "version_id": identifier}

    def remove_retention_version(self, item):
        """Delete and reconcile only a selected version, never its replacement."""
        from botocore.exceptions import ClientError

        args = {
            "Bucket": self.bucket,
            "Key": self.key(item["key"]),
            "VersionId": item["version_id"],
        }

        def absent():
            try:
                self.client.head_object(**args)
                return False
            except ClientError as exc:
                if str(exc.response.get("Error", {}).get("Code")) in {
                    "404",
                    "NoSuchKey",
                    "NoSuchVersion",
                    "NotFound",
                }:
                    return True
                raise

        try:
            if absent():
                return
            self.client.delete_object(**args, IfMatch=item["etag"])
            if not absent():
                raise ArtifactUnavailable(
                    "Deleted object version is still visible; keep cleanup pending and reconcile"
                )
        except ArtifactUnavailable:
            raise
        except Exception:
            raise ArtifactUnavailable(
                "Object-version deletion is unconfirmed; resume the recorded cleanup before publication"
            ) from None

    def _existing(self, key, sha, size, check):
        actual, received = hashlib.sha256(), 0
        with closing(self.open(key)) as stream:
            while chunk := stream.read(min(CHUNK, size - received + 1)):
                check()
                received += len(chunk)
                if received > size:
                    raise ValueError("Existing immutable object has different content")
                actual.update(chunk)
        if received != size or actual.hexdigest() != sha:
            raise ValueError("Existing immutable object has different content")

    def put_file(self, key, source, sha, size, check=lambda: None):
        from botocore.exceptions import BotoCoreError, ClientError

        args = {"Bucket": self.bucket, "Key": self.key(key)}
        upload, completed = None, False
        try:
            check()
            try:
                existing = self.client.head_object(**args)
            except ClientError as exc:
                if exc.response.get("ResponseMetadata", {}).get(
                    "HTTPStatusCode"
                ) not in (403, 404):
                    raise
                # A missing key can return 403 when ListBucket is not granted.
                # Conditional publication still enforces the actual write rule.
            else:
                if existing.get("ContentLength") != size:
                    raise ValueError("Existing immutable object has different content")
                self._existing(key, sha, size, check)
                return
            with Path(source).open("rb") as stream:
                if size <= self.part_size:
                    self.client.put_object(
                        **args,
                        Body=_CheckedBody(stream, check),
                        ContentLength=size,
                        ChecksumSHA256=base64.b64encode(bytes.fromhex(sha)).decode(),
                        IfNoneMatch="*",
                    )
                else:
                    upload = self.client.create_multipart_upload(
                        **args, ChecksumAlgorithm="SHA256"
                    )["UploadId"]
                    parts, actual, received = [], hashlib.sha256(), 0
                    while content := stream.read(self.part_size):
                        check()
                        received += len(content)
                        actual.update(content)
                        if received > size or len(parts) >= 10000:
                            raise ValueError(
                                "Artifact changed or exceeded multipart limits"
                            )
                        checksum = base64.b64encode(
                            hashlib.sha256(content).digest()
                        ).decode()
                        number = len(parts) + 1
                        part = self.client.upload_part(
                            **args,
                            UploadId=upload,
                            PartNumber=number,
                            Body=content,
                            ChecksumSHA256=checksum,
                        )
                        parts.append(
                            {
                                "PartNumber": number,
                                "ETag": part["ETag"],
                                "ChecksumSHA256": checksum,
                            }
                        )
                    if received != size or actual.hexdigest() != sha:
                        raise ValueError("Artifact changed during publication")
                    check()
                    self.client.complete_multipart_upload(
                        **args,
                        UploadId=upload,
                        MultipartUpload={"Parts": parts},
                        IfNoneMatch="*",
                    )
                    completed = True
            check()
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 412:
                self._existing(key, sha, size, check)
            else:
                raise ArtifactUnavailable(
                    "Artifact publication failed; inspect storage permissions and retry the approved job"
                ) from None
        except BotoCoreError:
            raise ArtifactUnavailable(
                "Artifact publication could not be confirmed"
            ) from None
        finally:
            if upload is not None and not completed:
                try:
                    self.client.abort_multipart_upload(**args, UploadId=upload)
                except ClientError as exc:
                    if exc.response.get("Error", {}).get("Code") != "NoSuchUpload":
                        raise ArtifactUnavailable(
                            "Incomplete artifact upload needs operator cleanup"
                        ) from None
                except BotoCoreError:
                    raise ArtifactUnavailable(
                        "Incomplete artifact upload cleanup could not be confirmed"
                    ) from None


def repository_from_environment():
    """Opt-in storage configuration for the PostgreSQL operator launcher only."""
    bucket = os.environ.get("DMT_ARTIFACT_S3_BUCKET", "").strip()
    if not bucket:
        return None
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise RuntimeError(
            "Install requirements/aws-sdk.txt for S3 artifact storage"
        ) from None
    client = boto3.client(
        "s3",
        region_name=os.environ.get("DMT_ARTIFACT_S3_REGION", "us-east-1"),
        endpoint_url=os.environ.get("DMT_ARTIFACT_S3_ENDPOINT") or None,
        config=Config(
            signature_version="s3v4",
            connect_timeout=10,
            read_timeout=120,
            retries={"total_max_attempts": 1},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )
    return ArtifactRepository(
        S3Objects(
            client,
            bucket,
            os.environ.get("DMT_ARTIFACT_S3_PREFIX", "decision-workbench"),
        )
    )
