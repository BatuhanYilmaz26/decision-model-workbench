"""Actual personal HTTP/worker lifecycle, entry UI and shared-mode boundaries."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from decision_workbench.data import jsonl
from decision_workbench.platform.api import create_app
from decision_workbench.platform.local import LocalWorkspace, browser_environment
from decision_workbench.platform import capabilities
from decision_workbench.ui.onboarding import personal_workspace, local_backend_available
from test_platform import fixture_rows, QUESTIONS, keys

ROOT = Path(__file__).resolve().parents[1]


class PersonalWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = (Path(self.temp.name) / "personal").resolve()
        self.workspace = LocalWorkspace(self.home, ROOT)

    def tearDown(self):
        self.workspace.close()
        personal_workspace.clear()
        self.temp.cleanup()

    def wait_job(self, client, prefix, job):
        deadline = time.monotonic() + 60
        while True:
            found = next(
                j for j in client.get(prefix + "/jobs").json() if j["id"] == job
            )
            if found["status"] not in ("queued", "running"):
                self.assertEqual(found["status"], "succeeded", found["error"])
                return found["result"]
            if time.monotonic() >= deadline:
                self.fail("Personal worker did not finish the fixture job")
            time.sleep(0.1)

    def test_personal_cpu_lifecycle_persists_and_rejects_expired_and_missing_keys(self):
        seen = []

        class Teacher(BaseHTTPRequestHandler):
            def do_POST(self):
                request = json.loads(
                    self.rfile.read(int(self.headers["Content-Length"]))
                )
                seen.append(request)
                content = json.dumps(
                    {
                        "rows": [
                            {
                                "state": "Fictional invoice refund requested routine",
                                "gold": fixture_rows(1)[0]["gold"],
                            }
                        ]
                    }
                )
                body = json.dumps(
                    {"choices": [{"message": {"content": content}}]}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        import threading

        teacher = ThreadingHTTPServer(("127.0.0.1", 0), Teacher)
        teacher_thread = threading.Thread(target=teacher.serve_forever, daemon=True)
        teacher_thread.start()
        self.addCleanup(teacher.server_close)
        self.addCleanup(teacher_thread.join, 5)
        self.addCleanup(teacher.shutdown)
        self.workspace.environment.update(
            DMT_TEACHER_BASE_URL=f"http://127.0.0.1:{teacher.server_port}/v1",
            DMT_TEACHER_MODEL="fixture-teacher",
        )
        w = self.workspace.start()
        children = list(w.processes)
        token = w.token
        with httpx.Client(
            base_url=w.url,
            headers={"Authorization": "Bearer " + token},
            timeout=10,
            trust_env=False,
        ) as client:
            self.assertEqual(
                client.get("/v1/me", headers={"Authorization": ""}).status_code, 401
            )
            created = client.post(
                "/v1/projects",
                json={
                    "name": "Personal CPU fixture",
                    "description": "Fictional routing policy",
                    "questions": QUESTIONS,
                },
            )
            self.assertEqual(created.status_code, 201, created.text)
            project = created.json()["id"]
            prefix = "/v1/projects/" + project
            upload = client.post(
                prefix + "/datasets",
                files={"file": ("fictional.jsonl", jsonl(fixture_rows()).encode())},
            )
            self.assertEqual(upload.status_code, 201, upload.text)
            dataset = upload.json()
            for row in fixture_rows():
                review = client.post(
                    prefix + "/datasets/" + dataset["id"] + "/reviews",
                    json={
                        "record_id": row["id"],
                        "gold": row["gold"],
                        "approved": True,
                        "expected_hash": dataset["payload"]["sha256"],
                        "note": "Fictional fixture",
                    },
                )
                self.assertEqual(review.status_code, 201, review.text)
                dataset = review.json()
            queued = client.post(
                prefix + "/jobs",
                json={
                    "kind": "prepare",
                    "config": {"datasets": [dataset["id"]]},
                    "request_key": "personal-reserve",
                },
            )
            self.assertEqual(queued.status_code, 202, queued.text)
            snapshot = self.wait_job(client, prefix, queued.json()["id"])["snapshot"]
            self.assertEqual(seen, [])
            generated = client.post(
                prefix + "/jobs",
                json={
                    "kind": "generate",
                    "config": {
                        "snapshot": snapshot,
                        "count": 1,
                        "seed_count": 1,
                        "coverage": "Fictional payment boundaries",
                    },
                    "request_key": "personal-generate",
                },
            )
            self.assertEqual(generated.status_code, 202, generated.text)
            candidates = self.wait_job(client, prefix, generated.json()["id"])
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0]["model"], "fixture-teacher")
            candidate = client.get(
                prefix + "/datasets/" + candidates["dataset"] + "/rows"
            ).json()["rows"][0]
            self.assertFalse(candidate["metadata"]["reviewed"])
            policy = client.put(
                prefix + "/release-policy",
                json={
                    "min_accuracy": 0.0,
                    "min_macro_f1": 0.0,
                    "max_ece": 1.0,
                    "min_test_rows": 10,
                },
            )
            self.assertEqual(policy.status_code, 200, policy.text)
            queued = client.post(
                prefix + "/jobs",
                json={
                    "kind": "train",
                    "config": {"backend": "cpu-baseline", "snapshot": snapshot},
                    "request_key": "personal-train",
                },
            )
            self.assertEqual(queued.status_code, 202, queued.text)
            model = self.wait_job(client, prefix, queued.json()["id"])["model"]
            released = client.post(
                prefix + "/releases",
                json={
                    "model_id": model,
                    "expected_revision": 0,
                    "note": "Fictional CPU lifecycle",
                },
            )
            self.assertEqual(released.status_code, 200, released.text)
            decision = client.post(
                prefix + "/decisions",
                json={
                    "state": "invoice payment refund requested urgent",
                    "threshold": 0.8,
                },
            )
            self.assertEqual(decision.status_code, 200, decision.text)
            self.assertEqual(decision.json()["answers"]["team"]["answer"], "billing")
            w.close()
            self.assertTrue(all(p.poll() is not None for p in children))
        self.workspace = LocalWorkspace(self.home, ROOT).start()
        with httpx.Client(
            base_url=self.workspace.url,
            headers={"Authorization": "Bearer " + self.workspace.token},
            trust_env=False,
        ) as client:
            self.assertNotEqual(token, self.workspace.token)
            self.assertEqual(
                client.get(
                    "/v1/me", headers={"Authorization": "Bearer " + token}
                ).status_code,
                401,
            )
            self.assertEqual(client.get(prefix).status_code, 200)
            self.assertEqual(
                client.post(
                    prefix + "/decisions",
                    json={
                        "state": "invoice payment refund requested urgent",
                        "threshold": 0.8,
                    },
                ).status_code,
                200,
            )

    def test_streamlit_start_without_credentials_and_rerun_reuses_services(self):
        with patch.dict(
            os.environ,
            {
                "DMT_LOCAL_HOME": str(self.home),
                "DMT_LOCAL_TOKEN": "",
                "DMT_LOCAL_API_URL": "",
            },
        ):
            app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=40).run()
            self.assertEqual(len(app.exception), 0)
            next(
                b for b in app.button if b.label == "Start local workspace"
            ).click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.error), 0, [e.value for e in app.error])
            self.assertEqual(app.session_state["connection_mode"], "personal")
            self.assertEqual(app.session_state["principal"]["actor"], "local-owner")
            url = app.session_state["api_url"]
            resource = personal_workspace(str(self.home), str(ROOT))
            self.workspace = resource
            pids = [p.pid for p in resource.processes]
            app.run()
            self.assertEqual(app.session_state["api_url"], url)
            self.assertEqual([p.pid for p in resource.processes], pids)
            next(
                b
                for b in app.button
                if b.label == "Check available hardware and model packages"
            ).click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(app.session_state["runtime-capabilities"]["cpu_baseline"])
            next(b for b in app.button if b.label == "Sign out").click().run()
            self.assertTrue(any(b.label == "Start local workspace" for b in app.button))
            self.assertEqual(len(app.exception), 0)

    def test_network_bound_browser_cannot_start_personal_services(self):
        from decision_workbench.ui import onboarding

        with patch.object(onboarding, "loopback_server", return_value=False):
            app = AppTest.from_file(str(ROOT / "app.py")).run()
            self.assertTrue(
                next(
                    b for b in app.button if b.label == "Start local workspace"
                ).disabled
            )
            with self.assertRaises(ValueError):
                onboarding.connect("http://127.0.0.1:1", "secret", personal=True)
            self.assertFalse(self.home.exists())

    def test_same_personal_directory_refuses_a_second_service_owner(self):
        self.workspace.start()
        other = LocalWorkspace(self.home, ROOT)
        try:
            with self.assertRaises(Exception):
                other.start()
            self.workspace.check()
            self.assertEqual(other.processes, [])
        finally:
            other.close()

    def test_desktop_process_crash_ends_personal_services_and_allows_restart(self):
        marker = Path(self.temp.name) / "owner.json"
        code = """
