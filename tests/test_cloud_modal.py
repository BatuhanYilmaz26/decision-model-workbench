"""Modal public-SDK boundary fixtures, durable owner controls and cold restoration."""

import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

from decision_workbench.platform.cloud_modal import (
    ModalGateway,
    validate_profile,
    plan,
    tags,
    object_key,
    validate_result,
    artifact_location,
)
from decision_workbench.platform.cloud_aws import CloudUnavailable
from decision_workbench.platform.cloud import validate_history
from decision_workbench.platform.modal_transport import execute, run_process
from decision_workbench.training.cloud_packaging import pack_export
from decision_workbench.training.provenance import source_fingerprint
import test_remote_admission as fixtures

OWNER = fixtures.OWNER


def profile():
    return {
        "id": "modal-fixture",
        "provider": "modal",
        "tenants": ["A"],
        "credential_prefix": "DMT_CLOUD_MODAL",
        "account_id": "fixture-workspace",
        "region": "eu-west",
        "environment": "dmt-test",
        "app_name": "dmt-train",
        "app_id": "ap-fixture",
        "volume_name": "dmt-runs",
        "volume_id": "vo-fixture",
        "modal_image_id": "im-fixture",
        "image_digest": "ghcr.io/example/trainer@sha256:" + "a" * 64,
        "prefix": "studies",
        "gpu": "T4",
        "cpu": 2,
        "memory_mb": 8192,
        "max_runtime_seconds": 7200,
        "max_runs_day": 2,
        "max_inflight": 1,
        "max_artifact_bytes": 1024**2,
        "source_sha256": source_fingerprint(),
    }


class Missing(Exception):
    pass


