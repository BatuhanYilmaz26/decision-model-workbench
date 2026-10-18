"""Selected Azure ML/Blob REST boundary for owner-approved frozen decision studies."""

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
import shlex
import time
from email.utils import formatdate
from pathlib import Path
from urllib.parse import quote

import httpx
from .cloud_aws import CloudUnavailable
from decision_workbench.training.artifacts import file_sha

ML_VERSION = "2026-09-01"
BLOB_VERSION = "2026-04-06"
GUID = r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"
STATUS = {
    k: "InProgress"
    for k in (
        "NotStarted",
        "Starting",
        "Provisioning",
        "Preparing",
        "Queued",
        "Running",
        "Finalizing",
    )
}
STATUS.update(
    Completed="Completed",
    Failed="Failed",
    Canceled="Stopped",
    CancelRequested="Stopping",
)


def validate_profile(p):
    from .cloud_training_profile import provider_fields

    p = provider_fields(p)
    required = {
        "id",
        "provider",
        "tenants",
        "credential_prefix",
        "account_id",
        "tenant_id",
        "operator_application_id",
        "resource_group",
        "workspace",
        "compute",
        "compute_principal_id",
        "workspace_principal_id",
        "environment",
        "environment_version",
        "registry",
        "storage_account",
        "container",
        "datastore",
        "prefix",
        "image_digest",
        "gpu",
        "region",
        "max_runtime_seconds",
        "max_runs_day",
        "max_inflight",
        "max_artifact_bytes",
        "source_sha256",
        "idle_seconds",
    }
    if not isinstance(p, dict) or set(p) != required or p["provider"] != "azure":
        raise ValueError("Invalid Azure profile fields")
    patterns = {
        "id": r"[A-Za-z0-9_-]{1,64}",
        "credential_prefix": r"DMT_CLOUD_[A-Z0-9_]{1,64}",
        "region": r"[a-z][a-z0-9]{2,29}",
        "resource_group": r"[A-Za-z0-9_-]{1,90}",
        "workspace": r"[A-Za-z0-9][A-Za-z0-9_-]{2,32}",
        "compute": r"[A-Za-z][A-Za-z0-9_-]{1,63}",
        "environment": r"[A-Za-z][A-Za-z0-9_-]{1,63}",
        "environment_version": r"[1-9][0-9]{0,8}",
        "registry": r"[a-z][a-z0-9]{4,49}",
        "storage_account": r"[a-z0-9]{3,24}",
        "container": r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]",
        "datastore": r"[a-z][a-z0-9_]{1,254}",
        "prefix": r"[A-Za-z0-9_-]{1,80}",
        "source_sha256": r"[a-f0-9]{64}",
        "gpu": r"Standard_NC(4|8|16)as_T4_v3",
    }
    patterns.update(
        {
            k: GUID
            for k in (
                "account_id",
                "tenant_id",
                "operator_application_id",
                "compute_principal_id",
                "workspace_principal_id",
            )
        }
    )
    for key, pattern in patterns.items():
        if not isinstance(p[key], str) or not re.fullmatch(pattern, p[key]):
            raise ValueError("Invalid Azure resource identity: " + key)
    if not isinstance(p["image_digest"], str) or not re.fullmatch(
        re.escape(p["registry"] + ".azurecr.io/")
        + r"[a-z0-9][a-z0-9_./-]*@sha256:[a-f0-9]{64}",
        p["image_digest"],
    ):
        raise ValueError("Pin an approved ACR image digest")
    if (
        not isinstance(p["tenants"], list)
        or not 1 <= len(p["tenants"]) <= 100
        or any(not isinstance(t, str) or not 1 <= len(t) <= 120 for t in p["tenants"])
        or len(set(p["tenants"])) != len(p["tenants"])
    ):
        raise ValueError("Azure profile must name approved tenants")
    for key, low, high in (
        ("max_runtime_seconds", 60, 14400),
        ("idle_seconds", 1, 300),
        ("max_runs_day", 1, 100),
        ("max_inflight", 1, 4),
        ("max_artifact_bytes", 1024, MAX_ARTIFACT_BYTES),
    ):
        if type(p[key]) is not int or not low <= p[key] <= high:
            raise ValueError("Invalid Azure limit: " + key)


