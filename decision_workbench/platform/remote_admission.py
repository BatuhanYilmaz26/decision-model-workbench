"""Fenced worker admission of operator-staged remote exports."""

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from decision_workbench.data import dumps, write_splits
from decision_workbench.training.artifacts import file_sha, verify_manifest
from decision_workbench.training.resources import ResourceLease
from decision_workbench.training.process_guard import leased_process_options
from .remote import study_entity, validate_export
from .store import Conflict


def admit(worker, job, snapshot, questions, coverage, cancelled):
    store = worker.store
    config = job["config"]
    study = study_entity(store, job["tenant"], job["project"], config["study"])[
        "payload"
    ]
    request = {
        **study["request"],
        "questions": questions,
        "request_sha256": study["request_sha256"],
    }
    frozen = request["configuration"]
    if any(config.get(k) != v for k, v in frozen.items()):
        raise ValueError("Admission configuration differs from frozen remote study")
    incoming = store.folder / "incoming" / config["incoming_id"]
    validate_export(incoming, config["manifest_sha256"], request)
    job_folder = store.folder / "jobs" / job["id"]
    job_folder.mkdir(parents=True, exist_ok=False)
    if config["backend"] == "vega":
        from .vega_training import recheck
        from decision_workbench.training.vega_remote import write_inputs

        gate = recheck(worker.service, job, snapshot, questions)
        write_inputs(job_folder / "data", gate["positive_splits"], gate["splits"])
    else:
        split_manifest = write_splits(snapshot["splits"], job_folder / "data")
        if {k: v["sha256"] for k, v in split_manifest.items()} != request[
            "data_sha256"
        ]:
            raise ValueError("Admission snapshot bytes differ from frozen study")
    (job_folder / "request.json").write_text(dumps(request), encoding="utf-8")
    artifact_id = uuid.uuid4().hex
    artifact = store.folder / "artifacts" / artifact_id
    artifact.parent.mkdir(parents=True, exist_ok=True)
    registered = False
    try:
        # Input was already privately staged. Copy, then verify again before loading.
        shutil.copytree(incoming, artifact)
        validate_export(artifact, config["manifest_sha256"], request)
        argv = [
            sys.executable,
            "-m",
            "decision_workbench.training.admission_runner",
            "--export",
            str(artifact),
            "--data",
            str(job_folder / "data"),
            "--request",
            str(job_folder / "request.json"),
            "--manifest-sha256",
            config["manifest_sha256"],
        ]
        environment = os.environ.copy()
        environment.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        deadline = time.monotonic() + 4 * 60 * 60
        store.heartbeat(job, "Waiting for exclusive exported-runtime qualification")
        with (
            ResourceLease(store.folder / "gpu.lock").hold(
                timeout=240, cancelled=cancelled
            ) as model_lease,
            (job_folder / "qualification.log").open("w", encoding="utf-8") as log,
        ):
            process = subprocess.Popen(
                argv,
                stdout=log,
                stderr=subprocess.STDOUT,
                shell=False,
                **leased_process_options(model_lease, environment),
            )
            try:
                while process.poll() is None:
                    store.heartbeat(
                        job,
                        "Measuring local validation/calibration/test and second-load equivalence",
                    )
                    if cancelled.is_set():
                        raise Conflict("Cancelled")
                    if time.monotonic() > deadline:
                        raise ValueError(
                            "Admission exceeded the four-hour qualification limit"
                        )
                    cancelled.wait(1)
                if process.returncode:
                    raise ValueError(
                        "Remote export runtime qualification failed; inspect the private admission log"
                    )
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
        checksum = file_sha(artifact / "manifest.json")
        verify_manifest(artifact, checksum, config["backend"], questions)
        report = json.loads((artifact / "report.json").read_text(encoding="utf-8"))
        if (
            report.get("admission", {}).get("source_manifest_sha256")
            != config["manifest_sha256"]
        ):
            raise ValueError("Admission report lost its source export binding")
        model = store.add(
            "model",
            job["tenant"],
            job["project"],
            job["actor"],
            {
                "backend": config["backend"],
                "artifact_id": artifact_id,
                "model_sha256": checksum,
                "snapshot": config["snapshot"],
                "snapshot_sha256": snapshot["sha256"],
                "schema_sha256": request["schema_sha256"],
                "job_id": job["id"],
                "report": report,
                "configuration": config,
                "data_gate_counts": coverage,
                "remote_study": config["study"],
                "provider_receipt": config["provider_receipt"],
            },
            job=job,
        )
        registered = True
        return {
            "model": model["id"],
            "backend": config["backend"],
            "remote_study": config["study"],
            "release_required": True,
        }
    finally:
        if not registered and artifact.exists():
            shutil.rmtree(artifact)
