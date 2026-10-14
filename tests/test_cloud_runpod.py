"""RunPod v2/SigV4 serialization fixtures; no RunPod account or GPU calls."""

import copy
import hashlib
import json
import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx
from decision_workbench.platform.cloud_runpod import (
    RunPodGateway,
    plan,
    object_key,
    pod_name,
    validate_profile,
    validate_result,
    artifact_location,
    BASE,
)
from decision_workbench.platform.cloud import validate_history
from decision_workbench.platform.cloud_aws import CloudUnavailable
from decision_workbench.training.provenance import source_fingerprint
from decision_workbench.training.cloud_packaging import pack_export
from decision_workbench.training.artifacts import file_sha
import test_remote_admission as fixtures

OWNER = fixtures.OWNER


def profile():
    return {
        "id": "runpod-fixture",
        "provider": "runpod",
        "tenants": ["A"],
        "credential_prefix": "DMT_CLOUD_RUNPOD",
        "account_id": "user_fixture",
        "region": "EU-RO-1",
        "volume_id": "vol_fixture",
        "volume_gb": 100,
        "prefix": "studies",
        "registry_id": None,
        "image_digest": "ghcr.io/example/trainer@sha256:" + "a" * 64,
        "gpu": "NVIDIA GeForce RTX 4090",
        "cuda_version": "12.8",
        "disk_gb": 50,
        "max_runtime_seconds": 7200,
        "max_runs_day": 2,
        "max_inflight": 1,
        "max_artifact_bytes": 1024**2,
        "source_sha256": source_fingerprint(),
    }


def pod_response(run, identifier="pod_fixture"):
    body = plan(run)
    return {
        **{
            k: body[k]
            for k in (
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
            )
        },
        "args": json.dumps({"entrypoint": body["entrypoint"], "cmd": body["cmd"]}),
        "id": identifier,
        "status": "RUNNING",
        "gpu": {"id": run["profile"]["gpu"], "count": 1, "memory": 64, "vcpuCount": 16},
        "dataCenterId": run["profile"]["region"],
        "cudaVersion": run["profile"]["cuda_version"],
        "globalNetworking": {"enabled": False},
        "template": None,
        "locked": False,
        "cost": 0.44,
        "runtime": None,
    }


