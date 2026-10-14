"""Bind authenticated paired applicability reservations to local Vega training."""

import hashlib
import math
import re
from types import SimpleNamespace

from decision_workbench.data import jsonl
from decision_workbench.training.vega_local import PROFILE, DEFAULTS, request_body
from decision_workbench.training.contracts import SPLITS
from decision_workbench.training.provenance import source_fingerprint
from decision_workbench.training.vega_study import validate_settings
from .store import digest

INPUTS = {"snapshot", "backend", "gate_snapshot"} | set(PROFILE) | set(DEFAULTS)
POLICY = {
    "min_positive_accepted_fraction",
    "max_negative_false_open_rate",
    "min_negative_test_rows",
}


def validate_policy(value):
    if not isinstance(value, dict) or set(value) != POLICY:
        raise ValueError(
            "Vega needs explicit positive coverage, negative false-open and negative test-count criteria"
        )
    for key in POLICY - {"min_negative_test_rows"}:
        number = value[key]
        if (
            type(number) not in (int, float)
            or not math.isfinite(number)
            or not 0 <= number <= 1
        ):
            raise ValueError("Vega rate criteria must be finite numbers in [0,1]")
    count = value["min_negative_test_rows"]
    if type(count) is not int or not 10 <= count <= 20000:
        raise ValueError(
            "Vega needs an owner negative test-count floor between 10 and 20000"
        )
    return value


def validate_references(config):
    for key in ("snapshot", "gate_snapshot"):
        value = config.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
            raise ValueError(
                "Vega snapshot references must be 32-character lowercase identifiers"
            )


def check_negative_floor(gate, questions, criteria):
    limits = validate_policy(criteria.get("vega"))
    if any(
        sum(name in row["negative_for"] for row in gate["splits"]["test"])
        < limits["min_negative_test_rows"]
        for name in questions
    ):
        raise ValueError(
            "Vega needs the owner minimum of reserved negative test rows for every question"
        )


def paired(service, tenant, project, config, positive):
    from .semantic import profile_for

    profile = profile_for(tenant, project)
    gate = service.gates.verify_snapshot(tenant, project, config["gate_snapshot"])
    if profile and profile["required"] and not gate.get("semantic_contract"):
        raise ValueError(
            "Vega requires a reviewed combined positive/applicability semantic reservation"
        )
    if gate.get("semantic_contract"):
        from .gate_semantic import validate_binding

        validate_binding(
            service.store,
            tenant,
            project,
            gate["semantic_contract"],
            {
                "snapshot": gate["snapshot"],
                "datasets": gate["datasets"],
                "hashes": gate["dataset_hashes"],
            },
        )
    job = service.store.job(gate["job_id"], tenant, project)
    if (
        job["status"] != "succeeded"
        or job["result"].get("gate_snapshot") != config["gate_snapshot"]
    ):
        raise ValueError(
            "Vega requires a successfully completed applicability reservation"
        )
    if (
        gate["snapshot"] != config["snapshot"]
        or gate["snapshot_sha256"] != positive["sha256"]
    ):
        raise ValueError(
            "Vega applicability data belong to a different positive snapshot"
        )
    from .semantic import verify_snapshot
    from .taxonomy import verify_snapshot as verify_taxonomy

    verify_snapshot(service.store, tenant, project, positive)
    verify_taxonomy(service.store, tenant, project, positive)
    return gate


def input_hashes(positive, negative):
    return {
        "data_sha256": {
            role: hashlib.sha256(jsonl(positive[role]).encode()).hexdigest()
            for role in SPLITS
        },
        "gate_data_sha256": {
            role: hashlib.sha256(jsonl(negative[role]).encode()).hexdigest()
            for role in SPLITS
        },
    }


