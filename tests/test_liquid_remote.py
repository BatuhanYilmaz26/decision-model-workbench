"""Frozen Liquid cloud transport and image capabilities; no paid provider calls."""

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from decision_workbench.data import dumps
from decision_workbench.platform.remote import study_archive, study_entity
from decision_workbench.platform.cloud_training_profile import training_backends
from decision_workbench.training.cloud_runner import prepare_archive, trainer_command
from decision_workbench.training.contracts import load_splits
from decision_workbench.training.liquid_remote import check_inputs, BACKENDS
from decision_workbench.training import liquid_local, liquid_omni_local
import test_remote_admission as fixtures


class LiquidRemoteTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.RemoteAdmissionTests("runTest")
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def study(self, backend="liquid-omni", provider="modal"):
        local = liquid_omni_local if backend == "liquid-omni" else liquid_local
        config = {
            "snapshot": self.f.snapshot,
            **local.PROFILE,
            "backend": backend,
            "device": "cuda",
            "max_length": 256,
            "max_steps": 1,
            **{k: self.f.config[k] for k in ("region", "gpu", "image_digest")},
            "provider": provider,
        }
        job = self.f.service.submit(
            fixtures.OWNER,
            self.f.project,
            "remote-study",
            config,
            "liquid-" + backend + "-" + provider,
        )
        self.f.worker.once()
        done = self.f.store.job(job["id"], "A", self.f.project)
        self.assertEqual(done["status"], "succeeded", done["error"])
        return study_entity(self.f.store, "A", self.f.project, job["id"])["payload"]

    def inputs(self, study, suffix):
        archive = study_archive(
            self.f.store, fixtures.OWNER, self.f.project, study["job_id"]
        )
        path = self.f.root / (suffix + ".zip")
        path.write_bytes(archive.read_bytes())
        work = self.f.root / suffix
        request = prepare_archive(
            path, work, study["archive_sha256"], study["request_sha256"]
        )
        return request, work

    def test_three_native_profiles_prepare_unchanged_archives_for_all_five_providers(
        self,
    ):
        for backend in BACKENDS:
            for provider in ("aws", "azure", "gcp", "modal", "runpod"):
                with self.subTest(backend=backend, provider=provider):
                    study = self.study(backend, provider)
                    request, work = self.inputs(study, backend + "-" + provider)
                    rows, hashes = load_splits(work / "data")
                    native = check_inputs(
                        request, rows, hashes, fixtures.fixtures.QUESTIONS
                    )
                    self.assertEqual(native["configuration"]["backend"], backend)
                    self.assertEqual(
                        native["counts"],
                        {role: len(values) for role, values in rows.items()},
                    )
                    command = trainer_command(request, work, self.f.root / "output")
                    self.assertEqual(command[0], sys.executable)
                    self.assertIn(
                        "decision_workbench.training.native_remote_runner", command
                    )
                    self.assertNotIn("--rank", command)
                    self.assertFalse(native["configuration"]["download"])

    def test_changed_native_study_source_schema_or_settings_is_refused(self):
        request, work = self.inputs(self.study(), "changed-study")
        rows, hashes = load_splits(work / "data")
        for field, value in (
            ("source_sha256", "0" * 64),
            ("data_sha256", {}),
            ("schema_sha256", "0" * 64),
        ):
            changed = copy.deepcopy(request)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                check_inputs(changed, rows, hashes, fixtures.fixtures.QUESTIONS)
        for change in (
            {"device": "cpu"},
            {"download": True},
            {"python": "unapproved"},
            {"learning_rate": 0.0002},
        ):
            changed = copy.deepcopy(request)
            changed["configuration"].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                check_inputs(changed, rows, hashes, fixtures.fixtures.QUESTIONS)
        augmented = {
            **request,
            "request_sha256": "0" * 64,
            "questions": fixtures.fixtures.QUESTIONS,
        }
        with self.assertRaisesRegex(ValueError, "checksum"):
            check_inputs(augmented, rows, hashes, fixtures.fixtures.QUESTIONS)

    def test_study_recovery_reconstructs_identical_archives_without_weights(self):
        study = self.study()
        before = study_archive(
            self.f.store, fixtures.OWNER, self.f.project, study["job_id"]
        ).read_bytes()
        folder = self.f.store.folder / "jobs" / study["job_id"] / "remote-study"
        # Model the recovery state in which transient job files are absent.
        import shutil

        self.assertTrue(folder.resolve().is_relative_to(self.f.root.resolve()))
        shutil.rmtree(folder)
        after = study_archive(
            self.f.store, fixtures.OWNER, self.f.project, study["job_id"]
        ).read_bytes()
        self.assertEqual(before, after)

    def test_native_trainer_executes_before_remote_provenance_and_empty_output_publication(
        self,
    ):
        from decision_workbench.training.liquid_remote_runner import execute
        from decision_workbench.training.artifacts import write_manifest

        request, work = self.inputs(self.study(), "native-dispatch")
        output = self.f.root / "provider-model"
        output.mkdir()  # SageMaker and other providers can precreate this directory.
        calls = []

        def train(args):
            calls.append(args)
            self.assertEqual(args.device, "cuda")
            self.assertFalse(args.download)
            self.assertEqual(args.max_steps, 1)
            self.assertEqual(
                json.loads(Path(args.local_study_request).read_text()),
                request["configuration"]["omni_study"],
            )
            folder = Path(args.out)
            (folder / "checkpoint").mkdir(parents=True)
            (folder / "checkpoint/model.safetensors").write_bytes(
                b"fictional transport fixture"
            )
            report = {"backend": "liquid-omni"}
            (folder / "report.json").write_text(dumps(report))
            write_manifest(
                folder, "liquid-omni", fixtures.fixtures.QUESTIONS, "checkpoint"
            )
            return report

        with (
            patch(
                "decision_workbench.training.liquid_omni_runner.train_omni",
                side_effect=train,
            ),
            patch("decision_workbench.platform.remote.validate_export") as validate,
        ):
            result = execute(work / "request.json", work / "data", output)
        self.assertEqual(len(calls), 1)
        self.assertEqual(validate.call_count, 1)
        self.assertEqual(
            result["remote_study_sha256"],
            hashlib.sha256(dumps(request).encode()).hexdigest(),
        )
        self.assertEqual(
            json.loads((output / "report.json").read_text())["remote_study_sha256"],
            result["remote_study_sha256"],
        )
        self.assertTrue((output / "manifest.json").is_file())
        self.assertFalse((output / ".liquid-incomplete").exists())
        with self.assertRaisesRegex(ValueError, "new or empty"):
            execute(work / "request.json", work / "data", output)

    def test_image_capabilities_require_explicit_liquid_approval(self):
        self.assertEqual(training_backends({}), ("laya-native", "unsloth"))
        self.assertEqual(
            training_backends({"training_backends": list(BACKENDS)}), BACKENDS
        )
        for value in (
            [],
            ["unknown"],
            ["liquid-omni", "liquid-omni"],
            "liquid-omni",
            [None],
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                training_backends({"training_backends": value})

    def test_remote_native_code_is_checked_at_its_original_hash_before_any_import(self):
        from decision_workbench.platform.remote import validate_export
        from decision_workbench.training.artifacts import write_manifest
        from decision_workbench.training.liquid_omni_data import ASSETS

        request, work = self.inputs(self.study(), "tampered-code")
        config = request["configuration"]
        study = config["omni_study"]
        folder = self.f.root / "untrusted-export"
        checkpoint = folder / "checkpoint"
        checkpoint.mkdir(parents=True)
        for name in ASSETS:
            if name.endswith(".py"):
                (checkpoint / name).write_text(
                    'raise AssertionError("must never execute")'
                )
        questions = fixtures.fixtures.QUESTIONS
        report = {
            "backend": "liquid-omni",
            "model": config["model"],
            "revision": config["revision"],
            "source_sha256": study["source_sha256"],
            "data_sha256": study["data_sha256"],
            "counts": study["counts"],
            "configuration": study["configuration"],
            "local_study_sha256": hashlib.sha256(
                liquid_omni_local.request_body(study)
            ).hexdigest(),
            "schema_conditioned": True,
            "test": {k: {} for k in questions},
            "calibration": {
                k: {"temperature": 1.0, "records": study["counts"]["calibration"]}
                for k in questions
            },
            "training": {
                "method": "native-head",
                "optimizer_updates": 1,
                "max_parameter_delta": 0.001,
                "frozen_parameters_unchanged": True,
                "frozen_parameters_sha256": "a" * 64,
                "dtype": "float32",
            },
        }
        (folder / "report.json").write_text(dumps(report))
        checksum = write_manifest(folder, "liquid-omni", questions, "checkpoint")
        with self.assertRaisesRegex(ValueError, "reviewed revision"):
            validate_export(
                folder,
                checksum,
                {
                    **request,
                    "questions": questions,
                    "request_sha256": hashlib.sha256(
                        dumps(request).encode()
                    ).hexdigest(),
                },
            )

    def test_browser_prepares_all_liquid_cloud_profiles_without_a_local_gpu(self):
        from streamlit.testing.v1 import AppTest
        from http_server_fixture import LiveServer

        cpu = {
            "system": "fixture",
            "python": "3.13",
            "cuda": False,
            "mps": False,
            "gpus": [],
            "error": None,
            "packages": {"numpy": "fixture", "scikit-learn": "fixture"},
        }
        with LiveServer(self.f.store.folder, fixtures.fixtures.keys()) as live:
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
            for key, value in {
                "credential": "a-secret",
                "api_url": live.url,
                "principal": fixtures.OWNER,
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
            for backend in BACKENDS:
                with self.subTest(backend=backend):
                    next(
                        w for w in app.selectbox if w.label == "Training backend"
                    ).select(backend).run(timeout=30)
                    next(
                        w for w in app.radio if w.label == "Training location"
                    ).set_value("Cloud GPU").run(timeout=30)
                    next(
                        w
                        for w in app.checkbox
                        if w.label
                        == "The selected provider is approved to receive this study's domain data"
                    ).check().run(timeout=30)
                    next(
                        w for w in app.selectbox if w.label == "Cloud training provider"
                    ).select("modal")
                    next(
                        w
                        for w in app.text_input
                        if w.label == "Approved provider region or environment"
                    ).set_value("fixture-environment")
                    next(
                        w
                        for w in app.text_input
                        if w.label == "Approved GPU or instance identity"
                    ).set_value("A10G")
                    next(
                        w
                        for w in app.text_input
                        if w.label == "Qualified training image digest"
                    ).set_value("fixture/trainer@sha256:" + "a" * 64)
                    button = next(
                        w
                        for w in app.button
                        if w.label == "Prepare remote training study"
                    )
                    self.assertFalse(button.disabled)
                    self.assertTrue(
                        next(
                            w for w in app.button if w.label == "Queue training"
                        ).disabled
                    )
                    button.click().run(timeout=30)
                    self.assertFalse(app.exception)
                    self.assertFalse(app.error, [w.value for w in app.error])
                    job = next(
                        j
                        for j in self.f.store.jobs("A", self.f.project)
                        if j["kind"] == "remote-study"
                        and j["config"]["backend"] == backend
                    )
                    self.assertEqual(job["config"]["device"], "cuda")
                    self.assertFalse(job["config"]["download"])
                    self.assertIn(
                        {"d3-edge": "d3_study", "liquid-omni": "omni_study"}.get(backend, "liquid_study"),
                        job["config"],
                    )
                    self.f.worker.once()
                    done = self.f.store.job(job["id"], "A", self.f.project)
                    self.assertEqual(done["status"], "succeeded", done["error"])

    def test_managed_archive_accepts_only_selected_reviewed_native_assets(self):
        import io
        import tarfile
        from decision_workbench.platform.cloud_archive import extract_model
        from decision_workbench.training.liquid_omni_data import ASSETS

        body = b"fictional transport fixture, never executed"

        def archive(name, code, suffix):
            target = self.f.root / (suffix + ".tar.gz")
            with tarfile.open(target, "w:gz", format=tarfile.USTAR_FORMAT) as bundle:
                for key, value in {
                    "report.json": b"{}",
                    "manifest.json": b"{}",
                    name: code,
                }.items():
                    info = tarfile.TarInfo(key)
                    info.size = len(value)
                    bundle.addfile(info, io.BytesIO(value))
            return target

        source = archive("checkpoint/modeling_d1.py", body, "reviewed")
        with self.assertRaisesRegex(ValueError, "unsupported/executable"):
            extract_model(source, self.f.root / "generic-refusal", 4096)
        with patch.dict(ASSETS, {"modeling_d1.py": hashlib.sha256(body).hexdigest()}):
            result = extract_model(
                source, self.f.root / "selected-native", 4096, backend="liquid-omni"
            )
            self.assertEqual(result["entries"], 3)
            changed = archive(
                "checkpoint/modeling_d1.py", body + b" changed", "changed"
            )
            with self.assertRaisesRegex(ValueError, "reviewed native asset"):
                extract_model(
                    changed, self.f.root / "changed-native", 4096, backend="liquid-omni"
                )
            self.assertFalse((self.f.root / "changed-native").exists())
            rogue = archive("checkpoint/unapproved.py", body, "unapproved")
            with self.assertRaisesRegex(ValueError, "unsupported/executable"):
                extract_model(
                    rogue,
                    self.f.root / "unapproved-native",
                    4096,
                    backend="liquid-omni",
                )

    def test_all_provider_profiles_refuse_undeclared_models_before_run_creation(self, backend="liquid-omni"):
        from decision_workbench.training.native_remote import profile

        native, _, _ = profile(backend)
        import test_cloud as aws
        import test_cloud_gcp as gcp
        import test_cloud_azure as azure
        import test_cloud_runpod as runpod
        import test_cloud_modal as modal

        for case in (
            aws.CloudTests,
            gcp.GcpCloudTests,
            azure.AzureCloudTests,
            runpod.RunPodCloudTests,
            modal.ModalCloudTests,
        ):
            with self.subTest(provider=case.__name__):
                fixture = case("runTest")
                fixture.setUp()
                try:
                    config = {
                        "snapshot": fixture.f.snapshot,
                        **native.PROFILE,
                        "device": "cuda",
                        "download": False,
                        "max_length": 256,
                        "max_steps": 1,
                        **{
                            k: fixture.p[k]
                            for k in ("provider", "region", "gpu", "image_digest")
                        },
                    }
                    job = fixture.f.service.submit(
                        fixtures.OWNER,
                        fixture.f.project,
                        "remote-study",
                        config,
                        "provider-omni-study",
                    )
                    fixture.f.worker.once()
                    self.assertEqual(
                        fixture.f.store.job(job["id"], "A", fixture.f.project)[
                            "status"
                        ],
                        "succeeded",
                    )
                    with self.assertRaisesRegex(ValueError, "does not declare"):
                        fixture.cloud.create(
                            fixtures.OWNER,
                            fixture.f.project,
                            job["id"],
                            fixture.p["id"],
                            True,
                            True,
                            "undeclared-omni-image",
                        )
                    fixture.p["training_backends"] = [backend]
                    if hasattr(fixture, "write_profiles"):
                        fixture.write_profiles()
                    else:
                        fixture.file.write_text(
                            json.dumps(
                                {
                                    "format": "domain-cloud-profiles-v1",
                                    "profiles": [fixture.p],
                                }
                            ),
                            encoding="utf-8",
                        )
                    run = fixture.cloud.create(
                        fixtures.OWNER,
                        fixture.f.project,
                        job["id"],
                        fixture.p["id"],
                        True,
                        True,
                        "declared-omni-image",
                    )
                    self.assertEqual(
                        run["payload"]["profile"]["training_backends"], [backend]
                    )
                    self.assertEqual(run["state"]["phase"], "ready")
                finally:
                    fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
