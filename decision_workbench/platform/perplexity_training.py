"""Freeze local native Perplexity studies and verify their registry artifacts."""

import hashlib
import math
import os
import re
from pathlib import Path

from decision_workbench.data import dumps, jsonl, options
from decision_workbench.json_tools import strict_json
from decision_workbench.training.contracts import SPLITS, validate_splits
from decision_workbench.training.perplexity_local import (
    PROFILE,
    DEFAULTS,
    INPUTS,
    settings,
    request_body,
)
from decision_workbench.training.provenance import source_fingerprint


def study_for(config, snapshot, questions, *, source=None):
    splits = snapshot["splits"]
    if validate_splits(splits) != questions:
        raise ValueError("Native Perplexity snapshot differs from the project schema")
    if (
        hashlib.sha256(dumps(splits).encode()).hexdigest() != snapshot["sha256"]
        or hashlib.sha256(dumps(questions).encode()).hexdigest()
        != snapshot["schema_sha256"]
        or snapshot["counts"]
        != {role: len(records) for role, records in splits.items()}
    ):
        raise ValueError(
            "Reserved native snapshot integrity differs from its data/schema/counts"
        )
    if any(q["type"] not in ("choice", "noul") for q in questions.values()):
        raise ValueError(
            "Native Perplexity training supports Choice and Noul only; Score needs another backend"
        )
    if len(splits["calibration"]) < 8:
        raise ValueError(
            "Native Perplexity needs at least eight reserved real calibration records"
        )
    for role in SPLITS[1:]:
        for row in splits[role]:
            for name in questions:
                values = list(row["gold"][name]["probabilities"].values())
                if sum(p == 1 for p in values) != 1 or any(
                    p not in (0, 1) for p in values
                ):
                    raise ValueError(
                        "Native Perplexity heldouts need discrete one-hot reviewed gold; soft labels are training-only"
                    )
    return {
        "format": "domain-perplexity-local-study-v1",
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
        "counts": {role: len(records) for role, records in splits.items()},
    }


def freeze(service, principal, project, config):
    if set(config) - INPUTS:
        raise ValueError(
            "Unsupported native Perplexity options; interpreter/checkpoint paths are operator settings"
        )
    snapshot = service.store.get(
        config["snapshot"], principal["tenant"], project, "snapshot"
    )["payload"]
    questions = service.project(principal, project)["payload"]["questions"]
    study = study_for(config, snapshot, questions)
    return {
        "snapshot": config["snapshot"],
        **study["configuration"],
        "perplexity_study": study,
    }


def recheck(config, snapshot, questions):
    if config.get("perplexity_study") != study_for(config, snapshot, questions):
        raise ValueError(
            "Native Perplexity source, settings or reserved data changed after queueing"
        )


def command(store, config, job_folder):
    from .model_environment import model_python, native_environment

    environment, root, _ = native_environment(store.folder, backend="perplexity-native")
    checkpoint = Path(environment.get("DMT_PERPLEXITY_CHECKPOINT", ""))
    if not checkpoint.is_absolute() or not checkpoint.is_dir():
        raise ValueError(
            "Configure DMT_PERPLEXITY_CHECKPOINT with the complete pinned checkpoint on the worker"
        )
    request = job_folder / "perplexity-request.json"
    request.write_bytes(request_body(config["perplexity_study"]))
    out = job_folder / "perplexity-run"
    argv = [
        model_python("perplexity-native", "training"),
        "-m",
        "decision_workbench.training.perplexity_runner",
        "--data",
        str(job_folder / "data"),
        "--out",
        str(out),
        "--checkpoint",
        str(checkpoint),
        "--local-study-request",
        str(request),
    ]
    for name in DEFAULTS:
        argv.extend(["--" + name.replace("_", "-"), str(config[name])])
    return argv, environment, root


def job_timeout(environment=None):
    value = (os.environ if environment is None else environment).get(
        "DMT_PERPLEXITY_TRAINING_TIMEOUT_SECONDS", "86400"
    )
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Set a finite native training deadline between 600 and 604800 seconds"
        ) from exc
    if not math.isfinite(seconds) or not 600 <= seconds <= 604800:
        raise ValueError(
            "Set a finite native training deadline between 600 and 604800 seconds"
        )
    return seconds


def move_export(store, job_folder, destination):
    """Move only this owned job's completed export into its artifact slot."""
    source = job_folder / "perplexity-run/export"
    jobs = (store.folder / "jobs").resolve()
    artifacts = (store.folder / "artifacts").resolve()
    if (
        job_folder.resolve().parent != jobs
        or job_folder.is_symlink()
        or source.is_symlink()
        or (hasattr(source, "is_junction") and source.is_junction())
        or (hasattr(job_folder, "is_junction") and job_folder.is_junction())
        or not source.resolve().is_relative_to(job_folder.resolve())
        or destination.resolve().parent != artifacts
        or destination.exists()
        or not (source / "manifest.json").is_file()
    ):
        raise ValueError(
            "Native export must remain inside its owned job and empty artifact slot"
        )
    # Resolved containment is checked before the directory move on Windows.
    source.rename(destination)