def freeze(service, principal, project, config):
    validate_references(config)
    if set(config) - INPUTS or not all(
        config.get(key) for key in ("snapshot", "gate_snapshot")
    ):
        raise ValueError(
            "Use Vega options and a paired positive/applicability snapshot"
        )
    selected = {**PROFILE, **DEFAULTS, **config}
    if any(selected[key] != value for key, value in PROFILE.items()):
        raise ValueError(
            "Vega requires the separately qualified pinned 0.8B engine and backbone profile"
        )
    if type(selected["offline"]) is not bool:
        raise ValueError("Vega offline selection must be a boolean")
    positive = service.store.get(
        selected["snapshot"], principal["tenant"], project, "snapshot"
    )["payload"]
    gate = paired(service, principal["tenant"], project, selected, positive)
    settings = validate_settings(
        SimpleNamespace(**selected),
        gate["positive_splits"],
        gate["splits"],
        positive["splits"]["train"][0]["questions"],
    )
    policy = service.store.release_policy(principal["tenant"], project)["policy"]
    if not policy:
        raise ValueError("Set explicit owner acceptance criteria before Vega training")
    check_negative_floor(
        gate, positive["splits"]["train"][0]["questions"], policy["payload"]["criteria"]
    )
    selected["vega_contract"] = {
        "tenant": principal["tenant"],
        "project": project,
        "positive_sha256": positive["sha256"],
        "gate_sha256": gate["sha256"],
        "schema_sha256": gate["schema_sha256"],
        "preparation_source_sha256": gate["source_sha256"],
        "source_sha256": source_fingerprint(),
        **input_hashes(gate["positive_splits"], gate["splits"]),
        "preflight": settings,
    }
    return selected


def recheck(service, job, positive, questions, require_current_source=True):
    config = job["config"]
    gate = paired(service, job["tenant"], job["project"], config, positive)
    binding = config["vega_contract"]
    if (
        binding["tenant"] != job["tenant"]
        or binding["project"] != job["project"]
        or (require_current_source and binding["source_sha256"] != source_fingerprint())
    ):
        raise ValueError(
            "Vega study source or scoped identity differs from its frozen request"
        )
    if (
        binding["positive_sha256"] != positive["sha256"]
        or binding["gate_sha256"] != gate["sha256"]
        or binding["schema_sha256"] != digest(questions)
        or binding["preparation_source_sha256"] != gate["source_sha256"]
    ):
        raise ValueError("Vega paired input identity changed after submission")
    if any(
        binding[key] != value
        for key, value in input_hashes(gate["positive_splits"], gate["splits"]).items()
    ):
        raise ValueError("Vega serialized inputs differ from the frozen study")
    preflight = validate_settings(
        SimpleNamespace(**config), gate["positive_splits"], gate["splits"], questions
    )
    if preflight != binding["preflight"]:
        raise ValueError("Vega calibration or feature-cache preflight changed")
    return gate


def checked_model(service, model, questions):
    """Replay reviewed reservations for release/recovery without rebinding old source."""
    config = model["configuration"]
    binding = config["vega_contract"]
    job = service.store.job(model["job_id"], binding["tenant"], binding["project"])
    if (
        job["config"] != config
        or job["status"] != "succeeded"
        or job["kind"] not in ("train", "remote-import")
        or model["snapshot"] != config["snapshot"]
    ):
        raise ValueError("Vega model differs from its completed frozen training job")
    if job["kind"] == "remote-import":
        from .remote import study_entity

        study = study_entity(
            service.store, job["tenant"], job["project"], config["study"]
        )["payload"]
        if (
            config["remote_request_sha256"] != study["request_sha256"]
            or any(
                config.get(k) != v for k, v in study["request"]["configuration"].items()
            )
            or model["report"].get("admission", {}).get("study_request_sha256")
            != study["request_sha256"]
            or model["report"].get("admission", {}).get("source_manifest_sha256")
            != config["manifest_sha256"]
        ):
            raise ValueError(
                "Vega admission lost its frozen study/source-export identity"
            )
    positive = service.store.get(
        config["snapshot"], job["tenant"], job["project"], "snapshot"
    )["payload"]
    gate = recheck(service, job, positive, questions, require_current_source=False)
    report = model["report"]
    if (
        model["snapshot_sha256"] != positive["sha256"]
        or report.get("counts")
        != {role: len(rows) for role, rows in positive["splits"].items()}
        or report.get("gate_counts") != gate["counts"]
    ):
        raise ValueError("Vega registered data counts differ from their reservations")
    for role in ("validation", "calibration", "test"):
        for name in questions:
            expected = sum(name in row["negative_for"] for row in gate["splits"][role])
            if (
                report.get("applicability", {})
                .get(role, {})
                .get(name, {})
                .get("negative", {})
                .get("count")
                != expected
            ):
                raise ValueError(
                    "Vega per-question applicability count differs from its reservation"
                )


