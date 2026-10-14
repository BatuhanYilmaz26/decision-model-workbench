"""Frozen, bounded project audit windows and independently verifiable JSONL."""

import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import uuid

from .store import Conflict, digest, encode

FORMAT = "domain-audit-export-v1"
PAGE_ROWS = 200
MAX_ROWS = 100000
MAX_BYTES = 64 * 1024 * 1024
MAX_LINE = 65536
MAX_MANIFEST = 1024 * 1024
MAX_ID = 9223372036854775807
CAPTURE_SECONDS = 20
EVENT_FIELDS = {"id", "tenant", "project", "actor", "action", "target", "created"}


def line(value):
    return (encode(value) + "\n").encode("utf-8")


def event_row(value, tenant, project):
    if not isinstance(value, (dict, sqlite3.Row)):
        raise ValueError("Audit event must be a structured record")
    row = dict(value)
    if set(row) != EVENT_FIELDS or row["tenant"] != tenant or row["project"] != project:
        raise ValueError("Audit event scope or shape is invalid")
    if type(row["id"]) is not int or not 1 <= row["id"] <= MAX_ID:
        raise ValueError("Audit event identifier is invalid")
    for field in ("tenant", "project", "actor", "action", "target"):
        if not isinstance(row[field], str) or not 1 <= len(row[field]) <= 8192:
            raise ValueError("Audit event text is invalid")
    if (
        type(row["created"]) not in (int, float)
        or not math.isfinite(row["created"])
        or row["created"] < 0
    ):
        raise ValueError("Audit event timestamp is invalid")
    body = line({"event": row})
    if len(body) > MAX_LINE:
        raise ValueError("Audit event exceeds the export line limit")
    return row, body


