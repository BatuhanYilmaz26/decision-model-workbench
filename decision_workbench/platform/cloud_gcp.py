"""Bounded Google CustomJob/GCS REST transport with selected service-account credentials.

No ambient operator credentials, endpoint overrides, automatic replay or cloud SDK retries.
"""

from decision_workbench.training.transfer_limits import (
    MAX_ARTIFACT_BYTES,
    transfer_seconds,
)

import base64
import copy
import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from .cloud_aws import CloudUnavailable
from decision_workbench.training.artifacts import file_sha

STATUS = {
    "JOB_STATE_QUEUED": "InProgress",
    "JOB_STATE_PENDING": "InProgress",
    "JOB_STATE_RUNNING": "InProgress",
    "JOB_STATE_SUCCEEDED": "Completed",
    "JOB_STATE_FAILED": "Failed",
    "JOB_STATE_CANCELLING": "Stopping",
    "JOB_STATE_CANCELLED": "Stopped",
}


def validate_profile(p):
    from .cloud_training_profile import provider_fields

    p = provider_fields(p)
    required = {
        "id",
        "provider",
        "tenants",
        "credential_prefix",
        "account_id",
        "project_id",
        "operator_service_account",
        "training_service_account",
        "region",
        "bucket",
        "prefix",
        "image_digest",
        "gpu",
        "machine_type",
        "volume_gb",
        "max_runtime_seconds",
        "max_runs_day",
        "max_inflight",
        "max_artifact_bytes",
        "source_sha256",
    }
    if not isinstance(p, dict) or set(p) != required or p["provider"] != "gcp":
        raise ValueError("Invalid GCP profile fields")
    patterns = {
        "id": r"[A-Za-z0-9_-]{1,64}",
        "credential_prefix": r"DMT_CLOUD_[A-Z0-9_]{1,64}",
        "account_id": r"[1-9][0-9]{5,19}",  # Immutable project number, not display name.
        "project_id": r"[a-z][a-z0-9-]{4,28}[a-z0-9]",
        "region": r"[a-z]+-[a-z]+[0-9]",
        "bucket": r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]",
        "prefix": r"[A-Za-z0-9_-]{1,80}",
        "source_sha256": r"[a-f0-9]{64}",
        "machine_type": r"(?:n1-standard-(4|8|16|32)|a3-ultragpu-8g)",
    }
    for key, pattern in patterns.items():
        if not isinstance(p[key], str) or not re.fullmatch(pattern, p[key]):
            raise ValueError("Invalid GCP resource identity: " + key)
    expected_gpu = (
        "NVIDIA_H200_141GB"
        if p["machine_type"] == "a3-ultragpu-8g"
        else "NVIDIA_TESLA_T4"
    )
    if p["gpu"] != expected_gpu:
        raise ValueError("Match n1-standard/T4 or a3-ultragpu-8g/eight H200 GPUs")
    for key in ("operator_service_account", "training_service_account"):
        if not isinstance(p[key], str) or not re.fullmatch(
            r"[a-z][a-z0-9-]{4,28}[a-z0-9]@"
            + re.escape(p["project_id"])
            + r"\.iam\.gserviceaccount\.com",
            p[key],
        ):
            raise ValueError("GCP identities must belong to the approved project")
    if not isinstance(p["image_digest"], str) or not re.fullmatch(
        re.escape(p["region"] + "-docker.pkg.dev/" + p["project_id"] + "/")
        + r"[a-z][a-z0-9_-]{0,62}/[a-z0-9_./-]+@sha256:[a-f0-9]{64}",
        p["image_digest"],
    ):
        raise ValueError(
            "Pin an Artifact Registry image in the same project and region"
        )
    if (
        not isinstance(p["tenants"], list)
        or not 1 <= len(p["tenants"]) <= 100
        or any(not isinstance(t, str) or not 1 <= len(t) <= 120 for t in p["tenants"])
        or len(set(p["tenants"])) != len(p["tenants"])
    ):
        raise ValueError("GCP profile must name approved tenants")
    for key, low, high in (
        ("volume_gb", 20, 1024),
        ("max_runtime_seconds", 60, 14400),
        ("max_runs_day", 1, 100),
        ("max_inflight", 1, 4),
        ("max_artifact_bytes", 1024, MAX_ARTIFACT_BYTES),
    ):
        if type(p[key]) is not int or not low <= p[key] <= high:
            raise ValueError("Invalid GCP limit: " + key)


