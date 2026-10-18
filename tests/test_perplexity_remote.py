"""Native cloud studies and independent admission with fictional model tensors."""

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from decision_workbench.data import dumps
from decision_workbench.platform.remote import (
    study_archive,
    study_entity,
    stage_import,
    validate_export,
)
from decision_workbench.platform.cloud_archive import extract_model
from decision_workbench.platform.cloud_providers import validate_profile
from decision_workbench.platform.cloud_training_profile import (
    require_perplexity_resources,
)
from decision_workbench.training.cloud_runner import prepare_archive, trainer_command
from decision_workbench.training.cloud_packaging import pack_export
from decision_workbench.training.contracts import load_splits
from decision_workbench.training.native_remote import check_inputs, export_assets
from decision_workbench.training.native_remote_runner import execute
from decision_workbench.training.artifacts import file_sha
from decision_workbench.training.admission_runner import qualify
from decision_workbench.training import perplexity_profile
from decision_workbench.training.provenance import source_fingerprint
from perplexity_native_fixture import FakeEngine
import test_perplexity_platform as platform_fixtures

OWNER = platform_fixtures.OWNER

ROOT = Path(__file__).resolve().parents[1]


class NativePerplexityRemoteTests(unittest.TestCase):
    def setUp(self):
        self.f = platform_fixtures.NativePerplexityPlatformTests("runTest")
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.f.stack.enter_context(
            patch.object(perplexity_profile, "ASSETS", self.f.native.assets)
        )
        self.f.stack.enter_context(
            patch.dict(os.environ, {"DMT_CLOUD_IMAGE_BACKEND": "perplexity-native"})
        )

    def profile(self, provider):
        value = json.loads(
            (ROOT / "examples/cloud" / (provider + "-profiles.json")).read_text()
        )["profiles"][0]
        value.update(
            id="native-" + provider,
            tenants=["A"],
            training_backends=["perplexity-native"],
            source_sha256=source_fingerprint(),
            max_artifact_bytes=64 * 1024**3,
        )
        if provider == "aws":
            value.update(gpu="ml.p5e.48xlarge", volume_gb=300)
        elif provider == "azure":
            value.update(gpu="Standard_ND96isr_H200_v5")
        elif provider == "gcp":
            value.update(
                gpu="NVIDIA_H200_141GB", machine_type="a3-ultragpu-8g", volume_gb=300
            )
        elif provider == "modal":
            value.update(gpu="H200", memory_mb=524288)
        else:
            value.update(
                gpu="NVIDIA H200", min_memory_gb=512, volume_gb=300, disk_gb=100
            )
        validate_profile(value)
        require_perplexity_resources(value)
        return value

    def inputs(self, provider):
        profile = self.profile(provider)
        config = {
            **self.f.config,
            **{k: profile[k] for k in ("provider", "region", "gpu", "image_digest")},
        }
        response = self.f.client.post(
            self.f.prefix + "/jobs",
            json={
                "kind": "remote-study",
                "config": config,
                "request_key": "native-remote-" + provider,
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        self.f.worker.once()
        done = self.f.store.job(response.json()["id"], "A", self.f.project)
        self.assertEqual(done["status"], "succeeded", done["error"])
        study = study_entity(self.f.store, "A", self.f.project, done["id"])["payload"]
        archive = study_archive(self.f.store, OWNER, self.f.project, done["id"])
        work = self.f.root / (provider + "-work")
        request = prepare_archive(
            archive, work, study["archive_sha256"], study["request_sha256"]
        )
        return study, request, work, profile

    def test_all_five_native_studies_preserve_settings_roles_and_image_identity(self):
        profiles = []
        for provider in ("aws", "azure", "gcp", "modal", "runpod"):
            with self.subTest(provider=provider):
                study, request, work, profile = self.inputs(provider)
                profiles.append(profile)
                rows, hashes = load_splits(work / "data")
                self.assertEqual(
                    check_inputs(request, rows, hashes, self.f.questions),
                    request["configuration"]["perplexity_study"],
                )
                command = trainer_command(request, work, work / "result")
                self.assertIn(
                    "decision_workbench.training.native_remote_runner", command
                )
                self.assertNotIn("--download", command)
                for change in (
                    {"download": False},
                    {"device": "cpu"},
                    {"checkpoint": "untrusted"},
                    {"perplexity_study": {}},
                ):
                    altered = copy.deepcopy(request)
                    altered["configuration"].update(change)
                    with self.assertRaises(ValueError):
                        check_inputs(altered, rows, hashes, self.f.questions)
        path = self.f.root / "cloud-profiles.json"
        path.write_text(
            json.dumps({"format": "domain-cloud-profiles-v1", "profiles": profiles})
        )
        with patch.dict(os.environ, {"DMT_CLOUD_PROFILES_FILE": str(path)}):
            for profile in profiles:
                studies = self.f.store.list("A", self.f.project, "remote-study")
                study = next(
                    s
                    for s in studies
                    if s["payload"]["request"]["configuration"]["provider"]
                    == profile["provider"]
                )
                run = self.f.service.cloud.create(
                    OWNER,
                    self.f.project,
                    study["id"],
                    profile["id"],
                    True,
                    True,
                    "approve-" + profile["provider"],
                )
                self.assertEqual(
                    run["payload"]["profile"]["training_backends"],
                    ["perplexity-native"],
                )

    def test_real_native_runner_pack_extract_admission_and_release_preserve_remote_binding(
        self,
    ):
        study, request, work, _ = self.inputs("aws")
        output = self.f.root / "native-provider-output"
        from decision_workbench.training import perplexity_runner

        original = perplexity_runner.train_perplexity
        seen = []

        def train(args):
            seen.append(Path.cwd())
            return original(args)

        previous = Path.cwd()
        os.chdir(work)
        try:
            with patch.object(perplexity_runner, "train_perplexity", side_effect=train):
                result = execute(work / "request.json", work / "data", output)
            self.assertEqual(Path.cwd(), work)
        finally:
            os.chdir(previous)
        self.assertEqual(seen, [ROOT])
        checked = {
            **request,
            "questions": self.f.questions,
            "request_sha256": study["request_sha256"],
        }
        validate_export(output, result["manifest_sha256"], checked)
        archive = self.f.root / "model.tar.gz"
        pack_export(output, archive, 4 * 1024**2)
        extracted = self.f.root / "extracted"
        extract_model(archive, extracted, 4 * 1024**2, backend="perplexity-native")
        receipt = {
            **{
                k: request["configuration"][k]
                for k in ("provider", "region", "gpu", "image_digest")
            },
            "provider_job_id": "fictional-approved-job",
            "status": "succeeded",
            "manifest_sha256": result["manifest_sha256"],
        }
        job = stage_import(
            self.f.service,
            OWNER,
            self.f.project,
            study["job_id"],
            extracted,
            result["manifest_sha256"],
            receipt,
            "native-returned-study",
        )

        def child(argv, **kwargs):
            if (
                len(argv) > 2
                and argv[2] == "decision_workbench.training.admission_runner"
            ):
                args = {argv[i]: argv[i + 1] for i in range(3, len(argv), 2)}
                qualify(
                    args["--export"],
                    args["--data"],
                    args["--manifest-sha256"],
                    json.loads(Path(args["--request"]).read_text()),
                    engine_factory=lambda backend, checkpoint: FakeEngine(checkpoint),
                )
                from types import SimpleNamespace

                return SimpleNamespace(poll=lambda: 0, returncode=0)
            return self.f.child(argv, **kwargs)

        with patch(
            "decision_workbench.platform.remote_admission.subprocess.Popen",
            side_effect=child,
        ):
            self.f.worker.once()
            done = self.f.store.job(job["id"], "A", self.f.project)
            self.assertEqual(done["status"], "succeeded", done["error"])
            model = self.f.store.get(
                done["result"]["model"], "A", self.f.project, "model"
            )
            self.assertEqual(
                model["payload"]["report"]["remote_study_sha256"],
                study["request_sha256"],
            )
            folder = self.f.service.checked_export(model["payload"], self.f.questions)
            self.assertTrue((folder / "training_report.json").is_file())
            response = self.f.client.post(
                self.f.prefix + "/releases",
                json={
                    "model_id": model["id"],
                    "note": "fictional native remote qualification",
                    "expected_revision": 0,
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            prediction = self.f.client.post(
                self.f.prefix + "/decisions", json={"state": {"ready": True}}
            )
            self.assertEqual(prediction.status_code, 200, prediction.text)
            from decision_workbench.platform.recovery import (
                backup_workspace,
                restore_workspace,
            )
            from decision_workbench.platform.api import create_app
            from fastapi.testclient import TestClient
            from test_platform import keys

            backup = backup_workspace(
                self.f.store.folder, self.f.root / "remote-backup"
            )
            restored = restore_workspace(
                backup["destination"],
                self.f.root / "remote-restored",
                backup["manifest_sha256"],
            )
            recovered = create_app(restored["destination"], keys())
            try:
                with TestClient(recovered) as client:
                    response = client.post(
                        self.f.prefix + "/decisions",
                        headers={"Authorization": "Bearer a-secret"},
                        json={"state": {"ready": False}},
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["model_id"], model["id"])
                    self.assertFalse(response.json()["answers"]["ready"]["answer"])
            finally:
                recovered.state.service.runtime.close()

    def test_provider_request_resources_match_native_h200_and_ram_declarations(self):
        from decision_workbench.platform import cloud_gcp, cloud_modal, cloud_runpod

        for provider in ("aws", "azure", "gcp", "modal", "runpod"):
            profile = self.profile(provider)
            bad = copy.deepcopy(profile)
            bad["gpu"] = "undersized"
            with self.assertRaises(ValueError):
                require_perplexity_resources(bad)
        base = {"id": "a" * 32, "archive_sha256": "b" * 64, "request_sha256": "c" * 64}
        gcp = cloud_gcp.plan(
            {
                **base,
                "profile": self.profile("gcp"),
                "input_receipt": {"generation": "1"},
            }
        )
        worker = gcp["jobSpec"]["workerPoolSpecs"][0]
        self.assertEqual(worker["machineSpec"]["acceleratorCount"], 8)
        self.assertEqual(worker["diskSpec"]["bootDiskType"], "hyperdisk-balanced")
        modal = cloud_modal.plan({**base, "profile": self.profile("modal")})
        self.assertIn(524288, modal["memory"])
        runpod = cloud_runpod.plan({**base, "profile": self.profile("runpod")})
        self.assertEqual(runpod["gpu"]["minRamPerGpu"], 512)
        bad = self.profile("runpod")
        bad["min_memory_gb"] = 64
        with self.assertRaises(ValueError):
            require_perplexity_resources(bad)

    def test_undersized_or_undeclared_images_are_refused_before_cloud_run_creation(
        self,
    ):
        study, _, _, profile = self.inputs("aws")
        path = self.f.root / "cloud-profile-limits.json"
        for changes in (
            {"training_backends": ["unsloth"]},
            {"max_artifact_bytes": 1024**2},
            {"gpu": "ml.g5.xlarge"},
            {"volume_gb": 100},
        ):
            value = {**profile, **changes}
            path.write_text(
                json.dumps({"format": "domain-cloud-profiles-v1", "profiles": [value]})
            )
            with patch.dict(os.environ, {"DMT_CLOUD_PROFILES_FILE": str(path)}):
                with self.assertRaises(ValueError):
                    self.f.service.cloud.create(
                        OWNER,
                        self.f.project,
                        study["job_id"],
                        value["id"],
                        True,
                        True,
                        "refuse-native-image",
                    )
            self.assertFalse(self.f.store.list("A", self.f.project, "cloud-run"))

    def test_native_image_preparation_uses_only_the_explicit_checkpoint_helper(self):
        from decision_workbench.training.native_image import prepare
        from decision_workbench.training.perplexity_runtime import PACKAGES

        with (
            patch(
                "decision_workbench.training.perplexity_checkpoint.download_checkpoint",
                return_value=self.f.native.base,
            ) as download,
            patch(
                "decision_workbench.training.perplexity_runtime.runtime_identity",
                return_value=PACKAGES,
            ),
        ):
            value = prepare("perplexity-native", "fictional-approved-cache")
        download.assert_called_once_with("fictional-approved-cache")
        self.assertEqual(value["cached_checkpoint"], str(self.f.native.base))
        self.assertEqual(value["source_sha256"], source_fingerprint())

    def test_native_aws_request_validates_against_the_installed_official_service_model(
        self,
    ):
        from botocore.session import get_session
        from botocore.validate import validate_parameters
        from decision_workbench.platform.cloud_aws import plan

        run = {
            "id": "a" * 32,
            "archive_sha256": "b" * 64,
            "request_sha256": "c" * 64,
            "profile": self.profile("aws"),
        }
        body = plan(run)
        shape = (
            get_session()
            .get_service_model("sagemaker")
            .operation_model("CreateTrainingJob")
            .input_shape
        )
        validate_parameters(body, shape)
        self.assertEqual(body["ResourceConfig"]["InstanceType"], "ml.p5e.48xlarge")

    def test_browser_prepares_all_five_studies_without_a_local_model_environment(self):
        from streamlit.testing.v1 import AppTest
        from http_server_fixture import LiveServer
        from test_platform import keys

        cpu = {
            "system": "fixture",
            "python": "3.13",
            "cuda": False,
            "mps": False,
            "packages": {},
            "gpus": [],
            "error": None,
        }
        with LiveServer(self.f.store.folder, keys()) as live:
            app = AppTest.from_file(str(ROOT / "app.py"))
            for key, value in {
                "credential": "a-secret",
                "api_url": live.url,
                "principal": OWNER,
                "connection_mode": "shared",
                "runtime-capabilities": {
                    "cpu_baseline": True,
                    "teacher_configured": False,
                    "runtimes": {"training": cpu, "serving": cpu},
                    "model_profiles": {},
                },
            }.items():
                app.session_state[key] = value
            app.run(timeout=30)
            self.assertFalse(app.exception)
            self.assertFalse(app.error, [w.value for w in app.error])
            next(w for w in app.selectbox if w.label == "Training backend").select(
                "perplexity-native"
            ).run(timeout=30)
            next(w for w in app.radio if w.label == "Training location").set_value(
                "Cloud GPU"
            ).run(timeout=30)
            next(
                w
                for w in app.checkbox
                if w.label
                == "The selected provider is approved to receive this study's domain data"
            ).check().run(timeout=30)
            for provider in ("aws", "azure", "gcp", "modal", "runpod"):
                profile = self.profile(provider)
                next(
                    w for w in app.selectbox if w.label == "Cloud training provider"
                ).select(provider)
                for label, value in (
                    ("Approved provider region or environment", profile["region"]),
                    ("Approved GPU or instance identity", profile["gpu"]),
                    ("Qualified training image digest", profile["image_digest"]),
                ):
                    next(w for w in app.text_input if w.label == label).set_value(value)
                button = next(
                    w for w in app.button if w.label == "Prepare remote training study"
                )
                self.assertFalse(button.disabled)
                self.assertTrue(
                    next(w for w in app.button if w.label == "Queue training").disabled
                )
                button.click().run(timeout=30)
                self.assertFalse(app.exception)
                self.assertFalse(app.error, [w.value for w in app.error])
                job = next(
                    j
                    for j in self.f.store.jobs("A", self.f.project)
                    if j["kind"] == "remote-study"
                    and j["config"]["provider"] == provider
                )
                self.f.worker.once()
                self.assertEqual(
                    self.f.store.job(job["id"], "A", self.f.project)["status"],
                    "succeeded",
                )
