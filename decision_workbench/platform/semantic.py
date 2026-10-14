"""Scoped offline semantic jobs, immutable pair reviews and reservation binding."""

import copy
import json
import math
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

from decision_workbench.data import validate
from decision_workbench.semantic import row_hashes, enforce_edges
from decision_workbench.training.provenance import source_fingerprint
from decision_workbench.training.semantic_runner import MODEL_FILES
from .store import Conflict, digest, encode


def validate_profile(profile):
    fields = {
        "id",
        "tenants",
        "projects",
        "required",
        "model",
        "revision",
        "model_dir",
        "files_sha256",
        "packages",
        "max_tokens",
        "max_rows",
        "max_pairs",
        "timeout_seconds",
    }
    if not isinstance(profile, dict) or set(profile) != fields:
        raise ValueError("Invalid operator semantic profile fields")
    if not isinstance(profile["id"], str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,64}", profile["id"]
    ):
        raise ValueError("Invalid semantic profile identifier")
    for field in ("tenants", "projects"):
        if (
            not isinstance(profile[field], list)
            or not profile[field]
            or any(not isinstance(v, str) or not v for v in profile[field])
        ):
            raise ValueError("Semantic profiles require explicit tenant/project scope")
    if (
        type(profile["required"]) is not bool
        or not isinstance(profile["model_dir"], str)
        or not Path(profile["model_dir"]).is_absolute()
    ):
        raise ValueError(
            "Semantic model path and required flag must be operator configured"
        )
    if (
        not isinstance(profile["model"], str)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", profile["model"])
        or not isinstance(profile["revision"], str)
        or not re.fullmatch(r"[a-f0-9]{40}", profile["revision"])
    ):
        raise ValueError("Pin semantic model ownership and a 40-character revision")
    hashes = profile["files_sha256"]
    if (
        not isinstance(hashes, dict)
        or set(hashes) != set(MODEL_FILES)
        or any(
            not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{64}", v)
            for v in hashes.values()
        )
    ):
        raise ValueError("Pin every semantic model/tokenizer file SHA-256")
    if (
        not isinstance(profile["packages"], dict)
        or set(profile["packages"])
        != {"torch", "transformers", "numpy", "safetensors", "tokenizers"}
        or any(not isinstance(v, str) or not v for v in profile["packages"].values())
    ):
        raise ValueError("Pin semantic runtime dependency versions")
    for field, low, high in (
        ("max_tokens", 16, 512),
        ("max_rows", 4, 20000),
        ("max_pairs", 1, 10000),
        ("timeout_seconds", 10, 1800),
    ):
        if type(profile[field]) is not int or not low <= profile[field] <= high:
            raise ValueError("Semantic " + field + " is outside the supported bounds")
    return profile


def profile_for(tenant, project):
    path = os.environ.get("DMT_SEMANTIC_PROFILE_FILE")
    if not path:
        return None
    try:
        with Path(path).open("rb") as stream:
            body = stream.read(64001)
    except OSError as exc:
        raise ValueError("Configured semantic profile is unavailable") from exc
    if len(body) > 64000:
        raise ValueError("Operator semantic profile exceeds 64 KB")
    profile = validate_profile(json.loads(body))
    if tenant not in profile["tenants"] or (
        "*" not in profile["projects"] and project not in profile["projects"]
    ):
        return None
    return profile


def public_profile(profile):
    return {
        key: value
        for key, value in profile.items()
        if key not in ("model_dir", "tenants", "projects")
    }


