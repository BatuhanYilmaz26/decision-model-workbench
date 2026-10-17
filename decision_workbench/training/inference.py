"""Local exported-head inference using the same calibrated engine as the service."""

import argparse
import json
from pathlib import Path
from decision_workbench.data import validate_questions, dumps


def predict_export(model_path, state, questions, backend="unsloth"):
    validate_questions(questions)
    if (
        not isinstance(state, (str, dict, list))
        or not state
        or len(dumps(state).encode()) > 64000
    ):
        raise ValueError("Provide a nonempty state under 64 KB")
    checkpoint = Path(model_path)
    if not checkpoint.is_dir():
        raise ValueError("Provide a local trained export directory")
    from .engines import load_engine

    if backend == "liquid-omni":
        from .artifacts import file_sha, verify_manifest

        manifest = verify_manifest(
            checkpoint, file_sha(checkpoint / "manifest.json"), backend, questions
        )
        checkpoint = checkpoint / manifest["checkpoint"]
    return load_engine(backend, checkpoint).predict(state, questions)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument(
        "--backend",
        choices=[
            "unsloth",
            "laya-native",
            "liquid-trl",
            "liquid-unsloth",
            "liquid-omni",
        ],
        default="unsloth",
    )
    args = parser.parse_args()
    try:
        request = json.loads(Path(args.request).read_text(encoding="utf-8-sig"))
        answers = predict_export(
            args.model, request["state"], request["questions"], args.backend
        )
        print(json.dumps(answers, indent=2))
    except (ValueError, KeyError, ImportError, OSError) as exc:
        parser.exit(1, f"Inference failed: {exc}\n")


if __name__ == "__main__":
    main()
