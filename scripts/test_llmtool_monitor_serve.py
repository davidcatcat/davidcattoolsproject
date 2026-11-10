#!/usr/bin/env python3
"""Tests for loopback state server in llmtool_monitor."""

import http.client
import importlib.util
import io
import os
import sys
import threading
import unittest

# Load the monitor module dynamically like other monitor tests.
MONITOR_PATH = os.path.join(os.path.dirname(__file__), "llmtool_monitor.py")
spec = importlib.util.spec_from_file_location("llmtool_monitor", MONITOR_PATH)
monitor = importlib.util.module_from_spec(spec)
sys.modules["llmtool_monitor"] = monitor
spec.loader.exec_module(monitor)


class TestLlmtoolMonitorServe(unittest.TestCase):
    """Test suite for make_state_server and StateServer."""

    def setUp(self) -> None:
        self.test_payload = b'{"status": "ok", "count": 42}'
        self.server, self.url = monitor.make_state_server(
            lambda: self.test_payload, port=0
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3.0)

    def _request(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        try:
            req_headers = headers if headers is not None else {}
            conn.request(method, path, body=body, headers=req_headers)
            resp = conn.getresponse()
            resp_headers = {k.lower(): v for k, v in resp.getheaders()}
            data = resp.read()
            return resp.status, resp_headers, data
        finally:
            conn.close()

    def test_non_loopback_hosts_refused(self) -> None:
        """Refuse any host other than 127.0.0.1 before opening sockets."""
        invalid_hosts = ["0.0.0.0", "::", "localhost", "192.168.1.5"]
        for host in invalid_hosts:
            with self.assertRaises(ValueError) as ctx:
                monitor.make_state_server(lambda: b"{}", host=host, port=0)
            self.assertEqual(str(ctx.exception), "SERVE_HOST_REFUSED")

        with self.assertRaises(ValueError) as ctx:
            monitor.StateServer(("0.0.0.0", 0))
        self.assertEqual(str(ctx.exception), "SERVE_HOST_REFUSED")

    def test_page_and_state_json_served_with_right_bytes(self) -> None:
        """Serve placeholder page and state.json with exact bytes."""
        page_path = f"/{self.server.token}/"
        status, headers, data = self._request("GET", page_path)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("content-type"), "text/html; charset=utf-8")
        self.assertEqual(data, monitor.STATE_SERVER_HTML)

        # HEAD request for placeholder page
        head_status, head_headers, head_data = self._request("HEAD", page_path)
        self.assertEqual(head_status, 200)
        self.assertEqual(head_headers.get("content-type"), "text/html; charset=utf-8")
        self.assertEqual(head_headers.get("content-length"), str(len(monitor.STATE_SERVER_HTML)))
        self.assertEqual(head_data, b"")

        # GET request for state.json
        state_path = f"/{self.server.token}/state.json"
        status, headers, data = self._request("GET", state_path)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("content-type"), "application/json")
        self.assertEqual(data, self.test_payload)

        # HEAD request for state.json
        head_status, head_headers, head_data = self._request("HEAD", state_path)
        self.assertEqual(head_status, 200)
        self.assertEqual(head_headers.get("content-type"), "application/json")
        self.assertEqual(head_headers.get("content-length"), str(len(self.test_payload)))
        self.assertEqual(head_data, b"")

    def test_wrong_or_missing_token_and_doubled_slash_give_404(self) -> None:
        """Wrong token, missing token, and doubled slashes give 404 with fixed body."""
        paths_to_test = [
            "/wrong-token/",
            "/wrong-token/state.json",
            "/",
            f"/{self.server.token}",  # missing trailing slash
            f"//{self.server.token}/",  # leading doubled slash
            f"/{self.server.token}//state.json",  # inner doubled slash
            f"/{self.server.token}/other.json",
            f"/{self.server.token}/extra/",
        ]
        for path in paths_to_test:
            status, _, data = self._request("GET", path)
            self.assertEqual(status, 404, f"Path {path} should return 404")
            self.assertEqual(data, b"Not Found\n")

    def test_wrong_host_gives_403(self) -> None:
        """A Host header other than 127.0.0.1:<port> or localhost:<port> gets 403."""
        state_path = f"/{self.server.token}/state.json"
        port = self.server.server_port

        # Forbidden host headers
        forbidden_hosts = [
            "evil.com",
            f"evil.com:{port}",
            "127.0.0.1",  # missing port
            "localhost",  # missing port
            "127.0.0.1:1",  # wrong port
            "192.168.1.10:8080",
        ]
        for host in forbidden_hosts:
            status, _, data = self._request(
                "GET", state_path, headers={"Host": host}
            )
            self.assertEqual(status, 403, f"Host {host} should return 403")
            self.assertEqual(data, b"Forbidden\n")

        # Allowed host headers
        allowed_hosts = [
            f"127.0.0.1:{port}",
            f"localhost:{port}",
        ]
        for host in allowed_hosts:
            status, _, data = self._request(
                "GET", state_path, headers={"Host": host}
            )
            self.assertEqual(status, 200, f"Host {host} should return 200")
            self.assertEqual(data, self.test_payload)

    def test_methods_not_allowed_give_405(self) -> None:
        """POST, PUT, PATCH, DELETE and OPTIONS get 405."""
        state_path = f"/{self.server.token}/state.json"
        disallowed_methods = ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
        for method in disallowed_methods:
            status, _, data = self._request(
                method, state_path, body=b"data" if method != "OPTIONS" else None
            )
            self.assertEqual(status, 405, f"Method {method} should return 405")
            self.assertEqual(data, b"Method Not Allowed\n")

    def test_security_headers_present_and_no_cors(self) -> None:
        """Responses include strict security headers and no Access-Control-* headers."""
        port = self.server.server_port
        requests = [
            ("GET", f"/{self.server.token}/", {}),
            ("GET", f"/{self.server.token}/state.json", {}),
            ("GET", "/wrong-token/", {}),
            ("GET", f"/{self.server.token}/state.json", {"Host": "evil.com"}),
            ("POST", f"/{self.server.token}/state.json", {}),
        ]
        expected_csp = (
            "default-src 'none'; frame-ancestors 'none'; form-action 'none'; "
            "script-src 'self'; style-src 'self'; connect-src 'self'"
        )
        for method, path, headers in requests:
            status, resp_headers, _ = self._request(method, path, headers=headers)
            self.assertEqual(
                resp_headers.get("cache-control"),
                "no-store",
                f"Failed cache-control on {method} {path} (status {status})",
            )
            self.assertEqual(
                resp_headers.get("x-content-type-options"),
                "nosniff",
                f"Failed x-content-type-options on {method} {path} (status {status})",
            )
            self.assertEqual(
                resp_headers.get("referrer-policy"),
                "no-referrer",
                f"Failed referrer-policy on {method} {path} (status {status})",
            )
            self.assertEqual(
                resp_headers.get("content-security-policy"),
                expected_csp,
                f"Failed content-security-policy on {method} {path} (status {status})",
            )
            # Ensure no Access-Control-* CORS headers are present
            cors_headers = [
                h for h in resp_headers if h.startswith("access-control-")
            ]
            self.assertEqual(
                cors_headers,
                [],
                f"Unexpected CORS header on {method} {path}: {cors_headers}",
            )

    def test_token_never_appears_in_captured_stderr(self) -> None:
        """Ensure server log_message writes nothing and token never reaches stderr."""
        stderr_capture = io.StringIO()
        old_stderr = sys.stderr
        try:
            sys.stderr = stderr_capture
            # Perform various requests including valid, invalid token, wrong host, wrong method
            self._request("GET", f"/{self.server.token}/")
            self._request("GET", f"/{self.server.token}/state.json")
            self._request("GET", f"/{self.server.token}/not-found")
            self._request("GET", "/invalid-token/")
            self._request("GET", f"/{self.server.token}/state.json", headers={"Host": "evil.com"})
            self._request("POST", f"/{self.server.token}/state.json")
        finally:
            sys.stderr = old_stderr

        captured = stderr_capture.getvalue()
        self.assertNotIn(
            self.server.token,
            captured,
            "Secret token found in captured stderr",
        )
        self.assertEqual(captured, "", "Expected nothing logged to stderr")

    def test_make_state_server_url_format(self) -> None:
        """Verify URL format produced by make_state_server."""
        port = self.server.server_port
        expected_url = f"http://127.0.0.1:{port}/{self.server.token}/"
        self.assertEqual(self.url, expected_url)
        self.assertTrue(len(self.server.token) >= 22)


if __name__ == "__main__":
    unittest.main()
