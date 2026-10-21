"""Operator-owned decision profiles, typed provider boundaries and durable call accounting."""

import copy
import hashlib
import json
import math
import os
import re
import socket
import time
from pathlib import Path
import httpx
from decision_workbench.data import options, validate_questions
from decision_workbench.training.metrics import normalize_answers
from .store import Conflict, digest, encode
from .hosted_transport import (
    TransportFailure,
    endpoint as endpoint,
    receive,
    run_process,
)


class HostedUnavailable(RuntimeError):
    """Safe public failure; upstream bodies and credentials are never exposed."""


class HostedQuota(HostedUnavailable):
    pass


def number(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def profiles():
    filename = os.environ.get("DMT_DECISION_PROFILES_FILE")
    if not filename:
        return []
    try:
        with Path(filename).open("rb") as stream:
            body = stream.read(262145)
        if len(body) > 262144:
            raise ValueError()
        config = json.loads(body)
        if (
            not isinstance(config, dict)
            or set(config) != {"format", "profiles"}
            or config["format"] != "domain-decision-profiles-v1"
        ):
            raise ValueError()
        values = config["profiles"]
        if not isinstance(values, list) or len(values) > 100:
            raise ValueError()
        identifiers = set()
        for value in values:
            validate_profile(value)
            if value["id"] in identifiers:
                raise ValueError()
            identifiers.add(value["id"])
        return values
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        raise HostedUnavailable(
            "Decision provider configuration is unavailable or invalid"
        ) from None


def validate_profile(p):
    required = {
        "id",
        "provider",
        "model",
        "response_model",
        "key_env",
        "tenants",
        "max_calls_day",
        "max_inflight",
        "timeout_seconds",
        "max_request_bytes",
    }
    optional = {"response_provider", "provider_slug", "account_id"}
    if (
        not isinstance(p, dict)
        or not required <= set(p)
        or set(p) - required - optional
    ):
        raise ValueError("Invalid decision profile fields")
    if not isinstance(p["id"], str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,64}", p["id"]
    ):
        raise ValueError("Invalid profile identifier")
    if p["provider"] not in (
        "typesafe",
        "openrouter",
        "perplexity",
        "cloudflare",
        "liquid",
    ):
        raise ValueError("Unsupported decision provider")
    for name in ("model", "response_model"):
        if not isinstance(p[name], str) or not re.fullmatch(
            r"[A-Za-z0-9_./-]{1,150}", p[name]
        ):
            raise ValueError("Invalid provider model identifier")
    if not isinstance(p["key_env"], str) or not re.fullmatch(
        r"DMT_PROVIDER_[A-Z0-9_]{1,80}", p["key_env"]
    ):
        raise ValueError("Use a dedicated DMT_PROVIDER_ credential variable")
    if (
        not isinstance(p["tenants"], list)
        or not 1 <= len(p["tenants"]) <= 100
        or any(not isinstance(t, str) or not t or len(t) > 120 for t in p["tenants"])
        or len(set(p["tenants"])) != len(p["tenants"])
    ):
        raise ValueError("Profile must name approved tenants")
    for name, low, high in (
        ("max_calls_day", 1, 100000),
        ("max_inflight", 1, 16),
        ("max_request_bytes", 256, 64000),
    ):
        if type(p[name]) is not int or not low <= p[name] <= high:
            raise ValueError("Invalid provider limit")
    if not number(p["timeout_seconds"], 3, 30):
        raise ValueError("Provider request deadline must be 3–30 seconds")
    if p["provider"] == "openrouter":
        if (
            not isinstance(p.get("response_provider"), str)
            or not p["response_provider"].strip()
            or len(p["response_provider"]) > 120
            or not isinstance(p.get("provider_slug"), str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,119}", p["provider_slug"])
        ):
            raise ValueError("Pin the OpenRouter response provider")
    elif "response_provider" in p or "provider_slug" in p:
        raise ValueError("Direct provider identity comes from its fixed endpoint")
    if p["provider"] == "cloudflare":
        if (
            not isinstance(p.get("account_id"), str)
            or not re.fullmatch(r"[a-f0-9]{32}", p["account_id"])
            or p["model"] not in ("clef", "clef-flash")
            or p["max_request_bytes"] > 1024
        ):
            raise ValueError(
                "Cloudflare requires an account and a conservative 1024-byte input bound"
            )
    elif "account_id" in p:
        raise ValueError("Unexpected account identifier")
    if p["provider"] == "liquid" and (
        p["model"] not in ("d1", "d1-ultra") or p["response_model"] != p["model"]
    ):
        raise ValueError("Liquid profiles must select d1 or d1-ultra explicitly")
    if p["provider"] == "perplexity":
        from .perplexity import validate_profile as validate_perplexity

        validate_perplexity(p)


