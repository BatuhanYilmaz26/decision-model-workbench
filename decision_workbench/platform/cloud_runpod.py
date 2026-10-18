"""Selected RunPod v2 Pods and signed network-volume transport; no ambient keys."""

from decision_workbench.training.transfer_limits import (
    MAX_ARTIFACT_BYTES,
    transfer_seconds,
)

import hashlib
import json
import math
import os
import re
import time
from urllib.parse import quote

import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from .cloud_aws import CloudUnavailable
from decision_workbench.training.artifacts import file_sha

BASE = "https://api.runpod.io/v2"
REGIONS = frozenset(
    (
        "EU-CZ-1",
        "EU-RO-1",
        "EUR-IS-1",
        "EUR-NO-1",
        "US-CA-2",
        "US-GA-2",
        "US-IL-1",
        "US-KS-2",
        "US-MD-1",
        "US-MO-1",
        "US-MO-2",
        "US-NC-1",
        "US-NC-2",
        "US-NE-1",
        "US-WA-1",
    )
)
ACTIVE = frozenset(("PROVISIONING", "STARTING", "RUNNING", "EXITED", "ERROR"))


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
        "volume_id",
        "volume_gb",
        "prefix",
        "registry_id",
        "image_digest",
        "gpu",
        "cuda_version",
        "disk_gb",
        "max_runtime_seconds",
        "max_runs_day",
        "max_inflight",
        "max_artifact_bytes",
        "source_sha256",
    }
    if (
        not isinstance(p, dict)
        or set(p) - {"min_memory_gb"} != fields
        or p["provider"] != "runpod"
    ):
        raise ValueError("Invalid RunPod profile fields")
    if "min_memory_gb" in p and (
        type(p["min_memory_gb"]) is not int or not 1 <= p["min_memory_gb"] <= 4096
    ):
        raise ValueError("RunPod min_memory_gb must be an integer from 1 to 4096")
    patterns = {
        "id": r"[A-Za-z0-9_-]{1,64}",
        "credential_prefix": r"DMT_CLOUD_[A-Z0-9_]{1,64}",
        "account_id": r"user_[A-Za-z0-9]{1,100}",
        "volume_id": r"[A-Za-z0-9_-]{1,64}",
        "prefix": r"[A-Za-z0-9_-]{1,80}",
        "source_sha256": r"[a-f0-9]{64}",
        "cuda_version": r"[1-9][0-9]?\.[0-9]{1,2}",
        "gpu": r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,100}",
    }
    for k, pattern in patterns.items():
        if not isinstance(p[k], str) or not re.fullmatch(pattern, p[k]):
            raise ValueError("Invalid RunPod resource identity: " + k)
    if p["region"] not in REGIONS:
        raise ValueError("Select a reviewed S3-compatible RunPod data center")
    if p["registry_id"] is not None and (
        not isinstance(p["registry_id"], str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", p["registry_id"])
    ):
        raise ValueError("Invalid RunPod registry identity")
    if not isinstance(p["image_digest"], str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9./_-]+@sha256:[a-f0-9]{64}", p["image_digest"]
    ):
        raise ValueError("Pin a RunPod training image digest")
    if (
        not isinstance(p["tenants"], list)
        or not 1 <= len(p["tenants"]) <= 100
        or any(not isinstance(t, str) or not 1 <= len(t) <= 120 for t in p["tenants"])
        or len(set(p["tenants"])) != len(p["tenants"])
    ):
        raise ValueError("RunPod profile must name approved tenants")
    for k, low, high in (
        ("volume_gb", 10, 4096),
        ("disk_gb", 20, 1024),
        ("max_runtime_seconds", 60, 14400),
        ("max_runs_day", 1, 100),
        ("max_inflight", 1, 4),
        ("max_artifact_bytes", 1024, MAX_ARTIFACT_BYTES),
    ):
        if type(p[k]) is not int or not low <= p[k] <= high:
            raise ValueError("Invalid RunPod limit: " + k)


def pod_name(run):
    if not re.fullmatch(r"[a-f0-9]{32}", run["id"]):
        raise ValueError("Invalid RunPod run identity")
    return "dmt-" + run["id"]


def valid_job_id(run, identifier):
    pod_name(run)
    return isinstance(identifier, str) and bool(
        re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identifier)
    )


