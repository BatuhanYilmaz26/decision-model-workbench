"""Owner-controlled reversible archival, with immutable transition history."""

import json
import re
import time
import uuid

from .store import Conflict, digest, encode

FORMAT = "domain-project-lifecycle-v1"
MAX_HISTORY = 10000


def checked(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"format", "state", "revision", "previous_sha256", "sha256"}
        or value["format"] != FORMAT
        or value["state"] not in ("active", "archived")
        or type(value["revision"]) is not int
        or not 1 <= value["revision"] <= MAX_HISTORY
        or (
            value["previous_sha256"] is not None
            and (
                not isinstance(value["previous_sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", value["previous_sha256"])
            )
        )
        or value["sha256"] != digest({k: v for k, v in value.items() if k != "sha256"})
    ):
        raise ValueError("Project lifecycle history integrity check failed")
    return value


def latest(db, tenant, project):
    row = db.execute(
        "SELECT payload FROM entities WHERE tenant=? AND project=? AND kind='project-lifecycle' ORDER BY json_extract(payload,'$.revision') DESC LIMIT 1",
        (tenant, project),
    ).fetchone()
    return checked(json.loads(row["payload"])) if row else None


def require_active_db(db, tenant, project):
    value = latest(db, tenant, project)
    if value and value["state"] == "archived":
        raise Conflict("Project is archived. An owner must restore it before use.")


def view(store, principal, project):
    store.get(project, principal["tenant"], project, "project")
    with store.transaction() as db:
        value = latest(db, principal["tenant"], project)
    return {
        "project": project,
        **(value or {"state": "active", "revision": 0, "sha256": None}),
    }


def idle(db, tenant, project):
    if db.execute(
        "SELECT 1 FROM jobs WHERE tenant=? AND project=? AND status IN ('queued','running','cancel_requested') LIMIT 1",
        (tenant, project),
    ).fetchone():
        raise Conflict("Finish or cancel pending jobs before archiving the project")
    runs = db.execute(
        "SELECT id FROM entities WHERE tenant=? AND project=? AND kind='cloud-run' LIMIT 1001",
        (tenant, project),
    ).fetchall()
    if len(runs) > 1000:
        raise Conflict("Project cloud-run inventory exceeds the archival limit")
    for run in runs:
        row = db.execute(
            "SELECT payload FROM entities WHERE tenant=? AND project=? AND kind='cloud-event' AND json_extract(payload,'$.run_id')=? ORDER BY json_extract(payload,'$.revision') DESC LIMIT 1",
            (tenant, project, run["id"]),
        ).fetchone()
        state = json.loads(row["payload"]) if row else {}
        if state.get("phase") not in {
            "ready",
            "abandoned",
            "Completed",
            "Failed",
            "Stopped",
        }:
            raise Conflict(
                "Observe and stop unfinished or uncertain cloud runs before archiving"
            )
    if db.execute(
        "SELECT 1 FROM entities c LEFT JOIN entities r ON r.id=json_extract(c.payload,'$.result_id') AND r.kind='provider-call-result' AND r.tenant=c.tenant AND r.project=c.project LEFT JOIN entities s ON s.kind='provider-call-reconciliation' AND s.tenant=c.tenant AND s.project=c.project AND json_extract(s.payload,'$.call_id')=c.id WHERE c.tenant=? AND c.project=? AND c.kind='provider-call' AND (r.id IS NULL OR coalesce(json_extract(r.payload,'$.status'),'unknown')!='succeeded') AND s.id IS NULL LIMIT 1",
        (tenant, project),
    ).fetchone():
        raise Conflict("Reconcile unsettled hosted calls before archiving the project")
    if db.execute(
        "SELECT 1 FROM entities p WHERE p.tenant=? AND p.project=? AND p.kind='generation-plan' AND EXISTS(SELECT 1 FROM entities c WHERE c.tenant=p.tenant AND c.project=p.project AND c.kind='generation-process' AND json_extract(c.payload,'$.plan_id')=p.id) AND NOT EXISTS(SELECT 1 FROM entities r WHERE r.tenant=p.tenant AND r.project=p.project AND r.kind IN ('generation-response','generation-settlement') AND json_extract(r.payload,'$.plan_id')=p.id) LIMIT 1",
        (tenant, project),
    ).fetchone():
        raise Conflict(
            "Settle uncertain synthetic-generation attempts before archiving"
        )


def transition(
    store, principal, project, state, expected_revision, confirmation, authorize=None
):
    if principal["role"] != "admin":
        raise PermissionError("Only workspace owners can archive or restore projects")
    if (
        state not in ("active", "archived")
        or type(expected_revision) is not int
        or not 0 <= expected_revision < MAX_HISTORY
        or confirmation != project
    ):
        raise ValueError("Confirm the exact project ID and current lifecycle revision")
    tenant = principal["tenant"]
    with store.transaction() as db:
        if authorize:
            authorize()
        if not db.execute(
            "SELECT 1 FROM entities WHERE id=? AND tenant=? AND project=? AND kind='project'",
            (project, tenant, project),
        ).fetchone():
            raise LookupError("Project not found")
        previous = latest(db, tenant, project)
        revision = previous["revision"] if previous else 0
        if revision != expected_revision:
            raise Conflict("Project lifecycle changed. Refresh before trying again.")
        if (previous["state"] if previous else "active") == state:
            raise Conflict("Project already has the requested state")
        if state == "archived":
            idle(db, tenant, project)
        value = {
            "format": FORMAT,
            "state": state,
            "revision": revision + 1,
            "previous_sha256": previous["sha256"] if previous else None,
        }
        value["sha256"] = digest(value)
        identifier, now = uuid.uuid4().hex, time.time()
        db.execute(
            "INSERT INTO entities VALUES (?,?,?,?,?,?)",
            (identifier, "project-lifecycle", tenant, project, encode(value), now),
        )
        # This is the only transition allowed to write while the project is archived.
        db.execute(
            "INSERT INTO events(tenant,project,actor,action,target,created) VALUES (?,?,?,?,?,?)",
            (tenant, project, principal["actor"], "project." + state, identifier, now),
        )
        if authorize:
            authorize()
    return {"project": project, **value}


def validate_history(store):
    """Validate archived state when accepting a captured workspace for recovery."""
    with store.transaction() as db:
        scopes = db.execute(
            "SELECT DISTINCT tenant,project FROM entities WHERE kind='project-lifecycle'"
        ).fetchall()
        for scope in scopes:
            if not db.execute(
                "SELECT 1 FROM entities WHERE id=? AND tenant=? AND project=? AND kind='project'",
                (scope["project"], scope["tenant"], scope["project"]),
            ).fetchone():
                raise ValueError("Lifecycle history has no scoped project")
            rows = db.execute(
                "SELECT payload FROM entities WHERE tenant=? AND project=? AND kind='project-lifecycle' ORDER BY json_extract(payload,'$.revision') LIMIT ?",
                (scope["tenant"], scope["project"], MAX_HISTORY + 1),
            ).fetchall()
            if len(rows) > MAX_HISTORY:
                raise ValueError("Project lifecycle history exceeds its limit")
            previous, state = None, "active"
            for revision, row in enumerate(rows, 1):
                value = checked(json.loads(row["payload"]))
                if (
                    value["revision"] != revision
                    or value["previous_sha256"] != previous
                    or value["state"] == state
                ):
                    raise ValueError("Project lifecycle history chain is invalid")
                previous, state = value["sha256"], value["state"]
