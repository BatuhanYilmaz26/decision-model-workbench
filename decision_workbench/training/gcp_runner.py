"""Google training-container wrapper: generation-bound input, local trainer, conditional output."""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from decision_workbench.platform.cloud_gcp import GcpGateway, validate_profile, parent
from decision_workbench.training.cloud_runner import ordinary
from decision_workbench.training.cloud_packaging import pack_export


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-json", required=True)
    args = parser.parse_args(argv)
    if len(args.run_json) > 16384:
        raise ValueError("GCP runtime configuration exceeds its bound")
    run = json.loads(args.run_json)
    if not isinstance(run, dict) or set(run) != {
        "id",
        "profile",
        "archive_sha256",
        "request_sha256",
        "input_generation",
    }:
        raise ValueError("Invalid GCP runtime configuration")
    validate_profile(
        {
            **run["profile"],
            "credential_prefix": "DMT_CLOUD_TRAINING",
            "tenants": ["training"],
        }
    )
    parent(run)
    execute(run)


def execute(run, root=Path("/tmp/domain-gcp")):
    # This module is only launched inside the approved training container. The
    # operator control process never enables compute-metadata authentication.
    root = ordinary(root)
    root.mkdir(parents=True, exist_ok=False)
    gateway = GcpGateway.from_training(run["profile"])
    try:
        gateway.download_object(
            run, "input", root / "study.zip", run["input_generation"]
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "decision_workbench.training.cloud_runner",
                "--archive",
                str(root / "study.zip"),
                "--archive-sha256",
                run["archive_sha256"],
                "--request-sha256",
                run["request_sha256"],
                "--work",
                str(root / "study"),
                "--out",
                str(root / "export"),
            ],
            check=True,
        )
        pack_export(
            root / "export", root / "model.tar.gz", run["profile"]["max_artifact_bytes"]
        )
        receipt = gateway.upload_file(run, root / "model.tar.gz", "output")
        print(
            json.dumps(
                {
                    "format": "domain-gcp-output-v1",
                    "run_id": run["id"],
                    "output": receipt,
                },
                sort_keys=True,
            )
        )
    finally:
        gateway.close()


if __name__ == "__main__":
    main()
