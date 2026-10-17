"""Bind local Liquid training and release to the project's frozen domain study."""

import hashlib
import json
import math

from decision_workbench.data import jsonl
from decision_workbench.training.contracts import SPLITS, validate_splits
from decision_workbench.training.liquid_local import (
    DEFAULTS,
    PROFILE,
    INPUTS,
    request_body,
    settings,
)
from decision_workbench.training.provenance import source_fingerprint


def study_for(config, snapshot, questions, *, source=None):
    splits = snapshot["splits"]
    if validate_splits(splits) != questions:
        raise ValueError("Liquid snapshot questions differ from the project")
    if len(splits["calibration"]) < 8:
        raise ValueError(
            "Liquid needs at least eight reserved real calibration records"
        )
    for split in ("train", "validation"):
        for row in splits[split]:
            for target in row["gold"].values():
                values = list(target["probabilities"].values())
                if values.count(1) != 1 or any(value not in (0, 1) for value in values):
                    raise ValueError(
                        "Liquid SFT needs one-hot training/validation targets; soft labels are never rounded"
                    )
    return {
        "format": "domain-liquid-local-study-v1",
        "snapshot": config["snapshot"],
        "snapshot_sha256": snapshot["sha256"],
        "schema_sha256": snapshot["schema_sha256"],
        "configuration": settings(
            {k: config[k] for k in set(PROFILE) | set(DEFAULTS) if k in config}
        ),
        "source_sha256": source or source_fingerprint(),
        "data_sha256": {
            role: hashlib.sha256(jsonl(splits[role]).encode()).hexdigest()
            for role in SPLITS
        },
        "counts": {role: len(splits[role]) for role in SPLITS},
    }


def freeze(service, principal, project, config):
    if set(config) - INPUTS:
        raise ValueError("Training options do not apply to Liquid d1-3B")
    snapshot = service.store.get(
        config["snapshot"], principal["tenant"], project, "snapshot"
    )["payload"]
    questions = service.project(principal, project)["payload"]["questions"]
    study = study_for(config, snapshot, questions)
    return {
        "snapshot": config["snapshot"],
        **study["configuration"],
        "liquid_study": study,
    }


def recheck(config, snapshot, questions):
    if config.get("liquid_study") != study_for(config, snapshot, questions):
        raise ValueError(
            "Liquid source, configuration or reservation changed after queueing"
        )


def checked_report(report, config):
    study = config.get("liquid_study", {})
    if (
        report.get("backend") != "liquid-trl"
        or report.get("model") != PROFILE["model"]
        or report.get("revision") != PROFILE["revision"]
        or report.get("source_sha256") != study.get("source_sha256")
        or report.get("data_sha256") != study.get("data_sha256")
        or report.get("counts") != study.get("counts")
        or report.get("local_study_sha256")
        != hashlib.sha256(request_body(study)).hexdigest()
        or report.get("schema_conditioned") is not True
    ):
        raise ValueError("Liquid export does not match the frozen local study")
    if (
        settings({key: report.get("configuration", {}).get(key) for key in DEFAULTS})
        != study["configuration"]
    ):
        raise ValueError("Liquid export used different training settings")
    training = report.get("training", {})
    updates, attempts, skipped = (
        training.get(k)
        for k in ("optimizer_updates", "optimizer_attempts", "optimizer_skipped")
    )
    delta = training.get("max_parameter_delta")
    if (
        any(type(v) is not int for v in (updates, attempts, skipped))
        or updates < 1
        or skipped < 0
        or updates + skipped != attempts
        or type(delta) not in (int, float)
        or not math.isfinite(delta)
        or delta <= 0
        or training.get("vision_frozen") is not True
    ):
        raise ValueError(
            "Liquid training lacks finite parameter updates and valid optimizer accounting"
        )


def checked_artifact(folder, report, config, questions):
    from decision_workbench.training.artifacts import inventory
    from decision_workbench.training.liquid_runtime import (
        RUNTIME_ASSETS,
        checked_runtime,
    )

    checked_report(report, config)
    files = inventory(folder.resolve())
    allowed_code = {"merged/" + name for name in RUNTIME_ASSETS if name.endswith(".py")}
    if {name for name in files if name.endswith(".py")} != allowed_code:
        raise ValueError("Liquid export contains missing or unapproved model code")
    checked_runtime(folder / "merged")
    metadata = json.loads(
        (folder / "merged/dmt_liquid.json").read_text(encoding="utf-8")
    )
    if (
        metadata.get("questions") != questions
        or metadata.get("temperatures")
        != {name: value["temperature"] for name, value in report["calibration"].items()}
        or metadata.get("max_length") != config["max_length"]
    ):
        raise ValueError(
            "Liquid runtime schema, calibration or token budget differs from the registered study"
        )
