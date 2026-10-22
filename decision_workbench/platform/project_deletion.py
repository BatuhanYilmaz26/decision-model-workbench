"""Reviewed, transactional deletion of archived project records and unused rows."""

import json
import math
import re
import time

from . import retained_usage
from .project_lifecycle import idle, latest
from .store import Conflict, digest, encode

FORMAT = "domain-project-deletion-v1"
MAX_ROWS = 200000
MAX_METADATA = 64 * 1024**2


def owner(principal):
    if principal["role"] != "admin":
        raise PermissionError(
            "Only workspace owners can delete project data or read deletion receipts"
        )


def deleted(db, tenant, project):
    return db.execute(
        "SELECT payload FROM entities WHERE tenant=? AND project=? AND kind IN ('project-deletion','scope-deletion') LIMIT 1",
        (tenant, project),
    ).fetchone()


def _rows(db, query, params=()):
    rows = [dict(row) for row in db.execute(query, params).fetchmany(MAX_ROWS + 1)]
    if len(rows) > MAX_ROWS:
        raise ValueError("Project deletion inventory exceeds its row limit")
    return rows


def _source(db, tenant, scopes):
    data = {}
    columns = {
        "entities": ("id", "kind", "tenant", "project", "payload"),
        "jobs": (
            "id",
            "tenant",
            "project",
            "actor",
            "kind",
            "config",
            "request_hash",
            "request_key",
            "status",
            "token",
            "progress",
            "result",
            "error",
        ),
        "events": ("tenant", "project", "actor", "action", "target"),
        "operational_incidents": (
            "id",
            "tenant",
            "project",
            "kind",
            "target",
            "severity",
            "status",
            "acknowledged_by",
            "note",
        ),
    }
    total_rows, total_bytes = 0, 0
    for table, names in columns.items():
        expression = "+".join("length(coalesce(" + name + ",''))" for name in names)
        for scope in scopes:
            counts = db.execute(
                "SELECT count(*),coalesce(sum("
                + expression
                + "),0) FROM "
                + table
                + " WHERE tenant=? AND project=?",
                (tenant, scope),
            ).fetchone()
            total_rows += counts[0]
            total_bytes += counts[0] * 512 + counts[1] * 4
            if total_rows > MAX_ROWS or total_bytes > MAX_METADATA:
                raise ValueError(
                    "Project deletion metadata exceeds its bounded capacity"
                )
    for table in ("entities", "jobs", "events", "operational_incidents"):
        rows = []
        for scope in scopes:
            rows.extend(
                _rows(
                    db,
                    "SELECT * FROM "
                    + table
                    + " WHERE tenant=? AND project=? ORDER BY id",
                    (tenant, scope),
                )
            )
        if len(rows) > MAX_ROWS:
            raise ValueError("Project deletion inventory exceeds its row limit")
        data[table] = rows
    data["incident_history"] = []
    for scope in scopes:
        size = db.execute(
            "SELECT count(*),coalesce(sum(length(h.actor)+length(h.note)+length(h.action)),0) FROM incident_history h JOIN operational_incidents i ON i.id=h.incident_id WHERE i.tenant=? AND i.project=?",
            (tenant, scope),
        ).fetchone()
        total_rows += size[0]
        total_bytes += size[0] * 512 + size[1] * 4
        if total_rows > MAX_ROWS or total_bytes > MAX_METADATA:
            raise ValueError("Project deletion metadata exceeds its bounded capacity")
        data["incident_history"].extend(
            _rows(
                db,
                "SELECT h.* FROM incident_history h JOIN operational_incidents i ON i.id=h.incident_id WHERE i.tenant=? AND i.project=? ORDER BY h.id",
                (tenant, scope),
            )
        )
    if len(encode(data).encode()) > MAX_METADATA:
        raise ValueError("Project deletion metadata exceeds its byte limit")
    return data