def create(
    service,
    principal,
    project,
    note,
    request_key,
    after_id=0,
    until_id=None,
    authorize=lambda: None,
):
    service.require(principal, ("admin",))
    service.project(principal, project)
    if not isinstance(note, str) or not 1 <= len(note.strip()) <= 1000:
        raise ValueError("Record the reason for exporting this audit window")
    if not isinstance(request_key, str) or not 1 <= len(request_key) <= 128:
        raise ValueError("Use an audit request key of 1–128 characters")
    if (
        type(after_id) is not int
        or not 0 <= after_id <= MAX_ID
        or (
            until_id is not None
            and (type(until_id) is not int or not after_id <= until_id <= MAX_ID)
        )
    ):
        raise ValueError("Audit cursor bounds are invalid")
    tenant = principal["tenant"]
    key_sha = hashlib.sha256(request_key.encode()).hexdigest()
    request_sha = digest(
        {"note": note.strip(), "after_id": after_id, "until_id": until_id}
    )
    identifier = uuid.uuid4().hex
    deadline = time.monotonic() + CAPTURE_SECONDS
    with service.store.transaction() as db:
        prior = db.execute(
            "SELECT id,payload FROM entities WHERE tenant=? AND project=? AND kind='audit-export' AND json_extract(payload,'$.request_key_sha256')=?",
            (tenant, project, key_sha),
        ).fetchone()
        if prior:
            if json.loads(prior["payload"])["request_sha256"] != request_sha:
                raise Conflict(
                    "This audit request key was already used with different content"
                )
            identifier = prior["id"]
        else:
            head = db.execute(
                "SELECT coalesce(max(id),0) FROM events WHERE tenant=? AND project=?",
                (tenant, project),
            ).fetchone()[0]
            end = head if until_id is None else until_id
            if end > head or after_id > end:
                raise ValueError("Audit window exceeds the current project history")
            cursor, count, size = after_id, 0, 0
            segments = []
            content = hashlib.sha256()
            while count < MAX_ROWS and cursor < end:
                if time.monotonic() > deadline:
                    raise ValueError(
                        "Audit capture exceeded its operation window; retry with the same request key"
                    )
                rows = db.execute(
                    "SELECT * FROM events WHERE tenant=? AND project=? AND id>? AND id<=? ORDER BY id LIMIT ?",
                    (tenant, project, cursor, end, min(PAGE_ROWS, MAX_ROWS - count)),
                ).fetchall()
                if not rows:
                    break
                chunk = hashlib.sha256()
                previous, chunk_count = cursor, 0
                for row in rows:
                    if time.monotonic() > deadline:
                        raise ValueError(
                            "Audit capture exceeded its operation window; retry with the same request key"
                        )
                    event, body = event_row(row, tenant, project)
                    if size + len(body) > MAX_BYTES:
                        break
                    content.update(body)
                    chunk.update(body)
                    cursor = event["id"]
                    count += 1
                    size += len(body)
                    chunk_count += 1
                if chunk_count:
                    segments.append(
                        {
                            "after_id": previous,
                            "last_id": cursor,
                            "count": chunk_count,
                            "sha256": chunk.hexdigest(),
                        }
                    )
                if chunk_count != len(rows):
                    if count == 0:
                        raise ValueError(
                            "The first audit event exceeds the window byte budget"
                        )
                    break
            more = (
                db.execute(
                    "SELECT 1 FROM events WHERE tenant=? AND project=? AND id>? AND id<=? LIMIT 1",
                    (tenant, project, cursor, end),
                ).fetchone()
                is not None
            )
            manifest = {
                "format": FORMAT,
                "tenant": tenant,
                "project": project,
                "export_id": identifier,
                "actor": principal["actor"],
                "note": note.strip(),
                "created": time.time(),
                "after_id": after_id,
                "until_id": end,
                "last_id": cursor,
                "count": count,
                "byte_count": size,
                "has_more": more,
                "data_sha256": content.hexdigest(),
                "segments": segments,
            }
            if (
                len(line({"manifest": manifest, "manifest_sha256": digest(manifest)}))
                > MAX_MANIFEST
            ):
                raise ValueError("Audit manifest exceeds its size limit")
            if time.monotonic() > deadline:
                raise ValueError(
                    "Audit capture exceeded its operation window; retry with the same request key"
                )
            payload = {
                "manifest": manifest,
                "manifest_sha256": digest(manifest),
                "request_key_sha256": key_sha,
                "request_sha256": request_sha,
            }
            authorize()
            db.execute(
                "INSERT INTO entities VALUES (?,?,?,?,?,?)",
                (
                    identifier,
                    "audit-export",
                    tenant,
                    project,
                    encode(payload),
                    time.time(),
                ),
            )
            service.store.audit(
                db,
                tenant,
                project,
                principal["actor"],
                "create.audit-export",
                identifier,
            )
        authorize()
        if time.monotonic() > deadline:
            raise ValueError(
                "Audit capture exceeded its operation window; retry with the same request key"
            )
    return service.store.get(identifier, tenant, project, "audit-export")


