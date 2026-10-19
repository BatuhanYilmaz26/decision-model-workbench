"""Operator helper: hash an approved local BERT snapshot; does not download a model."""

import argparse
import importlib.metadata
import json
from pathlib import Path

from decision_workbench.training.semantic_runner import (
    MODEL_FILES,
    file_hash,
    verify_model,
)
from decision_workbench.platform.semantic import validate_profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--tenant", action="append", required=True)
    parser.add_argument("--project", action="append", required=True)
    parser.add_argument("--id", default="local-semantic")
    parser.add_argument("--optional", action="store_true")
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--max-rows", type=int, default=5000)
    parser.add_argument("--max-pairs", type=int, default=10000)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--memory-limit-mb", type=int, default=16384)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    folder = Path(args.model_dir).resolve()
    profile = {
        "id": args.id,
        "tenants": args.tenant,
        "projects": args.project,
        "required": not args.optional,
        "model": args.model,
        "revision": args.revision,
        "model_dir": str(folder),
        "files_sha256": {name: file_hash(folder / name) for name in MODEL_FILES},
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("torch", "transformers", "numpy", "safetensors", "tokenizers")
        },
        "max_tokens": args.max_tokens,
        "max_rows": args.max_rows,
        "max_pairs": args.max_pairs,
        "timeout_seconds": args.timeout_seconds,
        "memory_limit_mb": args.memory_limit_mb,
    }
    validate_profile(profile)
    verify_model(profile)
    target = Path(args.out)
    if target.exists():
        parser.error("Choose a new profile file; the previous one is preserved")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(profile, indent=2))
    print(
        "Pinned the selected local model files and installed CPU runtime profile. This is operator attestation of model ownership/revision, not source authentication."
    )


if __name__ == "__main__":
    main()
