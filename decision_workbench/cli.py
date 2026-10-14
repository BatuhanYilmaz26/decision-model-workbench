"""Interactive intake and deterministic preparation."""

import argparse
import json
import os
from pathlib import Path
from .data import read_records, jsonl, prepare, write_splits, validate_questions
from .synthetic import generate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    wizard = sub.add_parser("wizard")
    wizard.add_argument("--out", required=True)
    for action in ("validate", "prepare", "generate"):
        p = sub.add_parser(action)
        p.add_argument("--input", required=True)
        p.add_argument("--spec")
        if action != "validate":
            p.add_argument("--out", required=True)
        if action == "prepare":
            p.add_argument("--seed", type=int, default=3407)
            p.add_argument(
                "--split-strategy",
                choices=("auto", "group-random", "chronological"),
                default="auto",
            )
        if action == "generate":
            p.add_argument(
                "--base-url", required=True, help="Chat API root ending in /v1"
            )
            p.add_argument("--model", required=True)
            p.add_argument("--count", type=int, default=10)
    args = parser.parse_args()
    try:
        if args.command == "wizard":
            description = input(
                "Describe your domain, decision policy, rare cases and required data in natural language: "
            ).strip()
            if not description:
                raise ValueError("Description cannot be empty")
            print("Define questions using the format in examples/support/spec.json.")
            schema_path = input("Path to a JSON question map (or spec.json): ").strip()
            schema = json.loads(Path(schema_path).read_text(encoding="utf-8-sig"))
            questions = schema.get("questions", schema)
            validate_questions(questions)
            source = input(
                "Path to a few labeled examples or real data (CSV/XLSX/JSONL), or leave empty to add later: "
            ).strip()
            spec = {"description": description, "questions": questions}
            destination = Path(args.out)
            if destination.exists():
                raise ValueError(
                    "Choose a new specification file to preserve previous work"
                )
            if source:
                rows = read_records(Path(source).read_bytes(), source, spec)
                print(f"Validated {len(rows)} seed rows. Keep seeds in training only.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(spec, indent=2), encoding="utf-8")
            print(
                f"Saved {args.out}. Prepare labels in the documented format before training."
            )
            return
        spec = (
            json.loads(Path(args.spec).read_text(encoding="utf-8-sig"))
            if args.spec
            else None
        )
        rows = read_records(Path(args.input).read_bytes(), args.input, spec)
        if args.command == "validate":
            print(f"Valid: {len(rows)} rows")
        elif args.command == "prepare":
            print(
                json.dumps(
                    write_splits(
                        prepare(rows, args.seed, split_strategy=args.split_strategy),
                        args.out,
                    ),
                    indent=2,
                )
            )
        else:
            if spec is None:
                raise ValueError("Generation needs --spec")
            destination = Path(args.out)
            if destination.exists():
                raise ValueError(
                    "Choose a new output file to preserve existing candidates"
                )
            candidates = generate(
                spec,
                rows,
                base_url=args.base_url,
                model=args.model,
                api_key=os.environ.get("TEACHER_API_KEY", ""),
                count=args.count,
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(jsonl(candidates), encoding="utf-8")
            print(f"Saved {len(candidates)} unreviewed candidates to {destination}")
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
