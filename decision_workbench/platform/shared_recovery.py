"""Coordinated logical PostgreSQL and immutable object-store recovery bundles."""

import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
import uuid

from . import shared_metadata
from .artifact_s3 import S3Objects
from .artifact_azure import AzureBlobObjects
from .artifact_storage import (
    ArtifactRepository,
    DirectoryObjects,
    MAX_MANIFEST,
    manifest_content,
    reference_key,
)
from .recovery import _new_target, _publish
from .shared_recovery_objects import RecoveryObjects, independent
from .store import Store, encode

FORMAT = "domain-shared-backup-v1"
RESTORE_FORMAT = "domain-shared-restore-v1"
MANIFEST = "shared-backup.json"
MAX_BUNDLE_MANIFEST = 64 * 1024**2
MAX_OBJECTS = 250000


def deadline(seconds):
    if (
        type(seconds) not in (int, float)
        or not math.isfinite(seconds)
        or not 0 < seconds <= 86400
    ):
        raise ValueError("Recovery timeout must be positive and at most 24 hours")
    until = time.monotonic() + seconds

    def check():
        if time.monotonic() > until:
            raise TimeoutError("Shared recovery exceeded its operator deadline")

    return check


def scoped_repository(repository, identifier):
    objects = repository.objects
    if isinstance(objects, AzureBlobObjects):
        return ArtifactRepository(objects.scoped(identifier))
    if isinstance(objects, DirectoryObjects):
        return ArtifactRepository(DirectoryObjects(objects.root / identifier))
    if isinstance(objects, S3Objects):
        return ArtifactRepository(
            S3Objects(
                objects.client,
                objects.bucket,
                objects.prefix + "/" + identifier,
                objects.part_size,
            )
        )
    raise ValueError("Unsupported shared recovery storage")


def object_inventory(metadata, transport, pinned=None, check=lambda: None):
    """Derive the exact required object set from retained database references."""
    namespace = metadata["header"]["workspace"]
    result = {}

    def item(key, sha, size):
        check()
        existing = result.get(key)
        if existing:
            if (existing["sha256"], existing["bytes"]) != (sha, size):
                raise ValueError("Conflicting recovery blob identities")
            return existing
        if len(result) >= MAX_OBJECTS:
            raise ValueError("Recovery object inventory exceeds its bound")
        if pinned is None:
            value = transport.pin(key, sha, size)
        else:
            value = pinned.get(key)
            if not isinstance(value, dict) or (
                value.get("key"),
                value.get("sha256"),
                value.get("bytes"),
            ) != (key, sha, size):
                raise ValueError(
                    "Backup object inventory differs from metadata references"
                )
            transport.verify(value)
        result[key] = value
        return value

    for root in metadata["references"]:
        ref = root["reference"]
        key = reference_key(namespace, ref)
        if root["key"] != key:
            raise ValueError("Recovery root key differs")
        manifest = item(key, ref["sha256"], ref["bytes"])
        _, files, _ = manifest_content(
            transport.read(manifest, MAX_MANIFEST), namespace, ref, root["model"]
        )
        for value in files.values():
            item(
                namespace + "/blobs/" + value["sha256"], value["sha256"], value["bytes"]
            )
    if pinned is not None and set(result) != set(pinned):
        raise ValueError("Backup contains unreferenced or missing recovery objects")
    return [result[key] for key in sorted(result)]


def backup_shared(store, destination, vault, timeout=3600):
    """Capture one database view and independent copies of every referenced object."""
    check = deadline(timeout)
    if store.backend != "postgresql" or store.artifact_store is None:
        raise ValueError("Shared backup requires PostgreSQL and shared artifacts")
    independent(store.artifact_store.objects, vault.objects)
    _, destination = _new_target(store.folder, destination)
    snapshot = uuid.uuid4().hex
    retained = scoped_repository(vault, snapshot)
    source_objects, backup_objects = RecoveryObjects(
        store.artifact_store.objects
    ), RecoveryObjects(retained.objects)
    with tempfile.TemporaryDirectory(
        prefix=".dmt-shared-backup-", dir=destination.parent
    ) as temporary:
        staging = Path(temporary)
        with store.artifact_guard(exclusive=True) as live:

            def check_source():
                check()
                live()

            # Refuse in-progress retention or a mismatched configured source store.
            with store.transaction() as db:
                store._artifact_binding(db)
            metadata = shared_metadata.write_snapshot(
                store, staging / "shared-metadata.jsonl", check_source
            )
            selected = object_inventory(metadata, source_objects, check=check_source)
            copies = [
                source_objects.copy_to(item, backup_objects, check_source)
                for item in selected
            ]
            manifest = {
                "format": FORMAT,
                "snapshot": snapshot,
                "created": time.time(),
                "source_storage": store.artifact_store.descriptor,
                "vault_storage": retained.descriptor,
                "metadata": metadata,
                "objects": copies,
            }
            body = (encode(manifest) + "\n").encode()
            if len(body) > MAX_BUNDLE_MANIFEST:
                raise ValueError("Shared backup manifest exceeds its capacity")
            with (staging / MANIFEST).open("xb") as output:
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
            check_source()
            _publish(staging, destination, MANIFEST)
    return {
        "destination": str(destination),
        "manifest_sha256": hashlib.sha256(body).hexdigest(),
        "snapshot": snapshot,
        "vault_storage": retained.descriptor,
        "objects": len(copies),
        "metadata_rows": metadata["rows"],
    }


