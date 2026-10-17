"""Freeze and admit native omni domain studies through the shared registry."""

import hashlib
import math
from pathlib import Path

from decision_workbench.data import jsonl
from decision_workbench.json_tools import strict_json
from decision_workbench.training.contracts import SPLITS, validate_splits
from decision_workbench.training.liquid_omni_local import (
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
        raise ValueError("Omni snapshot questions differ from the project")
    if len(splits["calibration"]) < 8:
        raise ValueError("Omni needs at least eight reserved real calibration records")
    return {
        "format": "domain-liquid-omni-local-study-v1",
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
        raise ValueError("Training options do not apply to native omni head adaptation")
    snapshot = service.store.get(
        config["snapshot"], principal["tenant"], project, "snapshot"
    )["payload"]
    questions = service.project(principal, project)["payload"]["questions"]
    study = study_for(config, snapshot, questions)
    return {
        "snapshot": config["snapshot"],
        **study["configuration"],
        "omni_study": study,
    }


def recheck(config, snapshot, questions):
    if config.get("omni_study") != study_for(config, snapshot, questions):
        raise ValueError("Omni source, settings or reservation changed after queueing")


def checked_report(report, config):
    study = config.get("omni_study", {})
    if (
        any(report.get(k) != v for k, v in PROFILE.items() if k != "training_method")
        or report.get("source_sha256") != study.get("source_sha256")
        or report.get("data_sha256") != study.get("data_sha256")
        or report.get("counts") != study.get("counts")
        or report.get("configuration") != study.get("configuration")
        or report.get("local_study_sha256")
        != hashlib.sha256(request_body(study)).hexdigest()
        or report.get("schema_conditioned") is not True
    ):
        raise ValueError("Omni export differs from the frozen local study")
    calibration = report.get("calibration")
    if (
        not isinstance(calibration, dict)
        or set(calibration) != set(report.get("test", {}))
        or not calibration
        or any(
            not isinstance(fit, dict)
            or type(fit.get("temperature")) not in (int, float)
            or not math.isfinite(fit["temperature"])
            or not 0.25 <= fit["temperature"] <= 10
            or fit.get("records") != study["counts"]["calibration"]
            for fit in calibration.values()
        )
    ):
        raise ValueError(
            "Omni fitted temperatures must be separate from calibration metrics"
        )
    training = report.get("training", {})
    updates, delta = training.get("optimizer_updates"), training.get(
        "max_parameter_delta"
    )
    if (
        training.get("method") != "native-head"
        or type(updates) is not int
        or updates < 1
        or type(delta) not in (int, float)
        or not math.isfinite(delta)
        or delta <= 0
        or training.get("frozen_parameters_unchanged") is not True
        or training.get("dtype") != "float32"
        or not isinstance(training.get("frozen_parameters_sha256"), str)
        or len(training["frozen_parameters_sha256"]) != 64
    ):
        raise ValueError(
            "Omni requires finite native-head updates and unchanged frozen parameters"
        )


def checked_artifact(folder, report, config, questions):
    from decision_workbench.training.artifacts import inventory
    from decision_workbench.training.liquid_omni_runtime import checked_runtime
    from decision_workbench.training.liquid_omni_data import ASSETS

    folder = Path(folder).resolve()
    checked_report(report, config)
    files = inventory(folder)
    if {name for name in files if name.endswith(".py")} != {
        "checkpoint/" + name for name in ASSETS if name.endswith(".py")
    }:
        raise ValueError("Omni export contains missing or unapproved native code")
    manifest = strict_json((folder / "manifest.json").read_bytes())
    if manifest.get("checkpoint") != "checkpoint":
        raise ValueError("Omni manifest selects a different native checkpoint")
    checked_runtime(folder / "checkpoint")
    metadata = strict_json((folder / "checkpoint/dmt_omni.json").read_bytes())
    if (
        metadata.get("format") != "domain-liquid-omni-runtime-v1"
        or metadata.get("model") != PROFILE["model"]
        or metadata.get("revision") != PROFILE["revision"]
        or metadata.get("training_method") != "native-head"
        or metadata.get("questions") != questions
        or metadata.get("temperatures")
        != {name: value["temperature"] for name, value in report["calibration"].items()}
        or metadata.get("max_length") != config["max_length"]
    ):
        raise ValueError(
            "Omni runtime policy/calibration differs from the registered study"
        )
