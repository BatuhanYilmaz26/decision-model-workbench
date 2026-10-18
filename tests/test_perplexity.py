"""Official Perplexity contract and fictional HTTP application workflows; no paid calls."""

import copy
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx
import test_hosted as fixture
import test_platform as domain
from hosted_fixture_transport import FixtureTransport
from decision_workbench.platform.hosted import (
    Hosted,
    HostedUnavailable,
    decode_response,
    profile_for,
    public_profile,
    request_body,
    validate_profile,
)
from decision_workbench.platform.hosted_transport import (
    TransportFailure,
    endpoint,
    receive,
)
from decision_workbench.platform.perplexity import CONTRACT, MODELS
from decision_workbench.platform.service import Service
from decision_workbench.platform.store import Store


class PerplexityTests(unittest.TestCase):
    # Reuse the shared real localhost API/store/worker fixture, without rerunning
    # the unrelated provider test methods through inheritance.
    write_profiles = fixture.HostedTests.write_profiles
    tearDown = fixture.HostedTests.tearDown
    queue = fixture.HostedTests.queue
    study = fixture.HostedTests.study
    release = fixture.HostedTests.release
    decision = fixture.HostedTests.decision
    child_command = fixture.HostedTests.child_command

    def setUp(self):
        fixture.HostedTests.setUp(self)
        self.profile = fixture.profile("perplexity")
        self.write_profiles()

    def test_documented_models_aliases_and_echo_only_identity(self):
        for model, documented in MODELS.items():
            with self.subTest(model=model):
                p = {**self.profile, "model": model, "response_model": model}
                validate_profile(p)
                frozen = public_profile(p)
                self.assertEqual(frozen["api_contract"]["contract"], CONTRACT)
                self.assertEqual(frozen["api_contract"]["documented_route"], documented)
                self.assertEqual(
                    frozen["api_contract"]["model_identity"], "request-echo-only"
                )
                self.assertNotIn("key_env", frozen)
                self.assertNotIn("tenants", frozen)
                payload = json.loads(
                    request_body(p, "invoice routine", domain.QUESTIONS)
                )
                response = fixture.answer(payload, "perplexity")
                response.pop("id")  # The direct API does not require a JSON id.
                response["usage"].pop(
                    "cost"
                )  # Neither a billed cost nor provider name.
                result = decode_response(response, p, domain.QUESTIONS)
                self.assertEqual(result["identity"]["model"], model)
                self.assertEqual(
                    result["identity"]["api_contract"], frozen["api_contract"]
                )
                self.assertNotIn("cost", result["identity"]["usage"])
                if model != documented:
                    with self.assertRaises(HostedUnavailable):
                        decode_response(
                            {**response, "model": documented}, p, domain.QUESTIONS
                        )
        for change in (
            {"model": "pplx-decidr-latest"},
            {"model": "perplexity/pplx-decider-v1.2"},
            {"response_model": "pplx-decider-latest"},
            {"response_provider": "Perplexity"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_profile({**self.profile, **change})

    def test_pre_remapping_policy_refused_before_network_or_reservation(self):
        self.profile.update(
            model="pplx-decider-v1.1-27b", response_model="pplx-decider-v1.1-27b"
        )
        self.write_profiles()
        old = public_profile(self.profile)
        old.pop("api_contract")
        with self.assertRaisesRegex(HostedUnavailable, "successor"):
            self.hosted.call(
                fixture.OWNER,
                self.project,
                old,
                "invoice routine",
                domain.QUESTIONS,
                "legacy-study-call",
                {},
            )
        self.assertEqual(self.requests, [])
        self.assertEqual(self.store.list("A", self.project, "provider-call"), [])
        self.assertEqual(
            profile_for("A", "perplexity")["model"], "pplx-decider-v1.1-27b"
        )

    def test_question_media_byte_and_serialization_refusals(self):
        q = {
            "review": {"type": "noul", "instructions": "Does evidence require review?"}
        }
        for state in ("evidence", {"body": "evidence"}, ["evidence", {"amount": 2}]):
            wire = json.loads(request_body(self.profile, state, q))
            self.assertEqual(wire["state"], state)
            self.assertEqual(set(wire), {"model", "state", "questions"})
        image = {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,fictional"},
        }
        for state in (image, ["look", image], {"document": {"photo": image}}):
            with self.assertRaisesRegex(ValueError, "image parts"):
                request_body(self.profile, state, q)
        for field, value in (
            ("temperature", 0),
            ("images", []),
            ("criteria", {"yes": "Yes", "no": "No"}),
        ):
            bad = copy.deepcopy(q)
            bad["review"][field] = value
            with self.assertRaises(ValueError):
                request_body(self.profile, "evidence", bad)
        valid = copy.deepcopy(q)
        valid["review"]["criteria"] = {"true": "Review", "false": "No review"}
        request_body(self.profile, "evidence", valid)
        with self.assertRaises(ValueError):
            request_body(self.profile, "x" * 64000, q)
        with self.assertRaises(ValueError):
            request_body(self.profile, {"value": float("nan")}, q)
        cycle = []
        cycle.append(cycle)
        with self.assertRaises(ValueError):
            request_body(self.profile, cycle, q)
        self.assertEqual(self.requests, [])

    def test_complete_token_usage_required_and_output_tokens_can_be_nonzero(self):
        payload = json.loads(
            request_body(self.profile, "invoice routine", domain.QUESTIONS)
        )
        good = fixture.answer(payload, "perplexity")
        good["usage"] = {"input_tokens": 367, "output_tokens": 3}
        self.assertEqual(
            decode_response(good, self.profile, domain.QUESTIONS)["identity"]["usage"],
            good["usage"],
        )
        for usage in (
            {},
            {"input_tokens": 3},
            {"input_tokens": True, "output_tokens": 0},
            {"input_tokens": 3, "output_tokens": -1},
        ):
            with self.subTest(usage=usage), self.assertRaises(HostedUnavailable):
                decode_response(
                    {**good, "usage": usage}, self.profile, domain.QUESTIONS
                )

    def test_fixed_endpoint_bearer_timeout_and_one_attempt_on_errors(self):
        p = {**self.profile, "timeout_seconds": 30}
        body = request_body(p, "invoice routine", domain.QUESTIONS)
        attempts = []

        def respond(request):
            attempts.append(request)
            self.assertEqual(str(request.url), "https://api.perplexity.ai/v1/decisions")
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.headers["authorization"], "Bearer fictional-key")
            self.assertNotIn("x-api-key", request.headers)
            self.assertEqual(request.extensions["timeout"]["read"], 30)
            return httpx.Response(
                200,
                stream=httpx.ByteStream(
                    json.dumps(fixture.answer(json.loads(body), "perplexity")).encode()
                ),
            )

        self.assertEqual(endpoint(p), "https://api.perplexity.ai/v1/decisions")
        decode_response(
            json.loads(receive(p, body, "fictional-key", httpx.MockTransport(respond))),
            p,
            domain.QUESTIONS,
        )
        self.assertEqual(len(attempts), 1)
        for status in (301, 400, 401, 404, 405, 413, 429, 500, 504):
            seen = []

            def fail(request):
                seen.append(request)
                return httpx.Response(
                    status,
                    headers={"location": "https://example.invalid", "retry-after": "1"},
                    stream=httpx.ByteStream(b"<html>fictional error</html>"),
                )

            with self.subTest(status=status), self.assertRaises(TransportFailure):
                receive(p, body, "fictional-key", httpx.MockTransport(fail))
            self.assertEqual(len(seen), 1)

    def test_both_models_study_release_restart_and_rollback(self):
        profiles = [
            {
                **self.profile,
                "id": name.replace(".", "-"),
                "model": name,
                "response_model": name,
            }
            for name in ("pplx-decider-v1.2", "pplx-decider-flash-v1.2")
        ]
        self.write_profiles(profiles)
        models = []
        for index, p in enumerate(profiles):
            self.profile = p
            model = self.study("perplexity-study-" + str(index))
            models.append(model)
            self.release(model, index)
            response = self.decision("perplexity-inference-" + str(index))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["answers"]["team"]["answer"], "billing")
            self.assertTrue(
                self.decision("perplexity-inference-" + str(index)).json()["provider"][
                    "cached"
                ]
            )
            bundle = self.store.get(model, "A", self.project, "model")["payload"]
            self.assertEqual(bundle["report"]["provider_usage"]["calls"], 54)
            self.assertEqual(
                bundle["configuration"]["profile"]["api_contract"]["question_context"],
                "joint-request",
            )
        self.assertEqual(len(self.requests), 110)
        self.assertEqual(
            {r["payload"]["model"] for r in self.requests},
            {p["model"] for p in profiles},
        )
        self.assertTrue(
            all(
                set(r["payload"]) == {"model", "state", "questions"}
                and r["path"] == "/v1/decisions"
                for r in self.requests
            )
        )
        self.release(models[0], 2)
        cold_store = Store(self.store.folder)
        cold = Service(
            cold_store,
            hosted=Hosted(cold_store, FixtureTransport(self.server.server_port)),
        )
        try:
            result = cold.predict(
                fixture.OWNER,
                self.project,
                "invoice urgent",
                0.8,
                "perplexity-cold-inference",
            )
            self.assertEqual(result["model_id"], models[0])
            self.assertEqual(result["release_revision"], 3)
        finally:
            cold.runtime.close()
        self.assertNotIn("fictional-provider-credential", json.dumps(self.requests))

    def test_isolated_http_transport_and_portable_recovery(self):
        fixture.HostedTests.test_actual_isolated_process_http_and_default_hosted_wiring(
            self
        )

    def test_recovery_refuses_changed_contract_assurance(self):
        from decision_workbench.platform.hosted import validate_call_history

        self.hosted.call(
            fixture.OWNER,
            self.project,
            public_profile(self.profile),
            "invoice routine",
            domain.QUESTIONS,
            "perplexity-history-check",
            {},
        )
        validate_call_history(self.store)
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT id,payload FROM entities WHERE kind='provider-call-result'"
            ).fetchone()
            value = json.loads(row["payload"])
            value["result"]["identity"]["api_contract"][
                "model_identity"
            ] = "immutable-weights"
            db.execute(
                "UPDATE entities SET payload=? WHERE id=?",
                (json.dumps(value), row["id"]),
            )
        with self.assertRaisesRegex(ValueError, "identity history"):
            validate_call_history(self.store)

    def test_legacy_call_history_remains_readable_without_enabling_old_policy(self):
        from decision_workbench.platform.hosted import validate_call_history
        from decision_workbench.platform.store import digest

        self.hosted.call(
            fixture.OWNER,
            self.project,
            public_profile(self.profile),
            "invoice routine",
            domain.QUESTIONS,
            "perplexity-legacy-history",
            {},
        )
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT id,payload FROM entities WHERE kind='provider-call'"
            ).fetchone()
            call = json.loads(row["payload"])
            call["profile"].pop("api_contract")
            call["profile"].update(
                model="pplx-decider-v1.1-27b", response_model="pplx-decider-v1.1-27b"
            )
            call["profile_sha256"] = digest(call["profile"])
            db.execute(
                "UPDATE entities SET payload=? WHERE id=?",
                (json.dumps(call), row["id"]),
            )
            row = db.execute(
                "SELECT id,payload FROM entities WHERE kind='provider-call-result'"
            ).fetchone()
            outcome = json.loads(row["payload"])
            outcome["result"]["identity"].pop("api_contract")
            outcome["result"]["identity"]["model"] = "pplx-decider-v1.1-27b"
            db.execute(
                "UPDATE entities SET payload=? WHERE id=?",
                (json.dumps(outcome), row["id"]),
            )
        validate_call_history(self.store)
        with self.assertRaisesRegex(HostedUnavailable, "successor"):
            profile_for("A", "perplexity", call["profile"])

    def test_browser_consent_study_release_inference_and_settlement(self):
        from streamlit.testing.v1 import AppTest

        instances = []
        create = AppTest.from_file

        def capture(*args, **kwargs):
            at = create(*args, **kwargs)
            instances.append(at)
            return at

        with patch.object(AppTest, "from_file", side_effect=capture):
            fixture.HostedTests.test_hosted_browser_consent_queue_release_and_inference_over_http(
                self
            )
        self.assertTrue(
            any("echoes your request" in value.value for value in instances[0].info)
        )

    def test_runnable_example_uses_user_key_and_safe_failure(self):
        spec = importlib.util.spec_from_file_location(
            "perplexity_example", Path("examples/perplexity/predict.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        request = self.root / "request.json"
        request.write_text(
            json.dumps({"state": "invoice routine", "questions": domain.QUESTIONS}),
            encoding="utf-8",
        )
        calls = []

        def exchange(p, body, key):
            calls.append((p, json.loads(body), key))
            return json.dumps(fixture.answer(json.loads(body), "perplexity")).encode()

        with patch.dict(
            "os.environ", {"DMT_PROVIDER_PERPLEXITY_API_KEY": "fictional-user-key"}
        ), patch.object(module, "run_process", side_effect=exchange):
            for model in ("pplx-decider-v1.2", "pplx-decider-flash-v1.2"):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    self.assertEqual(
                        module.main(["--request", str(request), "--model", model]), 0
                    )
                self.assertEqual(
                    json.loads(stdout.getvalue())["identity"]["model"], model
                )
                self.assertNotIn("fictional-user-key", stdout.getvalue())
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(c[2] == "fictional-user-key" for c in calls))
        with patch.dict(
            "os.environ", {"DMT_PROVIDER_PERPLEXITY_API_KEY": ""}
        ), patch.object(module, "run_process") as transport, contextlib.redirect_stderr(
            io.StringIO()
        ):
            self.assertEqual(module.main(["--request", str(request)]), 2)
            transport.assert_not_called()


if __name__ == "__main__":
    unittest.main()
