"""Owner views and explicit reconciliation of synthetic-generation attempts."""

import json
import time

from .generation_plans import ACTIVE, load, record_id, sealed
from .generation_worker import verify_history
from .paging import validate_page
from .store import Conflict, digest, encode


def list_requests(service, principal, project, limit=20, offset=0):
    service.require(principal, ("admin",))
    service.project(principal, project)
    validate_page(limit, offset, 20)
    with service.store.transaction() as db:
        rows = db.execute(
            "SELECT p.id,p.created,json_extract(p.payload,'$.policy.name') AS name,json_extract(p.payload,'$.selector.count') AS candidates,j.status "
            "FROM entities p JOIN jobs j ON j.tenant=p.tenant AND j.project=p.project AND j.kind='generate' AND j.request_key=json_extract(p.payload,'$.request_key') "
            "WHERE p.tenant=? AND p.project=? AND p.kind='generation-plan' ORDER BY p.created DESC,p.id DESC LIMIT ? OFFSET ?",
            (principal["tenant"], project, limit, offset),
        ).fetchall()
    return [dict(row) for row in rows]


def status_digest(entity, job, history):
    return digest(
        {
            "plan_sha256": entity["payload"]["sha256"],
            "job_id": job["id"],
            "job_status": job["status"],
            **{
                kind + "_sha256": None if value is None else value["payload"]["sha256"]
                for kind, value in history.items()
            },
        }
    )


def view(service, principal, project, identifier):
    service.require(principal, ("admin",))
    service.project(principal, project)
    entity = service.store.get(
        identifier, principal["tenant"], project, "generation-plan"
    )
    entity, job, _, _ = load(
        service.store,
        principal["tenant"],
        project,
        {"id": identifier, "sha256": entity["payload"]["sha256"]},
    )
    history = verify_history(service.store, principal["tenant"], project, entity)
    plan = entity["payload"]
    if history["response"]:
        state = "response_received"
    elif history["settlement"]:
        state = "owner_attested_complete"
    elif job["status"] not in ACTIVE and history["process"] is None:
        state = "not_dispatched"
    else:
        state = "pending" if job["status"] in ACTIVE else "unknown"
    response = history["response"]
    result = history["result"]
    return {
        "id": identifier,
        "created": entity["created"],
        "policy_name": plan["policy"]["name"],
        "selector": plan["selector"],
        "teacher": plan["profile"],
        "estimate": plan["estimate"],
        "attempt": plan["attempt"],
        "approved_by": plan["approved_by"],
        "request_sha256": plan["request_sha256"],
        "request_body": plan["request_body"],
        "policy_sha256": plan["policy_sha256"],
        "seed_ids": plan["seed_ids"],
        "preview_sha256": plan["preview_sha256"],
        "state": state,
        "status_sha256": status_digest(entity, job, history),
        "job": {
            k: job[k]
            for k in (
                "id",
                "status",
                "progress",
                "error",
                "result",
                "created",
                "updated",
            )
        },
        "process": (
            None if history["process"] is None else history["process"]["payload"]
        ),
        "response": (
            None
            if response is None
            else {k: v for k, v in response["payload"].items() if k != "raw_base64"}
        ),
        "dataset": (
            None
            if result is None
            else {
                "id": result["payload"]["dataset_id"],
                "rows": result["payload"]["rows"],
            }
        ),
        "settlement": (
            None if history["settlement"] is None else history["settlement"]["payload"]
        ),
    }


