"""Paired Vega cloud packaging and five-provider approval without paid compute."""

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import zipfile
from unittest.mock import patch

from decision_workbench.data import dumps
from decision_workbench.platform.remote import study_entity, study_archive
from decision_workbench.platform.worker import Worker
from decision_workbench.training.artifacts import file_sha
from decision_workbench.training.cloud_runner import prepare_archive, trainer_command
from decision_workbench.training.contracts import load_splits
from decision_workbench.training.vega_contracts import load_gate_splits
import test_vega_admission as fixtures


class VegaCloudTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.VegaAdmissionTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.VegaAdmissionTests.tearDownClass.__func__)
    setUp = fixtures.VegaAdmissionTests.setUp
    tearDown = fixtures.VegaAdmissionTests.tearDown
    policy = fixtures.VegaAdmissionTests.policy

    def study(self, provider="modal", profile=None):
        if not getattr(self, "_cloud_policy_ready", False):
            self.policy(
                {
                    "min_positive_accepted_fraction": 0.0,
                    "max_negative_false_open_rate": 1.0,
                    "min_negative_test_rows": 10,
                }
            )
            self._cloud_policy_ready = True
        resources = {
            "provider": provider,
            "region": "fixture",
            "gpu": "fixture",
            "image_digest": "fixture/trainer@sha256:" + "a" * 64,
        }
        if profile is not None:
            resources = {k: profile[k] for k in resources}
        job = self.service.submit(
            fixtures.OWNER,
            self.project,
            "remote-study",
            {
                "backend": "vega",
                "snapshot": self.snapshot,
                "gate_snapshot": self.gate,
                "offline": True,
                "max_steps": 2,
                **resources,
            },
            "vega-cloud-" + provider,
        )
        worker = Worker(self.store)
        try:
            worker.once()
        finally:
            worker.service.runtime.close()
        done = self.store.job(job["id"], "A", self.project)
        self.assertEqual(done["status"], "succeeded", done["error"])
        return study_entity(self.store, "A", self.project, job["id"])["payload"]

    def archive(self, study):
        return study_archive(self.store, fixtures.OWNER, self.project, study["job_id"])

    def unpack(self, study, suffix="work", archive=None, archive_sha=None):
        archive = Path(archive or self.archive(study))
        work = self.home.parent / suffix
        request = prepare_archive(
            archive,
            work,
            archive_sha or study["archive_sha256"],
            study["request_sha256"],
        )
        return request, work

    def test_paired_archive_and_native_command_preserve_all_inputs_and_settings(self):
        study = self.study()
        request, work = self.unpack(study)
        self.assertEqual(len(list((work / "data").iterdir())), 8)
        self.assertFalse((work / "data/manifest.json").exists())
        positive, hashes = load_splits(work / "data")
        _, negatives = load_gate_splits(work / "data", positive)
        self.assertEqual(hashes, request["data_sha256"])
        self.assertEqual(negatives, request["gate_data_sha256"])
        command = trainer_command(request, work, work / "output")
        self.assertEqual(
            command[:3],
            [sys.executable, "-m", "decision_workbench.training.vega_runner"],
        )
        self.assertEqual(command[command.index("--device") + 1], "cuda")
        self.assertEqual(
            command[command.index("--study-request") + 1], str(work / "request.json")
        )
        self.assertIn("--offline", command)
        self.assertNotIn("--grad-accum", command)
        for name in (
            "model",
            "revision",
            "backbone_model",
            "backbone_revision",
            "epochs",
            "batch_size",
            "rank",
            "max_steps",
            "learning_rate",
            "gate_weight",
        ):
            self.assertEqual(
                command[command.index("--" + name.replace("_", "-")) + 1],
                str(request["configuration"][name]),
            )

    def test_negative_tamper_is_refused_even_when_archive_hash_is_updated(self):
        study = self.study()
        original = self.archive(study)
        changed = self.home.parent / "changed.zip"
        with (
            zipfile.ZipFile(original) as src,
            zipfile.ZipFile(changed, "w", compression=zipfile.ZIP_STORED) as dst,
        ):
            for info in src.infolist():
                body = src.read(info.filename)
                if info.filename == "data/gate_test.jsonl":
                    rows = [json.loads(line) for line in body.splitlines()]
                    rows[0]["state"] = "Changed negative evidence"
                    body = ("\n".join(dumps(row) for row in rows) + "\n").encode()
                dst.writestr(info.filename, body)
        with self.assertRaises(ValueError):
            self.unpack(study, archive=changed, archive_sha=file_sha(changed))
        self.assertFalse((self.home.parent / "work").exists())

    def test_command_rechecks_changed_inputs_and_model_options(self):
        request, work = self.unpack(self.study())
        for change in ({"rank": 0}, {"offline": "yes"}, {"revision": "b" * 40}):
            bad = copy.deepcopy(request)
            bad["configuration"].update(change)
            with self.assertRaises(ValueError):
                trainer_command(bad, work, work / "output")
        path = work / "data/gate_train.jsonl"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaises(ValueError):
            trainer_command(request, work, work / "output")

    def test_archive_inventory_refuses_missing_extra_and_wrong_format(self):
        study = self.study()
        with zipfile.ZipFile(self.archive(study)) as src:
            original = {name: src.read(name) for name in src.namelist()}
        for index, mode in enumerate(("missing", "extra", "format")):
            entries = dict(original)
            request_sha = study["request_sha256"]
            if mode == "missing":
                del entries["data/gate_test.jsonl"]
            elif mode == "extra":
                entries["data/manifest.json"] = b"{}"
            else:
                request = json.loads(entries["request.json"])
                request["format"] = "domain-remote-study-v1"
                entries["request.json"] = dumps(request).encode()
                request_sha = hashlib.sha256(entries["request.json"]).hexdigest()
            archive = self.home.parent / f"invalid-{index}.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as dst:
                for name, body in entries.items():
                    dst.writestr(name, body)
            work = self.home.parent / f"invalid-work-{index}"
            with self.assertRaises(ValueError):
                prepare_archive(archive, work, file_sha(archive), request_sha)
            self.assertFalse(work.exists())

    def test_real_check_only_cli_never_imports_a_model_or_creates_an_export(self):
        study = self.study()
        work = self.home.parent / "cli-work"
        output = self.home.parent / "cli-export"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "decision_workbench.training.cloud_runner",
                "--archive",
                str(self.archive(study)),
                "--archive-sha256",
                study["archive_sha256"],
                "--request-sha256",
                study["request_sha256"],
                "--work",
                str(work),
                "--out",
                str(output),
                "--check-only",
            ],
            env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["training_executed"])
        self.assertFalse(output.exists())

    def test_all_five_controllers_require_opt_in_before_creating_a_ready_run(self):
        import test_cloud as aws
        import test_cloud_azure as azure
        import test_cloud_gcp as gcp
        import test_cloud_modal as modal
        import test_cloud_runpod as runpod

        for case in (
            aws.CloudTests,
            azure.AzureCloudTests,
            gcp.GcpCloudTests,
            modal.ModalCloudTests,
            runpod.RunPodCloudTests,
        ):
            with self.subTest(provider=case.__name__):
                provider = case("runTest")
                provider.setUp()
                try:
                    study = self.study(provider.p["provider"], provider.p)
                    args = (
                        fixtures.OWNER,
                        self.project,
                        study["job_id"],
                        provider.p["id"],
                        True,
                        True,
                    )
                    with self.assertRaisesRegex(ValueError, "does not declare"):
                        self.service.cloud.create(*args, "undeclared-vega-image")
                    provider.p["training_backends"] = ["vega"]
                    if hasattr(provider, "write_profiles"):
                        provider.write_profiles()
                    else:
                        provider.file.write_text(
                            json.dumps(
                                {
                                    "format": "domain-cloud-profiles-v1",
                                    "profiles": [provider.p],
                                }
                            ),
                            encoding="utf-8",
                        )
                    run = self.service.cloud.create(*args, "declared-vega-image")
                    self.assertEqual(run["state"]["phase"], "ready")
                    self.assertEqual(
                        run["payload"]["profile"]["training_backends"], ["vega"]
                    )
                    self.assertFalse(run["state"]["create_attempted"])
                finally:
                    provider.tearDown()

    def test_cloud_browser_prepares_paired_inputs_without_local_vega_or_gpu(self):
        from http_server_fixture import LiveServer
        from streamlit.testing.v1 import AppTest
        from test_platform import keys

        self.policy(
            {
                "min_positive_accepted_fraction": 0.0,
                "max_negative_false_open_rate": 1.0,
                "min_negative_test_rows": 10,
            }
        )
        cpu = {
            "system": "fixture",
            "python": "3.13",
            "cuda": False,
            "mps": False,
            "gpus": [],
            "error": None,
            "packages": {"numpy": "fixture", "scikit-learn": "fixture"},
        }
        with LiveServer(self.home, keys()) as live:
            browser = AppTest.from_file(str(Path("app.py").resolve()))
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
                browser.session_state[key] = value
            browser.run(timeout=30)

            def widget(kind, label):
                return next(w for w in getattr(browser, kind) if w.label == label)

            widget("selectbox", "Training backend").select("vega").run(timeout=30)
            self.assertTrue(widget("button", "Queue training").disabled)
            widget("radio", "Training location").set_value("Cloud GPU").run(timeout=30)
            widget(
                "checkbox", "Use only cached pinned Vega/backbone assets"
            ).uncheck().run(timeout=30)
            widget(
                "checkbox",
                "The selected provider is approved to receive this study's domain data",
            ).check().run(timeout=30)
            widget("selectbox", "Cloud training provider").select("modal")
            widget("text_input", "Approved provider region or environment").set_value(
                "fixture"
            )
            widget("text_input", "Approved GPU or instance identity").set_value(
                "fixture"
            )
            widget("text_input", "Qualified training image digest").set_value(
                "fixture/trainer@sha256:" + "a" * 64
            )
            self.assertFalse(widget("button", "Prepare remote training study").disabled)
            widget("button", "Prepare remote training study").click().run(timeout=30)
            self.assertFalse(browser.exception)
            self.assertFalse(browser.error)
            job = next(
                j
                for j in self.store.jobs("A", self.project)
                if j["kind"] == "remote-study"
            )
            self.assertEqual(job["config"]["backend"], "vega")
            self.assertFalse(job["config"]["offline"])
            worker = Worker(self.store)
            try:
                worker.once()
            finally:
                worker.service.runtime.close()
            done = self.store.job(job["id"], "A", self.project)
            self.assertEqual(done["status"], "succeeded", done["error"])
            study = study_entity(self.store, "A", self.project, job["id"])["payload"]
            _, work = self.unpack(study, "browser-work")
            self.assertEqual(len(list((work / "data").iterdir())), 8)
