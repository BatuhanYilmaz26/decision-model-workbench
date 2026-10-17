"""Liquid job bindings and interpreter boundaries; no model weights in CI."""

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from decision_workbench.data import jsonl
from decision_workbench.platform.api import create_app
from decision_workbench.platform.worker import Worker
from decision_workbench.platform.model_environment import (
    liquid_environment,
    model_python,
)
from decision_workbench.platform.liquid_training import (
    checked_artifact,
    checked_report,
    recheck,
    study_for,
)
from decision_workbench.training.liquid_local import (
    DEFAULTS,
    PROFILE,
    request_body,
    validate_request,
)
from decision_workbench.ui.onboarding import local_backend_available
from test_platform import QUESTIONS, fixture_rows, keys

OWNER = {"tenant": "A", "actor": "owner", "role": "admin"}


class LiquidPlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.app = create_app(self.home, keys())
        self.service, self.store = self.app.state.service, self.app.state.store
        self.addCleanup(self.service.runtime.close)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.client.headers.update({"Authorization": "Bearer a-secret"})
        self.project = self.client.post(
            "/v1/projects",
            json={
                "name": "Liquid contract",
                "description": "Fictional routing/refund/rubric mechanics policy",
                "questions": QUESTIONS,
            },
        ).json()["id"]
        self.prefix = "/v1/projects/" + self.project
        reviewed = self.service.save_dataset(
            OWNER, self.project, fixture_rows(180), {"fictional_fixture": True}
        )
        prepared = self.service.submit(
            OWNER,
            self.project,
            "prepare",
            {"datasets": [reviewed["id"]]},
            "prepare-liquid-fixture",
        )
        worker = Worker(self.store)
        try:
            worker.once()
        finally:
            worker.service.runtime.close()
        job = self.store.job(prepared["id"], "A", self.project)
        self.assertEqual(job["status"], "succeeded", job)
        self.snapshot_id = job["result"]["snapshot"]
        self.snapshot = self.store.get(self.snapshot_id, "A", self.project, "snapshot")[
            "payload"
        ]
        policy = self.client.put(
            self.prefix + "/release-policy",
            json={
                "min_accuracy": 0.0,
                "min_macro_f1": 0.0,
                "max_ece": 1.0,
                "min_test_rows": 10,
            },
        )
        self.assertEqual(policy.status_code, 200, policy.text)
        self.config = {
            "snapshot": self.snapshot_id,
            **PROFILE,
            "rank": 4,
            "max_steps": 8,
        }

    def queue(self, config=None, key="liquid-study-fixture"):
        return self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "train",
                "config": self.config if config is None else config,
                "request_key": key,
            },
        )

    def test_authenticated_job_freezes_native_inputs_source_settings_and_policy(self):
        response = self.queue()
        self.assertEqual(response.status_code, 202, response.text)
        job = response.json()
        frozen = self.store.job(job["id"], "A", self.project)["config"]
        study = frozen["liquid_study"]
        self.assertEqual(study["counts"], self.snapshot["counts"])
        self.assertTrue(frozen["release_policy_id"])
        self.assertEqual(
            study["data_sha256"],
            {
                name: hashlib.sha256(jsonl(rows).encode()).hexdigest()
                for name, rows in self.snapshot["splits"].items()
            },
        )
        self.assertEqual(study["configuration"]["model"], PROFILE["model"])
        self.assertFalse(study["configuration"]["download"])
        self.assertEqual(self.queue().json()["id"], job["id"])
        self.assertTrue(self.store.has_model_training())
        recheck(frozen, self.snapshot, QUESTIONS)
        changed = copy.deepcopy(frozen)
        changed["liquid_study"]["source_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "changed after queueing"):
            recheck(changed, self.snapshot, QUESTIONS)

    def test_request_cannot_select_an_executable_or_replace_the_frozen_contract(self):
        for extra in (
            {"python": sys.executable},
            {"cache": "elsewhere"},
            {"liquid_study": {}},
            {"train_encoder": True},
            {"full_precision": True},
            {"gate_snapshot": "0" * 32},
        ):
            response = self.queue({**self.config, **extra})
            self.assertEqual(response.status_code, 400, response.text)
        for changed in (
            {"model": "somewhere/unreviewed"},
            {"revision": "0" * 40},
            {"rank": True},
            {"download": "yes"},
            {"device": "mps"},
        ):
            response = self.queue({**self.config, **changed})
            self.assertEqual(response.status_code, 400, response.text)
        denied = self.client.post(
            self.prefix + "/jobs",
            headers={"Authorization": "Bearer b-secret"},
            json={
                "kind": "train",
                "config": self.config,
                "request_key": "other-tenant-liquid",
            },
        )
        self.assertEqual(denied.status_code, 404)

    def test_soft_training_targets_and_too_small_calibration_are_refused(self):
        for kind in ("soft", "short"):
            snapshot = copy.deepcopy(self.snapshot)
            if kind == "soft":
                snapshot["splits"]["train"][0]["gold"]["team"]["probabilities"] = {
                    "billing": 0.2,
                    "engineering": 0.8,
                }
            else:
                snapshot["splits"]["calibration"] = snapshot["splits"]["calibration"][
                    :7
                ]
            with self.assertRaises(ValueError):
                study_for(self.config, snapshot, QUESTIONS)

    def test_model_child_must_match_all_frozen_training_values_and_hashes(self):
        study = study_for(self.config, self.snapshot, QUESTIONS)
        path = self.home / "study.json"
        path.write_bytes(request_body(study))
        args = SimpleNamespace(**{key: study["configuration"][key] for key in DEFAULTS})
        expected = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(
            validate_request(path, args, study["data_sha256"], study["source_sha256"]),
            expected,
        )
        args.rank += 1
        with self.assertRaisesRegex(ValueError, "configuration differs"):
            validate_request(path, args, study["data_sha256"], study["source_sha256"])
        args.rank -= 1
        with self.assertRaisesRegex(ValueError, "input hashes differ"):
            validate_request(path, args, {}, study["source_sha256"])

    def test_changed_worker_source_fails_the_job_before_model_start(self):
        queued = self.queue().json()
        worker = Worker(self.store)
        try:
            with patch(
                "decision_workbench.platform.liquid_training.source_fingerprint",
                return_value="0" * 64,
            ):
                worker.once()
        finally:
            worker.service.runtime.close()
        job = self.store.job(queued["id"], "A", self.project)
        self.assertEqual(job["status"], "failed", job)
        self.assertIn("changed after queueing", job["error"])
        self.assertEqual(self.store.list("model", "A", self.project), [])
        self.assertFalse(self.store.has_model_training())

    def test_queued_cancellation_releases_training_priority_without_registering_a_model(
        self,
    ):
        queued = self.queue().json()
        self.assertTrue(self.store.has_model_training())
        cancelled = self.client.post(self.prefix + "/jobs/" + queued["id"] + "/cancel")
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertFalse(self.store.has_model_training())
        self.assertEqual(self.store.list("model", "A", self.project), [])

    def test_report_must_match_request_and_successful_optimizer_accounting(self):
        study = study_for(self.config, self.snapshot, QUESTIONS)
        config = {**self.config, "liquid_study": study}
        report = {
            **PROFILE,
            "source_sha256": study["source_sha256"],
            "data_sha256": study["data_sha256"],
            "counts": study["counts"],
            "local_study_sha256": hashlib.sha256(request_body(study)).hexdigest(),
            "schema_conditioned": True,
            "configuration": study["configuration"],
            "training": {
                "optimizer_updates": 6,
                "optimizer_attempts": 8,
                "optimizer_skipped": 2,
                "max_parameter_delta": 0.001,
                "vision_frozen": True,
            },
        }
        checked_report(report, config)
        bundle = self.home / "bundle"
        bundle.mkdir()
        (bundle / "unapproved.py").write_text(
            "raise AssertionError('must not execute')"
        )
        with self.assertRaisesRegex(ValueError, "unapproved model code"):
            checked_artifact(bundle, report, config, QUESTIONS)
        for edit in (
            lambda r: r.update(local_study_sha256="0" * 64),
            lambda r: r["training"].update(optimizer_updates=8),
            lambda r: r["training"].update(max_parameter_delta=float("nan")),
            lambda r: r.update(counts={}),
        ):
            bad = copy.deepcopy(report)
            edit(bad)
            with self.assertRaises(ValueError):
                checked_report(bad, config)

    def test_browser_queues_liquid_with_a_separate_profile_and_native_controls(self):
        self.browser_queue("liquid-trl")

    def test_browser_keeps_the_selected_unsloth_backend_and_disallows_cpu(self):
        self.browser_queue("liquid-unsloth")

    def browser_queue(self, backend):
        from streamlit.testing.v1 import AppTest
        from http_server_fixture import LiveServer
        from decision_workbench.training.liquid_local import (
            PINNED_PACKAGES,
            UNSLOTH_PACKAGES,
        )

        cpu = {
            "system": "fixture",
            "python": "3.13",
            "cuda": False,
            "mps": False,
            "gpus": [],
            "error": None,
            "packages": {"scikit-learn": "fixture", "numpy": "fixture"},
        }
        model = {
            **cpu,
            "python": "3.12",
            "cuda": True,
            "packages": {
                **(
                    UNSLOTH_PACKAGES if backend == "liquid-unsloth" else PINNED_PACKAGES
                ),
                "torch": "2.10.0+cu126",
                "triton-windows": "3.6.0.post26",
            },
        }
        with LiveServer(self.home, keys()) as live:
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
            for key, value in dict(
                credential="a-secret",
                api_url=live.url,
                principal=OWNER,
                connection_mode="shared",
            ).items():
                app.session_state[key] = value
            app.session_state["runtime-capabilities"] = {
                "cpu_baseline": True,
                "teacher_configured": False,
                "runtimes": {"training": cpu, "serving": cpu},
                "model_profiles": {backend: {"training": model, "serving": model}},
            }
            app.run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            next(w for w in app.selectbox if w.label == "Training backend").select(
                backend
            ).run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertFalse(
                any(w.label == "Prepare remote training study" for w in app.button)
            )
            if backend == "liquid-unsloth":
                devices = next(
                    w for w in app.selectbox if w.label == "Liquid compute device"
                )
                self.assertNotIn("CPU (at least 20 GiB available RAM)", devices.options)
            self.assertEqual(
                next(w for w in app.radio if w.label == "Training location").options,
                ["This computer"],
            )
            next(
                w
                for w in app.number_input
                if w.label == "Liquid maximum optimizer attempts (0 uses all epochs)"
            ).set_value(8).run(timeout=30)
            button = next(w for w in app.button if w.label == "Queue training")
            self.assertFalse(button.disabled)
            button.click().run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.error), 0)
            with self.store.transaction() as db:
                rows = db.execute(
                    "SELECT config FROM jobs WHERE kind='train'"
                ).fetchall()
            self.assertEqual(len(rows), 1)
            config = json.loads(rows[0]["config"])
            self.assertEqual(config["backend"], backend)
            self.assertEqual(config["rank"], 4)
            self.assertEqual(config["max_steps"], 8)
            self.assertFalse(config["download"])
            self.assertIn("liquid_study", config)

    def test_unsloth_study_cannot_be_run_or_admitted_as_trl(self):
        response = self.queue(
            {**self.config, "backend": "liquid-unsloth", "device": "cuda"}
        )
        self.assertEqual(response.status_code, 202, response.text)
        config = response.json()["config"]
        self.assertEqual(
            config["liquid_study"]["configuration"]["backend"], "liquid-unsloth"
        )
        self.assertTrue(self.store.has_model_training())
        bad = self.queue(
            {**self.config, "backend": "liquid-unsloth", "device": "cpu"},
            key="unsloth-cpu-refusal",
        )
        self.assertEqual(bad.status_code, 400, bad.text)
        with self.assertRaisesRegex(ValueError, "frozen local study"):
            checked_report({"backend": "liquid-trl"}, config)


