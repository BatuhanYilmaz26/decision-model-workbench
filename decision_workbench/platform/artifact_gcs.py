"""Generation-pinned Google Cloud Storage artifacts and server-side recovery copies."""

from contextlib import closing
import hashlib
import math
import re
import uuid

from .artifact_storage import ArtifactUnavailable, CHUNK, MAX_BYTES, checksum
from .artifact_azure import _key


class _CheckedReader:
    def __init__(self, stream, check):
        self.stream, self.check = stream, check
        self.digest, self.received = hashlib.sha256(), 0

    def read(self, size=-1):
        self.check()
        value = self.stream.read(size)
        self.digest.update(value)
        self.received += len(value)
        return value

    def tell(self):
        return self.stream.tell()


class GCSObjects:
    """Immutable final objects in a private versioned bucket, using operator ADC."""

    def __init__(self, client, bucket, prefix="decision-workbench"):
        if (
            not isinstance(bucket, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,61}[a-z0-9]", bucket)
            or re.fullmatch(r"\d+\.\d+\.\d+\.\d+", bucket)
        ):
            raise ValueError("Use a canonical GCS bucket name without dots")
        self.client, self.bucket, self.prefix = client, bucket, _key(prefix)
        self.descriptor = {"provider": "gcs", "bucket": bucket, "prefix": self.prefix}

    def scoped(self, identifier):
        return GCSObjects(
            self.client, self.bucket, self.prefix + "/" + _key(identifier)
        )

    def key(self, key):
        return _key(self.prefix + "/" + _key(key))

    def blob(self, key, version=None):
        if version is not None and (
            not isinstance(version, str)
            or not re.fullmatch(r"[1-9][0-9]{0,29}", version)
        ):
            raise ValueError("Use an exact positive GCS object generation")
        return self.client.bucket(self.bucket).blob(
            self.key(key), generation=int(version) if version is not None else None
        )

    def _head(self, key, version=None):
        blob = self.blob(key, version)
        blob.reload(timeout=(10, 120), retry=None)
        return blob

    def open(self, key):
        try:
            # Resolve the generation before progressive range reads.

            blob = self._head(key)
            return blob.open(
                "rb", chunk_size=CHUNK, timeout=(10, 120), retry=None, raw_download=True
            )
        except Exception:
            raise ArtifactUnavailable("GCS artifact storage is unavailable") from None

    def available(self, key, size):
        try:
            if self._head(key).size != size:
                raise ValueError("GCS recovery object size differs")
        except Exception:
            raise ArtifactUnavailable("GCS recovery object is unavailable") from None

    def _existing(self, key, sha, size, check):
        digest, received = hashlib.sha256(), 0
        with closing(self.open(key)) as stream:
            while chunk := stream.read(min(CHUNK, size - received + 1)):
                check()
                received += len(chunk)
                digest.update(chunk)
                if received > size:
                    raise ValueError("Existing immutable GCS object differs")
        if received != size or digest.hexdigest() != sha:
            raise ValueError("Existing immutable GCS object differs")

    def _rewrite(self, source, target, check):
        token, seen = None, set()
        while True:
            check()
            token, written, total = target.rewrite(
                source,
                token=token,
                if_generation_match=0,
                if_source_generation_match=source.generation,
                timeout=(10, 120),
                retry=None,
            )
            if written > total or total != source.size:
                raise ValueError("GCS copy returned inconsistent object lengths")
            if token is None:
                if written != total:
                    raise ValueError("GCS copy did not complete")
                break
            if (
                not isinstance(token, str)
                or not 1 <= len(token) <= 8192
                or token in seen
            ):
                raise ValueError("GCS copy did not advance")
            seen.add(token)
        check()

    def put_file(self, key, source, sha, size, check=lambda: None):
        from google.api_core.exceptions import PreconditionFailed, NotFound

        if (
            not isinstance(sha, str)
            or not re.fullmatch(r"[a-f0-9]{64}", sha)
            or type(size) is not int
            or not 0 <= size <= MAX_BYTES
        ):
            raise ValueError("Invalid GCS artifact size or digest")
        if checksum(source, check) != (sha, size):
            raise ValueError("GCS artifact changed before publication")
        callback_failed = False

        def guard():
            nonlocal callback_failed
            try:
                check()
            except BaseException:
                callback_failed = True
                raise

        try:
            try:
                self._head(key)
            except NotFound:
                pass
            else:
                self._existing(key, sha, size, guard)
                return
            # Publish only after the actual uploaded bytes have passed SHA-256.
            # Staging objects are immutable orphans, removed by reviewed retention.

            stage = self.blob(key + "-upload-" + uuid.uuid4().hex)
            stage.chunk_size = 8 * 1024**2
            stage.metadata = {"sha256": sha}
            with source.open("rb") as stream:
                reader = _CheckedReader(stream, guard)
                stage.upload_from_file(
                    reader,
                    size=size,
                    rewind=False,
                    checksum="crc32c",
                    if_generation_match=0,
                    timeout=(10, 120),
                    retry=None,
                )
                if reader.received != size or reader.digest.hexdigest() != sha:
                    raise ValueError("GCS artifact changed during publication")
            guard()
            self._rewrite(stage, self.blob(key), guard)
        except PreconditionFailed:
            if callback_failed:
                raise
            self._existing(key, sha, size, guard)
        except (ValueError, TimeoutError):
            raise
        except Exception:
            if callback_failed:
                raise
            raise ArtifactUnavailable(
                "GCS artifact publication is unconfirmed; inspect the approved operation"
            ) from None

    def require_versioning(self):
        try:
            bucket = self.client.bucket(self.bucket)
            bucket.reload(timeout=(10, 120), retry=None)
            if bucket.versioning_enabled is not True:
                raise ValueError("GCS retention/recovery requires enabled versioning")
        except ValueError:
            raise
        except Exception:
            raise ArtifactUnavailable("Cannot inspect GCS bucket versioning") from None

    def retention_inventory(self, namespace, limit):
        if (
            not isinstance(namespace, str)
            or not re.fullmatch(r"[a-f0-9]{32}", namespace)
            or type(limit) is not int
            or not 1 <= limit <= 100000
        ):
            raise ValueError("Invalid GCS workspace inventory bound")
        self.require_versioning()
        prefix, result, keys, tokens = self.key(namespace) + "/", [], set(), set()
        try:
            pages = self.client.list_blobs(
                self.bucket,
                prefix=prefix,
                page_size=min(1000, limit + 1),
                timeout=(10, 120),
                retry=None,
            )
            for page in pages.pages:
                for blob in page:
                    if (
                        not blob.name.startswith(prefix)
                        or blob.name in keys
                        or type(blob.size) is not int
                        or not 0 <= blob.size <= MAX_BYTES
                        or not isinstance(blob.etag, str)
                        or not 1 <= len(blob.etag) <= 256
                        or getattr(blob.updated, "tzinfo", None) is None
                        or not math.isfinite(blob.updated.timestamp())
                    ):
                        raise ValueError(
                            "GCS returned an invalid or duplicate inventory record"
                        )
                    keys.add(blob.name)
                    result.append(
                        {
                            "key": _key(blob.name[len(self.prefix) + 1 :]),
                            "bytes": blob.size,
                            "etag": blob.etag,
                            "modified": blob.updated.timestamp(),
                        }
                    )
                    if len(result) > limit:
                        raise ValueError(
                            "GCS inventory exceeds its bound; no objects deleted"
                        )
                token = pages.next_page_token
                if token is not None:
                    if (
                        not isinstance(token, str)
                        or not 1 <= len(token) <= 8192
                        or token in tokens
                    ):
                        raise ValueError("GCS object listing did not advance")
                    tokens.add(token)
            return result
        except ValueError:
            raise
        except Exception:
            raise ArtifactUnavailable("GCS object inventory is unavailable") from None

    def recovery_pin(self, key, sha, size, version=None):
        from .shared_recovery_objects import validate_item

        try:
            blob = self._head(key, version)
            item = {
                "key": key,
                "sha256": sha,
                "bytes": size,
                "version_id": str(blob.generation),
                "etag": blob.etag,
            }
            validate_item(item)
            if (
                blob.size != size
                or (blob.metadata or {}).get("sha256") != sha
                or (version is not None and item["version_id"] != version)
            ):
                raise ValueError("GCS recovery object size/digest/generation differs")
            return item
        except ValueError:
            raise
        except Exception:
            raise ArtifactUnavailable(
                "Cannot pin GCS recovery object generation"
            ) from None

    def recovery_read(self, item, limit):
        return self.blob(item["key"], item["version_id"]).download_as_bytes(
            start=0,
            end=min(limit, item["bytes"] + 1),
            raw_download=True,
            if_generation_match=int(item["version_id"]),
            timeout=(10, 120),
            retry=None,
        )

    def recovery_copy_to(self, item, destination, check):
        from google.api_core.exceptions import PreconditionFailed

        source = self._head(item["key"], item["version_id"])
        callback_failed = False

        def guard():
            nonlocal callback_failed
            try:
                check()
            except BaseException:
                callback_failed = True
                raise

        try:
            destination._rewrite(source, destination.blob(item["key"]), guard)
        except PreconditionFailed:
            if callback_failed:
                raise
            destination._existing(item["key"], item["sha256"], item["bytes"], guard)
        except (ValueError, TimeoutError):
            raise
        except Exception:
            if callback_failed:
                raise
            raise ArtifactUnavailable(
                "GCS server-side recovery copy is unconfirmed"
            ) from None

    def pin_retention_version(self, item):
        try:
            blob = self._head(item["key"])
            if (
                blob.size != item["bytes"]
                or blob.etag != item["etag"]
                or blob.updated.timestamp() != item["modified"]
            ):
                raise ValueError("GCS cleanup candidate changed; preview again")
            version = str(blob.generation)
            self.blob(item["key"], version)
            return {**item, "version_id": version}
        except ValueError:
            raise
        except Exception:
            raise ArtifactUnavailable("GCS cleanup generation is unavailable") from None

    def remove_retention_version(self, item):
        from google.api_core.exceptions import NotFound

        try:
            try:
                blob = self._head(item["key"], item["version_id"])
            except NotFound:
                return
            if (
                blob.etag != item["etag"]
                or blob.size != item["bytes"]
                or blob.updated.timestamp() != item["modified"]
            ):
                raise ValueError("GCS cleanup generation changed; review again")
            blob.delete(
                if_generation_match=int(item["version_id"]),
                if_metageneration_match=blob.metageneration,
                timeout=(10, 120),
                retry=None,
            )
            try:
                self._head(item["key"], item["version_id"])
            except NotFound:
                return
            raise ArtifactUnavailable("GCS deleted generation is still readable")
        except (ValueError, ArtifactUnavailable):
            raise
        except Exception:
            raise ArtifactUnavailable(
                "GCS generation deletion is unconfirmed; reconcile before retrying"
            ) from None


def repository_from_environment(config_prefix="DMT_ARTIFACT_GCS_"):
    import os
    from .artifact_storage import ArtifactRepository

    if config_prefix not in {"DMT_ARTIFACT_GCS_", "DMT_BACKUP_GCS_"}:
        raise ValueError("Unsupported GCS configuration prefix")
    bucket = os.environ.get(config_prefix + "BUCKET", "")
    if not bucket:
        return None
    if os.environ.get("STORAGE_EMULATOR_HOST"):
        raise ValueError(
            "Managed GCS storage requires the official service, not an emulator endpoint"
        )
    try:
        from google.cloud import storage
    except ImportError:
        raise RuntimeError(
            "Install requirements/gcp-artifacts.txt for GCS storage"
        ) from None
    client = storage.Client(project=os.environ.get(config_prefix + "PROJECT") or None)
    return ArtifactRepository(
        GCSObjects(
            client,
            bucket,
            os.environ.get(config_prefix + "PREFIX", "decision-workbench"),
        )
    )
