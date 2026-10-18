"""Freeze and admit native D3 domain studies through the shared registry."""

import hashlib
import math
from pathlib import Path

from decision_workbench.data import jsonl, options
from decision_workbench.json_tools import strict_json
from decision_workbench.training.contracts import SPLITS, validate_splits
from decision_workbench.training.d3_local import (
    DEFAULTS,
    PROFILE,
    INPUTS,
    request_body,
    settings,
)
from decision_workbench.training.provenance import source_fingerprint
from decision_workbench.training.d3_profiles import get_profile


def study_for(config, snapshot, questions, *, source=None):
    splits = snapshot["splits"]
    if validate_splits(splits) != questions:
        raise ValueError("D3 snapshot questions differ from the project")
    if len(splits["calibration"]) < 8:
        raise ValueError("D3 needs at least eight reserved real calibration records")
    return {
        "format": "domain-d3-local-study-v1",
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
        raise ValueError("Training options do not apply to native D3 head adaptation")
    snapshot = service.store.get(
        config["snapshot"], principal["tenant"], project, "snapshot"
    )["payload"]
    questions = service.project(principal, project)["payload"]["questions"]
    study = study_for(config, snapshot, questions)
    return {
        "snapshot": config["snapshot"],
        **study["configuration"],
        "d3_study": study,
    }


def recheck(config, snapshot, questions):
    if config.get("d3_study") != study_for(config, snapshot, questions):
        raise ValueError("D3 source, settings or reservation changed after queueing")


def checked_report(report, config):
    study = config.get("d3_study", {})
    profile = get_profile(config.get("backend"))
    if (
        any(
            report.get(k) != v
            for k, v in profile.fields.items()
            if k != "training_method"
        )
        or report.get("source_sha256") != study.get("source_sha256")
        or report.get("data_sha256") != study.get("data_sha256")
        or report.get("counts") != study.get("counts")
        or report.get("configuration") != study.get("configuration")
        or report.get("local_study_sha256")
        != hashlib.sha256(request_body(study)).hexdigest()
        or report.get("schema_conditioned") is not True
    ):
        raise ValueError("D3 export differs from the frozen local study")
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
            "D3 fitted temperatures must be separate from calibration metrics"
        )
    training = report.get("training", {})
    updates, delta = training.get("optimizer_updates"), training.get(
        "max_parameter_delta"
    )
    if (
        training.get("method") != "native-readout"
        or type(updates) is not int
        or updates < 1
        or type(delta) not in (int, float)
        or not math.isfinite(delta)
        or delta <= 0
        or training.get("frozen_parameters_unchanged") is not True
        or training.get("backbone_dtype") != "bfloat16"
        or training.get("readout_dtype") != "float32"
        or training.get("trainable_parameters") != profile.trainable_parameters
        or training.get("device") not in ("cpu", "cuda:0")
        or not isinstance(training.get("frozen_parameters_sha256"), str)
        or len(training["frozen_parameters_sha256"]) != 64
    ):
        raise ValueError(
            "D3 requires finite native-readout updates and unchanged frozen parameters"
        )


def checked_artifact(folder, report, config, questions):
    from decision_workbench.training.artifacts import inventory
    from decision_workbench.training.d3_runtime import checked_runtime

    profile = get_profile(config["backend"])
    assets = profile.assets

    folder = Path(folder).resolve()
    checked_report(report, config)
    files = inventory(folder)

    allowed = {"report.json", "data/manifest.json"}
    if report.get("admission"):
        allowed.add("training_report.json")
    allowed.update(
        "checkpoint/" + name
        for name in (*assets, *profile.weights, "readout.safetensors", "dmt_d3.json")
    )
    allowed.update(
        "data/" + directory + "/" + role + ".jsonl"
        for directory in ("canonical", "native", "lineage")
        for role in SPLITS
    )
    if set(files) - allowed:
        raise ValueError(
            "D3 export contains an unapproved native file or bytecode cache"
        )
    if {name for name in files if name.endswith(".py")} != {
        "checkpoint/" + name for name in assets if name.endswith(".py")
    }:
        raise ValueError("D3 export contains missing or unapproved native code")
    manifest = strict_json((folder / "manifest.json").read_bytes())
    if manifest.get("checkpoint") != "checkpoint":
        raise ValueError("D3 manifest selects a different native checkpoint")
    checked_runtime(folder / "checkpoint", backend=profile.backend)
    with (folder / "checkpoint/dmt_d3.json").open("rb") as stream:
        body = stream.read(16 * 1024**2 + 1)
    if len(body) > 16 * 1024**2:
        raise ValueError("D3 domain metadata exceeds 16 MiB")
    metadata = strict_json(body)
    if (
        metadata.get("format") != "domain-d3-runtime-v1"
        or metadata.get("model") != profile.model
        or metadata.get("revision") != profile.revision
        or metadata.get("training_method") != "native-readout"
        or metadata.get("questions") != questions
        or any(
            options(metadata["questions"][name]) != options(question)
            for name, question in questions.items()
        )
        or metadata.get("temperatures")
        != {name: value["temperature"] for name, value in report["calibration"].items()}
        or metadata.get("max_length") != config["max_length"]
        or metadata.get("device") != report["training"].get("device", "").split(":")[0]
        or metadata.get("device") not in ("cpu", "cuda")
        or config["device"] != "auto"
        and config["device"] != metadata.get("device")
    ):
        raise ValueError(
            "D3 runtime policy/calibration differs from the registered study"
        )
