"""Preview and resume version-specific deletion of unreferenced shared objects."""

import json
import re
import time
import uuid

from .artifact_storage import MAX_MANIFEST, reference_key
from .store import Conflict, PUBLICATION_JOB_SQL, digest, encode

FORMAT = "domain-object-cleanup-v1"
MAX_REFERENCES = 100000
MAX_MANIFEST_READ = 128 * 1024**2
MAX_PLAN = 16 * 1024**2


def require_shared(store):
    if store.artifact_store is None or store.backend != "postgresql":
        raise ValueError(
            "Object cleanup requires the configured PostgreSQL/shared-artifact workspace"
        )
    if store.artifact_store.descriptor.get("provider") not in {"s3", "azure-blob"}:
        raise ValueError("Object cleanup requires versioned S3 or Azure blob storage")


def roots(store):
    """Retain every registry model and every recorded remote-admission input."""
    with store.transaction() as db:
        models = db.execute(
            "SELECT payload FROM entities WHERE kind='model' LIMIT ?",
            (MAX_REFERENCES + 1,),
        ).fetchall()
        jobs = db.execute(
            "SELECT config FROM jobs WHERE kind='remote-import' LIMIT ?",
            (MAX_REFERENCES + 1,),
        ).fetchall()
    if len(models) + len(jobs) > MAX_REFERENCES:
        raise ValueError("Reference inventory exceeds its safety limit")
    result = {}
    for row in models:
        model = json.loads(row["payload"])
        ref = model.get("artifact_reference")
        key = reference_key(store.workspace_id, ref)
        result[key] = (ref, model)
    for row in jobs:
        config = json.loads(row["config"])
        ref = config.get("incoming_reference")
        if ref is not None:
            key = reference_key(store.workspace_id, ref)
            result.setdefault(key, (ref, None))
    return result


def build_plan(store, min_age_seconds, max_objects, live):
    if (
        type(min_age_seconds) is not int
        or not 3600 <= min_age_seconds <= 10 * 365 * 86400
    ):
        raise ValueError("Select a minimum object age between one hour and ten years")
    repository, namespace = store.artifact_store, store.workspace_id
    objects = repository.objects
    inventory = objects.retention_inventory(namespace, max_objects)
    listed = {item["key"]: item for item in inventory}
    references = roots(store)
    keep, unknown, manifests, candidates = set(references), [], dict(references), []
    cutoff = time.time() - min_age_seconds
    manifest_pattern = re.compile(
        re.escape(namespace) + r"/manifests/([a-f0-9]{64})/([a-f0-9]{64})\Z"
    )
    blob_pattern = re.compile(re.escape(namespace) + r"/blobs/[a-f0-9]{64}\Z")
    for item in inventory:
        match = manifest_pattern.fullmatch(item["key"])
        if match and (item["key"] in keep or item["modified"] > cutoff):
            if not 1 <= item["bytes"] <= MAX_MANIFEST:
                raise ValueError("Retained manifest exceeds the format limit")
            ref = {
                "namespace": namespace,
                "scope": match[1],
                "sha256": match[2],
                "bytes": item["bytes"],
            }
            manifests.setdefault(item["key"], (ref, None))
            keep.add(item["key"])
        elif not match and not blob_pattern.fullmatch(item["key"]):
            unknown.append(item["key"])
    read_bytes = 0
    for key, (ref, model) in manifests.items():
        live()
        read_bytes += ref["bytes"]
        if read_bytes > MAX_MANIFEST_READ:
            raise ValueError(
                "Retained manifest inventory exceeds the bounded read budget"
            )
        manifest = repository.inspect(namespace, ref, model)
        keep.update(
            namespace + "/blobs/" + item["sha256"]
            for item in manifest["files"].values()
        )
        if len(keep) > MAX_REFERENCES:
            raise ValueError("Protected object inventory exceeds its safety limit")
    if not keep.issubset(listed):
        raise ValueError(
            "Referenced storage objects are missing from the inventory; no cleanup is allowed"
        )
    for item in inventory:
        if (
            item["key"] not in keep
            and item["key"] not in unknown
            and item["modified"] <= cutoff
        ):
            live()
            candidates.append(objects.pin_retention_version(item))
    # Remove orphan manifests before their unreferenced blobs. No live root is selected.
    candidates.sort(key=lambda item: ("/manifests/" not in item["key"], item["key"]))
    plan = {
        "format": FORMAT,
        "workspace": namespace,
        "storage": repository.descriptor,
        "min_age_seconds": min_age_seconds,
        "max_objects": max_objects,
        "reference_digest": digest(
            sorted((k, ref) for k, (ref, _) in references.items())
        ),
        "retained_objects": len(keep),
        "unknown_objects": len(unknown),
        "items": candidates,
        "bytes": sum(i["bytes"] for i in candidates),
    }
    if len(encode(plan).encode()) > MAX_PLAN:
        raise ValueError("Cleanup plan exceeds its size limit; no objects deleted")
    return plan


