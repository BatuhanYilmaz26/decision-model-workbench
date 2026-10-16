"""Immutable owner-approved requests, policy archives and generation admission."""

import copy
import base64
from decimal import Decimal
import hashlib
import json
import math
import re
import time

from decision_workbench.json_tools import strict_json
from decision_workbench.synthetic_contracts import (
    MAX_REQUEST,
    request_body,
    usage_fields,
)
from decision_workbench.training.contracts import validate_splits
from decision_workbench.training.provenance import source_fingerprint
from .generation_profiles import estimate, selector, teacher_profile, validate_profile
from .store import Conflict, digest, encode

DOMAIN_FIELDS = (
    "name",
    "description",
    "data_requirements",
    "questions",
    "policy_version",
    "schema_sha256",
    "parent_project",
)
PLAN_FIELDS = {
    "format",
    "selector",
    "policy",
    "policy_sha256",
    "snapshot_sha256",
    "seed_ids",
    "seed_sha256s",
    "profile",
    "estimate",
    "request_body",
    "request_sha256",
    "source_sha256",
    "approved_by",
    "request_key",
    "data_approved",
    "cost_approved",
    "attempt",
    "preview_sha256",
    "sha256",
}
RECORD_KINDS = {
    "generation-process",
    "generation-response",
    "generation-result",
    "generation-settlement",
}
ACTIVE = ("queued", "running", "cancel_requested")


def record_id(kind, plan_id):
    if kind not in RECORD_KINDS:
        raise ValueError("Unsupported generation record")
    return digest({"generation_record": kind, "plan_id": plan_id})[:32]


def sealed(kind, plan_id, plan_sha256, values):
    if kind not in RECORD_KINDS:
        raise ValueError("Unsupported generation record")
    if set(values) & {"format", "plan_id", "plan_sha256", "sha256"}:
        raise ValueError("Generation record values cannot replace their identity")
    payload = {
        "format": "domain-" + kind + "-v1",
        "plan_id": plan_id,
        "plan_sha256": plan_sha256,
        **copy.deepcopy(values),
    }
    return {**payload, "sha256": digest(payload)}