def verify_shared(backup, manifest_sha256, vault, *, timeout=3600):
    """Verify the retained checksum, logical archive and exact object versions."""
    check = deadline(timeout)
    source = Path(backup).absolute()
    from .owned_staging import linked

    if any(linked(p) for p in (source, *source.parents)) or not source.is_dir():
        raise ValueError("Shared backup must be an ordinary private directory")
    if set(p.name for p in source.iterdir()) != {MANIFEST, "shared-metadata.jsonl"}:
        raise ValueError("Unexpected shared backup directory contents")
    path = source / MANIFEST
    if linked(path) or not path.is_file() or path.stat().st_size > MAX_BUNDLE_MANIFEST:
        raise ValueError("Invalid shared backup manifest file")
    body = path.read_bytes()
    if (
        not isinstance(manifest_sha256, str)
        or hashlib.sha256(body).hexdigest() != manifest_sha256
    ):
        raise ValueError("Shared backup differs from the separately retained checksum")
    try:
        manifest = json.loads(body)
        if (
            set(manifest)
            != {
                "format",
                "snapshot",
                "created",
                "source_storage",
                "vault_storage",
                "metadata",
                "objects",
            }
            or manifest["format"] != FORMAT
            or not shared_metadata.IDENTIFIER.fullmatch(manifest["snapshot"])
        ):
            raise ValueError("Invalid shared backup format")
        retained = scoped_repository(vault, manifest["snapshot"])
        if retained.descriptor != manifest["vault_storage"]:
            raise ValueError("Configured recovery vault differs from this backup")
        shared_metadata.read_snapshot(
            source / "shared-metadata.jsonl", manifest["metadata"], check=check
        )
        values = manifest["objects"]
        if not isinstance(values, list) or len(values) > MAX_OBJECTS:
            raise ValueError("Invalid recovery object count")
        pinned = {item["key"]: item for item in values}
        if len(pinned) != len(values):
            raise ValueError("Duplicate recovery objects")
        object_inventory(
            manifest["metadata"], RecoveryObjects(retained.objects), pinned, check
        )
    except (KeyError, TypeError, RecursionError, AttributeError):
        raise ValueError("Malformed shared backup inventory") from None
    return manifest


