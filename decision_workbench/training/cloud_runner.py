"""Verify a frozen study and dispatch its unchanged native/Unsloth configuration."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from decision_workbench.data import dumps
from .artifacts import file_sha
from .contracts import SPLITS, load_splits
from .provenance import source_fingerprint

MAX_INPUT = 256 * 1024**2
MEMBERS = {"request.json", "data/manifest.json", *[f"data/{s}.jsonl" for s in SPLITS]}


def sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("Use an exact SHA-256 identity")
    return value


def ordinary(path):
    path = Path(path).absolute()
    for parent in (path, *path.parents):
        if parent.is_symlink() or (
            hasattr(parent, "is_junction") and parent.is_junction()
        ):
            raise ValueError("Cloud input/work paths must not traverse links")
    return path


def prepare_archive(archive, work, archive_sha256, request_sha256):
    """Accept only the bounded, uncompressed six-file archive emitted by the platform."""
    archive, work = ordinary(archive), ordinary(work)
    sha(archive_sha256)
    sha(request_sha256)
    if not archive.is_file() or archive.stat().st_size > MAX_INPUT + 2 * 1024**2:
        raise ValueError("Frozen study archive exceeds the input bound")
    if file_sha(archive) != archive_sha256:
        raise ValueError("Frozen study archive hash differs")
    work.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            if len(entries) != len(MEMBERS) or {e.filename for e in entries} != MEMBERS:
                raise ValueError("Frozen study archive inventory differs")
            if sum(e.file_size for e in entries) > MAX_INPUT + 1024**2 + 65536:
                raise ValueError("Frozen study expanded input exceeds the bound")
            for entry in entries:
                limit = (
                    65536
                    if entry.filename == "request.json"
                    else (
                        1024**2 if entry.filename == "data/manifest.json" else MAX_INPUT
                    )
                )
                mode = entry.external_attr >> 16
                if (
                    entry.compress_type != zipfile.ZIP_STORED
                    or entry.flag_bits & 1
                    or entry.is_dir()
                    or entry.file_size > limit
                    or mode & 0o170000 not in (0, 0o100000)
                ):
                    raise ValueError("Unsupported frozen study archive entry")
                destination = work / entry.filename
                destination.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(entry) as reader, destination.open("xb") as writer:
                    total = 0
                    while chunk := reader.read(1024**2):
                        total += len(chunk)
                        if total > limit:
                            raise ValueError(
                                "Frozen study entry exceeds its byte bound"
                            )
                        writer.write(chunk)
        request_path = work / "request.json"
        if file_sha(request_path) != request_sha256:
            raise ValueError("Frozen study request hash differs")
        request = json.loads(request_path.read_bytes())
        if (
            request.get("format") != "domain-remote-study-v1"
            or request.get("source_sha256") != source_fingerprint()
        ):
            raise ValueError("Cloud trainer package differs from the frozen study")
        rows, hashes = load_splits(work / "data")
        schema_sha = hashlib.sha256(
            dumps(rows["train"][0]["questions"]).encode()
        ).hexdigest()
        snapshot_sha = hashlib.sha256(dumps(rows).encode()).hexdigest()
        manifest = json.loads((work / "data/manifest.json").read_bytes())
        if (
            hashes != request.get("data_sha256")
            or schema_sha != request.get("schema_sha256")
            or snapshot_sha != request.get("snapshot_sha256")
            or manifest
            != {s: {"rows": len(rows[s]), "sha256": hashes[s]} for s in SPLITS}
        ):
            raise ValueError("Frozen data/schema/snapshot identities differ")
        return request
    except BaseException:
        shutil.rmtree(work)
        raise


def trainer_command(request, work, output, interpreter=sys.executable):
    """Construct arguments without a shell; there are no caller-provided commands."""
    config = request["configuration"]
    backend = config.get("backend")
    if backend not in ("laya-native", "unsloth"):
        raise ValueError("Cloud study requires native Laya or Unsloth")
    if (
        not isinstance(config.get("model"), str)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", config["model"])
        or not re.fullmatch(r"[a-f0-9]{40}", config.get("revision", ""))
    ):
        raise ValueError("Cloud checkpoint must have a pinned Hub revision")
    names = {
        "epochs": (1, 20),
        "batch_size": (1, 32),
        "grad_accum": (1, 128),
        "max_length": (64, 16384),
    }
    if backend == "unsloth":
        names.update(rank=(1, 128), max_steps=(1, 100000))
    forbidden = (
        ("rank", "max_steps", "full_precision")
        if backend == "laya-native"
        else ("train_encoder",)
    )
    if any(name in config for name in forbidden):
        raise ValueError("Cloud study contains options for a different trainer")
    module = "laya_runner" if backend == "laya-native" else "unsloth_runner"
    command = [
        str(interpreter),
        "-m",
        "decision_workbench.training." + module,
        "--data",
        str(Path(work) / "data"),
        "--out",
        str(output),
        "--study-request",
        str(Path(work) / "request.json"),
        "--model",
        config["model"],
        "--revision",
        config["revision"],
    ]
    for name, (low, high) in names.items():
        if name in config:
            value = config[name]
            if value is None and name in ("max_length", "max_steps"):
                continue
            if type(value) is not int or not low <= value <= high:
                raise ValueError("Invalid frozen trainer option: " + name)
            command.extend(["--" + name.replace("_", "-"), str(value)])
    flag = "train_encoder" if backend == "laya-native" else "full_precision"
    if flag in config:
        if type(config[flag]) is not bool:
            raise ValueError("Invalid frozen trainer flag")
        if config[flag]:
            command.append("--" + flag.replace("_", "-"))
    if backend == "laya-native":
        command.extend(["--device", "cuda"])
    return command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--request-sha256", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    request = prepare_archive(
        args.archive, args.work, args.archive_sha256, args.request_sha256
    )
    output = ordinary(args.out)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("Cloud trainer output must be new or empty")
    command = trainer_command(request, args.work, output)
    if not args.check_only:
        subprocess.run(command, check=True, shell=False)
        from decision_workbench.platform.remote import validate_export

        questions = load_splits(Path(args.work) / "data")[0]["train"][0]["questions"]
        validate_export(
            output,
            file_sha(output / "manifest.json"),
            {
                **request,
                "questions": questions,
                "request_sha256": args.request_sha256,
            },
        )
    print(
        dumps(
            {
                "study_id": request["study_id"],
                "source_sha256": request["source_sha256"],
                "archive_sha256": args.archive_sha256,
                "request_sha256": args.request_sha256,
                "training_executed": not args.check_only,
            }
        )
    )


if __name__ == "__main__":
    main()
