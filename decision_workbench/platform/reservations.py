"""Owner-reviewed immutable reservation plans for local and remote studies."""

import copy
import json
import time

from decision_workbench.data import validate
from decision_workbench.reservations import MAX_ROWS, inventory, normalize, split
from decision_workbench.semantic import attach_links, row_hashes
from .store import Conflict, digest, encode


def preserve_roles(db, tenant, project, splits):
    """A new study cannot turn known project holdouts into training or another role."""
    from .taxonomy import matching_ids, footprints

    assigned = {row["id"]: role for role, part in splits.items() for row in part}
    rows = [row for part in splits.values() for row in part]
    for saved in db.execute(
        "SELECT payload FROM entities WHERE tenant=? AND project=? AND kind='snapshot'",
        (tenant, project),
    ):
        prior = json.loads(saved["payload"])
        for role, part in prior["splits"].items():
            known = matching_ids(rows, footprints(part))
            if any(assigned[identifier] != role for identifier in known):
                raise ValueError(
                    "An existing project reservation fixes these source families to their original roles; preserve them or use independent data in a new project"
                )


def inputs(store, tenant, project, config, require_current=True):
    from .semantic import profile_for, public_profile, resolve_binding

    if set(config) != {"datasets", "hashes", "semantic_contract"}:
        raise ValueError("Invalid frozen reservation inputs")
    datasets = config["datasets"]
    if (
        not isinstance(datasets, list)
        or not 1 <= len(datasets) <= 20
        or any(not isinstance(v, str) or not v for v in datasets)
        or len(set(datasets)) != len(datasets)
        or set(config["hashes"]) != set(datasets)
    ):
        raise ValueError("Choose 1–20 unique immutable dataset versions")
    spec = store.get(project, tenant, project, "project")["payload"]
    rows = []
    for identifier in datasets:
        data = store.get(identifier, tenant, project, "dataset", include_rows=False)[
            "payload"
        ]
        if data["sha256"] != config["hashes"][identifier]:
            raise ValueError("Reservation source identity changed")
        for row in store.verify_dataset(identifier, tenant, project):
            if row["metadata"].get("review_status") == "rejected":
                continue
            validate(row, require_review=True)
            if row["questions"] != spec["questions"]:
                raise ValueError("Reservation questions differ from the project schema")
            rows.append(row)
            if len(rows) > MAX_ROWS:
                raise ValueError(
                    "Explicit reservations support up to 20,000 eligible records; no sampling is applied"
                )
    related = []
    profile = profile_for(tenant, project) if require_current else None
    binding = config["semantic_contract"]
    if binding:
        audit, related = resolve_binding(store, tenant, project, binding)
        if require_current and (
            profile is None or public_profile(profile) != audit["profile"]
        ):
            raise ValueError("Semantic profile changed before reservation")
        rows = attach_links(
            rows, related, {k: binding[k] for k in ("audit_id", "audit_sha256")}
        )
    elif profile and profile["required"]:
        raise ValueError("Review a semantic audit before making this reservation")
    return spec, rows, related


def selection(service, principal, project, datasets, semantic_audit=None):
    service.require(principal, ("admin",))
    service.project(principal, project)
    if (
        not isinstance(datasets, list)
        or not 1 <= len(datasets) <= 20
        or any(not isinstance(v, str) or not v for v in datasets)
        or len(set(datasets)) != len(datasets)
    ):
        raise ValueError("Choose 1–20 unique immutable dataset versions")
    config = {
        "datasets": list(datasets),
        "hashes": {
            identifier: service.store.get(
                identifier, principal["tenant"], project, "dataset", include_rows=False
            )["payload"]["sha256"]
            for identifier in datasets
        },
        "semantic_contract": None,
    }
    if semantic_audit:
        from .semantic import freeze_review

        config["semantic_contract"] = freeze_review(
            service.store, principal["tenant"], project, semantic_audit, config
        )
    return config


