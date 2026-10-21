"""Existing hosted/cloud contracts on shared metadata and separate node caches.

Provider traffic uses the existing fictional HTTP/SDK fixtures. No paid provider,
model checkpoint or cloud compute is contacted. Database cases need the explicit
disposable DMT_TEST_POSTGRES_URL used by test_postgres.py.
"""

from concurrent.futures import ThreadPoolExecutor
import json
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from decision_workbench.platform.api import create_app
from decision_workbench.platform.artifact_storage import (
    ArtifactRepository,
    DirectoryObjects,
)
from decision_workbench.platform.hosted import Hosted
from decision_workbench.platform.remote import stage_import
from decision_workbench.platform.service import Service
from decision_workbench.platform.store import Store, Conflict
from decision_workbench.platform.worker import Worker
from decision_workbench.training.admission_runner import qualify
import test_postgres as pg
import test_platform as data
import test_remote_admission as remote
import test_hosted as hosted


class EnterpriseFixture(pg.DatabaseFixture, unittest.TestCase):
    def setUp(self):
        self.database_setup()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.repository = ArtifactRepository(DirectoryObjects(self.home / "objects"))

    def node(self, home):
        return Store(
            home,
            database_url=pg.URL,
            database_schema=self.schema,
            artifact_store=self.repository,
        )

    def app(self, *args, **kwargs):
        return create_app(
            *args,
            **kwargs,
            database_url=pg.URL,
            database_schema=self.schema,
            artifact_store=self.repository
        )

    def remote_fixture(self):
        binding = patch.object(remote, "create_app", side_effect=self.app)
        binding.start()
        self.addCleanup(binding.stop)
        fixture = remote.RemoteAdmissionTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        return fixture