def record(store, tenant, project, kind, plan_id, plan_sha256):
    try:
        entity = store.get(record_id(kind, plan_id), tenant, project, kind)
    except LookupError:
        return None
    payload = entity["payload"]
    if (
        not isinstance(payload, dict)
        or payload.get("format") != "domain-" + kind + "-v1"
        or payload.get("plan_id") != plan_id
        or payload.get("plan_sha256") != plan_sha256
        or payload.get("sha256")
        != digest({k: v for k, v in payload.items() if k != "sha256"})
    ):
        raise ValueError("Generation history integrity check failed")
    common = {"format", "plan_id", "plan_sha256", "sha256"}
    fields = {
        "generation-process": {
            "host_sha256",
            "pid",
            "parent_pid",
            "token",
            "started_at",
        },
        "generation-response": {
            "raw_base64",
            "response_sha256",
            "process_sha256",
            "received_at",
            "reported_model",
            "usage",
            "usage_status",
            "reported_cost_usd",
        },
        "generation-result": {
            "dataset_id",
            "dataset_sha256",
            "response_sha256",
            "rows",
        },
        "generation-settlement": {
            "expected_status_sha256",
            "job_id",
            "actor",
            "process_sha256",
            "local_exit_confirmed",
            "upstream_complete_confirmed",
            "evidence_ref",
            "note",
        },
    }[kind]
    if set(payload) != common | fields:
        raise ValueError("Unexpected generation history fields")
    for key in fields:
        value = payload[key]
        if key.endswith("sha256") and (
            not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
        ):
            raise ValueError("Invalid generation history checksum")
        if key in ("started_at", "received_at") and (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not 0 <= value <= 253402300799
        ):
            raise ValueError("Invalid generation history timestamp")
    if kind == "generation-process" and (
        any(
            type(payload[k]) is not int or not 1 <= payload[k] <= 2**32
            for k in ("pid", "parent_pid")
        )
        or not isinstance(payload["token"], str)
        or not re.fullmatch(r"[a-f0-9]{32}", payload["token"])
    ):
        raise ValueError("Invalid generation process identity")
    if kind == "generation-response":
        try:
            if (
                not isinstance(payload["raw_base64"], str)
                or len(payload["raw_base64"]) > 1398104
            ):
                raise ValueError()
            data = base64.b64decode(payload["raw_base64"], validate=True)
            if (
                len(data) > 1048576
                or hashlib.sha256(data).hexdigest() != payload["response_sha256"]
            ):
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError("Saved generation response changed") from None
        if payload["usage_status"] not in ("reported", "missing", "invalid") or (
            payload["usage_status"] == "reported"
        ) != (payload["usage"] is not None):
            raise ValueError("Saved generation usage status changed")
        usage_fields(payload["usage"])
        if payload["reported_cost_usd"] is not None:
            value = payload["reported_cost_usd"]
            if (
                not isinstance(value, str)
                or len(value) > 40
                or not re.fullmatch(r"[0-9]+\.[0-9]{8}", value)
                or not 0 <= Decimal(value) <= 200000000
                or payload["usage"] is None
            ):
                raise ValueError("Saved usage-cost estimate changed")
    if kind == "generation-result" and (
        not isinstance(payload["dataset_id"], str)
        or not re.fullmatch(r"[a-f0-9]{32}", payload["dataset_id"])
        or type(payload["rows"]) is not int
        or not 1 <= payload["rows"] <= 50
    ):
        raise ValueError("Invalid generated dataset binding")
    if kind == "generation-settlement":
        if (
            payload["local_exit_confirmed"] is not True
            or payload["upstream_complete_confirmed"] is not True
            or any(
                not isinstance(payload[k], str)
                or not payload[k].strip()
                or len(payload[k]) > maximum
                for k, maximum in (
                    ("job_id", 128),
                    ("actor", 200),
                    ("evidence_ref", 500),
                    ("note", 1000),
                )
            )
        ):
            raise ValueError("Invalid generation outcome attestation")
    return entity


def inputs(store, tenant, project, chosen, *, require_current=True):
    spec = store.get(project, tenant, project, "project")["payload"]
    domain = {k: copy.deepcopy(spec[k]) for k in DOMAIN_FIELDS}
    snapshot = store.get(chosen["snapshot"], tenant, project, "snapshot")["payload"]
    if (
        snapshot["schema_sha256"] != domain["schema_sha256"]
        or digest(snapshot["splits"]) != snapshot["sha256"]
    ):
        raise ValueError(
            "Generation snapshot differs from the project schema or its pinned content"
        )
    validate_splits(snapshot["splits"])
    from .semantic import verify_snapshot
    from .taxonomy import verify_snapshot as verify_taxonomy

    verify_snapshot(
        store, tenant, project, snapshot, require_current_profile=require_current
    )
    verify_taxonomy(store, tenant, project, snapshot)
    if chosen["seed_count"] > len(snapshot["splits"]["train"]):
        raise ValueError("Choose no more than the available approved training seeds")
    return (
        domain,
        snapshot,
        copy.deepcopy(snapshot["splits"]["train"][: chosen["seed_count"]]),
    )


def teacher_spec(domain, coverage):
    return {
        "questions": domain["questions"],
        "description": domain["description"]
        + "\nData requirements: "
        + domain["data_requirements"]
        + "\nRequested candidate coverage: "
        + coverage,
    }


def definition(store, tenant, project, chosen, profile):
    domain, snapshot, seeds = inputs(store, tenant, project, chosen)
    body = request_body(
        teacher_spec(domain, chosen["coverage"]),
        seeds,
        profile["model"],
        chosen["count"],
        output_tokens=profile["max_output_tokens"],
        token_parameter=profile["token_parameter"],
        temperature=profile["temperature"],
    )
    value = {
        "selector": chosen,
        "policy": domain,
        "policy_sha256": digest(domain),
        "snapshot_sha256": snapshot["sha256"],
        "seed_ids": [r["id"] for r in seeds],
        "seed_sha256s": [digest(r) for r in seeds],
        "profile": profile,
        "estimate": estimate(body, profile),
        "request_body": body.decode("utf-8"),
        "request_sha256": hashlib.sha256(body).hexdigest(),
        "source_sha256": source_fingerprint(),
    }
    return value, body


