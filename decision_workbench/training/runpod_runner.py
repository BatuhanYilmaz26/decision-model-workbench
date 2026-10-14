"""Linux Pod runner: local-disk training, network-volume export; exit is not termination."""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from decision_workbench.training.cloud_runner import ordinary
from decision_workbench.training.cloud_packaging import pack_export
from decision_workbench.training.artifacts import file_sha
from decision_workbench.training.provenance import source_fingerprint


def run_training(command, seconds):
    if os.name != "posix":
        raise ValueError(
            "RunPod process-group deadlines require a qualified Linux runtime"
        )
    process = subprocess.Popen(command, shell=False, start_new_session=True)
    try:
        code = process.wait(timeout=seconds)
        if code:
            raise subprocess.CalledProcessError(code, command)
    except BaseException:
        # Include the common-runner/trainer descendants, not just the wrapper PID.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        raise


def execute(
    archive,
    archive_sha256,
    request_sha256,
    run_id,
    source_sha256,
    output,
    max_artifact_bytes,
    max_runtime_seconds,
    root=Path("/tmp/domain-runpod"),
    trainer=run_training,
    result_format="domain-runpod-result-v1",
):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id) or any(
        not re.fullmatch(r"[a-f0-9]{64}", h)
        for h in (archive_sha256, request_sha256, source_sha256)
    ):
        raise ValueError("RunPod frozen identities are invalid")
    if (
        source_fingerprint() != source_sha256
        or not 1024 <= max_artifact_bytes <= 32 * 1024**3
        or not 60 <= max_runtime_seconds <= 14400
    ):
        raise ValueError("RunPod package source or runtime bounds differ")
    root, output, archive = ordinary(root), ordinary(output), ordinary(archive)
    root.mkdir(parents=True, exist_ok=False)
    output.mkdir(parents=True, exist_ok=False)
    marker = {
        "format": result_format,
        "run_id": run_id,
        "archive_sha256": archive_sha256,
        "request_sha256": request_sha256,
        "source_sha256": source_sha256,
        "status": "failed",
    }
    try:
        trainer(
            [
                sys.executable,
                "-m",
                "decision_workbench.training.cloud_runner",
                "--archive",
                str(archive),
                "--archive-sha256",
                archive_sha256,
                "--request-sha256",
                request_sha256,
                "--work",
                str(root / "study"),
                "--out",
                str(root / "export"),
            ],
            max_runtime_seconds,
        )
        packed = root / "model.tar.gz"
        pack_export(root / "export", packed, max_artifact_bytes)
        target = output / "model.tar.gz"
        with packed.open("rb") as data, target.open("xb") as stream:
            shutil.copyfileobj(data, stream, 1024**2)
            stream.flush()
            os.fsync(stream.fileno())
        if file_sha(target) != file_sha(packed):
            raise ValueError("RunPod mounted output differs from local export")
        marker.update(
            status="succeeded",
            artifact_sha256=file_sha(packed),
            artifact_bytes=packed.stat().st_size,
            manifest_sha256=file_sha(root / "export" / "manifest.json"),
        )
    finally:
        # Last-written, fsynced, no-overwrite marker; no storage/API keys enter this Pod.
        with (output / "result.json").open(
            "x", encoding="utf-8", newline="\n"
        ) as stream:
            stream.write(json.dumps(marker, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    return marker


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("archive-sha256", "request-sha256", "run-id", "source-sha256"):
        p.add_argument("--" + name, required=True)
    for name in ("archive", "output"):
        p.add_argument("--" + name, required=True, type=Path)
    p.add_argument("--max-artifact-bytes", type=int, required=True)
    p.add_argument("--max-runtime-seconds", type=int, required=True)
    execute(**vars(p.parse_args(argv)))


if __name__ == "__main__":
    main()
