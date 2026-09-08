"""Local mock HTTP server for offline unreachable-tier tests (D9).

A tiny :mod:`http.server` on ``127.0.0.1`` with an OS-assigned port that
serves canned failure responses so the unreachable tier can be exercised
fully offline:

* ``/not-found``      → 404
* ``/server-error``   → 500
* ``/timeout``        → sleeps past the fetcher's 5 s timeout (→ ReadTimeout)
* ``/robots-blocked`` → 200, but the server's ``/robots.txt`` disallows it
  (the fetcher never requests it; it is listed here for documentation)

Usage in tests::

    from tests.fixtures.mock_server import MockServer

    server = MockServer()
    server.start()
    try:
        url = server.url("/not-found")
        ...
    finally:
        server.stop()

Or as a pytest fixture (see :func:`mock_server`).
"""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

#: Paths and their canned responses.
ROUTES = {
    "/not-found": (404, b"<html><body><h1>404 Not Found</h1></body></html>"),
    "/server-error": (500, b"<html><body><h1>500 Internal Server Error</h1></body></html>"),
    "/timeout": (200, b"never arrives"),
}

ROBOTS_TXT = b"User-agent: *\nDisallow: /robots-blocked\n"

TIMEOUT_DELAY_SECONDS = 8.0  # > fetcher REQUEST_TIMEOUT (5 s) + retry budget


class _Handler(BaseHTTPRequestHandler):
    """Canned-response handler; logs nothing."""

    def log_message(self, *args):  # silence request logging
        pass

    def do_GET(self):  # noqa: N802 (http.server API)
        path = self.path.split("?", 1)[0]
        if path == "/robots.txt":
            self._send(200, ROBOTS_TXT)
            return
        if path == "/timeout":
            time.sleep(TIMEOUT_DELAY_SECONDS)
            self._send(200, ROUTES["/timeout"][1])
            return
        if path in ROUTES:
            status, body = ROUTES[path]
            self._send(status, body)
            return
        self._send(404, b"<html><body><h1>404 Not Found</h1></body></html>")

    def _send(self, status: int, body: bytes) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client gave up (e.g. fetcher timeout) — expected for /timeout


class MockServer:
    """Threaded local HTTP server on 127.0.0.1 with a random free port."""

    def __init__(self) -> None:
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def start(self) -> "MockServer":
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="citesure-mock-server", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


@pytest.fixture()
def mock_server():
    """Pytest fixture: a running :class:`MockServer`, stopped on teardown."""
    server = MockServer().start()
    yield server
    server.stop()