def duration_seconds(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value)
    if not match or not any(match.groups()):
        return None
    return sum(
        int(part or 0) * factor for part, factor in zip(match.groups(), (3600, 60, 1))
    )


def workspace_id(p):
    return (
        "/subscriptions/"
        + p["account_id"]
        + "/resourceGroups/"
        + p["resource_group"]
        + "/providers/Microsoft.MachineLearningServices/workspaces/"
        + p["workspace"]
    )


def resource_id(p, provider, kind, name):
    return (
        "/subscriptions/"
        + p["account_id"]
        + "/resourceGroups/"
        + p["resource_group"]
        + "/providers/"
        + provider
        + "/"
        + kind
        + "/"
        + name
    )


def job_id(run):
    if not re.fullmatch(r"[a-f0-9]{32}", run["id"]):
        raise ValueError("Invalid cloud run identity")
    return workspace_id(run["profile"]) + "/jobs/dmt-" + run["id"]


def same_id(actual, expected):
    return isinstance(actual, str) and actual.casefold() == expected.casefold()


def valid_job_id(run, identifier):
    return same_id(identifier, job_id(run))


def blob_key(run, kind):
    job_id(run)
    if kind not in ("input", "output"):
        raise ValueError("Invalid Azure object kind")
    return (
        run["profile"]["prefix"]
        + "/"
        + run["id"]
        + ("/inputs/study.zip" if kind == "input" else "/outputs/model.tar.gz")
    )


def blob_uri(run, kind):
    p = run["profile"]
    return (
        "https://"
        + p["storage_account"]
        + ".blob.core.windows.net/"
        + p["container"]
        + "/"
        + blob_key(run, kind)
    )


def artifact_location(run, uri):
    if uri != blob_uri(run, "output"):
        raise ValueError("Azure artifact URI differs from the approved run prefix")
    return blob_key(run, "output")


def datastore_uri(run, kind):
    return (
        "azureml://datastores/"
        + run["profile"]["datastore"]
        + "/paths/"
        + blob_key(run, kind)
    )


def plan(run):
    p = run["profile"]
    job_id(run)
    args = [
        "python",
        "-m",
        "decision_workbench.training.azure_runner",
        "--archive",
        "${{inputs.study}}",
        "--archive-sha256",
        run["archive_sha256"],
        "--request-sha256",
        run["request_sha256"],
        "--artifact",
        "${{outputs.artifact}}",
        "--max-artifact-bytes",
        str(p["max_artifact_bytes"]),
    ]
    return {
        "properties": {
            "jobType": "Command",
            "displayName": "dmt-" + run["id"],
            "experimentName": "domain-decisions",
            "tags": {"dmt_run": run["id"]},
            "computeId": workspace_id(p) + "/computes/" + p["compute"],
            "environmentId": workspace_id(p)
            + "/environments/"
            + p["environment"]
            + "/versions/"
            + p["environment_version"],
            "command": shlex.join(args),
            "identity": {"identityType": "Managed"},
            "resources": {"instanceCount": 1, "shmSize": "2g"},
            "limits": {
                "jobLimitsType": "Command",
                "timeout": "PT" + str(p["max_runtime_seconds"]) + "S",
            },
            "inputs": {
                "study": {
                    "jobInputType": "uri_file",
                    "mode": "Download",
                    "uri": datastore_uri(run, "input"),
                }
            },
            "outputs": {
                "artifact": {
                    "jobOutputType": "uri_file",
                    "mode": "Upload",
                    "uri": datastore_uri(run, "output"),
                }
            },
            "isArchived": False,
        }
    }


