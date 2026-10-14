"""Combined audits keep positive reservations immutable and qualify gate evidence."""

from decision_workbench.gate_data import validate_record
from decision_workbench.semantic import row_hashes, attach_links, enforce_edges
from .store import digest


def collect(store, tenant, project, config):
    from .semantic import verify_snapshot

    positive = store.get(config["base_snapshot"], tenant, project, "snapshot")[
        "payload"
    ]
    if digest(positive["splits"]) != positive["sha256"]:
        raise ValueError("Combined semantic positive snapshot integrity mismatch")
    verify_snapshot(store, tenant, project, positive, require_current_profile=False)
    questions = positive["splits"]["train"][0]["questions"]
    rows, exclusions = [], []
    for identifier in config["datasets"]:
        payload = store.get(
            identifier, tenant, project, "gate-dataset", include_rows=False
        )["payload"]
        if payload["sha256"] != config["hashes"][identifier]:
            raise ValueError(
                "Combined semantic applicability collection identity mismatch"
            )
        for row in store.verify_dataset(
            identifier, tenant, project, kind="gate-dataset"
        ):
            validate_record(row, questions)
            if row["metadata"]["review_status"] == "rejected":
                exclusions.append(row["id"])
                continue
            validate_record(row, questions, require_review=True)
            if not row["metadata"].get("reviewer") or not row["metadata"].get(
                "review_note"
            ):
                raise ValueError(
                    "Combined semantic evidence needs authenticated review attribution"
                )
            rows.append(row)
    if not rows:
        raise ValueError(
            "Combined semantic audit needs approved applicability evidence"
        )
    all_rows = rows + [row for role in positive["splits"].values() for row in role]
    hashes = row_hashes(all_rows)
    return (
        rows,
        positive,
        sorted(all_rows, key=lambda r: r["id"]),
        digest(hashes),
        exclusions,
    )


def freeze(service, principal, project, config):
    import re
    from .semantic import profile_for, public_profile, verify_snapshot
    from decision_workbench.training.provenance import source_fingerprint

    ids = config["datasets"]
    if (
        not isinstance(config["snapshot"], str)
        or not re.fullmatch(r"[a-f0-9]{32}", config["snapshot"])
        or not isinstance(ids, list)
        or not 1 <= len(ids) <= 20
        or any(
            not isinstance(i, str) or not re.fullmatch(r"[a-f0-9]{32}", i) for i in ids
        )
        or len(set(ids)) != len(ids)
    ):
        raise ValueError(
            "Choose a positive snapshot and 1–20 unique applicability versions"
        )
    profile = profile_for(principal["tenant"], project)
    if profile is None:
        raise ValueError("Configure a scoped semantic profile before a combined audit")
    positive = service.store.get(
        config["snapshot"], principal["tenant"], project, "snapshot"
    )["payload"]
    verify_snapshot(service.store, principal["tenant"], project, positive)
    frozen = {
        "datasets": ids,
        "base_snapshot": config["snapshot"],
        "applicability": True,
        "hashes": {
            i: service.store.get(
                i, principal["tenant"], project, "gate-dataset", include_rows=False
            )["payload"]["sha256"]
            for i in ids
        },
        "threshold": config["threshold"],
        "profile": public_profile(profile),
        "source_sha256": source_fingerprint(),
    }
    frozen["input_sha256"] = collect(
        service.store, principal["tenant"], project, frozen
    )[3]
    return frozen


def binding_for(store, tenant, project, audit_id, config):
    from .semantic import freeze_review

    audit = store.get(audit_id, tenant, project, "semantic-audit")["payload"]
    if (
        audit.get("applicability") is not True
        or audit["datasets"] != config["datasets"]
    ):
        raise ValueError(
            "Select a combined audit for these exact ordered applicability versions"
        )
    return freeze_review(
        store,
        tenant,
        project,
        audit_id,
        {"hashes": config["hashes"], "base_snapshot": config["snapshot"]},
    )


def validate_binding(store, tenant, project, binding, config, current=True):
    from .semantic import resolve_binding, profile_for, public_profile
    from decision_workbench.training.provenance import source_fingerprint

    audit, related = resolve_binding(store, tenant, project, binding)
    if (
        audit.get("applicability") is not True
        or audit["datasets"] != config["datasets"]
        or audit["dataset_hashes"] != config["hashes"]
        or audit["base_snapshot"] != config["snapshot"]
    ):
        raise ValueError(
            "Combined semantic binding differs from the paired reservation"
        )
    if current:
        profile = profile_for(tenant, project)
        if (
            profile is None
            or public_profile(profile) != audit["profile"]
            or audit["report"]["source_sha256"] != source_fingerprint()
        ):
            raise ValueError(
                "Combined semantic profile/source changed; audit a successor study"
            )
    return audit, related


def materialize(store, tenant, project, positive, raw_negative, binding):
    from .semantic import resolve_binding

    audit, related = resolve_binding(store, tenant, project, binding)
    if audit.get("applicability") is not True:
        raise ValueError("Applicability reservation needs a combined semantic audit")
    _, _, originals, checksum, _ = collect(
        store,
        tenant,
        project,
        {
            "datasets": audit["datasets"],
            "hashes": audit["dataset_hashes"],
            "base_snapshot": audit["base_snapshot"],
            "applicability": True,
        },
    )
    if (
        checksum != audit["report"]["input_sha256"]
        or row_hashes(originals) != audit["row_hashes"]
    ):
        raise ValueError("Combined semantic content differs from its frozen audit")
    p_rows = [row for role in positive.values() for row in role]
    if row_hashes(p_rows + raw_negative) != audit["row_hashes"]:
        raise ValueError("Combined semantic reservation membership/content mismatch")
    mark = {key: binding[key] for key in ("audit_id", "audit_sha256")}
    positives = {row["id"] for row in p_rows}
    # Cross-collection links live on negatives. Positive-only contracts cannot
    # reference negative IDs, and original positive snapshots are never mutated.
    p_pairs = [(a, b) for a, b in related if a in positives and b in positives]
    p_copy = {r["id"]: r for r in attach_links(p_rows, p_pairs, mark)}
    prepared_positive = {
        role: [p_copy[r["id"]] for r in rows] for role, rows in positive.items()
    }
    n_copy = attach_links(raw_negative, related, mark)
    from decision_workbench.gate_data import reserve

    prepared_negative = reserve(prepared_positive, n_copy, related)
    enforce_edges(
        {role: prepared_positive[role] + prepared_negative[role] for role in positive},
        related,
    )
    return prepared_positive, prepared_negative
