"""Hash-pinned, portable artifact manifests and export qualification."""

import hashlib
import json
import math
from pathlib import Path
from decision_workbench.data import dumps
from .metrics import normalize_answers

FORMAT = "domain-decision-bundle-v1"


def file_sha(path):
    value = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def inventory(folder):
    """Refuse links, including junctions, before accepting an export inventory."""
    found = {}
    for path in sorted(folder.rglob("*")):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Export contains a linked file or directory")
        if not path.resolve().is_relative_to(folder):
            raise ValueError("Export contains an external reference")
        if path.is_file() and path != folder / "manifest.json":
            found[path.relative_to(folder).as_posix()] = path
    return found


def write_manifest(folder, backend, questions, checkpoint="merged"):
    folder = Path(folder).resolve()
    files = {
        name: {"sha256": file_sha(path), "bytes": path.stat().st_size}
        for name, path in inventory(folder).items()
    }
    manifest = {
        "format": FORMAT,
        "backend": backend,
        "checkpoint": checkpoint,
        "schema_sha256": hashlib.sha256(dumps(questions).encode()).hexdigest(),
        "files": files,
    }
    path = folder / "manifest.json"
    path.write_text(dumps(manifest), encoding="utf-8")
    return file_sha(path)


def verify_manifest(folder, expected_sha, backend, questions):
    folder = Path(folder).resolve()
    path = folder / "manifest.json"
    try:
        if (
            path.stat().st_size > 1024 * 1024
            or (folder / "report.json").stat().st_size > 16 * 1024 * 1024
        ):
            raise ValueError("Export manifest/report exceeds the metadata size limit")
        if path.is_symlink() or file_sha(path) != expected_sha:
            raise ValueError("Model manifest integrity check failed")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if (
            manifest.get("format") != FORMAT
            or manifest.get("backend") != backend
            or manifest.get("schema_sha256")
            != hashlib.sha256(dumps(questions).encode()).hexdigest()
        ):
            raise ValueError("Model manifest does not match the pinned schema/backend")
        files = manifest.get("files")
        if not isinstance(files, dict) or not files or "report.json" not in files:
            raise ValueError("Export manifest is incomplete")
        found = inventory(folder)
        if set(found) != set(files):
            raise ValueError("Export file inventory differs from manifest")
        for name, metadata in files.items():
            target = found[name]
            if (
                target.stat().st_size != metadata["bytes"]
                or file_sha(target) != metadata["sha256"]
            ):
                raise ValueError("Export file integrity check failed")
        checkpoint = folder / manifest["checkpoint"]
        if not checkpoint.resolve().is_relative_to(folder) or not checkpoint.is_dir():
            raise ValueError("Invalid checkpoint manifest path")
        return manifest
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "Export is missing or malformed; integrity qualification failed"
        ) from exc


def compare_predictions(expected, actual, questions, tolerance):
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("Probability tolerance must be finite and nonnegative")
    if len(expected) != len(actual) or not expected:
        raise ValueError(
            "Reload comparison requires nonempty matching validation probes"
        )
    maximum = 0.0
    for before, after in zip(expected, actual):
        before = normalize_answers(before, questions)
        after = normalize_answers(after, questions)
        for name in questions:
            if before[name]["answer"] != after[name]["answer"]:
                raise ValueError("Reloaded checkpoint changes a validation decision")
            if before[name].get("model_abstained") != after[name].get(
                "model_abstained"
            ):
                raise ValueError("Reloaded checkpoint changes model abstention")
            for key, value in before[name]["probabilities"].items():
                maximum = max(maximum, abs(value - after[name]["probabilities"][key]))
    if not math.isfinite(maximum) or maximum > tolerance:
        raise ValueError("Reloaded checkpoint exceeds the fixed probability tolerance")
    return {
        "passed": True,
        "probes": len(expected),
        "max_probability_delta": maximum,
        "tolerance": tolerance,
        "same_answers": True,
    }
