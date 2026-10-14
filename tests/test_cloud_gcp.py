"""Fictional Google REST responses; real auth signing/serialization, no GCP resources."""

import base64
import copy
import hashlib
import json
import os
import unittest
import uuid
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import httpx
from decision_workbench.platform.cloud import validate_history
from decision_workbench.platform.cloud_aws import CloudUnavailable
from decision_workbench.platform.cloud_gcp import (
    GcpGateway,
    plan,
    object_key,
    validate_profile,
    artifact_location,
    AuthRequest,
)
from decision_workbench.platform.store import Conflict
from decision_workbench.training.gcp_runner import pack_export
from decision_workbench.training.provenance import source_fingerprint
from decision_workbench.platform.recovery import backup_workspace, restore_workspace
import test_remote_admission as fixtures

OWNER = fixtures.OWNER


class Credentials:
    valid = True
    token = "fictional-token"
    service_account_email = "operator@dmt-fixture.iam.gserviceaccount.com"


class GcpCloudTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.RemoteAdmissionTests("runTest")
        self.f.setUp()
        self.p = {
            "id": "gcp-fixture",
            "provider": "gcp",
            "tenants": ["A"],
            "credential_prefix": "DMT_CLOUD_GCP",
            "account_id": "123456789012",
            "project_id": "dmt-fixture",
            "region": "europe-west4",
            "operator_service_account": Credentials.service_account_email,
            "training_service_account": "trainer@dmt-fixture.iam.gserviceaccount.com",
            "bucket": "dmt-fixture",
            "prefix": "decision-runs",
            "image_digest": "europe-west4-docker.pkg.dev/dmt-fixture/trainers/laya@sha256:"
            + "a" * 64,
            "gpu": "NVIDIA_TESLA_T4",
            "machine_type": "n1-standard-8",
            "volume_gb": 100,
            "max_runtime_seconds": 7200,
            "max_runs_day": 2,
            "max_inflight": 1,
            "max_artifact_bytes": 1024**2,
            "source_sha256": source_fingerprint(),
        }
        self.profile_file = self.f.root / "profiles.json"
        self.write_profiles()
        self.env = patch.dict(
            os.environ, {"DMT_CLOUD_PROFILES_FILE": str(self.profile_file)}
        )
        self.env.start()
        self.f.config.update(
            {k: self.p[k] for k in ("provider", "region", "gpu", "image_digest")}
        )
        self.study = self.f.study()
        self.cloud = self.f.service.cloud
        self.calls = []
        self.observed = None
        self.fail_create = False
        self.duplicates = False
        self.missing = False
        self.output = None
        self.truncated = False
        self.corrupt = False
        self.f.worker.service.cloud.gateway_factory = self.gateway

    def tearDown(self):
        self.env.stop()
        self.f.tearDown()

    def write_profiles(self, extra=None):
        self.profile_file.write_text(
            json.dumps(
                {
                    "format": "domain-cloud-profiles-v1",
                    "profiles": [self.p] + (extra or []),
                }
            )
        )

    def gateway(self, p):
        return GcpGateway(
            p,
            httpx.Client(
                transport=httpx.MockTransport(self.transport), trust_env=False
            ),
            Credentials(),
        )

    def transport(self, request):
        self.calls.append((request.method, str(request.url)))
        self.assertEqual(request.headers.get("Authorization"), "Bearer fictional-token")
        url = request.url
        if url.host == "cloudresourcemanager.googleapis.com":
            return httpx.Response(
                200,
                json={
                    "projectId": self.p["project_id"],
                    "projectNumber": self.p["account_id"],
                    "lifecycleState": "ACTIVE",
                },
            )
        if url.host == "storage.googleapis.com":
            if request.method == "POST":
                body = request.read()
                self.assertEqual(url.params["ifGenerationMatch"], "0")
                self.assertEqual(url.params["uploadType"], "media")
                self.assertEqual(
                    hashlib.sha256(body).hexdigest(),
                    self.study["payload"]["archive_sha256"],
                )
                return httpx.Response(
                    200,
                    json={
                        "bucket": self.p["bucket"],
                        "name": url.params["name"],
                        "size": str(len(body)),
                        "generation": "11",
                        "md5Hash": base64.b64encode(
                            hashlib.md5(body, usedforsecurity=False).digest()
                        ).decode(),
                    },
                )
            if "/o/" not in url.path:
                return httpx.Response(
                    200,
                    json={
                        "name": self.p["bucket"],
                        "projectNumber": self.p["account_id"],
                        "location": self.p["region"].upper(),
                    },
                )
            if url.params.get("alt") == "media":
                self.assertEqual(url.params["generation"], "22")
                body = self.output[:-1] if self.truncated else self.output
                return httpx.Response(200, stream=httpx.ByteStream(body))
            return httpx.Response(
                200,
                json={
                    "bucket": self.p["bucket"],
                    "name": object_key(self.runtime_run(), "output"),
                    "size": str(len(self.output)),
                    "generation": "22",
                    "md5Hash": base64.b64encode(
                        hashlib.md5(
                            self.output + (b"corrupt" if self.corrupt else b""),
                            usedforsecurity=False,
                        ).digest()
                    ).decode(),
                },
            )
        self.assertEqual(url.host, "europe-west4-aiplatform.googleapis.com")
        if request.method == "POST" and url.path.endswith("customJobs"):
            value = json.loads(request.read())
            self.assertEqual(value, plan(self.runtime_run()))
            self.observed = {
                **copy.deepcopy(value),
                "name": "projects/123456789012/locations/europe-west4/customJobs/101",
                "state": "JOB_STATE_RUNNING",
            }
            return (
                httpx.Response(503, json={"error": "fixture lost create response"})
                if self.fail_create
                else httpx.Response(200, json=self.observed)
            )
        if request.method == "POST" and url.path.endswith(":cancel"):
            self.assertEqual(json.loads(request.read()), {})
            return httpx.Response(200, json={})
        if url.path.endswith("customJobs"):
            self.assertEqual(url.params["pageSize"], "2")
            self.assertIn(self.run["id"], url.params["filter"])
            return httpx.Response(
                200,
                json={
                    "customJobs": (
                        []
                        if self.missing
                        else [self.observed] * (2 if self.duplicates else 1)
                    )
                },
            )
        return httpx.Response(200, json=self.observed)

    def create(self):
        url = f"/v1/projects/{self.f.project}/cloud-runs"
        response = self.f.client.post(
            url,
            json={
                "study": self.study["id"],
                "profile_id": self.p["id"],
                "data_approved": True,
                "compute_approved": True,
                "request_key": "approve-gcp-once",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.run = response.json()
        return self.run

    def runtime_run(self):
        entity = self.cloud.get(OWNER, self.f.project, self.run["id"])
        return {
            **entity["payload"],
            "input_receipt": entity["state"].get("input_receipt"),
        }

    def action(self, name):
        run = self.cloud.get(OWNER, self.f.project, self.run["id"])
        response = self.f.client.post(
            f"/v1/projects/{self.f.project}/cloud-runs/{self.run['id']}/actions",
            json={
                "action": name,
                "revision": run["state"]["revision"],
                "request_key": "gcp-action-" + uuid.uuid4().hex,
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        job = response.json()
        self.f.worker.once()
        return self.f.store.job(job["id"], "A", self.f.project)

    def succeeded(self, job):
        self.assertEqual(job["status"], "succeeded", job["error"])

    def test_api_worker_create_observe_cancel_and_terminal_history(self):
        self.create()
        self.succeeded(self.action("submit"))
        self.succeeded(self.action("observe"))
        self.succeeded(self.action("stop"))
        state = self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]
        self.assertEqual(state["phase"], "stop_requested")
        self.observed["state"] = "JOB_STATE_CANCELLED"
        self.succeeded(self.action("observe"))
        validate_history(self.f.store)
        counts = Counter(method for method, url in self.calls if "aiplatform" in url)
        self.assertEqual(counts["POST"], 2)

    def test_lost_create_response_reconciles_original_without_replay(self):
        self.create()
        self.fail_create = True
        self.assertEqual(self.action("submit")["status"], "failed")
        state = self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]
        self.assertEqual(state["phase"], "unknown")
        self.assertTrue(state["create_attempted"])
        self.assertEqual(self.action("submit")["status"], "failed")
        self.assertEqual(self.action("abandon")["status"], "failed")
        self.succeeded(self.action("observe"))
        self.assertEqual(
            sum(m == "POST" and u.endswith("customJobs") for m, u in self.calls), 1
        )
        validate_history(self.f.store)

    def test_duplicate_absent_changed_job_and_configuration_hold_capacity(self):
        self.create()
        self.succeeded(self.action("submit"))
        for flag in ("duplicates", "missing"):
            setattr(self, flag, True)
            self.assertEqual(self.action("observe")["status"], "failed")
            setattr(self, flag, False)
        old = copy.deepcopy(self.observed)
        self.observed["jobSpec"]["workerPoolSpecs"][0]["replicaCount"] = "2"
        self.assertEqual(self.action("observe")["status"], "failed")
        self.observed = old
        self.observed["name"] = self.observed["name"].replace("101", "102")
        self.assertEqual(self.action("observe")["status"], "failed")
        self.assertEqual(
            self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]["phase"],
            "unknown",
        )

    def test_completed_download_and_existing_independent_admission(self):
        self.create()
        self.succeeded(self.action("submit"))
        self.observed["state"] = "JOB_STATE_SUCCEEDED"
        self.succeeded(self.action("observe"))
        folder, checksum, _ = self.f.export(self.study)
        archive = self.f.root / "returned.tar.gz"
        pack_export(folder, archive, self.p["max_artifact_bytes"])
        self.output = archive.read_bytes()
        self.succeeded(self.action("retrieve"))
        state = self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]
        admission = self.f.store.job(state["admission_job"], "A", self.f.project)
        self.assertEqual(admission["kind"], "remote-import")
        self.assertEqual(admission["status"], "queued")
        self.assertEqual(admission["config"]["provider_receipt"]["provider"], "gcp")
        self.assertEqual(admission["config"]["manifest_sha256"], checksum)
        self.assertEqual(self.f.store.list("A", self.f.project, "model"), [])
        validate_history(self.f.store)
        self.f.store.cancel(admission["id"], "A", self.f.project, "owner")

    def test_truncated_and_corrupt_generation_bound_downloads_refused(self):
        self.create()
        self.succeeded(self.action("submit"))
        self.output = b"fixture-output"
        run = self.runtime_run()
        gateway = self.gateway(self.p)
        uri = "gs://" + self.p["bucket"] + "/" + object_key(run, "output")
        try:
            for flag in ("truncated", "corrupt"):
                setattr(self, flag, True)
                with self.assertRaises(CloudUnavailable):
                    gateway.download(run, uri, self.f.root / (flag + ".tar.gz"))
                setattr(self, flag, False)
        finally:
            gateway.close()

    def test_profiles_refuse_endpoints_ambient_credentials_and_resource_drift(self):
        for key, value in (
            ("endpoint", "https://evil.example"),
            ("gpu", "CPU"),
            (
                "image_digest",
                self.p["image_digest"].replace("dmt-fixture", "other-project"),
            ),
            ("max_runtime_seconds", True),
        ):
            with self.assertRaises(ValueError):
                validate_profile({**self.p, key: value})
        with patch.dict(os.environ, {"DMT_CLOUD_GCP_CREDENTIALS_FILE": ""}):
            with self.assertRaises(CloudUnavailable):
                GcpGateway.from_operator(self.p)
        self.create()
        self.p["machine_type"] = "n1-standard-16"
        self.write_profiles()
        with self.assertRaises(CloudUnavailable):
            self.cloud.queue(
                OWNER, self.f.project, self.run["id"], "submit", 1, "changed-profile"
            )
        self.assertEqual(self.calls, [])

    def test_auth_transport_real_google_signing_fixed_endpoint_and_no_retries(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        info = {
            "type": "service_account",
            "project_id": self.p["project_id"],
            "client_email": self.p["operator_service_account"],
            "private_key": pem,
            "token_uri": "https://oauth2.googleapis.com/token",
        }
        path = self.f.root / "credentials.json"
        path.write_text(json.dumps(info))
        seen = []

        def token_transport(request):
            seen.append(request)
            self.assertEqual(str(request.url), "https://oauth2.googleapis.com/token")
            self.assertIn(b"assertion=", request.read())
            return httpx.Response(
                200,
                json={
                    "access_token": "fixture-signed-token",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )

        with patch.dict(os.environ, {"DMT_CLOUD_GCP_CREDENTIALS_FILE": str(path)}):
            with patch.object(
                GcpGateway,
                "client_new",
                return_value=httpx.Client(
                    transport=httpx.MockTransport(token_transport)
                ),
            ):
                gateway = GcpGateway.from_operator(self.p)
            self.assertEqual(gateway.token(), "fixture-signed-token")
            self.assertEqual(len(seen), 1)
            gateway.close()
            info["token_uri"] = "https://evil.example/token"
            path.write_text(json.dumps(info))
            with self.assertRaises(CloudUnavailable):
                GcpGateway.from_operator(self.p)
        failures = []

        def denied(request):
            failures.append(request)
            return httpx.Response(503, json={"error": "server_error"})

        client = httpx.Client(transport=httpx.MockTransport(denied))
        try:
            with self.assertRaises(CloudUnavailable):
                AuthRequest(client)(
                    "https://oauth2.googleapis.com/token", method="POST"
                )
            with self.assertRaises(CloudUnavailable):
                AuthRequest(client)("https://evil.example/token")
            self.assertEqual(len(failures), 1)
        finally:
            client.close()

    def test_google_discovery_request_contract_is_current(self):
        discovery = json.loads(
            Path(
                "tests/fixtures/provider-contracts/cloud-gcp-contract-2026-10-11.json"
            ).read_bytes()
        )
        self.create()
        run = {**self.run["payload"], "input_receipt": {"generation": "11"}}
        value = plan(run)
        schemas = discovery["schemas"]

        def validate(value, name):
            schema = schemas[name]
            self.assertFalse(
                set(value) - set(schema["properties"]),
                (name, set(value) - set(schema["properties"])),
            )
            for key, actual in value.items():
                prop = schema["properties"][key]
                if "$ref" in prop:
                    validate(actual, prop["$ref"])
                elif prop.get("type") == "array" and "$ref" in prop["items"]:
                    for item in actual:
                        validate(item, prop["items"]["$ref"])
                elif "enum" in prop:
                    self.assertIn(actual, prop["enum"])
                elif prop.get("type") == "string":
                    self.assertIsInstance(actual, str)

        validate(value, "GoogleCloudAiplatformV1CustomJob")
        methods = discovery["resources"]["projects"]["resources"]["locations"][
            "resources"
        ]["customJobs"]["methods"]
        self.assertEqual(methods["create"]["httpMethod"], "POST")
        self.assertEqual(set(methods["create"]["parameters"]), {"parent"})

    def test_unknown_run_backup_restore_preserves_receipt_and_capacity(self):
        from decision_workbench.platform.store import Store
        from decision_workbench.platform.service import Service

        self.create()
        self.fail_create = True
        self.assertEqual(self.action("submit")["status"], "failed")
        from decision_workbench.training.artifacts import file_sha

        archive = self.f.root / "backup"
        restored = self.f.root / "restored"
        backup_workspace(self.f.store.folder, archive)
        restore_workspace(archive, restored, file_sha(archive / "backup-manifest.json"))
        service = Service(Store(restored))
        try:
            run = service.cloud.get(OWNER, self.f.project, self.run["id"])
            self.assertEqual(
                run["state"],
                self.cloud.get(OWNER, self.f.project, self.run["id"])["state"],
            )
            self.assertEqual(run["state"]["input_receipt"]["generation"], "11")
            validate_history(service.store)
        finally:
            service.runtime.close()

    def test_shared_quota_aliases_and_provider_namespaces(self):
        self.create()
        config = {**self.f.config}
        job = self.f.service.submit(
            OWNER, self.f.project, "remote-study", config, "second-gcp-study"
        )
        self.f.worker.once()
        with self.assertRaises(Conflict):
            self.cloud.create(
                OWNER,
                self.f.project,
                job["id"],
                self.p["id"],
                True,
                True,
                "second-gcp-approval",
            )
        aws = {
            k: v
            for k, v in self.p.items()
            if k
            not in (
                "project_id",
                "operator_service_account",
                "training_service_account",
                "machine_type",
            )
        }
        aws.update(
            id="aws-fixture",
            provider="aws",
            region=self.p["region"],
            gpu="ml.g5.xlarge",
            image_digest="123456789012.dkr.ecr.eu-west-1.amazonaws.com/trainer@sha256:"
            + "a" * 64,
            role_arn="arn:aws:iam::123456789012:role/trainer",
        )
        # AWS/GCP region syntax differs, so use a syntactically AWS region and
        # its corresponding image. Namespace logic is also directly checked.
        aws["region"] = "eu-west-1"
        self.write_profiles([aws])
        config.update(
            {k: aws[k] for k in ("provider", "region", "gpu", "image_digest")}
        )
        job = self.f.service.submit(
            OWNER, self.f.project, "remote-study", config, "aws-other-provider-study"
        )
        self.f.worker.once()
        other = self.cloud.create(
            OWNER,
            self.f.project,
            job["id"],
            aws["id"],
            True,
            True,
            "other-cloud-approval",
        )
        self.assertEqual(other["state"]["phase"], "ready")
        self.assertEqual(self.calls, [])

    def test_project_bucket_identity_and_response_size_refusal(self):
        for change in ("project", "bucket", "oversize"):

            def bad(request):
                if change == "oversize":
                    return httpx.Response(200, content=b"x" * (1024**2 + 1))
                if request.url.host == "cloudresourcemanager.googleapis.com":
                    return httpx.Response(
                        200,
                        json={
                            "projectId": self.p["project_id"],
                            "projectNumber": (
                                "999999999999"
                                if change == "project"
                                else self.p["account_id"]
                            ),
                            "lifecycleState": "ACTIVE",
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "name": self.p["bucket"],
                        "projectNumber": self.p["account_id"],
                        "location": "US",
                    },
                )

            gateway = GcpGateway(
                self.p, httpx.Client(transport=httpx.MockTransport(bad)), Credentials()
            )
            try:
                with self.assertRaises(CloudUnavailable):
                    gateway.identity()
            finally:
                gateway.close()

    def test_training_wrapper_uses_frozen_runner_and_regular_ustar_output(self):
        import shutil
        from decision_workbench.training.gcp_runner import execute
        from decision_workbench.platform.cloud_archive import extract_model

        self.create()
        self.succeeded(self.action("submit"))
        run = self.runtime_run()
        profile = run["profile"]
        runtime = {
            "id": run["id"],
            "profile": profile,
            "archive_sha256": run["archive_sha256"],
            "request_sha256": run["request_sha256"],
            "input_generation": "11",
        }
        folder, checksum, _ = self.f.export(self.study)

        class TrainingGateway:
            closed = False

            def download_object(inner, r, kind, target, generation):
                self.assertEqual(generation, "11")
                self.assertEqual(kind, "input")
                shutil.copyfile(
                    self.f.store.folder
                    / "jobs"
                    / self.study["id"]
                    / "remote-study/study.zip",
                    target,
                )

            def upload_file(inner, r, path, kind):
                self.assertEqual(kind, "output")
                dest = self.f.root / "wrapper-extracted"
                extract_model(path, dest, profile["max_artifact_bytes"])
                self.assertEqual(
                    (dest / "manifest.json").read_bytes(),
                    (folder / "manifest.json").read_bytes(),
                )
                return {"fixture": True}

            def close(inner):
                inner.closed = True

        gateway = TrainingGateway()

        def fake_trainer(command, check):
            self.assertTrue(check)
            self.assertIn("decision_workbench.training.cloud_runner", command)
            self.assertEqual(
                command[command.index("--archive-sha256") + 1],
                runtime["archive_sha256"],
            )
            shutil.copytree(folder, Path(command[command.index("--out") + 1]))

        with (
            patch.object(GcpGateway, "from_training", return_value=gateway),
            patch(
                "decision_workbench.training.gcp_runner.subprocess.run",
                side_effect=fake_trainer,
            ),
        ):
            execute(runtime, self.f.root / "wrapper")
        self.assertTrue(gateway.closed)
        self.assertEqual(self.f.store.list("A", self.f.project, "model"), [])

    def test_training_credentials_use_actual_compute_auth_and_selected_identity(self):
        requests = []

        def metadata(request):
            requests.append(request)
            self.assertEqual(request.url.host, "metadata.google.internal")
            self.assertEqual(request.headers["Metadata-Flavor"], "Google")
            if request.url.path.endswith("/token"):
                return httpx.Response(
                    200,
                    json={
                        "access_token": "fixture-training-token",
                        "expires_in": 3600,
                        "token_type": "Bearer",
                    },
                    headers={"Metadata-Flavor": "Google"},
                )
            return httpx.Response(
                200,
                json={
                    "email": self.p["training_service_account"],
                    "scopes": ["https://www.googleapis.com/auth/cloud-platform"],
                    "aliases": ["default"],
                },
                headers={"Metadata-Flavor": "Google"},
            )

        with patch.object(
            GcpGateway,
            "client_new",
            return_value=httpx.Client(transport=httpx.MockTransport(metadata)),
        ):
            gateway = GcpGateway.from_training(self.p)
        try:
            self.assertEqual(gateway.token(), "fixture-training-token")
            self.assertEqual(len(requests), 2)
        finally:
            gateway.close()

    def test_cancel_second_observation_cannot_switch_numeric_job_identity(self):
        self.create()
        self.succeeded(self.action("submit"))
        run = self.runtime_run()
        run["provider_job_id"] = self.observed["name"]
        self.observed["name"] = self.observed["name"].replace("101", "102")
        gateway = self.gateway(self.p)
        try:
            with self.assertRaises(CloudUnavailable):
                gateway.stop(run)
            self.assertFalse(any(u.endswith(":cancel") for _, u in self.calls))
        finally:
            gateway.close()

    def test_history_refuses_cleared_job_identity_and_changed_input_generation(self):
        from decision_workbench.platform.store import digest, encode

        self.create()
        self.succeeded(self.action("submit"))
        self.succeeded(self.action("observe"))
        with self.f.store.transaction() as db:
            latest = self.cloud.latest(db, self.run)
        original = copy.deepcopy(latest["payload"])
        for key, value in (
            ("provider_job_id", None),
            ("input_receipt", {**original["input_receipt"], "generation": "999"}),
        ):
            changed = {**original, key: value}
            with self.f.store.transaction() as db:
                db.execute(
                    "UPDATE entities SET payload=? WHERE id=?",
                    (encode(changed), latest["id"]),
                )
            with self.assertRaises(ValueError):
                validate_history(self.f.store)
            with self.f.store.transaction() as db:
                db.execute(
                    "UPDATE entities SET payload=? WHERE id=?",
                    (encode(original), latest["id"]),
                )
        validate_history(self.f.store)