import json,sys,time
from pathlib import Path
from decision_workbench.platform.local import LocalWorkspace
w=LocalWorkspace(sys.argv[1],sys.argv[2]).start()
Path(sys.argv[3]).write_text(json.dumps({"url":w.url}),encoding="utf-8")
while True:
    time.sleep(0.1)
"""
        owner = subprocess.Popen(
            [sys.executable, "-c", code, str(self.home), str(ROOT), str(marker)],
            cwd=ROOT,
        )
        try:
            deadline = time.monotonic() + 30
            while not marker.exists():
                self.assertIsNone(owner.poll(), "Fixture owner exited before readiness")
                if time.monotonic() > deadline:
                    self.fail("Fixture owner did not become ready")
                time.sleep(0.05)
            url = json.loads(marker.read_text(encoding="utf-8"))["url"]
            owner.kill()
            owner.wait(timeout=10)
            deadline = time.monotonic() + 10
            with httpx.Client(timeout=0.5, trust_env=False) as client:
                while True:
                    try:
                        client.get(url + "/health")
                    except httpx.HTTPError:
                        break
                    if time.monotonic() > deadline:
                        self.fail("Personal API survived its desktop owner")
                    time.sleep(0.05)
            self.workspace.start()
            self.workspace.check()
        finally:
            if owner.poll() is None:
                owner.kill()
                owner.wait(timeout=10)


class CapabilityTests(unittest.TestCase):
    def test_probe_is_owner_only_and_has_no_provider_secrets(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            TestClient(create_app(folder, keys())) as client,
        ):
            self.assertEqual(client.get("/v1/runtime-capabilities").status_code, 401)
            with patch(
                "decision_workbench.platform.capabilities.runtime_capabilities"
            ) as probe:
                probe.return_value = {"cpu_baseline": True, "runtimes": {}}
                self.assertEqual(
                    client.get(
                        "/v1/runtime-capabilities",
                        headers={"Authorization": "Bearer review-secret"},
                    ).status_code,
                    403,
                )
                self.assertEqual(
                    client.get(
                        "/v1/runtime-capabilities",
                        headers={"Authorization": "Bearer infer-secret"},
                    ).status_code,
                    403,
                )
                probe.assert_not_called()
                self.assertEqual(
                    client.get(
                        "/v1/runtime-capabilities",
                        headers={"Authorization": "Bearer a-secret"},
                    ).status_code,
                    200,
                )
        env = {
            "DMT_TEACHER_API_KEY": "teacher-secret",
            "DMT_CLOUD_AWS_KEY": "cloud-secret",
            "DMT_LOCAL_TOKEN": "local-secret",
            "DMT_KEYS_FILE": "private.json",
            "PATH": "safe",
        }
        self.assertEqual(browser_environment(env), {"PATH": "safe"})

    def test_unavailable_interpreter_is_reported_without_claiming_cpu_support(self):
        with (
            patch.dict(
                os.environ,
                {
                    "DMT_WORKER_PYTHON": "missing-fixture-python",
                    "DMT_MODEL_PYTHON": "missing-fixture-python",
                },
            ),
            patch.object(capabilities, "_cached", None),
        ):
            result = capabilities.runtime_capabilities()
        self.assertFalse(result["cpu_baseline"])
        self.assertTrue(result["runtimes"]["training"]["error"])

    def test_backend_selection_uses_packages_and_cuda_without_equating_cpu_with_laya(
        self,
    ):
        value = {
            "cpu_baseline": True,
            "runtimes": {
                "training": {
                    "packages": {
                        "torch": "fixture",
                        "laya": "fixture",
                        "unsloth": "fixture",
                    },
                    "cuda": False,
                    "error": None,
                }
            },
        }
        self.assertTrue(local_backend_available("cpu-baseline", value)[0])
        self.assertTrue(local_backend_available("laya-native", value)[0])
        self.assertFalse(local_backend_available("unsloth", value)[0])
        self.assertFalse(local_backend_available("vega", value)[0])


if __name__ == "__main__":
    unittest.main()