def plan_job(store, tenant, project, plan):
    with store.transaction() as db:
        row = db.execute(
            "SELECT * FROM jobs WHERE tenant=? AND project=? AND request_key=?",
            (tenant, project, plan["request_key"]),
        ).fetchone()
    if not row:
        raise ValueError("Generation plan has no matching durable job")
    return store.decode_job(row)


def load(store, tenant, project, binding, *, require_current=False, seen=None):
    if (
        not isinstance(binding, dict)
        or set(binding) != {"id", "sha256"}
        or not isinstance(binding["id"], str)
        or not re.fullmatch(r"[a-f0-9]{32}", binding["id"])
        or not isinstance(binding["sha256"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", binding["sha256"])
    ):
        raise ValueError("Invalid generation plan binding")
    seen = set() if seen is None else seen
    if binding["id"] in seen or len(seen) >= 64:
        raise ValueError("Cyclic or oversized generation retry history")
    seen.add(binding["id"])
    entity = store.get(binding["id"], tenant, project, "generation-plan")
    plan = entity["payload"]
    if (
        not isinstance(plan, dict)
        or set(plan) != PLAN_FIELDS
        or plan["format"] != "domain-generation-plan-v1"
        or plan["sha256"] != binding["sha256"]
        or plan["sha256"] != digest({k: v for k, v in plan.items() if k != "sha256"})
        or plan["data_approved"] is not True
        or plan["cost_approved"] is not True
        or not isinstance(plan["approved_by"], str)
        or not plan["approved_by"].strip()
        or not isinstance(plan["request_key"], str)
        or not 8 <= len(plan["request_key"]) <= 128
        or not isinstance(plan["source_sha256"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", plan["source_sha256"])
        or type(plan["attempt"]) is not int
        or not 1 <= plan["attempt"] <= 5
    ):
        raise ValueError("Generation plan integrity check failed")
    if (
        selector(plan["selector"]) != plan["selector"]
        or validate_profile(plan["profile"]) != plan["profile"]
    ):
        raise ValueError("Generation selection or teacher configuration changed")
    if (
        entity["id"]
        != digest(
            {
                "generation": project,
                "tenant": tenant,
                "request_key": plan["request_key"],
            }
        )[:32]
    ):
        raise ValueError("Generation request identity differs from its approval key")
    domain, snapshot, seeds = inputs(
        store, tenant, project, plan["selector"], require_current=require_current
    )
    if (
        domain != plan["policy"]
        or digest(domain) != plan["policy_sha256"]
        or snapshot["sha256"] != plan["snapshot_sha256"]
        or plan["seed_ids"] != [r["id"] for r in seeds]
        or plan["seed_sha256s"] != [digest(r) for r in seeds]
    ):
        raise ValueError(
            "Frozen generation policy, reservation or seed identity changed"
        )
    try:
        raw = plan["request_body"]
        if not isinstance(raw, str):
            raise ValueError()
        body = raw.encode("utf-8")
        if (
            len(body) > MAX_REQUEST
            or hashlib.sha256(body).hexdigest() != plan["request_sha256"]
        ):
            raise ValueError()
        request = strict_json(body)
        profile = plan["profile"]
        fields = {
            "model",
            "messages",
            "response_format",
            "stream",
            profile["token_parameter"],
        } | ({"temperature"} if profile["temperature"] is not None else set())
        if (
            set(request) != fields
            or request["model"] != profile["model"]
            or request[profile["token_parameter"]] != profile["max_output_tokens"]
            or request.get("temperature") != profile["temperature"]
            or request["response_format"] != {"type": "json_object"}
            or request["stream"] is not False
        ):
            raise ValueError()
        messages = request["messages"]
        if (
            not isinstance(messages, list)
            or len(messages) != 2
            or any(
                not isinstance(m, dict)
                or set(m) != {"role", "content"}
                or not isinstance(m["content"], str)
                or not m["content"].strip()
                for m in messages
            )
            or messages[0]["role"] != "system"
            or messages[1]["role"] != "user"
        ):
            raise ValueError()
        prompt = strict_json(messages[1]["content"])
        brief = teacher_spec(domain, plan["selector"]["coverage"])
        if (
            prompt["domain_description"] != brief["description"]
            or prompt["questions"] != domain["questions"]
            or prompt["examples"]
            != [{"state": r["state"], "gold": r["gold"]} for r in seeds]
        ):
            raise ValueError()
        if estimate(body, profile) != plan["estimate"]:
            raise ValueError()
    except (
        KeyError,
        TypeError,
        ValueError,
        AttributeError,
        UnicodeError,
        RecursionError,
    ):
        raise ValueError("Frozen generation request or cost estimate changed") from None
    context = {
        k: plan[k]
        for k in (
            "selector",
            "policy",
            "policy_sha256",
            "snapshot_sha256",
            "seed_ids",
            "seed_sha256s",
            "profile",
            "estimate",
            "request_body",
            "request_sha256",
            "source_sha256",
        )
    }
    if digest(context) != plan["preview_sha256"]:
        raise ValueError("Generation preview binding changed")
    job = plan_job(store, tenant, project, plan)
    if (
        job["kind"] != "generate"
        or job["actor"] != plan["approved_by"]
        or job["config"]
        != {"snapshot": plan["selector"]["snapshot"], "generation_plan": binding}
    ):
        raise ValueError("Generation plan differs from its approved job")
    parent_id = plan["selector"]["retry_of"]
    if parent_id:
        parent = store.get(parent_id, tenant, project, "generation-plan")
        previous, parent_job, _, _ = load(
            store,
            tenant,
            project,
            {"id": parent_id, "sha256": parent["payload"]["sha256"]},
            seen=seen,
        )
        prior = previous["payload"]
        if (
            parent_job["status"] not in ("failed", "cancelled", "interrupted")
            or closed(store, tenant, project, previous, parent_job)
            not in ("response_received", "owner_attested_complete", "not_dispatched")
            or plan["attempt"] != prior["attempt"] + 1
            or plan["attempt"] > plan["profile"]["max_attempts"]
            or any(
                plan["selector"][k] != prior["selector"][k]
                for k in ("snapshot", "count", "seed_count", "coverage")
            )
            or plan["request_sha256"] != prior["request_sha256"]
            or plan["profile"] != prior["profile"]
            or plan["source_sha256"] != prior["source_sha256"]
        ):
            raise ValueError("Generation retry differs from its closed approved parent")
    elif plan["attempt"] != 1:
        raise ValueError("An initial generation request must be attempt one")
    return entity, job, seeds, body


def closed(store, tenant, project, entity, job):
    plan = entity["payload"]
    identifier = entity["id"]
    response = record(
        store, tenant, project, "generation-response", identifier, plan["sha256"]
    )
    process = record(
        store, tenant, project, "generation-process", identifier, plan["sha256"]
    )
    settlement = record(
        store, tenant, project, "generation-settlement", identifier, plan["sha256"]
    )
    if response and not process:
        raise ValueError("A generation response has no dispatched process receipt")
    if (
        response
        and response["payload"]["process_sha256"] != process["payload"]["sha256"]
    ):
        raise ValueError("Generation response process identity changed")
    if settlement and (
        not process
        or job["status"] in ACTIVE
        or settlement["payload"]["job_id"] != job["id"]
        or settlement["payload"]["process_sha256"] != process["payload"]["sha256"]
    ):
        raise ValueError("Generation outcome attestation differs from its terminal job")
    if response:
        return "response_received"
    if settlement:
        return "owner_attested_complete"
    if job["status"] not in ACTIVE and not process:
        return "not_dispatched"
    return "pending" if job["status"] in ACTIVE else "unknown"


def retry_attempt(store, tenant, project, chosen, context):
    if not chosen["retry_of"]:
        return 1
    parent = store.get(chosen["retry_of"], tenant, project, "generation-plan")
    prior, job, _, _ = load(
        store,
        tenant,
        project,
        {"id": parent["id"], "sha256": parent["payload"]["sha256"]},
    )
    if job["status"] not in ("failed", "cancelled", "interrupted") or closed(
        store, tenant, project, prior, job
    ) not in ("response_received", "owner_attested_complete", "not_dispatched"):
        raise Conflict(
            "Inspect and close the previous terminal attempt before approving a retry"
        )
    previous = prior["payload"]
    if record(
        store, tenant, project, "generation-result", prior["id"], previous["sha256"]
    ):
        raise Conflict(
            "Candidates already exist for this request; review that dataset or intentionally start a new batch"
        )
    if (
        any(
            chosen[k] != previous["selector"][k]
            for k in ("snapshot", "count", "seed_count", "coverage")
        )
        or context["request_sha256"] != previous["request_sha256"]
        or context["profile"] != previous["profile"]
        or context["source_sha256"] != previous["source_sha256"]
    ):
        raise Conflict(
            "A retry must preserve the exact request and compatible source; otherwise preview a new generation request"
        )
    attempt = previous["attempt"] + 1
    if attempt > context["profile"]["max_attempts"]:
        raise Conflict("This generation request reached its approved attempt limit")
    return attempt


def admission(db, tenant, profile, planned):
    since = time.time() - 86400
    rows = db.execute(
        "SELECT p.payload, (SELECT json_extract(r.payload,'$.reported_cost_usd') FROM entities r WHERE r.tenant=p.tenant AND r.project=p.project AND r.kind='generation-response' AND json_extract(r.payload,'$.plan_id')=p.id LIMIT 1) AS reported_usd FROM entities p WHERE p.tenant=? AND p.kind='generation-plan' AND json_extract(p.payload,'$.profile.base_url')=? AND p.created>=? LIMIT 101",
        (tenant, profile["base_url"], since),
    ).fetchall()
    if len(rows) >= profile["max_calls_day"]:
        raise Conflict("Synthetic generation daily request limit reached")
    request_cap = profile["max_estimated_request_usd"]
    if request_cap is not None and Decimal(planned["usd"]) > Decimal(request_cap):
        raise Conflict(
            "The token-cost planning estimate exceeds the configured per-request estimate limit"
        )
    day_cap = profile["max_estimated_day_usd"]
    if day_cap is not None:
        values = [json.loads(r["payload"])["estimate"]["usd"] for r in rows]
        if any(v is None for v in values):
            raise Conflict(
                "Earlier unpriced requests prevent a reliable daily planning-budget check"
            )
        reserved = [
            max(
                Decimal(v),
                (
                    Decimal(row["reported_usd"])
                    if row["reported_usd"] is not None
                    else Decimal(v)
                ),
            )
            for v, row in zip(values, rows)
        ]
        if sum(reserved, Decimal(0)) + Decimal(planned["usd"]) > Decimal(day_cap):
            raise Conflict(
                "The reserved token-cost estimates exceed the configured daily planning limit"
            )
    unresolved = db.execute(
        "SELECT count(*) FROM entities p JOIN jobs j ON j.tenant=p.tenant AND j.project=p.project AND j.kind='generate' AND j.request_key=json_extract(p.payload,'$.request_key') "
        "WHERE p.tenant=? AND p.kind='generation-plan' AND json_extract(p.payload,'$.profile.base_url')=? "
        "AND NOT EXISTS(SELECT 1 FROM entities r WHERE r.tenant=p.tenant AND r.project=p.project AND r.kind IN ('generation-response','generation-settlement') AND json_extract(r.payload,'$.plan_id')=p.id) "
        "AND (j.status IN ('queued','running','cancel_requested') OR EXISTS(SELECT 1 FROM entities c WHERE c.tenant=p.tenant AND c.project=p.project AND c.kind='generation-process' AND json_extract(c.payload,'$.plan_id')=p.id))",
        (tenant, profile["base_url"]),
    ).fetchone()[0]
    if unresolved >= profile["max_inflight"]:
        raise Conflict(
            "Synthetic generation capacity is reserved by a pending or unresolved request; inspect its outcome before another call"
        )


def preview(service, principal, project, config):
    service.require(principal, ("admin",))
    chosen = selector(config)
    context, _ = definition(
        service.store, principal["tenant"], project, chosen, teacher_profile()
    )
    attempt = retry_attempt(
        service.store, principal["tenant"], project, chosen, context
    )
    return {**context, "sha256": digest(context), "attempt": attempt}


def freeze(service, principal, project, config, request_key, authorize=None):
    service.require(principal, ("admin",))
    service.project(principal, project)
    if not isinstance(request_key, str) or not 8 <= len(request_key) <= 128:
        raise ValueError("Use an 8–128 character generation request key")
    if (
        config.get("data_approved") is not True
        or config.get("cost_approved") is not True
    ):
        raise ValueError(
            "Approve sending the displayed policy/training seeds and any charges before generation"
        )
    chosen = selector(config)
    tenant = principal["tenant"]
    with service.store.transaction() as db:
        old = db.execute(
            "SELECT kind,config FROM jobs WHERE tenant=? AND project=? AND request_key=?",
            (tenant, project, request_key),
        ).fetchone()
    if old:
        if authorize:
            authorize()
        if old["kind"] != "generate":
            raise Conflict("Request key belongs to a different job")
        existing = json.loads(old["config"])
        if "generation_plan" not in existing:
            raise Conflict(
                "This historical generation job cannot be replayed; create a separately approved request"
            )
        entity, _, _, _ = load(
            service.store, tenant, project, existing["generation_plan"]
        )
        if (
            chosen != entity["payload"]["selector"]
            or config.get("expected_preview_sha256")
            != entity["payload"]["preview_sha256"]
        ):
            raise Conflict(
                "Request key already belongs to a different approved generation request"
            )
        if authorize:
            authorize()
        return existing, None
    context, _ = definition(service.store, tenant, project, chosen, teacher_profile())
    if config.get("expected_preview_sha256") != digest(context):
        raise Conflict(
            "Generation inputs, teacher or prices changed; preview and approve again"
        )
    attempt = retry_attempt(service.store, tenant, project, chosen, context)
    identifier = digest(
        {"generation": project, "tenant": tenant, "request_key": request_key}
    )[:32]
    payload = {
        "format": "domain-generation-plan-v1",
        **context,
        "approved_by": principal["actor"],
        "request_key": request_key,
        "data_approved": True,
        "cost_approved": True,
        "attempt": attempt,
        "preview_sha256": digest(context),
    }
    payload["sha256"] = digest(payload)

    def insert(db):
        if authorize:
            authorize()
        admission(db, tenant, context["profile"], context["estimate"])
        if (
            chosen["retry_of"]
            and db.execute(
                "SELECT 1 FROM entities WHERE tenant=? AND project=? AND kind='generation-plan' AND json_extract(payload,'$.selector.retry_of')=? LIMIT 1",
                (tenant, project, chosen["retry_of"]),
            ).fetchone()
        ):
            raise Conflict(
                "A retry already exists for this request; open its later attempt"
            )
        db.execute(
            "INSERT INTO entities VALUES (?,?,?,?,?,?)",
            (
                identifier,
                "generation-plan",
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
            "confirm.generation-plan",
            identifier,
        )
        if authorize:
            authorize()

    return {
        "snapshot": chosen["snapshot"],
        "generation_plan": {"id": identifier, "sha256": payload["sha256"]},
    }, insert
