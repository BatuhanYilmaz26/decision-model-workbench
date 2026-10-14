"""Azure REST serialization and failure contracts; fictional transport only."""

import base64
import copy
import hashlib
import json
import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs
import httpx
from decision_workbench.platform.cloud_azure import (
    AzureGateway,
    plan,
    job_id,
    workspace_id,
    resource_id,
    blob_uri,
    validate_profile,
    artifact_location,
    ML_VERSION,
    BLOB_VERSION,
)
from decision_workbench.platform.cloud import validate_history
from decision_workbench.platform.cloud_aws import CloudUnavailable
from decision_workbench.platform.store import Conflict
from decision_workbench.training.provenance import source_fingerprint
from decision_workbench.training.cloud_packaging import pack_export
import test_remote_admission as fixtures

OWNER = fixtures.OWNER


def profile():
    return {
        "id": "azure-fixture",
        "provider": "azure",
        "tenants": ["A"],
        "credential_prefix": "DMT_CLOUD_AZURE",
        "account_id": "00000000-1111-2222-3333-444444444444",
        "tenant_id": "11111111-2222-3333-4444-555555555555",
        "operator_application_id": "22222222-3333-4444-5555-666666666666",
        "compute_principal_id": "33333333-4444-5555-6666-777777777777",
        "workspace_principal_id": "44444444-5555-6666-7777-888888888888",
        "resource_group": "dmt-fixture",
        "workspace": "dmt-workspace",
        "compute": "decision-gpu",
        "environment": "decision-trainer",
        "environment_version": "1",
        "registry": "dmtregistry",
        "storage_account": "dmtstorage",
        "container": "decision-runs",
        "datastore": "decision_data",
        "prefix": "studies",
        "image_digest": "dmtregistry.azurecr.io/trainer@sha256:" + "a" * 64,
        "gpu": "Standard_NC4as_T4_v3",
        "region": "westeurope",
        "max_runtime_seconds": 7200,
        "idle_seconds": 120,
        "max_runs_day": 2,
        "max_inflight": 1,
        "max_artifact_bytes": 1024**2,
        "source_sha256": source_fingerprint(),
    }


class AzureCloudTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.RemoteAdmissionTests("runTest")
        self.f.setUp()
        self.p = profile()
        self.file = self.f.root / "profiles.json"
        self.write_profiles()
        self.env = patch.dict(os.environ, {"DMT_CLOUD_PROFILES_FILE": str(self.file)})
        self.env.start()
        self.f.config.update(
            {k: self.p[k] for k in ("provider", "region", "gpu", "image_digest")}
        )
        self.study = self.f.study()
        self.cloud = self.f.service.cloud
        self.calls = []
        self.observed = None
        self.fail_create = False
        self.output = None
        self.changed = None
        self.truncated = False
        self.corrupt = False
        self.create_status = 201
        self.output_present = False
        self.f.worker.service.cloud.gateway_factory = self.gateway

    def tearDown(self):
        self.env.stop()
        self.f.tearDown()

    def write_profiles(self):
        self.file.write_text(
            json.dumps({"format": "domain-cloud-profiles-v1", "profiles": [self.p]})
        )

    def gateway(self, p):
        return AzureGateway(
            p,
            httpx.Client(
                transport=httpx.MockTransport(self.transport), trust_env=False
            ),
            "fixture-secret",
        )

    def run_value(self):
        e = self.cloud.get(OWNER, self.f.project, self.run["id"])
        return {**e["payload"], "input_receipt": e["state"].get("input_receipt")}

    def identity_resources(self):
        p = self.p
        wid = workspace_id(p)
        sid = resource_id(
            p, "Microsoft.Storage", "storageAccounts", p["storage_account"]
        )
        rid = resource_id(p, "Microsoft.ContainerRegistry", "registries", p["registry"])
        return {
            "/subscriptions/"
            + p["account_id"]: {
                "subscriptionId": p["account_id"],
                "tenantId": p["tenant_id"],
                "state": "Enabled",
            },
            wid: {
                "id": wid,
                "location": p["region"],
                "identity": {
                    "type": "SystemAssigned",
                    "tenantId": p["tenant_id"],
                    "principalId": p["workspace_principal_id"],
                },
                "properties": {
                    "provisioningState": "Succeeded",
                    "storageAccount": sid,
                    "containerRegistry": rid,
                },
            },
            wid
            + "/computes/"
            + p["compute"]: {
                "id": wid + "/computes/" + p["compute"],
                "location": p["region"],
                "identity": {
                    "type": "SystemAssigned",
                    "tenantId": p["tenant_id"],
                    "principalId": p["compute_principal_id"],
                },
                "properties": {
                    "computeType": "AmlCompute",
                    "provisioningState": "Succeeded",
                    "properties": {
                        "vmSize": p["gpu"],
                        "vmPriority": "Dedicated",
                        "osType": "Linux",
                        "scaleSettings": {
                            "minNodeCount": 0,
                            "maxNodeCount": 1,
                            "nodeIdleTimeBeforeScaleDown": "PT120S",
                        },
                        "remoteLoginPortPublicAccess": "Disabled",
                        "currentNodeCount": 1,
                        "targetNodeCount": 1,
                        "allocationState": "Steady",
                    },
                },
            },
            wid
            + "/environments/"
            + p["environment"]
            + "/versions/"
            + p["environment_version"]: {
                "id": wid
                + "/environments/"
                + p["environment"]
                + "/versions/"
                + p["environment_version"],
                "properties": {
                    "image": p["image_digest"],
                    "osType": "Linux",
                    "provisioningState": "Succeeded",
                    "autoRebuild": "Disabled",
                    "isArchived": False,
                },
            },
            sid: {
                "id": sid,
                "location": p["region"],
                "properties": {
                    "provisioningState": "Succeeded",
                    "primaryEndpoints": {
                        "blob": "https://"
                        + p["storage_account"]
                        + ".blob.core.windows.net/"
                    },
                    "allowBlobPublicAccess": False,
                    "minimumTlsVersion": "TLS1_2",
                },
            },
            wid
            + "/datastores/"
            + p["datastore"]: {
                "id": wid + "/datastores/" + p["datastore"],
                "properties": {
                    "datastoreType": "AzureBlob",
                    "accountName": p["storage_account"],
                    "containerName": p["container"],
                    "endpoint": "core.windows.net",
                    "protocol": "https",
                    "credentials": {"credentialsType": "None"},
                    "serviceDataAccessAuthIdentity": "WorkspaceSystemAssignedIdentity",
                },
            },
        }

    def transport(self, request):
        url = request.url
        self.calls.append((request.method, str(url)))
        if url.host == "login.microsoftonline.com":
            self.assertEqual(url.path, "/" + self.p["tenant_id"] + "/oauth2/v2.0/token")
            fields = parse_qs(request.read().decode())
            self.assertEqual(fields["client_id"], [self.p["operator_application_id"]])
            self.assertEqual(fields["client_secret"], ["fixture-secret"])
            self.assertEqual(fields["grant_type"], ["client_credentials"])
            self.assertIn(
                fields["scope"][0],
                [
                    "https://management.azure.com/.default",
                    "https://storage.azure.com/.default",
                ],
            )
            return httpx.Response(
                200,
                json={
                    "access_token": "fixture-token",
                    "token_type": "Bearer",
                    "expires_in": 3599,
                },
            )
        self.assertEqual(request.headers["Authorization"], "Bearer fixture-token")
        if url.host == "management.azure.com":
            if "/jobs/" not in url.path:
                value = copy.deepcopy(self.identity_resources()[url.path])
                if self.changed:
                    self.changed(url.path, value)
                return httpx.Response(200, json=value)
            self.assertEqual(url.params["api-version"], ML_VERSION)
            if request.method == "PUT":
                self.assertEqual(request.headers["If-None-Match"], "*")
                self.assertEqual(json.loads(request.read()), plan(self.run_value()))
                self.observed = {
                    **plan(self.run_value()),
                    "id": job_id(self.run_value()),
                    "name": "dmt-" + self.run["id"],
                }
                self.observed["properties"]["status"] = "Running"
                return (
                    httpx.Response(503, json={"error": "fixture lost create response"})
                    if self.fail_create
                    else httpx.Response(self.create_status, json=self.observed)
                )
            if url.path.endswith("/cancel"):
                # A malicious Location must not be followed by the adapter.
                return httpx.Response(
                    202,
                    headers={
                        "Location": "https://evil.example/cancel",
                        "Retry-After": "999",
                    },
                )
            return (
                httpx.Response(404, json={"error": "not found"})
                if self.observed is None
                else httpx.Response(200, json=self.observed)
            )
        self.assertEqual(url.host, self.p["storage_account"] + ".blob.core.windows.net")
        self.assertEqual(request.headers["x-ms-version"], BLOB_VERSION)
        if request.method == "PUT":
            self.assertEqual(str(url), blob_uri(self.run_value(), "input"))
            body = request.read()
            self.assertEqual(
                hashlib.sha256(body).hexdigest(),
                self.study["payload"]["archive_sha256"],
            )
            self.assertEqual(request.headers["If-None-Match"], "*")
            self.assertEqual(request.headers["x-ms-blob-type"], "BlockBlob")
            return httpx.Response(
                201,
                headers={
                    "Content-MD5": request.headers["Content-MD5"],
                    "ETag": '"input-etag"',
                    "x-ms-version-id": "fixture-input-version",
                },
            )
        if request.method == "HEAD":
            if self.output is None:
                return httpx.Response(200 if self.output_present else 404)
            checksum = base64.b64encode(
                hashlib.md5(
                    self.output + (b"corrupt" if self.corrupt else b""),
                    usedforsecurity=False,
                ).digest()
            ).decode()
            return httpx.Response(
                200,
                headers={
                    "Content-Length": str(len(self.output)),
                    "ETag": '"output-etag"',
                    "x-ms-blob-type": "BlockBlob",
                    "x-ms-version-id": "fixture-output-version",
                    "Content-MD5": checksum,
                },
            )
        self.assertEqual(url.params["versionid"], "fixture-output-version")
        self.assertEqual(request.headers["If-Match"], '"output-etag"')
        return httpx.Response(
            200,
            headers={"ETag": '"output-etag"'},
            stream=httpx.ByteStream(
                self.output[:-1] if self.truncated else self.output
            ),
        )

    def create(self):
        r = self.f.client.post(
            f"/v1/projects/{self.f.project}/cloud-runs",
            json={
                "study": self.study["id"],
                "profile_id": self.p["id"],
                "data_approved": True,
                "compute_approved": True,
                "request_key": "approve-azure-once",
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.run = r.json()
        return self.run

    def action(self, name):
        state = self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]
        r = self.f.client.post(
            f"/v1/projects/{self.f.project}/cloud-runs/{self.run['id']}/actions",
            json={
                "action": name,
                "revision": state["revision"],
                "request_key": "azure-action-" + uuid.uuid4().hex,
            },
        )
        self.assertEqual(r.status_code, 202, r.text)
        self.f.worker.once()
        return self.f.store.job(r.json()["id"], "A", self.f.project)

    def passed(self, j):
        self.assertEqual(j["status"], "succeeded", j["error"])

    def test_owner_api_worker_named_job_observation_and_cancel(self):
        self.create()
        self.passed(self.action("submit"))
        self.passed(self.action("observe"))
        self.passed(self.action("stop"))
        self.assertEqual(
            self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]["phase"],
            "stop_requested",
        )
        self.observed["properties"]["status"] = "Canceled"
        self.passed(self.action("observe"))
        validate_history(self.f.store)
        self.assertFalse(any("evil.example" in u for _, u in self.calls))
        self.assertEqual(
            sum(m == "PUT" and "management.azure.com" in u for m, u in self.calls), 1
        )

    def test_lost_create_response_reconciles_without_replay_or_abandonment(self):
        self.create()
        self.fail_create = True
        self.assertEqual(self.action("submit")["status"], "failed")
        self.assertEqual(
            self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]["phase"],
            "unknown",
        )
        self.assertEqual(self.action("submit")["status"], "failed")
        self.assertEqual(self.action("abandon")["status"], "failed")
        self.passed(self.action("observe"))
        validate_history(self.f.store)
        self.assertEqual(
            sum(m == "PUT" and "management.azure.com" in u for m, u in self.calls), 1
        )

    def test_existing_named_job_refused_before_put(self):
        self.create()
        self.observed = {
            **plan(self.run_value()),
            "id": job_id(self.run_value()),
            "name": "dmt-" + self.run["id"],
        }
        self.observed["properties"]["status"] = "Running"
        self.assertEqual(self.action("submit")["status"], "failed")
        self.assertFalse(
            any(m == "PUT" and "management.azure.com" in u for m, u in self.calls)
        )
        self.passed(self.action("observe"))

    def test_completed_bounded_blob_retrieval_queues_independent_admission(self):
        self.create()
        self.passed(self.action("submit"))
        self.observed["properties"]["status"] = "Completed"
        self.passed(self.action("observe"))
        folder, checksum, _ = self.f.export(self.study)
        archive = self.f.root / "model.tar.gz"
        pack_export(folder, archive, self.p["max_artifact_bytes"])
        self.output = archive.read_bytes()
        self.passed(self.action("retrieve"))
        state = self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]
        j = self.f.store.job(state["admission_job"], "A", self.f.project)
        self.assertEqual(j["kind"], "remote-import")
        self.assertEqual(j["status"], "queued")
        self.assertEqual(j["config"]["provider_receipt"]["provider"], "azure")
        self.assertEqual(j["config"]["manifest_sha256"], checksum)
        self.assertEqual(self.f.store.list("A", self.f.project, "model"), [])
        validate_history(self.f.store)
        self.f.store.cancel(j["id"], "A", self.f.project, "owner")

    def test_compute_scaling_identity_environment_and_datastore_drift_refused(self):
        self.create()
        changes = [
            lambda path, v: (
                v["properties"]["properties"]["scaleSettings"].update(maxNodeCount=2)
                if "/computes/" in path
                else None
            ),
            lambda path, v: (
                v["identity"].update(principalId="99999999-9999-9999-9999-999999999999")
                if "/computes/" in path
                else None
            ),
            lambda path, v: (
                v["properties"].update(image="malicious.azurecr.io/trainer:latest")
                if "/environments/" in path
                else None
            ),
            lambda path, v: (
                v["properties"].update(credentials={"credentialsType": "Sas"})
                if "/datastores/" in path
                else None
            ),
        ]
        for change in changes:
            self.changed = change
            gateway = self.gateway(self.p)
            try:
                with self.assertRaises(CloudUnavailable):
                    gateway.identity()
            finally:
                gateway.close()
        self.assertFalse(any(m in ("PUT",) for m, _ in self.calls))

    def test_job_command_resources_outputs_and_unsupported_states_refused(self):
        self.create()
        self.passed(self.action("submit"))
        old = copy.deepcopy(self.observed)
        for change in (
            lambda v: v["properties"]["resources"].update(instanceCount=2),
            lambda v: v["properties"].update(command="echo bypass"),
            lambda v: v["properties"]["outputs"]["artifact"].update(
                uri="https://evil.example/model"
            ),
            lambda v: v["properties"].update(status="NotResponding"),
        ):
            self.observed = copy.deepcopy(old)
            change(self.observed)
            self.assertEqual(self.action("observe")["status"], "failed")
        self.observed = old
        self.passed(self.action("observe"))

    def test_truncated_and_corrupt_version_bound_output_refused(self):
        self.create()
        self.passed(self.action("submit"))
        self.output = b"fixture-output"
        for flag in ("truncated", "corrupt"):
            setattr(self, flag, True)
            gateway = self.gateway(self.p)
            try:
                with self.assertRaises(CloudUnavailable):
                    gateway.download(
                        self.run_value(),
                        blob_uri(self.run_value(), "output"),
                        self.f.root / (flag + ".tar.gz"),
                    )
            finally:
                gateway.close()
                setattr(self, flag, False)

    def test_profiles_and_ambient_credentials_and_changed_public_profile_refused(self):
        for key, value in (
            ("endpoint", "https://evil.example"),
            ("gpu", "Standard_D4_v3"),
            ("idle_seconds", True),
            ("image_digest", "dmtregistry.azurecr.io/trainer:latest"),
        ):
            with self.assertRaises(ValueError):
                validate_profile({**self.p, key: value})
        with patch.dict(os.environ, {"DMT_CLOUD_AZURE_CLIENT_SECRET": ""}):
            with self.assertRaises(CloudUnavailable):
                AzureGateway.from_operator(self.p)
        self.create()
        self.p["environment_version"] = "2"
        self.write_profiles()
        with self.assertRaises(CloudUnavailable):
            self.cloud.queue(
                OWNER, self.f.project, self.run["id"], "submit", 1, "drift-refused"
            )
        self.assertEqual(self.calls, [])

    def test_output_collision_prevents_compute_create(self):
        self.create()
        self.output_present = True
        self.assertEqual(self.action("submit")["status"], "failed")
        state = self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]
        self.assertFalse(state["create_attempted"])
        self.assertTrue(state["upload_attempted"])
        self.passed(self.action("abandon"))
        self.assertFalse(any(m == "PUT" for m, _ in self.calls))

    def test_azure_wrapper_invokes_common_runner_and_packages_named_output(self):
        import shutil
        from decision_workbench.training.azure_runner import execute
        from decision_workbench.platform.cloud_archive import extract_model

        self.create()
        folder, checksum, _ = self.f.export(self.study)
        archive = (
            self.f.store.folder / "jobs" / self.study["id"] / "remote-study/study.zip"
        )
        out = self.f.root / "output" / "artifact.tar.gz"

        def trainer(command, check):
            self.assertTrue(check)
            self.assertIn("decision_workbench.training.cloud_runner", command)
            self.assertEqual(
                command[command.index("--archive-sha256") + 1],
                self.study["payload"]["archive_sha256"],
            )
            shutil.copytree(folder, Path(command[command.index("--out") + 1]))

        with patch(
            "decision_workbench.training.azure_runner.subprocess.run",
            side_effect=trainer,
        ):
            execute(
                archive,
                self.study["payload"]["archive_sha256"],
                self.study["payload"]["request_sha256"],
                out,
                self.p["max_artifact_bytes"],
                self.f.root / "runner",
            )
        extract_model(out, self.f.root / "extracted", self.p["max_artifact_bytes"])
        self.assertEqual(
            (self.f.root / "extracted/manifest.json").read_bytes(),
            (folder / "manifest.json").read_bytes(),
        )
        self.assertEqual(self.f.store.list("A", self.f.project, "model"), [])

    def test_cold_restore_preserves_unknown_named_run_and_input_receipt(self):
        from decision_workbench.platform.recovery import (
            backup_workspace,
            restore_workspace,
        )
        from decision_workbench.platform.store import Store
        from decision_workbench.platform.service import Service
        from decision_workbench.training.artifacts import file_sha

        self.create()
        self.fail_create = True
        self.assertEqual(self.action("submit")["status"], "failed")
        backup = self.f.root / "backup"
        dest = self.f.root / "restored"
        backup_workspace(self.f.store.folder, backup)
        restore_workspace(backup, dest, file_sha(backup / "backup-manifest.json"))
        service = Service(Store(dest))
        try:
            self.assertEqual(
                service.cloud.get(OWNER, self.f.project, self.run["id"])["state"],
                self.cloud.get(OWNER, self.f.project, self.run["id"])["state"],
            )
            validate_history(service.store)
        finally:
            service.runtime.close()

    def test_current_pinned_openapi_command_request_and_duration_units(self):
        from decision_workbench.platform.cloud_azure import duration_seconds

        defs = json.loads(
            Path(
                "tests/fixtures/provider-contracts/cloud-azure-contract-2026-10-11.json"
            ).read_bytes()
        )["definitions"]
        self.create()
        properties = plan(self.run_value())["properties"]

        def fields(name):
            value = defs[name]
            result = {}
            for parent in value.get("allOf", []):
                if parent["$ref"].startswith("#/definitions/"):
                    result.update(fields(parent["$ref"].split("/")[-1]))
            result.update(value.get("properties", {}))
            return result

        self.assertFalse(set(properties) - set(fields("CommandJob")))
        for value, name in (
            (properties["limits"], "CommandJobLimits"),
            (properties["resources"], "JobResourceConfiguration"),
            (properties["identity"], "ManagedIdentity"),
            (properties["inputs"]["study"], "UriFileJobInput"),
            (properties["outputs"]["artifact"], "UriFileJobOutput"),
        ):
            schema = fields(name)
            self.assertFalse(set(value) - set(schema), (name, set(value) - set(schema)))
            for k, v in value.items():
                field = schema[k]
                if "$ref" in field and field["$ref"].split("/")[-1] in defs:
                    field = defs[field["$ref"].split("/")[-1]]
                if "enum" in field:
                    self.assertIn(v, field["enum"])
        self.assertEqual(duration_seconds("PT2H"), 7200)
        self.assertEqual(duration_seconds("PT2M"), 120)
        self.assertIsNone(duration_seconds("P1D"))
        self.assertIsNone(duration_seconds("PT"))
        self.passed(self.action("submit"))
        self.observed["properties"]["limits"]["timeout"] = "PT2H"
        self.passed(self.action("observe"))

    def test_owner_roles_scopes_approvals_and_secret_projection(self):
        url = f"/v1/projects/{self.f.project}/cloud-runs"
        body = {
            "study": self.study["id"],
            "profile_id": self.p["id"],
            "data_approved": False,
            "compute_approved": True,
            "request_key": "azure-scoped-approval",
        }
        self.assertEqual(self.f.client.post(url, json=body).status_code, 400)
        body["data_approved"] = True
        for token in ("infer-secret", "review-secret"):
            self.assertEqual(
                self.f.client.post(
                    url, json=body, headers={"Authorization": "Bearer " + token}
                ).status_code,
                403,
            )
        self.assertEqual(
            self.f.client.post(
                url, json=body, headers={"Authorization": "Bearer b-secret"}
            ).status_code,
            404,
        )
        run = self.create()
        self.assertNotIn("credential_prefix", json.dumps(run))
        self.assertNotIn("fixture-secret", json.dumps(run))
        self.assertEqual(
            self.f.client.get(
                url + "/" + run["id"], headers={"Authorization": "Bearer b-secret"}
            ).status_code,
            404,
        )
        self.assertEqual(self.calls, [])

    def test_auth_errors_and_redirects_are_never_retried_or_followed(self):
        for status in (503, 302):
            calls = []

            def error(request):
                calls.append(request)
                return httpx.Response(
                    status, headers={"Location": "https://evil.example/token"}
                )

            gateway = AzureGateway(
                self.p,
                httpx.Client(
                    transport=httpx.MockTransport(error),
                    follow_redirects=False,
                    trust_env=False,
                ),
                "fixture-secret",
            )
            try:
                with self.assertRaises(CloudUnavailable):
                    gateway.token("https://management.azure.com/.default")
                self.assertEqual(len(calls), 1)
            finally:
                gateway.close()

    def test_create_update_response_is_uncertain_not_accepted_as_new_compute(self):
        self.create()
        self.create_status = 200
        self.assertEqual(self.action("submit")["status"], "failed")
        state = self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]
        self.assertEqual(state["phase"], "unknown")
        self.assertTrue(state["create_attempted"])
        self.passed(self.action("observe"))
        self.assertEqual(
            sum(m == "PUT" and "management.azure.com" in u for m, u in self.calls), 1
        )

    def test_standard_tracking_metadata_allowed_interactive_services_refused(self):
        self.create()
        self.passed(self.action("submit"))
        self.observed["properties"]["services"] = {
            "Studio": {
                "jobServiceType": "Studio",
                "endpoint": "https://ml.azure.com/fixture",
            },
            "Tracking": {
                "jobServiceType": "Tracking",
                "endpoint": "azureml://fixture-tracking",
            },
        }
        self.passed(self.action("observe"))
        for kind in ("SSH", "JupyterLab", "TensorBoard", "Custom", "VSCode"):
            self.observed["properties"]["services"] = {
                "injected": {"jobServiceType": kind, "endpoint": "https://evil.example"}
            }
            self.assertEqual(self.action("observe")["status"], "failed")
        self.assertFalse(any("evil.example" in u for _, u in self.calls))

    def test_compressed_archive_overflow_removes_only_new_output(self):
        folder = self.f.root / "large"
        folder.mkdir()
        (folder / "model.safetensors").write_bytes(os.urandom(1024))
        output = self.f.root / "overflow.tar.gz"
        with self.assertRaises(ValueError):
            pack_export(folder, output, 1024)
        self.assertFalse(output.exists())
        self.assertEqual((folder / "model.safetensors").stat().st_size, 1024)
        existing = self.f.root / "existing.tar.gz"
        existing.write_bytes(b"preserved")
        with self.assertRaises(FileExistsError):
            pack_export(folder, existing, 4096)
        self.assertEqual(existing.read_bytes(), b"preserved")
