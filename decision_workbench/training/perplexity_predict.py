"""Predict from a complete native domain export with a pinned manifest."""

from .process_guard import arm_parent_guard

arm_parent_guard()

import argparse
import json
from pathlib import Path

from decision_workbench.json_tools import strict_json
from .artifacts import verify_manifest
from .metrics import normalize_answers
from .perplexity_runtime import PerplexityEngine


def predict(folder, manifest_sha, request, device="auto", threshold=0.0):
    folder = Path(folder).resolve()
    with Path(request).open("rb") as stream:
        body = stream.read(64001)
    if len(body) > 64000:
        raise ValueError("Native request exceeds 64,000 bytes")
    value = strict_json(body)
    if (
        not isinstance(value, dict)
        or set(value) != {"state"}
        or not isinstance(value["state"], (str, dict, list))
        or not value["state"]
    ):
        raise ValueError(
            "Provide exactly one nonempty text/JSON state field; the domain schema comes from the export"
        )
    metadata_path = folder / "checkpoint/dmt_perplexity.json"
    if metadata_path.stat().st_size > 1024**2:
        raise ValueError("Native domain metadata exceeds 1 MiB")
    metadata = strict_json(metadata_path.read_bytes())
    if not isinstance(metadata, dict) or not isinstance(
        metadata.get("questions"), dict
    ):
        raise ValueError("Native domain metadata must contain the frozen questions")
    questions = metadata["questions"]
    from math import isfinite

    if (
        type(threshold) not in (int, float)
        or not isfinite(threshold)
        or not 0 <= threshold <= 1
    ):
        raise ValueError("Abstention threshold must be finite and in [0,1]")
    manifest = verify_manifest(folder, manifest_sha, "perplexity-native", questions)
    if manifest["checkpoint"] != "checkpoint":
        raise ValueError("Native export checkpoint path differs")
    engine = PerplexityEngine(folder / "checkpoint", device=device)
    answers = normalize_answers(
        engine.predict(value["state"], questions), questions, threshold
    )
    return {
        "model": metadata["model"],
        "revision": metadata["revision"],
        "manifest_sha256": manifest_sha,
        "answers": answers,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("export", "manifest-sha", "request"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--threshold", type=float, default=0.0)
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                predict(
                    args.export,
                    args.manifest_sha,
                    args.request,
                    args.device,
                    args.threshold,
                ),
                ensure_ascii=False,
                allow_nan=False,
            )
        )
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        parser.exit(1, f"Perplexity native prediction failed: {exc}\n")


if __name__ == "__main__":
    main()
