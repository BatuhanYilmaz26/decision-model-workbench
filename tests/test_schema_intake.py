"""Actual teacher HTTP, durable worker, review, isolation and recovery contracts."""

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from decision_workbench.platform.api import create_app
from decision_workbench.platform import schema_intake as intake
from decision_workbench.platform.store import Store, encode
from decision_workbench.training.resources import ResourceLease, ResourceBusy
from decision_workbench.platform.worker import Worker
from decision_workbench.platform.recovery import backup_workspace, restore_workspace
from test_platform import QUESTIONS, keys

DOMAIN = {
    "name": "Fictional intake",
    "description": "Route invoice questions to billing, crashes to engineering and assess refund requests and urgency.",
    "data_requirements": "Decision-time evidence, rare cases and independent evaluation records",
}


class Teacher:
    def __init__(self):
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.delay = False
        self.result = {
            "questions": QUESTIONS,
            "assumptions": ["The owner must define the urgency policy."],
        }
        self.finish_reason = "stop"
        self.raw = None
        self.usage = None
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fixture.calls.append((self.path, dict(self.headers), body))
                fixture.started.set()
                if fixture.delay:
                    fixture.release.wait(15)
                value = {
                    "model": "fictional-teacher",
                    "choices": [
                        {
                            "finish_reason": fixture.finish_reason,
                            "message": {
                                "content": (
                                    fixture.raw
                                    if fixture.raw is not None
                                    else json.dumps(fixture.result)
                                )
                            },
                        }
                    ],
                }
                if fixture.usage is not None:
                    value["usage"] = fixture.usage
                data = json.dumps(value).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


class SchemaIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = (Path(self.temp.name) / "workspace").resolve()
        self.teacher = Teacher()
        self.environment = patch.dict(
            os.environ,
            {
                "DMT_TEACHER_BASE_URL": self.teacher.url,
                "DMT_TEACHER_MODEL": "fictional-teacher",
                "DMT_TEACHER_API_KEY": "private-fixture-key",
                "DMT_SCHEMA_TIMEOUT_SECONDS": "10",
                "DMT_SCHEMA_CALLS_PER_DAY": "20",
            },
        )
        self.environment.start()
        self.app = create_app(self.home, keys())
        self.client = TestClient(self.app)
        self.client.headers["Authorization"] = "Bearer a-secret"
        self.worker = Worker(Store(self.home))

    def tearDown(self):
        self.teacher.close()
        self.worker.service.runtime.close()
        self.app.state.service.runtime.close()
        self.client.close()
        self.environment.stop()
        self.temp.cleanup()

    def submit(self, key="schema-fixture-1", **changes):
        body = {
            **DOMAIN,
            "request_key": key,
            "data_approved": True,
            "cost_approved": True,
            **changes,
        }
        return self.client.post("/v1/schema-intakes", json=body)

    def detail(self, identifier):
        response = self.client.get("/v1/schema-intakes/" + identifier)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def confirm(self, identifier, **changes):
        value = self.detail(identifier)
        return self.client.post(
            f"/v1/schema-intakes/{identifier}/confirm",
            json={
                **DOMAIN,
                "questions": QUESTIONS,
                "confirmed": True,
                "expected_proposal_sha256": value["job"]["result"]["proposal_sha256"],
                **changes,
            },
        )

    def test_real_teacher_worker_review_and_idempotent_project_confirmation(self):
        response = self.submit()
        self.assertEqual(response.status_code, 202, response.text)
        identifier = response.json()["id"]
        self.assertEqual(self.client.get("/v1/projects").json(), [])
        self.assertEqual(
            self.submit().json()["job"]["id"], response.json()["job"]["id"]
        )
        self.assertTrue(self.worker.once())
        value = self.detail(identifier)
        self.assertEqual(value["job"]["status"], "succeeded", value)
        self.assertTrue(value["job"]["result"]["review_required"])
        self.assertEqual(value["job"]["result"]["questions"], QUESTIONS)
        self.assertEqual(len(self.teacher.calls), 1)
        path, headers, sent = self.teacher.calls[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer private-fixture-key")
        self.assertEqual(sent["model"], "fictional-teacher")
        self.assertEqual(json.loads(sent["messages"][1]["content"])["domain"], DOMAIN)
        self.assertNotIn("private-fixture-key", json.dumps(value))
        self.assertEqual(self.client.get("/v1/projects").json(), [])
        self.assertEqual(self.confirm(identifier, confirmed=False).status_code, 400)
        edited = copy.deepcopy(QUESTIONS)
        edited["refund"]["instructions"] = "Owner-reviewed refund policy"
        created = self.confirm(identifier, questions=edited)
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["payload"]["questions"], edited)
        self.assertEqual(
            self.detail(identifier)["confirmed_project"]["id"], created.json()["id"]
        )
        self.assertEqual(
            self.confirm(identifier, questions=edited).json()["id"],
            created.json()["id"],
        )
        self.assertEqual(self.confirm(identifier).status_code, 409)
        self.assertEqual(len(self.client.get("/v1/projects").json()), 1)
        self.assertFalse(self.worker.once())

    def test_roles_tenants_and_untrusted_endpoints_are_refused(self):
        value = self.submit().json()
        for token in ("review-secret", "infer-secret"):
            for method, path, kwargs in [
                ("GET", "/v1/schema-teacher", {}),
                ("GET", "/v1/schema-intakes", {}),
                ("GET", "/v1/schema-intakes/" + value["id"], {}),
                (
                    "POST",
                    "/v1/schema-intakes",
                    {
                        "json": {
                            **DOMAIN,
                            "request_key": "refused-role",
                            "data_approved": True,
                            "cost_approved": True,
                        }
                    },
                ),
                ("POST", f"/v1/schema-intakes/{value['id']}/cancel", {}),
                (
                    "POST",
                    f"/v1/schema-intakes/{value['id']}/confirm",
                    {
                        "json": {
                            **DOMAIN,
                            "questions": QUESTIONS,
                            "confirmed": True,
                            "expected_proposal_sha256": "0" * 64,
                        }
                    },
                ),
            ]:
                r = self.client.request(
                    method, path, headers={"Authorization": "Bearer " + token}, **kwargs
                )
                self.assertEqual(r.status_code, 403, r.text)
        crossed = self.client.get(
            "/v1/schema-intakes/" + value["id"],
            headers={"Authorization": "Bearer b-secret"},
        )
        self.assertEqual(crossed.status_code, 404)
        self.assertEqual(
            self.client.post(
                f"/v1/schema-intakes/{value['id']}/cancel",
                headers={"Authorization": "Bearer b-secret"},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.submit(
                "unexpected-profile", base_url="https://unapproved.example"
            ).status_code,
            422,
        )

    def test_approval_input_and_quota_are_enforced_before_any_call(self):
        for change in (
            {"data_approved": False},
            {"cost_approved": False},
            {"name": " "},
            {"description": " "},
        ):
            self.assertEqual(self.submit(**change).status_code, 400)
        self.assertEqual(self.client.get("/v1/schema-intakes").json(), [])
        with patch.dict(os.environ, {"DMT_SCHEMA_CALLS_PER_DAY": "1"}):
            first = self.submit()
            self.assertEqual(first.status_code, 202)
            self.assertEqual(self.submit().status_code, 202)
            self.assertEqual(self.submit("over-daily-limit").status_code, 409)
        self.assertEqual(len(self.teacher.calls), 0)

    def test_atomic_creation_rolls_back_both_intake_and_job(self):
        original = self.app.state.store.audit

        def fail(db, tenant, project, actor, action, target):
            if action == "submit.schema-suggest":
                raise ValueError("fixture write failure")
            return original(db, tenant, project, actor, action, target)

        with patch.object(self.app.state.store, "audit", side_effect=fail):
            self.assertEqual(self.submit().status_code, 400)
        self.assertEqual(self.client.get("/v1/schema-intakes").json(), [])
        with self.app.state.store.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM jobs").fetchone()[0], 0)

    def test_concurrent_duplicate_intake_has_one_durable_job(self):
        service = self.app.state.service
        owner = {"tenant": "A", "actor": "owner", "role": "admin"}

        def queue(_):
            return intake.submit(
                service, owner, DOMAIN, "concurrent-schema", True, True
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            values = list(executor.map(queue, range(2)))
        self.assertEqual(values[0]["job"]["id"], values[1]["job"]["id"])
        self.assertEqual(len(self.client.get("/v1/schema-intakes").json()), 1)

    def test_configuration_drift_and_invalid_profile_never_dispatch(self):
        identifier = self.submit().json()["id"]
        with patch.dict(os.environ, {"DMT_TEACHER_MODEL": "changed-model"}):
            self.worker.once()
        self.assertEqual(self.detail(identifier)["job"]["status"], "failed")
        self.assertEqual(len(self.teacher.calls), 0)
        for endpoint in (
            "http://remote.example/v1",
            "https://user:password@example.com/v1",
            "https://example.com/v1?key=secret",
        ):
            with patch.dict(os.environ, {"DMT_TEACHER_BASE_URL": endpoint}):
                self.assertFalse(
                    self.client.get("/v1/schema-teacher").json()["available"]
                )

    def test_invalid_teacher_response_never_creates_project_or_retries(self):
        cases = [
            ({"questions": {}, "assumptions": []}, "stop", None),
            (
                {"questions": QUESTIONS, "assumptions": [], "approved": True},
                "stop",
                None,
            ),
            ({"questions": QUESTIONS, "assumptions": []}, "length", None),
            (None, "stop", '{"questions":{},"questions":{},"assumptions":[]}'),
        ]
        for index, (result, finish, raw) in enumerate(cases):
            self.teacher.result, self.teacher.finish_reason, self.teacher.raw = (
                result,
                finish,
                raw,
            )
            identifier = self.submit("invalid-schema-" + str(index)).json()["id"]
            self.worker.once()
            value = self.detail(identifier)
            self.assertEqual(value["job"]["status"], "failed", value)
            self.assertEqual(
                self.submit("invalid-schema-" + str(index)).json()["job"]["id"],
                value["job"]["id"],
            )
        self.assertEqual(len(self.teacher.calls), len(cases))
        self.assertEqual(self.client.get("/v1/projects").json(), [])

    def test_queue_cancellation_does_not_call_teacher(self):
        identifier = self.submit().json()["id"]
        response = self.client.post(f"/v1/schema-intakes/{identifier}/cancel")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["job"]["status"], "cancelled")
        self.assertFalse(self.worker.once())
        self.assertEqual(len(self.teacher.calls), 0)

    def test_local_teacher_without_api_key_uses_no_authorization_header(self):
        os.environ.pop("DMT_TEACHER_API_KEY", None)
        identifier = self.submit().json()["id"]
        self.worker.once()
        self.assertEqual(self.detail(identifier)["job"]["status"], "succeeded")
        self.assertEqual(len(self.teacher.calls), 1)
        self.assertNotIn("Authorization", self.teacher.calls[0][1])

    def test_saved_request_and_confirmed_project_survive_prompt_code_changes(self):
        identifier = self.submit().json()["id"]
        payload = intake.intake_payload(self.app.state.store, "A", identifier)
        self.assertEqual(
            payload["request_body"].encode("utf-8"),
            intake.request_body(DOMAIN, payload["profile"]),
        )
        self.worker.once()
        self.assertEqual(self.confirm(identifier).status_code, 201)
        with patch.object(
            intake, "request_body", side_effect=ValueError("New prompt version")
        ):
            self.assertEqual(self.detail(identifier)["job"]["status"], "succeeded")
            backup_workspace(self.home, Path(self.temp.name) / "prompt-upgrade-backup")

    def test_rehashed_unapproved_prompt_cannot_dispatch(self):
        value = self.submit().json()
        payload = intake.intake_payload(self.app.state.store, "A", value["id"])
        request = json.loads(payload["request_body"])
        request["messages"][0][
            "content"
        ] = "Substituted after the owner approved the request"
        payload["request_body"] = encode(request)
        payload["request_sha256"] = hashlib.sha256(
            payload["request_body"].encode()
        ).hexdigest()
        with self.app.state.store.transaction() as db:
            db.execute(
                "UPDATE entities SET payload=? WHERE id=?",
                (encode(payload), value["id"]),
            )
            db.execute(
                "UPDATE jobs SET config=? WHERE id=?",
                (encode(payload), value["job"]["id"]),
            )
        self.worker.once()
        self.assertEqual(self.detail(value["id"])["job"]["status"], "failed")
        self.assertEqual(len(self.teacher.calls), 0)
        self.assertEqual(self.client.get("/v1/projects").json(), [])

    def test_reviewed_domain_mutation_is_refused_by_backup(self):
        identifier = self.submit().json()["id"]
        self.worker.once()
        created = self.confirm(identifier).json()
        payload = created["payload"]
        payload["description"] = "Changed after owner confirmation"
        with self.app.state.store.transaction() as db:
            db.execute(
                "UPDATE entities SET payload=? WHERE id=?",
                (encode(payload), created["id"]),
            )
        with self.assertRaisesRegex(ValueError, "Reviewed schema provenance changed"):
            backup_workspace(self.home, Path(self.temp.name) / "changed-policy-backup")

    @unittest.skipUnless(
        sys.platform in ("win32", "linux"), "Native child ownership contract"
    )
    def test_actual_worker_crash_ends_transport_and_does_not_replay_request(self):
        self.teacher.delay = True
        value = self.submit().json()
        options = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "decision_workbench.platform.worker",
                "--home",
                str(self.home),
                "--once",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **options,
        )
        lock = self.home / "schema-teacher.lock"
        try:
            self.assertTrue(
                self.teacher.started.wait(10),
                "Independent worker never reached teacher",
            )
            with self.assertRaises(ResourceBusy):
                ResourceLease(lock).acquire(timeout=0.05)
            process.kill()
            process.wait(timeout=10)
            deadline = time.monotonic() + 8
            while True:
                try:
                    with ResourceLease(lock).hold(timeout=0.05):
                        break
                except ResourceBusy:
                    if time.monotonic() >= deadline:
                        self.fail(
                            "Orphan teacher transport retained capacity after worker crash"
                        )
            # Expire the fixture lease to exercise recovery without a 30-second wait.
            with self.app.state.store.transaction() as db:
                db.execute("UPDATE jobs SET lease=0 WHERE id=?", (value["job"]["id"],))
            self.assertFalse(self.worker.once())
            outcome = self.detail(value["id"])
            self.assertEqual(outcome["job"]["status"], "interrupted")
            self.assertIsNone(outcome["job"]["result"])
            self.assertEqual(len(self.teacher.calls), 1)
            self.assertEqual(self.client.get("/v1/projects").json(), [])
            backup_workspace(self.home, Path(self.temp.name) / "crashed-intake-backup")
        finally:
            self.teacher.release.set()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)

    def test_running_cancellation_stops_transport_and_discards_proposal(self):
        self.teacher.delay = True
        identifier = self.submit().json()["id"]
        run = threading.Thread(target=self.worker.once)
        run.start()
        try:
            self.assertTrue(self.teacher.started.wait(8))
            self.client.post(f"/v1/schema-intakes/{identifier}/cancel")
            run.join(timeout=8)
            self.assertFalse(run.is_alive())
            value = self.detail(identifier)
            self.assertEqual(value["job"]["status"], "cancelled", value)
            self.assertIsNone(value["job"]["result"])
        finally:
            self.teacher.release.set()
            run.join(timeout=12)

    def test_teacher_deadline_records_failure_without_automatic_replay(self):
        self.teacher.delay = True
        with patch.dict(os.environ, {"DMT_SCHEMA_TIMEOUT_SECONDS": "5"}):
            identifier = self.submit().json()["id"]
            started = time.monotonic()
            self.worker.once()
            self.assertLess(time.monotonic() - started, 10)
            value = self.detail(identifier)
            self.assertEqual(value["job"]["status"], "failed", value)
            self.assertIn("charges may have occurred", value["job"]["error"])
            self.assertFalse(self.worker.once())
        self.assertEqual(len(self.teacher.calls), 1)

    def test_auth_change_rolls_back_reviewed_project_creation(self):
        identifier = self.submit().json()["id"]
        self.worker.once()
        value = self.detail(identifier)
        checks = []

        def authorize():
            checks.append(True)
            if len(checks) == 2:
                raise PermissionError("Fixture permission changed")

        with self.assertRaises(PermissionError):
            intake.confirm(
                self.app.state.service,
                {"tenant": "A", "actor": "owner", "role": "admin"},
                identifier,
                DOMAIN,
                QUESTIONS,
                value["job"]["result"]["proposal_sha256"],
                True,
                authorize=authorize,
            )
        self.assertEqual(self.client.get("/v1/projects").json(), [])

    def test_drafts_and_confirmed_provenance_survive_backup_and_restore(self):
        identifier = self.submit().json()["id"]
        self.worker.once()
        created = self.confirm(identifier)
        self.assertEqual(created.status_code, 201, created.text)
        queued = self.submit("pending-intake").json()["id"]
        backup = backup_workspace(self.home, Path(self.temp.name) / "backup")
        restored = Path(self.temp.name) / "restored"
        restore_workspace(
            Path(self.temp.name) / "backup", restored, backup["manifest_sha256"]
        )
        service = self.app.state.service
        owner = {"tenant": "A", "actor": "owner", "role": "admin"}
        from decision_workbench.platform.service import Service

        other = Service(Store(restored))
        try:
            self.assertEqual(
                intake.view(other, owner, identifier)["job"]["status"], "succeeded"
            )
            self.assertEqual(
                intake.view(other, owner, queued)["job"]["status"], "interrupted"
            )
            self.assertEqual(
                other.project(owner, created.json()["id"])["payload"]["schema_intake"][
                    "intake_id"
                ],
                identifier,
            )
        finally:
            other.runtime.close()
        result = self.detail(identifier)["job"]["result"]
        job_id = self.detail(identifier)["job"]["id"]
        result["questions"]["refund"]["instructions"] = "Changed after approval"
        with service.store.transaction() as db:
            db.execute("UPDATE jobs SET result=? WHERE id=?", (encode(result), job_id))
        with self.assertRaises(ValueError):
            backup_workspace(self.home, Path(self.temp.name) / "tampered-backup")
