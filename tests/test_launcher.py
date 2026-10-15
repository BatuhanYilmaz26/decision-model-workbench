"""Check the operator's actual three-process local launcher and orderly shutdown."""

import hashlib
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import httpx
from decision_workbench.platform.cli import launch


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class LauncherTests(unittest.TestCase):
    def test_personal_launcher_needs_no_keys_file_and_stops_its_services(self):
        with tempfile.TemporaryDirectory() as folder:
            args = SimpleNamespace(
                home=str(Path(folder) / "personal"),
                keys_file=None,
                api_port=free_port(),
                ui_port=free_port(),
            )
            stop = threading.Event()
            errors = []

            def run():
                try:
                    launch(args, stop)
                except BaseException as exc:
                    errors.append(exc)

            with patch.dict(
                "os.environ",
                {
                    "DMT_WORKER_PYTHON": "",
                    "DMT_APP_PATH": str(Path(__file__).resolve().parents[1] / "app.py"),
                },
            ):
                thread = threading.Thread(target=run, daemon=True)
                thread.start()
                try:
                    deadline = time.monotonic() + 30
                    with httpx.Client(timeout=2, trust_env=False) as client:
                        while True:
                            if errors:
                                raise errors[0]
                            try:
                                api = client.get(
                                    f"http://127.0.0.1:{args.api_port}/v1/me"
                                )
                                web = client.get(
                                    f"http://127.0.0.1:{args.ui_port}/_stcore/health"
                                )
                                if api.status_code == 401 and web.status_code == 200:
                                    break
                            except httpx.HTTPError:
                                pass
                            if time.monotonic() > deadline:
                                self.fail("Personal services did not start")
                            time.sleep(0.1)
                finally:
                    stop.set()
                    thread.join(timeout=30)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
                self.assertTrue((Path(folder) / "personal/workspace.sqlite3").is_file())
                with self.assertRaises(httpx.HTTPError):
                    httpx.get(f"http://127.0.0.1:{args.api_port}/health", timeout=1)

    def test_api_worker_and_web_start_with_operator_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            keys_file = Path(folder) / "operator.json"
            token = "launcher-fixture-secret"
            keys_file.write_text(
                json.dumps(
                    [
                        {
                            "key_sha256": hashlib.sha256(token.encode()).hexdigest(),
                            "tenant": "launcher",
                            "actor": "operator",
                            "role": "admin",
                        }
                    ]
                )
            )
            args = SimpleNamespace(
                home=str(Path(folder) / "workspace"),
                keys_file=str(keys_file),
                api_port=free_port(),
                ui_port=free_port(),
            )
            stop = threading.Event()
            errors = []

            def run():
                try:
                    launch(args, stop)
                except BaseException as exc:
                    errors.append(exc)

            with patch.dict(
                "os.environ",
                {
                    "DMT_WORKER_PYTHON": "",
                    "DMT_APP_PATH": str(Path(__file__).resolve().parents[1] / "app.py"),
                },
            ):
                thread = threading.Thread(target=run, daemon=True)
                thread.start()
                try:
                    deadline = time.monotonic() + 25
                    with httpx.Client(timeout=2) as client:
                        while True:
                            if errors:
                                raise errors[0]
                            try:
                                api = client.get(
                                    f"http://127.0.0.1:{args.api_port}/v1/me",
                                    headers={"Authorization": "Bearer " + token},
                                )
                                web = client.get(
                                    f"http://127.0.0.1:{args.ui_port}/_stcore/health"
                                )
                                if api.status_code == 200 and web.status_code == 200:
                                    break
                            except httpx.HTTPError:
                                pass
                            if time.monotonic() >= deadline:
                                self.fail("Local API/web did not start")
                            time.sleep(0.1)
                        self.assertEqual(api.json()["tenant"], "launcher")
                        self.assertTrue(
                            (Path(folder) / "workspace/workspace.sqlite3").exists()
                        )
                finally:
                    stop.set()
                    thread.join(timeout=25)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
