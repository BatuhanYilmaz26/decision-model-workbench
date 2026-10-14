"""Loopback HTTP server shared by API integration tests."""

import socket
import threading
import time

import uvicorn

from decision_workbench.platform.api import create_app


class LiveServer:
    def __init__(self, home, keys):
        self.app = create_app(home, keys)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        self.server = uvicorn.Server(
            uvicorn.Config(
                self.app,
                host="127.0.0.1",
                port=port,
                access_log=False,
                log_level="error",
            )
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("Test HTTP server failed to start")
            time.sleep(0.05)
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=20)
        self.app.state.service.runtime.close()
        if self.thread.is_alive():
            raise AssertionError("HTTP server failed to stop")