def public_profile(profile):
    # Credential locations and approved tenant identities are operator configuration.

    value = {
        k: copy.deepcopy(v)
        for k, v in profile.items()
        if k not in ("key_env", "tenants")
    }
    if profile["provider"] == "perplexity":
        from .perplexity import binding

        value["api_contract"] = binding(profile)
    return value


def profile_for(tenant, identifier, expected=None):
    for p in profiles():
        if p["id"] == identifier and tenant in p["tenants"]:
            public = public_profile(p)
            if expected is not None and public != expected:
                raise HostedUnavailable(
                    "Decision profile changed; qualify a successor model study"
                )
            return p
    raise HostedUnavailable(
        "Decision profile is disabled or unavailable for this workspace"
    )


def request_body(profile, state, questions):
    validate_questions(questions)
    if not isinstance(state, (str, dict, list)) or not state:
        raise ValueError("Decision evidence must be nonempty text or JSON")
    if profile["provider"] == "cloudflare" and any(
        not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name) for name in questions
    ):
        raise ValueError("Cloudflare question IDs exceed its contract")
    value = {"model": profile["model"], "state": state, "questions": questions}
    if profile["provider"] == "openrouter":
        value["provider"] = {
            "allow_fallbacks": False,
            "only": [profile["provider_slug"]],
        }
    body = encode(value).encode("utf-8")
    if len(body) > profile["max_request_bytes"]:
        raise ValueError(
            "Decision input exceeds the qualified provider byte budget; no truncation is performed"
        )
    if profile["provider"] == "perplexity":
        from .perplexity import validate_request

        validate_request(state, questions)
    return body


def decode_response(body, profile, questions):
    try:
        if profile["provider"] == "cloudflare":
            if (
                not isinstance(body, dict)
                or body.get("success") is not True
                or body.get("errors") not in (None, [])
            ):
                raise ValueError()
            body = body["result"]
        if not isinstance(body, dict) or body.get("model") != profile["response_model"]:
            raise ValueError()
        if (
            profile["provider"] == "openrouter"
            and body.get("provider") != profile["response_provider"]
        ):
            raise ValueError()
        wire = body["answers"]
        if not isinstance(wire, dict) or set(wire) != set(questions):
            raise ValueError()
        canonical, confidence, raw_probabilities = {}, {}, {}
        for name, q in questions.items():
            a = wire[name]
            if not isinstance(a, dict) or a.get("type") != q["type"]:
                raise ValueError()
            keys = options(q)
            if q["type"] == "noul":
                yes = a["noul"]
                if not number(yes, 0, 1):
                    raise ValueError()
                probs = {"false": 1 - yes, "true": yes}
            else:
                probs = a["probabilities"]
                if (
                    not isinstance(probs, dict)
                    or set(probs) != set(keys)
                    or any(not number(p, 0, 1) for p in probs.values())
                    or abs(sum(probs.values()) - 1) > 0.002
                ):
                    raise ValueError()
                if q["type"] == "choice":
                    label = a["choice"]
                    if (
                        not isinstance(label, str)
                        or label not in keys
                        or probs[label] != max(probs.values())
                    ):
                        raise ValueError()
                else:
                    score = a["score"]
                    if (
                        not number(score, 0, len(keys) - 1)
                        or abs(score - sum(int(k) * p for k, p in probs.items())) > 0.01
                    ):
                        raise ValueError()
                    if "legend" in a and a["legend"] != dict(zip(keys, q["criteria"])):
                        raise ValueError()
                if not number(a.get("confidence"), 0, 1):
                    raise ValueError()
                confidence[name] = a["confidence"]
                raw_probabilities[name] = dict(probs)
                total = sum(probs.values())
                probs = {k: p / total for k, p in probs.items()}
            raw_probabilities.setdefault(name, dict(probs))
            canonical[name] = {"probabilities": probs}
        usage = body.get("usage")
        if not isinstance(usage, dict):
            raise ValueError()
        safe_usage = {}
        for key in ("input_tokens", "output_tokens"):
            if key in usage:
                if type(usage[key]) is not int or not 0 <= usage[key] <= 100000000:
                    raise ValueError()
                safe_usage[key] = usage[key]
        if profile["provider"] == "liquid" and (
            "input_tokens" not in safe_usage or safe_usage.get("output_tokens") != 0
        ):
            raise ValueError()
        if profile["provider"] == "perplexity" and set(safe_usage) != {
            "input_tokens",
            "output_tokens",
        }:
            raise ValueError()
        if "cost" in usage:
            if not number(usage["cost"], 0, 10000):
                raise ValueError()
            safe_usage["cost"] = usage["cost"]
        identity = {
            "provider": profile["provider"],
            "model": body["model"],
            "usage": safe_usage,
            "provider_confidence": confidence,
            "raw_probabilities": raw_probabilities,
        }
        if profile["provider"] == "openrouter":
            identity["routed_provider"] = body["provider"]
        if profile["provider"] == "perplexity":
            from .perplexity import binding

            identity["api_contract"] = binding(profile)
        if "id" in body:
            if not isinstance(body["id"], str) or len(body["id"]) > 256:
                raise ValueError()
            identity["request_id"] = body["id"]
        return {
            "answers": normalize_answers(canonical, questions),
            "identity": identity,
        }
    except (ValueError, TypeError, KeyError, OverflowError):
        raise HostedUnavailable(
            "Provider response failed the pinned identity, question or probability contract"
        ) from None