def parent(run):
    p = run["profile"]
    if not re.fullmatch(r"[a-f0-9]{32}", run["id"]):
        raise ValueError("Invalid cloud run identity")
    return "projects/" + p["account_id"] + "/locations/" + p["region"]


def valid_job_id(run, identifier):
    return isinstance(identifier, str) and bool(
        re.fullmatch(
            re.escape(parent(run)) + r"/customJobs/[1-9][0-9]{0,24}", identifier
        )
    )


def object_key(run, kind):
    if kind not in ("input", "output"):
        raise ValueError("Invalid GCP object kind")
    parent(run)
    return (
        run["profile"]["prefix"]
        + "/"
        + run["id"]
        + ("/inputs/study.zip" if kind == "input" else "/outputs/model.tar.gz")
    )


def artifact_location(run, uri):
    expected = "gs://" + run["profile"]["bucket"] + "/" + object_key(run, "output")
    if uri != expected:
        raise ValueError("GCP artifact URI differs from the approved run prefix")
    return object_key(run, "output")


def plan(run):
    p = run["profile"]
    parent(run)
    receipt = run.get("input_receipt")
    if not isinstance(receipt, dict) or not re.fullmatch(
        r"[1-9][0-9]{0,24}", str(receipt.get("generation", ""))
    ):
        raise ValueError("GCP submission requires the uploaded input generation")
    runtime_run = {
        "id": run["id"],
        "profile": copy.deepcopy(p),
        "archive_sha256": run["archive_sha256"],
        "request_sha256": run["request_sha256"],
        "input_generation": str(receipt["generation"]),
    }
    return {
        "displayName": "dmt-" + run["id"],
        "labels": {"dmt_run": run["id"]},
        "jobSpec": {
            "serviceAccount": p["training_service_account"],
            "enableWebAccess": False,
            "enableDashboardAccess": False,
            "scheduling": {
                "timeout": str(p["max_runtime_seconds"]) + "s",
                "disableRetries": True,
                "restartJobOnWorkerRestart": False,
                "strategy": "STANDARD",
            },
            "workerPoolSpecs": [
                {
                    "replicaCount": "1",
                    "machineSpec": {
                        "machineType": p["machine_type"],
                        "acceleratorType": p["gpu"],
                        "acceleratorCount": (
                            8 if p["machine_type"] == "a3-ultragpu-8g" else 1
                        ),
                    },
                    "diskSpec": {
                        "bootDiskType": (
                            "hyperdisk-balanced"
                            if p["machine_type"] == "a3-ultragpu-8g"
                            else "pd-ssd"
                        ),
                        "bootDiskSizeGb": p["volume_gb"],
                    },
                    "containerSpec": {
                        "imageUri": p["image_digest"],
                        "command": ["python"],
                        "args": [
                            "-m",
                            "decision_workbench.training.gcp_runner",
                            "--run-json",
                            json.dumps(
                                runtime_run, sort_keys=True, separators=(",", ":")
                            ),
                        ],
                    },
                }
            ],
        },
    }


class AuthResponse:
    def __init__(self, status, headers, data):
        self.status, self.headers, self.data = status, headers, data


class AuthRequest:
    """google-auth transport restricted to Google's fixed OAuth or compute metadata endpoint."""

    def __init__(self, client, training=False):
        self.client, self.training = client, training

    def __call__(
        self, url, method="GET", body=None, headers=None, timeout=None, **kwargs
    ):
        allowed = (
            url == "https://oauth2.googleapis.com/token"
            if not self.training
            else url.startswith("http://metadata.google.internal/computeMetadata/v1/")
        )
        if not allowed:
            raise CloudUnavailable(
                "Authentication endpoint is outside the approved transport"
            )
        deadline = time.monotonic() + 60
        with self.client.stream(method, url, content=body, headers=headers) as response:
            chunks, total = [], 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > 1024**2 or time.monotonic() > deadline:
                    raise CloudUnavailable(
                        "Authentication response exceeds its size bound"
                    )
                chunks.append(chunk)
            if response.status_code != 200:
                raise CloudUnavailable(
                    "Selected Google authentication failed; automatic token retries refused"
                )
            return AuthResponse(
                response.status_code, response.headers, b"".join(chunks)
            )


