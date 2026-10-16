"""Owned loopback API and worker for the personal desktop application."""

import atexit
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time

import httpx

from decision_workbench.training.resources import ResourceLease


def browser_environment(environment):
    """The browser process never needs operator provider credentials."""
    return {
        k: v
        for k, v in environment.items()
        if not k.startswith(("DMT_PROVIDER_", "DMT_CLOUD_"))
        and k
        not in (
            "DMT_DECISION_PROFILES_FILE",
            "DMT_TEACHER_API_KEY",
            "TEACHER_API_KEY",
            "DMT_KEYS_FILE",
            "DMT_SSO_FILE",
            "DMT_LOCAL_TOKEN",
            "DMT_LOCAL_API_URL",
        )
    }


class LocalWorkspace:
    """One personal identity per service lifetime; no token stored in the browser."""

    def __init__(self, home, root, *, api_port=0, environment=None):
        self.home = Path(home).resolve()
        self.root = Path(root).resolve()
        self.environment = dict(os.environ if environment is None else environment)
        self.environment.setdefault(
            "DMT_MODEL_CACHE", str(self.home.parent / "model-cache" / "hub")
        )
        self.environment.setdefault(
            "HF_HOME", str(Path(self.environment["DMT_MODEL_CACHE"]).parent)
        )
        self.environment.setdefault(
            "UNSLOTH_COMPILE_LOCATION",
            str(Path(self.environment["HF_HOME"]) / "unsloth_compiled_cache"),
        )
        self.processes = []
        self._lock = threading.Lock()
        self._scratch = None
        self._lease = None
        self._worker_lease = None
        self.token = secrets.token_urlsafe(40)
        self.principal = {"tenant": "local", "actor": "local-owner", "role": "admin"}
        if not api_port:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                api_port = listener.getsockname()[1]
        if type(api_port) is not int or not 1 <= api_port <= 65535:
            raise ValueError("Choose an API port between 1 and 65535")
        self.url = f"http://127.0.0.1:{api_port}"
        self.api_port = api_port

    def start(self, timeout=30):
        with self._lock:
            if self.processes:
                self.check()
                return self
            # Separate personal storage from existing operator-managed workspaces.
            self.home.parent.mkdir(parents=True, exist_ok=True)
            name = hashlib.sha256(str(self.home).encode()).hexdigest()[:24]
            self._lease = ResourceLease(self.home.parent / f".dmt-local-{name}.lock")
            try:
                self._lease.acquire()
                self.token = secrets.token_urlsafe(40)
                self._scratch = tempfile.TemporaryDirectory(
                    prefix=".dmt-local-", dir=self.home.parent
                )
                keys = Path(self._scratch.name) / "keys.json"
                keys.write_text(
                    json.dumps(
                        [
                            {
                                **self.principal,
                                "key_sha256": hashlib.sha256(
                                    self.token.encode()
                                ).hexdigest(),
                            }
                        ]
                    ),
                    encoding="utf-8",
                )
                os.chmod(keys, 0o600)
                self.environment.update(
                    DMT_HOME=str(self.home),
                    DMT_KEYS_FILE=str(keys),
                    DMT_API_URL=self.url,
                    DMT_LOCAL_PARENT_PID=str(os.getpid()),
                )
                # Personal mode never inherits enterprise identity configuration.
                self.environment.pop("DMT_SSO_FILE", None)
                from .store import Store

                Store(self.home)
                Path(self.environment["HF_HOME"]).mkdir(parents=True, exist_ok=True)
                commands = [
                    [
                        sys.executable,
                        "-m",
                        "decision_workbench.platform.local_service",
                        "api",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(self.api_port),
                    ],
                    [
                        self.environment.get("DMT_WORKER_PYTHON") or sys.executable,
                        "-m",
                        "decision_workbench.platform.local_service",
                        "worker",
                    ],
                ]
                for index, command in enumerate(commands):
                    child_environment = dict(self.environment)
                    if index == 0:
                        child_environment.pop("DMT_TEACHER_API_KEY", None)
                        child_environment.pop("TEACHER_API_KEY", None)
                    process_options = {"env": child_environment}
                    if sys.platform == "win32":
                        from decision_workbench.training.process_guard import (
                            leased_process_options,
                        )

                        lease = self._lease
                        if index:
                            self._worker_lease = ResourceLease(
                                self.home.parent / f".dmt-local-{name}-worker.lock"
                            )
                            self._worker_lease.acquire()
                            lease = self._worker_lease
                        # Distinct jobs support virtualenv launchers with separate
                        # inherited Windows job hierarchies.
                        process_options = leased_process_options(
                            lease, child_environment
                        )
                    self.processes.append(
                        subprocess.Popen(
                            command,
                            cwd=self.root,
                            shell=False,
                            **process_options,
                        )
                    )
                deadline = time.monotonic() + timeout
                with httpx.Client(timeout=1, trust_env=False) as client:
                    while True:
                        self.check()
                        try:
                            response = client.get(
                                self.url + "/v1/me",
                                headers={
                                    "Authorization": "Bearer " + self.token,
                                },
                            )
                            if (
                                response.status_code == 200
                                and response.json() == self.principal
                            ):
                                break
                        except (httpx.HTTPError, ValueError):
                            pass
                        if time.monotonic() >= deadline:
                            raise RuntimeError(
                                "Local API did not start; inspect terminal output"
                            )
                        time.sleep(0.1)
                atexit.register(self.close)
                return self
            except BaseException:
                self._close()
                raise

    def check(self):
        if any(process.poll() is not None for process in self.processes):
            raise RuntimeError(
                "A local service stopped. Restart the application; data is retained."
            )

    def close(self):
        with self._lock:
            self._close()

    def _close(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
        for process in self.processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        self.processes.clear()
        if self._scratch is not None:
            self._scratch.cleanup()
            self._scratch = None
        if self._lease is not None:
            self._lease.release()
            self._lease = None
        if self._worker_lease is not None:
            self._worker_lease.release()
            self._worker_lease = None


def launch_personal(args, app_path, stop_event=None):
    """CLI supervisor: personal services outlive individual browser sessions."""
    home = getattr(args, "home", None) or "data/local-workspace"
    workspace = LocalWorkspace(home, app_path.parent, api_port=args.api_port).start()
    web = None
    try:
        environment = browser_environment(workspace.environment)
        environment.update(
            DMT_LOCAL_API_URL=workspace.url, DMT_LOCAL_TOKEN=workspace.token
        )
        web = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(app_path),
                "--server.address",
                "127.0.0.1",
                "--server.port",
                str(args.ui_port),
                "--server.headless",
                "true",
            ],
            cwd=app_path.parent,
            env=environment,
            shell=False,
        )
        print(
            f"Open http://127.0.0.1:{args.ui_port} — your personal workspace is ready.",
            flush=True,
        )
        print(
            "Press Ctrl+C to stop services. Projects remain on this computer.",
            flush=True,
        )
        while stop_event is None or not stop_event.is_set():
            workspace.check()
            if web.poll() is not None:
                raise RuntimeError(
                    "Browser service stopped; local services are stopping"
                )
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        if web is not None and web.poll() is None:
            web.terminate()
            try:
                web.wait(timeout=10)
            except subprocess.TimeoutExpired:
                web.kill()
                web.wait(timeout=10)
        workspace.close()
