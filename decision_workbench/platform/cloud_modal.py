"""Owner-selected Modal GPU Sandbox lifecycle; volume/tags are writer attestations."""

from decision_workbench.training.transfer_limits import (
    MAX_ARTIFACT_BYTES,
    transfer_seconds,
)

import os
import re
from .cloud_aws import CloudUnavailable
from .store import digest
from decision_workbench.training.artifacts import file_sha

SDK_VERSION = "1.6.1"
REGIONS = frozenset(
    (
        "us",
        "us-east",
        "us-central",
        "us-south",
        "us-west",
        "eu",
        "eu-west",
        "eu-north",
        "eu-south",
        "ap",
        "ap-northeast",
        "ap-southeast",
        "ap-south",
        "ap-melbourne",
        "jp",
        "au",
        "uk",
        "ca",
        "me",
        "sa",
        "af",
        "mx",
    )
)
GPUS = frozenset(("T4", "L4", "A10", "L40S", "A100-40GB", "A100-80GB", "H100!", "H200"))


def validate_profile(p):
    from .cloud_training_profile import provider_fields

    p = provider_fields(p)
    fields = {
        "id",
        "provider",
        "tenants",
        "credential_prefix",
        "account_id",
        "region",
        "environment",
        "app_name",
        "app_id",
        "volume_name",
        "volume_id",
        "modal_image_id",
        "image_digest",
        "prefix",
        "gpu",
        "cpu",
        "memory_mb",
        "max_runtime_seconds",
        "max_runs_day",
        "max_inflight",
        "max_artifact_bytes",
        "source_sha256",
    }
    if not isinstance(p, dict) or set(p) != fields or p["provider"] != "modal":
        raise ValueError("Invalid Modal profile fields")
    patterns = {
        "id": r"[A-Za-z0-9_-]{1,64}",
        "credential_prefix": r"DMT_CLOUD_[A-Z0-9_]{1,64}",
        "account_id": r"[A-Za-z0-9_-]{1,100}",
        "environment": r"[A-Za-z0-9_-]{1,64}",
        "app_name": r"[A-Za-z0-9_-]{1,64}",
        "app_id": r"ap-[A-Za-z0-9]{1,80}",
        "volume_name": r"[A-Za-z0-9_-]{1,64}",
        "volume_id": r"vo-[A-Za-z0-9]{1,80}",
        "modal_image_id": r"im-[A-Za-z0-9]{1,80}",
        "prefix": r"[A-Za-z0-9_-]{1,80}",
        "source_sha256": r"[a-f0-9]{64}",
        "image_digest": r"[a-z0-9][a-z0-9./_-]+@sha256:[a-f0-9]{64}",
    }
    for k, pattern in patterns.items():
        if not isinstance(p[k], str) or not re.fullmatch(pattern, p[k]):
            raise ValueError("Invalid Modal identity: " + k)
    if p["region"] not in REGIONS or p["gpu"] not in GPUS:
        raise ValueError("Choose one reviewed Modal region and exact single GPU")
    if (
        not isinstance(p["tenants"], list)
        or not 1 <= len(p["tenants"]) <= 100
        or any(not isinstance(t, str) or not 1 <= len(t) <= 120 for t in p["tenants"])
        or len(set(p["tenants"])) != len(p["tenants"])
    ):
        raise ValueError("Modal profile must name approved tenants")
    for k, low, high in (
        ("cpu", 1, 16),
        ("memory_mb", 2048, 524288),
        ("max_runtime_seconds", 300, 14400),
        ("max_runs_day", 1, 100),
        ("max_inflight", 1, 4),
        ("max_artifact_bytes", 1024, MAX_ARTIFACT_BYTES),
    ):
        if type(p[k]) is not int or not low <= p[k] <= high:
            raise ValueError("Invalid Modal limit: " + k)


def name(run):
    if not re.fullmatch(r"[a-f0-9]{32}", run["id"]):
        raise ValueError("Invalid Modal run identity")
    return "dmt-" + run["id"]


def valid_job_id(run, identifier):
    name(run)
    return isinstance(identifier, str) and bool(
        re.fullmatch(r"sb-[A-Za-z0-9]{22}", identifier)
    )


def object_key(run, kind):
    name(run)
    suffix = {
        "input": "inputs/study.zip",
        "output": "outputs/model.tar.gz",
        "result": "outputs/result.json",
    }
    return "/" + run["profile"]["prefix"] + "/" + run["id"] + "/" + suffix[kind]


def artifact_location(run, uri):
    expected = (
        "modal-volume://" + run["profile"]["volume_id"] + object_key(run, "output")
    )
    if uri != expected:
        raise ValueError("Modal artifact differs from the approved volume path")
    return object_key(run, "output")