def collect(store, tenant, project, config):
    if config.get("applicability") is True:
        from .gate_semantic import collect as combined_collect

        return combined_collect(store, tenant, project, config)
    rows, exclusions = [], []
    for dataset in config["datasets"]:
        payload = store.get(dataset, tenant, project, "dataset", include_rows=False)[
            "payload"
        ]
        if payload["sha256"] != config["hashes"][dataset]:
            raise ValueError("Pinned semantic dataset identity mismatch")
        for row in store.verify_dataset(dataset, tenant, project):
            validate(row, allow_missing_labels=True)
            if row["metadata"].get("review_status") == "rejected":
                exclusions.append(row["id"])
            else:
                validate(row, require_review=True)
                rows.append(row)
    base = None
    all_rows = list(rows)
    if config.get("base_snapshot"):
        base = store.get(config["base_snapshot"], tenant, project, "snapshot")[
            "payload"
        ]
        from decision_workbench.training.contracts import validate_splits

        validate_splits(base["splits"])
        if digest(base["splits"]) != base["sha256"]:
            raise ValueError("Semantic base snapshot integrity mismatch")
        all_rows += [row for part in base["splits"].values() for row in part]
    if not all_rows:
        raise ValueError("No eligible semantic records")
    hashes = row_hashes(all_rows)
    return (
        rows,
        base,
        sorted(all_rows, key=lambda row: row["id"]),
        digest(hashes),
        exclusions,
    )


def validate_report(payload):
    fields = {
        "report",
        "row_hashes",
        "datasets",
        "dataset_hashes",
        "base_snapshot",
        "excluded_ids",
        "profile",
        "job_id",
        "sha256",
    }
    report_fields = {
        "format",
        "input_sha256",
        "source_sha256",
        "threshold",
        "row_count",
        "compared_pairs",
        "pairs",
        "embedding_sha256",
        "dimension",
        "max_observed_tokens",
        "pooling",
        "device",
        "packages",
        "scope",
    }
    if isinstance(payload, dict) and "applicability" in payload:
        if payload["applicability"] is not True:
            raise ValueError("Combined semantic audit marker is malformed")
        fields.add("applicability")
    if (
        not isinstance(payload, dict)
        or set(payload) != fields
        or not isinstance(payload["report"], dict)
        or set(payload["report"]) != report_fields
    ):
        raise ValueError("Semantic report fields are incomplete or unexpected")
    report = payload["report"]
    profile = payload["profile"]
    if not isinstance(profile, dict) or set(profile) != {
        "id",
        "required",
        "model",
        "revision",
        "files_sha256",
        "packages",
        "max_tokens",
        "max_rows",
        "max_pairs",
        "timeout_seconds",
    }:
        raise ValueError("Semantic report has no frozen profile")
    validate_profile(
        {
            **profile,
            "model_dir": str(Path.cwd()),
            "tenants": ["validated"],
            "projects": ["validated"],
        }
    )
    if (
        any(
            type(report[field]) is not int
            for field in (
                "row_count",
                "compared_pairs",
                "dimension",
                "max_observed_tokens",
            )
        )
        or not 1 <= report["row_count"] <= profile["max_rows"]
        or not 1 <= report["dimension"] <= 4096
        or not 1 <= report["max_observed_tokens"] <= profile["max_tokens"]
        or type(report["threshold"]) not in (int, float)
        or not math.isfinite(report["threshold"])
        or not 0.5 <= report["threshold"] <= 1
        or report["pooling"] != "attention-mask-mean-l2"
        or report["device"] != "cpu"
        or report["packages"] != profile["packages"]
        or not isinstance(report["scope"], str)
        or not report["scope"]
    ):
        raise ValueError("Semantic report runtime/count/threshold contract failed")
    for field in ("source_sha256", "input_sha256", "embedding_sha256"):
        if not isinstance(report[field], str) or not re.fullmatch(
            r"[a-f0-9]{64}", report[field]
        ):
            raise ValueError("Semantic report fingerprint is malformed")
    if (
        not isinstance(report["pairs"], list)
        or len(report["pairs"]) > profile["max_pairs"]
    ):
        raise ValueError("Semantic report candidate capacity failed")
    if (
        digest({key: value for key, value in payload.items() if key != "sha256"})
        != payload["sha256"]
        or report["format"] != "domain-semantic-audit-v1"
    ):
        raise ValueError("Semantic report integrity mismatch")
    ids = payload["row_hashes"]
    if not isinstance(ids, dict) or any(
        not isinstance(key, str)
        or not key
        or not isinstance(sha, str)
        or not re.fullmatch(r"[a-f0-9]{64}", sha)
        for key, sha in ids.items()
    ):
        raise ValueError("Semantic row content fingerprints are malformed")
    if (
        digest(ids) != report["input_sha256"]
        or report["row_count"] != len(ids)
        or report["compared_pairs"] != len(ids) * (len(ids) - 1) // 2
    ):
        raise ValueError("Semantic report membership/comparison mismatch")
    seen = set()
    for pair in report["pairs"]:
        if not isinstance(pair, dict) or set(pair) != {
            "id",
            "left",
            "right",
            "similarity",
        }:
            raise ValueError("Semantic candidate pair fields are malformed")
        left, right = pair["left"], pair["right"]
        score = pair["similarity"]
        if (
            not isinstance(left, str)
            or not isinstance(right, str)
            or not left < right
            or left not in ids
            or right not in ids
            or pair["id"] != digest([left, right])
            or pair["id"] in seen
            or type(score) not in (int, float)
            or not math.isfinite(score)
            or not report["threshold"] <= score <= 1
        ):
            raise ValueError("Semantic candidate pair integrity mismatch")
        seen.add(pair["id"])
    if len(seen) > payload["profile"]["max_pairs"]:
        raise ValueError("Semantic report exceeds frozen candidate capacity")
    return report


