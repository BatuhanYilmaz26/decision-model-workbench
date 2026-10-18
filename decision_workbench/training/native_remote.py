"""Bind portable native-model studies to native local trainers and remote exports."""

from decision_workbench.training.d3_profiles import BACKENDS as D3_BACKENDS, get_profile

import hashlib
import os
from pathlib import Path
import re

from decision_workbench.data import dumps
from decision_workbench.json_tools import strict_json
from .provenance import source_fingerprint

BACKENDS = (
    "liquid-trl",
    "liquid-unsloth",
    "liquid-omni",
    "perplexity-native",
    *D3_BACKENDS,
)
LOCATION = {"provider", "region", "gpu", "image_digest"}


def profile(backend):
    if backend == "perplexity-native":
        from . import perplexity_local as local
        from decision_workbench.platform import perplexity_training as platform

        return local, platform, "perplexity_study"
    if backend in D3_BACKENDS:
        from . import d3_local as local
        from decision_workbench.platform import d3_training as platform

        return local, platform, "d3_study"
    if backend == "liquid-omni":
        from . import liquid_omni_local as local
        from decision_workbench.platform import liquid_omni_training as platform

        return local, platform, "omni_study"
    if backend in ("liquid-trl", "liquid-unsloth"):
        from . import liquid_local as local
        from decision_workbench.platform import liquid_training as platform

        return local, platform, "liquid_study"
    raise ValueError("Choose a compatible native training profile")


def check_inputs(request, rows, hashes, questions):
    """Recompute both outer transfer and inner native-study identities."""
    if not isinstance(request, dict):
        raise ValueError("Native remote request must be a JSON object")
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
        raise ValueError("Native remote request format differs")
    for key in ("study_id", "project"):
        if not isinstance(core[key], str) or not re.fullmatch(
            r"[a-f0-9]{32}", core[key]
        ):
            raise ValueError("Native remote study/project identity is invalid")
    if not isinstance(core["tenant"], str) or not core["tenant"]:
        raise ValueError("Native remote tenant identity is invalid")
    if (
        "request_sha256" in request
        and request["request_sha256"]
        != hashlib.sha256(dumps(core).encode()).hexdigest()
    ):
        raise ValueError("Native remote request checksum differs")
    if "questions" in request and request["questions"] != questions:
        raise ValueError("Native remote questions differ")
    config = core["configuration"]
    if not isinstance(config, dict):
        raise ValueError("Native remote configuration must be a JSON object")
    offline = (
        "download" not in config
        if config.get("backend") == "perplexity-native"
        else config.get("download") is False
    )
    if config.get("device") != "cuda" or not offline:
        raise ValueError(
            "Remote native training requires CUDA and a pre-cached offline checkpoint"
        )
    local, platform, key = profile(config.get("backend"))
    from decision_workbench.platform.remote import validate_location

    validate_location(config)
    if (
        os.environ.get("DMT_CLOUD_IMAGE_BACKEND", config["backend"])
        != config["backend"]
    ):
        raise ValueError(
            "Selected native image was built for a different native backend"
        )
    study = platform.study_for(
        config,
        {
            "splits": rows,
            "sha256": hashlib.sha256(dumps(rows).encode()).hexdigest(),
            "schema_sha256": hashlib.sha256(dumps(questions).encode()).hexdigest(),
            "counts": {role: len(records) for role, records in rows.items()},
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
        raise ValueError("Native remote data/source/native study binding differs")
    allowed = (
        set(local.INPUTS)
        | LOCATION
        | {key, "release_policy_id", "release_policy_sha256", "error_policy"}
    )
    if set(config) - allowed:
        raise ValueError("Native remote study contains unsupported training options")
    return study


def native_command(request, work, output, interpreter):
    """The cloud launcher cannot select a different executable or model."""
    config = request["configuration"]
    profile(config.get("backend"))
    return [
        str(interpreter),
        "-m",
        "decision_workbench.training.native_remote_runner",
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
    if backend == "perplexity-native":
        from .perplexity_profile import ASSETS

        return {"publisher/" + name for name in ASSETS if name.endswith(".json")} | {
            checkpoint + "/" + name
            for name in (
                "config.json",
                "processor_config.json",
                "tokenizer.json",
                "tokenizer_config.json",
            )
        }
    if backend in D3_BACKENDS:
        assets = get_profile(backend).assets
    elif backend == "liquid-omni":
        from .liquid_omni_data import ASSETS as assets
    else:
        from .liquid_runtime import RUNTIME_ASSETS as assets
    return {
        checkpoint + "/config.json",
        *(checkpoint + "/" + name for name in assets if name.endswith(".json")),
    }


def export_assets(backend, checkpoint):
    if backend == "perplexity-native":
        from .perplexity_profile import ASSETS

        result = {"publisher/" + name: info["sha256"] for name, info in ASSETS.items()}
        result.update(
            {
                checkpoint + "/" + name: ASSETS[name]["sha256"]
                for name in (
                    "config.json",
                    "processor_config.json",
                    "tokenizer.json",
                    "tokenizer_config.json",
                    "chat_template.jinja",
                )
            }
        )
        return result
    if backend in D3_BACKENDS:
        assets = get_profile(backend).assets
    elif backend == "liquid-omni":
        from .liquid_omni_data import ASSETS as assets
    else:
        profile(backend)
        from .liquid_runtime import RUNTIME_ASSETS as assets
    return {
        checkpoint + "/" + name: value
        for name, value in assets.items()
        if backend in D3_BACKENDS or name != "config.json"
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
        raise ValueError("Native remote request exceeds 64 KB")
    request = strict_json(body)
    if not isinstance(request, dict) or body != dumps(request).encode():
        raise ValueError("Native remote request must retain its canonical bytes")
    return request, hashlib.sha256(body).hexdigest()
