"""Immutable settings and request identity for D3 native-readout adaptation."""

import hashlib
import math
from pathlib import Path
import re

from decision_workbench.data import dumps
from decision_workbench.json_tools import strict_json
from .d3_assets import MODEL, REVISION
from .d3_profiles import get_profile

PROFILE = {
    "backend": "d3-edge",
    "model": MODEL,
    "revision": REVISION,
    "training_method": "native-readout",
}
DEFAULTS = {
    "epochs": 1,
    "batch_size": 1,
    "grad_accum": 4,
    "max_length": 2048,
    "max_steps": None,
    "learning_rate": 1e-4,
    "device": "auto",
    "download": False,
}
INPUTS = {"snapshot"} | set(PROFILE) | set(DEFAULTS)


def settings(value):
    if not isinstance(value, dict) or set(value) - set(PROFILE) - set(DEFAULTS):
        raise ValueError("Unexpected D3 training settings")
    profile = get_profile(value.get("backend", "d3-edge")).fields
    result = {**profile, **DEFAULTS, **value}
    if any(result[name] != expected for name, expected in profile.items()):
        raise ValueError("Choose the matching pinned D3 native-readout profile")
    for name, low, high in (
        ("epochs", 1, 20),
        ("batch_size", 1, 16),
        ("grad_accum", 1, 128),
        ("max_length", 64, 32768),
    ):
        if type(result[name]) is not int or not low <= result[name] <= high:
            raise ValueError(f"{name} must be a whole number from {low} to {high}")
    if result["max_steps"] is not None and (
        type(result["max_steps"]) is not int or not 1 <= result["max_steps"] <= 100000
    ):
        raise ValueError("D3 max_steps must be 1–100000 or unset")
    rate = result["learning_rate"]
    if (
        type(rate) not in (int, float)
        or not math.isfinite(rate)
        or not 0 < rate <= 0.001
    ):
        raise ValueError("D3 learning rate must be finite and within (0, 0.001]")
    if (
        result["device"] not in ("auto", "cpu", "cuda")
        or type(result["download"]) is not bool
    ):
        raise ValueError("Choose auto/CPU/CUDA and explicit download permission")
    return result


def request_body(study):
    return dumps(study).encode()


def validate_request(path, args, hashes, source, rows):
    with Path(path).open("rb") as stream:
        body = stream.read(65537)
    if len(body) > 65536:
        raise ValueError("D3 frozen request exceeds 64 KB")
    value = strict_json(body)
    expected = settings(
        {
            "backend": getattr(args, "backend", "d3-edge"),
            **{name: getattr(args, name) for name in DEFAULTS},
        }
    )
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "format",
            "snapshot",
            "snapshot_sha256",
            "schema_sha256",
            "configuration",
            "source_sha256",
            "data_sha256",
            "counts",
        }
        or value.get("format") != "domain-d3-local-study-v1"
        or value.get("configuration") != expected
        or value.get("source_sha256") != source
        or value.get("data_sha256") != hashes
        or not isinstance(value.get("snapshot"), str)
        or not re.fullmatch(r"[a-f0-9]{32}", value["snapshot"])
        or value.get("counts") != {role: len(records) for role, records in rows.items()}
        or value.get("schema_sha256")
        != hashlib.sha256(dumps(rows["train"][0]["questions"]).encode()).hexdigest()
        or value.get("snapshot_sha256")
        != hashlib.sha256(dumps(rows).encode()).hexdigest()
    ):
        raise ValueError(
            "D3 training differs from the frozen source/settings/data request"
        )
    return hashlib.sha256(body).hexdigest()