class GcpGateway:
    def __init__(self, profile, client, credentials):
        self.profile, self.client, self.credentials = profile, client, credentials
        self.training = False

    @staticmethod
    def client_new():
        return httpx.Client(
            timeout=httpx.Timeout(20, connect=5),
            follow_redirects=False,
            trust_env=False,
        )

    @classmethod
    def from_operator(cls, profile):
        from google.oauth2 import service_account

        validate_profile(profile)
        filename = os.environ.get(profile["credential_prefix"] + "_CREDENTIALS_FILE")
        if not filename:
            raise CloudUnavailable("Selected GCP credentials are unavailable")
        try:
            with Path(filename).open("rb") as stream:
                raw = stream.read(65537)
            info = json.loads(raw)
            if (
                len(raw) > 65536
                or info.get("type") != "service_account"
                or info.get("project_id") != profile["project_id"]
                or info.get("client_email") != profile["operator_service_account"]
                or info.get("token_uri") != "https://oauth2.googleapis.com/token"
                or info.get("universe_domain", "googleapis.com") != "googleapis.com"
            ):
                raise ValueError()
            credentials = service_account.Credentials.from_service_account_info(
                info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
        except (OSError, ValueError, TypeError, KeyError):
            raise CloudUnavailable(
                "Selected GCP credential identity is unavailable or invalid"
            ) from None
        return cls(profile, cls.client_new(), credentials)

    @classmethod
    def from_training(cls, profile):
        from google.auth import compute_engine

        credentials = compute_engine.Credentials(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        gateway = cls(profile, cls.client_new(), credentials)
        gateway.training = True
        return gateway

    def close(self):
        self.client.close()

    def token(self):
        if not self.credentials.valid:
            self.credentials.refresh(AuthRequest(self.client, self.training))
        expected = (
            self.profile["training_service_account"]
            if self.training
            else self.profile["operator_service_account"]
        )
        if (
            self.credentials.service_account_email != expected
            or not isinstance(self.credentials.token, str)
            or not self.credentials.token
        ):
            raise CloudUnavailable("GCP credential service-account identity differs")
        return self.credentials.token

    def headers(self):
        return {
            "Authorization": "Bearer " + self.token(),
            "Accept-Encoding": "identity",
        }

    def json(self, method, url, **kwargs):
        # URLs originate only from fixed resource methods below; no response links are followed.
        headers = self.headers()
        deadline = time.monotonic() + 60
        with self.client.stream(method, url, headers=headers, **kwargs) as response:
            if response.status_code not in (200, 201):
                raise CloudUnavailable(
                    "GCP request failed (HTTP "
                    + str(response.status_code)
                    + "); inspect operator logs without replaying mutations"
                )
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > 1024**2 or time.monotonic() > deadline:
                    raise CloudUnavailable(
                        "GCP control response exceeds its size bound"
                    )
            try:
                value = json.loads(raw)
            except (ValueError, RecursionError):
                raise CloudUnavailable(
                    "GCP returned invalid control metadata"
                ) from None
            if not isinstance(value, dict):
                raise CloudUnavailable("GCP returned invalid control metadata")
            return value

    def identity(self):
        p = self.profile
        value = self.json(
            "GET",
            "https://cloudresourcemanager.googleapis.com/v1/projects/"
            + p["project_id"],
        )
        if (
            value.get("projectId") != p["project_id"]
            or value.get("projectNumber") != p["account_id"]
            or value.get("lifecycleState") != "ACTIVE"
        ):
            raise CloudUnavailable("GCP project identity differs or is inactive")
        bucket = self.json(
            "GET", "https://storage.googleapis.com/storage/v1/b/" + p["bucket"]
        )
        if (
            bucket.get("name") != p["bucket"]
            or bucket.get("projectNumber") != p["account_id"]
            or bucket.get("location", "").lower() != p["region"]
        ):
            raise CloudUnavailable(
                "GCP bucket must belong to the approved project and region"
            )
        return {
            "project_number": p["account_id"],
            "service_account": p["operator_service_account"],
        }

    def object_url(self, run, kind):
        return (
            "https://storage.googleapis.com/storage/v1/b/"
            + self.profile["bucket"]
            + "/o/"
            + quote(object_key(run, kind), safe="")
        )

    def upload_file(self, run, path, kind):
        path = Path(path)
        size = path.stat().st_size
        bound = 256 * 1024**2 if kind == "input" else self.profile["max_artifact_bytes"]
        if not 1 <= size <= bound:
            raise ValueError("GCP upload exceeds its byte bound")
        sha256 = hashlib.sha256()
        md5 = hashlib.md5(usedforsecurity=False)
        with path.open("rb") as stream:
            while chunk := stream.read(1024**2):
                md5.update(chunk)
                sha256.update(chunk)
        sha = sha256.hexdigest()
        if kind == "input" and sha != run["archive_sha256"]:
            raise ValueError("GCP study archive checksum differs")
        url = (
            "https://storage.googleapis.com/upload/storage/v1/b/"
            + self.profile["bucket"]
            + "/o"
        )
        headers = {
            **self.headers(),
            "Content-Type": (
                "application/zip" if kind == "input" else "application/gzip"
            ),
            "Content-Length": str(size),
        }
        deadline = time.monotonic() + transfer_seconds(size)

        def content():
            sent_sha = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1024**2):
                    if time.monotonic() > deadline:
                        raise CloudUnavailable("GCP upload exceeded its time bound")
                    sent_sha.update(chunk)
                    yield chunk
            if sent_sha.hexdigest() != sha:
                raise CloudUnavailable("GCP upload input changed during transfer")

        with self.client.stream(
            "POST",
            url,
            headers=headers,
            params={
                "uploadType": "media",
                "name": object_key(run, kind),
                "ifGenerationMatch": "0",
            },
            content=content(),
        ) as response:
            if response.status_code not in (200, 201):
                raise CloudUnavailable(
                    "GCP upload failed; never replay an uncertain upload"
                )
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > 1024**2 or time.monotonic() > deadline:
                    raise CloudUnavailable("GCP upload response exceeds its size bound")
            try:
                metadata = json.loads(body)
            except (ValueError, RecursionError):
                raise CloudUnavailable("GCP upload metadata is invalid") from None
        if (
            not isinstance(metadata, dict)
            or metadata.get("bucket") != self.profile["bucket"]
            or metadata.get("name") != object_key(run, kind)
            or metadata.get("size") != str(size)
            or metadata.get("md5Hash") != base64.b64encode(md5.digest()).decode()
            or not re.fullmatch(
                r"[1-9][0-9]{0,24}", str(metadata.get("generation", ""))
            )
        ):
            raise CloudUnavailable("GCP uploaded object identity/checksum differs")
        return {
            "sha256": sha,
            "bytes": size,
            "generation": metadata["generation"],
            "md5_base64": metadata["md5Hash"],
        }

    def upload(self, run, archive):
        return self.upload_file(run, archive, "input")

    def base(self, run):
        return (
            "https://"
            + self.profile["region"]
            + "-aiplatform.googleapis.com/v1/"
            + parent(run)
        )

    def bind(self, run, value):
        expected = plan(run)
        if (
            not valid_job_id(run, value.get("name"))
            or value.get("displayName") != expected["displayName"]
            or value.get("labels") != expected["labels"]
        ):
            raise CloudUnavailable("GCP job identity/labels differ")
        # Reject unrequested compute, mounts, networking, resume or debug settings.
        actual = copy.deepcopy(value.get("jobSpec"))
        if not isinstance(actual, dict):
            raise CloudUnavailable("GCP returned invalid job configuration")
        for key in ("enableWebAccess", "enableDashboardAccess"):
            actual.setdefault(key, False)
        scheduling = actual.get("scheduling")
        if isinstance(scheduling, dict):
            scheduling.setdefault("restartJobOnWorkerRestart", False)
        if (
            actual != expected["jobSpec"]
            or value.get("encryptionSpec")
            or value.get("webAccessUris")
        ):
            raise CloudUnavailable(
                "GCP job configuration differs from the approved study"
            )
        return value["name"]

    def create(self, run):
        result = self.json("POST", self.base(run) + "/customJobs", json=plan(run))
        return {"provider_job_id": self.bind(run, result)}

    def describe(self, run):
        result = self.json(
            "GET",
            self.base(run) + "/customJobs",
            params={
                "filter": 'labels.dmt_run="'
                + run["id"]
                + '" AND display_name="dmt-'
                + run["id"]
                + '"',
                "pageSize": 2,
            },
        )
        values = result.get("customJobs", [])
        if (
            not isinstance(values, list)
            or len(values) != 1
            or result.get("nextPageToken")
        ):
            raise CloudUnavailable(
                "GCP job lookup is absent or ambiguous; keep capacity reserved"
            )
        identifier = self.bind(run, values[0])
        observed = self.json(
            "GET",
            "https://"
            + self.profile["region"]
            + "-aiplatform.googleapis.com/v1/"
            + identifier,
        )
        if (
            self.bind(run, observed) != identifier
            or observed.get("state") not in STATUS
        ):
            raise CloudUnavailable("GCP observation identity/state is unsupported")
        status = STATUS[observed["state"]]
        return {
            "provider_job_id": identifier,
            "status": status,
            "provider_status": observed["state"],
            "artifact_uri": (
                "gs://" + self.profile["bucket"] + "/" + object_key(run, "output")
                if status == "Completed"
                else None
            ),
            "verification": "Google REST control-plane observation; not supply-chain or domain acceptance",
        }

    def stop(self, run):
        # Controller already binds the job immediately before this call; bind again so
        # a lost creation response never causes cancellation of a guessed numeric ID.
        result = self.describe(run)
        if (
            run.get("provider_job_id") is None
            or result["provider_job_id"] != run["provider_job_id"]
        ):
            raise CloudUnavailable(
                "GCP cancellation job differs from the recorded observation"
            )
        if result["status"] in ("Completed", "Failed", "Stopped"):
            return {"stop_requested": False, "terminal": True}
        self.json(
            "POST",
            "https://"
            + self.profile["region"]
            + "-aiplatform.googleapis.com/v1/"
            + result["provider_job_id"]
            + ":cancel",
            json={},
        )
        return {"stop_requested": True, "terminal": False}

    def download_object(self, run, kind, destination, generation=None):
        params = {"generation": str(generation)} if generation is not None else {}
        metadata = self.json("GET", self.object_url(run, kind), params=params)
        bound = 256 * 1024**2 if kind == "input" else self.profile["max_artifact_bytes"]
        gen, size = metadata.get("generation"), metadata.get("size")
        checksum = metadata.get("md5Hash")
        if (
            metadata.get("bucket") != self.profile["bucket"]
            or metadata.get("name") != object_key(run, kind)
            or not isinstance(gen, str)
            or not re.fullmatch(r"[1-9][0-9]{0,24}", gen)
            or generation is not None
            and gen != str(generation)
            or not isinstance(size, str)
            or not size.isdecimal()
            or not 1 <= int(size) <= bound
            or not isinstance(checksum, str)
        ):
            raise CloudUnavailable("GCP object metadata is outside the approved bounds")
        md5, actual = hashlib.md5(usedforsecurity=False), 0
        deadline = time.monotonic() + transfer_seconds(int(size))
        with self.client.stream(
            "GET",
            self.object_url(run, kind),
            headers=self.headers(),
            params={"alt": "media", "generation": gen},
        ) as response:
            if (
                response.status_code != 200
                or response.headers.get("content-encoding", "identity") != "identity"
            ):
                raise CloudUnavailable("GCP object download failed or changed encoding")
            with Path(destination).open("xb") as stream:
                for chunk in response.iter_raw(1024**2):
                    actual += len(chunk)
                    if actual > int(size) or time.monotonic() > deadline:
                        raise CloudUnavailable("GCP object download exceeded its bound")
                    md5.update(chunk)
                    stream.write(chunk)
        if actual != int(size) or base64.b64encode(md5.digest()).decode() != checksum:
            raise CloudUnavailable("GCP downloaded object size/checksum differs")
        sha = file_sha(destination)
        if kind == "input" and sha != run["archive_sha256"]:
            raise CloudUnavailable("GCP downloaded study checksum differs")
        return {
            "bytes": actual,
            "sha256": sha,
            "generation": gen,
            "md5_base64": checksum,
        }

    def download(self, run, uri, destination):
        artifact_location(run, uri)
        return self.download_object(run, "output", destination)