def latest_reviews(store, tenant, project, audit_id):
    with store.transaction() as db:
        rows = db.execute(
            "SELECT * FROM entities WHERE tenant=? AND project=? AND kind='semantic-review' AND json_extract(payload,'$.audit_id')=? ORDER BY json_extract(payload,'$.revision')",
            (tenant, project, audit_id),
        ).fetchall()
    output = {}
    for row in rows:
        payload = json.loads(row["payload"])
        output[payload["pair_id"]] = {"id": row["id"], **payload}
    return output


def audit_page(service, principal, project, audit_id, limit=25, offset=0):
    from .paging import validate_page

    validate_page(limit, offset, 100)
    service.require(principal, ("admin", "editor", "reviewer"))
    service.project(principal, project)
    audit = service.store.get(audit_id, principal["tenant"], project, "semantic-audit")[
        "payload"
    ]
    report = validate_report(audit)
    _, _, rows, checksum, _ = collect(
        service.store,
        principal["tenant"],
        project,
        {
            "datasets": audit["datasets"],
            "hashes": audit["dataset_hashes"],
            "base_snapshot": audit["base_snapshot"],
            "applicability": audit.get("applicability", False),
        },
    )
    if checksum != report["input_sha256"]:
        raise ValueError("Semantic audit source content mismatch")
    sources = {row["id"]: row for row in rows}
    reviews = latest_reviews(service.store, principal["tenant"], project, audit_id)
    pairs = []
    for pair in report["pairs"][offset : offset + limit]:
        pairs.append(
            {
                **pair,
                "left_state": sources[pair["left"]]["state"],
                "right_state": sources[pair["right"]]["state"],
                "review": reviews.get(pair["id"]),
            }
        )
    return {
        "audit_id": audit_id,
        "sha256": audit["sha256"],
        "profile": audit["profile"],
        "threshold": report["threshold"],
        "row_count": report["row_count"],
        "compared_pairs": report["compared_pairs"],
        "candidate_pairs": len(report["pairs"]),
        "pending_pairs": sum(pair["id"] not in reviews for pair in report["pairs"]),
        "pairs": pairs,
        "limit": limit,
        "offset": offset,
    }