def _usage(data):
    rows = data["entities"]
    by_id = {row["id"]: json.loads(row["payload"]) for row in rows}
    responses = {
        v.get("plan_id"): v
        for row in rows
        if row["kind"] == "generation-response"
        for v in [by_id[row["id"]]]
    }
    result = []
    for row in rows:
        value = by_id[row["id"]]
        if row["kind"] == "provider-call":
            result.append(retained_usage.make(row, "hosted", value["profile_id"]))
        elif row["kind"] == "generation-plan":
            result.append(
                retained_usage.make(
                    row,
                    "generation",
                    value["profile"]["base_url"],
                    value["estimate"]["usd"],
                    responses.get(row["id"], {}).get("reported_cost_usd"),
                )
            )
        elif row["kind"] == "cloud-run":
            p = value["profile"]
            result.append(
                retained_usage.make(
                    row, "cloud", [p["account_id"], p["region"], p["provider"]]
                )
            )
        elif row["kind"] == "schema-intake":
            result.append(retained_usage.make(row, "schema", "tenant"))
    return result


def inventory(db, tenant, project, audit_mode, min_archived_days):
    if (
        audit_mode not in ("retain", "erase")
        or type(min_archived_days) is not int
        or not 0 <= min_archived_days <= 3650
    ):
        raise ValueError(
            "Choose retain/erase audit history and an archived retention period from 0 to 3650 days"
        )
    root = db.execute(
        "SELECT * FROM entities WHERE tenant=? AND project=? AND id=? AND kind='project'",
        (tenant, project, project),
    ).fetchone()
    if not root:
        raise LookupError("Project not found")
    lifecycle = latest(db, tenant, project)
    if not lifecycle or lifecycle["state"] != "archived":
        raise Conflict("Archive the project before previewing permanent deletion")
    if db.execute(
        "SELECT 1 FROM entities WHERE tenant=? AND kind='project' AND json_extract(payload,'$.parent_project')=? LIMIT 1",
        (tenant, project),
    ).fetchone():
        raise Conflict(
            "Delete dependent successor projects first; their histories still reference this policy"
        )
    archive = db.execute(
        "SELECT created FROM entities WHERE tenant=? AND project=? AND kind='project-lifecycle' AND json_extract(payload,'$.revision')=?",
        (tenant, project, lifecycle["revision"]),
    ).fetchone()
    archived_at = archive["created"]
    if (
        type(archived_at) not in (int, float)
        or not 0 <= archived_at <= 1e12
        or not math.isfinite(archived_at)
        or archived_at > time.time() + 86400
    ):
        raise ValueError("Invalid archival time")
    payload = json.loads(root["payload"])
    intake = payload.get("schema_intake", {}).get("intake_id")
    scopes = [project]
    if intake is not None:
        if (
            not isinstance(intake, str)
            or not re.fullmatch(r"[a-f0-9]{32}", intake)
            or digest({"confirmed_schema_intake": intake, "tenant": tenant})[:32]
            != project
        ):
            raise ValueError("Schema-intake source binding is invalid")
        scopes.append(intake)
    for scope in scopes:
        idle(db, tenant, scope)
        if db.execute(
            "SELECT 1 FROM worker_heartbeats h JOIN jobs j ON j.id=h.job_id WHERE j.tenant=? AND j.project=? AND h.state!='stopped' AND h.updated>=? LIMIT 1",
            (tenant, scope, time.time() - 60),
        ).fetchone():
            raise Conflict(
                "Wait for the owning worker to finish its heartbeat cleanup before deletion"
            )
    data = _source(db, tenant, scopes)
    roots = sorted(
        {
            json.loads(row["payload"])["root"]
            for row in data["entities"]
            if row["kind"] in ("dataset", "gate-dataset")
        }
    )
    if len(roots) > 1000 or any(
        not isinstance(r, str) or not re.fullmatch(r"[a-f0-9]{32}", r) for r in roots
    ):
        raise ValueError("Project collection roots exceed the deletion limits")
    if any(
        db.execute(
            "SELECT 1 FROM entities WHERE kind IN ('dataset','gate-dataset') AND json_extract(payload,'$.root')=? AND (tenant!=? OR project!=?) LIMIT 1",
            (r, tenant, project),
        ).fetchone()
        for r in roots
    ):
        raise Conflict("Another project still references a selected collection root")
    hashes = set()
    data["collections"] = []
    collection_rows = 0
    collection_bytes = len(encode(data).encode())
    for r in roots:
        for table, names in (
            ("dataset_members", ("root", "record_id", "content_hash")),
            ("dataset_versions", ("id", "root", "record_id", "content_hash", "parent")),
            ("dataset_heads", ("root", "version")),
        ):
            expression = "+".join("length(coalesce(" + name + ",''))" for name in names)
            size = db.execute(
                "SELECT count(*),coalesce(sum("
                + expression
                + "),0) FROM "
                + table
                + " WHERE root=?",
                (r,),
            ).fetchone()
            collection_rows += size[0]
            collection_bytes += size[0] * 128 + size[1] * 4
            if collection_rows > MAX_ROWS or collection_bytes > MAX_METADATA:
                raise ValueError(
                    "Project collection inventory exceeds its bounded capacity"
                )
        size = db.execute(
            "SELECT count(*),coalesce(sum(length(a.manifest)),0) FROM dataset_appends a JOIN dataset_versions v ON v.id=a.version WHERE v.root=?",
            (r,),
        ).fetchone()
        collection_rows += size[0]
        collection_bytes += size[0] * 128 + size[1] * 4
        if collection_rows > MAX_ROWS or collection_bytes > MAX_METADATA:
            raise ValueError(
                "Project collection inventory exceeds its bounded capacity"
            )
        for table, column in (
            ("dataset_members", "root"),
            ("dataset_versions", "root"),
            ("dataset_heads", "root"),
        ):
            rows = _rows(
                db,
                "SELECT * FROM "
                + table
                + " WHERE "
                + column
                + "=? ORDER BY "
                + (
                    "ordinal"
                    if table == "dataset_members"
                    else "sequence" if table == "dataset_versions" else "root"
                ),
                (r,),
            )
            data["collections"].append([table, rows])
            hashes.update(
                row["content_hash"]
                for row in rows
                if row.get("content_hash") is not None
            )
        data["collections"].append(
            [
                "dataset_appends",
                _rows(
                    db,
                    "SELECT a.* FROM dataset_appends a JOIN dataset_versions v ON v.id=a.version WHERE v.root=? ORDER BY a.version",
                    (r,),
                ),
            ]
        )
        if len(hashes) > MAX_ROWS or len(encode(data).encode()) > MAX_METADATA:
            raise ValueError("Project deletion inventory exceeds its bounded capacity")
    if len(hashes) > MAX_ROWS or len(encode(data).encode()) > MAX_METADATA:
        raise ValueError("Project deletion inventory exceeds its bounded capacity")
    hashes = sorted(hashes)
    shared = set()
    for start in range(0, len(hashes), 200):
        chunk = hashes[start : start + 200]
        placeholders = ",".join("?" for _ in chunk)
        for table in ("dataset_members", "dataset_versions"):
            rows = db.execute(
                "SELECT DISTINCT content_hash FROM "
                + table
                + " m WHERE content_hash IN ("
                + placeholders
                + ") AND EXISTS(SELECT 1 FROM entities e WHERE e.tenant=? AND e.project!=? AND e.kind IN ('dataset','gate-dataset') AND json_extract(e.payload,'$.root')=m.root)",
                (*chunk, tenant, project),
            ).fetchall()
            shared.update(row["content_hash"] for row in rows)
    artifacts = []
    for row in data["entities"]:
        if row["kind"] == "model":
            model = json.loads(row["payload"])
            identifier = model["artifact_id"]
            if not isinstance(identifier, str) or not re.fullmatch(
                r"[a-f0-9]{32}", identifier
            ):
                raise ValueError("Invalid model artifact identity")
            artifacts.append(identifier)
    usage = _usage(data)
    plan = {
        "format": FORMAT,
        "tenant": tenant,
        "project": project,
        "intake": intake,
        "scopes": scopes,
        "audit_mode": audit_mode,
        "min_archived_days": min_archived_days,
        "eligible_at": archived_at + min_archived_days * 86400,
        "lifecycle_sha256": lifecycle["sha256"],
        "source_sha256": digest(data),
        "roots": roots,
        "content_sha256": digest(hashes),
        "shared_sha256": digest(sorted(shared)),
        "records_to_remove": len(hashes),
        "shared_records_retained": len(shared),
        "entities": len(data["entities"]),
        "jobs": len(data["jobs"]),
        "audit_events": len(data["events"]),
        "artifacts": sorted(set(artifacts)),
        "usage_sha256": digest(usage),
        "usage_records": len(usage),
    }
    return plan, usage, hashes


