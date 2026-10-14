"""Bind externally trained exports to a previously frozen platform study."""

import hashlib
import json
from pathlib import Path
from decision_workbench.data import dumps


def source_fingerprint():
    package = Path(__file__).resolve().parents[1]
    files = {
        p.relative_to(package).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(package.rglob("*.py"))
    }
    return hashlib.sha256(dumps(files).encode()).hexdigest()


def training_provenance(args, hashes):
    result = {"source_sha256": source_fingerprint(), "remote_study_sha256": None}
    request_path = getattr(args, "study_request", None)
    if request_path:
        body = Path(request_path).read_bytes()
        if len(body) > 65536:
            raise ValueError("Study request exceeds 64 KB")
        request = json.loads(body)
        config = request["configuration"]
        if (
            request["format"] != "domain-remote-study-v1"
            or request["data_sha256"] != hashes
        ):
            raise ValueError("Remote study input hashes differ from the frozen request")
        if request["source_sha256"] != result["source_sha256"]:
            raise ValueError("Training package source differs from the frozen study")
        for name in (
            "model",
            "revision",
            "epochs",
            "batch_size",
            "grad_accum",
            "max_length",
            "rank",
            "max_steps",
            "full_precision",
            "train_encoder",
        ):
            if name in config and getattr(args, name, None) != config[name]:
                raise ValueError(
                    "Training configuration differs from the frozen study: " + name
                )
        backend = "laya-native" if hasattr(args, "train_encoder") else "unsloth"
        if config["backend"] != backend:
            raise ValueError("Remote study backend differs from trainer")
        result["remote_study_sha256"] = hashlib.sha256(body).hexdigest()
    return result
