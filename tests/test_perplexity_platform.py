"""Local application contracts with fictional native weights and a real worker."""

from contextlib import ExitStack, redirect_stdout, redirect_stderr
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import socket
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from decision_workbench.platform.api import create_app
from decision_workbench.platform.worker import Worker
from decision_workbench.platform.perplexity_training import (
    study_for,
    recheck,
    checked_artifact,
    checked_report,
    job_timeout,
    move_export,
)
from decision_workbench.training.perplexity_local import PROFILE, request_body
from decision_workbench.training import perplexity_runner as runner
from decision_workbench.platform.model_environment import model_python
from decision_workbench.platform.recovery import backup_workspace, restore_workspace
from perplexity_native_fixture import NativeFixture, QUESTIONS
from test_platform import keys
import uvicorn
from streamlit.testing.v1 import AppTest
from decision_workbench.training.perplexity_runtime import PACKAGES
from decision_workbench.data import dumps
from decision_workbench.ui.onboarding import local_backend_available
from decision_workbench.ui.prediction_input import parsed_evidence

OWNER = {"tenant": "A", "actor": "owner", "role": "admin"}
REAL_POPEN = subprocess.Popen


class NativePerplexityPlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        folder = self.root / "fictional-model"
        folder.mkdir()
        self.native = NativeFixture(folder)
        self.app = create_app(self.root / "workspace", keys())
        self.service, self.store = self.app.state.service, self.app.state.store
        self.client = TestClient(self.app)
        self.client.headers.update({"Authorization": "Bearer a-secret"})
        response = self.client.post(
            "/v1/projects",
            json={
                "name": "Fictional native lifecycle",
                "description": "Fictional required-field policy for mechanics tests",
                "questions": QUESTIONS,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.project = response.json()["id"]
        self.prefix = "/v1/projects/" + self.project
        reviewed = self.service.save_dataset(
            OWNER,
            self.project,
            [row for rows in self.native.rows.values() for row in rows],
            {"fictional_fixture": True},
        )
        prepared = self.service.submit(
            OWNER,
            self.project,
            "prepare",
            {"datasets": [reviewed["id"]]},
            "native-prepare-fixture",
        )
        self.worker = Worker(self.store)
        self.worker.once()
        job = self.store.job(prepared["id"], "A", self.project)
        self.assertEqual(job["status"], "succeeded", job)
        self.snapshot_id = job["result"]["snapshot"]
        self.snapshot = self.store.get(self.snapshot_id, "A", self.project, "snapshot")[
            "payload"
        ]
        self.questions = self.service.project(OWNER, self.project)["payload"][
            "questions"
        ]
        self.native.rows = self.snapshot["splits"]
        response = self.client.put(
            self.prefix + "/release-policy",
            json={
                "min_accuracy": 0.0,
                "min_macro_f1": 0.0,
                "max_ece": 1.0,
                "min_test_rows": 10,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.config = {"snapshot": self.snapshot_id, **PROFILE}
        self.stack = ExitStack()
        self.native.patches(self.stack)
        self.stack.enter_context(
            patch.dict(
                os.environ,
                {
                    "DMT_PERPLEXITY_PYTHON": sys.executable,
                    "DMT_PERPLEXITY_CHECKPOINT": str(self.native.base),
                },
            )
        )
        self.children = []

    def tearDown(self):
        self.service.runtime.close()
        self.worker.service.runtime.close()
        self.stack.close()
        self.client.close()
        self.temp.cleanup()

    def queue(self, config=None, key="native-platform-study"):
        return self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "train",
                "config": self.config if config is None else config,
                "request_key": key,
            },
        )

    def child(self, argv, **kwargs):
        self.children.append((argv, kwargs))
        if len(argv) > 2 and argv[2] == "decision_workbench.training.perplexity_runner":
            previous = sys.argv
            try:
                sys.argv = ["perplexity-runner", *argv[3:]]
                with (
                    redirect_stdout(kwargs["stdout"]),
                    redirect_stderr(kwargs["stdout"]),
                ):
                    runner.main()
            finally:
                sys.argv = previous
            return SimpleNamespace(poll=lambda: 0, returncode=0)
        if len(argv) > 2 and argv[2] == "decision_workbench.training.runtime":
            argv = [
                argv[0],
                str(Path(__file__).with_name("perplexity_runtime_fixture.py")),
                *argv[3:],
            ]
        return REAL_POPEN(argv, **kwargs)

    def train(self, key="native-platform-study"):
        response = self.queue(key=key)
        self.assertEqual(response.status_code, 202, response.text)
        queued = response.json()
        with patch("subprocess.Popen", side_effect=self.child):
            self.worker.once()
        job = self.store.job(queued["id"], "A", self.project)
        self.assertEqual(job["status"], "succeeded", job)
        return self.store.get(job["result"]["model"], "A", self.project, "model")

    def test_queue_freezes_data_settings_source_and_model_resource(self):
        response = self.queue()
        self.assertEqual(response.status_code, 202, response.text)
        queued = response.json()
        self.assertEqual(
            queued["config"]["perplexity_study"]["counts"], self.snapshot["counts"]
        )
        self.assertTrue(queued["config"]["release_policy_id"])
        self.assertTrue(self.store.has_model_training())
        self.assertEqual(self.queue().json()["id"], queued["id"])
        recheck(queued["config"], self.snapshot, self.questions)
        changed = copy.deepcopy(queued["config"])
        changed["perplexity_study"]["source_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "changed after queueing"):
            recheck(changed, self.snapshot, self.questions)

    def test_paths_downloads_wrong_profiles_roles_and_incomplete_cloud_are_refused(
        self,
    ):
        for extra in (
            {"python": sys.executable},
            {"checkpoint": str(self.native.base)},
            {"cache": "elsewhere"},
            {"download": False},
            {"device": "cpu"},
            {"model": "unknown/model"},
            {"max_steps": 1},
            {"perplexity_study": {}},
        ):
            response = self.queue({**self.config, **extra})
            self.assertEqual(response.status_code, 400, response.text)
        response = self.client.post(
            self.prefix + "/jobs",
            headers={"Authorization": "Bearer review-secret"},
            json={
                "kind": "train",
                "config": self.config,
                "request_key": "native-reviewer",
            },
        )
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "remote-study",
                "config": {**self.config, "provider": "aws"},
                "request_key": "native-remote",
            },
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertFalse(self.store.has_model_training())

    def test_actual_worker_produces_and_validates_fictional_native_export(self):
        model = self.train()
        self.assertEqual(model["payload"]["backend"], "perplexity-native")
        self.assertFalse(self.store.has_model_training())
        argv, options = self.children[0]
        self.assertEqual(argv[0], sys.executable)
        request = json.loads(
            Path(argv[argv.index("--local-study-request") + 1]).read_text()
        )
        self.assertEqual(request, model["payload"]["configuration"]["perplexity_study"])
        self.assertNotIn("--download", argv)
        self.assertEqual(options["cwd"], Path(__file__).resolve().parents[1])
        folder = self.service.checked_export(model["payload"], self.questions)
        self.assertEqual(folder.parent, (self.store.folder / "artifacts").resolve())
        self.assertTrue((folder / "checkpoint/dmt_perplexity.json").is_file())
        self.assertEqual(self.store.active("A", self.project)["model_id"], None)

    def test_registered_report_refuses_forged_training_calibration_and_reference(self):
        model = self.train()["payload"]
        report, config = model["report"], model["configuration"]
        checked_report(report, config)
        changes = [
            (("source_sha256",), "0" * 64),
            (("local_study_sha256",), "0" * 64),
            (("schema_conditioned",), False),
            (("configuration", "epochs"), config["epochs"] + 1),
            (("counts", "test"), 0),
            (("training", "method"), "readout-only"),
            (("training", "device"), "cpu"),
            (("training", "optimizer_updates"), True),
            (("training", "checkpoint_policy"), "best-test-score"),
            (("training", "backbone_updated"), False),
            (("training", "readout_payload_sha256"), "invalid"),
            (
                ("training", "backbone_payload_sha256"),
                report["training"]["original_backbone_payload_sha256"],
            ),
            (("packages", "torch"), "unknown"),
            (("calibration", "ready", "temperature"), 21),
            (("calibration", "ready", "records"), 0),
            (("calibration", "ready", "decision_fit_records"), 0),
            (("calibration", "ready", "method"), "test-fitted"),
            (("reference", "kind"), "paid-provider"),
            (("reference", "model"), "another/model"),
            (("reference", "revision"), "0" * 40),
            (("reference", "paid_api_calls"), True),
        ]
        for path, value in changes:
            with self.subTest(path=path):
                changed = copy.deepcopy(report)
                target = changed
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaises(ValueError):
                    checked_report(changed, config)
        changed = copy.deepcopy(report)
        del changed["calibration"]["ready"]
        with self.assertRaises(ValueError):
            checked_report(changed, config)
        changed = copy.deepcopy(report)
        changed["calibration"]["ready"]["temperature"] = 1.25
        changed["calibration"]["action"]["temperature"] = 1.75
        with self.assertRaisesRegex(ValueError, "temperature/reference"):
            checked_report(changed, config)

    def test_registered_artifact_refuses_extra_code_or_changed_runtime_metadata(self):
        model = self.train()["payload"]
        folder = self.service.checked_export(model, self.questions)
        extra = folder / "unexpected.py"
        extra.write_text("raise RuntimeError('unapproved')", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unapproved"):
            checked_artifact(
                folder, model["report"], model["configuration"], self.questions
            )
        extra.unlink()
        metadata = folder / "checkpoint/dmt_perplexity.json"
        original = metadata.read_bytes()
        value = json.loads(original)
        value["max_length"] += 1
        metadata.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "schema/temperature/payload"):
            checked_artifact(
                folder, model["report"], model["configuration"], self.questions
            )
        metadata.write_bytes(original)
        template = folder / "checkpoint/chat_template.jinja"
        original = template.read_bytes()
        template.write_bytes(original + b"\n ")
        with self.assertRaisesRegex(ValueError, "processor/architecture"):
            checked_artifact(
                folder, model["report"], model["configuration"], self.questions
            )
        template.write_bytes(original)
        self.service.checked_export(model, self.questions)

    def test_export_move_is_confined_to_an_owned_job_and_new_artifact_slot(self):
        job = self.store.folder / "jobs" / ("c" * 32)
        source = job / "perplexity-run/export"
        source.mkdir(parents=True)
        (source / "manifest.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "owned job"):
            move_export(self.store, job, self.root / "outside-artifacts")
        occupied = self.store.folder / "artifacts" / ("d" * 32)
        occupied.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "owned job"):
            move_export(self.store, job, occupied)
        destination = self.store.folder / "artifacts" / ("e" * 32)
        move_export(self.store, job, destination)
        self.assertTrue((destination / "manifest.json").is_file())
        self.assertFalse(source.exists())

    def test_worker_source_drift_and_cancellation_cannot_register_model(self):
        queued = self.queue().json()
        with patch(
            "decision_workbench.platform.perplexity_training.source_fingerprint",
            return_value="0" * 64,
        ):
            self.worker.once()
        self.assertEqual(
            self.store.job(queued["id"], "A", self.project)["status"], "failed"
        )
        queued = self.queue(key="native-cancelled").json()
        self.assertEqual(
            self.client.post(
                self.prefix + "/jobs/" + queued["id"] + "/cancel"
            ).status_code,
            200,
        )
        self.assertFalse(self.store.has_model_training())
        self.assertEqual(self.store.list("A", self.project, "model"), [])

    def test_operator_interpreter_and_deadline_are_explicit(self):
        self.assertEqual(model_python("perplexity-native", "training"), sys.executable)
        self.assertEqual(job_timeout({}), 86400)
        for value in ("nan", "-1", "infinite", "604801"):
            with self.assertRaises(ValueError):
                job_timeout({"DMT_PERPLEXITY_TRAINING_TIMEOUT_SECONDS": value})

    def test_native_availability_is_profile_and_free_memory_specific(self):
        runtime = {
            "system": "Linux",
            "python": "3.12.15",
            "packages": {**PACKAGES, "scikit-learn": "1.9.1"},
            "cuda": True,
            "gpus": [{"free_vram_gib": 190}],
            "ram_available_gib": 1024,
        }
        capabilities = {
            "model_profiles": {
                "perplexity-native": {"training": runtime, "serving": runtime}
            },
            "perplexity_checkpoint_configured": True,
        }
        self.assertTrue(local_backend_available("perplexity-native", capabilities)[0])
        for change in (
            {"system": "Windows"},
            {"python": "3.11.15"},
            {"gpus": [{"vram_gib": 192, "free_vram_gib": 1}]},
            {"ram_available_gib": 16},
            {"packages": dict(PACKAGES)},
            {"packages": {**PACKAGES, "torch": "2.10.0"}},
        ):
            value = copy.deepcopy(capabilities)
            value["model_profiles"]["perplexity-native"]["training"].update(change)
            self.assertFalse(local_backend_available("perplexity-native", value)[0])
        capabilities["perplexity_checkpoint_configured"] = False
        self.assertFalse(local_backend_available("perplexity-native", capabilities)[0])

    def test_text_json_evidence_keeps_structure_and_refuses_invalid_inputs(self):
        self.assertEqual(parsed_evidence("Text", '{"ready": true}'), '{"ready": true}')
        self.assertEqual(parsed_evidence("JSON", '{"ready": true}'), {"ready": True})
        for value in (
            '{"ready":true,"ready":false}',
            '{"ready":',
            "null",
            "false",
            "5",
            "{}",
            "[]",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parsed_evidence("JSON", value)
        with self.assertRaises(ValueError):
            parsed_evidence("Text", "a" * 64001)

    def test_release_prediction_version_rollback_and_cold_runtime_use_real_framing(
        self,
    ):
        first = self.train()
        with patch("subprocess.Popen", side_effect=self.child):
            response = self.client.post(
                self.prefix + "/releases",
                json={
                    "model_id": first["id"],
                    "expected_revision": 0,
                    "note": "Fictional native release",
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            response = self.client.post(
                self.prefix + "/decisions",
                headers={"Authorization": "Bearer infer-secret"},
                json={"state": {"ready": True}, "threshold": 0.99},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["model_id"], first["id"])
            self.assertTrue(response.json()["answers"]["ready"]["abstained"])
            second = self.train(key="native-second-version")
            response = self.client.post(
                self.prefix + "/releases",
                json={
                    "model_id": second["id"],
                    "expected_revision": 1,
                    "note": "Fictional native second version",
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            response = self.client.post(
                self.prefix + "/releases",
                json={
                    "model_id": first["id"],
                    "expected_revision": 2,
                    "note": "Fictional rollback",
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.service.runtime.close()
            cold = create_app(self.store.folder, keys())
            try:
                with TestClient(cold) as client:
                    response = client.post(
                        self.prefix + "/decisions",
                        headers={"Authorization": "Bearer a-secret"},
                        json={"state": {"ready": False}, "threshold": 0.5},
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["model_id"], first["id"])
                    self.assertFalse(response.json()["answers"]["ready"]["answer"])
            finally:
                cold.state.service.runtime.close()
            backup = backup_workspace(self.store.folder, self.root / "native-backup")
            restored = restore_workspace(
                backup["destination"],
                self.root / "native-restored",
                backup["manifest_sha256"],
            )
            recovered = create_app(restored["destination"], keys())
            try:
                with TestClient(recovered) as client:
                    response = client.post(
                        self.prefix + "/decisions",
                        headers={"Authorization": "Bearer a-secret"},
                        json={"state": {"ready": True}, "threshold": 0.5},
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["model_id"], first["id"])
                    self.assertTrue(response.json()["answers"]["ready"]["answer"])
            finally:
                recovered.state.service.runtime.close()

    def test_soft_heldouts_and_corrupt_reservations_are_refused_before_job_allocation(
        self,
    ):
        changed = copy.deepcopy(self.snapshot)
        changed["splits"]["calibration"][0]["gold"]["action"]["probabilities"] = {
            "continue": 0.5,
            "review": 0.5,
        }
        import hashlib

        changed["sha256"] = hashlib.sha256(
            dumps(changed["splits"]).encode()
        ).hexdigest()
        with self.assertRaisesRegex(ValueError, "one-hot"):
            study_for(self.config, changed, self.questions)
        changed["counts"]["test"] = 0
        with self.assertRaisesRegex(ValueError, "integrity"):
            study_for(self.config, changed, self.questions)
        self.assertFalse(self.store.has_model_training())

    def test_browser_native_training_release_and_structured_json_prediction(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        server = uvicorn.Server(
            uvicorn.Config(self.app, log_level="error", access_log=False)
        )
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [listener]}, daemon=True
        )
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if time.monotonic() > deadline:
                    raise RuntimeError("Fixture HTTP server startup timed out")
                time.sleep(0.02)
            profile = {
                "system": "Linux",
                "python": "3.12.15",
                "packages": {**PACKAGES, "scikit-learn": "1.9.1"},
                "cuda": True,
                "mps": False,
                "gpus": [
                    {"name": "Fictional GPU", "vram_gib": 192, "free_vram_gib": 190}
                ],
                "ram_available_gib": 1024,
                "error": None,
            }
            capabilities = {
                "model_profiles": {
                    "perplexity-native": {"training": profile, "serving": profile}
                },
                "runtimes": {"training": profile, "serving": profile},
                "teacher_configured": False,
                "cpu_baseline": True,
                "perplexity_checkpoint_configured": True,
            }
            at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
            at.session_state["credential"] = "a-secret"
            at.session_state["api_url"] = (
                f"http://127.0.0.1:{listener.getsockname()[1]}"
            )
            at.session_state["principal"] = OWNER
            at.session_state["runtime-capabilities"] = capabilities
            with patch("subprocess.Popen", side_effect=self.child):
                at.run(timeout=30)
                self.assertFalse(at.exception)
                next(w for w in at.selectbox if w.label == "Training backend").select(
                    "perplexity-native"
                ).run(timeout=30)
                self.assertFalse(at.exception)
                self.assertEqual(
                    next(r for r in at.radio if r.label == "Training location").options,
                    ["This computer", "Cloud GPU"],
                )
                button = next(b for b in at.button if b.label == "Queue training")
                self.assertFalse(button.disabled)
                button.click().run(timeout=30)
                self.assertFalse(at.exception)
                self.assertFalse(at.error, [e.value for e in at.error])
                queued = self.store.jobs("A", self.project)[0]
                self.assertEqual(queued["config"]["backend"], "perplexity-native")
                self.worker.once()
                self.assertEqual(
                    self.store.job(queued["id"], "A", self.project)["status"],
                    "succeeded",
                )
                next(
                    b for b in at.button if b.label == "Refresh workspace"
                ).click().run(timeout=30)
                next(
                    t for t in at.text_input if t.label == "Release or rollback reason"
                ).set_value("Fictional native browser release")
                next(
                    b for b in at.button if b.label == "Activate selected version"
                ).click().run(timeout=30)
                self.assertFalse(at.exception)
                self.assertFalse(at.error, [e.value for e in at.error])
                next(r for r in at.radio if r.label == "Evidence format").set_value(
                    "JSON"
                )
                next(
                    t
                    for t in at.text_area
                    if t.label == "Evidence for the released model"
                ).set_value('{"ready": true}')
                next(b for b in at.button if b.label == "Make decision").click().run(
                    timeout=30
                )
                self.assertFalse(at.exception)
                self.assertFalse(at.error, [e.value for e in at.error])
                self.assertTrue(self.store.active("A", self.project)["model_id"])
                at.session_state["credential"] = "infer-secret"
                at.session_state["principal"] = {
                    "tenant": "A",
                    "actor": "app",
                    "role": "inference",
                }
                at.run(timeout=30)
                self.assertFalse(
                    any(w.label == "Training backend" for w in at.selectbox)
                )
                next(r for r in at.radio if r.label == "Evidence format").set_value(
                    "JSON"
                )
                next(t for t in at.text_area if t.label == "Input evidence").set_value(
                    '{"ready": false}'
                )
                next(b for b in at.button if b.label == "Decide").click().run(
                    timeout=30
                )
                self.assertFalse(at.exception)
                self.assertFalse(at.error, [e.value for e in at.error])
        finally:
            server.should_exit = True
            thread.join(timeout=15)
            listener.close()
            self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
