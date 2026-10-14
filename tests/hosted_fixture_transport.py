"""Lightweight test-only localhost redirect; no provider credentials or URL override."""

import http.client
import httpx


class FixtureTransport(httpx.BaseTransport):
    def __init__(self, port):
        self.port = port

    def handle_request(self, request):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(
                request.method,
                request.url.path,
                body=request.content,
                headers=dict(request.headers),
            )
            response = connection.getresponse()
            body = response.read()
            return httpx.Response(
                response.status,
                headers={"content-type": "application/json"},
                stream=httpx.ByteStream(body),
                request=request,
            )
        finally:
            connection.close()