def object_key(run, kind):
    suffix = {
        "input": "inputs/study.zip",
        "output": "outputs/model.tar.gz",
        "result": "outputs/result.json",
    }
    pod_name(run)
    if kind not in suffix:
        raise ValueError("Invalid RunPod object kind")
    return run["profile"]["prefix"] + "/" + run["id"] + "/" + suffix[kind]


def artifact_location(run, uri):
    key = object_key(run, "output")
    if uri != "s3://" + run["profile"]["volume_id"] + "/" + key:
        raise ValueError("RunPod artifact differs from approved network-volume path")
    return key


def plan(run):
    p = run["profile"]
    for k in ("archive_sha256", "request_sha256"):
        if not isinstance(run.get(k), str) or not re.fullmatch(r"[a-f0-9]{64}", run[k]):
            raise ValueError("Invalid frozen RunPod input hash")
    return {
        "name": pod_name(run),
        "cloud": "SECURE",
        "gpu": {
            "id": p["gpu"],
            "count": 1,
            "allowedCudaVersions": [p["cuda_version"]],
            **({"minRamPerGpu": p["min_memory_gb"]} if "min_memory_gb" in p else {}),
        },
        "dataCenterIds": [p["region"]],
        "image": p["image_digest"],
        "entrypoint": ["python", "-m", "decision_workbench.training.runpod_runner"],
        "cmd": [
            "--archive",
            "/workspace/" + object_key(run, "input"),
            "--archive-sha256",
            run["archive_sha256"],
            "--request-sha256",
            run["request_sha256"],
            "--run-id",
            run["id"],
            "--source-sha256",
            p["source_sha256"],
            "--output",
            "/workspace/" + p["prefix"] + "/" + run["id"] + "/outputs",
            "--max-artifact-bytes",
            str(p["max_artifact_bytes"]),
            "--max-runtime-seconds",
            str(p["max_runtime_seconds"]),
        ],
        "disk": p["disk_gb"],
        "mounts": {"network": [{"volumeId": p["volume_id"], "path": "/workspace"}]},
        "registry": p["registry_id"],
        "env": {},
        "ports": [],
        "globalNetworking": False,
        "startSsh": False,
        "startJupyter": False,
    }


def validate_result(run, value):
    from .cloud_volume import validate_result as validate

    return validate(run, value, "domain-runpod-result-v1")


