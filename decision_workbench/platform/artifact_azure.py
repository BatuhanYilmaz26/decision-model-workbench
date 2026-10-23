"""Immutable Azure block blobs with bounded transfers and version-pinned recovery."""

import base64
from contextlib import closing
import hashlib
import math
import re
import uuid
from urllib.parse import urlencode, urlsplit, urlunsplit

from .artifact_storage import ArtifactUnavailable, CHUNK, MAX_BYTES


def _key(value):
    if (
        not isinstance(value, str)
        or len(value.encode()) > 1024
        or not re.fullmatch(r"[a-z0-9][a-z0-9/_-]*", value)
        or any(p in {"", ".", ".."} for p in value.split("/"))
    ):
        raise ValueError("Use a canonical lowercase artifact object key")
    return value


class _Body:
    def __init__(self, downloader):
        self.downloader = downloader
        self.closed = False

    def read(self, size=-1):
        if self.closed:
            raise ValueError("Artifact stream is closed")
        try:
            return self.downloader.read(size)
        except Exception:
            raise ArtifactUnavailable(
                "Azure artifact download was interrupted"
            ) from None

    def close(self):
        self.closed = True


class AzureBlobObjects:
    """A private, versioned container; credentials stay outside its descriptor."""

    def __init__(
        self, client, container, prefix="decision-workbench", *, credential=None
    ):
        if not isinstance(container, str) or not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9]|-(?!-)){1,61}[a-z0-9]", container
        ):
            raise ValueError("Use a canonical Azure blob container name")
        parsed = urlsplit(client.url)
        if (
            parsed.scheme != "https"
            or not re.fullmatch(
                r"[a-z0-9]{3,24}\.blob\.core\.windows\.net", parsed.netloc
            )
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Use the public Azure account HTTPS endpoint without SAS")
        self.client, self.container, self.prefix = client, container, _key(prefix)
        self.credential = credential
        self.part_size = 64 * 1024**2
        self.descriptor = {
            "provider": "azure-blob",
            "endpoint": "https://" + parsed.netloc,
            "container": container,
            "prefix": self.prefix,
        }

    def scoped(self, identifier):
        return AzureBlobObjects(
            self.client,
            self.container,
            self.prefix + "/" + _key(identifier),
            credential=self.credential,
        )

    def key(self, key):
        return _key(self.prefix + "/" + _key(key))

    def blob(self, key, version=None):
        return self.client.get_blob_client(
            self.container, self.key(key), version_id=version
        )

    def open(self, key):
        try:
            return _Body(self.blob(key).download_blob(max_concurrency=1))
        except Exception:
            raise ArtifactUnavailable("Azure artifact storage is unavailable") from None

    def available(self, key, size):
        try:
            if self.blob(key).get_blob_properties().size != size:
                raise ArtifactUnavailable("Azure recovery object size differs")
        except ArtifactUnavailable:
            raise
        except Exception:
            raise ArtifactUnavailable("Azure recovery object is unavailable") from None

    def _existing(self, key, sha, size, check):
        actual, received = hashlib.sha256(), 0
        with closing(self.open(key)) as stream:
            while chunk := stream.read(min(CHUNK, size - received + 1)):
                check()
                received += len(chunk)
                actual.update(chunk)
                if received > size:
                    raise ValueError("Existing immutable Azure object differs")
        if received != size or actual.hexdigest() != sha:
            raise ValueError("Existing immutable Azure object differs")

    def put_file(self, key, source, sha, size, check=lambda: None):
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceExistsError, ResourceModifiedError

        if (
            not isinstance(sha, str)
            or not re.fullmatch(r"[a-f0-9]{64}", sha)
            or type(size) is not int
            or not 0 <= size <= MAX_BYTES
        ):
            raise ValueError("Invalid Azure artifact size or digest")
        destination = self.blob(key)
        upload = uuid.uuid4().hex
        actual, received, blocks = hashlib.sha256(), 0, []
        callback_failed = False

        def guard():
            nonlocal callback_failed
            try:
                check()
            except BaseException:
                callback_failed = True
                raise

        try:
            with source.open("rb") as stream:
                while chunk := stream.read(min(self.part_size, size - received + 1)):
                    guard()
                    received += len(chunk)
                    actual.update(chunk)
                    if received > size:
                        raise ValueError("Azure artifact changed during publication")
                    block = base64.b64encode(
                        (upload + f"{len(blocks):08d}").encode()
                    ).decode()
                    destination.stage_block(
                        block, chunk, length=len(chunk), validate_content="md5"
                    )
                    blocks.append(block)
            if received != size or actual.hexdigest() != sha:
                raise ValueError("Azure artifact changed during publication")
            guard()
            destination.commit_block_list(
                blocks,
                metadata={"sha256": sha},
                etag="*",
                match_condition=MatchConditions.IfMissing,
            )
            guard()
        except (ResourceExistsError, ResourceModifiedError) as exc:
            if callback_failed:
                raise
            if getattr(exc, "status_code", None) not in {409, 412}:
                raise ArtifactUnavailable("Azure artifact publication failed") from None
            self._existing(key, sha, size, check)
        except (ValueError, TimeoutError):
            raise
        except Exception:
            if callback_failed:
                raise
            raise ArtifactUnavailable(
                "Azure artifact publication is unconfirmed; inspect and retry the approved operation"
            ) from None

    def require_versioning(self):
        try:
            if (
                self.client.get_service_properties().get("is_versioning_enabled")
                is not True
            ):
                raise ValueError(
                    "Azure retention/recovery requires enabled blob versioning"
                )
        except ValueError:
            raise
        except Exception:
            raise ArtifactUnavailable("Cannot inspect Azure blob versioning") from None

    def retention_inventory(self, namespace, limit):
        if (
            not isinstance(namespace, str)
            or not re.fullmatch(r"[a-f0-9]{32}", namespace)
            or type(limit) is not int
            or not 1 <= limit <= 100000
        ):
            raise ValueError("Invalid Azure workspace inventory bound")
        self.require_versioning()
        prefix = self.key(namespace) + "/"
        result, keys, tokens = [], set(), set()
        try:
            pages = (
                self.client.get_container_client(self.container)
                .list_blobs(
                    name_starts_with=prefix, results_per_page=min(1000, limit + 1)
                )
                .by_page()
            )
            for page in pages:
                for item in page:
                    key, size, etag, stamp = (
                        item.name,
                        item.size,
                        item.etag,
                        item.last_modified,
                    )
                    if (
                        not key.startswith(prefix)
                        or key in keys
                        or type(size) is not int
                        or not 0 <= size <= MAX_BYTES
                        or not isinstance(etag, str)
                        or not 1 <= len(etag) <= 256
                        or etag == "*"
                        or getattr(stamp, "tzinfo", None) is None
                        or not math.isfinite(stamp.timestamp())
                    ):
                        raise ValueError(
                            "Azure returned an invalid or duplicate inventory record"
                        )
                    relative = _key(key[len(self.prefix) + 1 :])
                    keys.add(key)
                    result.append(
                        {
                            "key": relative,
                            "bytes": size,
                            "etag": etag,
                            "modified": stamp.timestamp(),
                        }
                    )
                    if len(result) > limit:
                        raise ValueError(
                            "Azure inventory exceeds its bound; no objects deleted"
                        )
                token = pages.continuation_token
                if token is not None:
                    if (
                        not isinstance(token, str)
                        or not 1 <= len(token) <= 4096
                        or token in tokens
                    ):
                        raise ValueError("Azure object listing did not advance")
                    tokens.add(token)
            return result
        except (ValueError, ArtifactUnavailable):
            raise
        except Exception:
            raise ArtifactUnavailable("Azure object inventory is unavailable") from None

    def recovery_pin(self, key, sha, size, version=None):
        from .shared_recovery_objects import validate_item

        try:
            value = self.blob(key, version).get_blob_properties()
            item = {
                "key": key,
                "sha256": sha,
                "bytes": size,
                "version_id": value.version_id,
                "etag": value.etag,
            }
            validate_item(item)
            if (
                value.size != size
                or value.metadata.get("sha256") != sha
                or (version and value.version_id != version)
            ):
                raise ValueError("Azure recovery version identity differs")
            return item
        except ValueError:
            raise
        except Exception:
            raise ArtifactUnavailable(
                "Azure recovery object version is unavailable"
            ) from None

    def recovery_read(self, item, limit):
        from azure.core import MatchConditions

        if not item["bytes"]:
            return b""
        return (
            self.blob(item["key"], item["version_id"])
            .download_blob(
                offset=0,
                length=min(item["bytes"], limit + 1),
                etag=item["etag"],
                match_condition=MatchConditions.IfNotModified,
                max_concurrency=1,
            )
            .readall()
        )

    def recovery_copy_to(self, item, destination, check):
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceExistsError, ResourceModifiedError

        if self.credential is None:
            raise ValueError(
                "Azure recovery needs a token credential for private source copies"
            )
        source = self.blob(item["key"], item["version_id"])
        # BlobClient.url omits its version_id; pin the URL sent to Azure explicitly.
        source_url = urlunsplit(
            urlsplit(source.url)._replace(
                query=urlencode({"versionid": item["version_id"]})
            )
        )
        target = destination.blob(item["key"])
        upload, blocks = uuid.uuid4().hex, []
        callback_failed = False

        def guard():
            nonlocal callback_failed
            try:
                check()
            except BaseException:
                callback_failed = True
                raise

        try:
            for offset in range(0, item["bytes"], self.part_size):
                guard()
                token = self.credential.get_token("https://storage.azure.com/.default")
                block = base64.b64encode(
                    (upload + f"{len(blocks):08d}").encode()
                ).decode()
                target.stage_block_from_url(
                    block,
                    source_url,
                    source_offset=offset,
                    source_length=min(self.part_size, item["bytes"] - offset),
                    source_authorization="Bearer " + token.token,
                    headers={"x-ms-source-if-match": item["etag"]},
                )
                blocks.append(block)
            guard()
            target.commit_block_list(
                blocks,
                metadata={"sha256": item["sha256"]},
                etag="*",
                match_condition=MatchConditions.IfMissing,
            )
        except (ResourceExistsError, ResourceModifiedError) as exc:
            if callback_failed:
                raise
            if getattr(exc, "status_code", None) not in {409, 412}:
                raise ArtifactUnavailable("Azure recovery copy failed") from None
            # A previous attempt may already have completed. Never replace its bytes.
            destination._existing(item["key"], item["sha256"], item["bytes"], check)
        except TimeoutError:
            raise
        except Exception:
            if callback_failed:
                raise
            raise ArtifactUnavailable(
                "Azure server-side recovery copy is unconfirmed"
            ) from None
        check()

    def pin_retention_version(self, item):
        try:
            value = self.blob(item["key"]).get_blob_properties()
            if (
                value.size != item["bytes"]
                or value.etag != item["etag"]
                or value.last_modified.timestamp() != item["modified"]
                or not isinstance(value.version_id, str)
                or not 1 <= len(value.version_id) <= 1024
            ):
                raise ValueError("Azure cleanup candidate changed; preview again")
            return {**item, "version_id": value.version_id}
        except ValueError:
            raise
        except Exception:
            raise ArtifactUnavailable("Azure cleanup version is unavailable") from None

    def remove_retention_version(self, item):
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceNotFoundError

        target = self.blob(item["key"], item["version_id"])
        try:
            try:
                target.get_blob_properties()
            except ResourceNotFoundError:
                return
            target.delete_blob(
                etag=item["etag"], match_condition=MatchConditions.IfNotModified
            )
            try:
                target.get_blob_properties()
            except ResourceNotFoundError:
                return
            raise ArtifactUnavailable(
                "Azure deleted version is still readable; keep cleanup pending"
            )
        except ArtifactUnavailable:
            raise
        except Exception:
            raise ArtifactUnavailable(
                "Azure version deletion is unconfirmed; reconcile before retrying"
            ) from None


def repository_from_environment(config_prefix="DMT_ARTIFACT_AZURE_"):
    """Resolve operator credentials outside model records and browser processes."""
    import os
    from .artifact_storage import ArtifactRepository

    if config_prefix not in {"DMT_ARTIFACT_AZURE_", "DMT_BACKUP_AZURE_"}:
        raise ValueError("Unsupported Azure object storage configuration prefix")
    endpoint = os.environ.get(config_prefix + "ACCOUNT_URL", "")
    container = os.environ.get(config_prefix + "CONTAINER", "")
    if not endpoint and not container:
        return None
    if not endpoint or not container:
        raise ValueError("Configure both the Azure artifact account URL and container")
    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient
    except ImportError:
        raise RuntimeError(
            "Install requirements/azure-artifacts.txt for Azure blob storage"
        ) from None
    credential = DefaultAzureCredential(
        managed_identity_client_id=os.environ.get(config_prefix + "CLIENT_ID") or None,
        exclude_interactive_browser_credential=True,
    )
    client = BlobServiceClient(
        endpoint,
        credential=credential,
        retry_total=0,
        connection_timeout=10,
        read_timeout=120,
        max_single_get_size=8 * 1024**2,
        max_chunk_get_size=CHUNK,
    )
    return ArtifactRepository(
        AzureBlobObjects(
            client,
            container,
            os.environ.get(config_prefix + "PREFIX", "decision-workbench"),
            credential=credential,
        )
    )