def reserve(
    store, principal, project, profile, call_key, request_sha, operation, job=None
):
    tenant, actor = principal["tenant"], principal["actor"]
    key_sha = digest(call_key)
    call_id = digest({"tenant": tenant, "project": project, "key_sha256": key_sha})[:32]
    now = time.time()
    with store.transaction() as db:
        if job:
            store._assert_owner(db, job)
        existing = db.execute(
            "SELECT payload FROM entities WHERE id=? AND tenant=? AND project=? AND kind='provider-call'",
            (call_id, tenant, project),
        ).fetchone()
        if existing:
            value = json.loads(existing["payload"])
            if value["request_sha256"] != request_sha:
                raise Conflict(
                    "Decision idempotency key belongs to a different request or release"
                )
            result = db.execute(
                "SELECT payload FROM entities WHERE id=? AND tenant=? AND project=? AND kind='provider-call-result'",
                (digest({"call": call_id})[:32], tenant, project),
            ).fetchone()
            if result and json.loads(result["payload"])["status"] == "succeeded":
                return call_id, json.loads(result["payload"])["result"]
            raise Conflict(
                "Previous decision is pending, failed or ambiguous; it will not be retried automatically"
            )
        used = db.execute(
            "SELECT count(*) FROM entities WHERE tenant=? AND kind='provider-call' AND json_extract(payload,'$.profile_id')=? AND created>=?",
            (tenant, profile["id"], now - 86400),
        ).fetchone()[0]
        if used >= profile["max_calls_day"]:
            raise HostedQuota(
                "Decision profile rolling 24-hour call quota is exhausted"
            )
        inflight = db.execute(
            "SELECT count(*) FROM entities e LEFT JOIN entities r ON r.id=json_extract(e.payload,'$.result_id') AND r.kind='provider-call-result' AND r.tenant=e.tenant AND r.project=e.project LEFT JOIN entities c ON c.kind='provider-call-reconciliation' AND c.tenant=e.tenant AND c.project=e.project AND json_extract(c.payload,'$.call_id')=e.id WHERE e.tenant=? AND e.kind='provider-call' AND json_extract(e.payload,'$.profile_id')=? AND (r.id IS NULL OR coalesce(json_extract(r.payload,'$.status'),'unknown')!='succeeded') AND c.id IS NULL",
            (tenant, profile["id"]),
        ).fetchone()[0]
        if inflight >= profile["max_inflight"]:
            raise HostedQuota(
                "Decision profile unresolved-call capacity is exhausted; inspect and reconcile uncertain calls"
            )
        value = {
            "lifecycle_version": 2,
            "profile_id": profile["id"],
            "profile_sha256": digest(public_profile(profile)),
            "profile": public_profile(profile),
            "key_sha256": key_sha,
            "result_id": digest({"call": call_id})[:32],
            "request_sha256": request_sha,
            "timeout_seconds": profile["timeout_seconds"],
            "operation": operation,
            "job_id": job["id"] if job else None,
        }
        db.execute(
            "INSERT INTO entities VALUES (?,?,?,?,?,?)",
            (call_id, "provider-call", tenant, project, encode(value), now),
        )
        store.audit(db, tenant, project, actor, "create.provider-call", call_id)
    return call_id, None


