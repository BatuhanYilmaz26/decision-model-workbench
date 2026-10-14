"""Azure ML named-input/output wrapper around the unchanged frozen-study trainer."""

import argparse
import subprocess
import sys
from pathlib import Path
from decision_workbench.training.cloud_runner import ordinary
from decision_workbench.training.cloud_packaging import pack_export


def execute(
    archive,
    archive_sha256,
    request_sha256,
    artifact,
    max_artifact_bytes,
    root=Path("/tmp/domain-azure"),
):
    root = ordinary(root)
    root.mkdir(parents=True, exist_ok=False)
    artifact = ordinary(artifact)
    if artifact.exists():
        raise ValueError("Azure artifact output already exists")
    artifact.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "decision_workbench.training.cloud_runner",
            "--archive",
            str(ordinary(archive)),
            "--archive-sha256",
            archive_sha256,
            "--request-sha256",
            request_sha256,
            "--work",
            str(root / "study"),
            "--out",
            str(root / "export"),
        ],
        check=True,
    )
    pack_export(root / "export", artifact, max_artifact_bytes)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive", required=True, type=Path)
    p.add_argument("--archive-sha256", required=True)
    p.add_argument("--request-sha256", required=True)
    p.add_argument("--artifact", required=True, type=Path)
    p.add_argument("--max-artifact-bytes", required=True, type=int)
    args = p.parse_args(argv)
    if not 1024 <= args.max_artifact_bytes <= 32 * 1024**3:
        raise ValueError("Azure artifact bound is invalid")
    execute(
        args.archive,
        args.archive_sha256,
        args.request_sha256,
        args.artifact,
        args.max_artifact_bytes,
    )


if __name__ == "__main__":
    main()