def validate_manifest(manifest, expected_sha):
    fields = {
        "format",
        "tenant",
        "project",
        "export_id",
        "actor",
        "note",
        "created",
        "after_id",
        "until_id",
        "last_id",
        "count",
        "byte_count",
        "has_more",
        "data_sha256",
        "segments",
    }
    if (
        not isinstance(manifest, dict)
        or set(manifest) != fields
        or manifest["format"] != FORMAT
        or digest(manifest) != expected_sha
    ):
        raise ValueError("Audit manifest format or trusted checksum does not match")
    for name in ("tenant", "project", "export_id", "actor", "note"):
        if not isinstance(manifest[name], str) or not 1 <= len(manifest[name]) <= 8192:
            raise ValueError("Invalid audit manifest identity")
    for name, maximum in (
        ("after_id", MAX_ID),
        ("until_id", MAX_ID),
        ("last_id", MAX_ID),
        ("count", MAX_ROWS),
        ("byte_count", MAX_BYTES),
    ):
        if type(manifest[name]) is not int or not 0 <= manifest[name] <= maximum:
            raise ValueError("Invalid audit manifest bounds")
    if (
        not manifest["after_id"] <= manifest["last_id"] <= manifest["until_id"]
        or type(manifest["has_more"]) is not bool
    ):
        raise ValueError("Invalid audit manifest range")
    if manifest["has_more"] and manifest["last_id"] >= manifest["until_id"]:
        raise ValueError("Audit continuation does not advance within its frozen range")
    if (
        type(manifest["created"]) not in (int, float)
        or not math.isfinite(manifest["created"])
        or manifest["created"] < 0
    ):
        raise ValueError("Invalid audit manifest time")
    if not isinstance(manifest["data_sha256"], str) or not re.fullmatch(
        r"[a-f0-9]{64}", manifest["data_sha256"]
    ):
        raise ValueError("Invalid audit data checksum")
    segments = manifest["segments"]
    if (
        not isinstance(segments, list)
        or len(segments) > (MAX_ROWS + PAGE_ROWS - 1) // PAGE_ROWS
    ):
        raise ValueError("Invalid audit segment inventory")
    cursor, count = manifest["after_id"], 0
    for segment in segments:
        if not isinstance(segment, dict) or set(segment) != {
            "after_id",
            "last_id",
            "count",
            "sha256",
        }:
            raise ValueError("Invalid audit segment")
        if (
            any(type(segment[n]) is not int for n in ("after_id", "last_id", "count"))
            or segment["after_id"] != cursor
            or not cursor < segment["last_id"] <= manifest["until_id"]
            or not 1 <= segment["count"] <= PAGE_ROWS
            or not isinstance(segment["sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", segment["sha256"])
        ):
            raise ValueError("Audit segment bounds or checksum are invalid")
        cursor = segment["last_id"]
        count += segment["count"]
    if (
        cursor != manifest["last_id"]
        or count != manifest["count"]
        or (count == 0 and manifest["byte_count"] != 0)
    ):
        raise ValueError("Audit segment inventory is incomplete")
    return manifest


def get_manifest(store, tenant, project, identifier):
    entity = store.get(identifier, tenant, project, "audit-export")
    value = entity["payload"]
    manifest = validate_manifest(value["manifest"], value["manifest_sha256"])
    if (manifest["tenant"], manifest["project"], manifest["export_id"]) != (
        tenant,
        project,
        identifier,
    ):
        raise ValueError("Audit manifest has the wrong project identity")
    return entity, manifest


def page(store, tenant, project, identifier, index):
    entity, manifest = get_manifest(store, tenant, project, identifier)
    if type(index) is not int or not 0 <= index < len(manifest["segments"]):
        raise LookupError("Audit export page not found")
    segment = manifest["segments"][index]
    with store.transaction() as db:
        source = db.execute(
            "SELECT * FROM events WHERE tenant=? AND project=? AND id>? AND id<=? ORDER BY id LIMIT ?",
            (tenant, project, segment["after_id"], segment["last_id"], PAGE_ROWS + 1),
        ).fetchall()
    rows, body = [], hashlib.sha256()
    cursor = segment["after_id"]
    for source_row in source:
        row, encoded = event_row(source_row, tenant, project)
        if row["id"] <= cursor:
            raise ValueError("Audit page order changed")
        cursor = row["id"]
        rows.append(row)
        body.update(encoded)
    if (
        len(rows) != segment["count"]
        or cursor != segment["last_id"]
        or body.hexdigest() != segment["sha256"]
    ):
        raise ValueError(
            "Audit source changed after capture; the export page is unavailable"
        )
    return {
        "manifest_sha256": entity["payload"]["manifest_sha256"],
        "index": index,
        "rows": rows,
        "next_page": index + 1 if index + 1 < len(manifest["segments"]) else None,
    }


def stream(store, tenant, project, identifier, authorize=lambda: None):
    authorize()
    entity, manifest = get_manifest(store, tenant, project, identifier)
    yield line(
        {"manifest": manifest, "manifest_sha256": entity["payload"]["manifest_sha256"]}
    )
    for index in range(len(manifest["segments"])):
        authorize()
        rows = page(store, tenant, project, identifier, index)["rows"]
        authorize()
        yield b"".join(line({"event": row}) for row in rows)
    authorize()
    yield line(
        {
            "complete": True,
            "count": manifest["count"],
            "data_sha256": manifest["data_sha256"],
        }
    )


def verify_stream(file, manifest_sha256):
    if not isinstance(manifest_sha256, str) or not re.fullmatch(
        r"[a-f0-9]{64}", manifest_sha256
    ):
        raise ValueError("Provide the separately retained audit manifest checksum")

    def read(limit):
        raw = file.readline(limit + 1)
        if not raw or len(raw) > limit or not raw.endswith(b"\n"):
            raise ValueError("Audit export is truncated or exceeds its line limit")
        try:
            value = json.loads(raw)
            if raw != line(value):
                raise ValueError("Audit export uses a noncanonical record encoding")
        except (RecursionError, UnicodeError) as exc:
            raise ValueError("Audit export has an invalid record encoding") from exc
        return value, raw

    header, _ = read(MAX_MANIFEST)
    if (
        not isinstance(header, dict)
        or set(header) != {"manifest", "manifest_sha256"}
        or header["manifest_sha256"] != manifest_sha256
    ):
        raise ValueError("Audit export header does not match the retained receipt")
    manifest = validate_manifest(header["manifest"], manifest_sha256)
    count, size, cursor = 0, 0, manifest["after_id"]
    content = hashlib.sha256()
    for segment in manifest["segments"]:
        chunk = hashlib.sha256()
        for _ in range(segment["count"]):
            value, raw = read(MAX_LINE)
            if not isinstance(value, dict) or set(value) != {"event"}:
                raise ValueError("Audit export is missing an event")
            row, _ = event_row(value["event"], manifest["tenant"], manifest["project"])
            if not cursor < row["id"] <= segment["last_id"]:
                raise ValueError("Audit events are missing, duplicated or reordered")
            cursor = row["id"]
            content.update(raw)
            chunk.update(raw)
            count += 1
            size += len(raw)
            if size > MAX_BYTES:
                raise ValueError("Audit export exceeds its byte budget")
        if cursor != segment["last_id"] or chunk.hexdigest() != segment["sha256"]:
            raise ValueError("Audit export segment checksum does not match")
    footer, _ = read(MAX_LINE)
    expected = {
        "complete": True,
        "count": manifest["count"],
        "data_sha256": manifest["data_sha256"],
    }
    if (
        footer != expected
        or type(footer.get("complete")) is not bool
        or type(footer.get("count")) is not int
        or file.read(1)
        or count != manifest["count"]
        or size != manifest["byte_count"]
        or cursor != manifest["last_id"]
        or content.hexdigest() != manifest["data_sha256"]
    ):
        raise ValueError(
            "Audit export is incomplete or its content checksum does not match"
        )
    return {
        "verified": True,
        "count": count,
        "manifest_sha256": manifest_sha256,
        "has_more": manifest["has_more"],
        "next_after_id": manifest["last_id"],
        "until_id": manifest["until_id"],
    }


def verify_file(path, manifest_sha256):
    with Path(path).open("rb") as file:
        return verify_stream(file, manifest_sha256)


def verify_entity(store, entity):
    _, manifest = get_manifest(store, entity["tenant"], entity["project"], entity["id"])
    content, size = hashlib.sha256(), 0
    for index in range(len(manifest["segments"])):
        for row in page(
            store, entity["tenant"], entity["project"], entity["id"], index
        )["rows"]:
            raw = line({"event": row})
            content.update(raw)
            size += len(raw)
    if size != manifest["byte_count"] or content.hexdigest() != manifest["data_sha256"]:
        raise ValueError("Audit export source checksum does not match")