class AzureGateway:
    def __init__(self, profile, client, client_secret):
        self.profile, self.client, self.client_secret = profile, client, client_secret
        self.tokens = {}
        self.observed_compute = None

    @classmethod
    def from_operator(cls, profile):
        validate_profile(profile)
        secret = os.environ.get(profile["credential_prefix"] + "_CLIENT_SECRET")
        if not secret:
            raise CloudUnavailable("Selected Azure client credentials are unavailable")
        return cls(
            profile,
            httpx.Client(
                timeout=httpx.Timeout(20, connect=5),
                follow_redirects=False,
                trust_env=False,
            ),
            secret,
        )

    def close(self):
        self.client.close()

    @staticmethod
    def body(response, bound=1024**2):
        value = bytearray()
        deadline = time.monotonic() + 60
        for chunk in response.iter_bytes():
            value.extend(chunk)
            if len(value) > bound or time.monotonic() > deadline:
                raise CloudUnavailable("Azure response exceeds its size/time bound")
        return bytes(value)

    def token(self, scope):
        if scope not in (
            "https://management.azure.com/.default",
            "https://storage.azure.com/.default",
        ):
            raise ValueError("Unsupported Azure token scope")
        old = self.tokens.get(scope)
        if old and old[1] > time.monotonic():
            return old[0]
        p = self.profile
        with self.client.stream(
            "POST",
            "https://login.microsoftonline.com/"
            + p["tenant_id"]
            + "/oauth2/v2.0/token",
            data={
                "client_id": p["operator_application_id"],
                "client_secret": self.client_secret,
                "scope": scope,
                "grant_type": "client_credentials",
            },
        ) as response:
            if response.status_code != 200:
                raise CloudUnavailable(
                    "Selected Azure authentication failed; token requests are not retried"
                )
            try:
                v = json.loads(self.body(response))
            except (ValueError, RecursionError):
                raise CloudUnavailable(
                    "Azure returned invalid authentication metadata"
                ) from None
        if (
            not isinstance(v, dict)
            or v.get("token_type", "").lower() != "bearer"
            or not isinstance(v.get("access_token"), str)
            or not v["access_token"]
            or len(v["access_token"]) > 16384
            or not str(v.get("expires_in", "")).isdigit()
            or not 60 <= int(v["expires_in"]) <= 86400
        ):
            raise CloudUnavailable(
                "Azure authentication metadata is outside its bounds"
            )
        self.tokens[scope] = (
            v["access_token"],
            time.monotonic() + int(v["expires_in"]) - 60,
        )
        return v["access_token"]

    def arm(
        self,
        method,
        identifier,
        version=ML_VERSION,
        allowed=(200,),
        empty=False,
        **kwargs,
    ):
        # Identifiers are constructed from strict frozen fields, never response links.
        with self.client.stream(
            method,
            "https://management.azure.com" + identifier,
            params={"api-version": version},
            headers={
                "Authorization": "Bearer "
                + self.token("https://management.azure.com/.default"),
                "Accept-Encoding": "identity",
                **kwargs.pop("headers", {}),
            },
            **kwargs,
        ) as response:
            if response.status_code not in allowed:
                raise CloudUnavailable(
                    "Azure ARM request failed (HTTP "
                    + str(response.status_code)
                    + "); do not replay uncertain mutations"
                )
            raw = self.body(response)
            if empty:
                return {}
            try:
                value = json.loads(raw)
            except (ValueError, RecursionError):
                raise CloudUnavailable(
                    "Azure returned invalid resource metadata"
                ) from None
            if not isinstance(value, dict):
                raise CloudUnavailable("Azure returned invalid resource metadata")
            return value

    def identity(self):
        p = self.profile
        wid = workspace_id(p)
        sub = self.arm("GET", "/subscriptions/" + p["account_id"], version="2022-12-01")
        if (
            sub.get("subscriptionId") != p["account_id"]
            or sub.get("tenantId") != p["tenant_id"]
            or sub.get("state") != "Enabled"
        ):
            raise CloudUnavailable("Azure subscription/tenant differs or is disabled")
        ws = self.arm("GET", wid)
        identity = ws.get("identity", {})
        prop = ws.get("properties", {})
        if (
            not same_id(ws.get("id"), wid)
            or ws.get("location") != p["region"]
            or prop.get("provisioningState") != "Succeeded"
            or identity.get("type") != "SystemAssigned"
            or identity.get("tenantId") != p["tenant_id"]
            or identity.get("principalId") != p["workspace_principal_id"]
        ):
            raise CloudUnavailable("Azure workspace/identity differs")
        compute = self.arm("GET", wid + "/computes/" + p["compute"])
        cp = compute.get("properties", {})
        vm = cp.get("properties", {})
        scale = vm.get("scaleSettings", {})
        ident = compute.get("identity", {})
        if (
            not same_id(compute.get("id"), wid + "/computes/" + p["compute"])
            or compute.get("location") != p["region"]
            or cp.get("computeType") != "AmlCompute"
            or cp.get("provisioningState") != "Succeeded"
            or vm.get("vmSize") != p["gpu"]
            or vm.get("vmPriority") != "Dedicated"
            or vm.get("osType", "Linux") != "Linux"
            or scale.get("minNodeCount") != 0
            or scale.get("maxNodeCount") != 1
            or duration_seconds(scale.get("nodeIdleTimeBeforeScaleDown"))
            != p["idle_seconds"]
            or ident.get("type") != "SystemAssigned"
            or ident.get("tenantId") != p["tenant_id"]
            or ident.get("principalId") != p["compute_principal_id"]
            or vm.get("subnet")
            or vm.get("isolatedNetwork")
            or vm.get("remoteLoginPortPublicAccess") != "Disabled"
        ):
            raise CloudUnavailable(
                "Azure GPU compute, scaling or managed identity differs"
            )
        for key in ("currentNodeCount", "targetNodeCount"):
            if vm.get(key) is not None and (
                type(vm[key]) is not int or not 0 <= vm[key] <= 1
            ):
                raise CloudUnavailable(
                    "Azure compute reports more nodes than the approved profile"
                )
        self.observed_compute = {
            k: vm.get(k)
            for k in ("currentNodeCount", "targetNodeCount", "allocationState")
        }
        eid = (
            wid
            + "/environments/"
            + p["environment"]
            + "/versions/"
            + p["environment_version"]
        )
        env = self.arm("GET", eid)
        ep = env.get("properties", {})
        if (
            not same_id(env.get("id"), eid)
            or ep.get("image") != p["image_digest"]
            or ep.get("isArchived", False)
            or ep.get("provisioningState") != "Succeeded"
            or ep.get("osType", "Linux") != "Linux"
            or ep.get("autoRebuild", "Disabled") != "Disabled"
            or ep.get("build")
            or ep.get("condaFile")
        ):
            raise CloudUnavailable(
                "Azure environment image/version differs or requires an unapproved build"
            )
        sid = resource_id(
            p, "Microsoft.Storage", "storageAccounts", p["storage_account"]
        )
        storage = self.arm("GET", sid, version="2025-06-01")
        sp = storage.get("properties", {})
        if (
            not same_id(storage.get("id"), sid)
            or storage.get("location") != p["region"]
            or sp.get("provisioningState") != "Succeeded"
            or sp.get("primaryEndpoints", {}).get("blob")
            != "https://" + p["storage_account"] + ".blob.core.windows.net/"
            or sp.get("allowBlobPublicAccess") is not False
            or sp.get("minimumTlsVersion") != "TLS1_2"
        ):
            raise CloudUnavailable(
                "Azure storage identity, endpoint or transport profile differs"
            )
        registry = resource_id(
            p, "Microsoft.ContainerRegistry", "registries", p["registry"]
        )
        if not same_id(prop.get("storageAccount"), sid) or not same_id(
            prop.get("containerRegistry"), registry
        ):
            raise CloudUnavailable("Azure workspace storage/registry differs")
        ds = self.arm("GET", wid + "/datastores/" + p["datastore"])
        dp = ds.get("properties", {})
        if (
            not same_id(ds.get("id"), wid + "/datastores/" + p["datastore"])
            or dp.get("datastoreType") != "AzureBlob"
            or dp.get("accountName") != p["storage_account"]
            or dp.get("containerName") != p["container"]
            or dp.get("endpoint") != "core.windows.net"
            or dp.get("protocol") != "https"
            or dp.get("credentials") != {"credentialsType": "None"}
            or dp.get("serviceDataAccessAuthIdentity")
            != "WorkspaceSystemAssignedIdentity"
        ):
            raise CloudUnavailable(
                "Azure datastore differs or carries unapproved credentials"
            )
        return {"subscription": p["account_id"], "compute": self.observed_compute}

    def blob_headers(self):
        return {
            "Authorization": "Bearer "
            + self.token("https://storage.azure.com/.default"),
            "x-ms-version": BLOB_VERSION,
            "x-ms-date": formatdate(usegmt=True),
            "Accept-Encoding": "identity",
        }

    def upload(self, run, archive):
        archive = Path(archive)
        size = archive.stat().st_size
        if not 1 <= size <= 256 * 1024**2:
            raise ValueError("Azure study upload exceeds its byte bound")
        sha = hashlib.sha256()
        md5 = hashlib.md5(usedforsecurity=False)
        with archive.open("rb") as stream:
            while chunk := stream.read(1024**2):
                sha.update(chunk)
                md5.update(chunk)
        if sha.hexdigest() != run["archive_sha256"]:
            raise ValueError("Azure archive checksum differs")
        headers = self.blob_headers()
        # Preexisting output must not be silently overwritten by AML collection.
        with self.client.stream(
            "HEAD", blob_uri(run, "output"), headers=headers
        ) as response:
            if response.status_code != 404:
                raise CloudUnavailable(
                    "Azure output is present or inaccessible; a fresh study is required"
                )
        deadline = time.monotonic() + 600

        def content():
            sent = hashlib.sha256()
            with archive.open("rb") as stream:
                while chunk := stream.read(1024**2):
                    if time.monotonic() > deadline:
                        raise CloudUnavailable(
                            "Azure input upload exceeded its time bound"
                        )
                    sent.update(chunk)
                    yield chunk
            if sent.hexdigest() != sha.hexdigest():
                raise CloudUnavailable("Azure input changed during transfer")

        encoded = base64.b64encode(md5.digest()).decode()
        with self.client.stream(
            "PUT",
            blob_uri(run, "input"),
            headers={
                **headers,
                "If-None-Match": "*",
                "x-ms-blob-type": "BlockBlob",
                "Content-MD5": encoded,
                "Content-Length": str(size),
                "Content-Type": "application/zip",
            },
            content=content(),
        ) as response:
            if (
                response.status_code != 201
                or response.headers.get("Content-MD5") != encoded
                or not response.headers.get("ETag")
            ):
                raise CloudUnavailable(
                    "Azure input upload is uncertain or its checksum/identity differs"
                )
            return {
                "sha256": sha.hexdigest(),
                "bytes": size,
                "md5_base64": encoded,
                "etag": response.headers["ETag"],
                "version_id": response.headers.get("x-ms-version-id"),
            }

    def create(self, run):
        identifier = job_id(run)
        self.arm("GET", identifier, allowed=(404,), empty=True)
        result = self.arm(
            "PUT",
            identifier,
            allowed=(201,),
            headers={"If-None-Match": "*"},
            json=plan(run),
        )
        self.bind(run, result)
        return {"provider_job_id": identifier}

    def bind(self, run, value):
        if (
            not valid_job_id(run, value.get("id"))
            or value.get("name") != "dmt-" + run["id"]
        ):
            raise CloudUnavailable("Azure job identity differs")
        expected = plan(run)["properties"]
        actual = value.get("properties")
        if not isinstance(actual, dict):
            raise CloudUnavailable("Azure job metadata is invalid")
        for key, wanted in expected.items():
            got = actual.get(key, False if key == "isArchived" else None)
            if key in ("computeId", "environmentId"):
                if not same_id(got, wanted):
                    raise CloudUnavailable("Azure job resource binding differs")
            elif key == "limits":
                if (
                    not isinstance(got, dict)
                    or set(got) != set(wanted)
                    or got.get("jobLimitsType") != "Command"
                    or duration_seconds(got.get("timeout"))
                    != run["profile"]["max_runtime_seconds"]
                ):
                    raise CloudUnavailable("Azure job runtime limit differs")
            elif got != wanted:
                raise CloudUnavailable("Azure job configuration differs: " + key)
        for key in (
            "codeId",
            "componentId",
            "distribution",
            "queueSettings",
            "environmentVariables",
        ):
            if actual.get(key):
                raise CloudUnavailable(
                    "Azure job has unapproved code, distribution, queue or services"
                )
        services = actual.get("services") or {}
        if not isinstance(services, dict) or len(services) > 2:
            raise CloudUnavailable("Azure job service metadata is unsupported")
        seen = set()
        for service in services.values():
            if (
                not isinstance(service, dict)
                or service.get("jobServiceType") not in ("Studio", "Tracking")
                or service.get("port")
                or service.get("nodes")
                or service["jobServiceType"] in seen
            ):
                raise CloudUnavailable(
                    "Azure job contains unapproved interactive/custom services"
                )
            if service.get("endpoint") is not None and (
                not isinstance(service["endpoint"], str)
                or len(service["endpoint"]) > 4096
            ):
                raise CloudUnavailable("Azure service endpoint metadata is invalid")
            seen.add(service["jobServiceType"])
        # Studio/Tracking endpoints are metadata only and are never followed.
        return job_id(run)

    def describe(self, run):
        result = self.arm("GET", job_id(run))
        identifier = self.bind(run, result)
        state = result["properties"].get("status")
        if state not in STATUS:
            raise CloudUnavailable(
                "Azure job status is unsupported; keep capacity reserved"
            )
        status = STATUS[state]
        return {
            "provider_job_id": identifier,
            "status": status,
            "provider_status": state,
            "artifact_uri": blob_uri(run, "output") if status == "Completed" else None,
            "compute_observation": copy.deepcopy(self.observed_compute),
            "verification": "Azure ARM control-plane observation; not supply-chain, node-shutdown or domain acceptance",
        }

    def stop(self, run):
        self.arm("POST", job_id(run) + "/cancel", allowed=(200, 202), empty=True)
        return {"stop_requested": True, "terminal": False}

    def download(self, run, uri, destination):
        artifact_location(run, uri)
        headers = self.blob_headers()
        with self.client.stream("HEAD", uri, headers=headers) as response:
            h = response.headers
            if (
                response.status_code != 200
                or not h.get("Content-Length", "").isdigit()
                or not 1
                <= int(h["Content-Length"])
                <= self.profile["max_artifact_bytes"]
                or not h.get("ETag")
                or h.get("x-ms-blob-type") != "BlockBlob"
            ):
                raise CloudUnavailable(
                    "Azure output metadata is outside the approved bounds"
                )
            size, etag, version, md5 = (
                int(h["Content-Length"]),
                h["ETag"],
                h.get("x-ms-version-id"),
                h.get("Content-MD5"),
            )
        params = {"versionid": version} if version else {}
        deadline = time.monotonic() + transfer_seconds(size)
        total = 0
        actual_md5 = hashlib.md5(usedforsecurity=False)
        with self.client.stream(
            "GET", uri, params=params, headers={**headers, "If-Match": etag}
        ) as response:
            if (
                response.status_code != 200
                or response.headers.get("ETag") != etag
                or response.headers.get("content-encoding", "identity") != "identity"
            ):
                raise CloudUnavailable(
                    "Azure output download changed identity/encoding"
                )
            with Path(destination).open("xb") as stream:
                for chunk in response.iter_raw(1024**2):
                    total += len(chunk)
                    if total > size or time.monotonic() > deadline:
                        raise CloudUnavailable(
                            "Azure output download exceeded its bounds"
                        )
                    actual_md5.update(chunk)
                    stream.write(chunk)
        if (
            total != size
            or md5 is not None
            and base64.b64encode(actual_md5.digest()).decode() != md5
        ):
            raise CloudUnavailable("Azure output size/checksum differs")
        return {
            "bytes": total,
            "sha256": file_sha(destination),
            "etag": etag,
            "version_id": version,
            "provider_md5_verified": md5 is not None,
        }
