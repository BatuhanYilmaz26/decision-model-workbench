"""Hosted lifecycle and adverse contracts using an actual local HTTP fixture provider."""

import copy
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from decision_workbench.data import options, prepare
from decision_workbench.platform.api import create_app
from decision_workbench.platform.hosted import (
    Hosted,
    HostedQuota,
    HostedUnavailable,
    decode_response,
    public_profile,
    reserve,
    validate_profile,
)
from decision_workbench.platform.hosted_evaluation import fit
from decision_workbench.platform.recovery import backup_workspace, restore_workspace
from decision_workbench.platform.service import Service
from decision_workbench.platform.store import Store, Conflict, digest
from decision_workbench.platform.worker import Worker
from hosted_fixture_transport import FixtureTransport
import test_platform as fixtures

OWNER = {"tenant": "A", "actor": "owner", "role": "admin"}


def profile(provider="openrouter"):
    result = {
        "id": provider,
        "provider": provider,
        "model": (
            "typesafe/jev-1.13"
            if provider == "openrouter"
            else (
                "clef-flash"
                if provider == "cloudflare"
                else ("pplx-decider-v1.2" if provider == "perplexity" else "jev-1.13.0")
            )
        ),
        "response_model": (
            "typesafe/jev-1.13-20260917"
            if provider == "openrouter"
            else (
                "clef-flash"
                if provider == "cloudflare"
                else ("pplx-decider-v1.2" if provider == "perplexity" else "jev-1.13.0")
            )
        ),
        "key_env": "DMT_PROVIDER_FIXTURE",
        "tenants": ["A"],
        "max_calls_day": 1000,
        "max_inflight": 2,
        "timeout_seconds": 5,
        "max_request_bytes": 1024 if provider == "cloudflare" else 64000,
    }
    if provider == "openrouter":
        result["response_provider"] = "TypeSafe"
        result["provider_slug"] = "typesafe"
    if provider == "cloudflare":
        result["account_id"] = "a" * 32
    if provider == "liquid":
        result.update(model="d1", response_model="d1")
    return result


def answer(payload, provider):
    state = payload["state"]
    labels = {
        "team": "billing" if "invoice" in state else "engineering",
        "refund": "refund requested" in state,
        "urgency": 0 if "routine" in state else 2 if "urgent" in state else 1,
    }
    answers = {}
    for name, q in payload["questions"].items():
        keys = options(q)
        target = labels[name]
        key = (
            target
            if q["type"] == "choice"
            else ("true" if target else "false") if q["type"] == "noul" else str(target)
        )
        probs = {k: 0.90 if k == key else 0.10 / (len(keys) - 1) for k in keys}
        if q["type"] == "noul":
            answers[name] = {"type": "noul", "noul": probs["true"]}
        elif q["type"] == "choice":
            answers[name] = {
                "type": "choice",
                "choice": key,
                "probabilities": probs,
                "confidence": 0.7,
            }
        else:
            answers[name] = {
                "type": "score",
                "score": sum(int(k) * p for k, p in probs.items()),
                "legend": dict(zip(keys, q["criteria"])),
                "probabilities": probs,
                "confidence": 0.7,
            }
    p = profile(provider)
    result = {
        "model": p["response_model"],
        "answers": answers,
        "usage": {"input_tokens": 120, "output_tokens": 20, "cost": 0.001},
        "id": "fixture-response",
    }
    if provider == "openrouter":
        result["provider"] = "TypeSafe"
    if provider in ("liquid", "perplexity"):
        result["model"] = payload["model"]
    if provider == "liquid":
        result["usage"]["output_tokens"] = 0
    return (
        {"success": True, "errors": [], "result": result}
        if provider == "cloudflare"
        else result
    )


def isolated_fixture_exchange(port):
    """Test-only routing seam; production process/pipe/deadline logic is unchanged."""
    import sys
    from decision_workbench.platform.hosted_transport import run_process

    code = (
        "import sys;sys.path.insert(0,"
        + repr(str(Path(__file__).resolve().parent))
        + ");from hosted_fixture_transport import FixtureTransport;from decision_workbench.platform.hosted_transport import child_main;child_main(FixtureTransport("
        + str(port)
        + "));assert 'sklearn' not in sys.modules"
    )

    def exchange(profile, body, key, record_started=None):
        return run_process(
            profile,
            body,
            key,
            command=[sys.executable, "-c", code],
            record_started=record_started,
        )

    return exchange


class HostedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.file = self.root / "profiles.json"
        self.profile = profile()
        self.write_profiles()
        self.env = patch.dict(
            os.environ,
            {
                "DMT_DECISION_PROFILES_FILE": str(self.file),
                "DMT_PROVIDER_FIXTURE": "fictional-provider-credential",
            },
        )
        self.env.start()
        self.requests = []
        self.mutate = None
        self.http_status = 200
        self.custom_bytes = None
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(
                    self.rfile.read(int(self.headers["Content-Length"]))
                )
                parent.requests.append({"path": self.path, "payload": payload})
                provider = (
                    "cloudflare"
                    if "/accounts/" in self.path
                    else (
                        "typesafe"
                        if self.path == "/v1/systemone"
                        else (
                            "perplexity"
                            if self.path == "/v1/decisions"
                            else (
                                "liquid"
                                if self.path == "/decisions/v1/systemone"
                                else "openrouter"
                            )
                        )
                    )
                )
                result = answer(payload, provider)
                if parent.mutate:
                    result = parent.mutate(result)
                data = (
                    parent.custom_bytes
                    if parent.custom_bytes is not None
                    else json.dumps(result).encode()
                )
                self.send_response(parent.http_status)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.store = Store(self.root / "workspace")
        self.hosted = Hosted(self.store, FixtureTransport(self.server.server_port))
        self.app = create_app(self.store.folder, fixtures.keys(), hosted=self.hosted)
        self.client = TestClient(self.app)
        self.client.headers["Authorization"] = "Bearer a-secret"
        self.service = self.app.state.service
        self.project = self.service.create_project(
            OWNER, "hosted support", "Fictional routing mechanics", fixtures.QUESTIONS
        )["id"]
        splits = prepare(fixtures.fixture_rows(180))
        self.snapshot = self.store.add(
            "snapshot",
            "A",
            self.project,
            "owner",
            {
                "splits": splits,
                "sha256": digest(splits),
                "schema_sha256": digest(fixtures.QUESTIONS),
                "counts": {k: len(v) for k, v in splits.items()},
            },
        )["id"]
        self.service.configure_release_policy(
            OWNER, self.project, 0.8, 0.8, 0.2, 10, None
        )
        self.worker = Worker(self.store, hosted=self.hosted)
        self.prefix = "/v1/projects/" + self.project

    def write_profiles(self, values=None):
        self.file.write_text(
            json.dumps(
                {
                    "format": "domain-decision-profiles-v1",
                    "profiles": values if values is not None else [self.profile],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.client.close()
        self.worker.service.runtime.close()
        self.service.runtime.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.env.stop()
        self.temp.cleanup()

    def queue(self, key="hosted-study-key"):
        return self.service.submit(
            OWNER,
            self.project,
            "hosted-evaluate",
            {
                "snapshot": self.snapshot,
                "profile_id": self.profile["id"],
                "data_approved": True,
            },
            key,
        )

    def study(self, key="hosted-study-key"):
        job = self.queue(key)
        self.worker.once()
        completed = self.store.job(job["id"], "A", self.project)
        self.assertEqual(completed["status"], "succeeded", completed.get("error"))
        return completed["result"]["model"]

    def release(self, model, revision=0):
        return self.service.activate(
            OWNER, self.project, model, revision, "Fictional fixture release"
        )

    def decision(self, key="decision-key-001", state="invoice refund requested urgent"):
        return self.client.post(
            self.prefix + "/decisions",
            json={"state": state, "threshold": 0.8, "request_key": key},
        )

    def test_all_five_provider_http_paths_and_three_primitives(self):
        self.write_profiles(
            [
                profile(provider)
                for provider in (
                    "typesafe",
                    "openrouter",
                    "perplexity",
                    "cloudflare",
                    "liquid",
                )
            ]
        )
        for provider in (
            "typesafe",
            "openrouter",
            "perplexity",
            "cloudflare",
            "liquid",
        ):
            p = profile(provider)
            result = self.hosted.call(
                OWNER,
                self.project,
                public_profile(p),
                "invoice refund requested urgent",
                fixtures.QUESTIONS,
                "direct-" + provider,
                {},
            )
            self.assertEqual(result["answers"]["team"]["answer"], "billing")
            self.assertTrue(result["answers"]["refund"]["answer"])
            self.assertEqual(result["answers"]["urgency"]["answer"], 2)
            self.assertNotEqual(
                result["identity"]["provider_confidence"]["team"],
                result["answers"]["team"]["confidence"],
            )
        self.assertEqual(len(self.requests), 5)
        for request in self.requests:
            self.assertLessEqual(
                set(request["payload"]), {"model", "state", "questions", "provider"}
            )
            self.assertNotIn("gold", request["payload"])
            if request["path"] == "/api/alpha/decisions":
                self.assertEqual(
                    request["payload"]["provider"],
                    {"allow_fallbacks": False, "only": ["typesafe"]},
                )

    def test_liquid_models_study_release_cold_inference_and_rollback(self):
        profiles = []
        for model in ("d1", "d1-ultra"):
            p = profile("liquid")
            p.update(id="liquid-" + model, model=model, response_model=model)
            profiles.append(p)
        self.write_profiles(profiles)
        models = []
        for revision, p in enumerate(profiles):
            self.profile = p
            model = self.study("liquid-study-" + p["model"])
            models.append(model)
            self.release(model, revision)
            key = "liquid-decision-" + p["model"]
            response = self.decision(key)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["model_id"], model)
            self.assertTrue(response.json()["answers"]["refund"]["answer"])
            self.assertEqual(response.json()["answers"]["team"]["answer"], "billing")
            self.assertEqual(response.json()["answers"]["urgency"]["answer"], 2)
            self.assertTrue(self.decision(key).json()["provider"]["cached"])
            bundle = self.store.get(model, "A", self.project, "model")["payload"]
            self.assertEqual(bundle["configuration"]["profile"]["provider"], "liquid")
            self.assertEqual(bundle["report"]["provider_usage"]["calls"], 54)
        self.assertEqual(len(self.requests), 110)
        self.assertTrue(
            all(r["path"] == "/decisions/v1/systemone" for r in self.requests)
        )
        self.assertEqual(
            {r["payload"]["model"] for r in self.requests}, {"d1", "d1-ultra"}
        )
        self.release(models[0], 2)
        cold_store = Store(self.store.folder)
        cold = Service(
            cold_store,
            hosted=Hosted(cold_store, FixtureTransport(self.server.server_port)),
        )
        try:
            response = cold.predict(
                OWNER,
                self.project,
                "invoice refund requested urgent",
                0.8,
                "liquid-after-restart",
            )
            self.assertEqual(response["model_id"], models[0])
            self.assertEqual(response["release_revision"], 3)
        finally:
            cold.runtime.close()
        self.assertNotIn("fictional-provider-credential", json.dumps(self.requests))

    def test_liquid_contract_profiles_and_response_drift(self):
        from decision_workbench.platform.hosted import request_body
        from decision_workbench.platform.hosted_transport import endpoint, receive

        for model in ("d1", "d1-ultra"):
            p = profile("liquid")
            p.update(model=model, response_model=model)
            validate_profile(p)
            self.assertEqual(
                endpoint(p), "https://api.liquid.ai/decisions/v1/systemone"
            )
            for field, value in (
                ("model", "d1-3B"),
                ("model", "d1-latest"),
                ("response_model", "different"),
                ("provider_slug", "liquid"),
                ("account_id", "a" * 32),
            ):
                with (
                    self.subTest(model=model, field=field, value=value),
                    self.assertRaises(ValueError),
                ):
                    validate_profile({**p, field: value})
            body = request_body(p, {"evidence": "Fictional test"}, fixtures.QUESTIONS)
            self.assertEqual(set(json.loads(body)), {"model", "state", "questions"})
            good = answer(
                {
                    "state": "invoice refund requested urgent",
                    "questions": fixtures.QUESTIONS,
                    "model": model,
                },
                "liquid",
            )
            self.assertEqual(
                decode_response(good, p, fixtures.QUESTIONS)["identity"]["usage"][
                    "output_tokens"
                ],
                0,
            )
            for bad in (
                {**good, "model": "wrong"},
                {**good, "usage": {"input_tokens": 1, "output_tokens": 1}},
                {**good, "usage": {"input_tokens": 1}},
                {**good, "usage": {"output_tokens": 0}},
                {**good, "usage": {"input_tokens": 1, "output_tokens": False}},
            ):
                with (
                    self.subTest(model=model, response=bad["usage"]),
                    self.assertRaises(HostedUnavailable),
                ):
                    decode_response(bad, p, fixtures.QUESTIONS)
            observed = []

            def handler(request):
                observed.append(
                    (
                        str(request.url),
                        request.headers.get("Authorization"),
                        set(json.loads(request.content)),
                    )
                )
                return httpx.Response(
                    200, stream=httpx.ByteStream(json.dumps(good).encode())
                )

            response = receive(
                p, body, "fictional-liquid-key", httpx.MockTransport(handler)
            )
            decode_response(json.loads(response), p, fixtures.QUESTIONS)
            self.assertEqual(
                observed,
                [
                    (
                        endpoint(p),
                        "Bearer fictional-liquid-key",
                        {"model", "state", "questions"},
                    )
                ],
            )

    def test_liquid_isolated_http_transport_and_recovery(self):
        self.profile = profile("liquid")
        self.write_profiles()
        self.test_actual_isolated_process_http_and_default_hosted_wiring()

    def test_liquid_browser_consent_study_release_and_prediction(self):
        self.profile = profile("liquid")
        self.profile.update(model="d1-ultra", response_model="d1-ultra")
        self.write_profiles()
        self.test_hosted_browser_consent_queue_release_and_inference_over_http()

    def test_evaluate_release_idempotent_inference_cold_service_and_scope(self):
        model = self.study()
        payload = self.store.get(model, "A", self.project, "model")["payload"]
        self.assertEqual(payload["report"]["test"]["team"]["accuracy"], 1)
        self.assertEqual(len(self.requests), 54)
        self.assertEqual(payload["report"]["provider_usage"]["calls"], 54)
        self.assertAlmostEqual(
            payload["report"]["provider_usage"]["reported_cost_sum"], 0.054
        )
        self.assertIsNone(self.store.active("A", self.project)["model_id"])
        self.assertTrue(self.store.release_policy("A", self.project)["locked"])
        self.release(model)
        first = self.decision()
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["answers"]["team"]["answer"], "billing")
        self.assertFalse(first.json()["provider"]["cached"])
        again = self.decision()
        self.assertTrue(again.json()["provider"]["cached"])
        self.assertEqual(first.json()["answers"], again.json()["answers"])
        self.assertEqual(len(self.requests), 55)
        self.assertEqual(self.decision(state="changed evidence").status_code, 409)
        cold_store = Store(self.store.folder)
        cold = Service(
            cold_store,
            hosted=Hosted(cold_store, FixtureTransport(self.server.server_port)),
        )
        try:
            result = cold.predict(
                OWNER,
                self.project,
                "invoice refund requested urgent",
                0.8,
                "decision-key-001",
            )
            self.assertEqual(result["answers"], first.json()["answers"])
            self.assertTrue(result["provider"]["cached"])
        finally:
            cold.runtime.close()
        self.client.headers["Authorization"] = "Bearer infer-secret"
        self.assertEqual(
            self.client.get(self.prefix + "/provider-calls").status_code, 403
        )
        self.assertEqual(self.decision("application-key").status_code, 200)
        self.client.headers["Authorization"] = "Bearer b-secret"
        self.assertEqual(self.decision("other-workspace").status_code, 404)
        self.assertEqual(len(self.requests), 56)

    def test_missing_consent_wrong_role_disabled_profile_and_policy_lock(self):
        config = {
            "snapshot": self.snapshot,
            "profile_id": self.profile["id"],
            "data_approved": False,
        }
        with self.assertRaises(ValueError):
            self.service.submit(
                OWNER, self.project, "hosted-evaluate", config, "missing-consent"
            )
        editor = {**OWNER, "role": "editor"}
        with self.assertRaises(PermissionError):
            self.service.submit(
                editor,
                self.project,
                "hosted-evaluate",
                {**config, "data_approved": True},
                "editor-no-owner",
            )
        self.queue()
        with self.assertRaises(Conflict):
            self.service.configure_release_policy(
                OWNER,
                self.project,
                0,
                0,
                1,
                10,
                self.store.release_policy("A", self.project)["policy"]["id"],
            )
        self.profile["response_model"] = "changed-revision"
        self.write_profiles()
        self.worker.once()
        self.assertEqual(self.store.jobs("A", self.project)[0]["status"], "failed")
        self.assertEqual(self.requests, [])
        self.assertEqual(self.store.list("A", self.project, "model"), [])

    def test_identity_drift_stops_study_and_never_releases(self):
        def change(result):
            if len(self.requests) >= 3:
                result["model"] = "unexpected-revision"
            return result

        self.mutate = change
        job = self.queue()
        self.worker.once()
        self.assertEqual(
            self.store.job(job["id"], "A", self.project)["status"], "failed"
        )
        self.assertEqual(len(self.requests), 3)
        history = self.client.get(self.prefix + "/provider-calls").json()
        self.assertEqual(history[0]["outcome"]["status"], "unknown")
        self.assertEqual(self.store.list("A", self.project, "model"), [])
        self.assertNotIn("fictional-provider-credential", json.dumps(history))
        self.assertNotIn('"state"', json.dumps(history))
        self.assertNotIn('"gold"', json.dumps(history))

    def test_ambiguous_provider_failure_not_retried_or_recharged(self):
        self.http_status = 429
        p = public_profile(self.profile)
        for _ in range(2):
            with self.assertRaises((HostedUnavailable, Conflict)):
                self.hosted.call(
                    OWNER,
                    self.project,
                    p,
                    "invoice normal",
                    fixtures.QUESTIONS,
                    "same-failed-call",
                    {},
                )
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(len(self.store.list("A", self.project, "provider-call")), 1)

    def test_atomic_quota_and_concurrency_across_store_instances(self):
        p = {**self.profile, "max_calls_day": 1}

        def attempt(index):
            try:
                reserve(
                    Store(self.store.folder),
                    OWNER,
                    self.project,
                    p,
                    str(index),
                    digest(index),
                    "inference",
                )
                return True
            except HostedQuota:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(attempt, range(2))), 1)
        p["max_calls_day"] = 1000
        p["max_inflight"] = 1
        with self.assertRaises(HostedQuota):
            reserve(
                self.store,
                OWNER,
                self.project,
                p,
                "concurrent",
                digest("different"),
                "inference",
            )
        self.assertEqual(self.requests, [])

    def test_insufficient_study_quota_refused_before_external_data(self):
        self.profile["max_calls_day"] = 5
        self.write_profiles()
        job = self.queue()
        self.worker.once()
        self.assertEqual(
            self.store.job(job["id"], "A", self.project)["status"], "failed"
        )
        self.assertEqual(self.requests, [])
        self.assertEqual(self.store.list("A", self.project, "provider-call"), [])

    def test_cancel_during_call_records_outcome_without_registering_model(self):
        job = self.queue()

        def cancel(result):
            self.store.cancel(job["id"], "A", self.project, "owner")
            return result

        self.mutate = cancel
        self.worker.once()
        self.assertEqual(
            self.store.job(job["id"], "A", self.project)["status"], "cancelled"
        )
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.store.list("A", self.project, "model"), [])
        self.assertEqual(
            self.store.list("A", self.project, "provider-call-result")[0]["payload"][
                "status"
            ],
            "succeeded",
        )

    def test_wire_contract_refusals_and_rounding(self):
        payload = {"state": "invoice normal", "questions": fixtures.QUESTIONS}
        base = answer(payload, "openrouter")
        mutations = []
        for field, value in (
            ("model", "changed"),
            ("provider", "changed"),
            ("answers", {}),
            ("usage", {"input_tokens": True}),
        ):
            value_body = copy.deepcopy(base)
            value_body[field] = value
            mutations.append(value_body)
        for probs in (
            {"billing": 1},
            {"billing": True, "engineering": 0},
            {"billing": float("nan"), "engineering": 0},
            {"billing": 0.9, "engineering": 0.4},
        ):
            value_body = copy.deepcopy(base)
            value_body["answers"]["team"]["probabilities"] = probs
            mutations.append(value_body)
        for name, field, value in (
            ("team", "choice", "engineering"),
            ("refund", "noul", None),
            ("urgency", "score", True),
            ("urgency", "score", 0),
            ("team", "confidence", float("inf")),
        ):
            value_body = copy.deepcopy(base)
            value_body["answers"][name][field] = value
            mutations.append(value_body)
        for body in mutations:
            with self.assertRaises(HostedUnavailable):
                decode_response(body, self.profile, fixtures.QUESTIONS)
        rounded = copy.deepcopy(base)
        rounded["answers"]["team"]["probabilities"] = {
            "billing": 0.901,
            "engineering": 0.1,
        }
        result = decode_response(rounded, self.profile, fixtures.QUESTIONS)
        self.assertAlmostEqual(
            sum(result["answers"]["team"]["probabilities"].values()), 1
        )
        self.assertLess(result["answers"]["team"]["confidence"], 0.9005)

    def test_portable_recovery_preserves_calls_and_policy_without_credentials(self):
        model = self.study()
        self.release(model)
        before = self.decision().json()
        backup = backup_workspace(self.store.folder, self.root / "backup", 60)
        restored = self.root / "restored"
        restore_workspace(self.root / "backup", restored, backup["manifest_sha256"])
        store = Store(restored)
        service = Service(
            store, hosted=Hosted(store, FixtureTransport(self.server.server_port))
        )
        try:
            after = service.predict(
                OWNER,
                self.project,
                "invoice refund requested urgent",
                0.8,
                "decision-key-001",
            )
            self.assertEqual(after["answers"], before["answers"])
            self.assertTrue(after["provider"]["cached"])
            self.assertEqual(len(self.requests), 55)
        finally:
            service.runtime.close()
        self.assertFalse((restored / "profiles.json").exists())
        folder = (
            restored
            / "artifacts"
            / self.store.get(model, "A", self.project, "model")["payload"][
                "artifact_id"
            ]
        )
        self.assertEqual(
            {p.name for p in folder.iterdir()}, {"model.json", "report.json"}
        )

    def test_artifact_tamper_profile_revocation_and_frozen_calibration(self):
        model = self.study()
        self.release(model)
        self.write_profiles([])
        self.assertEqual(self.decision().status_code, 503)
        self.assertEqual(len(self.requests), 54)
        self.write_profiles()
        artifact = self.store.get(model, "A", self.project, "model")["payload"][
            "artifact_id"
        ]
        (self.store.folder / "artifacts" / artifact / "model.json").write_text(
            "{}", encoding="utf-8"
        )
        self.assertEqual(self.decision().status_code, 400)
        self.assertEqual(len(self.requests), 54)
        rows = self.store.get(self.snapshot, "A", self.project, "snapshot")["payload"][
            "splits"
        ]["calibration"]
        probabilities = [
            decode_response(
                answer(
                    {"state": r["state"], "questions": fixtures.QUESTIONS}, "openrouter"
                ),
                self.profile,
                fixtures.QUESTIONS,
            )["answers"]
            for r in rows
        ]
        temps = fit(rows, probabilities, fixtures.QUESTIONS)
        self.assertEqual(set(temps), set(fixtures.QUESTIONS))
        self.assertTrue(all(t == 0.5 for t in temps.values()))

    def test_operator_profile_validation_and_secret_scope(self):
        listed = self.client.get(self.prefix + "/decision-profiles")
        self.assertEqual(listed.status_code, 200)
        self.assertNotIn("key_env", listed.text)
        self.assertNotIn("tenants", listed.text)
        invalid = {**self.profile, "endpoint": "http://arbitrary.invalid"}
        with self.assertRaises(ValueError):
            validate_profile(invalid)
        invalid = {**self.profile, "key_env": "HOME"}
        with self.assertRaises(ValueError):
            validate_profile(invalid)
        self.file.write_text("malformed", encoding="utf-8")
        self.assertEqual(
            self.client.get(self.prefix + "/decision-profiles").status_code, 503
        )
        self.assertEqual(self.client.get("/ready").status_code, 503)

    def test_stream_json_size_failures_are_recorded_without_replay(self):
        frozen = public_profile(self.profile)
        for index, body in enumerate(
            (b'{"model":"a","model":"b"}', b"not json", b" " * 1048577)
        ):
            self.custom_bytes = body
            key = "invalid-wire-" + str(index)
            with self.assertRaises(HostedUnavailable):
                self.hosted.call(
                    OWNER,
                    self.project,
                    frozen,
                    "invoice normal",
                    fixtures.QUESTIONS,
                    key,
                    {},
                )
            with self.assertRaises(Conflict):
                self.hosted.call(
                    OWNER,
                    self.project,
                    frozen,
                    "invoice normal",
                    fixtures.QUESTIONS,
                    key,
                    {},
                )
            history = self.client.get(self.prefix + "/provider-calls").json()[0]
            self.assertTrue(history["capacity_held"])
            response = self.client.post(
                self.prefix + "/provider-calls/" + history["id"] + "/reconcile",
                json={
                    "expected_outcome_sha256": history["outcome_sha256"],
                    "local_transport_stopped": True,
                    "provider_finished": True,
                    "reference": "Local HTTP fixture handler completed",
                    "note": "Synthetic invalid-body fixture; no external billing",
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(
            len(self.store.list("A", self.project, "provider-call-result")), 3
        )
        self.assertTrue(
            all(
                r["payload"]["status"] == "unknown"
                for r in self.store.list("A", self.project, "provider-call-result")
            )
        )

    def test_identity_reauthorization_after_provider_call_and_release_rollback(self):
        from decision_workbench.platform.auth import AuthenticationError

        model = self.study()
        self.release(model)
        with patch.object(
            self.app.state.authentication,
            "authenticate",
            side_effect=[
                OWNER,
                AuthenticationError("Credential revoked during request"),
            ],
        ):
            response = self.decision("revoked-request-key")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("answers", response.json())
        self.assertEqual(len(self.requests), 55)
        self.release(model, 1)
        self.assertEqual(self.store.active("A", self.project)["revision"], 2)
        self.assertEqual(self.decision("revoked-request-key").status_code, 409)
        self.assertEqual(len(self.requests), 55)

    def test_hosted_browser_consent_queue_release_and_inference_over_http(self):
        import socket
        import time
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
                    raise RuntimeError("API startup timed out")
                time.sleep(0.02)
            at = AppTest.from_file(str(Path("app.py").resolve()))
            at.session_state["credential"] = "a-secret"
            at.session_state["api_url"] = url
            at.session_state["principal"] = OWNER
            at.run(timeout=30)
            self.assertFalse(at.exception)

            def widget(kind, label):
                return next(w for w in getattr(at, kind) if w.label == label)

            self.assertTrue(widget("button", "Queue hosted evaluation").disabled)
            widget(
                "checkbox",
                "This provider is approved to receive study data and future inference states after owner release",
            ).check().run(timeout=30)
            self.assertFalse(widget("button", "Queue hosted evaluation").disabled)
            widget("button", "Queue hosted evaluation").click().run(timeout=30)
            self.assertFalse(at.exception)
            jobs = self.store.jobs("A", self.project)
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["kind"], "hosted-evaluate")
            self.worker.once()
            self.assertEqual(
                self.store.job(jobs[0]["id"], "A", self.project)["status"], "succeeded"
            )
            at.run(timeout=30)
            self.assertFalse(at.exception)
            widget("text_input", "Release or rollback reason").set_value(
                "Fictional hosted browser fixture"
            )
            widget("button", "Activate selected version").click().run(timeout=30)
            self.assertFalse(at.exception)
            self.assertEqual(self.store.active("A", self.project)["revision"], 1)
            widget("text_area", "Evidence for the released model").set_value(
                "invoice refund requested urgent"
            )
            widget("button", "Make decision").click().run(timeout=30)
            self.assertFalse(at.exception)
            self.assertEqual(len(self.requests), 55)
            widget("checkbox", "Show provider call history").check().run(timeout=30)
            self.assertFalse(at.exception)
            self.http_status = 429
            self.assertEqual(self.decision("browser-unknown-request").status_code, 503)
            at.run(timeout=30)
            self.assertFalse(at.exception)
            widget(
                "text_input", "Provider settlement or operator evidence reference"
            ).set_value("Completed fictional HTTP handler")
            widget("text_input", "Call reconciliation note").set_value(
                "No external provider or billing; fixture complete"
            )
            widget("button", "Record provider settlement").click().run(timeout=30)
            self.assertFalse(at.exception)
            self.assertEqual(
                self.store.list("A", self.project, "provider-call-reconciliation"), []
            )
            widget("checkbox", "I verified that the local transport is stopped").check()
            widget(
                "checkbox", "I verified that the provider finished this request"
            ).check()
            widget("button", "Record provider settlement").click().run(timeout=30)
            self.assertFalse(at.exception)
            self.assertEqual(
                len(self.store.list("A", self.project, "provider-call-reconciliation")),
                1,
            )
            self.assertEqual(len(self.requests), 56)
        finally:
            server.should_exit = True
            thread.join(timeout=15)
            listener.close()
            self.assertFalse(thread.is_alive())

    def settlement(self, call, **changes):
        body = {
            "expected_outcome_sha256": call["outcome_sha256"],
            "local_transport_stopped": True,
            "provider_finished": True,
            "reference": "Fixture server/process completed; not a provider receipt",
            "note": "Fictional operator reconciliation",
        }
        body.update(changes)
        return self.client.post(
            self.prefix + "/provider-calls/" + call["id"] + "/reconcile", json=body
        )

    def test_missing_and_unknown_outcomes_never_age_out_or_replay(self):
        from decision_workbench.platform.hosted import finish_call

        self.profile["max_inflight"] = 1
        self.write_profiles()
        call_id, _ = reserve(
            self.store,
            OWNER,
            self.project,
            self.profile,
            "orphan-key",
            digest("orphan"),
            "inference",
        )
        with patch("decision_workbench.platform.hosted.time.time", return_value=10**11):
            with self.assertRaises(HostedQuota):
                reserve(
                    Store(self.store.folder),
                    OWNER,
                    self.project,
                    self.profile,
                    "another",
                    digest("another"),
                    "inference",
                )
        call = self.client.get(self.prefix + "/provider-calls").json()[0]
        self.assertEqual(self.settlement(call).status_code, 409)
        finish_call(self.store, OWNER, self.project, call_id, "unknown", None, 0.1)
        with self.assertRaises(HostedQuota):
            reserve(
                Store(self.store.folder),
                OWNER,
                self.project,
                self.profile,
                "another",
                digest("another"),
                "inference",
            )
        call = self.client.get(self.prefix + "/provider-calls").json()[0]
        self.assertEqual(
            self.settlement(call, provider_finished=False).status_code, 400
        )
        self.assertEqual(self.settlement(call).status_code, 200)
        with self.assertRaises(Conflict):
            reserve(
                self.store,
                OWNER,
                self.project,
                self.profile,
                "orphan-key",
                digest("orphan"),
                "inference",
            )
        reserve(
            self.store,
            OWNER,
            self.project,
            self.profile,
            "another",
            digest("another"),
            "inference",
        )
        self.assertEqual(self.requests, [])

    def test_settlement_owner_scope_cas_immutable_and_atomic(self):
        self.http_status = 429
        with self.assertRaises(HostedUnavailable):
            self.hosted.call(
                OWNER,
                self.project,
                public_profile(self.profile),
                "invoice normal",
                fixtures.QUESTIONS,
                "uncertain",
                {},
            )
        call = self.client.get(self.prefix + "/provider-calls").json()[0]
        self.client.headers["Authorization"] = "Bearer infer-secret"
        self.assertEqual(self.settlement(call).status_code, 403)
        self.client.headers["Authorization"] = "Bearer b-secret"
        self.assertEqual(self.settlement(call).status_code, 404)
        self.client.headers["Authorization"] = "Bearer a-secret"
        self.assertEqual(
            self.settlement(call, expected_outcome_sha256="a" * 64).status_code, 409
        )
        self.assertEqual(self.settlement(call).status_code, 200)
        self.assertEqual(self.settlement(call).status_code, 200)
        self.assertEqual(
            self.settlement(call, note="change immutable note").status_code, 409
        )
        history = self.client.get(self.prefix + "/provider-calls").json()[0]
        self.assertFalse(history["capacity_held"])
        self.assertEqual(history["outcome"]["status"], "unknown")
        self.assertEqual(
            len(self.store.list("A", self.project, "provider-call-reconciliation")), 1
        )
        self.assertEqual(len(self.requests), 1)
        with self.assertRaises(Conflict):
            self.hosted.call(
                OWNER,
                self.project,
                public_profile(self.profile),
                "invoice normal",
                fixtures.QUESTIONS,
                "uncertain",
                {},
            )
        from decision_workbench.platform.hosted import reconcile_call

        barrier = threading.Barrier(2)

        def concurrent(note):
            barrier.wait()
            try:
                return reconcile_call(
                    Store(self.store.folder),
                    OWNER,
                    self.project,
                    call["id"],
                    call["outcome_sha256"],
                    True,
                    True,
                    "Fixture server/process completed; not a provider receipt",
                    note,
                )
            except Conflict:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(
                list(pool.map(concurrent, ["changed-one", "changed-two"])), [None, None]
            )
        from decision_workbench.platform.hosted import finish_call

        second, _ = reserve(
            self.store,
            OWNER,
            self.project,
            self.profile,
            "settlement-race",
            digest("race"),
            "inference",
        )
        finish_call(self.store, OWNER, self.project, second, "unknown", None, 0.1)
        call = self.client.get(self.prefix + "/provider-calls").json()[0]
        barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(concurrent, ["first-race", "second-race"]))
        self.assertEqual(sum(result is not None for result in outcomes), 1)
        self.assertEqual(
            len(self.store.list("A", self.project, "provider-call-reconciliation")), 2
        )

    def test_pending_settlement_waits_for_deadline_job_and_reauthorization(self):
        from decision_workbench.platform.hosted import reconcile_call, call_outcome
        from decision_workbench.platform.auth import AuthenticationError

        call_id, _ = reserve(
            self.store,
            OWNER,
            self.project,
            self.profile,
            "pending-call",
            digest("pending"),
            "inference",
        )
        original = call_outcome(self.store, OWNER, self.project, call_id)
        future = (
            self.store.get(call_id, "A", self.project)["created"]
            + self.profile["timeout_seconds"]
            + 11
        )

        def revoked():
            raise AuthenticationError("revoked")

        with patch("decision_workbench.platform.hosted.time.time", return_value=future):
            with self.assertRaises(AuthenticationError):
                reconcile_call(
                    self.store,
                    OWNER,
                    self.project,
                    call_id,
                    digest(original),
                    True,
                    True,
                    "manual local/upstream confirmation",
                    "pending fixture",
                    authorize=revoked,
                )
            self.assertEqual(
                self.store.list("A", self.project, "provider-call-reconciliation"), []
            )
            reconcile_call(
                self.store,
                OWNER,
                self.project,
                call_id,
                digest(original),
                True,
                True,
                "manual local/upstream confirmation",
                "pending fixture",
            )
        job = self.queue("unfinished-evaluation")
        claimed = self.store.claim()
        job_call, _ = reserve(
            self.store,
            OWNER,
            self.project,
            self.profile,
            "job-pending",
            digest("job-pending"),
            "evaluation",
            claimed,
        )
        with patch(
            "decision_workbench.platform.hosted.time.time", return_value=future + 100
        ):
            with self.assertRaises(Conflict):
                reconcile_call(
                    self.store,
                    OWNER,
                    self.project,
                    job_call,
                    digest({"status": "pending_or_unknown"}),
                    True,
                    True,
                    "manual confirmation",
                    "job still running",
                )
            self.store.claim()
            self.assertEqual(
                self.store.job(job["id"], "A", self.project)["status"], "interrupted"
            )
            reconcile_call(
                self.store,
                OWNER,
                self.project,
                job_call,
                digest({"status": "pending_or_unknown"}),
                True,
                True,
                "Verified stopped interrupted local job",
                "No provider request remains active",
            )

    def child_command(self, stalled=False):
        import sys

        if stalled:
            code = (
                "import os,time;from pathlib import Path;from decision_workbench.platform.hosted_transport import child_main;import httpx;assert 'DMT_PROVIDER_SENTINEL' not in os.environ;assert 'OPENAI_API_KEY' not in os.environ;child_main(httpx.MockTransport(lambda request:(Path("
                + repr(str(self.root / "transport-entered.txt"))
                + ").write_text('entered'),time.sleep(120),None)[2]))"
            )
        else:
            code = (
                "import sys;sys.path.insert(0,"
                + repr(str(Path("tests").resolve()))
                + ");from hosted_fixture_transport import FixtureTransport;from decision_workbench.platform.hosted_transport import child_main;child_main(FixtureTransport("
                + str(self.server.server_port)
                + "))"
            )
        return [sys.executable, "-c", code]

    def test_actual_isolated_process_http_and_default_hosted_wiring(self):
        from decision_workbench.platform.hosted_transport import run_process

        spawned = []

        def isolated(p, body, key, record_started=None):
            return run_process(
                p,
                body,
                key,
                command=self.child_command(),
                observe=spawned.append,
                record_started=record_started,
            )

        real_path = Hosted(self.store)
        with patch(
            "decision_workbench.platform.hosted.run_process", side_effect=isolated
        ):
            result = real_path.call(
                OWNER,
                self.project,
                public_profile(self.profile),
                "invoice refund requested urgent",
                fixtures.QUESTIONS,
                "process-success",
                {},
            )
        self.assertFalse(result["cached"])
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(len(spawned), 1)
        self.assertEqual(spawned[0].returncode, 0)
        self.assertTrue(
            self.store.list("A", self.project, "provider-call-result")[0]["payload"][
                "transport_terminated"
            ]
        )
        self.assertNotIn("fictional-provider-credential", repr(spawned[0].args))
        receipt = self.client.get(self.prefix + "/provider-calls").json()[0][
            "transport"
        ]
        self.assertEqual(receipt["pid"], spawned[0].pid)
        self.assertEqual(receipt["process_token"], spawned[0].args[-1])
        backup = self.root / "process-ledger-backup"
        captured = backup_workspace(self.store.folder, backup)
        restored = self.root / "process-ledger-restored"
        restore_workspace(backup, restored, captured["manifest_sha256"])
        from decision_workbench.platform.hosted import validate_call_history

        restored_store = Store(restored)
        validate_call_history(restored_store)
        self.assertEqual(
            restored_store.list("A", self.project, "provider-call-transport")[0][
                "payload"
            ],
            receipt,
        )
        with restored_store.transaction() as db:
            value = {**receipt, "process_token": "invalid"}
            db.execute(
                "UPDATE entities SET payload=? WHERE kind='provider-call-transport'",
                (json.dumps(value),),
            )
        with self.assertRaises(ValueError):
            validate_call_history(restored_store)
        self.assertTrue(all(s.closed for s in (spawned[0].stdin, spawned[0].stdout)))

    def test_stalled_child_killed_waited_unknown_capacity_held(self):
        import time
        from decision_workbench.platform.hosted_transport import run_process

        self.profile.update(timeout_seconds=3, max_inflight=1)
        self.write_profiles()
        spawned = []

        def isolated(p, body, key, record_started=None):
            return run_process(
                p,
                body,
                key,
                command=self.child_command(stalled=True),
                observe=spawned.append,
                record_started=record_started,
            )

        started = time.monotonic()
        with patch.dict(
            os.environ,
            {
                "DMT_PROVIDER_SENTINEL": "must-not-inherit",
                "OPENAI_API_KEY": "must-not-inherit",
            },
        ):
            with patch(
                "decision_workbench.platform.hosted.run_process", side_effect=isolated
            ):
                with self.assertRaises(HostedUnavailable):
                    Hosted(self.store).call(
                        OWNER,
                        self.project,
                        public_profile(self.profile),
                        "invoice normal",
                        fixtures.QUESTIONS,
                        "process-timeout",
                        {},
                    )
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 2.5)
        self.assertLess(elapsed, 7)
        self.assertTrue((self.root / "transport-entered.txt").is_file())
        self.assertIsNotNone(spawned[0].returncode)
        self.assertNotEqual(spawned[0].returncode, 0)
        self.assertTrue(all(s.closed for s in (spawned[0].stdin, spawned[0].stdout)))
        call = self.client.get(self.prefix + "/provider-calls").json()[0]
        self.assertTrue(call["capacity_held"])
        self.assertTrue(call["outcome"]["transport_terminated"])
        with self.assertRaises(HostedQuota):
            reserve(
                Store(self.store.folder),
                OWNER,
                self.project,
                self.profile,
                "no-silent-new-call",
                digest("no-new"),
                "inference",
            )
        self.assertEqual(self.requests, [])

    def test_settlement_survives_portable_recovery_and_tampering_refused(self):
        from decision_workbench.platform.hosted import validate_call_history

        self.http_status = 429
        with self.assertRaises(HostedUnavailable):
            self.hosted.call(
                OWNER,
                self.project,
                public_profile(self.profile),
                "invoice normal",
                fixtures.QUESTIONS,
                "recover-unknown",
                {},
            )
        call = self.client.get(self.prefix + "/provider-calls").json()[0]
        self.assertEqual(self.settlement(call).status_code, 200)
        backup = self.root / "settled-backup"
        result = backup_workspace(self.store.folder, backup)
        target = self.root / "settled-restored"
        restore_workspace(backup, target, result["manifest_sha256"])
        restored = Store(target)
        validate_call_history(restored)
        self.assertEqual(
            restored.list("A", self.project, "provider-call-reconciliation")[0][
                "payload"
            ],
            self.store.list("A", self.project, "provider-call-reconciliation")[0][
                "payload"
            ],
        )
        settlement = restored.list("A", self.project, "provider-call-reconciliation")[0]
        with restored.transaction() as db:
            value = settlement["payload"]
            value["provider_finished"] = False
            db.execute(
                "UPDATE entities SET payload=? WHERE id=?",
                (json.dumps(value), settlement["id"]),
            )
        with self.assertRaises(ValueError):
            validate_call_history(restored)
        self.assertEqual(len(self.requests), 1)

    def test_missing_or_invalid_openrouter_slug_rejected_before_transport(self):
        for slug in (None, "", ["typesafe"], "TypeSafe", "*", "typesafe/another"):
            candidate = copy.deepcopy(self.profile)
            if slug is None:
                candidate.pop("provider_slug")
            else:
                candidate["provider_slug"] = slug
            with self.assertRaises(ValueError):
                validate_profile(candidate)
        self.assertEqual(self.requests, [])

    def test_independent_process_crash_leaves_durable_held_reservation(self):
        import subprocess
        import sys

        code = "import os,sys;from decision_workbench.platform.store import Store,digest;from decision_workbench.platform.hosted import profiles,reserve;s=Store(sys.argv[1]);reserve(s,{'tenant':'A','actor':'owner','role':'admin'},sys.argv[2],profiles()[0],'crashed-before-send',digest('crashed'),'inference');os._exit(17)"
        proc = subprocess.run(
            [sys.executable, "-c", code, str(self.store.folder), self.project],
            capture_output=True,
            timeout=15,
        )
        self.assertEqual(proc.returncode, 17, proc.stderr.decode(errors="replace"))
        call = self.client.get(self.prefix + "/provider-calls").json()[0]
        self.assertTrue(call["capacity_held"])
        p = {**self.profile, "max_inflight": 1}
        with patch("decision_workbench.platform.hosted.time.time", return_value=10**11):
            with self.assertRaises(HostedQuota):
                reserve(
                    Store(self.store.folder),
                    OWNER,
                    self.project,
                    p,
                    "cannot-age-out",
                    digest("new"),
                    "inference",
                )
            self.assertEqual(
                self.settlement(
                    call,
                    reference="Known fixture crash before transport; process exit 17",
                ).status_code,
                200,
            )
        self.assertEqual(self.requests, [])