def plan(run):
    p = run["profile"]
    for k in ("archive_sha256", "request_sha256"):
        if not isinstance(run.get(k), str) or not re.fullmatch(r"[a-f0-9]{64}", run[k]):
            raise ValueError("Invalid Modal frozen input hash")
    return {
        "name": name(run),
        "image_id": p["modal_image_id"],
        "app_id": p["app_id"],
        "volume_id": p["volume_id"],
        "command": [
            "python",
            "-m",
            "decision_workbench.training.modal_runner",
            "--archive",
            "/runs" + object_key(run, "input"),
            "--archive-sha256",
            run["archive_sha256"],
            "--request-sha256",
            run["request_sha256"],
            "--run-id",
            run["id"],
            "--source-sha256",
            p["source_sha256"],
            "--output",
            "/runs/" + p["prefix"] + "/" + run["id"] + "/outputs",
            "--max-artifact-bytes",
            str(p["max_artifact_bytes"]),
            "--max-runtime-seconds",
            str(p["max_runtime_seconds"] - 120),
        ],
        "gpu": p["gpu"],
        "cpu": [p["cpu"], p["cpu"]],
        "memory": [p["memory_mb"], p["memory_mb"]],
        "timeout": p["max_runtime_seconds"],
        "region": p["region"],
        "runtime": "gvisor",
        "mount": "/runs",
        "env": {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
        "block_network": True,
    }


def tags(run):
    return {
        "dmt_run": run["id"],
        "dmt_plan": digest(plan(run)),
        "dmt_input": run["archive_sha256"],
        "dmt_source": run["profile"]["source_sha256"],
    }


def validate_result(run, marker):
    from .cloud_volume import validate_result as validate

    return validate(run, marker, "domain-modal-result-v1")


class ModalGateway:
    def __init__(self, profile, rpc):
        self.profile, self.rpc = profile, rpc

    @classmethod
    def from_operator(cls, profile):
        validate_profile(profile)
        from .modal_transport import run_process

        prefix = profile["credential_prefix"]
        credentials = [
            os.environ.get(prefix + suffix) for suffix in ("_TOKEN_ID", "_TOKEN_SECRET")
        ]
        if any(
            not isinstance(x, str) or not 1 <= len(x) <= 16384 or "\n" in x or "\r" in x
            for x in credentials
        ):
            raise CloudUnavailable("Selected Modal credentials are unavailable")
        return cls(
            profile, lambda op, body: run_process(profile, credentials, op, body)
        )

    def close(self):
        pass  # Each SDK operation uses a separate bounded process/client.

    def identity(self):
        observed = self.rpc("identity", {})
        if observed != {
            k: self.profile[k] for k in ("account_id", "app_id", "volume_id")
        }:
            raise CloudUnavailable("Modal workspace/app/volume identity differs")
        return observed

    def upload(self, run, archive):
        if (
            archive.stat().st_size > 256 * 1024**2
            or file_sha(archive) != run["archive_sha256"]
        ):
            raise ValueError("Frozen Modal input exceeds its bound or differs")
        result = self.rpc("upload", {"run": run, "archive": str(archive.resolve())})
        if result != {
            "sha256": run["archive_sha256"],
            "bytes": archive.stat().st_size,
            "volume_id": self.profile["volume_id"],
        }:
            raise CloudUnavailable("Modal uploaded input receipt differs")
        return result

    def create(self, run):
        if (
            not run.get("input_receipt")
            or run["input_receipt"].get("sha256") != run["archive_sha256"]
        ):
            raise CloudUnavailable(
                "Modal creation requires its committed input receipt"
            )
        result = self.rpc("create", {"run": run})
        self.check(run, result)
        return {
            "provider_job_id": result["provider_job_id"],
            "plan_sha256": digest(plan(run)),
        }

    @staticmethod
    def check(run, result):
        if (
            not isinstance(result, dict)
            or not valid_job_id(run, result.get("provider_job_id"))
            or result.get("tags") != tags(run)
            or (
                run.get("provider_job_id") is not None
                and result["provider_job_id"] != run["provider_job_id"]
            )
        ):
            raise CloudUnavailable("Modal Sandbox identity or writer binding differs")

    def describe(self, run):
        result = self.rpc("observe", {"run": run})
        self.check(run, result)
        code = result.get("exit_code")
        if code is not None and (type(code) is not int or not 0 <= code <= 255):
            raise CloudUnavailable("Modal exit evidence is invalid")
        marker = result.get("training_result")
        if marker is not None:
            validate_result(run, marker)
        if run.get("training_result") is not None and marker != run["training_result"]:
            raise CloudUnavailable("Modal training result changed or disappeared")
        # A shutdown commit may not yet be visible. Missing success evidence never
        # releases an uncertain run, and a later owner observation may resolve it.
        status = (
            "InProgress" if code is None or code == 0 and marker is None else "Failed"
        )
        if code == 0 and marker is not None and marker["status"] == "succeeded":
            status = "Completed"
        elif code in (137, 143) and run.get("termination_attempted"):
            status = "Stopped"
        return {
            "provider_job_id": result["provider_job_id"],
            "status": status,
            "provider_status": "RUNNING" if code is None else "EXITED",
            "exit_code": code,
            "plan_sha256": digest(plan(run)),
            "training_result": marker,
            "output_ready": marker is not None and marker["status"] == "succeeded",
            "artifact_uri": (
                "modal-volume://"
                + self.profile["volume_id"]
                + object_key(run, "output")
                if status == "Completed"
                else None
            ),
        }

    def stop(self, run):
        result = self.rpc("stop", {"run": run})
        self.check(run, result)
        if result.get("termination_acknowledged") is not True:
            raise CloudUnavailable("Modal termination acknowledgement is unknown")
        return {"termination_acknowledged": True}

    def download(self, run, uri, target):
        artifact_location(run, uri)
        observation = self.describe(run)
        if observation["status"] != "Completed":
            raise CloudUnavailable("Only a bound exited Modal export can be retrieved")
        marker = observation["training_result"]
        result = self.rpc(
            "download", {"run": run, "target": str(target.resolve()), "marker": marker}
        )
        if (
            result
            != {"sha256": marker["artifact_sha256"], "bytes": marker["artifact_bytes"]}
            or not target.is_file()
            or target.stat().st_size != marker["artifact_bytes"]
            or file_sha(target) != marker["artifact_sha256"]
        ):
            raise CloudUnavailable("Modal retrieved archive differs")
        if self.describe(run)["training_result"] != marker:
            raise CloudUnavailable("Modal export marker changed during retrieval")
        return {
            **result,
            "volume_id": self.profile["volume_id"],
            "writer_marker": marker,
        }