def checked_report(report, config):
    from decision_workbench.training.perplexity_runtime import PACKAGES

    study = config.get("perplexity_study", {})
    if (
        any(report.get(k) != v for k, v in PROFILE.items() if k != "training_method")
        or report.get("configuration") != study.get("configuration")
        or report.get("source_sha256") != study.get("source_sha256")
        or report.get("data_sha256") != study.get("data_sha256")
        or report.get("counts") != study.get("counts")
        or report.get("local_study_sha256")
        != hashlib.sha256(request_body(study)).hexdigest()
        or report.get("schema_conditioned") is not True
    ):
        raise ValueError("Native Perplexity export differs from its frozen local study")
    training = report.get("training", {})
    if (
        training.get("method") != "publisher-full-weight"
        or training.get("device") != "cuda:0"
        or type(training.get("optimizer_updates")) is not int
        or training["optimizer_updates"] < 1
        or training.get("checkpoint_policy") != "final_only"
        or training.get("backbone_updated") is not True
        or training.get("readout_updated") is not True
    ):
        raise ValueError("Native Perplexity needs completed full-weight CUDA updates")
    for kind in ("backbone", "readout"):
        before, after = training.get(
            "original_" + kind + "_payload_sha256"
        ), training.get(kind + "_payload_sha256")
        if (
            not isinstance(before, str)
            or not isinstance(after, str)
            or not re.fullmatch(r"[a-f0-9]{64}", before)
            or not re.fullmatch(r"[a-f0-9]{64}", after)
            or before == after
        ):
            raise ValueError("Native tensor payload update identities are missing")
    packages = report.get("packages", {})
    if not isinstance(packages, dict) or any(
        not isinstance(packages.get(k), str) or packages[k].split("+")[0] != v
        for k, v in PACKAGES.items()
    ):
        raise ValueError(
            "Native runtime package identities differ from the pinned profile"
        )
    calibration = report.get("calibration")
    if (
        not isinstance(calibration, dict)
        or not calibration
        or set(calibration) != set(report.get("test", {}))
    ):
        raise ValueError("Native calibration must cover every question")
    scales = []
    for fit in calibration.values():
        scale = fit.get("temperature") if isinstance(fit, dict) else None
        if (
            type(scale) not in (int, float)
            or not math.isfinite(scale)
            or not 0.05 <= scale <= 20
            or fit.get("records") != study["counts"]["calibration"]
            or fit.get("decision_fit_records")
            != study["counts"]["calibration"] * len(calibration)
            or fit.get("method") != "publisher-global-across-questions"
        ):
            raise ValueError(
                "Native calibration must retain the publisher's single fitted temperature"
            )
        scales.append(scale)
    reference = report.get("reference", {})
    if (
        len(set(scales)) != 1
        or not isinstance(reference, dict)
        or reference.get("kind") != "original-local-checkpoint"
        or reference.get("model") != PROFILE["model"]
        or reference.get("revision") != PROFILE["revision"]
        or type(reference.get("paid_api_calls")) is not int
        or reference["paid_api_calls"] != 0
    ):
        raise ValueError(
            "Native temperature/reference identity differs from the local workflow"
        )


def checked_artifact(folder, report, config, questions):
    from decision_workbench.training.artifacts import inventory
    from decision_workbench.training.perplexity_checkpoint import (
        ASSETS,
        asset,
        native_files,
        tensor_digest,
    )

    checked_report(report, config)
    folder = Path(folder).resolve()
    publisher, checkpoint = folder / "publisher", folder / "checkpoint"
    for name in ASSETS:
        asset(publisher, name)
    native, mapping, names = native_files(checkpoint, publisher)
    found = inventory(folder)
    allowed = (
        {"report.json"}
        | {"publisher/" + n for n in ASSETS}
        | {"checkpoint/" + n for n in names}
        | {"checkpoint/dmt_perplexity.json"}
    )
    if (
        set(found) != allowed
        or strict_json((folder / "manifest.json").read_bytes()).get("checkpoint")
        != "checkpoint"
    ):
        raise ValueError("Native artifact has missing or unapproved files/code")
    path = checkpoint / "dmt_perplexity.json"
    if path.stat().st_size > 1024**2:
        raise ValueError("Native metadata exceeds 1 MiB")
    metadata = strict_json(path.read_bytes())
    scale = next(iter(report["calibration"].values()))["temperature"]
    if (
        metadata.get("format") != "domain-perplexity-runtime-v1"
        or metadata.get("model") != PROFILE["model"]
        or metadata.get("revision") != PROFILE["revision"]
        or metadata.get("questions") != questions
        or any(
            options(metadata["questions"][k]) != options(q)
            for k, q in questions.items()
        )
        or metadata.get("max_length") != config["max_length"]
        or metadata.get("temperature") != scale
        or native["temperature"] != scale
        or metadata.get("backbone_payload_sha256")
        != report["training"]["backbone_payload_sha256"]
        or metadata.get("readout_payload_sha256")
        != report["training"]["readout_payload_sha256"]
        or tensor_digest(checkpoint, mapping, readout=True)
        != metadata.get("readout_payload_sha256")
    ):
        raise ValueError(
            "Native runtime schema/temperature/payload differs from the registered study"
        )
    for name in (
        "config.json",
        "processor_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "chat_template.jinja",
    ):
        if (checkpoint / name).read_bytes() != asset(publisher, name):
            raise ValueError(
                "Native processor/architecture differs from the pinned profile"
            )