class RunPodGateway:
    def __init__(self, profile, client, api_key, access_key, secret_key):
        self.profile, self.client = profile, client
        self.api_key, self.credentials = api_key, Credentials(access_key, secret_key)
        self.observed_volume = None

    @classmethod
    def from_operator(cls, profile):
        validate_profile(profile)
        prefix = profile["credential_prefix"]
        keys = [
            os.environ.get(prefix + suffix)
            for suffix in ("_API_KEY", "_S3_ACCESS_KEY_ID", "_S3_SECRET_ACCESS_KEY")
        ]
        if (
            any(
                not isinstance(k, str)
                or not 1 <= len(k) <= 16384
                or "\n" in k
                or "\r" in k
                for k in keys
            )
            or keys[1] != profile["account_id"]
        ):
            raise CloudUnavailable(
                "Selected RunPod REST/storage credentials are unavailable or differ"
            )
        return cls(
            profile,
            httpx.Client(
                timeout=httpx.Timeout(20, connect=5),
                trust_env=False,
                follow_redirects=False,
            ),
            *keys,
        )

    def close(self):
        self.client.close()

    @staticmethod
    def body(response, bound=1024**2):
        data = bytearray()
        start = time.monotonic()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > bound or time.monotonic() - start > 60:
                raise CloudUnavailable("RunPod response exceeds its bounds")
        try:
            return json.loads(data)
        except (ValueError, RecursionError):
            raise CloudUnavailable("RunPod response is malformed") from None

    def rest(self, method, path, payload=None, params=None, accepted=(200,)):
        try:
            with self.client.stream(
                method,
                BASE + path,
                headers={"Authorization": "Bearer " + self.api_key},
                json=payload,
                params=params,
            ) as response:
                if response.status_code not in accepted:
                    raise CloudUnavailable(
                        "RunPod control request failed or is uncertain"
                    )
                if response.status_code in (204, 404):
                    return None
                return self.body(response)
        except httpx.HTTPError:
            raise CloudUnavailable(
                "RunPod control transport failed or is uncertain"
            ) from None

    def storage_url(self, run, kind):
        return (
            "https://s3api-"
            + self.profile["region"].lower()
            + ".runpod.io/"
            + quote(self.profile["volume_id"], safe="")
            + "/"
            + object_key(run, kind)
        )

    def signed(self, method, url, checksum=None, headers=None):
        h = {
            "x-amz-content-sha256": checksum or hashlib.sha256(b"").hexdigest(),
            **(headers or {}),
        }
        request = AWSRequest(method=method, url=url, headers=h)
        SigV4Auth(self.credentials, "s3", self.profile["region"]).add_auth(request)
        return dict(request.headers)

    def identity(self):
        p = self.profile
        if self.credentials.access_key != p["account_id"]:
            raise CloudUnavailable("Selected RunPod storage account differs")
        volume = self.rest("GET", "/network-volumes/" + p["volume_id"])
        if (
            not isinstance(volume, dict)
            or volume.get("id") != p["volume_id"]
            or volume.get("dataCenter") != p["region"]
            or type(volume.get("size")) is not int
            or volume["size"] != p["volume_gb"]
            or volume.get("type") != "STANDARD"
        ):
            raise CloudUnavailable(
                "RunPod network volume identity/configuration differs"
            )
        if p["registry_id"] is not None:
            registry = self.rest("GET", "/registries/" + p["registry_id"])
            if not isinstance(registry, dict) or registry.get("id") != p["registry_id"]:
                raise CloudUnavailable("RunPod registry credential reference differs")
        url = "https://s3api-" + p["region"].lower() + ".runpod.io/" + p["volume_id"]
        try:
            with self.client.stream("HEAD", url, headers=self.signed("HEAD", url)) as r:
                if r.status_code != 200:
                    raise CloudUnavailable(
                        "Selected RunPod storage credential cannot access approved volume"
                    )
        except httpx.HTTPError:
            raise CloudUnavailable("RunPod storage identity request failed") from None
        self.observed_volume = volume
        # v2 has no user-whoami field. Both selected keys must access this unique volume.
        return {
            "volume_id": p["volume_id"],
            "account_id": p["account_id"],
            "account_basis": "selected S3 access key plus approved volume accessibility; not REST whoami",
        }

    def head(self, run, kind):
        url = self.storage_url(run, kind)
        try:
            with self.client.stream(
                "HEAD", url, headers=self.signed("HEAD", url)
            ) as response:
                if response.status_code == 404:
                    return None
                if response.status_code != 200:
                    raise CloudUnavailable("RunPod object metadata failed")
                try:
                    size = int(response.headers["Content-Length"])
                    etag = response.headers["ETag"]
                except (KeyError, ValueError):
                    raise CloudUnavailable(
                        "RunPod object metadata is invalid"
                    ) from None
                if size < 0 or not 1 <= len(etag) <= 256:
                    raise CloudUnavailable("RunPod object metadata exceeds bounds")
                return {"bytes": size, "etag": etag}
        except httpx.HTTPError:
            raise CloudUnavailable("RunPod object metadata transport failed") from None

    def upload(self, run, archive):
        if not 1 <= archive.stat().st_size <= 256 * 1024**2:
            raise ValueError("RunPod input archive exceeds its bounds")
        checksum = file_sha(archive)
        if checksum != run["archive_sha256"]:
            raise ValueError("RunPod frozen input bytes differ")
        for kind in ("input", "output", "result"):
            if self.head(run, kind) is not None:
                raise CloudUnavailable(
                    "RunPod run prefix already contains an input/output"
                )
        url = self.storage_url(run, "input")
        actual = hashlib.sha256()
        size = archive.stat().st_size
        start = time.monotonic()

        def chunks():
            with archive.open("rb") as stream:
                while chunk := stream.read(1024**2):
                    if time.monotonic() - start > 600:
                        raise CloudUnavailable(
                            "RunPod upload exceeds its checked deadline"
                        )
                    actual.update(chunk)
                    yield chunk

        try:
            with self.client.stream(
                "PUT",
                url,
                headers=self.signed(
                    "PUT",
                    url,
                    checksum,
                    {"Content-Length": str(size), "Content-Type": "application/zip"},
                ),
                content=chunks(),
            ) as response:
                if response.status_code != 200 or actual.hexdigest() != checksum:
                    raise CloudUnavailable("RunPod input upload failed or differs")
                etag = response.headers.get("ETag")
                if not isinstance(etag, str) or not 1 <= len(etag) <= 256:
                    raise CloudUnavailable("RunPod input upload has no bounded ETag")
        except httpx.HTTPError:
            raise CloudUnavailable("RunPod input upload is uncertain") from None
        observed = self.head(run, "input")
        if observed != {"bytes": size, "etag": etag}:
            raise CloudUnavailable("RunPod uploaded input metadata differs")
        return {
            "sha256": checksum,
            "bytes": size,
            "etag": etag,
            "conditional_create": False,
            "versioned": False,
        }

    def lookup(self, run):
        # No name filter or caller-selected id in v2. Fail closed on a second page.
        result = self.rest(
            "GET", "/pods", params={"limit": 1000, "includeClusterPods": "true"}
        )
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("pods"), list)
            or len(result["pods"]) > 1000
            or result.get("pagination") != {"nextCursor": None, "hasNextPage": False}
        ):
            raise CloudUnavailable("RunPod lookup is incomplete or malformed")
        matches = [
            v
            for v in result["pods"]
            if isinstance(v, dict) and v.get("name") == pod_name(run)
        ]
        if len(matches) > 1:
            raise CloudUnavailable(
                "RunPod run name is ambiguous; never replay creation"
            )
        return matches[0] if matches else None

    def bind(self, run, value):
        expected = plan(run)
        if not isinstance(value, dict) or not valid_job_id(run, value.get("id")):
            raise CloudUnavailable("RunPod Pod identity is invalid")
        for key in (
            "name",
            "image",
            "entrypoint",
            "cmd",
            "disk",
            "mounts",
            "registry",
            "env",
            "ports",
            "cloud",
        ):
            if value.get(key) != expected[key]:
                raise CloudUnavailable("RunPod frozen configuration differs: " + key)
        if (
            value.get("dataCenterId") != run["profile"]["region"]
            or value.get("cudaVersion") != run["profile"]["cuda_version"]
            or value.get("template") is not None
            or value.get("cluster")
            or value.get("cpu")
            or value.get("locked") is not False
            or value.get("globalNetworking") != {"enabled": False}
        ):
            raise CloudUnavailable("RunPod placement/services configuration differs")
        gpu = value.get("gpu")
        if (
            not isinstance(gpu, dict)
            or gpu.get("id") != run["profile"]["gpu"]
            or type(gpu.get("count")) is not int
            or gpu["count"] != 1
        ):
            raise CloudUnavailable("RunPod GPU configuration differs")
        if "min_memory_gb" in run["profile"] and (
            type(gpu.get("memory")) not in (int, float)
            or not math.isfinite(gpu["memory"])
            or gpu["memory"] < run["profile"]["min_memory_gb"]
        ):
            raise CloudUnavailable(
                "RunPod allocated host RAM is below the approved placement minimum"
            )
        # Readback args encodes the same explicit exec-form command, with whitespace varying.
        try:
            encoded = json.loads(value["args"])
        except (KeyError, ValueError, TypeError, RecursionError):
            raise CloudUnavailable("RunPod command encoding differs") from None
        if encoded != {"entrypoint": expected["entrypoint"], "cmd": expected["cmd"]}:
            raise CloudUnavailable("RunPod command encoding differs")
        cost = value.get("cost")
        if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
            raise CloudUnavailable("RunPod cost observation is invalid")
        if value.get("status") not in ACTIVE | {"TERMINATED"}:
            raise CloudUnavailable("RunPod lifecycle state is unsupported")
        return value

    def create(self, run):
        if self.lookup(run) is not None:
            raise CloudUnavailable(
                "RunPod run name already exists; refuse fresh submission"
            )
        value = self.rest("POST", "/pods", plan(run), accepted=(201,))
        value = self.bind(run, value)
        return {
            "provider_job_id": value["id"],
            "provider_status": value["status"],
            "observed_hourly_cost_usd": value["cost"],
            "verification": "RunPod v2 configuration observation; not image-origin, billing or model acceptance",
        }

    def marker(self, run):
        meta = self.head(run, "result")
        if meta is None:
            return None
        if not 1 <= meta["bytes"] <= 16384:
            raise CloudUnavailable("RunPod result marker exceeds its byte bound")
        url = self.storage_url(run, "result")
        try:
            with self.client.stream(
                "GET", url, headers=self.signed("GET", url)
            ) as response:
                if (
                    response.status_code != 200
                    or response.headers.get("ETag") != meta["etag"]
                ):
                    raise CloudUnavailable("RunPod result marker metadata changed")
                value = self.body(response, 16384)
        except httpx.HTTPError:
            raise CloudUnavailable("RunPod result transport failed") from None
        return validate_result(run, value)

    def describe(self, run):
        identifier = run.get("provider_job_id")
        if identifier is None:
            value = self.lookup(run)
            if value is None:
                raise CloudUnavailable(
                    "Unknown RunPod creation is absent; capacity remains unresolved"
                )
        else:
            if not valid_job_id(run, identifier):
                raise CloudUnavailable("RunPod Pod identity differs")
            value = self.rest("GET", "/pods/" + identifier, accepted=(200, 404))
            if value is None and not run.get("termination_acknowledged"):
                raise CloudUnavailable(
                    "RunPod disappearance lacks a recorded termination receipt"
                )
        if value is not None:
            value = self.bind(run, value)
            identifier = value["id"]
            if run.get("provider_job_id") not in (None, identifier):
                raise CloudUnavailable("RunPod Pod identity changed")
        marker = self.marker(run)
        if run.get("training_result") is not None and marker != run["training_result"]:
            raise CloudUnavailable(
                "RunPod recorded training result changed or disappeared"
            )
        terminated = value is None or value["status"] == "TERMINATED"
        status = (
            (
                "Completed"
                if marker and marker["status"] == "succeeded"
                else "Failed" if marker else "Stopped"
            )
            if terminated
            else "InProgress"
        )
        return {
            "provider_job_id": identifier,
            "provider_status": (
                "ABSENT_AFTER_ACKNOWLEDGED_TERMINATION"
                if value is None
                else value["status"]
            ),
            "status": status,
            "artifact_uri": (
                "s3://" + run["profile"]["volume_id"] + "/" + object_key(run, "output")
                if status == "Completed"
                else None
            ),
            "training_result": marker,
            "output_ready": bool(marker and marker["status"] == "succeeded"),
            "observed_hourly_cost_usd": value["cost"] if value is not None else None,
            "verification": "RunPod lifecycle/result observation; result is volume-writer attestation, not domain or invoice acceptance",
        }

    def stop(self, run):
        if run.get("termination_attempted"):
            raise CloudUnavailable(
                "RunPod termination already attempted; observe, do not replay"
            )
        identifier = run.get("provider_job_id")
        if not valid_job_id(run, identifier):
            raise CloudUnavailable("RunPod termination needs a bound Pod identity")
        # Controller binds configuration before committing the attempt marker.
        self.rest("DELETE", "/pods/" + identifier, accepted=(204,))
        return {"termination_acknowledged": True}

    def download(self, run, uri, target):
        artifact_location(run, uri)
        marker = self.marker(run)
        if marker is None or marker["status"] != "succeeded":
            raise CloudUnavailable("RunPod successful output marker is unavailable")
        meta = self.head(run, "output")
        if meta is None or meta["bytes"] != marker["artifact_bytes"]:
            raise CloudUnavailable("RunPod output byte count differs")
        url = self.storage_url(run, "output")
        size, h = 0, hashlib.sha256()
        start = time.monotonic()
        try:
            with self.client.stream(
                "GET", url, headers=self.signed("GET", url)
            ) as response:
                if (
                    response.status_code != 200
                    or response.headers.get("ETag") != meta["etag"]
                    or response.headers.get("Content-Encoding")
                    not in (None, "identity")
                ):
                    raise CloudUnavailable("RunPod output metadata/encoding changed")
                with target.open("xb") as stream:
                    for chunk in response.iter_raw():
                        size += len(chunk)
                        if size > marker[
                            "artifact_bytes"
                        ] or time.monotonic() - start > transfer_seconds(
                            marker["artifact_bytes"]
                        ):
                            raise CloudUnavailable(
                                "RunPod output exceeds checked size/deadline"
                            )
                        h.update(chunk)
                        stream.write(chunk)
        except httpx.HTTPError:
            raise CloudUnavailable("RunPod output transport failed") from None
        if (
            size != marker["artifact_bytes"]
            or h.hexdigest() != marker["artifact_sha256"]
            or self.head(run, "output") != meta
            or self.marker(run) != marker
        ):
            raise CloudUnavailable("RunPod output checksum or marker changed")
        return {
            "bytes": size,
            "sha256": h.hexdigest(),
            "etag": meta["etag"],
            "versioned": False,
            "producer_attestation": False,
        }
