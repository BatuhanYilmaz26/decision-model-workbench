"""Real localhost HTTP and worker-process acceptance with fictional labeled fixtures."""

import copy
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
import httpx
import uvicorn
from decision_workbench.data import jsonl
from decision_workbench.platform.api import create_app
from test_platform import fixture_rows, QUESTIONS, keys


class LocalProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.app = create_app(self.home, keys())
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        self.listener = listener
        self.url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        self.server = uvicorn.Server(
            uvicorn.Config(self.app, log_level="error", access_log=False)
        )
        self.thread = threading.Thread(
            target=lambda: self.server.run(sockets=[listener]), daemon=True
        )
        self.thread.start()
        self.http = httpx.Client(
            base_url=self.url, headers={"Authorization": "Bearer a-secret"}, timeout=10
        )
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("HTTP service failed to start")
            time.sleep(0.02)

    def tearDown(self):
        self.http.close()
        self.server.should_exit = True
        self.thread.join(timeout=5)
        self.listener.close()
        self.temp.cleanup()

    def worker(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "decision_workbench.platform.worker",
                "--home",
                str(self.home),
                "--once",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_http_upload_review_prepare_train_release_and_decide(self):
        created = self.http.post(
            "/v1/projects",
            json={
                "name": "HTTP lifecycle",
                "description": "Use the fixture routing/refund/urgency policy",
                "questions": QUESTIONS,
            },
        )
        self.assertEqual(created.status_code, 201)
        project = created.json()["id"]
        prefix = "/v1/projects/" + project
        uploaded = self.http.post(
            prefix + "/datasets",
            files={"file": ("fixtures.jsonl", jsonl(fixture_rows(180)).encode())},
        )
        self.assertEqual(uploaded.status_code, 201)
        dataset = uploaded.json()
        # Each immutable version is reviewed through the real HTTP route, not by editing flags in storage.
        for row in fixture_rows(180):
            reviewed = self.http.post(
                prefix + "/datasets/" + dataset["id"] + "/reviews",
                json={
                    "record_id": row["id"],
                    "gold": row["gold"],
                    "approved": True,
                    "expected_hash": dataset["payload"]["sha256"],
                    "note": "Fictional fixture label checked",
                },
            )
            self.assertEqual(reviewed.status_code, 201, reviewed.text)
            dataset = reviewed.json()
        queued = self.http.post(
            prefix + "/jobs",
            json={
                "kind": "prepare",
                "config": {"datasets": [dataset["id"]]},
                "request_key": "http-prepare-run",
            },
        )
        self.assertEqual(queued.status_code, 202)
        self.worker()
        jobs = self.http.get(prefix + "/jobs").json()
        self.assertEqual(jobs[0]["status"], "succeeded", jobs[0]["error"])
        snapshot = jobs[0]["result"]["snapshot"]
        policy = self.http.put(
            prefix + "/release-policy",
            json={
                "min_accuracy": 0.0,
                "min_macro_f1": 0.0,
                "max_ece": 1.0,
                "min_test_rows": 10,
            },
        )
        self.assertEqual(policy.status_code, 200, policy.text)
        trained = self.http.post(
            prefix + "/jobs",
            json={
                "kind": "train",
                "config": {"snapshot": snapshot, "backend": "cpu-baseline"},
                "request_key": "http-training-run",
            },
        )
        self.assertEqual(trained.status_code, 202)
        self.worker()
        jobs = self.http.get(prefix + "/jobs").json()
        self.assertEqual(jobs[0]["status"], "succeeded", jobs[0]["error"])
        model = jobs[0]["result"]["model"]
        release = self.http.post(
            prefix + "/releases",
            json={
                "model_id": model,
                "expected_revision": 0,
                "note": "Fixture lifecycle acceptance, not domain qualification",
            },
        )
        self.assertEqual(release.status_code, 200, release.text)
        decision = self.http.post(
            prefix + "/decisions",
            json={"state": "invoice payment refund requested urgent", "threshold": 0.8},
        )
        self.assertEqual(decision.status_code, 200, decision.text)
        self.assertEqual(decision.json()["model_id"], model)
        self.assertEqual(decision.json()["answers"]["team"]["answer"], "billing")
        self.assertGreater(len(self.http.get(prefix + "/audit").json()), 180)
        self.assertEqual(self.http.get("/ready").status_code, 200)

    def test_browser_clients_close_after_renders_and_keep_tenants_separate(self):
        from streamlit.testing.v1 import AppTest
        from unittest.mock import patch

        created = self.http.post(
            "/v1/projects",
            json={
                "name": "Tenant A project",
                "description": "Fixture policy",
                "questions": QUESTIONS,
            },
        )
        self.assertEqual(created.status_code, 201)
        real_client = httpx.Client
        clients = []
        fail_next_request = False

        class RenderClient(real_client):
            def __init__(client, *args, **kwargs):
                super().__init__(*args, **kwargs)
                client.calls = []
                if kwargs.get("base_url") == self.url:
                    clients.append(client)

            def request(client, method, url, **kwargs):
                nonlocal fail_next_request
                client.calls.append(
                    (str(client.headers.get("Authorization")), kwargs.get("timeout"))
                )
                if fail_next_request:
                    fail_next_request = False
                    raise httpx.ReadTimeout("Fictional service timeout")
                return super().request(method, url, **kwargs)

        path = str(Path(__file__).resolve().parents[1] / "app.py")
        first = AppTest.from_file(path)
        first.session_state["credential"] = "a-secret"
        first.session_state["api_url"] = self.url
        first.session_state["principal"] = self.http.get("/v1/me").json()
        second = AppTest.from_file(path)
        second.session_state["credential"] = "b-secret"
        second.session_state["api_url"] = self.url
        second.session_state["principal"] = self.http.get(
            "/v1/me", headers={"Authorization": "Bearer b-secret"}
        ).json()
        with patch("httpx.Client", RenderClient):
            first.run(timeout=30)
            self.assertEqual(len(first.exception), 0)
            self.assertEqual(len(first.error), 0)
            self.assertEqual(len(clients), 1)
            self.assertGreater(len(clients[0].calls), 5)
            first.run(timeout=30)
            self.assertEqual(len(clients), 2)
            second.run(timeout=30)
            self.assertEqual(len(second.exception), 0)
            self.assertEqual(len(second.error), 0)
            self.assertEqual(len(clients), 3)
            self.assertEqual(len(second.tabs), 0)
            fail_next_request = True
            first.run(timeout=30)
            self.assertEqual(len(first.exception), 0)
            self.assertEqual(len(first.error), 1)
            self.assertIn("Timed out waiting", first.error[0].value)
            self.assertEqual(len(clients), 4)
        self.assertEqual(len({id(client) for client in clients}), 4)
        for client, token in zip(
            clients, ("a-secret", "a-secret", "b-secret", "a-secret")
        ):
            self.assertTrue(client.is_closed)
            self.assertTrue(
                all(
                    auth == "Bearer " + token and timeout == 30
                    for auth, timeout in client.calls
                )
            )

    def test_browser_connect_and_create_durable_project(self):
        from streamlit.testing.v1 import AppTest

        browser_questions = copy.deepcopy(QUESTIONS)
        browser_questions["team"]["instructions"] = "Apply the written policy"
        browser_questions["refund"]["instructions"] = "Apply the written policy"
        app = AppTest.from_file(
            str(Path(__file__).resolve().parents[1] / "app.py")
        ).run(timeout=20)
        app.text_input[0].set_value(self.url)
        app.text_input[1].set_value("a-secret")
        next(b for b in app.button if b.label == "Connect").click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        next(t for t in app.text_input if t.label == "Project name").set_value(
            "Created in browser"
        )
        next(
            t for t in app.text_area if t.label.startswith("Describe your domain")
        ).set_value("Route billing and software problems using written policy")
        next(t for t in app.text_area if t.label == "Existing schema JSON").set_value(
            json.dumps(browser_questions)
        )
        next(b for b in app.button if b.label == "Import schema").click().run(
            timeout=20
        )
        next(t for t in app.text_input if t.label == "Project name").set_value(
            "Created in browser"
        )
        next(
            t for t in app.text_area if t.label.startswith("Describe your domain")
        ).set_value("Route billing and software problems using written policy")
        next(c for c in app.checkbox if c.label.startswith("I confirm")).set_value(True)
        next(b for b in app.button if b.label == "Create project").click().run(
            timeout=20
        )
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        self.assertEqual(
            self.http.get("/v1/projects").json()[0]["payload"]["name"],
            "Created in browser",
        )
        self.assertEqual(len(app.tabs), 6)
        project = self.http.get("/v1/projects").json()[0]["id"]
        prefix = "/v1/projects/" + project
        browser_rows = fixture_rows(6)
        for row in browser_rows:
            row["questions"] = browser_questions
        imported = self.http.post(
            prefix + "/datasets",
            files={"file": ("browser-fixture.jsonl", jsonl(browser_rows).encode())},
        )
        self.assertEqual(imported.status_code, 201)
        app.run(timeout=25)
        next(t for t in app.text_input if t.label == "Review note").set_value(
            "Fictional label reviewed through browser"
        )
        next(c for c in app.selectbox if c.label == "Review decision").set_value(
            "approved"
        )
        next(b for b in app.button if b.label == "Save reviewed version").click().run(
            timeout=25
        )
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        summaries = self.http.get(prefix + "/entities/dataset").json()
        self.assertEqual(summaries[0]["payload"]["reviewed_count"], 1)
        self.assertEqual(summaries[1]["payload"]["reviewed_count"], 0)
        next(
            n
            for n in app.number_input
            if n.label == "Minimum real test rows for every question"
        ).set_value(10)
        next(
            b for b in app.button if b.label == "Save acceptance criteria"
        ).click().run(timeout=25)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        acceptance = self.http.get(prefix + "/release-policy").json()
        self.assertEqual(
            acceptance["policy"]["payload"]["criteria"]["min_test_rows"], 10
        )
        self.assertFalse(acceptance["locked"])
        # New direct-example path: unknown labels remain unknown through browser and server.
        next(t for t in app.text_area if t.label == "Example input evidence").set_value(
            "Handwritten browser counterexample: invoice refund normal"
        )
        next(
            t for t in app.text_input if t.label == "Example identifier (optional)"
        ).set_value("browser-draft")
        next(s for s in app.selectbox if s.label == "Example outcome: team").set_value(
            "billing"
        )
        next(b for b in app.button if b.label == "Save example for review").click().run(
            timeout=30
        )
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        latest = self.http.get(prefix + "/entities/dataset").json()[0]
        draft = self.http.get(prefix + "/datasets/" + latest["id"] + "/rows").json()[
            "rows"
        ][0]
        self.assertEqual(draft["id"], "browser-draft")
        self.assertEqual(set(draft["gold"]), {"team"})
        self.assertEqual(draft["metadata"]["origin"], "synthetic")
        next(b for b in app.button if b.label == "Analyze data readiness").click().run(
            timeout=30
        )
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("missing" in warning.value for warning in app.warning))
        next(
            s
            for s in app.selectbox
            if s.key and s.key.startswith("label-") and s.key.endswith("-refund")
        ).set_value("true")
        next(
            s
            for s in app.selectbox
            if s.key and s.key.startswith("label-") and s.key.endswith("-urgency")
        ).set_value("1")
        next(c for c in app.selectbox if c.label == "Review decision").set_value(
            "approved"
        )
        next(t for t in app.text_input if t.label == "Review note").set_value(
            "Fictional direct example reviewed; no operational gold claim"
        )
        next(b for b in app.button if b.label == "Save reviewed version").click().run(
            timeout=30
        )
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        latest = self.http.get(prefix + "/entities/dataset").json()[0]
        self.assertEqual(latest["payload"]["reviewed_count"], 1)
        approved_row = self.http.get(
            prefix + "/datasets/" + latest["id"] + "/rows"
        ).json()["rows"][0]
        self.assertEqual(set(approved_row["gold"]), set(browser_questions))
        # Append into the approved example's collection, then explicitly reject the draft.
        collection_root = latest["payload"]["root"]
        next(
            s for s in app.selectbox if s.label == "Collection for new examples"
        ).set_value(collection_root).run(timeout=25)
        next(t for t in app.text_area if t.label == "Example input evidence").set_value(
            "Fictional unsupported example to exclude"
        )
        next(
            t for t in app.text_input if t.label == "Example identifier (optional)"
        ).set_value("browser-draft")
        next(b for b in app.button if b.label == "Save example for review").click().run(
            timeout=25
        )
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("already exists" in error.value for error in app.error))
        self.assertEqual(
            next(t for t in app.text_area if t.label == "Example input evidence").value,
            "Fictional unsupported example to exclude",
        )
        next(
            t for t in app.text_input if t.label == "Example identifier (optional)"
        ).set_value("browser-excluded")
        next(b for b in app.button if b.label == "Save example for review").click().run(
            timeout=25
        )
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        appended = self.http.get(prefix + "/entities/dataset").json()[0]
        self.assertEqual(appended["payload"]["root"], collection_root)
        self.assertEqual(appended["payload"]["row_count"], 2)
        self.assertEqual(appended["payload"]["reviewed_count"], 1)
        next(s for s in app.selectbox if s.label == "Example to review").set_value(
            "browser-excluded"
        ).run(timeout=25)
        next(s for s in app.selectbox if s.label == "Review decision").set_value(
            "rejected"
        )
        next(t for t in app.text_input if t.label == "Review note").set_value(
            "Exclude unsupported fictional evidence"
        )
        next(b for b in app.button if b.label == "Save reviewed version").click().run(
            timeout=25
        )
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        rejected = self.http.get(prefix + "/entities/dataset").json()[0]
        self.assertEqual(rejected["payload"]["rejected_count"], 1)
        self.assertEqual(rejected["payload"]["row_count"], 2)
        # AppTest has no interactive grid driver: inject the grid's documented edit
        # deltas to exercise its actual form -> service contract. Rendered grid
        # interaction remains a separate browser acceptance item.
        next(r for r in app.radio if r.label == "Example entry").set_value(
            "A batch of examples"
        ).run(timeout=25)
        app.session_state[
            "batch-examples-"
            + project
            + "-"
            + str(app.session_state["example-generation-" + project])
        ] = {
            "edited_rows": {
                0: {
                    "Input evidence": "Fictional batch example one",
                    "Example ID": "batch-one",
                    "Outcome: team": "billing",
                }
            },
            "added_rows": [
                {
                    "Input evidence": "Fictional batch example two",
                    "Example ID": "batch-two",
                    "Source": "synthetic",
                }
            ],
            "deleted_rows": [],
        }
        next(b for b in app.button if b.label == "Save batch for review").click().run(
            timeout=25
        )
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 0)
        batched = self.http.get(prefix + "/entities/dataset").json()[0]
        self.assertEqual(batched["payload"]["root"], collection_root)
        self.assertEqual(batched["payload"]["row_count"], 4)
        rows = self.http.get(prefix + "/datasets/" + batched["id"] + "/rows").json()[
            "rows"
        ]
        self.assertEqual([row["id"] for row in rows[-2:]], ["batch-one", "batch-two"])
        self.assertEqual(set(rows[-2]["gold"]), {"team"})
        self.assertEqual(rows[-1]["gold"], {})
        historical = self.http.get(
            prefix + "/datasets/" + latest["id"] + "/rows"
        ).json()["rows"]
        self.assertEqual(len(historical), 1)
        next(b for b in app.button if b.label == "Sign out").click().run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any(b.label == "Connect" for b in app.button))


if __name__ == "__main__":
    unittest.main()
