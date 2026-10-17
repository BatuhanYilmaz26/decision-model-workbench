"""D3 API/worker/browser contracts; model execution is separately qualified."""

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from decision_workbench.platform.d3_training import (
    study_for,
    recheck,
    checked_report,
    checked_artifact,
)
from decision_workbench.platform.model_environment import (
    model_python,
    native_environment,
)
from decision_workbench.platform.worker import Worker
from decision_workbench.training.d3_local import PROFILE, request_body
from decision_workbench.training.d3_runtime import PACKAGES
from decision_workbench.ui.onboarding import local_backend_available
import test_liquid_platform as fixtures
from test_platform import QUESTIONS, keys


class D3PlatformTests(unittest.TestCase):
    def setUp(self):
        fixtures.LiquidPlatformTests.setUp(self)
        self.config = {
            "snapshot": self.snapshot_id,
            **PROFILE,
            "device": "cpu",
            "max_steps": 1,
            "max_length": 256,
        }

    def queue(self, config=None, key="d3-study-fixture"):
        return self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "train",
                "config": self.config if config is None else config,
                "request_key": key,
            },
        )

    def test_queue_binds_source_settings_all_roles_policy_and_model_resource(self):
        response = self.queue()
        self.assertEqual(response.status_code, 202, response.text)
        job = response.json()
        config = job["config"]
        self.assertEqual(config["d3_study"]["counts"], self.snapshot["counts"])
        self.assertEqual(config["d3_study"]["configuration"]["device"], "cpu")
        self.assertTrue(config["release_policy_id"])
        self.assertTrue(self.store.has_model_training())
        self.assertEqual(self.queue().json()["id"], job["id"])
        recheck(config, self.snapshot, QUESTIONS)
        changed = copy.deepcopy(config)
        changed["d3_study"]["source_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "changed after queueing"):
            recheck(changed, self.snapshot, QUESTIONS)

    def test_unsupported_options_tenant_and_remote_routes_refused_before_allocation(
        self,
    ):
        for extra in (
            {"rank": 4},
            {"python": sys.executable},
            {"cache": "unapproved"},
            {"d3_study": {}},
            {"device": "mps"},
            {"model": "unapproved/model"},
        ):
            response = self.queue({**self.config, **extra})
            self.assertEqual(response.status_code, 400, response.text)
        denied = self.client.post(
            self.prefix + "/jobs",
            headers={"Authorization": "Bearer b-secret"},
            json={
                "kind": "train",
                "config": self.config,
                "request_key": "other-tenant-d3",
            },
        )
        self.assertEqual(denied.status_code, 404)
        remote = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "remote-study",
                "config": {
                    **self.config,
                    "provider": "aws",
                    "region": "us-east-1",
                    "gpu": "ml.g5.xlarge",
                    "image_digest": "fixture/image@sha256:" + "a" * 64,
                },
                "request_key": "d3-remote-refusal",
            },
        )
        self.assertEqual(remote.status_code, 400, remote.text)
        self.assertIn("D3 cloud studies", remote.text)
        self.assertEqual(self.store.jobs("A", self.project)[-1]["kind"], "prepare")

    def test_worker_source_tamper_and_queued_cancellation_register_nothing(self):
        queued = self.queue().json()
        worker = Worker(self.store)
        self.addCleanup(worker.service.runtime.close)
        with patch(
            "decision_workbench.platform.d3_training.source_fingerprint",
            return_value="0" * 64,
        ):
            worker.once()
        job = self.store.job(queued["id"], "A", self.project)
        self.assertEqual(job["status"], "failed")
        self.assertIn("changed after queueing", job["error"])
        queued = self.queue(key="d3-cancelled-study").json()
        self.assertTrue(self.store.has_model_training())
        self.assertEqual(
            self.client.post(
                self.prefix + "/jobs/" + queued["id"] + "/cancel"
            ).status_code,
            200,
        )
        self.assertFalse(self.store.has_model_training())
        self.assertEqual(self.store.list("A", self.project, "model"), [])

    def test_worker_uses_separate_interpreter_frozen_request_and_native_options(self):
        queued = self.queue().json()
        called = []

        def child(argv, **kwargs):
            called.append((argv, kwargs))
            return SimpleNamespace(poll=lambda: 1, returncode=1)

        worker = Worker(self.store)
        self.addCleanup(worker.service.runtime.close)
        with (
            patch.dict(os.environ, {"DMT_D3_PYTHON": sys.executable}),
            patch(
                "decision_workbench.platform.worker.subprocess.Popen", side_effect=child
            ),
        ):
            worker.once()
        self.assertEqual(len(called), 1)
        argv, options = called[0]
        self.assertEqual(argv[0], sys.executable)
        self.assertIn("decision_workbench.training.d3_runner", argv)
        self.assertNotIn("--rank", argv)
        self.assertNotIn("--model", argv)
        self.assertEqual(argv[argv.index("--device") + 1], "cpu")
        path = Path(argv[argv.index("--local-study-request") + 1])
        self.assertEqual(json.loads(path.read_bytes()), queued["config"]["d3_study"])
        self.assertEqual(options["env"]["D3_FAST"], "0")
        self.assertEqual(
            self.store.job(queued["id"], "A", self.project)["status"], "failed"
        )

    def test_report_rejects_untrained_head_missing_temperatures_and_bytecode(self):
        study = study_for(self.config, self.snapshot, QUESTIONS)
        config = {
            **study["configuration"],
            "snapshot": self.snapshot_id,
            "d3_study": study,
        }
        report = {
            **PROFILE,
            "source_sha256": study["source_sha256"],
            "data_sha256": study["data_sha256"],
            "counts": study["counts"],
            "configuration": study["configuration"],
            "local_study_sha256": hashlib.sha256(request_body(study)).hexdigest(),
            "schema_conditioned": True,
            "calibration": {
                name: {
                    "temperature": 1.0,
                    "records": study["counts"]["calibration"],
                    "nll": 0.0,
                }
                for name in QUESTIONS
            },
            "test": {name: {} for name in QUESTIONS},
            "training": {
                "method": "native-readout",
                "optimizer_updates": 1,
                "max_parameter_delta": 0.001,
                "frozen_parameters_unchanged": True,
                "frozen_parameters_sha256": "a" * 64,
                "backbone_dtype": "bfloat16",
                "readout_dtype": "float32",
                "trainable_parameters": 261120,
                "device": "cpu",
            },
        }
        checked_report(report, config)
        changed = copy.deepcopy(report)
        changed["calibration"] = {name: {"accuracy": 1.0} for name in QUESTIONS}
        with self.assertRaises(ValueError):
            checked_report(changed, config)
        for change in (
            {"optimizer_updates": 0},
            {"max_parameter_delta": float("nan")},
            {"frozen_parameters_unchanged": False},
            {"trainable_parameters": 1},
            {"device": None},
        ):
            changed = copy.deepcopy(report)
            changed["training"].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                checked_report(changed, config)
        bundle = self.home / "unapproved-artifact"
        bundle.mkdir()
        (bundle / "cached.pyc").write_bytes(b"must never execute")
        with self.assertRaisesRegex(ValueError, "unapproved native file"):
            checked_artifact(bundle, report, config, QUESTIONS)

    def test_operator_environment_and_training_serving_versions_are_separate(self):
        env = {
            "DMT_D3_PYTHON": sys.executable,
            "DMT_D3_CACHE": str(self.home / "cache"),
        }
        self.assertEqual(model_python("d3-edge", "training", env), sys.executable)
        self.assertEqual(model_python("d3-edge", "serving", env), sys.executable)
        values, source, cache = native_environment(self.home, env, backend="d3-edge")
        self.assertEqual(cache, self.home / "cache")
        self.assertEqual(values["D3_FAST"], "0")
        self.assertEqual(source, Path(__file__).resolve().parents[1])
        profile = {
            "packages": {**PACKAGES, "torch": "2.10.0+cpu"},
            "cuda": False,
            "ram_available_gib": 8,
        }
        capabilities = {
            "model_profiles": {
                "d3-edge": {"training": profile, "serving": copy.deepcopy(profile)}
            }
        }
        self.assertTrue(local_backend_available("d3-edge", capabilities)[0])
        capabilities["model_profiles"]["d3-edge"]["serving"]["packages"][
            "transformers"
        ] = "5.19.0"
        self.assertFalse(local_backend_available("d3-edge", capabilities)[0])

    def test_browser_exposes_native_controls_and_local_queue_without_cloud_submission(
        self,
    ):
        from streamlit.testing.v1 import AppTest
        from http_server_fixture import LiveServer

        cpu = {
            "packages": {"numpy": "fixture", "scikit-learn": "fixture"},
            "gpus": [],
            "cuda": False,
            "error": None,
            "system": "fixture",
            "python": "3.13",
            "mps": False,
            "ram_available_gib": 8,
        }
        profile = {**cpu, "packages": {**PACKAGES, "torch": "2.10.0+cpu"}}
        with LiveServer(self.home, keys()) as live:
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
                    "model_profiles": {
                        "d3-edge": {"training": profile, "serving": profile}
                    },
                },
            }.items():
                app.session_state[key] = value
            app.run(timeout=30)
            next(w for w in app.selectbox if w.label == "Training backend").select(
                "d3-edge"
            ).run(timeout=30)
            self.assertEqual(len(app.exception), 0, str(app.exception))
            self.assertEqual(
                next(w for w in app.radio if w.label == "Training location").options,
                ["This computer"],
            )
            self.assertFalse(any("LoRA rank" in w.label for w in app.number_input))
            next(w for w in app.selectbox if w.label == "D3 compute device").select(
                "cpu"
            ).run(timeout=30)
            next(
                w
                for w in app.number_input
                if w.label == "D3 maximum optimizer updates (0 uses all epochs)"
            ).set_value(1).run(timeout=30)
            next(w for w in app.button if w.label == "Queue training").click().run(
                timeout=30
            )
            self.assertEqual(len(app.exception), 0, str(app.exception))
            self.assertEqual(len(app.error), 0, str(app.error))
            job = next(
                job
                for job in self.store.jobs("A", self.project)
                if job["kind"] == "train"
            )
            self.assertEqual(job["config"]["backend"], "d3-edge")
            self.assertEqual(job["config"]["device"], "cpu")
            self.assertIn("d3_study", job["config"])
            self.assertFalse(job["config"]["download"])
            self.assertFalse(
                any(w.label == "Prepare remote training study" for w in app.button)
            )


if __name__ == "__main__":
    unittest.main()
