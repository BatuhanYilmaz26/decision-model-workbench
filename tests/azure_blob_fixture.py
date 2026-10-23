"""Fictional versioned Azure blobs for account-free application tests."""

from datetime import datetime, timezone
import io
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit


def error(kind, status):
    from azure.core import exceptions

    value = getattr(exceptions, kind)("Fictional Azure storage response")
    value.status_code = status
    return value


class MemoryAccount:
    url = "https://fictionalaccount.blob.core.windows.net"

    def __init__(self):
        self.current, self.versions, self.blocks = {}, {}, {}
        self.counter, self.versioning = 0, True
        self.calls = []
        self.lock = threading.Lock()

    def get_blob_client(self, container, name, version_id=None):
        return MemoryBlob(self, container, name, version_id)

    def get_service_properties(self):
        return {"is_versioning_enabled": self.versioning}

    def get_container_client(self, container):
        account = self

        class Container:
            def list_blobs(self, *, name_starts_with, results_per_page):
                rows = [
                    SimpleNamespace(
                        name=name, **account.versions[(bucket, name, version)][1]
                    )
                    for (bucket, name), version in sorted(account.current.items())
                    if bucket == container and name.startswith(name_starts_with)
                ]

                class Pages:
                    continuation_token = None

                    def __iter__(self):
                        for offset in range(0, len(rows), results_per_page):
                            end = offset + results_per_page
                            self.continuation_token = (
                                str(end) if end < len(rows) else None
                            )
                            yield iter(rows[offset:end])

                return SimpleNamespace(by_page=lambda: Pages())

        return Container()


class MemoryBlob:
    def __init__(self, account, container, name, version):
        self.account, self.container, self.name, self.version = (
            account,
            container,
            name,
            version,
        )
        self.url = account.url + "/" + container + "/" + name
        if version:
            self.url += "?versionid=" + version

    def stored(self):
        version = self.version or self.account.current.get((self.container, self.name))
        result = self.account.versions.get((self.container, self.name, version))
        if result is None:
            raise error("ResourceNotFoundError", 404)
        return result

    def get_blob_properties(self):
        return SimpleNamespace(**self.stored()[1])

    def download_blob(self, **kwargs):
        body, props = self.stored()
        if kwargs.get("etag") not in (None, props["etag"]):
            raise error("ResourceModifiedError", 412)
        offset = kwargs.get("offset", 0)
        length = kwargs.get("length", len(body))
        stream = io.BytesIO(body[offset : offset + length])
        return SimpleNamespace(read=stream.read, readall=lambda: stream.read())

    def stage_block(self, block, body, **kwargs):
        self.account.calls.append(("stage", self.name, kwargs))
        self.account.blocks[(self.container, self.name, block)] = bytes(body)

    def commit_block_list(self, blocks, **kwargs):
        from azure.core import MatchConditions

        assert (
            kwargs["etag"] == "*"
            and kwargs["match_condition"] == MatchConditions.IfMissing
        )
        with self.account.lock:
            key = (self.container, self.name)
            if key in self.account.current:
                raise error("ResourceExistsError", 409)
            self.account.counter += 1
            version = "v" + str(self.account.counter)
            body = b"".join(
                self.account.blocks[(self.container, self.name, b)] for b in blocks
            )
            props = dict(
                version_id=version,
                etag='"etag-' + version + '"',
                size=len(body),
                last_modified=datetime.now(timezone.utc),
                metadata=kwargs["metadata"],
            )
            self.account.versions[(*key, version)] = body, props
            self.account.current[key] = version
            self.account.calls.append(("commit", self.name, kwargs))

    def stage_block_from_url(self, block, url, **kwargs):
        parsed = urlsplit(url)
        container, name = parsed.path.lstrip("/").split("/", 1)
        version = parse_qs(parsed.query)["versionid"][0]
        body, props = self.account.versions[(container, name, version)]
        assert kwargs["source_authorization"] == "Bearer fictional-copy-token"
        assert kwargs["headers"]["x-ms-source-if-match"] == props["etag"]
        start, length = kwargs["source_offset"], kwargs["source_length"]
        self.account.blocks[(self.container, self.name, block)] = body[
            start : start + length
        ]
        self.account.calls.append(("server-copy", self.name, {"version": version}))

    def delete_blob(self, **kwargs):
        from azure.core import MatchConditions

        assert self.version, "The fixture refuses an unversioned destructive request"
        _, props = self.stored()
        if props["etag"] != kwargs["etag"]:
            raise error("ResourceModifiedError", 412)
        assert kwargs["match_condition"] == MatchConditions.IfNotModified
        del self.account.versions[(self.container, self.name, self.version)]
        if self.account.current.get((self.container, self.name)) == self.version:
            del self.account.current[(self.container, self.name)]
        self.account.calls.append(
            ("delete-version", self.name, {"version": self.version})
        )
