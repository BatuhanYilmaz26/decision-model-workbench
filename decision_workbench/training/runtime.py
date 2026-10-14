"""A resident exported-head process; stdout carries only framed JSON responses."""

from .process_guard import arm_parent_guard

arm_parent_guard()

import argparse
import contextlib
import json
import math
import sys
import traceback
from pathlib import Path
from .artifacts import verify_manifest
from .engines import load_engine
from .metrics import normalize_answers
from decision_workbench.data import validate_questions

PREFIX = "@@DMT_RESULT "


def emit(body):
    sys.stdout.write(
        PREFIX + json.dumps(body, ensure_ascii=True, allow_nan=False) + "\n"
    )
    sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", required=True)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--manifest-sha", required=True)
    parser.add_argument("--schema-file", required=True)
    args = parser.parse_args()
    folder = Path(args.folder)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            questions = json.loads(
                Path(args.schema_file).read_text(encoding="utf-8-sig")
            )
            validate_questions(questions)
            manifest = verify_manifest(
                folder, args.manifest_sha, args.backend, questions
            )
            engine = load_engine(args.backend, folder / manifest["checkpoint"])
        stamps = {
            name: (folder / name).stat().st_mtime_ns for name in manifest["files"]
        }
        emit({"id": "ready", "ok": True, "backend": args.backend})
    except Exception:
        traceback.print_exc(file=sys.stderr)
        emit(
            {
                "id": "ready",
                "ok": False,
                "error": "Model load or integrity qualification failed",
            }
        )
        return
    for line in sys.stdin:
        request_id = None
        try:
            if len(line.encode("utf-8")) > 100000:
                raise ValueError("Inference request exceeds the runtime bound")
            request = json.loads(line)
            request_id = request["id"]
            if set(request) != {"id", "state", "threshold"}:
                raise ValueError("Unknown inference fields")
            if (
                not isinstance(request["state"], (str, dict, list))
                or not request["state"]
            ):
                raise ValueError("Provide nonempty text or JSON state")
            threshold = request["threshold"]
            if (
                isinstance(threshold, bool)
                or not isinstance(threshold, (int, float))
                or not math.isfinite(threshold)
                or not 0 <= threshold <= 1
            ):
                raise ValueError("Threshold must be finite and in [0,1]")
            for name, stamp in stamps.items():
                path = folder / name
                if (
                    not path.is_file()
                    or path.is_symlink()
                    or path.stat().st_mtime_ns != stamp
                ):
                    raise ValueError("Loaded artifact changed; refuse inference")
            with contextlib.redirect_stdout(sys.stderr):
                result = normalize_answers(
                    engine.predict(request["state"], questions),
                    questions,
                    request["threshold"],
                )
            emit({"id": request_id, "ok": True, "answers": result})
        except ValueError as exc:
            emit({"id": request_id, "ok": False, "validation_error": str(exc)[:500]})
        except Exception:
            traceback.print_exc(file=sys.stderr)
            emit(
                {
                    "id": request_id,
                    "ok": False,
                    "error": "Model inference failed; inspect private runtime log",
                }
            )


if __name__ == "__main__":
    main()
