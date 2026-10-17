"""Bind portable Liquid studies to native local trainers and remote exports."""

import hashlib
import os
from pathlib import Path
import re

from decision_workbench.data import dumps
from decision_workbench.json_tools import strict_json
from .provenance import source_fingerprint

BACKENDS = ("liquid-trl", "liquid-unsloth", "liquid-omni")
LOCATION = {"provider", "region", "gpu", "image_digest"}


def profile(backend):
    if backend == "liquid-omni":
        from . import liquid_omni_local as local
        from decision_workbench.platform import liquid_omni_training as platform

        return local, platform, "omni_study"
    if backend in ("liquid-trl", "liquid-unsloth"):
        from . import liquid_local as local
        from decision_workbench.platform import liquid_training as platform

        return local, platform, "liquid_study"
    raise ValueError("Choose a compatible Liquid native training profile")


def check_inputs(request, rows, hashes, questions):
    """Recompute both outer transfer and inner native-study identities."""
    if not isinstance(request, dict):
        raise ValueError("Liquid remote request must be a JSON object")
    core = {
        k: v for k, v in request.items() if k not in ("questions", "request_sha256")
    }
    expected_keys = {
        "format",
        "study_id",
        "tenant",
        "project",
        "snapshot_sha256",
        "schema_sha256",
        "configuration",
        "source_sha256",
        "data_sha256",
    }
    if set(core) != expected_keys or core.get("format") != "domain-remote-study-v1":
        raise ValueError("Liquid remote request format differs")
    for key in ("study_id", "project"):
        if not isinstance(core[key], str) or not re.fullmatch(
            r"[a-f0-9]{32}", core[key]
        ):
            raise ValueError("Liquid remote study/project identity is invalid")
    if not isinstance(core["tenant"], str) or not core["tenant"]:
        raise ValueError("Liquid remote tenant identity is invalid")
    if (
        "request_sha256" in request
        and request["request_sha256"]
        != hashlib.sha256(dumps(core).encode()).hexdigest()
    ):
        raise ValueError("Liquid remote request checksum differs")
    if "questions" in request and request["questions"] != questions:
        raise ValueError("Liquid remote questions differ")
    config = core["configuration"]
    if not isinstance(config, dict):
        raise ValueError("Liquid remote configuration must be a JSON object")
    if config.get("device") != "cuda" or config.get("download") is not False:
        raise ValueError(
            "Remote Liquid requires CUDA and a pre-cached offline checkpoint"
        )
    local, platform, key = profile(config.get("backend"))
    from decision_workbench.platform.remote import validate_location

    validate_location(config)
    if (
        os.environ.get("DMT_CLOUD_IMAGE_BACKEND", config["backend"])
        != config["backend"]
    ):
        raise ValueError(
            "Selected Liquid image was built for a different native backend"
        )
    study = platform.study_for(
        config,
        {
            "splits": rows,
            "sha256": hashlib.sha256(dumps(rows).encode()).hexdigest(),
            "schema_sha256": hashlib.sha256(dumps(questions).encode()).hexdigest(),
        },
        questions,
    )
    if (
        config.get(key) != study
        or core["snapshot_sha256"] != study["snapshot_sha256"]
        or core["schema_sha256"] != study["schema_sha256"]
        or core["data_sha256"] != hashes
        or hashes != study["data_sha256"]
        or core["source_sha256"] != study["source_sha256"]
        or core["source_sha256"] != source_fingerprint()
    ):
        raise ValueError("Liquid remote data/source/native study binding differs")
    allowed = (
        set(local.INPUTS)
        | LOCATION
        | {key, "release_policy_id", "release_policy_sha256", "error_policy"}
    )
    if set(config) - allowed:
        raise ValueError("Liquid remote study contains unsupported training options")
    return study


def native_command(request, work, output, interpreter):
    """The cloud launcher cannot select a different executable or model."""
    config = request["configuration"]
    profile(config.get("backend"))
    return [
        str(interpreter),
        "-m",
        "decision_workbench.training.liquid_remote_runner",
        "--request",
        str(Path(work) / "request.json"),
        "--data",
        str(Path(work) / "data"),
        "--out",
        str(output),
    ]


def checked_artifact(folder, report, config, questions):
    _, platform, _ = profile(config["backend"])
    platform.checked_artifact(folder, report, config, questions)


def approved_configs(backend, checkpoint):
    if backend == "liquid-omni":
        from .liquid_omni_data import ASSETS as assets
    else:
        from .liquid_runtime import RUNTIME_ASSETS as assets
    return {
        checkpoint + "/config.json",
        *(checkpoint + "/" + name for name in assets if name.endswith(".json")),
    }


def export_assets(backend, checkpoint):
    if backend == "liquid-omni":
        from .liquid_omni_data import ASSETS as assets
    else:
        profile(backend)
        from .liquid_runtime import RUNTIME_ASSETS as assets
    return {
        checkpoint + "/" + name: value
        for name, value in assets.items()
        if name != "config.json"
    }


def export_files(backend, checkpoint):
    files = set(export_assets(backend, checkpoint))
    if backend in ("liquid-trl", "liquid-unsloth"):
        files.add("adapter/README.md")  # PEFT's non-executable model card.
    for directory in ("canonical", "lineage", "native"):
        files.update(
            "data/" + directory + "/" + role + ".jsonl"
            for role in ("train", "validation", "calibration", "test")
        )
    for directory in ("trl", "unsloth"):
        files.update(
            "data/" + directory + "/" + role + ".jsonl"
            for role in ("train", "validation")
        )
    return files


def read_request(path):
    with Path(path).open("rb") as stream:
        body = stream.read(65537)
    if len(body) > 65536:
        raise ValueError("Liquid remote request exceeds 64 KB")
    request = strict_json(body)
    if not isinstance(request, dict) or body != dumps(request).encode():
        raise ValueError("Liquid remote request must retain its canonical bytes")
    return request, hashlib.sha256(body).hexdigest()
