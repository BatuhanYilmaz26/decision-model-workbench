"""Frozen local Liquid studies shared by the API, worker and model child."""

import hashlib
import math
from pathlib import Path

from decision_workbench.data import dumps
from decision_workbench.json_tools import strict_json
from .liquid_data import MODEL, REVISION

DEFAULTS = {
    "epochs": 1,
    "rank": 16,
    "batch_size": 1,
    "grad_accum": 4,
    "max_length": 2048,
    "max_steps": None,
    "learning_rate": 2e-4,
    "device": "auto",
    "download": False,
}
PROFILE = {"backend": "liquid-trl", "model": MODEL, "revision": REVISION}
PINNED_PACKAGES = {
    "transformers": "5.19.0",
    "trl": "1.15.0",
    "peft": "0.21.2",
    "datasets": "4.8.5",
    "tokenizers": "0.23.3",
}
INPUTS = {"snapshot"} | set(PROFILE) | set(DEFAULTS)


def settings(value):
    if set(value) - set(PROFILE) - set(DEFAULTS):
        raise ValueError("Unexpected Liquid training settings")
    result = {**PROFILE, **DEFAULTS, **value}
    if any(result[key] != expected for key, expected in PROFILE.items()):
        raise ValueError(
            "Select the pinned Liquid d1-3B model/revision for this profile"
        )
    for name, low, high in (
        ("epochs", 1, 20),
        ("rank", 1, 128),
        ("batch_size", 1, 32),
        ("grad_accum", 1, 128),
        ("max_length", 64, 32768),
    ):
        if type(result[name]) is not int or not low <= result[name] <= high:
            raise ValueError(f"{name} must be between {low} and {high}")
    if result["max_steps"] is not None and (
        type(result["max_steps"]) is not int or not 1 <= result["max_steps"] <= 100000
    ):
        raise ValueError("max_steps must be between 1 and 100000")
    rate = result["learning_rate"]
    if (
        type(rate) not in (int, float)
        or not math.isfinite(rate)
        or not 0 < rate <= 0.01
    ):
        raise ValueError("learning_rate must be finite and within (0,0.01]")
    if (
        result["device"] not in ("auto", "cpu", "cuda")
        or type(result["download"]) is not bool
    ):
        raise ValueError("Choose auto/CPU/CUDA and an explicit boolean download choice")
    return {key: result[key] for key in set(PROFILE) | set(DEFAULTS)}


def request_body(study):
    return dumps(study).encode("utf-8")


def validate_request(path, args, hashes, source):
    body = Path(path).read_bytes()
    if len(body) > 65536:
        raise ValueError("Liquid study request exceeds 64 KB")
    study = strict_json(body.decode("utf-8"))
    if not isinstance(study, dict) or set(study) != {
        "format",
        "snapshot",
        "snapshot_sha256",
        "schema_sha256",
        "source_sha256",
        "data_sha256",
        "counts",
        "configuration",
    }:
        raise ValueError("Invalid frozen Liquid study request")
    actual = settings({key: getattr(args, key) for key in DEFAULTS})
    if (
        study["format"] != "domain-liquid-local-study-v1"
        or study["configuration"] != actual
    ):
        raise ValueError("Liquid trainer configuration differs from the frozen study")
    if study["source_sha256"] != source or study["data_sha256"] != hashes:
        raise ValueError(
            "Liquid training source or reserved input hashes differ from the frozen study"
        )
    return hashlib.sha256(body).hexdigest()