class LiquidEnvironmentTests(unittest.TestCase):
    def test_capability_probe_deduplicates_shared_interpreters_across_profiles(self):
        from decision_workbench.platform import capabilities

        value = {"packages": {"numpy": "fixture", "scikit-learn": "fixture"}}
        with (
            patch.dict(
                os.environ,
                {
                    "DMT_WORKER_PYTHON": sys.executable,
                    "DMT_MODEL_PYTHON": sys.executable,
                    "DMT_LIQUID_PYTHON": sys.executable,
                    "DMT_LIQUID_UNSLOTH_PYTHON": sys.executable,
                },
            ),
            patch.object(capabilities, "_cached", None),
            patch.object(
                capabilities.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0, stdout=json.dumps(value)),
            ) as probe,
        ):
            result = capabilities.runtime_capabilities()
            self.assertEqual(probe.call_count, 1)
            self.assertTrue(result["cpu_baseline"])
            self.assertEqual(set(result["runtimes"]), {"training", "serving"})
            self.assertEqual(
                set(result["model_profiles"]),
                {"liquid-trl", "liquid-unsloth", "liquid-omni"},
            )

    def test_unsloth_interpreter_and_compile_cache_are_independent(self):
        with tempfile.TemporaryDirectory() as name:
            interpreter = Path(name) / "python.exe"
            interpreter.touch()
            env = {
                "DMT_LIQUID_PYTHON": sys.executable,
                "DMT_LIQUID_UNSLOTH_PYTHON": str(interpreter),
            }
            self.assertEqual(
                model_python("liquid-unsloth", "training", env), str(interpreter)
            )
            self.assertEqual(
                model_python("liquid-unsloth", "serving", env), str(interpreter)
            )
            self.assertEqual(
                model_python("liquid-trl", "training", env), sys.executable
            )
            child, _, cache = liquid_environment(
                Path(name) / "workspace", env, backend="liquid-unsloth"
            )
            self.assertEqual(
                Path(child["UNSLOTH_COMPILE_LOCATION"]),
                cache.parent / "liquid-unsloth-compiled",
            )

    def test_liquid_interpreter_selection_does_not_replace_legacy_environments(self):
        env = {
            "DMT_LIQUID_PYTHON": sys.executable,
            "DMT_WORKER_PYTHON": "legacy-worker",
            "DMT_MODEL_PYTHON": "legacy-model",
        }
        self.assertEqual(model_python("liquid-trl", "training", env), sys.executable)
        self.assertEqual(model_python("liquid-trl", "serving", env), sys.executable)
        self.assertEqual(model_python("unsloth", "training", env), "legacy-worker")
        self.assertEqual(model_python("laya-native", "serving", env), "legacy-model")
        with self.assertRaises(ValueError):
            model_python(
                "liquid-trl", "training", {"DMT_LIQUID_PYTHON": "relative-python"}
            )

    def test_child_binds_source_and_private_caches(self):
        with tempfile.TemporaryDirectory() as name:
            environment, root, cache = liquid_environment(
                Path(name) / "workspace",
                {
                    "PYTHONPATH": "unrelated",
                    "DMT_LIQUID_CACHE": str(Path(name) / "cache"),
                },
            )
            self.assertEqual(environment["PYTHONPATH"], str(root))
            self.assertEqual(cache, Path(name) / "cache")
            for key in ("HF_MODULES_CACHE", "TRITON_CACHE_DIR", "CUDA_CACHE_PATH"):
                self.assertTrue(Path(environment[key]).is_relative_to(Path(name)))

    def test_browser_availability_checks_both_liquid_profiles_and_keeps_cpu_baseline(
        self,
    ):
        from decision_workbench.training.liquid_local import PINNED_PACKAGES

        profile = {
            "packages": {
                **PINNED_PACKAGES,
                "torch": "2.10.0+cu126",
                "triton-windows": "3.6.0.post26",
            },
            "cuda": True,
            "error": None,
        }
        capability = {
            "cpu_baseline": True,
            "model_profiles": {
                "liquid-trl": {"training": profile, "serving": copy.deepcopy(profile)}
            },
        }
        self.assertTrue(local_backend_available("liquid-trl", capability)[0])
        capability["model_profiles"]["liquid-trl"]["serving"]["packages"][
            "transformers"
        ] = "5.17.0"
        self.assertFalse(local_backend_available("liquid-trl", capability)[0])
        self.assertTrue(local_backend_available("cpu-baseline", capability)[0])


if __name__ == "__main__":
    unittest.main()