def finish_call(
    store,
    principal,
    project,
    call_id,
    status,
    result,
    elapsed,
    transport_terminated=True,
):
    # Record the external outcome even if a worker lost its lease during the call.

    value = {
        "call_id": call_id,
        "status": status,
        "result": result,
        "elapsed_seconds": elapsed,
        "transport_terminated": transport_terminated,
    }
    store.add(
        "provider-call-result",
        principal["tenant"],
        project,
        principal["actor"],
        value,
        digest({"call": call_id})[:32],
    )


def call_outcome(store, principal, project, call_id):
    try:
        return store.get(
            digest({"call": call_id})[:32],
            principal["tenant"],
            project,
            "provider-call-result",
        )["payload"]
    except LookupError:
        return {"status": "pending_or_unknown"}


def reconciliation_id(call_id):
    return digest({"reconcile_provider_call": call_id})[:32]


def reconcile_call(
    store,
    principal,
    project,
    call_id,
    expected_outcome_sha256,
    local_transport_stopped,
    provider_finished,
    reference,
    note,
    authorize=None,
):
    """Owner attestation frees capacity only; original call/key/outcome remain immutable."""
    if principal["role"] != "admin":
        raise PermissionError("Owner permission is required")
    if (
        local_transport_stopped is not True
        or provider_finished is not True
        or not isinstance(reference, str)
        or not 1 <= len(reference.strip()) <= 500
        or not isinstance(note, str)
        or not 1 <= len(note.strip()) <= 1000
        or not isinstance(expected_outcome_sha256, str)
        or not re.fullmatch(r"[a-f0-9]{64}", expected_outcome_sha256)
    ):
        raise ValueError(
            "Confirm local transport termination and upstream settlement with a reference and note"
        )
    tenant = principal["tenant"]
    identifier = reconciliation_id(call_id)
    now = time.time()
    with store.transaction() as db:
        row = db.execute(
            "SELECT * FROM entities WHERE id=? AND tenant=? AND project=? AND kind='provider-call'",
            (call_id, tenant, project),
        ).fetchone()
        if row is None:
            raise LookupError("Resource not found")
        call = json.loads(row["payload"])
        outcome_row = db.execute(
            "SELECT payload FROM entities WHERE id=? AND tenant=? AND project=? AND kind='provider-call-result'",
            (call["result_id"], tenant, project),
        ).fetchone()
        outcome = (
            json.loads(outcome_row[0])
            if outcome_row
            else {"status": "pending_or_unknown"}
        )
        if digest(outcome) != expected_outcome_sha256:
            raise Conflict(
                "Provider outcome changed; refresh call history before reconciliation"
            )
        if outcome["status"] == "succeeded":
            raise Conflict("Successful calls already release capacity")
        if (
            outcome_row is None or outcome.get("transport_terminated") is not True
        ) and now < row["created"] + call["timeout_seconds"] + 10:
            raise Conflict(
                "Call may still be active; wait and verify local termination before settlement"
            )
        if call.get("job_id"):
            job = db.execute(
                "SELECT status FROM jobs WHERE id=? AND tenant=? AND project=?",
                (call["job_id"], tenant, project),
            ).fetchone()
            if not job or job[0] not in (
                "failed",
                "cancelled",
                "succeeded",
                "interrupted",
            ):
                raise Conflict(
                    "Stop and reconcile the owning job before releasing call capacity"
                )
        value = {
            "call_id": call_id,
            "outcome_sha256": expected_outcome_sha256,
            "observed_outcome": outcome,
            "local_transport_stopped": True,
            "provider_finished": True,
            "reference": reference.strip(),
            "note": note.strip(),
            "actor": principal["actor"],
            "basis": "owner_attestation_not_provider_receipt",
        }
        if authorize:
            authorize()
        old = db.execute(
            "SELECT payload FROM entities WHERE id=?", (identifier,)
        ).fetchone()
        if old:
            if json.loads(old[0]) != value:
                raise Conflict("Call has an immutable settlement; refresh history")
            return {"id": identifier, "payload": value}
        db.execute(
            "INSERT INTO entities VALUES (?,?,?,?,?,?)",
            (
                identifier,
                "provider-call-reconciliation",
                tenant,
                project,
                encode(value),
                now,
            ),
        )
        store.audit(
            db, tenant, project, principal["actor"], "reconcile.provider-call", call_id
        )
    return {"id": identifier, "payload": value}


