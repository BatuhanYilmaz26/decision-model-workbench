"""Omni format, immutable study and UI/worker boundaries without model downloads."""

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

from decision_workbench.data import dumps, options
from decision_workbench.training import liquid_omni_data as data
from decision_workbench.training.liquid_omni_local import (
    DEFAULTS,
    PROFILE,
    request_body,
    settings,
    validate_request,
)
from decision_workbench.training.liquid_omni_runtime import PACKAGES, checked_runtime
from decision_workbench.platform.liquid_omni_training import (
    checked_artifact,
    checked_report,
    study_for,
    recheck,
)
from decision_workbench.platform.model_environment import (
    model_python,
    liquid_environment,
)
from decision_workbench.platform.worker import Worker
from decision_workbench.ui.onboarding import local_backend_available
import test_liquid_platform as liquid_tests
from test_liquid_data import example
from test_platform import QUESTIONS, keys


class PromptFixture:
    def decision(self, state, question, max_length):
        return {
            "input_ids": [1, 2, 3, 4],
            "marker_positions": list(range(len(options(question)))),
            "question_type": {"choice": 0, "score": 1, "noul": 2}[question["type"]],
            "option_order": options(question),
        }


class OmniDataTests(unittest.TestCase):
    def test_soft_targets_four_roles_and_native_lineage_are_preserved(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            rows = {role: [example(i)] for i, role in enumerate(data.SPLITS)}
            rows["train"][0]["gold"]["supported"]["probabilities"] = {
                "false": 0.7,
                "true": 0.3,
            }
            for role, values in rows.items():
                (root / (role + ".jsonl")).write_text(
                    "".join(dumps(row) + "\n" for row in values), encoding="utf-8"
                )
            result = data.export_dataset(root, root / "export", PromptFixture())
            self.assertEqual(result["counts"], {role: 3 for role in data.SPLITS})
            for role in data.SPLITS:
                self.assertEqual(
                    (root / "export/canonical" / (role + ".jsonl")).read_bytes(),
                    (root / (role + ".jsonl")).read_bytes(),
                )
            native = [
                json.loads(line)
                for line in (root / "export/native/train.jsonl")
                .read_text()
                .splitlines()
            ]
            yesno = next(row for row in native if row["question_id"] == "supported")
            self.assertEqual(yesno["option_order"], ["false", "true"])
            self.assertEqual(yesno["target_probabilities"], [0.7, 0.3])
            lineage = [
                json.loads(line)
                for line in (root / "export/lineage/train.jsonl")
                .read_text()
                .splitlines()
            ]
            self.assertEqual(
                {row["question_id"] for row in lineage},
                set(rows["train"][0]["questions"]),
            )
            self.assertTrue(all(row["metadata"]["reviewed"] is True for row in lineage))
            with self.assertRaisesRegex(ValueError, "existing data"):
                data.export_dataset(root, root / "export", PromptFixture())
            rows["test"][0]["group_id"] = rows["train"][0]["group_id"]
            (root / "test.jsonl").write_text(dumps(rows["test"][0]) + "\n")
            with self.assertRaisesRegex(ValueError, "leakage"):
                data.export_dataset(root, root / "bad", PromptFixture())
            self.assertFalse((root / "bad").exists())

    def test_code_is_hash_checked_before_loading_any_model_or_tokenizer(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            (folder / "modeling_d1.py").write_text('raise AssertionError("unapproved")')
            with self.assertRaisesRegex(ValueError, "reviewed revision"):
                checked_runtime(folder)

    def test_native_head_settings_refuse_sft_options_and_invalid_values(self):
        self.assertEqual(settings({})["training_method"], "native-head")
        for changes in (
            {"rank": 4},
            {"training_method": "lora"},
            {"device": "mps"},
            {"max_length": True},
            {"max_length": 16385},
            {"learning_rate": float("nan")},
            {"learning_rate": 0},
            {"download": "yes"},
            {"max_steps": True},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                settings(changes)

    def test_omni_interpreter_caches_and_cpu_availability_are_separate(self):
        with tempfile.TemporaryDirectory() as name:
            env = {
                "DMT_OMNI_PYTHON": sys.executable,
                "DMT_WORKER_PYTHON": "other-worker",
                "DMT_LIQUID_CACHE": "not-omni",
            }
            self.assertEqual(
                model_python("liquid-omni", "training", env), sys.executable
            )
            self.assertEqual(
                model_python("liquid-omni", "serving", env), sys.executable
            )
            _, _, cache = liquid_environment(
                Path(name) / "workspace", env, backend="liquid-omni"
            )
            self.assertEqual(cache.name, "liquid-omni-hub")
        profile = {
            "packages": {**PACKAGES, "torch": "2.10.0+cpu"},
            "cuda": False,
            "ram_available_gib": 8,
        }
        capabilities = {
            "model_profiles": {
                "liquid-omni": {"training": profile, "serving": copy.deepcopy(profile)}
            }
        }
        self.assertTrue(local_backend_available("liquid-omni", capabilities)[0])
        capabilities["model_profiles"]["liquid-omni"]["serving"]["packages"][
            "transformers"
        ] = "unreviewed"
        self.assertFalse(local_backend_available("liquid-omni", capabilities)[0])


class OmniPlatformTests(unittest.TestCase):
    def setUp(self):
        liquid_tests.LiquidPlatformTests.setUp(self)
        self.config = {
            "snapshot": self.snapshot_id,
            **PROFILE,
            "batch_size": 2,
            "max_steps": 1,
            "max_length": 256,
        }

    def queue(self, config=None, key="omni-study-fixture"):
        return self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "train",
                "config": self.config if config is None else config,
                "request_key": key,
            },
        )

    def test_authenticated_study_binds_all_roles_and_retains_soft_targets(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["splits"]["train"][0]["gold"]["refund"]["probabilities"] = {
            "false": 0.25,
            "true": 0.75,
        }
        study_for(self.config, snapshot, QUESTIONS)
        response = self.queue()
        self.assertEqual(response.status_code, 202, response.text)
        config = response.json()["config"]
        self.assertEqual(config["omni_study"]["counts"], self.snapshot["counts"])
        self.assertEqual(config["training_method"], "native-head")
        self.assertTrue(config["release_policy_id"])
        self.assertTrue(self.store.has_model_training())
        self.assertEqual(self.queue().json()["id"], response.json()["id"])
        recheck(config, self.snapshot, QUESTIONS)
        config["omni_study"]["source_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "changed after queueing"):
            recheck(config, self.snapshot, QUESTIONS)

    def test_child_rechecks_counts_schema_snapshot_and_raw_file_hashes(self):
        study = study_for(self.config, self.snapshot, QUESTIONS)
        path = self.home / "omni-request.json"
        args = SimpleNamespace(**{k: study["configuration"][k] for k in DEFAULTS})

        def validate(value):
            path.write_bytes(request_body(value))
            return validate_request(
                path,
                args,
                study["data_sha256"],
                study["source_sha256"],
                self.snapshot["splits"],
            )

        self.assertEqual(
            validate(study), hashlib.sha256(request_body(study)).hexdigest()
        )
        for field in (
            "counts",
            "snapshot_sha256",
            "schema_sha256",
            "source_sha256",
            "data_sha256",
            "configuration",
            "snapshot",
        ):
            changed = copy.deepcopy(study)
            changed[field] = {} if isinstance(study[field], dict) else "0" * 64
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate(changed)

    def test_unsupported_options_tenant_access_and_remote_training_are_refused(self):
        for extra in (
            {"rank": 4},
            {"python": sys.executable},
            {"omni_study": {}},
            {"training_method": "sft"},
            {"model": "unreviewed/model"},
            {"device": "mps"},
        ):
            response = self.queue({**self.config, **extra})
            self.assertEqual(response.status_code, 400, response.text)
        denied = self.client.post(
            self.prefix + "/jobs",
            headers={"Authorization": "Bearer b-secret"},
            json={
                "kind": "train",
                "config": self.config,
                "request_key": "other-omni-tenant",
            },
        )
        self.assertEqual(denied.status_code, 404)
        response = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "remote-study",
                "config": {
                    **self.config,
                    "provider": "aws",
                    "region": "us-east-1",
                    "gpu": "ml.g5.xlarge",
                    "image_digest": "example/train@sha256:" + "a" * 64,
                },
                "request_key": "omni-remote-refusal",
            },
        )
        self.assertEqual(response.status_code, 400, response.text)

    def test_worker_source_tamper_and_queue_cancellation_register_nothing(self):
        queued = self.queue().json()
        worker = Worker(self.store)
        try:
            with patch(
                "decision_workbench.platform.liquid_omni_training.source_fingerprint",
                return_value="0" * 64,
            ):
                worker.once()
        finally:
            worker.service.runtime.close()
        job = self.store.job(queued["id"], "A", self.project)
        self.assertEqual(job["status"], "failed", job)
        self.assertIn("changed after queueing", job["error"])
        queued = self.queue(key="omni-cancellation").json()
        self.assertEqual(
            self.client.post(
                self.prefix + "/jobs/" + queued["id"] + "/cancel"
            ).status_code,
            200,
        )
        self.assertFalse(self.store.has_model_training())
        self.assertEqual(self.store.list("model", "A", self.project), [])

    def test_worker_uses_selected_native_child_and_never_passes_lora_options(self):
        queued = self.queue().json()
        called = []

        def child(argv, **kwargs):
            called.append((argv, kwargs))
            return SimpleNamespace(poll=lambda: 1, returncode=1)

        worker = Worker(self.store)
        try:
            with (
                patch.dict(os.environ, {"DMT_OMNI_PYTHON": sys.executable}),
                patch(
                    "decision_workbench.platform.worker.subprocess.Popen",
                    side_effect=child,
                ),
            ):
                worker.once()
        finally:
            worker.service.runtime.close()
        self.assertEqual(len(called), 1)
        argv, environment = called[0]
        self.assertEqual(argv[0], sys.executable)
        self.assertIn("decision_workbench.training.liquid_omni_runner", argv)
        self.assertIn("--local-study-request", argv)
        self.assertNotIn("--rank", argv)
        self.assertNotIn("--model", argv)
        self.assertNotIn("--download", argv)
        self.assertEqual(
            environment["env"]["PYTHONPATH"], str(Path(__file__).resolve().parents[1])
        )
        self.assertEqual(
            self.store.job(queued["id"], "A", self.project)["status"], "failed"
        )

    def test_report_updates_and_unapproved_code_are_checked_before_admission(self):
        study = study_for(self.config, self.snapshot, QUESTIONS)
        config = {
            **study["configuration"],
            "snapshot": self.snapshot_id,
            "omni_study": study,
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
                "method": "native-head",
                "optimizer_updates": 1,
                "max_parameter_delta": 0.001,
                "frozen_parameters_unchanged": True,
                "frozen_parameters_sha256": "a" * 64,
                "dtype": "float32",
            },
        }
        checked_report(report, config)
        overwritten = copy.deepcopy(report)
        overwritten["calibration"] = {
            name: {"accuracy": 1.0, "nll": 0.0} for name in QUESTIONS
        }
        with self.assertRaisesRegex(ValueError, "separate from calibration metrics"):
            checked_report(overwritten, config)
        bundle = self.home / "bad-artifact"
        bundle.mkdir()
        (bundle / "unapproved.py").write_text('raise AssertionError("must not run")')
        with self.assertRaisesRegex(ValueError, "unapproved native code"):
            checked_artifact(bundle, report, config, QUESTIONS)
        for change in (
            {"optimizer_updates": 0},
            {"max_parameter_delta": float("nan")},
            {"frozen_parameters_unchanged": False},
            {"method": "random-head"},
        ):
            bad = copy.deepcopy(report)
            bad["training"].update(change)
            with self.assertRaises(ValueError):
                checked_report(bad, config)

    def test_browser_queues_native_head_settings_without_provider_keys_or_cloud_controls(
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
        }
        model = {
            **cpu,
            "packages": {**PACKAGES, "torch": "2.10.0+cpu"},
            "cuda": False,
            "ram_available_gib": 12,
        }
        with LiveServer(self.home, keys()) as live:
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
            for key, value in {
                "credential": "a-secret",
                "api_url": live.url,
                "principal": liquid_tests.OWNER,
                "connection_mode": "shared",
                "runtime-capabilities": {
                    "cpu_baseline": True,
                    "teacher_configured": False,
                    "runtimes": {"training": cpu, "serving": cpu},
                    "model_profiles": {
                        "liquid-omni": {"training": model, "serving": model}
                    },
                },
            }.items():
                app.session_state[key] = value
            app.run(timeout=30)
            self.assertEqual(len(app.exception), 0, str(app.exception))
            self.assertIn(
                "Training backend",
                [w.label for w in app.selectbox],
                str(
                    {
                        "errors": [w.value for w in app.error],
                        "warnings": [w.value for w in app.warning],
                        "selectboxes": [w.label for w in app.selectbox],
                    }
                ),
            )
            next(w for w in app.selectbox if w.label == "Training backend").select(
                "liquid-omni"
            ).run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(
                next(w for w in app.radio if w.label == "Training location").options,
                ["This computer"],
            )
            self.assertFalse(any("LoRA rank" in w.label for w in app.number_input))
            next(
                w
                for w in app.number_input
                if w.label == "Omni maximum optimizer updates (0 uses all epochs)"
            ).set_value(1).run(timeout=30)
            next(w for w in app.button if w.label == "Queue training").click().run(
                timeout=30
            )
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.error), 0)
            jobs = self.store.jobs("A", self.project)
            config = next(job for job in jobs if job["kind"] == "train")["config"]
            self.assertEqual(config["backend"], "liquid-omni")
            self.assertIn("omni_study", config)
            self.assertNotIn("rank", config)
            self.assertFalse(config["download"])


if __name__ == "__main__":
    unittest.main()