def import_metadata(store, path, metadata, manifest_sha256, check):
    """Import only into pristine tables; all callbacks remain rollbackable."""
    from psycopg import sql

    recovered = []
    with store.transaction() as db:
        raw = db.raw
        names = shared_metadata.columns(raw, store._postgres.schema)
        if names != metadata["header"]["tables"]:
            raise ValueError("Restore requires the exact compatible application schema")
        current = db.execute("SELECT max(version) FROM schema_version").fetchone()[0]
        if (
            current != metadata["header"]["protocol"]
            or store.workspace_id == metadata["header"]["workspace"]
        ):
            raise ValueError("Restore requires a fresh compatible workspace identity")
        bootstrap = {
            "schema_version",
            "workspace_metadata_identity",
            "workspace_artifact_storage",
        }
        for table in shared_metadata.TABLES:
            if (
                table not in bootstrap
                and raw.execute(
                    sql.SQL("SELECT 1 FROM {} LIMIT 1").format(sql.Identifier(table))
                ).fetchone()
            ):
                raise ValueError("Restore refuses nonempty target workspace tables")
        # Exclusive table locks also exclude accidental ordinary target writers.
        raw.execute(
            sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(
                sql.SQL(",").join(map(sql.Identifier, shared_metadata.TABLES))
            )
        )
        # Recheck under the locks; no gap between emptiness and replacement.
        for table in shared_metadata.TABLES:
            if (
                table not in bootstrap
                and raw.execute(
                    sql.SQL("SELECT 1 FROM {} LIMIT 1").format(sql.Identifier(table))
                ).fetchone()
            ):
                raise ValueError("Restore target changed during preflight")
        raw.execute(
            sql.SQL("TRUNCATE {} RESTART IDENTITY").format(
                sql.SQL(",").join(map(sql.Identifier, shared_metadata.TABLES))
            )
        )

        def row(table, columns, values):
            check()
            raw.execute(
                sql.SQL(
                    "INSERT INTO {} ({}) OVERRIDING SYSTEM VALUE VALUES ({})"
                ).format(
                    sql.Identifier(table),
                    sql.SQL(",").join(map(sql.Identifier, columns)),
                    sql.SQL(",").join(sql.Placeholder() for _ in columns),
                ),
                values,
            )

        shared_metadata.read_snapshot(path, metadata, row, check)
        # Pending object maintenance cannot be resumed against a different store.
        pending = db.execute(
            "SELECT state FROM workspace_object_maintenance WHERE slot=1"
        ).fetchone()
        if pending and pending["state"] != "completed":
            raise ValueError("Backup contains unfinished object maintenance")
        db.execute(
            "UPDATE workspace_artifact_storage SET descriptor=? WHERE slot=1",
            (encode(store.artifact_store.descriptor),),
        )
        if not db.execute(
            "SELECT 1 FROM workspace_artifact_storage WHERE slot=1"
        ).fetchone():
            raise ValueError("Backup has no shared-storage binding")
        for table in ("events", "incident_history"):
            maximum = raw.execute(
                sql.SQL("SELECT max(id) FROM {}").format(sql.Identifier(table))
            ).fetchone()[0]
            raw.execute(
                "SELECT pg_catalog.setval(pg_catalog.pg_get_serial_sequence(%s,'id'),%s,%s)",
                (
                    store._postgres.schema + "." + table,
                    maximum or 1,
                    maximum is not None,
                ),
            )
        cleared = db.execute("SELECT count(*) FROM worker_heartbeats").fetchone()[0]
        db.execute("DELETE FROM worker_heartbeats")
        for item in db.execute(
            "SELECT id,status FROM jobs WHERE status IN ('queued','running','cancel_requested')"
        ).fetchall():
            status = (
                "cancelled" if item["status"] == "cancel_requested" else "interrupted"
            )
            db.execute(
                "UPDATE jobs SET status=?,token=NULL,lease=NULL,progress=?,error=?,updated=? WHERE id=?",
                (
                    status,
                    "Recovered; no automatic replay",
                    "Inspect upstream work before explicit resubmission",
                    time.time(),
                    item["id"],
                ),
            )
            recovered.append(
                {"id": item["id"], "previous_status": item["status"], "status": status}
            )
        for project in db.execute(
            "SELECT tenant,project FROM entities WHERE kind='project'"
        ).fetchall():
            db.execute(
                "INSERT INTO events(tenant,project,actor,action,target,created) VALUES (?,?,?,?,?,?)",
                (
                    project["tenant"],
                    project["project"],
                    "operator",
                    "restore.shared-workspace",
                    manifest_sha256,
                    time.time(),
                ),
            )
        check()
    return {"interrupted_jobs": recovered, "cleared_worker_heartbeats": cleared}


def restore_shared(
    backup,
    destination,
    manifest_sha256,
    vault,
    target,
    *,
    database_url,
    database_schema,
    timeout=3600
):
    """Restore into a fresh private home, PostgreSQL schema and object scope."""
    check = deadline(timeout)
    source, destination = _new_target(backup, destination)
    manifest = verify_shared(source, manifest_sha256, vault, timeout=timeout)
    retained = scoped_repository(vault, manifest["snapshot"])
    independent(retained.objects, target.objects)
    recovered_repo = scoped_repository(target, uuid.uuid4().hex)
    incoming, outgoing = RecoveryObjects(retained.objects), RecoveryObjects(
        recovered_repo.objects
    )
    # Validate pristine target metadata before any object-copy request.
    with tempfile.TemporaryDirectory(
        prefix=".dmt-shared-restore-", dir=destination.parent
    ) as temporary:
        staging = Path(temporary)
        store = Store(
            staging,
            database_url=database_url,
            database_schema=database_schema,
            artifact_store=recovered_repo,
        )
        with store.artifact_guard(exclusive=True) as live:

            def check_target():
                check()
                live()

            with store.transaction() as db:
                for table in shared_metadata.TABLES:
                    if (
                        table
                        not in {
                            "schema_version",
                            "workspace_metadata_identity",
                            "workspace_artifact_storage",
                        }
                        and db.execute("SELECT 1 FROM " + table + " LIMIT 1").fetchone()
                    ):
                        raise ValueError("Restore refuses an existing workspace")
            for item in manifest["objects"]:
                incoming.copy_to(item, outgoing, check_target)
            result = import_metadata(
                store,
                source / "shared-metadata.jsonl",
                manifest["metadata"],
                manifest_sha256,
                check_target,
            )
            # Only this newly created, private staging-home binding is replaced.
            marker = staging / ".dmt-postgres.json"
            marker.unlink()
            store._postgres.bind_folder(staging)
            receipt = {
                "format": RESTORE_FORMAT,
                "manifest_sha256": manifest_sha256,
                "restored": time.time(),
                "workspace": manifest["metadata"]["header"]["workspace"],
                "database_schema": database_schema,
                "artifact_storage": recovered_repo.descriptor,
                **result,
            }
            (staging / "shared-restore.json").write_text(
                encode(receipt), encoding="utf-8"
            )
            check_target()
            _publish(staging, destination, "shared-restore.json")
    return {"destination": str(destination), **receipt}
