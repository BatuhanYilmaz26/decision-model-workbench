"""Distinct eight-file portable Vega study; no provider calls or weight relabeling."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from decision_workbench.data import dumps, jsonl
from .contracts import SPLITS
from .vega_local import validate_binding

FORMAT = "domain-remote-vega-study-v1"


def request_for(config, study_id):
    binding = config["vega_contract"]
    return {
        "format": FORMAT,
        "study_id": study_id,
        "tenant": binding["tenant"],
        "project": binding["project"],
        "configuration": config,
        "snapshot_sha256": binding["positive_sha256"],
        "gate_snapshot_sha256": binding["gate_sha256"],
        "schema_sha256": binding["schema_sha256"],
        "source_sha256": binding["source_sha256"],
        "data_sha256": binding["data_sha256"],
        "gate_data_sha256": binding["gate_data_sha256"],
    }


def write_inputs(folder, positive, negative):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    for prefix, collection in (("", positive), ("gate_", negative)):
        for role in SPLITS:
            (folder / (prefix + role + ".jsonl")).write_bytes(
                jsonl(collection[role]).encode()
            )


def validate_request(args, hashes, negative_hashes, questions, preflight):
    path = getattr(args, "study_request", None)
    if path is None:
        return None
    if getattr(args, "local_study_request", None):
        raise ValueError("Select one local or remote Vega request")
    body = Path(path).read_bytes()
    if len(body) > 65536:
        raise ValueError("Remote Vega request exceeds 64 KB")
    try:
        request = json.loads(body)
        config = request["configuration"]
        if (
            request != request_for(config, request["study_id"])
            or body != dumps(request).encode()
        ):
            raise ValueError("Remote Vega request identity or canonical bytes differ")
        validate_binding(config, args, hashes, negative_hashes, questions, preflight)
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed remote Vega study request") from exc
    return hashlib.sha256(body).hexdigest()


def check_inputs(request, rows, hashes, negatives, negative_hashes, questions):
    from .vega_study import validate_settings

    config = request["configuration"]
    canonical = request_for(config, request["study_id"])
    if {
        k: v for k, v in request.items() if k not in ("questions", "request_sha256")
    } != canonical:
        raise ValueError("Remote Vega request identity differs")
    if (
        "request_sha256" in request
        and request["request_sha256"]
        != hashlib.sha256(dumps(canonical).encode()).hexdigest()
    ):
        raise ValueError("Remote Vega admission request checksum differs")
    if "questions" in request and request["questions"] != questions:
        raise ValueError("Remote Vega admission questions differ")
    validate_binding(
        config,
        SimpleNamespace(**config),
        hashes,
        negative_hashes,
        questions,
        validate_settings(SimpleNamespace(**config), rows, negatives, questions),
    )