def settle(
    service,
    principal,
    project,
    identifier,
    expected_status_sha256,
    local_exit_confirmed,
    upstream_complete_confirmed,
    evidence_ref,
    note,
    authorize=None,
):
    service.require(principal, ("admin",))
    service.project(principal, project)
    if local_exit_confirmed is not True or upstream_complete_confirmed is not True:
        raise ValueError(
            "Confirm local transport exit and upstream completion after checking the teacher"
        )
    if (
        not isinstance(expected_status_sha256, str)
        or len(expected_status_sha256) != 64
        or any(c not in "0123456789abcdef" for c in expected_status_sha256)
    ):
        raise ValueError(
            "Refresh the displayed generation outcome before confirming it"
        )
    if any(
        not isinstance(v, str) or not v.strip() or len(v) > maximum
        for v, maximum in ((evidence_ref, 500), (note, 1000))
    ):
        raise ValueError(
            "Record a bounded evidence reference and note for the confirmed outcome"
        )
    if authorize:
        authorize()
    current = view(service, principal, project, identifier)
    entity = service.store.get(
        identifier, principal["tenant"], project, "generation-plan"
    )
    process = current["process"]
    if process is None:
        raise Conflict("No dispatched transport needs an owner outcome attestation")
    payload = sealed(
        "generation-settlement",
        identifier,
        entity["payload"]["sha256"],
        {
            "expected_status_sha256": expected_status_sha256,
            "job_id": current["job"]["id"],
            "actor": principal["actor"],
            "process_sha256": process["sha256"],
            "local_exit_confirmed": True,
            "upstream_complete_confirmed": True,
            "evidence_ref": evidence_ref.strip(),
            "note": note.strip(),
        },
    )
    if current["settlement"]:
        if current["settlement"] != payload:
            raise Conflict("An owner already confirmed a different outcome")
        if authorize:
            authorize()
        return current
    if (
        current["state"] != "unknown"
        or current["job"]["status"] in ACTIVE
        or current["status_sha256"] != expected_status_sha256
    ):
        raise Conflict(
            "This outcome is not an unresolved terminal attempt; refresh before confirming it"
        )
    with service.store.transaction() as db:
        if authorize:
            authorize()
        job = db.execute(
            "SELECT status,config FROM jobs WHERE id=? AND tenant=? AND project=?",
            (current["job"]["id"], principal["tenant"], project),
        ).fetchone()
        saved = db.execute(
            "SELECT payload FROM entities WHERE id=? AND tenant=? AND project=? AND kind='generation-plan'",
            (identifier, principal["tenant"], project),
        ).fetchone()
        receipt = db.execute(
            "SELECT payload FROM entities WHERE id=? AND tenant=? AND project=? AND kind='generation-process'",
            (record_id("generation-process", identifier), principal["tenant"], project),
        ).fetchone()
        if (
            not job
            or job["status"] != current["job"]["status"]
            or json.loads(job["config"]).get("generation_plan")
            != {"id": identifier, "sha256": entity["payload"]["sha256"]}
            or not saved
            or json.loads(saved["payload"]) != entity["payload"]
            or not receipt
            or json.loads(receipt["payload"]) != process
        ):
            raise Conflict(
                "Generation request changed while its outcome was being confirmed"
            )
        if db.execute(
            "SELECT 1 FROM entities WHERE id IN (?,?)",
            (
                record_id("generation-response", identifier),
                record_id("generation-result", identifier),
            ),
        ).fetchone():
            raise Conflict(
                "A response or candidate result already exists; refresh the request"
            )
        existing = db.execute(
            "SELECT payload FROM entities WHERE id=?",
            (record_id("generation-settlement", identifier),),
        ).fetchone()
        if existing:
            if json.loads(existing["payload"]) != payload:
                raise Conflict("Another owner confirmed a different outcome")
        else:
            db.execute(
                "INSERT INTO entities VALUES (?,?,?,?,?,?)",
                (
                    record_id("generation-settlement", identifier),
                    "generation-settlement",
                    principal["tenant"],
                    project,
                    encode(payload),
                    time.time(),
                ),
            )
            service.store.audit(
                db,
                principal["tenant"],
                project,
                principal["actor"],
                "confirm.generation-outcome",
                identifier,
            )
        if authorize:
            authorize()
    return view(service, principal, project, identifier)