def preview(store, principal, project, audit_mode="retain", min_archived_days=30):
    owner(principal)
    with store.transaction() as db:
        plan, _, _ = inventory(
            db, principal["tenant"], project, audit_mode, min_archived_days
        )
    return {
        "plan": plan,
        "plan_sha256": digest(plan),
        "eligible": time.time() >= plan["eligible_at"],
    }


def execute(
    store,
    principal,
    project,
    plan_sha256,
    confirmation,
    audit_mode="retain",
    min_archived_days=30,
    authorize=None,
):
    owner(principal)
    if (
        confirmation != project
        or not isinstance(plan_sha256, str)
        or not re.fullmatch(r"[a-f0-9]{64}", plan_sha256)
    ):
        raise ValueError(
            "Confirm the exact project ID and reviewed deletion plan SHA-256"
        )
    tenant = principal["tenant"]
    with store.artifact_guard(exclusive=True) as live:
        with store.transaction() as db:
            if authorize:
                authorize()
            previous = deleted(db, tenant, project)
            if previous:
                value = checked_receipt(json.loads(previous["payload"]), project)
                if (
                    value["plan_sha256"] != plan_sha256
                    or value["audit_mode"] != audit_mode
                    or value["min_archived_days"] != min_archived_days
                ):
                    raise Conflict("Project was already deleted with a different plan")
                return value
            if store.artifact_store is not None:
                store._artifact_binding(db)
            plan, usage, hashes = inventory(
                db, tenant, project, audit_mode, min_archived_days
            )
            if digest(plan) != plan_sha256:
                raise Conflict(
                    "Deletion inputs changed. Review a fresh preview before deleting."
                )
            if time.time() < plan["eligible_at"]:
                raise Conflict(
                    "The project's selected archived retention period has not elapsed"
                )
            live()
            retained_usage.insert(db, usage)
            for r in plan["roots"]:
                db.execute(
                    "DELETE FROM dataset_appends WHERE version IN (SELECT id FROM dataset_versions WHERE root=?)",
                    (r,),
                )
                for table in ("dataset_members", "dataset_versions", "dataset_heads"):
                    db.execute("DELETE FROM " + table + " WHERE root=?", (r,))
            for start in range(0, len(hashes), 200):
                chunk = hashes[start : start + 200]
                db.execute(
                    "DELETE FROM record_contents WHERE hash IN ("
                    + ",".join("?" for _ in chunk)
                    + ") AND NOT EXISTS(SELECT 1 FROM dataset_members m WHERE m.content_hash=record_contents.hash) AND NOT EXISTS(SELECT 1 FROM dataset_versions v WHERE v.content_hash=record_contents.hash)",
                    chunk,
                )
            for scope in plan["scopes"]:
                db.execute(
                    "DELETE FROM incident_history WHERE incident_id IN (SELECT id FROM operational_incidents WHERE tenant=? AND project=?)",
                    (tenant, scope),
                )
                db.execute(
                    "UPDATE worker_heartbeats SET job_id=NULL,state='stopped' WHERE job_id IN (SELECT id FROM jobs WHERE tenant=? AND project=?)",
                    (tenant, scope),
                )
                for table in (
                    "operational_incidents",
                    "active_models",
                    "release_policy_heads",
                    "jobs",
                    "entities",
                ):
                    db.execute(
                        "DELETE FROM " + table + " WHERE tenant=? AND project=?",
                        (tenant, scope),
                    )
                if audit_mode == "erase":
                    db.execute(
                        "DELETE FROM events WHERE tenant=? AND project=?",
                        (tenant, scope),
                    )
            value = {
                "format": FORMAT,
                "project": project,
                "intake": plan["intake"],
                "plan_sha256": plan_sha256,
                "audit_mode": audit_mode,
                "min_archived_days": min_archived_days,
                "deleted_at": time.time(),
                "deleted_by": principal["actor"],
                "entities_removed": plan["entities"],
                "jobs_removed": plan["jobs"],
                "records_removed": plan["records_to_remove"],
                "shared_records_retained": plan["shared_records_retained"],
                "usage_ids": [r["id"] for r in usage],
                "usage_sha256": plan["usage_sha256"],
                "artifacts": plan["artifacts"],
            }
            value["sha256"] = digest(value)
            db.execute(
                "INSERT INTO entities VALUES (?,?,?,?,?,?)",
                (
                    project,
                    "project-deletion",
                    tenant,
                    project,
                    encode(value),
                    value["deleted_at"],
                ),
            )
            if plan["intake"]:
                alias = {"format": "domain-scope-deletion-v1", "project": project}
                db.execute(
                    "INSERT INTO entities VALUES (?,?,?,?,?,?)",
                    (
                        plan["intake"],
                        "scope-deletion",
                        tenant,
                        plan["intake"],
                        encode(alias),
                        value["deleted_at"],
                    ),
                )
            db.execute(
                "INSERT INTO events(tenant,project,actor,action,target,created) VALUES (?,?,?,?,?,?)",
                (
                    tenant,
                    project,
                    principal["actor"],
                    "delete.project",
                    project,
                    value["deleted_at"],
                ),
            )
            live()
            if authorize:
                authorize()
    return value