@unittest.skipUnless(pg.URL, "Set DMT_TEST_POSTGRES_URL for enterprise flow contracts")
class SharedIntakeTests(EnterpriseFixture):
    def setUp(self):
        super().setUp()
        self.f = self.remote_fixture()
        self.study = self.f.study()
        self.export, self.sha, self.receipt = self.f.export(self.study)

    def intake(self, service, key="shared-intake-001", **kwargs):
        return stage_import(
            service,
            remote.OWNER,
            self.f.project,
            self.study["id"],
            self.export,
            self.sha,
            self.receipt,
            key,
            **kwargs
        )

    def test_input_hydrates_on_a_different_qualifying_worker(self):
        queued = self.intake(self.f.service)
        node = self.node(self.home / "qualifier")
        worker = Worker(node)
        self.addCleanup(worker.service.runtime.close)
        incoming = node.folder / "incoming" / queued["config"]["incoming_id"]
        self.assertFalse(incoming.exists())
        request = {
            **self.study["payload"]["request"],
            "request_sha256": self.study["payload"]["request_sha256"],
        }

        class Qualification:
            def __init__(self, argv, **kwargs):
                def arg(name):
                    return argv[argv.index(name) + 1]

                qualify(
                    arg("--export"),
                    arg("--data"),
                    arg("--manifest-sha256"),
                    request,
                    engine_factory=lambda *_: remote.FixtureEngine(),
                )
                self.returncode = 0

            def poll(self):
                return self.returncode

        with patch(
            "decision_workbench.platform.remote_admission.subprocess.Popen",
            Qualification,
        ):
            self.assertTrue(worker.once())
        result = node.job(queued["id"], "A", self.f.project)
        self.assertEqual(result["status"], "succeeded", result["error"])
        self.assertTrue(incoming.is_dir())
        model = node.get(result["result"]["model"], "A", self.f.project, "model")[
            "payload"
        ]
        self.f.service.checked_export(model, data.QUESTIONS)
        self.assertIsNone(self.f.store.active("A", self.f.project)["model_id"])

    def test_same_key_on_two_nodes_returns_one_job_and_one_input_identity(self):
        services = [Service(self.node(self.home / ("api-" + str(i)))) for i in range(2)]
        for service in services:
            self.addCleanup(service.runtime.close)
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = list(pool.map(self.intake, services))
        self.assertEqual(jobs[0]["id"], jobs[1]["id"])
        self.assertEqual(jobs[0]["config"], jobs[1]["config"])
        self.assertIn("incoming_reference", jobs[0]["config"])
        imports = [
            j
            for j in self.f.store.jobs("A", self.f.project)
            if j["kind"] == "remote-import"
        ]
        self.assertEqual(len(imports), 1)

    def test_cancelled_origin_cannot_queue_admission_after_upload(self):
        origin = self.f.store.submit(
            "A", self.f.project, "owner", "cloud-control", {}, "origin-control-001"
        )
        claimed = self.f.store.claim()
        self.assertEqual(claimed["id"], origin["id"])
        original = self.repository.objects.put_file

        def cancel(key, *args, **kwargs):
            result = original(key, *args, **kwargs)
            if "/manifests/" in key:
                self.f.store.cancel(origin["id"], "A", self.f.project, "owner")
            return result

        with patch.object(self.repository.objects, "put_file", side_effect=cancel):
            with self.assertRaises(Conflict):
                self.intake(self.f.service, origin_job=claimed)
        self.assertFalse(
            any(
                j["kind"] == "remote-import"
                for j in self.f.store.jobs("A", self.f.project)
            )
        )

    def test_uncertain_submission_preserves_input_for_same_key_recovery(self):
        original = self.f.store.submit

        def committed(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("Fictional lost response after commit")

        with patch.object(self.f.store, "submit", side_effect=committed):
            with self.assertRaises(OSError):
                self.intake(self.f.service)
        queued = self.intake(self.f.service)
        self.assertTrue(
            (
                self.f.store.folder / "incoming" / queued["config"]["incoming_id"]
            ).is_dir()
        )
        other = self.node(self.home / "recovered-node")
        path = other.incoming_path(
            queued["config"], self.study["payload"]["request"]["schema_sha256"]
        )
        self.assertTrue((path / "manifest.json").is_file())

    def test_cancelled_download_does_not_publish_a_partial_input_cache(self):
        queued = self.intake(self.f.service)
        node = self.node(self.home / "cancelled-qualifier")
        job = node.claim()
        original = self.repository.objects.open

        def cancelled_blob(key):
            stream = original(key)
            if "/blobs/" in key:
                node.cancel(job["id"], "A", self.f.project, "owner")
            return stream

        with patch.object(self.repository.objects, "open", side_effect=cancelled_blob):
            with self.assertRaises(Conflict):
                node.incoming_path(
                    queued["config"],
                    self.study["payload"]["request"]["schema_sha256"],
                    check=lambda: node.heartbeat(job),
                )
        parent = node.folder / "incoming"
        self.assertFalse((parent / queued["config"]["incoming_id"]).exists())
        self.assertEqual(list(parent.glob(".dmt-download-*")), [])

    def test_low_disk_space_refuses_independent_qualification_before_process_start(
        self,
    ):
        queued = self.intake(self.f.service)
        node = self.node(self.home / "low-space-qualifier")
        node.incoming_path(
            queued["config"], self.study["payload"]["request"]["schema_sha256"]
        )
        worker = Worker(node)
        self.addCleanup(worker.service.runtime.close)
        from types import SimpleNamespace

        with patch(
            "decision_workbench.platform.remote_admission.shutil.disk_usage",
            return_value=SimpleNamespace(free=0),
        ), patch(
            "decision_workbench.platform.remote_admission.subprocess.Popen"
        ) as process:
            worker.once()
        process.assert_not_called()
        result = node.job(queued["id"], "A", self.f.project)
        self.assertEqual(result["status"], "failed")
        self.assertIn("free disk space", result["error"])
        self.assertEqual(node.list("A", self.f.project, "model"), [])

    def test_operator_cli_selects_shared_database_schema_and_repository(self):
        from decision_workbench.platform.cli import main

        keys = self.home / "operator-keys.json"
        # Use the normal authenticated CLI and argument parser; only replace the
        # object transport with the same local conformance repository as the nodes.
        keys.write_text(json.dumps(data.keys()), encoding="utf-8")
        receipt = self.home / "receipt.json"
        receipt.write_text(json.dumps(self.receipt), encoding="utf-8")
        argv = [
            "domain-platform",
            "import-remote",
            "--home",
            str(self.home / "operator"),
            "--keys-file",
            str(keys),
            "--database-schema",
            self.schema,
            "--project",
            self.f.project,
            "--study",
            self.study["id"],
            "--export",
            str(self.export),
            "--manifest-sha256",
            self.sha,
            "--receipt",
            str(receipt),
            "--request-key",
            "operator-shared-001",
        ]
        with patch.dict(
            os.environ, {"DMT_DATABASE_URL": pg.URL, "DMT_OPERATOR_TOKEN": "a-secret"}
        ), patch("sys.argv", argv), patch(
            "decision_workbench.platform.artifact_s3.repository_from_environment",
            return_value=self.repository,
        ), patch(
            "sys.stdout", new_callable=io.StringIO
        ) as output:
            main()
        value = json.loads(output.getvalue())
        queued = self.f.store.job(value["job_id"], "A", self.f.project)
        self.assertEqual(queued["status"], "queued")
        self.assertIn("incoming_reference", queued["config"])
        self.assertFalse((self.home / "operator" / "workspace.sqlite3").exists())


@unittest.skipUnless(pg.URL, "Set DMT_TEST_POSTGRES_URL for shared hosted contracts")
class SharedHostedTests(EnterpriseFixture):
    def setUp(self):
        super().setUp()
        for target, name, replacement in (
            (hosted, "Store", self.node),
            (hosted, "create_app", self.app),
        ):
            binding = patch.object(target, name, side_effect=replacement)
            binding.start()
            self.addCleanup(binding.stop)
        self.f = hosted.HostedTests("runTest")
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def test_all_five_hosted_wire_contracts_on_postgres(self):
        self.f.test_all_five_provider_http_paths_and_three_primitives()

    def test_evaluated_policy_serves_from_an_independent_cache(self):
        model = self.f.study()
        self.f.release(model)
        store = self.node(self.home / "hosted-api")
        service = Service(
            store,
            hosted=Hosted(store, hosted.FixtureTransport(self.f.server.server_port)),
        )
        self.addCleanup(service.runtime.close)
        response = service.predict(
            hosted.OWNER,
            self.f.project,
            "invoice refund requested urgent",
            0.8,
            "other-node-001",
        )
        self.assertEqual(response["answers"]["team"]["answer"], "billing")
        payload = store.get(model, "A", self.f.project, "model")["payload"]
        self.assertTrue(
            (
                store.folder / "artifacts" / payload["artifact_id"] / "model.json"
            ).exists()
        )

    def test_shared_call_quota_remains_atomic(self):
        self.f.test_atomic_quota_and_concurrency_across_store_instances()


@unittest.skipUnless(
    pg.URL, "Set DMT_TEST_POSTGRES_URL for five-provider shared contracts"
)
class SharedCloudTests(EnterpriseFixture):
    def contract(self, module, cls, method):
        binding = patch.object(remote, "create_app", side_effect=self.app)
        binding.start()
        self.addCleanup(binding.stop)
        case = getattr(module, cls)("runTest")
        case.setUp()
        self.addCleanup(case.tearDown)
        original = case.f.worker
        node = self.node(self.home / "retrieving-worker")
        worker = Worker(node)
        worker.service.cloud.gateway_factory = original.service.cloud.gateway_factory
        self.addCleanup(worker.service.runtime.close)
        original.service.runtime.close()
        case.f.worker = worker
        getattr(case, method)()
        jobs = [
            j
            for j in case.f.store.jobs("A", case.f.project)
            if j["kind"] == "remote-import"
        ]
        self.assertEqual(len(jobs), 1)
        reference = jobs[0]["config"].get("incoming_reference")
        self.assertIsInstance(reference, dict)
        other = self.node(self.home / "admitting-worker")
        request = case.study["payload"]["request"]
        path = other.incoming_path(jobs[0]["config"], request["schema_sha256"])
        self.assertTrue((path / "manifest.json").is_file())

    def test_aws_retrieval_to_another_admission_node(self):
        import test_cloud as module

        self.contract(
            module,
            "CloudTests",
            "test_bounded_download_safe_extract_and_actual_admission_queue",
        )

    def test_azure_retrieval_to_another_admission_node(self):
        import test_cloud_azure as module

        self.contract(
            module,
            "AzureCloudTests",
            "test_completed_bounded_blob_retrieval_queues_independent_admission",
        )

    def test_gcp_retrieval_to_another_admission_node(self):
        import test_cloud_gcp as module

        self.contract(
            module,
            "GcpCloudTests",
            "test_completed_download_and_existing_independent_admission",
        )

    def test_modal_retrieval_to_another_admission_node(self):
        import test_cloud_modal as module

        self.contract(
            module,
            "ModalCloudTests",
            "test_actual_api_worker_approval_upload_create_exit_and_restore",
        )

    def test_runpod_retrieval_to_another_admission_node(self):
        import test_cloud_runpod as module

        self.contract(
            module,
            "RunPodCloudTests",
            "test_completed_retrieval_queues_inactive_independent_admission",
        )


if __name__ == "__main__":
    unittest.main()
