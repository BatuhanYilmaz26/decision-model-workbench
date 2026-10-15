"""Authenticated Vega job contracts; CPU fixtures do not qualify GPU/domain quality."""

import copy
import hashlib
import json
import shutil
import socket
import tempfile
import threading
import time
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from decision_workbench.data import jsonl, write_splits
from decision_workbench.platform.api import create_app
from decision_workbench.platform.store import Store
from decision_workbench.platform.worker import Worker
from decision_workbench.platform.vega_training import (
    checked_report,
    release_gates,
    validate_policy,
)
from decision_workbench.platform.recovery import backup_workspace, restore_workspace
from decision_workbench.training.artifacts import write_manifest
from decision_workbench.training.contracts import load_splits
from decision_workbench.training.metrics import normalize_answers
from decision_workbench.training.resources import ResourceLease, ResourceBusy
from decision_workbench.training.vega_contracts import load_gate_splits
from decision_workbench.training.vega_local import (
    PROFILE,
    DEFAULTS,
    request_body,
    validate_request,
)
from decision_workbench.ui.vega import applicability_rows
from test_gate_platform import gate_rows
from test_platform import QUESTIONS, fixture_rows, keys

OWNER = {"tenant": "A", "actor": "owner", "role": "admin"}
REVIEWER = {"tenant": "A", "actor": "reviewer", "role": "reviewer"}
LIMITS = {
    "min_positive_accepted_fraction": 0.5,
    "max_negative_false_open_rate": 0.2,
    "min_negative_test_rows": 10,
}


def reserved(service, project, count=600):
    """Reviewed positive internal fixture; negative history uses actual review CAS."""
    store = service.store
    positive = service.save_dataset(
        OWNER, project, fixture_rows(count), {"fictional_fixture": True}
    )
    job = service.submit(
        OWNER,
        project,
        "prepare",
        {"datasets": [positive["id"]]},
        "positive-fixture-" + str(count),
    )
    worker = Worker(store)
    try:
        worker.once()
        result = store.job(job["id"], "A", project)
        if result["status"] != "succeeded":
            raise AssertionError(result)
        snapshot = result["result"]["snapshot"]
        negative = service.gates.save(
            OWNER, project, gate_rows(100), {"fictional_fixture": True}
        )
        for row in gate_rows(100):
            negative = service.gates.review(
                REVIEWER,
                project,
                negative["id"],
                row["id"],
                row["negative_for"],
                row["reason"],
                "approved",
                negative["payload"]["sha256"],
                "Fictional mechanics fixture only",
            )
        job = service.submit(
            OWNER,
            project,
            "gate-prepare",
            {"snapshot": snapshot, "datasets": [negative["id"]]},
            "negative-fixture-" + str(count),
        )
        worker.once()
        result = store.job(job["id"], "A", project)
        if result["status"] != "succeeded":
            raise AssertionError(result)
        return snapshot, result["result"]["gate_snapshot"]
    finally:
        worker.service.runtime.close()


def fixture_report(config, positive, negative):
    return {
        "backend": "vega",
        **PROFILE,
        "configuration": {key: config[key] for key in DEFAULTS},
        "source_sha256": config["vega_contract"]["source_sha256"],
        "data_sha256": config["vega_contract"]["data_sha256"],
        "gate_data_sha256": config["vega_contract"]["gate_data_sha256"],
        "local_study_sha256": hashlib.sha256(request_body(config).encode()).hexdigest(),
        "feature_cache": config["vega_contract"]["preflight"],
        "reload_equivalence": True,
        "export_qualification": {"passed": True, "same_uncertainty": True},
        "counts": {role: len(rows) for role, rows in positive.items()},
        "gate_counts": {role: len(rows) for role, rows in negative.items()},
        "test": {
            name: {
                "accuracy": 0.9,
                "macro_f1": 0.9,
                "ece_10_equal_width_bins": 0.01,
                "count": len(positive["test"]),
                "test_class_counts": {"a": 10, "b": 10},
            }
            for name in QUESTIONS
        },
        "applicability": {
            role: {
                name: {
                    "positive": {
                        "count": len(positive[role]),
                        "open_rate": 1.0,
                        "error_rate": 0.0,
                    },
                    "negative": {
                        "count": sum(name in r["negative_for"] for r in negative[role]),
                        "open_rate": 0.0,
                        "error_rate": 0.0,
                    },
                    "positive_accepted_fraction": 0.8,
                }
                for name in QUESTIONS
            }
            for role in ("validation", "calibration", "test")
        },
    }


class VegaPlatformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.TemporaryDirectory()
        cls.template = Path(cls.base.name) / "template"
        app = create_app(cls.template, keys())
        service = app.state.service
        cls.project = service.create_project(
            OWNER, "Vega support", "Fictional routing mechanics only", QUESTIONS
        )["id"]
        cls.snapshot, cls.gate = reserved(service, cls.project)
        service.runtime.close()

    @classmethod
    def tearDownClass(cls):
        cls.base.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        shutil.copytree(self.template, self.home)
        self.app = create_app(self.home, keys())
        self.service = self.app.state.service
        self.store = self.app.state.store
        self.client = TestClient(self.app)
        self.client.headers.update({"Authorization": "Bearer a-secret"})
        self.prefix = "/v1/projects/" + self.project

    def tearDown(self):
        self.service.runtime.close()
        self.client.close()
        self.temp.cleanup()

    def policy(self, vega=LIMITS):
        existing = self.client.get(self.prefix + "/release-policy").json()["policy"]
        response = self.client.put(
            self.prefix + "/release-policy",
            json={
                "min_accuracy": 0,
                "min_macro_f1": 0,
                "max_ece": 1,
                "min_test_rows": 10,
                "min_training_examples_per_class": 0,
                "min_calibration_examples_per_class": 0,
                "vega": vega,
                "expected_id": existing["id"] if existing else None,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def submit(self, config=None, key="vega-training-fixture"):
        return self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "train",
                "config": config
                or {
                    "backend": "vega",
                    "snapshot": self.snapshot,
                    "gate_snapshot": self.gate,
                    "offline": True,
                    "max_steps": 2,
                },
                "request_key": key,
            },
        )

    def frozen(self):
        self.policy()
        response = self.submit()
        self.assertEqual(response.status_code, 202, response.text)
        return self.store.job(response.json()["id"], "A", self.project)

    def inputs(self):
        return (
            self.store.get(self.snapshot, "A", self.project, "snapshot")["payload"][
                "splits"
            ],
            self.service.gates.verify_snapshot("A", self.project, self.gate)["splits"],
        )

    def test_frozen_scoped_idempotent_request_and_policy_lock(self):
        job = self.frozen()
        self.assertEqual(self.submit().json()["id"], job["id"])
        self.assertTrue(self.store.has_model_training())
        binding = job["config"]["vega_contract"]
        self.assertEqual(
            binding["preflight"]["calibration_partition"]["family_counts"],
            {"probability_fit": 30, "conformal": 30},
        )
        self.assertEqual((binding["tenant"], binding["project"]), ("A", self.project))
        for token, expected in (
            ("b-secret", 404),
            ("infer-secret", 403),
            ("review-secret", 403),
        ):
            response = self.client.post(
                self.prefix + "/jobs",
                headers={"Authorization": "Bearer " + token},
                json={
                    "kind": "train",
                    "config": {
                        "backend": "vega",
                        "snapshot": self.snapshot,
                        "gate_snapshot": self.gate,
                    },
                    "request_key": "unauthorized-" + token,
                },
            )
            self.assertEqual(response.status_code, expected, response.text)
        response = self.client.put(
            self.prefix + "/release-policy",
            json={"vega": dict(LIMITS, min_positive_accepted_fraction=0.1)},
        )
        self.assertEqual(response.status_code, 409)

    def test_missing_policy_and_backend_options_refuse_remote_study_freezes(self):
        self.policy(None)
        self.assertEqual(self.submit().status_code, 400)
        self.policy()
        base = {
            "backend": "vega",
            "snapshot": self.snapshot,
            "gate_snapshot": self.gate,
        }
        for change in (
            {"grad_accum": 4},
            {"model": "other/model"},
            {"vega_contract": {}},
            {"backend": "unsloth"},
        ):
            with self.subTest(change=change):
                self.assertEqual(
                    self.submit(
                        {**base, **change}, "bad-options-" + str(len(change))
                    ).status_code,
                    400,
                )
        response = self.client.post(
            self.prefix + "/jobs",
            json={
                "kind": "remote-study",
                "config": {
                    **base,
                    "provider": "aws",
                    "region": "us-east-1",
                    "gpu": "ml.g5.xlarge",
                    "image_digest": "image@sha256:" + "a" * 64,
                },
                "request_key": "remote-vega-refusal",
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["config"]["backend"], "vega")

    def test_malformed_references_are_refused_before_database_access(self):
        self.policy()
        base = {
            "backend": "vega",
            "snapshot": self.snapshot,
            "gate_snapshot": self.gate,
        }
        for name in ("snapshot", "gate_snapshot"):
            for index, value in enumerate(
                ([], {"value": "reference"}, True, 1, None, "not-a-reference")
            ):
                with self.subTest(name=name, value=value):
                    response = self.submit(
                        {**base, name: value}, "invalid-reference-" + name + str(index)
                    )
                    self.assertEqual(response.status_code, 400, response.text)
        for name in ("snapshot", "gate_snapshot"):
            missing = dict(base)
            missing.pop(name)
            response = self.submit(missing, "missing-reference-" + name)
            self.assertEqual(response.status_code, 400, response.text)
        self.assertFalse(self.store.has_model_training())

    def test_worker_applies_negative_floor_from_atomically_frozen_owner_policy(self):
        self.policy()
        old = self.store.release_policy("A", self.project)["policy"]
        submit = self.store.submit

        def race(*args, **kwargs):
            self.service.configure_release_policy(
                OWNER,
                self.project,
                0,
                0,
                1,
                10,
                old["id"],
                0,
                0,
                vega=dict(LIMITS, min_negative_test_rows=11),
            )
            return submit(*args, **kwargs)

        with patch.object(self.store, "submit", side_effect=race):
            response = self.submit()
        self.assertEqual(response.status_code, 202, response.text)
        job = self.store.claim()
        policy = self.store.get(
            job["config"]["release_policy_id"], "A", self.project, "release-policy"
        )
        self.assertEqual(
            policy["payload"]["criteria"]["vega"]["min_negative_test_rows"], 11
        )
        worker = Worker(self.store)
        try:
            with (
                patch("importlib.util.find_spec", return_value=object()),
                patch(
                    "subprocess.Popen",
                    side_effect=AssertionError("Model work must not begin"),
                ) as child,
            ):
                worker.execute(job)
                child.assert_not_called()
            finished = self.store.job(job["id"], "A", self.project)
            self.assertEqual(finished["status"], "failed")
            self.assertIn("negative test rows", finished["error"])
            self.assertFalse((self.home / "artifacts").exists())
        finally:
            worker.service.runtime.close()

    def test_small_calibration_and_negative_test_floor_refuse_before_job(self):
        self.policy(dict(LIMITS, min_negative_test_rows=11))
        response = self.submit()
        self.assertEqual(response.status_code, 400)
        self.assertIn("negative test rows", response.text)
        self.policy()
        snapshot, gate = reserved(self.service, self.project, 180)
        response = self.submit(
            {"backend": "vega", "snapshot": snapshot, "gate_snapshot": gate},
            "small-calibration",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("sixty independent", response.text)
        self.assertFalse(self.store.has_model_training())

    def test_worker_rechecks_source_and_mismatched_pair_before_model_work(self):
        self.frozen()
        job = self.store.claim()
        worker = Worker(self.store)
        job["config"]["vega_contract"]["source_sha256"] = "0" * 64
        try:
            with patch("subprocess.Popen") as child:
                with self.assertRaisesRegex(ValueError, "study source"):
                    worker.dispatch(job, threading.Event())
                child.assert_not_called()
            self.assertFalse((self.home / "artifacts").exists())
            self.store.finish(job, error="Deliberately failed source-binding fixture")
        finally:
            worker.service.runtime.close()
        other, _ = reserved(self.service, self.project, 180)
        response = self.submit(
            {"backend": "vega", "snapshot": other, "gate_snapshot": self.gate},
            "crossed-reservation",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("different positive snapshot", response.text)

    def test_local_request_and_export_bind_all_eight_inputs_and_options(self):
        job = self.frozen()
        config = job["config"]
        positive, negative = self.inputs()
        folder = self.home / "local-fixture"
        write_splits(positive, folder)
        for role, rows in positive.items():
            (folder / (role + ".jsonl")).write_bytes(jsonl(rows).encode("utf-8"))
        for role, rows in negative.items():
            (folder / ("gate_" + role + ".jsonl")).write_bytes(
                jsonl(rows).encode("utf-8")
            )
        _, hashes = load_splits(folder)
        _, negative_hashes = load_gate_splits(folder, positive)
        path = folder / "request.json"
        path.write_text(request_body(config), encoding="utf-8")
        args = Namespace(
            **{key: config[key] for key in PROFILE | DEFAULTS},
            local_study_request=str(path),
        )
        preflight = config["vega_contract"]["preflight"]
        self.assertEqual(
            validate_request(args, hashes, negative_hashes, QUESTIONS, preflight),
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for change in ("options", "negative", "source", "schema"):
            with self.subTest(change=change):
                bad_args, bad_hashes, bad_questions = (
                    copy.copy(args),
                    copy.deepcopy(negative_hashes),
                    copy.deepcopy(QUESTIONS),
                )
                if change == "options":
                    bad_args.rank += 1
                elif change == "negative":
                    bad_hashes["test"] = "0" * 64
                elif change == "source":
                    changed = copy.deepcopy(config)
                    changed["vega_contract"]["source_sha256"] = "0" * 64
                    path.write_text(request_body(changed), encoding="utf-8")
                else:
                    bad_questions["team"]["instructions"] += " Changed"
                with self.assertRaises(ValueError):
                    validate_request(
                        bad_args, hashes, bad_hashes, bad_questions, preflight
                    )
                path.write_text(request_body(config), encoding="utf-8")
        report = fixture_report(config, positive, negative)
        checked_report(report, config)
        for key in (
            "local_study_sha256",
            "source_sha256",
            "backbone_revision",
            "gate_data_sha256",
            "feature_cache",
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                checked_report({**report, key: None}, config)

    def test_release_refuses_bad_coverage_negative_rate_count_and_nan(self):
        config = self.frozen()["config"]
        report = fixture_report(config, *self.inputs())
        release_gates(report, {"vega": LIMITS}, QUESTIONS)
        for change in ("coverage", "negative_rate", "count", "nan", "inconsistent"):
            bad = copy.deepcopy(report)
            value = bad["applicability"]["test"]["team"]
            if change == "coverage":
                value["positive_accepted_fraction"] = 0
            elif change == "negative_rate":
                value["negative"].update(open_rate=0.3, error_rate=0.3)
            elif change == "count":
                value["negative"]["count"] = 9
            elif change == "nan":
                value["positive_accepted_fraction"] = float("nan")
            else:
                value["negative"]["open_rate"] = 0.3
            with self.subTest(change=change), self.assertRaises(ValueError):
                release_gates(bad, {"vega": LIMITS}, QUESTIONS)
        for value in (
            None,
            {**LIMITS, "extra": 1},
            dict(LIMITS, max_negative_false_open_rate=float("nan")),
            dict(LIMITS, min_negative_test_rows=True),
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_policy(value)

    def test_applicability_table_separates_gate_opening_and_accepted_fraction(self):
        config = self.frozen()["config"]
        report = fixture_report(config, *self.inputs())
        report["applicability"]["test"]["urgency"]["positive_accepted_fraction"] = 0
        rows = {row["Question"]: row for row in applicability_rows(report, LIMITS)}
        self.assertEqual(rows["urgency"]["Positive gate open rate"], 1)
        self.assertEqual(rows["urgency"]["Positive accepted fraction"], 0)
        self.assertEqual(rows["urgency"]["Applicability gates"], "Fail")
        self.assertEqual(rows["team"]["Applicability gates"], "Pass")
        self.assertEqual(rows["team"]["Inapplicable test examples"], 10)
        self.assertEqual(rows["team"]["Minimum positive acceptance"], 0.5)
        self.assertTrue(
            all(
                row["Applicability gates"] == "Policy unavailable"
                for row in applicability_rows(report)
            )
        )

    def test_applicability_widget_refuses_to_show_unbound_policy_as_eligible(self):
        from streamlit.testing.v1 import AppTest

        config = self.frozen()["config"]
        report = fixture_report(config, *self.inputs())
        report["applicability"]["test"]["urgency"]["positive_accepted_fraction"] = 0
        model = {
            "id": "fixture",
            "payload": {
                "configuration": {
                    "release_policy_id": "strict-policy",
                    "release_policy_sha256": "policy-hash",
                }
            },
        }
        policy = {
            "policy": {
                "id": "strict-policy",
                "payload": {"sha256": "policy-hash", "criteria": {"vega": LIMITS}},
            }
        }

        def view(value):
            data = repr({"report": report, "model": model, "policy": value})
            return AppTest.from_string(
                "from decision_workbench.ui.vega import render_applicability\nd = "
                + data
                + "\nrender_applicability(d['report'],d['model'],d['policy'])"
            ).run(timeout=30)

        browser = view(policy)
        self.assertEqual(len(browser.exception), 0)
        rows = browser.dataframe[0].value.set_index("Question")
        self.assertEqual(rows.loc["urgency", "Positive gate open rate"], 1)
        self.assertEqual(rows.loc["urgency", "Positive accepted fraction"], 0)
        self.assertEqual(rows.loc["urgency", "Applicability gates"], "Fail")
        self.assertTrue(any("fails" in warning.value for warning in browser.warning))
        browser = view({"policy": None})
        self.assertEqual(len(browser.exception), 0)
        self.assertEqual(
            set(browser.dataframe[0].value["Applicability gates"]),
            {"Policy unavailable"},
        )
        self.assertTrue(
            any("does not bind" in warning.value for warning in browser.warning)
        )

    def test_worker_dispatch_release_abstention_and_cold_recovery(self):
        self.frozen()
        job = self.store.claim()
        positive, negative = self.inputs()
        worker = Worker(self.store)
        captured = []

        def child(argv, **kwargs):
            captured.extend(argv)
            with self.assertRaises(ResourceBusy):
                ResourceLease(self.home / "gpu.lock").acquire(timeout=0)
            request = Path(argv[argv.index("--local-study-request") + 1])
            self.assertEqual(
                request.read_text(encoding="utf-8"), request_body(job["config"])
            )
            data = Path(argv[argv.index("--data") + 1])
            self.assertEqual(len(list(data.glob("*.jsonl"))), 8)
            _, hashes = load_splits(data)
            _, gate_hashes = load_gate_splits(data, positive)
            args = Namespace(
                **{key: job["config"][key] for key in PROFILE | DEFAULTS},
                local_study_request=str(request),
            )
            validate_request(
                args,
                hashes,
                gate_hashes,
                QUESTIONS,
                job["config"]["vega_contract"]["preflight"],
            )
            out = Path(argv[argv.index("--out") + 1])
            (out / "merged").mkdir(parents=True)
            (out / "merged" / "fictional-fixture.txt").write_text(
                "CPU contract fixture; no trained weights", encoding="utf-8"
            )
            (out / "report.json").write_text(
                json.dumps(fixture_report(job["config"], positive, negative)),
                encoding="utf-8",
            )
            write_manifest(out, "vega", QUESTIONS)
            return Mock(poll=Mock(return_value=0), returncode=0)

        try:
            with (
                patch("importlib.util.find_spec", return_value=object()),
                patch("subprocess.Popen", side_effect=child),
            ):
                worker.execute(job)
            finished = self.store.job(job["id"], "A", self.project)
            self.assertEqual(finished["status"], "succeeded", finished)
            self.assertNotIn("--grad-accum", captured)
            self.assertIn("--offline", captured)
            model = self.store.get(
                finished["result"]["model"], "A", self.project, "model"
            )
            self.service.checked_export(model["payload"], QUESTIONS)
            runtime = Mock()
            runtime.predict.return_value = normalize_answers(
                {
                    name: {
                        "probabilities": {key: 1 / len(q) for key in q},
                        "model_abstained": True,
                    }
                    for name, q in {
                        "team": ["billing", "engineering"],
                        "refund": ["false", "true"],
                        "urgency": ["0", "1", "2"],
                    }.items()
                },
                QUESTIONS,
                0,
            )
            real_runtime = self.service.runtime
            self.service.runtime = runtime
            try:
                self.service.activate(
                    OWNER, self.project, model["id"], 0, "Fictional mechanics fixture"
                )
                result = self.service.predict(
                    OWNER, self.project, "Weather without support evidence", 0
                )
                self.assertTrue(
                    all(
                        result["answers"][name]["model_abstained"] for name in QUESTIONS
                    )
                )
                runtime.preload.assert_called_once()
            finally:
                self.service.runtime = real_runtime
            archive_path = Path(self.temp.name) / "backup"
            archive = backup_workspace(self.home, archive_path)
            destination = Path(self.temp.name) / "restored"
            restore_workspace(archive_path, destination, archive["manifest_sha256"])
            restored = Store(destination)
            self.assertEqual(
                restored.active("A", self.project)["model_id"], model["id"]
            )
        finally:
            worker.service.runtime.close()

    def test_browser_owner_criteria_paired_selection_and_training_over_live_http(self):
        import uvicorn
        from streamlit.testing.v1 import AppTest

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(
            uvicorn.Config(self.app, log_level="error", access_log=False)
        )
        thread = threading.Thread(
            target=lambda: server.run(sockets=[listener]), daemon=True
        )
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if time.monotonic() > deadline:
                    raise RuntimeError("Local fixture HTTP server did not start")
                time.sleep(0.02)
            browser = AppTest.from_file(
                str(Path(__file__).resolve().parents[1] / "app.py")
            ).run(timeout=30)
            browser.text_input[0].set_value(url)
            browser.text_input[1].set_value("a-secret")
            next(b for b in browser.button if b.label == "Connect").click().run(
                timeout=30
            )
            next(
                c
                for c in browser.checkbox
                if c.label == "Enable Vega applicability acceptance criteria"
            ).set_value(True)
            next(
                n
                for n in browser.number_input
                if n.label == "Minimum Vega negative test rows for every question"
            ).set_value(10)
            next(
                b for b in browser.button if b.label == "Save acceptance criteria"
            ).click().run(timeout=30)
            self.assertEqual(len(browser.exception), 0)
            self.assertEqual(len(browser.error), 0)
            self.assertEqual(
                self.store.release_policy("A", self.project)["policy"]["payload"][
                    "criteria"
                ]["vega"]["min_negative_test_rows"],
                10,
            )
            next(
                s for s in browser.selectbox if s.label == "Training backend"
            ).set_value("vega").run(timeout=30)
            self.assertEqual(len(browser.exception), 0)
            self.assertEqual(len(browser.error), 0)
            # This is a CPU-only HTTP/UI contract fixture, not GPU acceptance.
            # Keep the runtime-availability state explicit instead of pretending
            # that the test environment installed or executed Vega.
            availability = browser.session_state["runtime-capabilities"]
            if not availability["runtimes"]["training"]["packages"].get("vegaml"):
                self.assertTrue(next(b for b in browser.button if b.label == "Queue training").disabled)
            availability = copy.deepcopy(availability)
            for runtime in availability["runtimes"].values():
                runtime.update(system="Fixture", python="fixture", error=None, cuda=True, mps=False, gpus=[])
                runtime["packages"].update(torch="fixture", vegaml="fixture")
            browser.session_state["runtime-capabilities"] = availability
            browser.run(timeout=30)
            self.assertEqual(
                next(
                    s
                    for s in browser.selectbox
                    if s.label == "Paired applicability snapshot"
                ).value,
                self.gate,
            )
            next(
                n
                for n in browser.number_input
                if n.label == "Vega maximum optimizer steps (0 uses all epochs)"
            ).set_value(2)
            next(b for b in browser.button if b.label == "Queue training").click().run(
                timeout=30
            )
            self.assertEqual(len(browser.exception), 0)
            self.assertEqual(len(browser.error), 0)
            jobs = [
                j for j in self.store.jobs("A", self.project) if j["kind"] == "train"
            ]
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["config"]["gate_snapshot"], self.gate)
            self.assertEqual(jobs[0]["config"]["max_steps"], 2)
            self.assertTrue(jobs[0]["config"]["offline"])
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            listener.close()