def analyze(
    store, tenant, project, config, policy=None, forced_ids=None, require_current=True
):
    from .taxonomy import training_only_ids

    spec, rows, related = inputs(store, tenant, project, config, require_current)
    forced = sorted(
        training_only_ids(store, tenant, project, rows)
        if forced_ids is None
        else forced_ids
    )
    if any(not isinstance(v, str) for v in forced) or not set(forced) <= {
        r["id"] for r in rows
    }:
        raise ValueError("Invalid frozen training-only family inventory")
    summary = inventory(rows, related, forced)
    context = {
        "format": "domain-reservation-preview-v1",
        "configuration": config,
        "schema_sha256": spec["schema_sha256"],
        "input_sha256": digest(row_hashes(rows)),
        "forced_ids": forced,
    }
    preview = {**summary, "selection_sha256": digest(context)}
    if policy is None:
        return preview, None, context
    policy = normalize(policy)
    splits = split(rows, policy, related, forced)
    if require_current:
        with store.transaction() as db:
            preserve_roles(db, tenant, project, splits)
    assigned = {r["id"]: role for role, part in splits.items() for r in part}
    context.update(policy=policy, assignment_sha256=digest(assigned))
    preview.update(
        policy=policy,
        counts={role: len(part) for role, part in splits.items()},
        real_counts={
            role: sum(r["metadata"]["origin"] == "real" for r in part)
            for role, part in splits.items()
        },
        sha256=digest(context),
    )
    return preview, splits, context


def preview(service, principal, project, datasets, policy=None, semantic_audit=None):
    config = selection(service, principal, project, datasets, semantic_audit)
    return analyze(service.store, principal["tenant"], project, config, policy)[0]