def review(
    service,
    principal,
    project,
    audit_id,
    pair_id,
    decision,
    expected_revision,
    expected_hash,
    note,
):
    service.require(principal, ("admin", "reviewer"))
    service.project(principal, project)
    audit = service.store.get(audit_id, principal["tenant"], project, "semantic-audit")[
        "payload"
    ]
    report = validate_report(audit)
    if expected_hash != audit["sha256"] or pair_id not in {
        pair["id"] for pair in report["pairs"]
    }:
        raise Conflict("Refresh the immutable semantic audit and candidate pair")
    if (
        decision not in ("related", "independent")
        or type(expected_revision) is not int
        or expected_revision < 0
        or not isinstance(note, str)
        or not note.strip()
        or len(note) > 1000
    ):
        raise ValueError(
            "Provide related/independent, the review revision and a reason"
        )
    identifier = uuid.uuid4().hex
    with service.store.transaction() as db:
        current = (
            db.execute(
                "SELECT max(json_extract(payload,'$.revision')) FROM entities WHERE tenant=? AND project=? AND kind='semantic-review' AND json_extract(payload,'$.audit_id')=? AND json_extract(payload,'$.pair_id')=?",
                (principal["tenant"], project, audit_id, pair_id),
            ).fetchone()[0]
            or 0
        )
        if current != expected_revision:
            raise Conflict("Another reviewer advanced this pair; refresh its review")
        payload = {
            "audit_id": audit_id,
            "audit_sha256": audit["sha256"],
            "pair_id": pair_id,
            "decision": decision,
            "revision": current + 1,
            "note": note.strip(),
            "reviewer": principal["actor"],
        }
        db.execute(
            "INSERT INTO entities VALUES (?,?,?,?,?,?)",
            (
                identifier,
                "semantic-review",
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
            "review.semantic-pair",
            identifier,
        )
    return service.store.get(
        identifier, principal["tenant"], project, "semantic-review"
    )


def freeze_review(store, tenant, project, audit_id, config):
    audit = store.get(audit_id, tenant, project, "semantic-audit")["payload"]
    report = validate_report(audit)
    committed_audit(store, tenant, project, audit_id, audit)
    if report["source_sha256"] != source_fingerprint():
        raise ValueError(
            "Semantic package source changed; run a new audit before reservation"
        )
    if audit["dataset_hashes"] != config["hashes"] or audit[
        "base_snapshot"
    ] != config.get("base_snapshot"):
        raise ValueError(
            "Semantic audit does not cover these exact dataset versions/base snapshot"
        )
    profile = profile_for(tenant, project)
    if profile is None or public_profile(profile) != audit["profile"]:
        raise ValueError("Semantic operator profile changed; run a new audit")
    reviews = latest_reviews(store, tenant, project, audit_id)
    if any(pair["id"] not in reviews for pair in report["pairs"]):
        raise ValueError("Review every semantic candidate pair before preparing data")
    identifiers = [reviews[pair["id"]]["id"] for pair in report["pairs"]]
    binding = {
        "audit_id": audit_id,
        "audit_sha256": audit["sha256"],
        "review_ids": identifiers,
        "review_hashes": {
            identifier: digest(
                store.get(identifier, tenant, project, "semantic-review")["payload"]
            )
            for identifier in identifiers
        },
    }
    resolve_binding(store, tenant, project, binding)
    return binding


def resolve_binding(store, tenant, project, binding):
    if set(binding["review_hashes"]) != set(binding["review_ids"]):
        raise ValueError("Frozen semantic review hash inventory mismatch")
    audit = store.get(binding["audit_id"], tenant, project, "semantic-audit")["payload"]
    report = validate_report(audit)
    committed_audit(store, tenant, project, binding["audit_id"], audit)
    if audit["sha256"] != binding["audit_sha256"]:
        raise ValueError("Frozen semantic audit hash mismatch")
    reviews = {}
    for identifier in binding["review_ids"]:
        item = store.get(identifier, tenant, project, "semantic-review")["payload"]
        if digest(item) != binding["review_hashes"].get(identifier):
            raise ValueError("Frozen semantic review content hash mismatch")
        if (
            item["audit_id"] != binding["audit_id"]
            or item["audit_sha256"] != audit["sha256"]
            or item["pair_id"] in reviews
            or item["decision"] not in ("related", "independent")
        ):
            raise ValueError("Frozen semantic review identity mismatch")
        reviews[item["pair_id"]] = item
    if set(reviews) != {pair["id"] for pair in report["pairs"]}:
        raise ValueError("Frozen semantic review coverage mismatch")
    related = [
        (pair["left"], pair["right"])
        for pair in report["pairs"]
        if reviews[pair["id"]]["decision"] == "related"
    ]
    return audit, related


def committed_audit(store, tenant, project, identifier, audit):
    job = store.job(audit["job_id"], tenant, project)
    report = audit["report"]
    if (
        job["kind"]
        != ("gate-semantic-audit" if audit.get("applicability") else "semantic-audit")
        or job["status"] != "succeeded"
        or not job.get("result")
        or job["result"].get("audit_id") != identifier
        or job["config"]["profile"] != audit["profile"]
        or job["config"]["source_sha256"] != report["source_sha256"]
        or job["config"]["threshold"] != report["threshold"]
        or job["config"]["datasets"] != audit["datasets"]
        or bool(job["config"].get("applicability")) != bool(audit.get("applicability"))
        or (
            audit.get("applicability")
            and job["config"].get("input_sha256") != report["input_sha256"]
        )
        or job["config"]["hashes"] != audit["dataset_hashes"]
        or job["config"].get("base_snapshot") != audit["base_snapshot"]
    ):
        raise ValueError("Semantic audit has no matching successfully completed job")


def verify_snapshot(store, tenant, project, snapshot, require_current_profile=True):
    binding = snapshot.get("semantic_contract")
    profile = profile_for(tenant, project) if require_current_profile else None
    if not binding:
        if profile and profile["required"]:
            raise ValueError(
                "This project requires a reviewed semantic reservation before use"
            )
        return
    audit, related = resolve_binding(store, tenant, project, binding)
    if require_current_profile and (
        profile is None or public_profile(profile) != audit["profile"]
    ):
        raise ValueError(
            "Semantic operator profile changed; create a successor audited study"
        )
    if (
        require_current_profile
        and audit["report"]["source_sha256"] != source_fingerprint()
    ):
        raise ValueError(
            "Semantic package source changed; create a successor audited study"
        )
    enforce_edges(snapshot["splits"], related)
    rows = [row for part in snapshot["splits"].values() for row in part]
    if {row["id"] for row in rows} != set(audit["row_hashes"]):
        raise ValueError("Semantic reserved membership differs from the audit")
    config = {
        "datasets": audit["datasets"],
        "hashes": audit["dataset_hashes"],
        "base_snapshot": audit["base_snapshot"],
        "applicability": audit.get("applicability", False),
    }
    original, base, all_rows, checksum, _ = collect(store, tenant, project, config)
    if checksum != audit["report"]["input_sha256"]:
        raise ValueError("Semantic source rows differ from the frozen audit")
    from decision_workbench.semantic import attach_links

    expected = attach_links(
        original, related, {key: binding[key] for key in ("audit_id", "audit_sha256")}
    )
    if base:
        expected += [row for part in base["splits"].values() for row in part]
    if row_hashes(rows) != row_hashes(expected):
        raise ValueError(
            "Semantic reserved content differs from the reviewed source rows"
        )


def run_audit(worker, job, cancelled):
    config = job["config"]
    tenant, project = job["tenant"], job["project"]
    profile = profile_for(tenant, project)
    if (
        profile is None
        or public_profile(profile) != config["profile"]
        or source_fingerprint() != config["source_sha256"]
    ):
        raise ValueError("Semantic job profile/source identity changed")
    _, _, rows, input_sha, exclusions = collect(worker.store, tenant, project, config)
    if config.get("applicability") and input_sha != config["input_sha256"]:
        raise ValueError("Combined semantic inputs changed after submission")
    if len(rows) > profile["max_rows"]:
        raise ValueError("Semantic row capacity exceeded; no sampled audit is accepted")
    folder = worker.store.folder / "jobs" / job["id"]
    folder.mkdir(parents=True, exist_ok=False)
    request = {
        "profile": profile,
        "threshold": config["threshold"],
        "source_sha256": config["source_sha256"],
        "input_sha256": input_sha,
        "rows": [{"id": row["id"], "state": row["state"]} for row in rows],
    }
    body = encode(request).encode()
    if len(body) > 64 * 1024 * 1024:
        raise ValueError("Semantic input exceeds 64 MiB")
    (folder / "semantic-request.json").write_bytes(body)
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        in (
            "SYSTEMROOT",
            "WINDIR",
            "PATH",
            "TEMP",
            "TMP",
            "HOME",
            "USERPROFILE",
            "LOCALAPPDATA",
        )
    }
    env.update(
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        HF_HUB_DISABLE_TELEMETRY="1",
        HF_HUB_DISABLE_IMPLICIT_TOKEN="1",
        TOKENIZERS_PARALLELISM="false",
        # PyTorch imports Dynamo even for eager CPU encoders. Its default
        # cache asks getpass for a username, which is unavailable in this
        # deliberately restricted Windows environment. Keep its cache private
        # to this job instead of inheriting identity/cache/provider variables.
        TORCHINDUCTOR_CACHE_DIR=str(folder / "torch-cache"),
    )
    from decision_workbench.training.resources import ResourceLease
    from decision_workbench.training.process_guard import leased_process_options

    worker.store.heartbeat(job, "Waiting for local semantic CPU capacity")
    with (
        ResourceLease(worker.store.folder / "semantic.lock").hold(
            timeout=30, cancelled=cancelled
        ) as semantic_lease,
        (folder / "semantic.log").open("w", encoding="utf-8") as log,
    ):
        process = subprocess.Popen(
            [
                os.environ.get("DMT_SEMANTIC_PYTHON") or sys.executable,
                "-m",
                "decision_workbench.training.semantic_runner",
                "--request",
                str(folder / "semantic-request.json"),
                "--out",
                str(folder / "semantic-report.json"),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            **leased_process_options(semantic_lease, env),
            cwd=Path(__file__).resolve().parents[2],
            shell=False,
        )
        deadline = time.monotonic() + profile["timeout_seconds"]
        try:
            while process.poll() is None:
                worker.store.heartbeat(
                    job, "Embedding locally and checking every input pair"
                )
                if cancelled.is_set():
                    raise Conflict("Cancelled")
                if time.monotonic() > deadline:
                    raise ValueError("Semantic child exceeded its operator deadline")
                cancelled.wait(0.2)
            if process.returncode:
                raise ValueError(
                    "Semantic runner refused the profile, input or capacity; inspect its private job log"
                )
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=15)
    if cancelled.is_set():
        raise Conflict("Cancelled")
    path = folder / "semantic-report.json"
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Semantic report size limit exceeded")
    report = json.loads(path.read_bytes())
    payload = {
        "report": report,
        "row_hashes": row_hashes(rows),
        "datasets": config["datasets"],
        "dataset_hashes": config["hashes"],
        "base_snapshot": config.get("base_snapshot"),
        "excluded_ids": exclusions,
        "profile": config["profile"],
        "job_id": job["id"],
    }
    payload["sha256"] = digest(payload)
    if config.get("applicability"):
        payload["applicability"] = True
        payload["sha256"] = digest({k: v for k, v in payload.items() if k != "sha256"})
    validate_report(payload)
    if (
        report["source_sha256"] != config["source_sha256"]
        or report["threshold"] != config["threshold"]
        or report["input_sha256"] != input_sha
    ):
        raise ValueError("Semantic child result differs from its frozen request")
    audit = worker.store.add(
        "semantic-audit", tenant, project, job["actor"], payload, job=job
    )
    return {
        "audit_id": audit["id"],
        "candidate_pairs": len(report["pairs"]),
        "compared_pairs": report["compared_pairs"],
        "row_count": report["row_count"],
    }
