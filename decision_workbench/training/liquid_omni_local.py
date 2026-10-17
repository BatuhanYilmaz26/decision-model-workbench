"""Immutable settings and request identity for native omni head adaptation."""

import hashlib
import math
import re
from pathlib import Path

from decision_workbench.data import dumps
from decision_workbench.json_tools import strict_json
from .liquid_omni_data import MODEL, REVISION

PROFILE = {
    "backend": "liquid-omni",
    "model": MODEL,
    "revision": REVISION,
    "training_method": "native-head",
}
DEFAULTS = {
    "epochs": 1,
    "batch_size": 1,
    "grad_accum": 4,
    "max_length": 2048,
    "max_steps": None,
    "learning_rate": 1e-5,
    "device": "auto",
    "download": False,
}
INPUTS = {"snapshot"} | set(PROFILE) | set(DEFAULTS)


def settings(value):
    if not isinstance(value, dict) or set(value) - set(PROFILE) - set(DEFAULTS):
        raise ValueError("Unexpected omni training settings")
    result = {**PROFILE, **DEFAULTS, **value}
    if any(result[name] != expected for name, expected in PROFILE.items()):
        raise ValueError("Choose the pinned d1-omni native-head training profile")
    for name, low, high in (
        ("epochs", 1, 20),
        ("batch_size", 1, 16),
        ("grad_accum", 1, 128),
        ("max_length", 64, 16384),
    ):
        if type(result[name]) is not int or not low <= result[name] <= high:
            raise ValueError(f"{name} must be a whole number from {low} to {high}")
    if result["max_steps"] is not None and (
        type(result["max_steps"]) is not int or not 1 <= result["max_steps"] <= 100000
    ):
        raise ValueError("max_steps must be 1–100000 or unset")
    rate = result["learning_rate"]
    if (
        type(rate) not in (int, float)
        or not math.isfinite(rate)
        or not 0 < rate <= 0.001
    ):
        raise ValueError("Omni learning rate must be finite and within (0, 0.001]")
    if (
        result["device"] not in ("auto", "cpu", "cuda")
        or type(result["download"]) is not bool
    ):
        raise ValueError("Choose auto/CPU/CUDA and explicit download permission")
    return result


def request_body(study):
    return dumps(study).encode("utf-8")


def validate_request(path, args, hashes, source, rows):
    body = Path(path).read_bytes()
    if len(body) > 65536:
        raise ValueError("Omni frozen request exceeds 64 KB")
    value = strict_json(body)
    expected = settings({name: getattr(args, name) for name in DEFAULTS})
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
        or value.get("format") != "domain-liquid-omni-local-study-v1"
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
            "Omni training does not match the frozen source/settings/data request"
        )
    return hashlib.sha256(body).hexdigest()
