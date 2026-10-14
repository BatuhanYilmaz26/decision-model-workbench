"""Offline CPU mean-pooling encoder and exhaustive bounded cosine audit."""

from .process_guard import arm_parent_guard

arm_parent_guard()

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path

from decision_workbench.data import dumps
from .provenance import source_fingerprint

MODEL_FILES = (
    "config.json",
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "vocab.txt",
)


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_model(profile):
    folder = Path(profile["model_dir"])
    for name, sha in profile["files_sha256"].items():
        path = folder / name
        if not path.is_file() or file_hash(path) != sha:
            raise ValueError("Semantic model/tokenizer file identity mismatch")
    config = json.loads((folder / "config.json").read_text(encoding="utf-8"))
    token_config = json.loads(
        (folder / "tokenizer_config.json").read_text(encoding="utf-8")
    )
    if token_config.get("auto_map") or (folder / "added_tokens.json").exists():
        raise ValueError(
            "Unbound tokenizer configuration is not supported by this profile"
        )
    if config.get("model_type") != "bert" or config.get("auto_map"):
        raise ValueError(
            "This semantic profile requires a standard BERT encoder without remote code"
        )
    return folder


def cosine_pairs(vectors, ids, threshold, max_pairs, cancelled=lambda: False):
    import numpy as np

    vectors = np.asarray(vectors, dtype=np.float32)
    if vectors.ndim != 2 or len(vectors) != len(ids) or not np.isfinite(vectors).all():
        raise ValueError("Semantic encoder returned invalid vectors")
    norms = np.linalg.norm(vectors, axis=1)
    if np.any(norms < 1e-12):
        raise ValueError("Semantic encoder returned a zero vector")
    vectors = vectors / norms[:, None]
    pairs = []
    for begin in range(0, len(ids), 64):
        if cancelled():
            raise ValueError("Semantic audit cancelled")
        scores = vectors[begin : begin + 64] @ vectors.T
        for local, other in np.argwhere(scores >= threshold):
            first = begin + int(local)
            if int(other) <= first:
                continue
            left, right = sorted((ids[first], ids[int(other)]))
            pairs.append(
                {
                    "id": hashlib.sha256(dumps([left, right]).encode()).hexdigest(),
                    "left": left,
                    "right": right,
                    "similarity": min(1.0, max(-1.0, float(scores[local, other]))),
                }
            )
            if len(pairs) > max_pairs:
                raise ValueError(
                    "Semantic candidate limit exceeded; no partial audit is accepted"
                )
    pairs.sort(key=lambda pair: (pair["left"], pair["right"]))
    return pairs, hashlib.sha256(vectors.tobytes(order="C")).hexdigest()


def execute(request):
    profile = request["profile"]
    from decision_workbench.platform.semantic import validate_profile

    validate_profile(profile)
    if request["source_sha256"] != source_fingerprint():
        raise ValueError("Semantic runner package differs from the frozen job")
    for package, version in profile["packages"].items():
        if importlib.metadata.version(package) != version:
            raise ValueError("Semantic runner dependency profile mismatch: " + package)
    rows = request["rows"]
    if not 1 <= len(rows) <= profile["max_rows"] or len({r["id"] for r in rows}) != len(
        rows
    ):
        raise ValueError("Semantic row capacity or identifier contract failed")
    threshold = request["threshold"]
    if (
        type(threshold) not in (int, float)
        or not math.isfinite(threshold)
        or not 0.5 <= threshold <= 1
    ):
        raise ValueError("Choose a finite semantic threshold between 0.5 and 1")
    folder = verify_model(profile)
    # Set before importing the model libraries. No Hub lookup, tokenizer
    # download, remote code, GPU tensor or serialized Python weights is used.
    os.environ.update(
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        HF_HUB_DISABLE_TELEMETRY="1",
        HF_TOKEN="",
        HF_HUB_DISABLE_IMPLICIT_TOKEN="1",
    )
    import torch
    from transformers import AutoModel, AutoTokenizer

    torch.set_num_threads(4)
    tokenizer = AutoTokenizer.from_pretrained(
        folder, local_files_only=True, trust_remote_code=False
    )
    model = (
        AutoModel.from_pretrained(
            folder, local_files_only=True, trust_remote_code=False, use_safetensors=True
        )
        .to("cpu")
        .float()
        .eval()
    )
    texts = [
        r["state"] if isinstance(r["state"], str) else dumps(r["state"]) for r in rows
    ]
    lengths = [
        len(tokenizer(text, truncation=False, add_special_tokens=True)["input_ids"])
        for text in texts
    ]
    if (
        max(lengths) > profile["max_tokens"]
        or profile["max_tokens"] > model.config.max_position_embeddings
    ):
        raise ValueError(
            "Semantic model token budget would truncate input; no partial-state audit is accepted"
        )
    chunks = []
    with torch.inference_mode():
        for start in range(0, len(texts), 16):
            inputs = tokenizer(
                texts[start : start + 16],
                padding=True,
                truncation=False,
                return_tensors="pt",
            )
            hidden = model(**inputs).last_hidden_state
            mask = inputs["attention_mask"].unsqueeze(-1).to(hidden.dtype)
            pooled = (hidden * mask).sum(1) / mask.sum(1).clamp_min(1)
            chunks.append(pooled.cpu().numpy())
    import numpy as np

    vectors = np.concatenate(chunks)
    pairs, embedding_sha = cosine_pairs(
        vectors, [r["id"] for r in rows], threshold, profile["max_pairs"]
    )
    verify_model(profile)
    return {
        "format": "domain-semantic-audit-v1",
        "input_sha256": request["input_sha256"],
        "source_sha256": source_fingerprint(),
        "threshold": threshold,
        "row_count": len(rows),
        "compared_pairs": len(rows) * (len(rows) - 1) // 2,
        "pairs": pairs,
        "embedding_sha256": embedding_sha,
        "dimension": int(vectors.shape[1]),
        "max_observed_tokens": max(lengths),
        "pooling": "attention-mask-mean-l2",
        "device": "cpu",
        "packages": profile["packages"],
        "scope": "Exhaustive cosine candidates above the frozen threshold; not proof of duplication or absence of all semantic leakage",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    body = Path(args.request).read_bytes()
    if len(body) > 64 * 1024 * 1024:
        parser.error("Semantic request exceeds 64 MiB")
    result = execute(json.loads(body))
    path = Path(args.out)
    if path.exists():
        parser.error("Semantic output already exists")
    with path.open("x", encoding="utf-8") as stream:
        stream.write(dumps(result))
        stream.flush()
        os.fsync(stream.fileno())


if __name__ == "__main__":
    main()