class Hosted:
    def __init__(self, store, transport=None):
        self.store = store
        self.transport = transport

    def call(
        self, principal, project, frozen, state, questions, call_key, context, job=None
    ):
        profile = profile_for(principal["tenant"], frozen["id"], frozen)
        body = request_body(profile, state, questions)
        key = credential(profile)
        call_id, cached = reserve(
            self.store,
            principal,
            project,
            profile,
            call_key,
            digest(
                {
                    "body": hashlib.sha256(body).hexdigest(),
                    "profile": frozen,
                    "context": context,
                }
            ),
            "evaluation" if job else "inference",
            job,
        )
        if cached is not None:
            return {**cached, "call_id": call_id, "cached": True}
        started = time.monotonic()
        status = "unknown"
        result = None
        terminated = True

        def record_started(process, token):
            self.store.add(
                "provider-call-transport",
                principal["tenant"],
                project,
                principal["actor"],
                {
                    "call_id": call_id,
                    "pid": process.pid,
                    "parent_pid": os.getpid(),
                    "host_sha256": hashlib.sha256(
                        socket.gethostname().encode()
                    ).hexdigest(),
                    "process_token": token,
                    "mode": "isolated_process",
                },
                digest({"provider_call_transport": call_id})[:32],
            )

        try:
            received = (
                run_process(
                    public_profile(profile), body, key, record_started=record_started
                )
                if self.transport is None
                else receive(profile, body, key, self.transport)
            )

            def unique(pairs):
                value = {}
                for k, v in pairs:
                    if k in value:
                        raise ValueError()
                    value[k] = v
                return value

            decoded = json.loads(
                received,
                object_pairs_hook=unique,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
            result = decode_response(decoded, profile, questions)
            status = "succeeded"
            return {**result, "call_id": call_id, "cached": False}
        except TransportFailure as exc:
            terminated = exc.terminated
            raise HostedUnavailable(str(exc)) from None
        except (httpx.HTTPError, ValueError, RecursionError):
            raise HostedUnavailable(
                "Decision provider transport or JSON failed; no automatic retry"
            ) from None
        finally:
            finish_call(
                self.store,
                principal,
                project,
                call_id,
                status,
                result,
                time.monotonic() - started,
                terminated,
            )


def credential(profile):
    key = os.environ.get(profile["key_env"], "")
    if not key or len(key) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise HostedUnavailable("Decision provider credential is unavailable")
    return key


def checked_hosted(store, model, questions):
    folder = store.artifact_path(model)

    def linked(path):
        return path.is_symlink() or getattr(path, "is_junction", lambda: False)()

    if (
        not re.fullmatch(r"[a-f0-9]{32}", model["artifact_id"])
        or linked(folder.parent)
        or linked(folder)
        or set(p.name for p in folder.iterdir()) != {"model.json", "report.json"}
    ):
        raise ValueError("Invalid hosted policy inventory")
    values = {}
    for name in ("model.json", "report.json"):
        path = folder / name
        if linked(path) or not path.is_file() or path.stat().st_size > 16777216:
            raise ValueError("Invalid hosted policy metadata")
        values[name] = path.read_bytes()
    if hashlib.sha256(values["model.json"]).hexdigest() != model["model_sha256"]:
        raise ValueError("Hosted policy integrity check failed")
    bundle = json.loads(values["model.json"])
    if (
        bundle.get("format") != "domain-hosted-decision-v1"
        or bundle.get("questions") != questions
        or bundle.get("profile") != model["configuration"]["profile"]
        or bundle.get("profile_sha256") != digest(bundle["profile"])
        or json.loads(values["report.json"]) != model["report"]
    ):
        raise ValueError("Hosted policy schema/profile/report integrity check failed")
    temperatures = bundle.get("temperatures")
    if (
        not isinstance(temperatures, dict)
        or set(temperatures) != set(questions)
        or any(not number(t, 0.05, 20) for t in temperatures.values())
    ):
        raise ValueError("Hosted calibration contract is invalid")
    return bundle


def validate_call_history(store):
    """Verify provider ledger relationships on portable backup/restore."""
    with store.transaction() as db:
        calls = db.execute(
            "SELECT * FROM entities WHERE kind='provider-call'"
        ).fetchall()
        outcomes = db.execute(
            "SELECT * FROM entities WHERE kind='provider-call-result'"
        ).fetchall()
    by_id = {row["id"]: row for row in calls}
    for row in calls:
        value = json.loads(row["payload"])
        if (
            row["id"]
            != digest(
                {
                    "tenant": row["tenant"],
                    "project": row["project"],
                    "key_sha256": value["key_sha256"],
                }
            )[:32]
            or value["result_id"] != digest({"call": row["id"]})[:32]
            or digest(value["profile"]) != value["profile_sha256"]
            or value["profile_id"] != value["profile"]["id"]
            or value["operation"] not in ("inference", "evaluation")
            or not re.fullmatch(r"[a-f0-9]{64}", value["request_sha256"])
        ):
            raise ValueError("Provider call history integrity check failed")
        store.get(row["project"], row["tenant"], row["project"], "project")
        if value["job_id"]:
            job = store.job(value["job_id"], row["tenant"], row["project"])
            if (
                job["kind"] != "hosted-evaluate"
                or job["config"]["profile"] != value["profile"]
            ):
                raise ValueError("Provider call job reference is invalid")
    for row in outcomes:
        value = json.loads(row["payload"])
        call = by_id.get(value["call_id"])
        if (
            not call
            or row["tenant"] != call["tenant"]
            or row["project"] != call["project"]
            or row["id"] != digest({"call": value["call_id"]})[:32]
            or value["status"] not in ("succeeded", "unknown")
            or not number(value["elapsed_seconds"], 0, 7200)
            or (
                "transport_terminated" in value
                and type(value["transport_terminated"]) is not bool
            )
            or (
                value["status"] == "succeeded"
                and value.get("transport_terminated", True) is not True
            )
        ):
            raise ValueError("Provider call outcome integrity check failed")
        if value["status"] == "succeeded":
            profile = json.loads(call["payload"])["profile"]
            result = value["result"]
            identity = result["identity"]
            if (
                identity["provider"] != profile["provider"]
                or identity["model"] != profile["response_model"]
                or (
                    profile["provider"] == "openrouter"
                    and identity.get("routed_provider") != profile["response_provider"]
                )
                or (
                    profile["provider"] == "perplexity"
                    and identity.get("api_contract") != profile.get("api_contract")
                )
            ):
                raise ValueError(
                    "Provider call identity history differs from its profile"
                )
            questions = store.get(
                row["project"], row["tenant"], row["project"], "project"
            )["payload"]["questions"]
            normalize_answers(result["answers"], questions)
        elif value["result"] is not None:
            raise ValueError("Ambiguous provider calls cannot contain accepted results")
    with store.transaction() as db:
        settlements = db.execute(
            "SELECT * FROM entities WHERE kind='provider-call-reconciliation'"
        ).fetchall()
    for row in settlements:
        value = json.loads(row["payload"])
        call = by_id.get(value["call_id"])
        if (
            not call
            or row["tenant"] != call["tenant"]
            or row["project"] != call["project"]
            or row["id"] != reconciliation_id(value["call_id"])
            or value["local_transport_stopped"] is not True
            or value["provider_finished"] is not True
            or value["basis"] != "owner_attestation_not_provider_receipt"
            or value["outcome_sha256"] != digest(value["observed_outcome"])
            or value["observed_outcome"]["status"]
            not in ("unknown", "pending_or_unknown")
            or not isinstance(value["actor"], str)
            or not value["actor"]
            or not isinstance(value["reference"], str)
            or not 1 <= len(value["reference"].strip()) <= 500
            or not isinstance(value["note"], str)
            or not 1 <= len(value["note"].strip()) <= 1000
        ):
            raise ValueError("Provider settlement history integrity check failed")
    with store.transaction() as db:
        transports = db.execute(
            "SELECT * FROM entities WHERE kind='provider-call-transport'"
        ).fetchall()
    for row in transports:
        value = json.loads(row["payload"])
        call = by_id.get(value["call_id"])
        if (
            not call
            or row["tenant"] != call["tenant"]
            or row["project"] != call["project"]
            or row["id"] != digest({"provider_call_transport": value["call_id"]})[:32]
            or value["mode"] != "isolated_process"
            or type(value["pid"]) is not int
            or not 1 <= value["pid"] <= 2**32
            or type(value["parent_pid"]) is not int
            or not 1 <= value["parent_pid"] <= 2**32
            or not re.fullmatch(r"[a-f0-9]{64}", value["host_sha256"])
            or not re.fullmatch(r"[a-f0-9]{32}", value["process_token"])
        ):
            raise ValueError("Provider transport history integrity check failed")