def checked_report(report, config):
    binding = config["vega_contract"]
    if (
        report.get("backend") != "vega"
        or report.get("source_sha256") != binding["source_sha256"]
    ):
        raise ValueError("Vega export backend/source differs from the frozen job")
    for key in PROFILE:
        if report.get(key) != config[key]:
            raise ValueError(
                "Vega engine/backbone identity differs from its frozen job"
            )
    for key in ("data_sha256", "gate_data_sha256"):
        if report.get(key) != binding[key]:
            raise ValueError("Vega export data differ from the frozen paired inputs")
    if any(report.get("configuration", {}).get(key) != config[key] for key in DEFAULTS):
        raise ValueError("Vega export training options differ from the frozen job")
    if "remote_request_sha256" in config:
        if (
            report.get("remote_study_sha256") != config["remote_request_sha256"]
            or report.get("local_study_sha256") is not None
        ):
            raise ValueError("Vega export lacks the exact remote frozen-study request")
    elif (
        report.get("local_study_sha256")
        != hashlib.sha256(request_body(config).encode()).hexdigest()
    ):
        raise ValueError("Vega export lacks the exact local frozen-study request")
    if (
        report.get("feature_cache") != binding["preflight"]
        or report.get("export_qualification", {}).get("same_uncertainty") is not True
    ):
        raise ValueError(
            "Vega export lacks its reserved calibration or uncertainty qualification"
        )


def release_gates(report, criteria, questions):
    limits = validate_policy(criteria.get("vega"))
    metrics = report.get("applicability", {}).get("test")
    if not isinstance(metrics, dict) or set(metrics) != set(questions):
        raise ValueError("Vega applicability test does not cover every question")
    for name, value in metrics.items():
        try:
            rates = [value["positive_accepted_fraction"]]
            for label in ("positive", "negative"):
                if type(value[label]["count"]) is not int or value[label]["count"] < 1:
                    raise ValueError(
                        "Vega applicability test counts must be positive integers"
                    )
                rates.extend(value[label][key] for key in ("open_rate", "error_rate"))
            if any(
                type(rate) not in (int, float)
                or not math.isfinite(rate)
                or not 0 <= rate <= 1
                for rate in rates
            ):
                raise ValueError(
                    "Vega applicability rates must be finite probabilities"
                )
            if (
                value["positive"]["count"] != report["test"][name]["count"]
                or value["positive"]["count"] != report["counts"]["test"]
                or value["negative"]["count"] > report["gate_counts"]["test"]
            ):
                raise ValueError(
                    "Vega applicability counts disagree with the test reservation"
                )
            if (
                not math.isclose(
                    value["positive"]["error_rate"],
                    1 - value["positive"]["open_rate"],
                    abs_tol=1e-9,
                )
                or not math.isclose(
                    value["negative"]["error_rate"],
                    value["negative"]["open_rate"],
                    abs_tol=1e-9,
                )
                or value["positive_accepted_fraction"]
                > value["positive"]["open_rate"] + 1e-9
            ):
                raise ValueError("Vega applicability acceptance/error rates disagree")
        except (KeyError, TypeError) as exc:
            raise ValueError("Malformed Vega applicability evaluation") from exc
        if (
            value["negative"]["count"] < limits["min_negative_test_rows"]
            or value["negative"]["error_rate"] > limits["max_negative_false_open_rate"]
            or value["positive_accepted_fraction"]
            < limits["min_positive_accepted_fraction"]
        ):
            raise ValueError(
                "Vega fails its frozen positive coverage or negative applicability gates"
            )
