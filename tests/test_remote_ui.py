"""Remote-study form submission/download against a real local HTTP API."""

import hashlib
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
import uvicorn
from streamlit.testing.v1 import AppTest
from decision_workbench.data import prepare
from decision_workbench.platform.api import create_app
from decision_workbench.platform.store import digest
from decision_workbench.platform.worker import Worker
import test_platform as fixtures


class RemoteBrowserTests(unittest.TestCase):
    def test_browser_prepares_study_after_consent_and_retrieves_inputs(self):
        with tempfile.TemporaryDirectory() as name:
            home = Path(name)
            app = create_app(home, fixtures.keys() + [{
                "key_sha256": hashlib.sha256(b"editor-secret").hexdigest(),
                "tenant": "A", "actor": "editor", "role": "editor",
            }])
            owner = {"tenant": "A", "actor": "owner", "role": "admin"}
            project = app.state.service.create_project(
                owner, "remote UI", "Fictional policy", fixtures.QUESTIONS
            )["id"]
            rows = prepare(fixtures.fixture_rows(240))
            app.state.store.add(
                "snapshot",
                "A",
                project,
                "owner",
                {
                    "splits": rows,
                    "sha256": digest(rows),
                    "counts": {k: len(v) for k, v in rows.items()},
                    "schema_sha256": digest(fixtures.QUESTIONS),
                },
            )
            app.state.service.configure_release_policy(
                owner, project, 0.0, 0.0, 1.0, 10, None
            )
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            server = uvicorn.Server(
                uvicorn.Config(app, log_level="error", access_log=False)
            )
            thread = threading.Thread(
                target=lambda: server.run(sockets=[listener]), daemon=True
            )
            thread.start()
            try:
                deadline = time.monotonic() + 10
                while not server.started:
                    if time.monotonic() > deadline:
                        raise RuntimeError("HTTP startup timed out")
                    time.sleep(0.02)
                at = AppTest.from_file(str(Path("app.py").resolve()))
                at.session_state["credential"] = "a-secret"
                at.session_state["api_url"] = url
                at.session_state["principal"] = owner
                at.run(timeout=30)
                self.assertFalse(at.exception)

                def widget(kind, label):
                    return next(w for w in getattr(at, kind) if w.label == label)

                widget("selectbox", "Training backend").select("laya-native").run(timeout=30)
                widget("radio", "Training location").set_value("Cloud GPU").run(timeout=30)
                self.assertTrue(widget("button", "Queue training").disabled)

                self.assertTrue(
                    widget("button", "Prepare remote training study").disabled
                )
                widget(
                    "checkbox",
                    "The selected provider is approved to receive this study's domain data",
                ).check().run(timeout=30)
                self.assertFalse(
                    widget("button", "Prepare remote training study").disabled
                )
                widget("selectbox", "Cloud training provider").select("modal")
                widget(
                    "text_input", "Approved provider region or environment"
                ).set_value("fixture-environment")
                widget("text_input", "Approved GPU or instance identity").set_value(
                    "A10G"
                )
                widget("text_input", "Qualified training image digest").set_value(
                    "fixture/trainer@sha256:" + "a" * 64
                )
                widget("button", "Prepare remote training study").click().run(
                    timeout=30
                )
                self.assertFalse(at.exception)
                jobs = app.state.store.jobs("A", project)
                self.assertEqual(len(jobs), 1)
                self.assertEqual(jobs[0]["kind"], "remote-study")
                self.assertEqual(jobs[0]["status"], "queued")
                worker = Worker(app.state.store)
                try:
                    worker.once()
                finally:
                    worker.service.runtime.close()
                self.assertEqual(
                    app.state.store.job(jobs[0]["id"], "A", project)["status"],
                    "succeeded",
                )
                at.run(timeout=30)
                next(
                    b for b in at.button if b.label.startswith("Retrieve remote inputs")
                ).click().run(timeout=30)
                self.assertFalse(at.exception)
                study_downloads = [
                    element
                    for element in at.get("download_button")
                    if element.proto.label == "Download frozen study"
                ]
                self.assertEqual(len(study_downloads), 1)
                self.assertTrue(app.state.store.release_policy("A", project)["locked"])
                self.assertEqual(app.state.store.list("A", project, "model"), [])
                editor = AppTest.from_file(str(Path("app.py").resolve()))
                editor.session_state["credential"] = "editor-secret"
                editor.session_state["api_url"] = url
                editor.session_state["principal"] = {
                    "tenant": "A", "actor": "editor", "role": "editor"
                }
                editor.run(timeout=30)
                self.assertFalse(editor.exception)
                self.assertFalse(editor.error)
                location = next(w for w in editor.radio if w.label == "Training location")
                self.assertEqual(list(location.options), ["This computer"])
                self.assertFalse(any(b.label == "Prepare remote training study" for b in editor.button))
                self.assertTrue(any("Ask a workspace owner" in c.value for c in editor.caption))
            finally:
                server.should_exit = True
                thread.join(timeout=15)
                listener.close()
                app.state.service.runtime.close()
                self.assertFalse(thread.is_alive())