def freeze(service, principal, project, config, request_key, authorize=None):
    service.require(principal, ("admin",))
    if config.get("base_snapshot") or config.get("split_strategy", "auto") != "auto":
        raise ValueError(
            "Explicit policies create a first reservation; existing holdouts cannot be reassigned"
        )
    if config.get("reservation_approved") is not True:
        raise ValueError(
            "Preview and explicitly approve the reservation before submitting it"
        )
    chosen = {
        k: copy.deepcopy(config.get(k))
        for k in ("datasets", "hashes", "semantic_contract")
    }
    result, splits, context = analyze(
        service.store,
        principal["tenant"],
        project,
        chosen,
        config["reservation_policy"],
    )
    if result["sha256"] != config.get("expected_reservation_sha256"):
        raise Conflict(
            "Reservation inputs or assignments changed; preview and approve them again"
        )
    tenant = principal["tenant"]
    identifier = digest(
        {"reservation": project, "tenant": tenant, "request_key": request_key}
    )[:32]
    payload = {
        "format": "domain-reservation-plan-v1",
        "definition": context,
        "preview_sha256": result["sha256"],
        "approved_by": principal["actor"],
        "request_key": request_key,
    }
    payload["sha256"] = digest(payload)
    from .taxonomy import lineage, refuse_exposed_holdouts

    ancestry = lineage(service.store, tenant, project)

    def insert(db):
        if authorize:
            authorize()
        refuse_exposed_holdouts(db, tenant, ancestry, splits)
        preserve_roles(db, tenant, project, splits)
        db.execute(
            "INSERT INTO entities VALUES (?,?,?,?,?,?)",
            (
                identifier,
                "reservation-plan",
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
            "confirm.reservation-plan",
            identifier,
        )
        if authorize:
            authorize()

    config = {
        k: v
        for k, v in config.items()
        if k
        not in {
            "reservation_policy",
            "reservation_approved",
            "expected_reservation_sha256",
        }
    }
    config["reservation_plan"] = {"id": identifier, "sha256": payload["sha256"]}
    return config, insert


def load_plan(store, tenant, project, binding, require_current=True):
    if not isinstance(binding, dict) or set(binding) != {"id", "sha256"}:
        raise ValueError("Invalid reservation plan binding")
    plan = store.get(binding["id"], tenant, project, "reservation-plan")["payload"]
    if (
        not isinstance(plan, dict)
        or set(plan)
        != {
            "format",
            "definition",
            "preview_sha256",
            "approved_by",
            "request_key",
            "sha256",
        }
        or plan["format"] != "domain-reservation-plan-v1"
        or plan["sha256"] != binding["sha256"]
        or digest({k: v for k, v in plan.items() if k != "sha256"}) != plan["sha256"]
        or not isinstance(plan["approved_by"], str)
        or not plan["approved_by"].strip()
    ):
        raise ValueError("Reservation plan integrity check failed")
    definition = plan["definition"]
    result, splits, expected = analyze(
        store,
        tenant,
        project,
        definition["configuration"],
        definition["policy"],
        definition["forced_ids"],
        require_current,
    )
    if definition != expected or result["sha256"] != plan["preview_sha256"]:
        raise ValueError("Reservation plan source or assignments changed")
    with store.transaction() as db:
        job = db.execute(
            "SELECT kind,config,actor FROM jobs WHERE tenant=? AND project=? AND request_key=?",
            (tenant, project, plan["request_key"]),
        ).fetchone()
    job_config = json.loads(job["config"]) if job else {}
    if (
        not job
        or job["kind"] != "prepare"
        or job["actor"] != plan["approved_by"]
        or job_config.get("reservation_plan") != binding
        or any(
            job_config.get(k) != definition["configuration"][k]
            for k in ("datasets", "hashes", "semantic_contract")
        )
    ):
        raise ValueError("Reservation plan has no matching approved preparation job")
    return plan, splits


def verify_snapshot(store, tenant, project, snapshot, require_current=True, seen=None):
    seen = set() if seen is None else seen
    binding = snapshot.get("reservation_plan")
    # Bind persisted snapshots to their actual preparation job even if a plan
    # field was removed while leaving the split-only content hash unchanged.
    with store.transaction() as db:
        jobs = db.execute(
            "SELECT j.config FROM jobs j JOIN entities e ON e.id=json_extract(j.result,'$.snapshot') "
            "AND e.tenant=j.tenant AND e.project=j.project AND e.kind='snapshot' "
            "WHERE j.tenant=? AND j.project=? AND j.kind='prepare' AND e.payload=?",
            (tenant, project, encode(snapshot)),
        ).fetchall()
    if any(
        json.loads(job["config"]).get("reservation_plan") != binding for job in jobs
    ):
        raise ValueError("Reservation plan differs from its preparation job")
    if binding or snapshot.get("reservation_protocol") == "fixed-project-roles-v1":
        with store.transaction() as db:
            preserve_roles(db, tenant, project, snapshot["splits"])
    if binding:
        plan, expected = load_plan(store, tenant, project, binding, require_current)
        config = plan["definition"]["configuration"]
        if (
            snapshot.get("base_snapshot")
            or snapshot["splits"] != expected
            or snapshot["datasets"] != config["datasets"]
            or snapshot["dataset_hashes"] != config["hashes"]
            or snapshot.get("semantic_contract") != config["semantic_contract"]
        ):
            raise ValueError("Reserved content differs from the owner-confirmed plan")
    base_id = snapshot.get("base_snapshot")
    if base_id:
        if base_id in seen or len(seen) >= 64:
            raise ValueError("Cyclic or oversized reservation ancestry")
        seen.add(base_id)
        base = store.get(base_id, tenant, project, "snapshot")["payload"]
        if digest(base["splits"]) != base["sha256"]:
            raise ValueError("Base reservation integrity check failed")
        verify_snapshot(store, tenant, project, base, require_current, seen)
        if any(
            snapshot["splits"][role] != base["splits"][role]
            for role in ("validation", "calibration", "test")
        ):
            raise ValueError("Synthetic extension changed reserved real holdouts")
        current = {r["id"]: r for r in snapshot["splits"]["train"]}
        if any(current.get(r["id"]) != r for r in base["splits"]["train"]):
            raise ValueError("Synthetic extension changed original training content")
