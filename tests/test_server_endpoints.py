"""Tests for serve_dashboard.py HTTP endpoints.

Uses unittest.mock to avoid starting a real server. Uses socket.socketpair()
to create a real connection for the Handler, which expects socket-like
objects with makefile, sendall, etc.
"""

from __future__ import annotations

import json
import socket
import threading
from unittest.mock import MagicMock, patch

# Handler needs request (connection), client_address, server
# BaseHTTPRequestHandler uses connection.makefile() and socket ops


def _make_request_bytes(method: str, path: str, body: bytes | None = None, headers: dict | None = None) -> bytes:
    """Build raw HTTP request bytes."""
    parts = [f"{method} {path} HTTP/1.1", "Host: localhost"]
    if headers:
        for k, v in headers.items():
            parts.append(f"{k}: {v}")
    if body:
        parts.append(f"Content-Length: {len(body)}")
    parts.append("")
    parts.append("")
    raw = "\r\n".join(parts).encode("utf-8")
    if body:
        raw += body
    return raw


def _create_handler_and_run(request_data: bytes, client_address: tuple = ("127.0.0.1", 0)):
    """Create Handler with socket pair, run handle(), return (status_line, headers, body)."""
    from apps.dashboard.serve_dashboard import Handler

    client_sock, server_sock = socket.socketpair()
    err = []

    def run_handler():
        try:
            server = MagicMock()
            handler = Handler(server_sock, client_address, server)
        except Exception as e:
            err.append(e)
        finally:
            try:
                server_sock.close()
            except OSError:
                pass

    try:
        client_sock.sendall(request_data)
        client_sock.shutdown(socket.SHUT_WR)

        handler_thread = threading.Thread(target=run_handler)
        handler_thread.start()
        handler_thread.join(timeout=5.0)

        if err:
            raise err[0]

        response_bytes = b""
        client_sock.settimeout(0.5)
        while True:
            try:
                chunk = client_sock.recv(65536)
                if not chunk:
                    break
                response_bytes += chunk
            except (socket.timeout, OSError):
                break
    finally:
        try:
            client_sock.close()
        except OSError:
            pass

    # Parse response: status line, headers, body
    parts = response_bytes.split(b"\r\n\r\n", 1)
    if len(parts) == 1:
        head = response_bytes
        body = b""
    else:
        head, body = parts
    lines = head.decode("utf-8").split("\r\n")
    status_line = lines[0] if lines else ""
    headers = {}
    for line in lines[1:]:
        if ": " in line:
            k, v = line.split(": ", 1)
            headers[k] = v
    return status_line, headers, body


class TestHealthEndpoint:
    def test_get_health_returns_ok(self):
        req = _make_request_bytes("GET", "/health")
        status_line, _, body = _create_handler_and_run(req)
        assert "200" in status_line
        data = json.loads(body.decode("utf-8"))
        assert data == {"ok": True}


class TestApiGroups:
    def test_get_api_groups_returns_ok_and_data(self):
        req = _make_request_bytes("GET", "/api/groups")
        status_line, _, body = _create_handler_and_run(req)
        assert "200" in status_line
        data = json.loads(body.decode("utf-8"))
        assert data.get("ok") is True
        assert "data" in data
        assert isinstance(data["data"], dict)


class TestApiScanStatus:
    def test_get_api_scan_status_returns_status_object(self):
        req = _make_request_bytes("GET", "/api/scan/status")
        status_line, _, body = _create_handler_and_run(req)
        assert "200" in status_line
        data = json.loads(body.decode("utf-8"))
        assert data.get("ok") is True
        assert "data" in data
        d = data["data"]
        assert "scan_running" in d
        assert "refresh_running" in d
        assert "enrich_running" in d


class TestPostOversizedBody:
    def test_post_oversized_body_returns_413(self):
        # _MAX_POST_BODY is 64 KB; send more
        body = json.dumps({"ticker": "AAPL", "from": "a", "to": "b"}).encode()
        big_body = body + b"x" * (70 * 1024)  # ~70 KB
        req = _make_request_bytes(
            "POST",
            "/api/move",
            body=big_body,
            headers={"Content-Type": "application/json"},
        )
        status_line, _, _ = _create_handler_and_run(req)
        assert "413" in status_line


class TestPostInvalidJson:
    def test_post_invalid_json_returns_400_or_500(self):
        req = _make_request_bytes(
            "POST",
            "/api/move",
            body=b"not valid json {{{",
            headers={"Content-Type": "application/json", "Content-Length": "17"},
        )
        status_line, _, body = _create_handler_and_run(req)
        # Handler tries json.loads() which raises; may return 400 or 500
        assert "400" in status_line or "500" in status_line


class TestRateLimiting:
    def test_rate_limit_exceeded_returns_429(self):
        from apps.dashboard.serve_dashboard import _RATE_LIMITER

        # Mock rate limiter to deny on first call (simulate 61st request)
        with patch.object(_RATE_LIMITER, "allow", return_value=False):
            req = _make_request_bytes("GET", "/health")
            status_line, _, body = _create_handler_and_run(req)
            assert "429" in status_line
            data = json.loads(body.decode("utf-8"))
            assert "error" in data
            assert "rate" in data["error"].lower()


class TestAuthCheck:
    def test_auth_returns_401_without_creds_when_auth_set(self):
        with patch.dict("os.environ", {"AUTH_USER": "testuser", "AUTH_PASS": "testpass"}, clear=False):
            req = _make_request_bytes("GET", "/health")  # No Authorization header
            status_line, headers, _ = _create_handler_and_run(req)
            assert "401" in status_line
            assert "WWW-Authenticate" in headers

    def test_auth_passes_with_valid_creds(self):
        import base64

        creds = base64.b64encode(b"testuser:testpass").decode("utf-8")
        with patch.dict("os.environ", {"AUTH_USER": "testuser", "AUTH_PASS": "testpass"}, clear=False):
            req = _make_request_bytes(
                "GET",
                "/health",
                headers={"Authorization": f"Basic {creds}"},
            )
            status_line, _, body = _create_handler_and_run(req)
            assert "200" in status_line
            assert json.loads(body.decode("utf-8")) == {"ok": True}


class TestUnknownPath:
    def test_unknown_path_returns_404(self):
        req = _make_request_bytes("GET", "/api/nonexistent-endpoint-xyz")
        status_line, _, body = _create_handler_and_run(req)
        assert "404" in status_line
        assert b"Not found" in body or b"not found" in body.lower()