def receipt(store, principal, project):
    owner(principal)
    return checked_receipt(
        store.get(project, principal["tenant"], project, "project-deletion")["payload"],
        project,
    )


def audit_page(store, principal, project, after_id=0, limit=100):
    receipt(store, principal, project)
    if (
        type(after_id) is not int
        or not 0 <= after_id <= 9223372036854775807
        or type(limit) is not int
        or not 1 <= limit <= 200
    ):
        raise ValueError("Invalid deleted-project audit page")
    with store.transaction() as db:
        rows = _rows(
            db,
            "SELECT * FROM events WHERE tenant=? AND project=? AND id>? ORDER BY id LIMIT ?",
            (principal["tenant"], project, after_id, limit + 1),
        )
    return {
        "events": rows[:limit],
        "has_more": len(rows) > limit,
        "next_after_id": rows[limit - 1]["id"] if len(rows) > limit else None,
    }


def checked_receipt(value, project):
    fields = {
        "format",
        "project",
        "intake",
        "plan_sha256",
        "audit_mode",
        "min_archived_days",
        "deleted_at",
        "deleted_by",
        "entities_removed",
        "jobs_removed",
        "records_removed",
        "shared_records_retained",
        "usage_ids",
        "usage_sha256",
        "artifacts",
        "sha256",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value["format"] != FORMAT
        or value["project"] != project
        or value["audit_mode"] not in ("retain", "erase")
        or type(value["min_archived_days"]) is not int
        or not 0 <= value["min_archived_days"] <= 3650
        or type(value["deleted_at"]) not in (int, float)
        or not 0 <= value["deleted_at"] <= 1e12
        or not math.isfinite(value["deleted_at"])
        or not isinstance(value["deleted_by"], str)
        or not 1 <= len(value["deleted_by"]) <= 256
    ):
        raise ValueError("Project deletion receipt integrity check failed")
    for name in (
        "entities_removed",
        "jobs_removed",
        "records_removed",
        "shared_records_retained",
    ):
        if type(value[name]) is not int or not 0 <= value[name] <= MAX_ROWS:
            raise ValueError("Invalid deletion inventory count")
    for name in ("plan_sha256", "usage_sha256", "sha256"):
        if not isinstance(value[name], str) or not re.fullmatch(
            r"[a-f0-9]{64}", value[name]
        ):
            raise ValueError("Invalid deletion receipt hash")
    for name in ("usage_ids", "artifacts"):
        values = value[name]
        if (
            not isinstance(values, list)
            or len(values) > MAX_ROWS
            or any(
                not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{32}", v)
                for v in values
            )
            or len(set(values)) != len(values)
        ):
            raise ValueError("Invalid deletion receipt references")
    if value["intake"] is not None and (
        not isinstance(value["intake"], str)
        or not re.fullmatch(r"[a-f0-9]{32}", value["intake"])
    ):
        raise ValueError("Invalid deleted intake identity")
    if value["sha256"] != digest({k: v for k, v in value.items() if k != "sha256"}):
        raise ValueError("Project deletion receipt checksum changed")
    return value


