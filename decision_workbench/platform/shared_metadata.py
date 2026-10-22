"""Bounded logical workspace archives from one PostgreSQL repeatable-read view."""

import hashlib
import json
import math
import os
from pathlib import Path
import re
import uuid

from .artifact_storage import reference_key
from .owned_staging import linked
from .store import encode

FORMAT = "domain-shared-metadata-v1"
TABLES = (
    "schema_version",
    "entities",
    "events",
    "jobs",
    "active_models",
    "release_policy_heads",
    "record_contents",
    "dataset_members",
    "dataset_versions",
    "dataset_heads",
    "dataset_appends",
    "operational_incidents",
    "incident_history",
    "worker_heartbeats",
    "workspace_metadata_identity",
    "workspace_artifact_storage",
    "workspace_object_maintenance",
)
MAX_ROW = 8 * 1024**2
MAX_BYTES = 16 * 1024**3
MAX_ROWS = 10000000
MAX_REFERENCES = 100000
NAME = re.compile(r"[a-z][a-z0-9_]{0,62}\Z")
IDENTIFIER = re.compile(r"[a-f0-9]{32}\Z")
MODEL_FIELDS = ("artifact_id", "backend", "model_sha256", "schema_sha256")


def columns(connection, schema):
    found = connection.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema=%s ORDER BY table_name LIMIT 100",
        (schema,),
    ).fetchall()
    if {row["table_name"] for row in found} != set(TABLES):
        raise ValueError(
            "Shared recovery requires the exact supported workspace table set"
        )
    rows = connection.execute(
        "SELECT table_name,column_name FROM information_schema.columns WHERE table_schema=%s ORDER BY table_name,ordinal_position LIMIT 2049",
        (schema,),
    ).fetchall()
    result = {table: [] for table in TABLES}
    if len(rows) > 2048:
        raise ValueError("Shared metadata column inventory exceeds its bound")
    for row in rows:
        if row["table_name"] not in result or not NAME.fullmatch(row["column_name"]):
            raise ValueError("Unexpected shared metadata column")
        result[row["table_name"]].append(row["column_name"])
    validate_columns(result)
    return result


def validate_columns(value):
    if not isinstance(value, dict) or set(value) != set(TABLES):
        raise ValueError("Invalid shared metadata table inventory")
    for names in value.values():
        if (
            not isinstance(names, list)
            or not 1 <= len(names) <= 128
            or any(not isinstance(n, str) or not NAME.fullmatch(n) for n in names)
            or len(set(names)) != len(names)
        ):
            raise ValueError("Invalid shared metadata columns")


def scalar(value):
    return (
        value is None
        or type(value) in (str, int, bool)
        or (type(value) is float and math.isfinite(value))
    )


def collect_reference(references, namespace, table, row):
    ref, model = None, None
    if table == "entities" and row["kind"] == "model":
        value = json.loads(row["payload"])
        ref = value.get("artifact_reference")
        model = {key: value[key] for key in MODEL_FIELDS}
        if ref is None:
            raise ValueError("Registered model lacks a shared artifact reference")
    elif table == "jobs" and row["kind"] == "remote-import":
        ref = json.loads(row["config"]).get("incoming_reference")
    if ref is not None:
        key = reference_key(namespace, ref)
        previous = references.get(key)
        if previous and (
            previous["reference"] != ref
            or previous["model"] is not None
            and model is not None
            and previous["model"] != model
        ):
            raise ValueError("Conflicting shared artifact root identities")
        references[key] = {
            "key": key,
            "reference": ref,
            "model": model if model is not None else (previous or {}).get("model"),
        }
        if len(references) > MAX_REFERENCES:
            raise ValueError("Shared metadata references exceed their bound")


def write_snapshot(store, path, check=lambda: None):
    """Write all application tables without model weights or database credentials.

    The caller holds the shared-artifact maintenance guard until referenced
    object versions have been copied. This function holds one MVCC snapshot only
    while streaming metadata; ordinary application writes can continue.
    """
    import psycopg
    from psycopg import IsolationLevel, sql
    from psycopg.rows import dict_row

    if store.backend != "postgresql" or store.artifact_store is None:
        raise ValueError("Shared recovery requires PostgreSQL and shared artifacts")
    path = Path(path)
    if linked(path) or path.exists() or any(linked(p) for p in path.parents):
        raise ValueError("Metadata output must be a new unlinked private file")
    hasher, size, total = hashlib.sha256(), 0, 0
    counts, references = {table: 0 for table in TABLES}, {}

    with psycopg.connect(
        store._postgres._dsn,
        autocommit=False,
        row_factory=dict_row,
        connect_timeout=store._postgres.timeout,
    ) as connection:
        connection.isolation_level = IsolationLevel.REPEATABLE_READ
        with connection.transaction(), path.open("xb") as output:
            connection.execute("SET TRANSACTION READ ONLY")
            connection.execute("SET LOCAL row_security=off")
            connection.execute(
                "SELECT pg_catalog.set_config('statement_timeout', %s, true)",
                (str(store._postgres.timeout * 1000),),
            )
            connection.execute(
                sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(
                    sql.Identifier(store._postgres.schema)
                )
            )
            names = columns(connection, store._postgres.schema)
            identity = connection.execute(
                "SELECT id FROM workspace_metadata_identity WHERE slot=1"
            ).fetchone()
            protocol = connection.execute(
                "SELECT max(version) AS version FROM schema_version"
            ).fetchone()["version"]
            if (
                not identity
                or identity["id"] != store.workspace_id
                or type(protocol) is not int
            ):
                raise ValueError("Shared metadata identity/protocol differs")
            header = {
                "format": FORMAT,
                "workspace": store.workspace_id,
                "protocol": protocol,
                "source_schema": store._postgres.schema,
                "tables": names,
            }

            def write(value):
                nonlocal size
                check()
                body = (encode(value) + "\n").encode()
                size += len(body)
                if len(body) > MAX_ROW or size > MAX_BYTES:
                    raise ValueError(
                        "Shared metadata archive exceeds its row/byte capacity"
                    )
                output.write(body)
                hasher.update(body)

            write(header)
            for table in TABLES:
                query = sql.SQL("SELECT {} FROM {}.{}").format(
                    sql.SQL(",").join(map(sql.Identifier, names[table])),
                    sql.Identifier(store._postgres.schema),
                    sql.Identifier(table),
                )
                with connection.cursor(
                    name="dmt_snapshot_" + uuid.uuid4().hex
                ) as cursor:
                    cursor.itersize = 8
                    cursor.execute(query)
                    for row in cursor:
                        total += 1
                        if total > MAX_ROWS:
                            raise ValueError(
                                "Shared metadata archive exceeds its row capacity"
                            )
                        values = [row[name] for name in names[table]]
                        if not all(scalar(v) for v in values):
                            raise ValueError(
                                "Shared metadata contains unsupported scalar values"
                            )
                        collect_reference(references, store.workspace_id, table, row)
                        write({"table": table, "values": values})
                        counts[table] += 1
            write({"complete": FORMAT, "counts": counts})
            output.flush()
            os.fsync(output.fileno())
    return {
        "sha256": hasher.hexdigest(),
        "bytes": size,
        "rows": total,
        "counts": counts,
        "header": header,
        "references": [references[key] for key in sorted(references)],
    }