class RunPodCloudTests(unittest.TestCase):
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
        self.calls, self.objects, self.pods = [], {}, []
        self.fail_create = self.fail_delete = False
        self.corrupt = self.truncated = False
        self.registry = None
        self.volume = {
            "id": self.p["volume_id"],
            "dataCenter": self.p["region"],
            "size": self.p["volume_gb"],
            "type": "STANDARD",
        }
        self.pagination = {"nextCursor": None, "hasNextPage": False}
        self.f.worker.service.cloud.gateway_factory = self.gateway

    def tearDown(self):
        self.env.stop()
        self.f.tearDown()

    def write_profiles(self):
        self.file.write_text(
            json.dumps({"format": "domain-cloud-profiles-v1", "profiles": [self.p]}),
            encoding="utf-8",
        )

    def gateway(self, p):
        return RunPodGateway(
            p,
            httpx.Client(
                transport=httpx.MockTransport(self.transport),
                trust_env=False,
                follow_redirects=False,
            ),
            "dummy-api-key",
            p["account_id"],
            "dummy-storage-secret",
        )

    def run_value(self):
        e = self.cloud.get(OWNER, self.f.project, self.run["id"])
        return {
            **e["payload"],
            **{
                k: e["state"].get(k)
                for k in (
                    "provider_job_id",
                    "training_result",
                    "termination_attempted",
                    "termination_acknowledged",
                    "input_receipt",
                )
            },
        }

    def transport(self, request):
        self.calls.append((request.method, str(request.url)))
        if request.url.host == "api.runpod.io":
            self.assertEqual(request.headers["Authorization"], "Bearer dummy-api-key")
            path = request.url.path
            if path == "/v2/network-volumes/" + self.p["volume_id"]:
                return httpx.Response(200, json=self.volume)
            if path.startswith("/v2/registries/"):
                return httpx.Response(200, json=self.registry)
            if path == "/v2/pods":
                if request.method == "GET":
                    self.assertEqual(request.url.params["limit"], "1000")
                    self.assertEqual(request.url.params["includeClusterPods"], "true")
                    return httpx.Response(
                        200, json={"pods": self.pods, "pagination": self.pagination}
                    )
                self.assertEqual(request.method, "POST")
                self.assertEqual(json.loads(request.read()), plan(self.run_value()))
                self.pods = [pod_response(self.run_value())]
                if self.fail_create:
                    raise httpx.ReadTimeout("lost response", request=request)
                return httpx.Response(201, json=self.pods[0])
            if path == "/v2/pods/pod_fixture":
                if request.method == "DELETE":
                    self.pods = []
                    if self.fail_delete:
                        raise httpx.ReadTimeout(
                            "lost deletion acknowledgement", request=request
                        )
                    return httpx.Response(
                        204, headers={"Location": "https://evil.example"}
                    )
                return (
                    httpx.Response(200, json=self.pods[0])
                    if self.pods
                    else httpx.Response(404)
                )
            raise AssertionError("Unexpected control path " + path)
        self.assertEqual(request.url.host, "s3api-eu-ro-1.runpod.io")
        self.assertIn("Credential=user_fixture/", request.headers["Authorization"])
        self.assertIn("/EU-RO-1/s3/aws4_request", request.headers["Authorization"])
        self.assertNotIn("dummy-storage-secret", request.headers["Authorization"])
        path = request.url.path
        if path == "/" + self.p["volume_id"]:
            return httpx.Response(200)
        prefix = "/" + self.p["volume_id"] + "/"
        self.assertTrue(path.startswith(prefix))
        key = path[len(prefix) :]
        if request.method == "PUT":
            data = request.read()
            self.assertEqual(
                hashlib.sha256(data).hexdigest(),
                request.headers["x-amz-content-sha256"],
            )
            self.assertEqual(len(data), int(request.headers["Content-Length"]))
            self.objects[key] = data
            return httpx.Response(
                200, headers={"ETag": '"' + hashlib.md5(data).hexdigest() + '"'}
            )
        if key not in self.objects:
            return httpx.Response(404)
        data = self.objects[key]
        headers = {
            "ETag": '"' + hashlib.md5(data).hexdigest() + '"',
            "Content-Length": str(len(data)),
        }
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers)
        self.assertEqual(request.method, "GET")
        if key.endswith("model.tar.gz"):
            if self.corrupt:
                data = data[:-1] + bytes([data[-1] ^ 1])
            elif self.truncated:
                data = data[:-1]
        return httpx.Response(200, headers=headers, stream=httpx.ByteStream(data))

    def create(self):
        r = self.f.client.post(
            f"/v1/projects/{self.f.project}/cloud-runs",
            json={
                "study": self.study["id"],
                "profile_id": self.p["id"],
                "data_approved": True,
                "compute_approved": True,
                "request_key": "approve-runpod-once",
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
                "request_key": "runpod-action-" + uuid.uuid4().hex,
            },
        )
        self.assertEqual(r.status_code, 202, r.text)
        self.f.worker.once()
        return self.f.store.job(r.json()["id"], "A", self.f.project)

    def passed(self, job):
        self.assertEqual(job["status"], "succeeded", job["error"])

    def ready_output(self, failed=False):
        run = self.run_value()
        marker = {
            "format": "domain-runpod-result-v1",
            "run_id": run["id"],
            "archive_sha256": run["archive_sha256"],
            "request_sha256": run["request_sha256"],
            "source_sha256": self.p["source_sha256"],
            "status": "failed" if failed else "succeeded",
        }
        if not failed:
            folder, checksum, _ = self.f.export(self.study)
            packed = self.f.root / "model.tar.gz"
            pack_export(folder, packed, self.p["max_artifact_bytes"])
            data = packed.read_bytes()
            self.objects[object_key(run, "output")] = data
            marker.update(
                artifact_sha256=hashlib.sha256(data).hexdigest(),
                artifact_bytes=len(data),
                manifest_sha256=checksum,
            )
        self.objects[object_key(run, "result")] = json.dumps(marker).encode()
        return marker

    def test_actual_owner_api_worker_ready_output_keeps_capacity_until_termination(
        self,
    ):
        self.create()
        self.passed(self.action("submit"))
        self.ready_output()
        self.passed(self.action("observe"))
        state = self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]
        self.assertEqual(state["phase"], "InProgress")
        self.assertTrue(state["output_ready"])
        self.assertIsNone(state["artifact_uri"])
        self.passed(self.action("stop"))
        self.passed(self.action("observe"))
        state = self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]
        self.assertEqual(state["phase"], "Completed")
        self.assertTrue(
            state["termination_attempted"] and state["termination_acknowledged"]
        )
        validate_history(self.f.store)
        self.assertEqual(
            sum(m == "POST" and u == BASE + "/pods" for m, u in self.calls), 1
        )
        self.assertFalse(any("evil.example" in u for _, u in self.calls))

    def test_lost_creation_reconciles_one_pod_and_never_replays(self):
        self.create()
        self.fail_create = True
        self.assertEqual(self.action("submit")["status"], "failed")
        self.passed(self.action("observe"))
        self.assertEqual(self.action("submit")["status"], "failed")
        self.assertEqual(
            sum(m == "POST" and u == BASE + "/pods" for m, u in self.calls), 1
        )
        validate_history(self.f.store)

    def test_duplicate_name_incomplete_page_and_absent_unknown_creation_refused(self):
        self.create()
        self.fail_create = True
        self.action("submit")
        gateway = self.gateway(self.p)
        try:
            original = copy.deepcopy(self.pods)
            self.pods.append({**copy.deepcopy(original[0]), "id": "pod_second"})
            with self.assertRaises(CloudUnavailable):
                gateway.describe(self.run_value())
            self.pods = original
            self.pagination = {"nextCursor": "later", "hasNextPage": True}
            with self.assertRaises(CloudUnavailable):
                gateway.describe(self.run_value())
            self.pagination = {"nextCursor": None, "hasNextPage": False}
            self.pods = []
            with self.assertRaises(CloudUnavailable):
                gateway.describe(self.run_value())
        finally:
            gateway.close()

    def test_lost_termination_acknowledgement_does_not_free_or_repeat_compute(self):
        self.create()
        self.passed(self.action("submit"))
        self.fail_delete = True
        self.assertEqual(self.action("stop")["status"], "failed")
        self.assertEqual(self.action("observe")["status"], "failed")
        self.assertEqual(self.action("stop")["status"], "failed")
        state = self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]
        self.assertEqual(state["phase"], "unknown")
        self.assertTrue(state["termination_attempted"])
        self.assertFalse(state.get("termination_acknowledged", False))
        self.assertEqual(sum(m == "DELETE" for m, _ in self.calls), 1)
        validate_history(self.f.store)

    def test_exit_error_and_failed_marker_require_termination_before_terminal_state(
        self,
    ):
        self.create()
        self.passed(self.action("submit"))
        self.ready_output(failed=True)
        for status in ("EXITED", "ERROR"):
            self.pods[0]["status"] = status
            self.passed(self.action("observe"))
            self.assertEqual(
                self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]["phase"],
                "InProgress",
            )
        self.passed(self.action("stop"))
        self.passed(self.action("observe"))
        self.assertEqual(
            self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]["phase"],
            "Failed",
        )

    def test_completed_retrieval_queues_inactive_independent_admission(self):
        self.create()
        self.passed(self.action("submit"))
        marker = self.ready_output()
        self.passed(self.action("stop"))
        self.passed(self.action("observe"))
        self.passed(self.action("retrieve"))
        state = self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]
        job = self.f.store.job(state["admission_job"], "A", self.f.project)
        self.assertEqual(job["kind"], "remote-import")
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["config"]["manifest_sha256"], marker["manifest_sha256"])
        self.assertEqual(self.f.store.list("A", self.f.project, "model"), [])
        self.f.store.cancel(job["id"], "A", self.f.project, "owner")
        validate_history(self.f.store)

    def test_configuration_and_identity_drift_refused_before_termination(self):
        self.create()
        self.passed(self.action("submit"))
        original = copy.deepcopy(self.pods[0])
        changes = [
            {"image": "evil:latest"},
            {"dataCenterId": "US-GA-2"},
            {"cudaVersion": "13.0"},
            {"ports": ["22/tcp"]},
            {"cmd": ["bash"]},
            {"env": {"TOKEN": "secret"}},
            {"mounts": {"network": [{"volumeId": "foreign", "path": "/workspace"}]}},
            {"gpu": {"id": self.p["gpu"], "count": 2}},
            {"args": "echo bypass"},
            {"status": "UNKNOWN"},
            {"globalNetworking": {"enabled": True}},
            {"cluster": {"id": "cluster"}},
        ]
        for change in changes:
            self.pods = [{**copy.deepcopy(original), **change}]
            self.assertEqual(self.action("stop")["status"], "failed", change)
        self.assertFalse(any(m == "DELETE" for m, _ in self.calls))

    def test_unversioned_output_corruption_truncation_and_marker_drift_refused(self):
        self.create()
        self.passed(self.action("submit"))
        marker = self.ready_output()
        self.passed(self.action("stop"))
        self.passed(self.action("observe"))
        run = self.run_value()
        gateway = self.gateway(self.p)
        uri = "s3://" + self.p["volume_id"] + "/" + object_key(run, "output")
        try:
            for mode in ("truncated", "corrupt"):
                setattr(self, mode, True)
                with self.assertRaises(CloudUnavailable):
                    gateway.download(run, uri, self.f.root / (mode + ".tar.gz"))
                setattr(self, mode, False)
            self.objects[object_key(run, "result")] = json.dumps(
                {**marker, "artifact_sha256": "b" * 64}
            ).encode()
            with self.assertRaises(CloudUnavailable):
                gateway.describe(run)
            with self.assertRaises(ValueError):
                artifact_location(run, "s3://foreign/model.tar.gz")
        finally:
            gateway.close()

    def test_upload_prefix_collision_and_volume_drift_refused(self):
        self.create()
        self.objects[object_key(self.run_value(), "result")] = b"existing"
        self.assertEqual(self.action("submit")["status"], "failed")
        self.assertFalse(
            any(m == "POST" and u == BASE + "/pods" for m, u in self.calls)
        )
        self.passed(self.action("abandon"))
        gateway = self.gateway(self.p)
        try:
            for change in (
                {"dataCenter": "US-CA-2"},
                {"size": 101},
                {"type": "HIGH_PERFORMANCE"},
                {"id": "foreign"},
            ):
                original = copy.deepcopy(self.volume)
                self.volume.update(change)
                with self.assertRaises(CloudUnavailable):
                    gateway.identity()
                self.volume = original
        finally:
            gateway.close()

    def test_selected_credentials_strict_profiles_and_source_changes(self):
        validate_profile(self.p)
        for change in (
            {"endpoint": "https://evil.example"},
            {"region": "unreviewed"},
            {"image_digest": "latest"},
            {"disk_gb": True},
            {"cuda_version": "12.8;rm"},
        ):
            with self.assertRaises(ValueError):
                validate_profile({**self.p, **change})
        with patch.dict(
            os.environ,
            {
                "RUNPOD_API_KEY": "ambient",
                "AWS_ACCESS_KEY_ID": self.p["account_id"],
                "AWS_SECRET_ACCESS_KEY": "ambient",
            },
            clear=True,
        ):
            with self.assertRaises(CloudUnavailable):
                RunPodGateway.from_operator(self.p)
        with patch.dict(
            os.environ,
            {
                "DMT_CLOUD_RUNPOD_API_KEY": "dummy",
                "DMT_CLOUD_RUNPOD_S3_ACCESS_KEY_ID": "user_foreign",
                "DMT_CLOUD_RUNPOD_S3_SECRET_ACCESS_KEY": "dummy",
            },
            clear=True,
        ):
            with self.assertRaises(CloudUnavailable):
                RunPodGateway.from_operator(self.p)
        self.create()
        self.p["source_sha256"] = "b" * 64
        self.write_profiles()
        state = self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]
        r = self.f.client.post(
            f"/v1/projects/{self.f.project}/cloud-runs/{self.run['id']}/actions",
            json={
                "action": "submit",
                "revision": state["revision"],
                "request_key": "changed-profile",
            },
        )
        self.assertEqual(r.status_code, 503)
        self.assertEqual(self.calls, [])

    def test_pinned_v2_request_shape_and_no_v1_lifecycle_fields(self):
        self.create()
        body = plan(self.run_value())
        spec = json.loads(
            Path(
                "tests/fixtures/provider-contracts/cloud-runpod-contract-2026-10-11.json"
            ).read_text(encoding="utf-8")
        )
        schemas = spec["schemas"]
        allowed = (
            set(schemas["BaseContainerConfig"]["properties"])
            | set(schemas["ContainerConfig"]["allOf"][1]["properties"])
            | set(schemas["CreatePodRequest"]["allOf"][1]["properties"])
        )
        self.assertLessEqual(set(body), allowed)
        self.assertFalse(
            set(body)
            & {
                "imageName",
                "dockerEntrypoint",
                "dockerStartCmd",
                "gpuTypeIds",
                "networkVolumeId",
                "interruptible",
            }
        )
        self.assertEqual(
            body["mounts"],
            {"network": [{"volumeId": self.p["volume_id"], "path": "/workspace"}]},
        )
        self.assertFalse(
            body["startSsh"] or body["startJupyter"] or body["globalNetworking"]
        )
        self.assertEqual(body["env"], {})

    def test_control_redirect_and_oversized_response_refused_without_retry(self):
        calls = []

        def response(request):
            calls.append(str(request.url))
            return httpx.Response(302, headers={"Location": "https://evil.example"})

        gateway = RunPodGateway(
            self.p,
            httpx.Client(
                transport=httpx.MockTransport(response), follow_redirects=False
            ),
            "dummy",
            self.p["account_id"],
            "dummy",
        )
        try:
            with self.assertRaises(CloudUnavailable):
                gateway.identity()
            self.assertEqual(len(calls), 1)
            self.assertFalse(any("evil.example" in u for u in calls))
        finally:
            gateway.close()
        gateway = RunPodGateway(
            self.p,
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, content=b" " * (1024**2 + 1))
                )
            ),
            "dummy",
            self.p["account_id"],
            "dummy",
        )
        try:
            with self.assertRaises(CloudUnavailable):
                gateway.identity()
        finally:
            gateway.close()

    def test_preexisting_pod_name_prevents_new_allocation(self):
        self.create()
        self.pods = [pod_response(self.run_value())]
        self.assertEqual(self.action("submit")["status"], "failed")
        self.assertFalse(
            any(m == "POST" and u == BASE + "/pods" for m, u in self.calls)
        )
        self.assertEqual(self.action("abandon")["status"], "failed")
        self.passed(self.action("observe"))

    def test_owner_consent_tenant_scope_and_private_profile_projection(self):
        response = self.f.client.get("/v1/cloud-profiles")
        self.assertEqual(response.status_code, 200)
        public = response.json()[0]
        self.assertNotIn("credential_prefix", public)
        self.assertNotIn("tenants", public)
        body = {
            "study": self.study["id"],
            "profile_id": self.p["id"],
            "data_approved": False,
            "compute_approved": True,
            "request_key": "no-consent",
        }
        self.assertEqual(
            self.f.client.post(
                f"/v1/projects/{self.f.project}/cloud-runs", json=body
            ).status_code,
            400,
        )
        self.create()
        self.assertEqual(
            self.f.client.get(
                f'/v1/projects/{self.f.project}/cloud-runs/{self.run["id"]}',
                headers={"Authorization": "Bearer b-secret"},
            ).status_code,
            404,
        )
        with self.assertRaises(PermissionError):
            self.cloud.available({**OWNER, "role": "viewer"})
        self.assertEqual(self.calls, [])

    def test_cold_backup_restore_keeps_unknown_creation_and_selected_input_receipt(
        self,
    ):
        from decision_workbench.platform.recovery import (
            backup_workspace,
            restore_workspace,
        )
        from decision_workbench.platform.store import Store
        from decision_workbench.platform.cloud import Cloud

        self.create()
        self.fail_create = True
        self.assertEqual(self.action("submit")["status"], "failed")
        state = self.cloud.get(OWNER, self.f.project, self.run["id"])
        backup = self.f.root / "backup"
        dest = self.f.root / "restored"
        backup_workspace(self.f.store.folder, backup)
        restore_workspace(backup, dest, file_sha(backup / "backup-manifest.json"))
        store = Store(dest)
        self.assertEqual(Cloud(store).get(OWNER, self.f.project, self.run["id"]), state)
        validate_history(store)

    def test_runpod_wrapper_frozen_invocation_packaging_and_failure_marker(self):
        from decision_workbench.training.runpod_runner import execute

        self.create()
        run = self.run_value()
        recorded = []

        def trainer(command, seconds):
            recorded.append((command, seconds))
            out = Path(command[command.index("--out") + 1])
            out.mkdir()
            (out / "manifest.json").write_text("{}")
            (out / "model.safetensors").write_bytes(b"fake-not-loaded")

        result = execute(
            self.f.root / "input.zip",
            run["archive_sha256"],
            run["request_sha256"],
            run["id"],
            source_fingerprint(),
            self.f.root / "output",
            self.p["max_artifact_bytes"],
            7200,
            root=self.f.root / "scratch",
            trainer=trainer,
        )
        validate_result(run, result)
        self.assertEqual(recorded[0][1], 7200)
        self.assertIn("decision_workbench.training.cloud_runner", recorded[0][0])
        self.assertEqual(
            file_sha(self.f.root / "output/model.tar.gz"), result["artifact_sha256"]
        )
        with self.assertRaises(FileExistsError):
            execute(
                self.f.root / "input.zip",
                run["archive_sha256"],
                run["request_sha256"],
                run["id"],
                source_fingerprint(),
                self.f.root / "output",
                self.p["max_artifact_bytes"],
                7200,
                root=self.f.root / "scratch",
                trainer=trainer,
            )

        def fail(*args):
            raise RuntimeError("private-message")

        with self.assertRaises(RuntimeError):
            execute(
                self.f.root / "input.zip",
                run["archive_sha256"],
                run["request_sha256"],
                run["id"],
                source_fingerprint(),
                self.f.root / "failed",
                self.p["max_artifact_bytes"],
                7200,
                root=self.f.root / "failed-scratch",
                trainer=fail,
            )
        data = (self.f.root / "failed/result.json").read_text()
        self.assertNotIn("private-message", data)
        self.assertEqual(json.loads(data)["status"], "failed")
