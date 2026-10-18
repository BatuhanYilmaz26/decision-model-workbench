"""Pure configuration and frozen-study identity for native Perplexity jobs."""

import hashlib
import math
from pathlib import Path
import re

from decision_workbench.data import dumps
from decision_workbench.json_tools import strict_json
from .perplexity_assets import MODEL, REVISION

PROFILE = {
    "backend": "perplexity-native",
    "model": MODEL,
    "revision": REVISION,
    "training_method": "publisher-full-weight",
}
DEFAULTS = {
    "epochs": 1,
    "batch_size": 1,
    "grad_accum": 4,
    "max_length": 2048,
    "learning_rate": 2e-6,
    "cpu_threads": 32,
    "eval_every": 20,
    "device": "cuda",
}
INPUTS = {"snapshot"} | set(PROFILE) | set(DEFAULTS)


def settings(value):
    if not isinstance(value, dict) or set(value) - set(PROFILE) - set(DEFAULTS):
        raise ValueError("Unexpected native Perplexity training settings")
    result = {**PROFILE, **DEFAULTS, **value}
    if (
        any(result[name] != expected for name, expected in PROFILE.items())
        or result["device"] != "cuda"
    ):
        raise ValueError("Choose the pinned Perplexity full-weight CUDA profile")
    for name, low, high in (
        ("epochs", 1, 20),
        ("batch_size", 1, 4),
        ("grad_accum", 1, 128),
        ("max_length", 64, 8192),
        ("cpu_threads", 1, 128),
        ("eval_every", 1, 10000),
    ):
        if type(result[name]) is not int or not low <= result[name] <= high:
            raise ValueError(f"{name} must be a whole number from {low} to {high}")
    rate = result["learning_rate"]
    if (
        type(rate) not in (int, float)
        or not math.isfinite(rate)
        or not 0 < rate <= 1e-4
    ):
        raise ValueError("Native learning rate must be finite and in (0, 0.0001]")
    return result


def request_body(value):
    return dumps(value).encode("utf-8")


def validate_request(path, configuration, hashes, source, rows):
    if path is None:
        return None
    with Path(path).open("rb") as stream:
        body = stream.read(65537)
    if len(body) > 65536:
        raise ValueError("Native frozen request exceeds 64 KB")
    value = strict_json(body)
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
        or value.get("format") != "domain-perplexity-local-study-v1"
        or value.get("configuration") != configuration
        or value.get("source_sha256") != source
        or value.get("data_sha256") != hashes
        or value.get("counts") != {role: len(records) for role, records in rows.items()}
        or not isinstance(value.get("snapshot"), str)
        or not re.fullmatch(r"[a-f0-9]{32}", value["snapshot"])
        or value.get("schema_sha256")
        != hashlib.sha256(dumps(rows["train"][0]["questions"]).encode()).hexdigest()
        or value.get("snapshot_sha256")
        != hashlib.sha256(dumps(rows).encode()).hexdigest()
    ):
        raise ValueError(
            "Native training differs from the frozen source/settings/data request"
        )
    return hashlib.sha256(body).hexdigest()