class Backend:
    """Public SDK seam only: no account, authentication, container or GPU execution."""

    def __init__(self, p):
        self.p = p
        self.files, self.jobs, self.calls = {}, {}, []
        self.lost_create = self.lost_stop = False
        self.corrupt = False
        backend = self

        class Volume:
            object_id = p["volume_id"]

            def hydrate(self, client):
                return self

            def iterdir(self, path, recursive=False):
                backend.calls.append(("iterdir", path, recursive))
                if path not in backend.files:
                    raise Missing()
                yield SimpleNamespace(
                    path=path, type=1, size=len(backend.files[path]), mtime=1
                )

            def read_file(self, path):
                data = backend.files[path]
                if backend.corrupt and path.endswith("model.tar.gz"):
                    data = data[:-1] + bytes([data[-1] ^ 1])
                yield data[: len(data) // 2]
                yield data[len(data) // 2 :]

            def batch_upload(self, force=False):
                backend.calls.append(("batch_upload", force))

                class Batch:
                    def __enter__(self):
                        return self

                    def __exit__(self, *args):
                        return False

                    def put_file(self, source, target):
                        if target in backend.files:
                            raise ValueError("Existing")
                        backend.files[target] = Path(source).read_bytes()

                return Batch()

        class Job:
            def __init__(self, identifier, binding, code=None):
                self.object_id, self.binding, self.code = identifier, binding, code

            def get_tags(self):
                return self.binding

            def poll(self):
                return self.code

            def terminate(self, wait=False):
                backend.calls.append(("terminate", self.object_id, wait))
                self.code = 137
                if backend.lost_stop:
                    raise TimeoutError("Lost acknowledgement")

        class Sandbox:
            @staticmethod
            def from_name(app, name, **kw):
                backend.calls.append(("from_name", app, name, kw["environment_name"]))
                matches = [
                    v
                    for v in backend.jobs.values()
                    if v.binding["dmt_run"] == name[4:] and v.code is None
                ]
                if not matches:
                    raise Missing()
                return matches[0]

            @staticmethod
            def from_id(identifier, client):
                if identifier not in backend.jobs:
                    raise Missing()
                return backend.jobs[identifier]

            @staticmethod
            def create(*args, **kw):
                backend.calls.append(
                    (
                        "create",
                        list(args),
                        {
                            k: v
                            for k, v in kw.items()
                            if k not in ("client", "app", "image", "volumes")
                        },
                    )
                )
                job = Job("sb-FFFFFFFFFFFFFFFFFFFFFF", kw["tags"])
                backend.jobs[job.object_id] = job
                if backend.lost_create:
                    raise TimeoutError("Lost creation")
                return job

        self.Job = Job

        workspace = SimpleNamespace(name=p["account_id"], hydrate=lambda: None)
        app = SimpleNamespace(app_id=p["app_id"])
        image = SimpleNamespace(hydrate=lambda client: None)
        self.sdk = SimpleNamespace(
            Client=SimpleNamespace(from_credentials=lambda *creds: object()),
            Workspace=SimpleNamespace(from_context=lambda **kw: workspace),
            App=SimpleNamespace(lookup=lambda *args, **kw: app),
            Volume=SimpleNamespace(from_name=lambda *args, **kw: Volume()),
            Image=SimpleNamespace(from_id=lambda *args, **kw: image),
            Sandbox=Sandbox,
            exception=SimpleNamespace(NotFoundError=Missing),
            types=SimpleNamespace(FileEntryType=SimpleNamespace(FILE=1)),
        )

    def rpc(self, op, body):
        return execute(self.sdk, self.p, ["fixture-token", "fixture-secret"], op, body)


class ModalCloudTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.RemoteAdmissionTests("runTest")
        self.f.setUp()
        self.p = profile()
        self.file = self.f.root / "modal-profiles.json"
        self.file.write_text(
            json.dumps({"format": "domain-cloud-profiles-v1", "profiles": [self.p]}),
            encoding="utf-8",
        )
        self.env = patch.dict(os.environ, {"DMT_CLOUD_PROFILES_FILE": str(self.file)})
        self.env.start()
        self.f.config.update(
            {k: self.p[k] for k in ("provider", "region", "gpu", "image_digest")}
        )
        self.study = self.f.study()
        self.backend = Backend(self.p)
        self.f.worker.service.cloud.gateway_factory = lambda p: ModalGateway(
            p, self.backend.rpc
        )
        self.cloud = self.f.service.cloud

    def tearDown(self):
        self.env.stop()
        self.f.tearDown()

    def create(self):
        response = self.f.client.post(
            f"/v1/projects/{self.f.project}/cloud-runs",
            json={
                "study": self.study["id"],
                "profile_id": self.p["id"],
                "data_approved": True,
                "compute_approved": True,
                "request_key": "modal-approved-once",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.run = response.json()
        return self.run

    def value(self):
        entity = self.cloud.get(OWNER, self.f.project, self.run["id"])
        return {
            **entity["payload"],
            **{
                k: entity["state"].get(k)
                for k in (
                    "provider_job_id",
                    "input_receipt",
                    "training_result",
                    "termination_attempted",
                )
            },
        }

    def action(self, action):
        state = self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]
        response = self.f.client.post(
            f"/v1/projects/{self.f.project}/cloud-runs/{self.run['id']}/actions",
            json={
                "action": action,
                "revision": state["revision"],
                "request_key": "modal-action-" + uuid.uuid4().hex,
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        self.f.worker.once()
        return self.f.store.job(response.json()["id"], "A", self.f.project)

    def passed(self, job):
        self.assertEqual(job["status"], "succeeded", job["error"])

    def output(self, failed=False):
        run = self.value()
        marker = {
            "format": "domain-modal-result-v1",
            "run_id": run["id"],
            "archive_sha256": run["archive_sha256"],
            "request_sha256": run["request_sha256"],
            "source_sha256": self.p["source_sha256"],
            "status": "failed" if failed else "succeeded",
        }
        if not failed:
            folder, checksum, _ = self.f.export(self.study)
            packed = self.f.root / "modal.tar.gz"
            pack_export(folder, packed, self.p["max_artifact_bytes"])
            data = packed.read_bytes()
            self.backend.files[object_key(run, "output")] = data
            marker.update(
                artifact_sha256=hashlib.sha256(data).hexdigest(),
                artifact_bytes=len(data),
                manifest_sha256=checksum,
            )
        self.backend.files[object_key(run, "result")] = json.dumps(marker).encode()
        return marker

    def test_actual_api_worker_approval_upload_create_exit_and_restore(self):
        self.create()
        self.passed(self.action("submit"))
        command, kw = [x[1:] for x in self.backend.calls if x[0] == "create"][0]
        self.assertEqual(command, plan(self.value())["command"])
        self.assertEqual(kw["runtime"], "gvisor")
        self.assertTrue(kw["block_network"])
        self.assertEqual(
            kw["env"], {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
        )
        self.assertEqual(kw["cpu"], (2, 2))
        self.assertEqual(kw["secrets"], [])
        self.output()
        self.passed(self.action("observe"))
        self.assertEqual(
            self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]["phase"],
            "InProgress",
        )
        self.backend.jobs["sb-FFFFFFFFFFFFFFFFFFFFFF"].code = 0
        self.passed(self.action("observe"))
        self.assertEqual(
            self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]["phase"],
            "Completed",
        )
        validate_history(self.f.store)
        self.passed(self.action("retrieve"))
        state = self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]
        self.assertEqual(
            self.f.store.job(state["admission_job"], "A", self.f.project)["kind"],
            "remote-import",
        )
        self.assertEqual(self.f.store.list("A", self.f.project, "model"), [])

    def test_lost_creation_resolves_running_name_and_never_replays(self):
        self.create()
        self.backend.lost_create = True
        self.assertEqual(self.action("submit")["status"], "failed")
        self.passed(self.action("observe"))
        self.assertEqual(self.action("submit")["status"], "failed")
        self.assertEqual(sum(x[0] == "create" for x in self.backend.calls), 1)
        validate_history(self.f.store)

    def test_unknown_finished_creation_name_keeps_capacity(self):
        self.create()
        self.backend.lost_create = True
        self.action("submit")
        self.backend.jobs["sb-FFFFFFFFFFFFFFFFFFFFFF"].code = 0
        self.assertEqual(self.action("observe")["status"], "failed")
        state = self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]
        self.assertEqual(state["phase"], "unknown")
        self.assertIsNone(state["provider_job_id"])
        validate_history(self.f.store)

    def test_lost_stop_acknowledgement_poll_exit_resolves_without_repeat(self):
        self.create()
        self.passed(self.action("submit"))
        self.backend.lost_stop = True
        self.assertEqual(self.action("stop")["status"], "failed")
        self.passed(self.action("observe"))
        state = self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]
        self.assertEqual(state["phase"], "Stopped")
        self.assertTrue(state["termination_attempted"])
        self.assertFalse(state.get("termination_acknowledged", False))
        self.assertEqual(sum(x[0] == "terminate" for x in self.backend.calls), 1)
        validate_history(self.f.store)

    def test_lost_stop_running_refuses_second_termination(self):
        self.create()
        self.passed(self.action("submit"))
        self.backend.lost_stop = True
        self.action("stop")
        self.backend.jobs["sb-FFFFFFFFFFFFFFFFFFFFFF"].code = None
        self.assertEqual(self.action("stop")["status"], "failed")
        self.assertEqual(sum(x[0] == "terminate" for x in self.backend.calls), 1)

    def test_missing_success_marker_waits_and_failed_exit_is_terminal(self):
        self.create()
        self.passed(self.action("submit"))
        job = self.backend.jobs["sb-FFFFFFFFFFFFFFFFFFFFFF"]
        job.code = 0
        self.passed(self.action("observe"))
        self.assertEqual(
            self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]["phase"],
            "InProgress",
        )
        job.code = 124
        self.passed(self.action("observe"))
        self.assertEqual(
            self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]["phase"],
            "Failed",
        )
        validate_history(self.f.store)

    def test_wrong_tags_missing_known_id_and_changed_result_refused(self):
        self.create()
        self.passed(self.action("submit"))
        run = self.value()
        g = ModalGateway(self.p, self.backend.rpc)
        job = self.backend.jobs["sb-FFFFFFFFFFFFFFFFFFFFFF"]
        job.binding["dmt_plan"] = "0" * 64
        with self.assertRaises(CloudUnavailable):
            g.describe(run)
        job.binding = tags(run)
        self.backend.jobs = {}
        with self.assertRaises(Missing):
            g.describe(run)
        self.backend.jobs[job.object_id] = job
        original = self.output()
        self.passed(self.action("observe"))
        self.backend.files[object_key(run, "result")] = json.dumps(
            {**original, "manifest_sha256": "b" * 64}
        ).encode()
        self.assertEqual(self.action("observe")["status"], "failed")

    def test_corrupted_export_refused_and_created_partial_removed(self):
        self.create()
        self.passed(self.action("submit"))
        self.output()
        self.backend.jobs["sb-FFFFFFFFFFFFFFFFFFFFFF"].code = 0
        self.passed(self.action("observe"))
        self.backend.corrupt = True
        target = self.f.root / "download.tar.gz"
        with self.assertRaises(CloudUnavailable):
            ModalGateway(self.p, self.backend.rpc).download(
                self.value(),
                "modal-volume://"
                + self.p["volume_id"]
                + object_key(self.value(), "output"),
                target,
            )
        self.assertFalse(target.exists())

    def test_preexisting_download_preserved(self):
        self.create()
        self.passed(self.action("submit"))
        self.output()
        self.backend.jobs["sb-FFFFFFFFFFFFFFFFFFFFFF"].code = 0
        self.passed(self.action("observe"))
        target = self.f.root / "download.tar.gz"
        target.write_bytes(b"preserve")
        with self.assertRaises(FileExistsError):
            ModalGateway(self.p, self.backend.rpc).download(
                self.value(),
                "modal-volume://"
                + self.p["volume_id"]
                + object_key(self.value(), "output"),
                target,
            )
        self.assertEqual(target.read_bytes(), b"preserve")

    def test_existing_input_wrong_workspace_and_profile_resources_refused(self):
        self.create()
        run = self.value()
        self.backend.files[object_key(run, "input")] = b"existing"
        self.assertEqual(self.action("submit")["status"], "failed")
        self.assertFalse(any(x[0] == "create" for x in self.backend.calls))
        for field, value in (
            ("gpu", "H100:8"),
            ("image_digest", "trainer:latest"),
            ("region", "made-up"),
            ("max_runtime_seconds", True),
            ("cpu", 32),
        ):
            p = {**self.p, field: value}
            with self.assertRaises(ValueError):
                validate_profile(p)
        with self.assertRaises(ValueError):
            artifact_location(run, "https://evil.example/model")

    def test_marker_protocol_rejects_wrong_origin_and_unbounded_bytes(self):
        self.create()
        self.passed(self.action("submit"))
        marker = self.output()
        for field, value in (
            ("run_id", "0" * 32),
            ("source_sha256", "b" * 64),
            ("format", "domain-runpod-result-v1"),
            ("artifact_bytes", True),
            ("artifact_bytes", self.p["max_artifact_bytes"] + 1),
        ):
            with self.assertRaises(CloudUnavailable):
                validate_result(self.value(), {**marker, field: value})

    def test_installed_sdk_serializes_exact_gpu_backend_and_bounds(self):
        import sys

        self.create()
        result = run_process(
            self.p,
            ["fixture", "secret"],
            "create",
            {"run": self.value()},
            command=[
                sys.executable,
                str(Path(__file__).with_name("modal_sdk_fixture.py")),
            ],
        )
        self.assertEqual(len(result["requests"]), 1)
        request = result["requests"][0]
        self.assertEqual(request["app_id"], self.p["app_id"])
        definition = request["definition"]
        self.assertEqual(definition["entrypoint_args"], plan(self.value())["command"])
        self.assertEqual(definition["image_id"], self.p["modal_image_id"])
        self.assertEqual(
            definition["resources"],
            {
                "memory_mb": 8192,
                "milli_cpu": 2000,
                "gpu_config": {"count": 1, "gpu_type": "T4"},
                "memory_mb_max": 8192,
                "milli_cpu_max": 2000,
            },
        )
        self.assertEqual(definition["timeout_secs"], 7200)
        self.assertEqual(definition["runtime"], "gvisor")
        self.assertEqual(
            definition["network_access"], {"network_access_type": "BLOCKED"}
        )
        self.assertEqual(definition["open_ports"], {})
        self.assertEqual(definition["scheduler_placement"], {"regions": ["eu-west"]})
        self.assertEqual(
            definition["volume_mounts"],
            [
                {
                    "volume_id": "vo-fixture",
                    "mount_path": "/runs",
                    "allow_background_commits": True,
                }
            ],
        )
        self.assertEqual(result["receipt"]["tags"], tags(self.value()))

    def test_sdk_workspace_binding_and_missing_selected_credentials(self):
        self.create()
        self.backend.sdk.Workspace.from_context = lambda **kw: SimpleNamespace(
            name="other-workspace", hydrate=lambda: None
        )
        with self.assertRaises(CloudUnavailable):
            ModalGateway(self.p, self.backend.rpc).identity()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(CloudUnavailable):
                ModalGateway.from_operator(self.p)

    def test_failed_result_and_exit_code_restore_requires_same_evidence(self):
        self.create()
        self.passed(self.action("submit"))
        self.output(failed=True)
        self.backend.jobs["sb-FFFFFFFFFFFFFFFFFFFFFF"].code = 1
        self.passed(self.action("observe"))
        validate_history(self.f.store)
        state = self.cloud.get(OWNER, self.f.project, self.run["id"])["state"]
        self.assertEqual(state["phase"], "Failed")
        with self.f.store.transaction() as db:
            row = db.execute(
                "SELECT * FROM entities WHERE kind='cloud-event' AND json_extract(payload,'$.run_id')=? AND json_extract(payload,'$.revision')=?",
                (self.run["id"], state["revision"]),
            ).fetchone()
            body = json.loads(row["payload"])
            body["exit_code"] = None
            db.execute(
                "UPDATE entities SET payload=? WHERE id=?",
                (json.dumps(body, sort_keys=True, separators=(",", ":")), row["id"]),
            )
        with self.assertRaises(ValueError):
            validate_history(self.f.store)

    def test_real_private_process_timeout_kills_and_waits_local_transport(self):
        import sys

        processes = []
        with self.assertRaises(CloudUnavailable):
            run_process(
                self.p,
                ["private-id", "private-secret"],
                "identity",
                {},
                command=[sys.executable, "-c", "import time; time.sleep(30)"],
                timeout=0.5,
                observe=processes.append,
            )
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())

    def test_private_process_environment_excludes_ambient_credentials_and_config(self):
        import sys

        code = "import os,json,sys; keys=list(os.environ); print(json.dumps({'result':{'config':open(os.environ['MODAL_CONFIG_PATH']).read(),'server':os.environ['MODAL_SERVER_URL'],'sandbox':os.environ['MODAL_SANDBOX_V2'],'secret_present':any(k in keys for k in ['AWS_SECRET_ACCESS_KEY','MODAL_TOKEN_SECRET','DMT_CLOUD_MODAL_TOKEN_SECRET'])}}))"
        with patch.dict(
            os.environ,
            {
                "AWS_SECRET_ACCESS_KEY": "private",
                "MODAL_TOKEN_SECRET": "ambient",
                "DMT_CLOUD_MODAL_TOKEN_SECRET": "private",
                "MODAL_SERVER_URL": "https://evil.example",
            },
        ):
            result = run_process(
                self.p,
                ["fixture", "secret"],
                "identity",
                {},
                command=[sys.executable, "-c", code],
            )
        self.assertEqual(
            result,
            {
                "config": "",
                "server": "https://api.modal.com",
                "sandbox": "false",
                "secret_present": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