def read_snapshot(path, metadata, on_row=None, check=lambda: None):
    """Validate a complete archive; callbacks must remain in a rollbackable transaction."""
    path = Path(path)
    if linked(path) or not path.is_file() or any(linked(p) for p in path.parents):
        raise ValueError("Shared metadata must be an ordinary private file")
    if (
        not isinstance(metadata, dict)
        or type(metadata.get("bytes")) is not int
        or not 1 <= metadata["bytes"] <= MAX_BYTES
        or path.stat().st_size != metadata["bytes"]
    ):
        raise ValueError("Shared metadata size differs from its retained manifest")
    hasher, size, total = hashlib.sha256(), 0, 0
    counts, references = {table: 0 for table in TABLES}, {}
    seen_identity, versions = None, []

    def decode(body):
        try:
            return json.loads(
                body,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError("Nonfinite JSON value")
                ),
            )
        except (UnicodeError, ValueError, RecursionError):
            raise ValueError("Shared metadata contains invalid JSON") from None

    with path.open("rb") as stream:

        def line():
            nonlocal size
            check()
            body = stream.readline(MAX_ROW + 1)
            size += len(body)
            if (
                not body
                or len(body) > MAX_ROW
                or size > MAX_BYTES
                or not body.endswith(b"\n")
            ):
                raise ValueError(
                    "Shared metadata is incomplete or exceeds its line capacity"
                )
            hasher.update(body)
            return decode(body)

        header = line()
        if (
            not isinstance(header, dict)
            or set(header)
            != {"format", "workspace", "protocol", "source_schema", "tables"}
            or header["format"] != FORMAT
            or not isinstance(header["workspace"], str)
            or not IDENTIFIER.fullmatch(header["workspace"])
            or type(header["protocol"]) is not int
            or not isinstance(header["source_schema"], str)
            or not NAME.fullmatch(header["source_schema"])
        ):
            raise ValueError("Invalid shared metadata header")
        validate_columns(header["tables"])
        if header != metadata.get("header"):
            raise ValueError(
                "Shared metadata header differs from its retained manifest"
            )
        previous = -1
        while True:
            record = line()
            if isinstance(record, dict) and record.get("complete") == FORMAT:
                if (
                    set(record) != {"complete", "counts"}
                    or record["counts"] != counts
                    or stream.read(1)
                ):
                    raise ValueError("Invalid shared metadata completion footer")
                break
            if (
                not isinstance(record, dict)
                or set(record) != {"table", "values"}
                or record.get("table") not in TABLES
            ):
                raise ValueError("Invalid shared metadata row")
            table, values = record["table"], record["values"]
            order = TABLES.index(table)
            if (
                order < previous
                or not isinstance(values, list)
                or len(values) != len(header["tables"][table])
                or not all(scalar(v) for v in values)
            ):
                raise ValueError("Shared metadata row shape/order differs")
            previous = order
            total += 1
            if total > MAX_ROWS:
                raise ValueError("Shared metadata row capacity exceeded")
            row = dict(zip(header["tables"][table], values))
            collect_reference(references, header["workspace"], table, row)
            if table == "workspace_metadata_identity":
                if row.get("slot") != 1 or seen_identity is not None:
                    raise ValueError("Shared metadata workspace identity is ambiguous")
                seen_identity = row.get("id")
            if table == "schema_version":
                if type(row.get("version")) is not int or row["version"] < 1:
                    raise ValueError("Invalid shared metadata schema version")
                versions.append(row["version"])
            if on_row is not None:
                on_row(table, header["tables"][table], values)
            counts[table] += 1
    if (
        size != metadata["bytes"]
        or hasher.hexdigest() != metadata.get("sha256")
        or total != metadata.get("rows")
        or counts != metadata.get("counts")
        or seen_identity != header["workspace"]
        or not versions
        or max(versions) != header["protocol"]
        or [references[key] for key in sorted(references)] != metadata.get("references")
    ):
        raise ValueError("Shared metadata checksum, references or counts differ")
    return header
