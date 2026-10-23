"""Fictional GCS JSON/multipart HTTP responses consumed by the installed Google SDK."""

import base64
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
import hashlib
import io
import json
from urllib.parse import parse_qs, unquote, urlsplit

import requests
from urllib3.response import HTTPResponse


class MemoryGCS:
    def __init__(self):
        self.headers = {}
        self.is_mtls = False
        self.objects, self.requests = {}, []
        self.sequence = 1000
        self.uploads = {}
        self.versioning = True

    def close(self):
        pass

    def request(self, method, url, data=None, headers=None, **kwargs):
        parsed = urlsplit(url)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self.requests.append({"method": method, "url": url, "query": query})
        pieces = parsed.path.split("/b/", 1)[1].split("/o", 1)
        bucket = unquote(pieces[0])
        key = unquote(pieces[1].lstrip("/")) if len(pieces) > 1 else None

        def response(status, value=None, content=None, extra=None):
            result = requests.Response()
            result.status_code = status
            result.url = url
            result.request = requests.Request(method, url, headers=headers).prepare()
            result.headers = {"content-type": "application/json", **(extra or {})}
            body = content if content is not None else json.dumps(value).encode()
            result._content = body
            result.raw = HTTPResponse(
                body=io.BytesIO(body),
                headers=result.headers,
                status=status,
                preload_content=False,
            )
            return result

        def error(status):
            return response(
                status, {"error": {"code": status, "message": "Fictional GCS response"}}
            )

        def find(bucket, key, generation=None):
            versions = self.objects.get((bucket, key), {})
            return (
                versions.get(generation)
                if generation
                else next(reversed(versions.values()), None)
            )

        def save(bucket, key, content, metadata):
            import google_crc32c

            self.sequence += 1
            generation = str(self.sequence)
            value = {
                "name": key,
                "bucket": bucket,
                "generation": generation,
                "metageneration": "1",
                "size": str(len(content)),
                "etag": "etag-" + generation,
                "updated": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "metadata": metadata,
                "crc32c": base64.b64encode(
                    google_crc32c.Checksum(content).digest()
                ).decode(),
                "md5Hash": base64.b64encode(hashlib.md5(content).digest()).decode(),
            }
            self.objects.setdefault((bucket, key), {})[generation] = (value, content)
            return value

        if method == "POST" and "/rewriteTo/b/" in parsed.path:
            source_key, destination = pieces[1].lstrip("/").split("/rewriteTo/b/", 1)
            destination_bucket, destination_key = destination.split("/o/", 1)
            source = find(bucket, unquote(source_key), query.get("sourceGeneration"))
            if source is None:
                return error(404)
            if query.get("ifSourceGenerationMatch") != source[0]["generation"]:
                return error(412)
            if query.get("ifGenerationMatch") == "0" and find(
                destination_bucket, unquote(destination_key)
            ):
                return error(412)
            value = save(
                destination_bucket,
                unquote(destination_key),
                source[1],
                source[0]["metadata"],
            )
            return response(
                200,
                {
                    "done": True,
                    "totalBytesRewritten": value["size"],
                    "objectSize": value["size"],
                    "resource": value,
                },
            )
        if method == "POST" and query.get("uploadType") == "resumable":
            info = json.loads(data)
            token = str(len(self.uploads) + 1)
            self.uploads[token] = [bucket, info, bytearray()]
            return response(
                200,
                {},
                extra={
                    "location": "https://storage.googleapis.com/upload/storage/v1/b/"
                    + bucket
                    + "/o?upload_id="
                    + token
                },
            )
        if method == "PUT" and query.get("upload_id"):
            upload_bucket, info, content = self.uploads[query["upload_id"]]
            content.extend(data)
            content_range = next(
                v for k, v in headers.items() if k.lower() == "content-range"
            )
            total = content_range.split("/")[-1]
            if total != "*" and len(content) == int(total):
                if find(upload_bucket, info["name"]):
                    return error(412)
                return response(
                    200,
                    save(
                        upload_bucket,
                        info["name"],
                        bytes(content),
                        info.get("metadata", {}),
                    ),
                )
            return response(308, {}, extra={"range": f"bytes=0-{len(content)-1}"})
        if method == "POST" and query.get("uploadType") == "multipart":
            content_type = next(
                v for k, v in headers.items() if k.lower() == "content-type"
            )
            if isinstance(content_type, bytes):
                content_type = content_type.decode()
            message = BytesParser(policy=policy.default).parsebytes(
                b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + data
            )
            parts = list(message.iter_parts())
            info = json.loads(parts[0].get_payload(decode=True))
            key, content = info["name"], parts[1].get_payload(decode=True)
            if query.get("ifGenerationMatch") == "0" and find(bucket, key):
                return error(412)
            return response(200, save(bucket, key, content, info.get("metadata", {})))
        if key is None:
            return response(
                200, {"name": bucket, "versioning": {"enabled": self.versioning}}
            )
        if key == "":
            prefix = query.get("prefix", "")
            items = [
                next(reversed(v.values()))[0]
                for (b, k), v in self.objects.items()
                if b == bucket and k.startswith(prefix) and v
            ]
            return response(200, {"items": items})
        found = find(bucket, key, query.get("generation"))
        if found is None:
            return error(404)
        if (
            query.get("ifGenerationMatch")
            and query["ifGenerationMatch"] != found[0]["generation"]
        ):
            return error(412)
        if method == "DELETE":
            if (
                query.get("ifMetagenerationMatch")
                and query["ifMetagenerationMatch"] != found[0]["metageneration"]
            ):
                return error(412)
            del self.objects[(bucket, key)][found[0]["generation"]]
            return response(204, content=b"")
        if query.get("alt") == "media":
            content = found[1]
            range_value = next(
                (v for k, v in (headers or {}).items() if k.lower() == "range"), None
            )
            if range_value:
                start, _, end = range_value.removeprefix("bytes=").partition("-")
                start = int(start or 0)
                end = min(int(end) if end else len(content) - 1, len(content) - 1)
                return response(
                    206,
                    content=content[start : end + 1],
                    extra={
                        "content-range": f"bytes {start}-{end}/{len(content)}",
                        "content-type": "application/octet-stream",
                    },
                )
            return response(
                200, content=content, extra={"content-type": "application/octet-stream"}
            )
        return response(200, found[0])


def client(transport):
    from google.auth.credentials import AnonymousCredentials
    from google.cloud.storage import Client

    return Client(
        project="fictional-project", credentials=AnonymousCredentials(), _http=transport
    )