def validate_history(db):
    retained_usage.validate_history(db)
    receipts = db.execute(
        "SELECT * FROM entities WHERE kind='project-deletion'"
    ).fetchall()
    for row in receipts:
        value = checked_receipt(json.loads(row["payload"]), row["project"])
        if row["id"] != row["project"] or row["created"] != value["deleted_at"]:
            raise ValueError("Deletion receipt scope changed")
        usage = []
        for identifier in value["usage_ids"]:
            ledger = db.execute(
                "SELECT * FROM entities WHERE id=? AND kind='retained-usage' AND tenant=? AND project=?",
                (identifier, row["tenant"], retained_usage.SCOPE),
            ).fetchone()
            if not ledger:
                raise ValueError("Retained deletion accounting is missing")
            usage.append(
                {
                    "id": identifier,
                    "tenant": row["tenant"],
                    "payload": json.loads(ledger["payload"]),
                }
            )
        if digest(usage) != value["usage_sha256"]:
            raise ValueError("Retained deletion accounting changed")
        scopes = [row["project"]] + ([value["intake"]] if value["intake"] else [])
        for scope in scopes:
            if (
                db.execute(
                    "SELECT 1 FROM entities WHERE tenant=? AND project=? AND kind NOT IN ('project-deletion','scope-deletion') LIMIT 1",
                    (row["tenant"], scope),
                ).fetchone()
                or db.execute(
                    "SELECT 1 FROM jobs WHERE tenant=? AND project=? LIMIT 1",
                    (row["tenant"], scope),
                ).fetchone()
            ):
                raise ValueError("Deleted project records were recreated")
    for row in db.execute("SELECT * FROM entities WHERE kind='scope-deletion'"):
        value = json.loads(row["payload"])
        if (
            not isinstance(value, dict)
            or set(value) != {"format", "project"}
            or value["format"] != "domain-scope-deletion-v1"
            or row["id"] != row["project"]
        ):
            raise ValueError("Invalid deleted scope alias")
        target = db.execute(
            "SELECT payload FROM entities WHERE id=? AND tenant=? AND kind='project-deletion'",
            (value["project"], row["tenant"]),
        ).fetchone()
        if not target or json.loads(target["payload"])["intake"] != row["project"]:
            raise ValueError("Deleted intake alias has no matching project receipt")