def summary(plan):
    return {
        "plan_sha256": digest(plan),
        "objects": len(plan["items"]),
        "bytes": plan["bytes"],
        "retained_objects": plan["retained_objects"],
        "unknown_objects": plan["unknown_objects"],
        "min_age_seconds": plan["min_age_seconds"],
        "workspace": plan["workspace"],
    }


def status(store, operation_id=None):
    require_shared(store)
    with store.transaction() as db:
        row = db.execute(
            "SELECT * FROM workspace_object_maintenance WHERE slot=1"
        ).fetchone()
    if not row and operation_id is None:
        return {"state": "idle"}
    if operation_id is not None:
        with store.transaction() as db:
            record = db.execute(
                "SELECT payload FROM entities WHERE id=? AND kind='storage-cleanup-record' AND tenant='__operator__' AND project=?",
                (operation_id, store.workspace_id),
            ).fetchone()
            events = db.execute(
                "SELECT action,created FROM events WHERE tenant='__operator__' AND project=? AND target=? ORDER BY id",
                (store.workspace_id, operation_id),
            ).fetchall()
        if not record:
            raise ValueError("Recorded storage cleanup was not found")
        retained = json.loads(record["payload"])
        if retained["plan_sha256"] != digest(retained["plan"]):
            raise ValueError("Recorded cleanup plan integrity check failed")
        return {
            **summary(retained["plan"]),
            "operation_id": operation_id,
            "state": (
                row["state"]
                if row and row["operation_id"] == operation_id
                else (
                    "completed"
                    if any(e["action"] == "storage.cleanup.completed" for e in events)
                    else "incomplete"
                )
            ),
            "candidates": retained["plan"]["items"],
            "events": [dict(e) for e in events],
        }
    plan = json.loads(row["payload"])
    with store.transaction() as db:
        record = db.execute(
            "SELECT payload FROM entities WHERE id=? AND kind='storage-cleanup-record'",
            (row["operation_id"],),
        ).fetchone()
    if not record or json.loads(record["payload"])["plan_sha256"] != digest(plan):
        raise ValueError("Current cleanup plan differs from its immutable record")
    return {
        **summary(plan),
        "operation_id": row["operation_id"],
        "state": row["state"],
        "completed_objects": row["progress"],
        "error": row["error"],
    }


def preview(store, min_age_seconds=86400, max_objects=10000):
    require_shared(store)
    with store.artifact_guard() as live:
        current = status(store)
        if current["state"] not in {"idle", "completed"}:
            raise Conflict(
                "Resume the existing storage cleanup before preparing another plan"
            )
        plan = build_plan(store, min_age_seconds, max_objects, live)
        return {**summary(plan), "candidates": plan["items"]}


