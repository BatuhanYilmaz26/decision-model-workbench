"""Runnable release-pinned client against the real CPU API and adverse HTTP replies."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock

import httpx

from http_server_fixture import LiveServer
import test_platform as platform_fixtures
import test_release_pins as fixtures

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "platform_client_example", ROOT / "examples/platform/predict.py"
)
client_example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client_example)


class PlatformClientTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ReleasePinTests("runTest")
        self.f.setUp()
        self.body = {
            **self.f.body,
            "threshold": 0.0,
            "expected_release": self.f.pin,
            "request_key": "client-logical-request",
        }

    def tearDown(self):
        self.f.tearDown()

    def test_real_http_acceptance_abstention_and_release_conflict(self):
        with LiveServer(self.f.f.home, platform_fixtures.keys()) as live:
            result = client_example.request_decision(
                live.url, self.f.f.project, "infer-secret", self.body
            )
            self.assertEqual(result["status"], "accepted", result)
            self.assertEqual(result["decision"]["release_revision"], 1)
            review = client_example.request_decision(
                live.url,
                self.f.f.project,
                "infer-secret",
                {**self.body, "state": "unfamiliar evidence", "threshold": 1.0},
            )
            self.assertEqual(review["reason"], "abstained", review)
            self.assertNotIn("decision", review)
            self.f.release(self.f.model, 1)
            conflict = client_example.request_decision(
                live.url, self.f.f.project, "infer-secret", self.body
            )
            self.assertEqual(
                conflict,
                {"status": "review_required", "reason": "conflict", "http_status": 409},
            )

    def test_cli_uses_a_user_key_without_printing_it(self):
        path = self.f.f.home.parent / "client-request.json"
        path.write_text(json.dumps(self.body), encoding="utf-8")
        with LiveServer(self.f.f.home, platform_fixtures.keys()) as live:
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "examples/platform/predict.py"),
                    "--base-url",
                    live.url,
                    "--project",
                    self.f.f.project,
                    "--request",
                    str(path),
                    "--timeout",
                    "20",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "DMT_INFERENCE_KEY": "infer-secret"},
            )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)["status"], "accepted")
        self.assertNotIn("infer-secret", result.stdout + result.stderr)

    def test_invalid_release_answers_and_abstention_never_return_a_decision(self):
        response = self.f.predict(self.f.pin)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        changes = []
        for name, value in (
            ("model_id", "f" * 32),
            ("release_revision", True),
            ("release_revision", 2),
            ("schema_sha256", "f" * 64),
            ("answers", {}),
            ("abstained", "false"),
        ):
            changed = copy.deepcopy(payload)
            changed[name] = value
            changes.append(changed)
        for name, value in (
            ("confidence", True),
            ("confidence", 10**1000),
            ("confidence", 0.01),
            ("abstained", 0),
            ("probabilities", {"billing": float("nan"), "engineering": 0.5}),
            ("answer", "not-an-option"),
        ):
            changed = copy.deepcopy(payload)
            changed["answers"]["team"][name] = value
            changes.append(changed)
        for changed in changes:
            calls = []

            def handler(request):
                calls.append(request)
                return httpx.Response(200, content=json.dumps(changed).encode())

            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                result = client_example.request_decision(
                    "http://127.0.0.1:8080",
                    self.f.f.project,
                    "fictional",
                    self.body,
                    client=client,
                )
            self.assertEqual(result["status"], "review_required", result)
            self.assertNotIn("decision", result)
            self.assertEqual(len(calls), 1)
        changed = copy.deepcopy(payload)
        changed["answers"]["team"]["model_abstained"] = True
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=changed)
            )
        ) as client:
            result = client_example.request_decision(
                "http://127.0.0.1:8080",
                self.f.f.project,
                "fictional",
                self.body,
                client=client,
            )
        self.assertEqual(result["reason"], "abstained")
        self.assertNotIn("decision", result)

    def test_one_attempt_redirect_error_timeout_and_size_failures(self):
        variants = [
            httpx.Response(307, headers={"Location": "https://unapproved.invalid/"}),
            httpx.Response(503),
            httpx.Response(200, content=b" " * (client_example.MAX_RESPONSE_BYTES + 1)),
            httpx.Response(200, content=b'{"release_revision":1,"release_revision":2}'),
            httpx.Response(
                200, content=b"{}", headers={"Content-Encoding": "identity"}
            ),
        ]
        for response in variants:
            calls = []

            def handler(request):
                calls.append(request)
                return response

            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                result = client_example.request_decision(
                    "http://127.0.0.1:8080",
                    self.f.f.project,
                    "fictional",
                    self.body,
                    client=client,
                )
            self.assertEqual(result["status"], "review_required")
            self.assertEqual(len(calls), 1)
        calls = []

        def timeout(request):
            calls.append(request)
            raise httpx.ReadTimeout("fictional timeout")

        with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
            result = client_example.request_decision(
                "http://127.0.0.1:8080",
                self.f.f.project,
                "fictional",
                self.body,
                client=client,
            )
        self.assertEqual(result["reason"], "unavailable")
        self.assertEqual(len(calls), 1)

    def test_invalid_inputs_refuse_before_any_transport(self):
        handler = Mock(side_effect=AssertionError("Transported invalid request"))
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            for request in (
                {"state": "evidence"},
                {**self.body, "threshold": True},
                {**self.body, "threshold": 10**1000},
                {
                    **self.body,
                    "expected_release": {**self.f.pin, "release_revision": False},
                },
                {**self.body, "state": float("inf")},
            ):
                result = client_example.request_decision(
                    "http://127.0.0.1:8080",
                    self.f.f.project,
                    "fictional",
                    request,
                    client=client,
                )
                self.assertEqual(result["reason"], "invalid_request")
            for url in (
                "http://remote.invalid",
                "http://key:secret@localhost",
                "https://example.invalid?key=secret",
                "file:///tmp/api",
            ):
                result = client_example.request_decision(
                    url, self.f.f.project, "fictional", self.body, client=client
                )
                self.assertEqual(result["reason"], "invalid_request")
        handler.assert_not_called()
