"""Provider read budgets exercised through real localhost HTTP sockets."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
import unittest

import httpx

from decision_workbench.platform.hosted_transport import receive


class LocalSockets(httpx.BaseTransport):
    """Rewrite test traffic only; keep HTTPX socket timeout enforcement."""

    def __init__(self, port):
        self.port = port
        self.inner = httpx.HTTPTransport(retries=0, trust_env=False)

    def handle_request(self, request):
        redirected = httpx.Request(
            request.method,
            f"http://127.0.0.1:{self.port}{request.url.path}",
            headers=request.headers,
            content=request.content,
            extensions=request.extensions,
        )
        return self.inner.handle_request(redirected)

    def close(self):
        self.inner.close()


@contextmanager
def delayed_server(delay):
    stopping = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            stopping.wait(delay)
            body = b'{"answers":{}}'
            try:
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except ConnectionError:
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        stopping.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if thread.is_alive():
            raise RuntimeError("Local provider fixture did not stop")


class HostedDeadlineTests(unittest.TestCase):
    def test_all_decision_routes_allow_a_response_after_two_seconds_within_budget(self):
        with delayed_server(2.25) as port:
            for provider in (
                "typesafe",
                "openrouter",
                "liquid",
                "cloudflare",
                "perplexity",
            ):
                with self.subTest(provider=provider):
                    profile = {
                        "provider": provider,
                        "model": (
                            "clef-flash" if provider == "cloudflare" else "fixture"
                        ),
                        "account_id": "a" * 32,
                        "timeout_seconds": 6,
                    }
                    self.assertEqual(
                        receive(
                            profile, b"{}", "fictional", transport=LocalSockets(port)
                        ),
                        b'{"answers":{}}',
                    )

    def test_read_stall_still_times_out_at_the_configured_budget(self):
        with delayed_server(4) as port:
            started = time.monotonic()
            with self.assertRaises(httpx.ReadTimeout):
                receive(
                    {"provider": "liquid", "model": "d1", "timeout_seconds": 3},
                    b"{}",
                    "fictional",
                    transport=LocalSockets(port),
                )
            self.assertGreaterEqual(time.monotonic() - started, 2.7)