def execute(
    store, expected, min_age_seconds=86400, max_objects=10000, operation_id=None
):
    require_shared(store)
    if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError("Retain the exact reviewed cleanup plan SHA-256")
    owner = uuid.uuid4().hex
    with store.artifact_guard(exclusive=True) as live:
        current = status(store)
        if operation_id is None:
            if current["state"] not in {"idle", "completed"}:
                raise Conflict(
                    "Resume the existing storage cleanup instead of starting a new one"
                )
            with store.transaction() as db:
                if db.execute(
                    "SELECT 1 FROM jobs WHERE status IN ('queued','running','cancel_requested') AND "
                    + PUBLICATION_JOB_SQL
                    + " LIMIT 1"
                ).fetchone():
                    raise Conflict(
                        "Wait for pending training/evaluation/retrieval/admission jobs before cleanup"
                    )
            plan = build_plan(store, min_age_seconds, max_objects, live)
            if digest(plan) != expected:
                raise Conflict(
                    "Storage inventory or references changed; review a fresh cleanup plan"
                )
            operation_id = uuid.uuid4().hex
            with store.transaction() as db:
                live()
                if db.execute(
                    "SELECT 1 FROM jobs WHERE status IN ('running','cancel_requested') AND "
                    + PUBLICATION_JOB_SQL
                    + " LIMIT 1"
                ).fetchone():
                    raise Conflict(
                        "Wait for running training/evaluation/retrieval/admission jobs before cleanup"
                    )
                db.execute(
                    "INSERT INTO workspace_object_maintenance VALUES (1,?,?,?,?,?,?,?) ON CONFLICT(slot) DO UPDATE SET operation_id=excluded.operation_id,state=excluded.state,payload=excluded.payload,progress=0,owner_token=excluded.owner_token,error=NULL,updated=excluded.updated",
                    (
                        operation_id,
                        "running",
                        encode(plan),
                        0,
                        owner,
                        None,
                        time.time(),
                    ),
                )
                store.audit(
                    db,
                    "__operator__",
                    store.workspace_id,
                    "storage-operator",
                    "storage.cleanup.started",
                    operation_id,
                )
                db.execute(
                    "INSERT INTO entities VALUES (?,?,?,?,?,?)",
                    (
                        operation_id,
                        "storage-cleanup-record",
                        "__operator__",
                        store.workspace_id,
                        encode(
                            {
                                "format": "domain-storage-cleanup-record-v1",
                                "plan_sha256": expected,
                                "plan": plan,
                            }
                        ),
                        time.time(),
                    ),
                )
            progress = 0
        else:
            if (
                current.get("operation_id") != operation_id
                or current.get("plan_sha256") != expected
            ):
                raise Conflict(
                    "Cleanup identity or plan differs from the recorded operation"
                )
            if current["state"] == "completed":
                return current
            with store.transaction() as db:
                live()
                row = db.execute(
                    "SELECT * FROM workspace_object_maintenance WHERE slot=1"
                ).fetchone()
                plan, progress = json.loads(row["payload"]), row["progress"]
                if (
                    plan.get("format") != FORMAT
                    or type(progress) is not int
                    or not 0 <= progress <= len(plan["items"])
                ):
                    raise ValueError("Cleanup plan or progress is invalid")
                if (
                    plan["workspace"] != store.workspace_id
                    or plan["storage"] != store.artifact_store.descriptor
                ):
                    raise ValueError(
                        "Cleanup belongs to a different workspace or storage configuration"
                    )
                db.execute(
                    "UPDATE workspace_object_maintenance SET state='running',owner_token=?,error=NULL,updated=? WHERE slot=1",
                    (owner, time.time()),
                )
        try:
            references = roots(store)
            if (
                digest(sorted((key, ref) for key, (ref, _) in references.items()))
                != plan["reference_digest"]
            ):
                raise Conflict(
                    "Registered references changed during cleanup; investigate before resuming"
                )
            for index in range(progress, len(plan["items"])):
                live()
                store.artifact_store.objects.remove_retention_version(
                    plan["items"][index]
                )
                with store.transaction() as db:
                    live()
                    row = db.execute(
                        "SELECT owner_token FROM workspace_object_maintenance WHERE slot=1"
                    ).fetchone()
                    if row["owner_token"] != owner:
                        raise Conflict("Storage cleanup ownership changed")
                    db.execute(
                        "UPDATE workspace_object_maintenance SET progress=?,updated=? WHERE slot=1",
                        (index + 1, time.time()),
                    )
            with store.transaction() as db:
                live()
                row = db.execute(
                    "SELECT owner_token FROM workspace_object_maintenance WHERE slot=1"
                ).fetchone()
                if row["owner_token"] != owner:
                    raise Conflict("Storage cleanup ownership changed")
                db.execute(
                    "UPDATE workspace_object_maintenance SET state='completed',owner_token=NULL,error=NULL,updated=? WHERE slot=1",
                    (time.time(),),
                )
                store.audit(
                    db,
                    "__operator__",
                    store.workspace_id,
                    "storage-operator",
                    "storage.cleanup.completed",
                    operation_id,
                )
        except Exception as exc:
            with store.transaction() as db:
                db.execute(
                    "UPDATE workspace_object_maintenance SET state='pending',error=?,updated=? WHERE slot=1 AND owner_token=?",
                    (type(exc).__name__, time.time(), owner),
                )
            raise RuntimeError(
                "Storage cleanup is incomplete. Use storage-cleanup-status and resume-storage-cleanup for operation "
                + operation_id
            ) from None
        return status(store)
