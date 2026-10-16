"""Strict teacher contracts and bounded, deliberately unreviewed candidate batches."""

import copy
import io
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from decision_workbench.synthetic import generate
from decision_workbench.synthetic_contracts import (
    MAX_RESPONSE,
    request_body,
    teacher_address,
    completion,
    candidate_rows,
)
from test_platform import QUESTIONS, fixture_rows


class SyntheticContractTests(unittest.TestCase):
    def setUp(self):
        self.spec = {"description": "Fictional routing rules", "questions": QUESTIONS}
        self.seeds = fixture_rows(2)
        self.payload = {
            "model": "fixture-teacher-v1",
            "usage": {
                "prompt_tokens": 90,
                "completion_tokens": 20,
                "total_tokens": 110,
            },
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            {
                                "rows": [
                                    {
                                        "state": "Fictional new request",
                                        "gold": self.seeds[0]["gold"],
                                    }
                                ]
                            }
                        )
                    },
                }
            ],
        }

    def test_request_freezes_complete_policy_seeds_and_selected_token_contract(self):
        body = json.loads(
            request_body(
                self.spec,
                self.seeds,
                "teacher",
                1,
                output_tokens=2048,
                token_parameter="max_completion_tokens",
                temperature=None,
            )
        )
        self.assertNotIn("temperature", body)
        self.assertNotIn("max_tokens", body)
        self.assertEqual(body["max_completion_tokens"], 2048)
        prompt = json.loads(body["messages"][1]["content"])
        self.assertEqual(prompt["questions"], QUESTIONS)
        self.assertEqual(
            prompt["examples"],
            [{"state": r["state"], "gold": r["gold"]} for r in self.seeds],
        )
        self.assertEqual(prompt["domain_description"], self.spec["description"])

    def test_oversize_unreviewed_and_invalid_requests_are_not_truncated(self):
        seeds = copy.deepcopy(self.seeds)
        seeds[0]["state"] = "x" * 64000
        with self.assertRaisesRegex(ValueError, "Nothing was truncated"):
            request_body(self.spec, seeds, "teacher", 1)
        seeds = copy.deepcopy(self.seeds)
        seeds[0]["metadata"]["reviewed"] = False
        with self.assertRaises(ValueError):
            request_body(self.spec, seeds, "teacher", 1)
        for options in (
            {"output_tokens": True},
            {"temperature": float("nan")},
            {"token_parameter": "max_output_tokens"},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                request_body(self.spec, self.seeds, "teacher", 1, **options)

    def test_refusal_incomplete_tool_calls_duplicates_and_missing_identity_refuse(self):
        for kind in (
            "length",
            "tool",
            "function",
            "refusal",
            "model",
            "many",
            "duplicate",
            "nonfinite",
        ):
            raw = copy.deepcopy(self.payload)
            if kind == "length":
                raw["choices"][0]["finish_reason"] = "length"
            elif kind == "tool":
                raw["choices"][0]["message"]["tool_calls"] = [{}]
            elif kind == "function":
                raw["choices"][0]["message"]["function_call"] = {"name": "action"}
            elif kind == "refusal":
                raw["choices"][0]["message"]["refusal"] = "No"
            elif kind == "model":
                raw.pop("model")
            elif kind == "many":
                raw["choices"] *= 2
            elif kind == "duplicate":
                raw["choices"][0]["message"]["content"] = '{"rows":[],"rows":[]}'
            else:
                raw["usage"]["prompt_tokens"] = float("nan")
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                completion(json.dumps(raw).encode())

    def test_missing_usage_stays_unknown_but_invalid_reported_usage_refuses(self):
        raw = copy.deepcopy(self.payload)
        raw.pop("usage")
        self.assertIsNone(completion(json.dumps(raw).encode())["usage"])
        for usage in (
            {"prompt_tokens": True, "completion_tokens": 20, "total_tokens": 21},
            {"prompt_tokens": 90, "completion_tokens": 20, "total_tokens": 111},
        ):
            raw["usage"] = usage
            with self.assertRaises(ValueError):
                completion(json.dumps(raw).encode())

    def test_candidates_bind_seed_family_provenance_and_require_human_review(self):
        parsed = completion(json.dumps(self.payload).encode())
        rows = candidate_rows(
            self.spec,
            self.seeds,
            parsed,
            1,
            batch_id="fixed-batch",
            created_at="2026-10-16T00:00:00+00:00",
            provenance={
                "teacher_model": "requested-teacher",
                "request_sha256": "a" * 64,
            },
        )
        row = rows[0]
        self.assertEqual(row["id"], "fixed-batch-0")
        self.assertEqual(row["group_id"], "synthetic-family-fixed-batch")
        self.assertEqual(row["metadata"]["seed_ids"], [r["id"] for r in self.seeds])
        self.assertFalse(row["metadata"]["reviewed"])
        self.assertEqual(
            row["metadata"]["reported_teacher_model"], "fixture-teacher-v1"
        )
        self.assertEqual(row["metadata"]["teacher_model"], "requested-teacher")
        bad = copy.deepcopy(parsed)
        bad["rows"] = [bad["rows"][0], {"state": "Invalid gold", "gold": {}}]
        with self.assertRaises(ValueError):
            candidate_rows(self.spec, self.seeds, bad, 2, batch_id="bad")
        with self.assertRaises(ValueError):
            candidate_rows(self.spec, self.seeds, parsed, 2, batch_id="bad")

    def test_response_size_address_and_credentials_are_bounded(self):
        with self.assertRaises(ValueError):
            completion(b" " * (MAX_RESPONSE + 1))
        for url in (
            "https://user:secret@example.com/v1",
            "https://example.com/v1?key=secret",
            "https://example.com/v1#part",
            "http://remote.example/v1",
            "https://example.com:0/v1",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                teacher_address(url)
        with self.assertRaisesRegex(ValueError, "without control"):
            generate(
                self.spec,
                self.seeds,
                base_url="http://127.0.0.1:1/v1",
                model="teacher",
                api_key="secret\n",
            )

    def test_direct_helper_bounds_reads_and_omits_an_empty_bearer_header(self):
        outer = self

        class Response(io.BytesIO):
            def read(self, n=-1):
                outer.assertEqual(n, MAX_RESPONSE + 1)
                return super().read(n)

        def transport(request, timeout):
            self.assertFalse(request.has_header("Authorization"))
            return Response(json.dumps(self.payload).encode())

        result = generate(
            self.spec,
            self.seeds,
            base_url="http://127.0.0.1:8000/v1",
            model="teacher",
            count=1,
            request=transport,
        )
        self.assertFalse(result[0]["metadata"]["reviewed"])

    def test_direct_http_redirect_does_not_forward_approved_data_or_key(self):
        calls = []

        class Teacher(BaseHTTPRequestHandler):
            def do_POST(handler):
                calls.append(handler.path)
                handler.rfile.read(int(handler.headers["Content-Length"]))
                handler.send_response(307)
                handler.send_header(
                    "Location", f"http://127.0.0.1:{handler.server.server_port}/other"
                )
                handler.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Teacher)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaisesRegex(ValueError, "Teacher HTTP 307"):
                generate(
                    self.spec,
                    self.seeds,
                    base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    model="teacher",
                    api_key="fictional-key",
                    count=1,
                )
            self.assertEqual(calls, ["/v1/chat/completions"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
