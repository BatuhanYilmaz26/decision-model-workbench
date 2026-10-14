"""Shared audited input contract for all decision-head training adapters."""

import hashlib
import re
from pathlib import Path
from decision_workbench.data import read_records, state_key, validate

SPLITS = ("train", "validation", "calibration", "test")


def validate_splits(rows):
    if set(rows) != set(SPLITS) or any(not rows[name] for name in SPLITS):
        raise ValueError(
            "Provide four nonempty train/validation/calibration/test splits"
        )
    questions = rows["train"][0]["questions"]
    for name in SPLITS:
        ids = set()
        for row in rows[name]:
            validate(row, require_review=True)
            if row["id"] in ids or row["questions"] != questions:
                raise ValueError(
                    "Duplicate ids or inconsistent project schemas in training inputs"
                )
            ids.add(row["id"])
            if name != "train" and row["metadata"]["origin"] != "real":
                raise ValueError("Held-out splits must contain reviewed real data")
    for index, a in enumerate(SPLITS):
        for b in SPLITS[index + 1 :]:
            for field in ("id", "group_id"):
                if {r[field] for r in rows[a]} & {r[field] for r in rows[b]}:
                    raise ValueError(f"{field} leakage between {a} and {b}")
            if {state_key(r) for r in rows[a]} & {state_key(r) for r in rows[b]}:
                raise ValueError(f"Identical states in {a} and {b}")
    holdout = {r["id"] for name in SPLITS[1:] for r in rows[name]}
    if any(set(r["metadata"].get("seed_ids", [])) & holdout for r in rows["train"]):
        raise ValueError("Synthetic training lineage contains held-out seeds")
    from decision_workbench.temporal import audit_splits

    audit_splits(rows)
    from decision_workbench.semantic import enforce_edges

    enforce_edges(rows)
    return questions


def load_splits(folder):
    rows, hashes = {}, {}
    for name in SPLITS:
        file = Path(folder) / (name + ".jsonl")
        body = file.read_bytes()
        hashes[name] = hashlib.sha256(body).hexdigest()
        rows[name] = read_records(body, file.name)
    validate_splits(rows)
    return rows, hashes


def pinned_checkpoint(model, revision, cache=None):
    if Path(model).is_dir():
        return Path(model).resolve()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", model) or not re.fullmatch(
        r"[a-f0-9]{40}", revision or ""
    ):
        raise ValueError("A Hub checkpoint requires an exact 40-character revision")
    from huggingface_hub import snapshot_download

    return Path(
        snapshot_download(
            model,
            revision=revision,
            cache_dir=cache,
            allow_patterns=[
                "*.json",
                "*.safetensors",
                "*.txt",
                "*.model",
                "encoder/*",
                "tokenizer/*",
            ],
            ignore_patterns=["*.py", "*.bin", "*.pt", "*.pkl"],
        )
    )
