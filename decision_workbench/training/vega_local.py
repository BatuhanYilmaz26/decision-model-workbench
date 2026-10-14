"""Pure local-worker request binding for the separate eight-file Vega protocol."""

import hashlib
import json
from pathlib import Path

from decision_workbench.data import dumps
from .provenance import source_fingerprint

PROFILE = {
    "model": "nandakishorm/vega-08b-public-intents",
    "revision": "42ac8d4fa9efb61f96e53d80962cf28f6dfe667e",
    "backbone_model": "Qwen/Qwen3.5-0.8B",
    "backbone_revision": "2fc06364715b967f1860aea9cf38778875588b17",
}
DEFAULTS = {
    "epochs": 2,
    "batch_size": 8,
    "rank": 8,
    "max_steps": 0,
    "learning_rate": 0.0003,
    "gate_weight": 1.0,
    "offline": False,
}


def request_body(config):
    return dumps({"format": "domain-local-vega-study-v1", "configuration": config})


def validate_binding(config, args, hashes, negative_hashes, questions, preflight):
    contract = config["vega_contract"]
    if config["backend"] != "vega" or any(
        config[key] != value or getattr(args, key) != value
        for key, value in PROFILE.items()
    ):
        raise ValueError("Vega engine/backbone profile changed")
    if any(getattr(args, key) != config[key] for key in DEFAULTS):
        raise ValueError("Vega command options differ from the frozen request")
    if contract["source_sha256"] != source_fingerprint():
        raise ValueError("Vega runner source changed after submission")
    if (
        contract["schema_sha256"]
        != hashlib.sha256(dumps(questions).encode()).hexdigest()
    ):
        raise ValueError("Vega schema differs from its frozen request")
    if (
        contract["data_sha256"] != hashes
        or contract["gate_data_sha256"] != negative_hashes
        or contract["preflight"] != preflight
    ):
        raise ValueError("Vega paired inputs or calibration reservation changed")


def validate_request(args, hashes, negative_hashes, questions, preflight):
    """Refuse changed worker inputs/options before importing or loading a model."""
    path = getattr(args, "local_study_request", None)
    if path is None:
        return None
    body = Path(path).read_bytes()
    if len(body) > 65536:
        raise ValueError("Local Vega study request exceeds 64 KB")
    try:
        request = json.loads(body)
        if (
            set(request) != {"format", "configuration"}
            or request["format"] != "domain-local-vega-study-v1"
        ):
            raise ValueError("Unsupported local Vega study request")
        config = request["configuration"]
        if body != request_body(config).encode() or config["backend"] != "vega":
            raise ValueError("Local Vega study request is not canonical")
        validate_binding(config, args, hashes, negative_hashes, questions, preflight)
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed local Vega study request") from exc
    return hashlib.sha256(body).hexdigest()
