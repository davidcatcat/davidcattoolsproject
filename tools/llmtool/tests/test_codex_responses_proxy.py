"""Offline proof for the loopback Responses proxy: every server here binds 127.0.0.1."""

import http.client
import json
import socket
import struct
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from llmtool.codex_app_server_budget import RunLedger
from llmtool.codex_responses_proxy import (
    RESPONSES_PATH, ResponsesProxy, SSEUsage, _Handler, check_tools)
from llmtool.errors import LLMToolError

TOKEN = "sk-synthetic-proxy-test-token"


def sse(event_type, **payload):
    data = json.dumps(dict(payload, type=event_type))
    return ("event: %s\ndata: %s\n\n" % (event_type, data)).encode()


def usage_event(kind="response.completed", input_tokens=7, output_tokens=5):
    return sse(kind, response={"id": "r1", "usage": {
        "input_tokens": input_tokens, "output_tokens": output_tokens}})


class Upstream(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _UpstreamHandler)
        self.mode = "ok"
        self.requests = []
        self.lock = threading.Lock()
        self.release = threading.Event()
        self.out_tokens = 5
        self.delay = 0.0
        self.redirect_to = ""
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def endpoint(self):
        return "http://127.0.0.1:%d" % self.server_address[1]

    def handle_error(self, request, client_address):
        pass                                        # the proxy dropping us is expected

    def count(self):
        with self.lock:
            return len(self.requests)

    def stop(self):
        self.release.set()
        self.shutdown()
        self.server_close()
        self.thread.join(5)


class _UpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def _start(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()

    def _send(self, data):
        self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
        self.wfile.flush()

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        server = self.server
        with server.lock:
            server.requests.append({
                "path": self.path, "body": body,
                "headers": {k.lower(): v for k, v in self.headers.items()}})
        self.close_connection = True
        mode = server.mode
        if mode == "early-hints-then-terminal":
            payload = usage_event()
            wire = (b"HTTP/1.1 103 Early Hints\r\nLink: </ignored>\r\n\r\n"
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                    b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(payload))
            self.wfile.write(wire + payload)
            self.wfile.flush()
            return
        if mode == "disconnect":
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        if mode == "hang":
            server.release.wait(10)
            return
        if mode == "redirect":
            self.send_response(302)
            self.send_header("Location", server.redirect_to)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if mode in ("status500-short", "status500-chunked-short", "status500-hang"):
            payload = b'{"error":"PRIVATE_UPSTREAM"}'
            self.send_response(500)
            if mode == "status500-chunked-short":
                self.send_header("Transfer-Encoding", "chunked")
            else:
                self.send_header("Content-Length", str(len(payload) + 100))
            self.send_header("Connection", "close")
            self.end_headers()
            if mode == "status500-chunked-short":
                self._send(payload)
            else:
                self.wfile.write(payload)
            self.wfile.flush()
            if mode == "status500-hang":
                server.release.wait(10)
            return
        if mode == "status500":
            payload = b'{"error":"boom"}'
            self.send_response(500)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if mode == "delayed-ok":
            time.sleep(server.delay)
        if mode == "slow-headers":
            head = (b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                    b"Transfer-Encoding: chunked\r\n\r\n")
            for index in range(len(head) - 4):      # the header block is never completed
                self.wfile.write(head[index:index + 1])
                self.wfile.flush()
                time.sleep(0.15)
            return
        if mode in ("fixed-length", "fixed-length-truncate", "unframed"):
            payload = usage_event(output_tokens=server.out_tokens)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            if mode != "unframed":
                self.send_header("Content-Length", str(
                    len(payload) + (100 if mode == "fixed-length-truncate" else 0)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
            self.wfile.flush()
            return                                  # close after fewer than declared bytes
        self._start()
        self._send(sse("response.created", response={"id": "r1"}))
        if mode == "trickle":                       # never terminal, never idle
            end = time.monotonic() + 10
            while time.monotonic() < end and not server.release.is_set():
                self._send(sse("response.output_text.delta", delta="x"))
                time.sleep(0.1)
            return
        if mode == "terminal-hold":                 # terminal event now, final framing later
            self._send(usage_event(output_tokens=server.out_tokens))
            server.release.wait(10)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            return
        if mode in ("malformed-terminal", "unterminated-terminal"):
            terminal = (b'data: {"type":"response.completed",bad-json}\n\n'
                        if mode == "malformed-terminal" else usage_event().rstrip(b"\n"))
            self._send(terminal)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            return
        if mode == "terminal-missing-trailer":
            self._send(usage_event(output_tokens=server.out_tokens))
            self.wfile.write(b"0\r\n")               # no required trailer terminator
            self.wfile.flush()
            return
        if mode == "terminal-bad-chunk-crlf":
            payload = usage_event()
            self.wfile.write(b"%x\r\n" % len(payload) + payload + b"XX0\r\n\r\n")
            self.wfile.flush()
            return
        if mode == "unhashable-sse-type":
            self._send(sse([], response={"usage": {"input_tokens": 7, "output_tokens": 5}}))
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            return
        if mode == "terminal-truncate":
            self._send(usage_event(output_tokens=server.out_tokens))
            return                                  # usage is not proof of complete framing
        if mode == "truncate":
            self._send(sse("response.output_text.delta", delta="par"))
            return                                  # no terminal event, no final chunk
        if mode == "slow-stream":
            server.release.wait(10)
            self._send(sse("response.output_text.delta", delta="more"))
            return
        delta = sse("response.output_text.delta", delta="hello")
        self._send(delta[:20])                      # an event split across two chunks
        time.sleep(0.01)
        self._send(delta[20:])
        if mode == "no-usage":
            self._send(sse("response.completed", response={"id": "r1"}))
        elif mode == "incomplete":
            self._send(usage_event("response.incomplete", 9, 128))
        else:
            self._send(usage_event(output_tokens=server.out_tokens))
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


class Clock:
    def __init__(self):
        self.now = 5000.0

    def __call__(self):
        return self.now


def body_for(**extra):
    body = {"model": "m", "input": "hi", "stream": True}
    body.update(extra)
    return body


class ProxyTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = Clock()
        self.ledger = RunLedger(Path(self.temp.name), "run-1", clock=self.clock)
        self.upstream = Upstream()
        self.addCleanup(self.upstream.stop)
        self.proxy = self.make_proxy()

    def make_proxy(self, **kwargs):
        proxy = ResponsesProxy(kwargs.pop("ledger", self.ledger),
                               kwargs.pop("upstream", self.upstream.endpoint), TOKEN, **kwargs)
        proxy.start()
        self.addCleanup(proxy.close)
        return proxy

    def post(self, body=None, headers=None, proxy=None):
        proxy = proxy or self.proxy
        send = {"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"}
        send.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=10)
        try:
            connection.request("POST", RESPONSES_PATH, body=json.dumps(
                body_for() if body is None else body), headers=send)
            response = connection.getresponse()
            try:
                data = response.read()
            except http.client.IncompleteRead as partial:
                return response.status, partial.partial
            return response.status, data
        finally:
            connection.close()

    def raw(self, data, proxy=None):
        sock = socket.create_connection(("127.0.0.1", (proxy or self.proxy).port), timeout=5)
        try:
            sock.sendall(data)
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            sock.close()
        blob = b"".join(chunks)
        head, _, body = blob.partition(b"\r\n\r\n")
        return int(head.split()[1]), body

    def error_code(self, data):
        return json.loads(data.decode())["error"]

    def settle(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            states = self.ledger.snapshot()["states"]
            if "RESERVED" not in states:
                return states
            time.sleep(0.02)
        self.fail("ledger entry never finalized")

    def sent_body(self, index=-1):
        return json.loads(self.upstream.requests[index]["body"])

    def assert_nothing_forwarded(self):
        self.assertEqual(self.upstream.count(), 0)
        self.assertEqual(self.ledger.snapshot()["requests"], 0)
        self.assertEqual(self.proxy.forwarded, [])


class ForwardingTests(ProxyTestCase):
    def test_healthy_fixed_length_stream_completes_with_usage(self):
        self.upstream.mode = "fixed-length"
        status, data = self.post()
        self.assertEqual(status, 200)
        self.assertIn(b"response.completed", data)
        self.assertEqual(self.settle(), ["SUCCEEDED"])
        self.assertEqual(self.upstream.count(), 1)

    def test_success_streams_events_and_records_usage(self):
        status, data = self.post()
        self.assertEqual(status, 200)
        for needle in (b"response.created", b"hello", b"response.completed"):
            self.assertIn(needle, data)
        self.assertEqual(self.settle(), ["SUCCEEDED"])
        self.assertEqual(self.upstream.count(), 1)

    def test_healthy_stream_completes_promptly_despite_the_liveness_probe(self):
        started = time.monotonic()
        self.assertEqual(self.post()[0], 200)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(self.settle(), ["SUCCEEDED"])

    def test_every_forwarded_request_carries_the_ledger_cap(self):
        for requested in (None, 32, 10_000):
            ledger = RunLedger(Path(self.temp.name), "cap-%s" % requested, clock=self.clock)
            proxy = self.make_proxy(ledger=ledger)
            body = body_for() if requested is None else body_for(max_output_tokens=requested)
            self.assertEqual(self.post(body, proxy=proxy)[0], 200)
            expected = min(128, requested or 128)
            self.assertEqual(self.sent_body()["max_output_tokens"], expected)
            self.assertEqual(proxy.forwarded[-1]["max_output_tokens"], expected)

    def test_invalid_caller_caps_are_rejected_before_the_ledger(self):
        for bad in (0, -1, "5", 1.5, True, [], {}):
            with self.subTest(cap=bad):
                status, data = self.post(body_for(max_output_tokens=bad))
                self.assertEqual((status, self.error_code(data)), (400, "OUTPUT_CAP_INVALID"))
        self.assert_nothing_forwarded()

    def test_duplicate_json_keys_cannot_smuggle_a_larger_cap(self):
        raw = (b'{"stream":true,"max_output_tokens":5,"max_output_tokens":99999,"input":"x"}')
        connection = http.client.HTTPConnection("127.0.0.1", self.proxy.port, timeout=10)
        connection.request("POST", RESPONSES_PATH, body=raw, headers={
            "Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
        self.assertEqual(connection.getresponse().status, 200)
        connection.close()
        sent = self.upstream.requests[0]["body"]
        self.assertEqual(sent.count(b"max_output_tokens"), 1)
        self.assertEqual(json.loads(sent)["max_output_tokens"], 128)

    def test_upstream_sees_only_a_fixed_header_set_and_fixed_path(self):
        self.post(headers={"Cookie": "a=b", "X-Forwarded-For": "1.2.3.4", "Origin": "http://x",
                           "X-Request-ID": "req-77", "User-Agent": "evil", "Referer": "http://x"})
        seen = self.upstream.requests[0]
        self.assertEqual(seen["path"], RESPONSES_PATH)
        self.assertEqual(set(seen["headers"]), {
            "host", "content-type", "content-length", "accept", "authorization",
            "connection", "accept-encoding"})
        self.assertEqual(seen["headers"]["host"], "127.0.0.1:%d" % self.upstream.server_address[1])
        self.assertEqual(seen["headers"]["authorization"], "Bearer " + TOKEN)

    def test_one_retry_then_denied_across_connections_and_request_ids(self):
        self.upstream.mode = "status500"
        status, data = self.post(headers={"X-Request-ID": "a"})
        self.assertEqual((status, self.error_code(data)), (502, "UPSTREAM_FAILED"))
        self.upstream.mode = "ok"
        self.assertEqual(self.post(headers={"X-Request-ID": "b"})[0], 200)
        for request_id in ("c", "d", "a"):
            status, data = self.post(headers={"X-Request-ID": request_id})
            self.assertEqual((status, self.error_code(data)), (429, "CALL_LIMIT_REACHED"))
        self.assertEqual(self.upstream.count(), 2)
        self.assertEqual([r["phase"] for r in self.proxy.forwarded], ["PRIMARY", "RETRY"])
        self.assertEqual(self.settle(), ["FAILED", "SUCCEEDED"])

    def test_cap_overrun_records_actual_usage_and_blocks_replay(self):
        self.upstream.out_tokens = 200
        self.assertEqual(self.post()[0], 200)  # headers may already be delivered
        self.assertEqual(self.settle(), ["CAP_EXCEEDED"])
        self.assertEqual(self.ledger.snapshot()["usage"], [
            {"input_tokens": 7, "output_tokens": 200}])
        status, data = self.post()
        self.assertEqual((status, self.error_code(data)), (429, "TOKEN_BUDGET_EXCEEDED"))
        self.assertIn("OUTPUT_CAP_EXCEEDED", self.proxy.rejected)
        self.assertEqual(self.upstream.count(), 1)

    def test_incomplete_terminal_event_with_usage_counts(self):
        self.upstream.mode = "incomplete"
        self.post()
        self.assertEqual(self.settle(), ["SUCCEEDED"])
        self.assertEqual(self.ledger.reserve_request().max_output_tokens, 128)

    def test_concurrent_requests_forward_exactly_one(self):
        self.upstream.mode = "delayed-ok"
        self.upstream.delay = 1.0
        results = []
        barrier = threading.Barrier(6)

        def attempt():
            barrier.wait()
            results.append(self.post()[0])

        threads = [threading.Thread(target=attempt) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)
        self.assertEqual(sorted(results), [200, 429, 429, 429, 429, 429])
        self.assertEqual(self.upstream.count(), 1)

    def test_wall_clock_expiry_blocks_forwarding(self):
        self.assertTrue(self.ledger.reserve_launch())
        self.clock.now += 120
        status, data = self.post()
        self.assertEqual((status, self.error_code(data)), (429, "WALL_CLOCK_EXCEEDED"))
        self.assertEqual(self.upstream.count(), 0)


class UnknownOutcomeTests(ProxyTestCase):
    def assert_unknown_and_never_replayed(self):
        self.assertEqual(self.settle(), ["UNKNOWN"])
        status, data = self.post()
        self.assertEqual((status, self.error_code(data)), (429, "USAGE_UNKNOWN"))
        self.assertEqual(self.upstream.count(), 1)

    def test_truncated_stream_is_unknown_and_not_terminated_for_the_client(self):
        self.upstream.mode = "truncate"
        status, data = self.post()
        self.assertEqual(status, 200)
        self.assertNotIn(b"response.completed", data)
        self.assert_unknown_and_never_replayed()

    def test_terminal_usage_then_truncated_framing_is_unknown_and_never_replayed(self):
        self.upstream.mode = "terminal-truncate"
        status, data = self.post()
        self.assertEqual(status, 200)
        self.assertIn(b"response.completed", data)
        self.assert_unknown_and_never_replayed()

    def test_fixed_length_terminal_usage_then_premature_fin_is_unknown(self):
        self.upstream.mode = "fixed-length-truncate"
        status, data = self.post()
        self.assertEqual(status, 200)
        self.assertIn(b"response.completed", data)
        self.assert_unknown_and_never_replayed()

    def test_terminal_usage_without_known_framing_is_unknown(self):
        self.upstream.mode = "unframed"
        self.assertEqual(self.post()[0], 200)
        self.assert_unknown_and_never_replayed()

    def test_chunked_terminal_usage_without_trailer_terminator_is_unknown(self):
        self.upstream.mode = "terminal-missing-trailer"
        self.assertEqual(self.post()[0], 200)
        self.assert_unknown_and_never_replayed()

    def test_terminal_usage_then_timeout_is_unknown_and_never_replayed(self):
        proxy = self.make_proxy(idle_timeout=0.2)
        self.upstream.mode = "terminal-hold"
        status, data = self.post(proxy=proxy)
        self.assertEqual(status, 200)
        self.assertIn(b"response.completed", data)
        self.assert_unknown_and_never_replayed()

    def test_malformed_terminal_sse_is_unknown_and_never_replayed(self):
        self.upstream.mode = "malformed-terminal"
        self.assertEqual(self.post()[0], 200)
        self.assert_unknown_and_never_replayed()

    def test_unterminated_terminal_sse_is_unknown_despite_complete_http_framing(self):
        self.upstream.mode = "unterminated-terminal"
        self.assertEqual(self.post()[0], 200)
        self.assert_unknown_and_never_replayed()

    def test_completed_without_usage_is_unknown(self):
        self.upstream.mode = "no-usage"
        self.post()
        self.assert_unknown_and_never_replayed()

    def test_upstream_disconnect_is_unknown(self):
        self.upstream.mode = "disconnect"
        status, data = self.post()
        self.assertEqual((status, self.error_code(data)), (502, "UPSTREAM_OUTCOME_UNKNOWN"))
        self.assert_unknown_and_never_replayed()

    def test_upstream_timeout_is_unknown(self):
        proxy = self.make_proxy(idle_timeout=0.3)
        self.upstream.mode = "hang"
        started = time.monotonic()
        status, data = self.post(proxy=proxy)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual((status, self.error_code(data)), (502, "UPSTREAM_OUTCOME_UNKNOWN"))
        self.assertEqual(self.settle(), ["UNKNOWN"])
        status, data = self.post(proxy=proxy)
        self.assertEqual((status, self.error_code(data)), (429, "USAGE_UNKNOWN"))
        self.assertEqual(self.upstream.count(), 1)

    def test_client_disconnect_mid_stream_is_unknown(self):
        self.upstream.mode = "slow-stream"
        sock = socket.create_connection(("127.0.0.1", self.proxy.port), timeout=5)
        payload = json.dumps(body_for()).encode()
        sock.sendall(b"POST %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nAuthorization: Bearer %s\r\n"
                     b"Content-Length: %d\r\n\r\n%s" % (
                         RESPONSES_PATH.encode(), self.proxy.port, TOKEN.encode(),
                         len(payload), payload))
        seen = b""
        while b"response.created" not in seen:
            seen += sock.recv(4096)
        sock.close()
        self.upstream.release.set()
        self.assert_unknown_and_never_replayed()

    def test_closing_the_proxy_unblocks_a_hung_upstream_and_finalizes_unknown(self):
        self.upstream.mode = "hang"
        def blocked_client():
            try:
                self.post()
            except OSError:
                pass                                # http.client.* errors are OSError subclasses

        worker = threading.Thread(target=blocked_client, daemon=True)
        worker.start()
        deadline = time.monotonic() + 5
        while self.upstream.count() == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        started = time.monotonic()
        self.proxy.close()
        self.assertLess(time.monotonic() - started, 5)
        worker.join(5)
        self.assertEqual(self.ledger.snapshot()["states"], ["UNKNOWN"])
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", self.proxy.port), timeout=1)

    def test_connection_refused_is_a_definite_failure_not_unknown(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        dead = "http://127.0.0.1:%d" % sock.getsockname()[1]
        sock.close()
        proxy = self.make_proxy(upstream=dead)
        for _ in range(2):
            status, data = self.post(proxy=proxy)
            self.assertEqual((status, self.error_code(data)), (502, "UPSTREAM_UNREACHABLE"))
        status, data = self.post(proxy=proxy)
        self.assertEqual((status, self.error_code(data)), (429, "CALL_LIMIT_REACHED"))
        self.assertEqual(self.settle(), ["FAILED", "FAILED"])


class BypassTests(ProxyTestCase):
    def test_redirect_is_blocked_and_never_followed(self):
        canary = Upstream()
        self.addCleanup(canary.stop)
        self.upstream.mode = "redirect"
        self.upstream.redirect_to = canary.endpoint + "/steal"
        status, data = self.post()
        self.assertEqual((status, self.error_code(data)), (502, "UPSTREAM_REDIRECT_BLOCKED"))
        self.assertEqual(canary.count(), 0)
        self.assertEqual(self.upstream.count(), 1)
        self.assertEqual(self.settle(), ["FAILED"])

    def test_upgrade_and_websocket_requests_are_refused(self):
        host = b"Host: 127.0.0.1:%d\r\n" % self.proxy.port
        for request in (
                b"GET /v1/responses HTTP/1.1\r\n" + host + b"Upgrade: websocket\r\n"
                b"Connection: Upgrade\r\nSec-WebSocket-Key: x\r\nSec-WebSocket-Version: 13\r\n\r\n",
                b"GET /v1/responses HTTP/1.1\r\n" + host + b"Connection: upgrade\r\n\r\n",
                b"POST /v1/responses HTTP/1.1\r\n" + host + b"Upgrade: h2c\r\n"
                b"Connection: Upgrade, HTTP2-Settings\r\nContent-Length: 0\r\n\r\n"):
            status, data = self.raw(request)
            self.assertEqual((status, self.error_code(data)), (426, "UPGRADE_NOT_ALLOWED"))
        self.assert_nothing_forwarded()

    def test_only_the_fixed_post_target_is_served(self):
        host = "Host: 127.0.0.1:%d\r\n" % self.proxy.port
        targets = ("/v1/models", "/v1/responses?x=1", "/v1/responses/", "//v1/responses",
                   "/v1/Responses", "/v1/responses%2f", "/", "/backend-api/codex/responses",
                   "http://127.0.0.1:%d/v1/responses" % self.upstream.server_address[1],
                   "http://example.com/v1/responses")
        for target in targets:
            with self.subTest(target=target):
                status, data = self.raw((
                    "POST %s HTTP/1.1\r\n%sAuthorization: Bearer %s\r\n"
                    "Content-Length: 2\r\n\r\n{}" % (target, host, TOKEN)).encode())
                self.assertEqual((status, self.error_code(data)), (404, "ENDPOINT_NOT_ALLOWED"))
        for method in ("GET", "PUT", "DELETE", "PATCH", "OPTIONS"):
            with self.subTest(method=method):
                status, data = self.raw(
                    ("%s /v1/responses HTTP/1.1\r\n%s\r\n" % (method, host)).encode())
                self.assertEqual((status, self.error_code(data)), (405, "METHOD_NOT_ALLOWED"))
        self.assert_nothing_forwarded()

    def test_authentication_is_required(self):
        for header in ({"Authorization": ""}, {"Authorization": "Bearer wrong-token-value"},
                       {"Authorization": "bearer " + TOKEN}, {"Authorization": TOKEN}):
            status, data = self.post(headers=header)
            self.assertEqual((status, self.error_code(data)), (401, "AUTH_REQUIRED"))
        status, data = self.raw((
            "POST /v1/responses HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Length: 2\r\n\r\n{}"
            % self.proxy.port).encode())
        self.assertEqual((status, self.error_code(data)), (401, "AUTH_REQUIRED"))
        self.assert_nothing_forwarded()

    def test_foreign_host_header_is_refused(self):
        status, data = self.raw((
            "POST /v1/responses HTTP/1.1\r\nHost: evil.example\r\nAuthorization: Bearer %s\r\n"
            "Content-Length: 2\r\n\r\n{}" % TOKEN).encode())
        self.assertEqual((status, self.error_code(data)), (421, "HOST_NOT_ALLOWED"))
        self.assert_nothing_forwarded()

    def test_body_framing_and_content_are_validated(self):
        head = ("POST /v1/responses HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nAuthorization: Bearer %s\r\n"
                % (self.proxy.port, TOKEN))
        def sized(payload, extra=""):
            return head + extra + "Content-Length: %d\r\n\r\n%s" % (len(payload), payload)

        cases = {
            "chunked": (head + "Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n", 411),
            "no length": (head + "\r\n", 411),
            "duplicate length": (head + "Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}", 411),
            "gzip": (sized("{}", "Content-Encoding: gzip\r\n"), 415),
            "huge": (head + "Content-Length: 5000000\r\n\r\n", 400),
            "zero": (head + "Content-Length: 0\r\n\r\n", 400),
            "signed length": (head + "Content-Length: +2\r\n\r\n{}", 400),
            "not json": (sized("nope"), 400),
            "not object": (sized("[]"), 400),
            "not streaming": (sized('{"stream":false}'), 400),
            "stream missing": (sized('{"input":"x"}'), 400),
            "NaN": (sized('{"stream":true,"x":NaN}'), 400),
            "Infinity": (sized('{"stream":true,"x":-Infinity}'), 400),
        }
        for name, (request, expected) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(self.raw(request.encode())[0], expected)
        self.assert_nothing_forwarded()

    def test_tool_policy_is_deny_by_default(self):
        self.assertEqual(self.post(body_for(tools=[]))[0], 200)
        self.settle()
        proxy = self.make_proxy(ledger=RunLedger(Path(self.temp.name), "t2", clock=self.clock))
        status, data = self.post(body_for(tools=[{"type": "function", "name": "update_plan"}]),
                                 proxy=proxy)
        self.assertEqual((status, self.error_code(data)), (403, "TOOLS_NOT_ALLOWED"))
        self.assertEqual(proxy.forwarded, [])
        self.assertEqual(self.upstream.count(), 1)

    def test_malformed_tools_are_rejected(self):
        for tools in ("x", [1], [{"type": 5}], [{"name": "a"}], {"type": "function"}):
            with self.subTest(tools=tools):
                status, data = self.post(body_for(tools=tools))
                self.assertEqual((status, self.error_code(data)), (400, "TOOLS_INVALID"))
        self.assert_nothing_forwarded()

    def test_execution_capable_tools_are_refused_even_when_allowlisted(self):
        forbidden = [
            {"type": "local_shell"}, {"type": "function", "name": "shell"},
            {"type": "function", "name": "exec_command"},
            {"type": "function", "name": "unified_exec"},
            {"type": "function", "name": "apply_patch"},
            {"type": "custom", "name": "apply_patch"}, {"type": "mcp", "server_label": "x"},
            {"type": "function", "name": "mcp__srv__tool"},
            {"type": "function", "name": "write_stdin"}, {"type": "code_interpreter"},
            {"type": "computer_use_preview"}, {"type": "function", "function": {"name": "bash"}},
            {"type": "function", "name": "view_file"},
        ]
        names = {(t["type"], t.get("name") or (t.get("function") or {}).get("name"))
                 for t in forbidden}
        proxy = self.make_proxy(allowed_tools=names)
        for tool in forbidden:
            with self.subTest(tool=tool):
                status, data = self.post(body_for(tools=[tool]), proxy=proxy)
                self.assertEqual((status, self.error_code(data)),
                                 (403, "TOOL_EXECUTION_FORBIDDEN"))
        self.assert_nothing_forwarded()

    def test_explicitly_allowed_non_executing_tool_is_forwarded_and_recorded(self):
        proxy = self.make_proxy(allowed_tools=(("function", "update_plan"),))
        tools = [{"type": "function", "name": "update_plan", "parameters": {}}]
        self.assertEqual(self.post(body_for(tools=tools), proxy=proxy)[0], 200)
        self.assertEqual(proxy.forwarded[0]["tools"], ["function:update_plan"])
        self.assertEqual(self.sent_body()["tools"], tools)

    def test_one_forbidden_tool_among_allowed_ones_rejects_the_request(self):
        proxy = self.make_proxy(allowed_tools=(("function", "update_plan"),))
        tools = [{"type": "function", "name": "update_plan"}, {"type": "local_shell"}]
        status, data = self.post(body_for(tools=tools), proxy=proxy)
        self.assertEqual((status, self.error_code(data)), (403, "TOOL_EXECUTION_FORBIDDEN"))
        self.assert_nothing_forwarded()


class HardDeadlineTests(ProxyTestCase):
    """The run's wall clock bounds every network operation, not only the next read."""
    TOLERANCE = 1.5

    def setUp(self):
        super().setUp()
        self.offset = [0.0]
        self.ledger = RunLedger(Path(self.temp.name), "wall",
                                clock=lambda: time.time() + self.offset[0])
        self.proxy = self.make_proxy(ledger=self.ledger, idle_timeout=30)

    def leave_seconds(self, seconds):
        self.assertTrue(self.ledger.reserve_launch())
        self.offset[0] = 120 - seconds
        self.assertAlmostEqual(self.ledger.remaining_seconds(), seconds, delta=0.2)

    def assert_cut_at_deadline(self, started, seconds):
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, seconds - 0.2)
        self.assertLess(elapsed, seconds + self.TOLERANCE)
        self.assertEqual(self.settle(), ["UNKNOWN"])
        status, data = self.post()
        self.assertEqual(status, 429)
        self.assertIn(self.error_code(data), ("WALL_CLOCK_EXCEEDED", "USAGE_UNKNOWN"))
        self.assertEqual(self.upstream.count(), 1)      # never replayed

    def test_silent_upstream_is_cut_at_the_wall_deadline_not_the_idle_timeout(self):
        self.upstream.mode = "delayed-ok"
        self.upstream.delay = 6
        self.leave_seconds(0.8)
        started = time.monotonic()
        status, data = self.post()
        self.assertEqual((status, self.error_code(data)), (502, "UPSTREAM_OUTCOME_UNKNOWN"))
        self.assert_cut_at_deadline(started, 0.8)

    def test_trickling_stream_cannot_outlive_the_deadline(self):
        self.upstream.mode = "trickle"
        self.leave_seconds(0.8)
        started = time.monotonic()
        status, data = self.post()
        self.assertEqual(status, 200)
        self.assertIn(b"response.created", data)
        self.assertNotIn(b"response.completed", data)
        self.assert_cut_at_deadline(started, 0.8)

    def test_trickling_response_headers_cannot_outlive_the_deadline(self):
        self.upstream.mode = "slow-headers"
        self.leave_seconds(0.8)
        started = time.monotonic()
        status, data = self.post()
        self.assertEqual((status, self.error_code(data)), (502, "UPSTREAM_OUTCOME_UNKNOWN"))
        self.assert_cut_at_deadline(started, 0.8)

    def test_deadline_already_passed_never_contacts_the_upstream(self):
        self.leave_seconds(0.8)
        self.offset[0] = 119.0 + 5                      # more than the whole budget has elapsed
        status, data = self.post()
        self.assertEqual((status, self.error_code(data)), (429, "WALL_CLOCK_EXCEEDED"))
        self.assertEqual(self.upstream.count(), 0)


class DownstreamFramingTests(ProxyTestCase):
    def open_stream(self):
        sock = socket.create_connection(("127.0.0.1", self.proxy.port), timeout=5)
        payload = json.dumps(body_for()).encode()
        sock.sendall(b"POST %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nAuthorization: Bearer %s\r\n"
                     b"Content-Length: %d\r\n\r\n%s" % (
                         RESPONSES_PATH.encode(), self.proxy.port, TOKEN.encode(),
                         len(payload), payload))
        return sock

    def read_until(self, sock, needle):
        seen = b""
        while needle not in seen:
            chunk = sock.recv(4096)
            self.assertTrue(chunk, "stream ended before %r" % needle)
            seen += chunk
        return seen

    def assert_unknown_and_not_replayed(self):
        self.assertEqual(self.settle(), ["UNKNOWN"])
        status, data = self.post()
        self.assertEqual((status, self.error_code(data)), (429, "USAGE_UNKNOWN"))
        self.assertEqual(self.upstream.count(), 1)

    def test_client_closing_after_the_terminal_event_but_before_final_framing_is_unknown(self):
        self.upstream.mode = "terminal-hold"
        sock = self.open_stream()
        self.read_until(sock, b"response.completed")
        sock.close()                                    # orderly FIN before the last chunk
        time.sleep(0.3)
        self.upstream.release.set()
        self.assert_unknown_and_not_replayed()

    def test_client_reset_after_the_terminal_event_is_unknown(self):
        self.upstream.mode = "terminal-hold"
        sock = self.open_stream()
        self.read_until(sock, b"response.completed")
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        sock.close()                                    # RST
        time.sleep(0.3)
        self.upstream.release.set()
        self.assert_unknown_and_not_replayed()

    def test_client_that_reads_everything_then_closes_leaves_a_succeeded_entry(self):
        sock = self.open_stream()
        self.read_until(sock, b"0\r\n\r\n")
        sock.close()
        self.assertEqual(self.settle(), ["SUCCEEDED"])

    def test_follow_up_request_never_races_the_final_framing(self):
        for index in range(15):
            ledger = RunLedger(Path(self.temp.name), "race-%d" % index, clock=self.clock)
            proxy = self.make_proxy(ledger=ledger)
            self.assertEqual(self.post(proxy=proxy)[0], 200)
            status, data = self.post(proxy=proxy)       # immediately, no settle()
            self.assertEqual(status, 200, data)
            self.assertEqual([r["phase"] for r in proxy.forwarded], ["PRIMARY", "RETRY"])


class ToolIdentityTests(ProxyTestCase):
    def test_rejected_requests_record_sanitized_tool_identities(self):
        tools = [{"type": "function", "name": "update_plan", "parameters": {"secret": "x"}},
                 {"type": "web_search"}, {"type": "local_shell"}]
        status, data = self.post(body_for(tools=tools))
        self.assertEqual((status, self.error_code(data)), (403, "TOOL_EXECUTION_FORBIDDEN"))
        self.assertEqual(self.proxy.rejected_tools, [{
            "code": "TOOL_EXECUTION_FORBIDDEN", "count": 3,
            "tools": ["function:update_plan", "local_shell", "web_search"]}])
        self.assertNotIn("secret", json.dumps(self.proxy.rejected_tools))

    def test_unlisted_tool_names_are_redacted_not_echoed(self):
        weird = [{"type": "function", "name": "a b; token=sk-live-123"},
                 {"type": "function", "name": "x" * 200}]
        status, data = self.post(body_for(tools=weird))
        self.assertEqual((status, self.error_code(data)), (403, "TOOLS_NOT_ALLOWED"))
        record = self.proxy.rejected_tools[0]
        self.assertEqual(record["tools"], ["function:<redacted>"])
        self.assertNotIn("sk-live", json.dumps(record))

    def test_malformed_tool_lists_record_counts_without_identities(self):
        self.post(body_for(tools="x"))
        self.post(body_for(tools=[1, {"type": 5}]))
        self.assertEqual([(r["code"], r["count"], r["tools"]) for r in self.proxy.rejected_tools],
                         [("TOOLS_INVALID", None, []), ("TOOLS_INVALID", 2, [])])

    def test_forwarded_requests_record_the_transmitted_tool_identities(self):
        proxy = self.make_proxy(allowed_tools=(("function", "update_plan"), ("web_search", None)))
        tools = [{"type": "function", "name": "update_plan"}, {"type": "web_search"}]
        self.assertEqual(self.post(body_for(tools=tools), proxy=proxy)[0], 200)
        self.assertEqual(proxy.forwarded[0]["tools"], ["function:update_plan", "web_search"])
        self.assertEqual(proxy.rejected_tools, [])

    def test_requests_without_tools_record_nothing(self):
        self.assertEqual(self.post()[0], 200)
        self.assertEqual(self.proxy.forwarded[0]["tools"], [])
        self.assertEqual(self.proxy.rejected_tools, [])


class FormalReviewRepairTests(ProxyTestCase):
    def test_103_then_terminal_response_is_unknown_and_never_retried(self):
        self.upstream.mode = "early-hints-then-terminal"
        status, data = self.post()
        self.assertEqual((status, self.error_code(data)), (502, "UPSTREAM_OUTCOME_UNKNOWN"))
        self.assertEqual(self.settle(), ["UNKNOWN"])
        status, data = self.post()
        self.assertEqual((status, self.error_code(data)), (429, "USAGE_UNKNOWN"))
        self.assertEqual(self.upstream.count(), 1)
        self.assertNotIn("HANDLER_FAILURE", self.proxy.rejected)

    def test_unhashable_sse_type_uses_unknown_path_without_handler_failure(self):
        self.upstream.mode = "unhashable-sse-type"
        self.assertEqual(self.post()[0], 200)
        self.assertEqual(self.settle(), ["UNKNOWN"])
        status, data = self.post()
        self.assertEqual((status, self.error_code(data)), (429, "USAGE_UNKNOWN"))
        self.assertEqual(self.upstream.count(), 1)
        self.assertNotIn("HANDLER_FAILURE", self.proxy.rejected)

    def test_unreviewed_request_fields_are_refused_before_admission(self):
        for name, value in (("background", True), ("previous_response_id", "prior"),
                            ("store", False), ("include", []), ("future_field", {})):
            with self.subTest(name=name):
                status, data = self.post(body_for(**{name: value}))
                self.assertEqual((status, self.error_code(data)), (400, "REQUEST_FIELDS_NOT_ALLOWED"))
        self.assert_nothing_forwarded()

    def test_expect_continue_is_refused_without_reading_body_or_interim_success(self):
        status, data = self.raw((
            "POST /v1/responses HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
            "Authorization: Bearer %s\r\nExpect: 100-continue\r\n"
            "Content-Length: 2\r\n\r\n" % (self.proxy.port, TOKEN)).encode())
        self.assertEqual((status, self.error_code(data)), (417, "EXPECT_NOT_ALLOWED"))
        self.assert_nothing_forwarded()

    def test_tool_kind_and_name_must_both_match(self):
        allowed = (("function", "update_plan"),)
        for tool, expected in (({"type": "function", "name": "update_plan"}, None),
                               ({"type": "custom", "name": "update_plan"}, "TOOLS_NOT_ALLOWED"),
                               ({"type": "future", "name": "update_plan"}, "TOOLS_NOT_ALLOWED"),
                               ({"type": "function"}, "TOOLS_NOT_ALLOWED"),
                               ({"type": "function", "name": "update_plan",
                                 "function": {"name": "shell"}}, "TOOLS_INVALID"),
                               ({"type": "local_shell", "name": "update_plan"},
                                "TOOL_EXECUTION_FORBIDDEN")):
            with self.subTest(tool=tool):
                self.assertEqual(check_tools([tool], allowed), expected)

    def test_incomplete_non_2xx_fixed_body_is_unknown_and_not_retried(self):
        self.upstream.mode = "status500-short"
        self.assertEqual(self.post()[0], 502)
        self.assertEqual(self.settle(), ["UNKNOWN"])
        self.assertEqual(self.post()[0], 429)
        self.assertEqual(self.upstream.count(), 1)

    def test_incomplete_non_2xx_chunked_body_is_unknown_and_not_retried(self):
        self.upstream.mode = "status500-chunked-short"
        self.assertEqual(self.post()[0], 502)
        self.assertEqual(self.settle(), ["UNKNOWN"])
        self.assertEqual(self.post()[0], 429)
        self.assertEqual(self.upstream.count(), 1)

    def test_timeout_in_non_2xx_body_is_unknown(self):
        self.upstream.mode = "status500-hang"
        proxy = self.make_proxy(idle_timeout=0.2)
        self.assertEqual(self.post(proxy=proxy)[0], 502)
        self.assertEqual(self.settle(), ["UNKNOWN"])
        self.assertEqual(self.post(proxy=proxy)[0], 429)
        self.assertEqual(self.upstream.count(), 1)

    def test_bad_chunk_separator_after_terminal_usage_cannot_succeed(self):
        self.upstream.mode = "terminal-bad-chunk-crlf"
        self.assertEqual(self.post()[0], 200)
        self.assertEqual(self.settle(), ["UNKNOWN"])
        self.assertEqual(self.post()[0], 429)
        self.assertEqual(self.upstream.count(), 1)

    def terminal_connection(self):
        self.upstream.mode = "terminal-hold"
        connection = http.client.HTTPConnection("127.0.0.1", self.proxy.port, timeout=5)
        self.addCleanup(connection.close)
        connection.request("POST", RESPONSES_PATH, body=json.dumps(body_for()),
                           headers={"Authorization": "Bearer " + TOKEN})
        response = connection.getresponse()
        data = b""
        while b"response.completed" not in data:
            data += response.read1(8192)
        return response

    def test_followup_after_terminal_waits_until_actual_stream_finalization(self):
        response = self.terminal_connection()
        result = []
        worker = threading.Thread(target=lambda: result.append(self.post()))
        worker.start()
        try:
            time.sleep(0.05)
            self.assertTrue(worker.is_alive())
            self.assertEqual(self.upstream.count(), 1)
            self.upstream.mode = "ok"
            self.upstream.release.set()
            response.read()
        finally:
            self.upstream.release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0][0], 200)
        self.assertEqual(self.settle(), ["SUCCEEDED", "SUCCEEDED"])

    def test_settling_timeout_is_explicit_and_does_not_reserve_or_forward(self):
        response = self.terminal_connection()
        with patch("llmtool.codex_responses_proxy.SETTLE_SECONDS", 0.05):
            status, data = self.post()
        self.assertEqual((status, self.error_code(data)), (503, "STREAM_SETTLING_TIMEOUT"))
        self.assertEqual(self.ledger.snapshot()["requests"], 1)
        self.assertEqual(self.upstream.count(), 1)
        self.upstream.release.set()
        response.read()
        self.assertEqual(self.settle(), ["SUCCEEDED"])

    def test_expiry_before_connect_is_known_not_sent_not_unknown(self):
        with patch.object(self.ledger, "remaining_seconds", return_value=0):
            status, data = self.post()
        self.assertEqual((status, self.error_code(data)), (502, "UPSTREAM_NOT_SENT"))
        self.assertEqual(self.settle(), ["FAILED"])
        self.assertEqual(self.upstream.count(), 0)

    def test_corrupt_ledger_becomes_sanitized_503_without_forwarding(self):
        self.ledger.reserve_launch()
        Path(self.temp.name, "run-1.ledger.json").write_text('{"PRIVATE_STATE":true}')
        status, data = self.post()
        self.assertEqual((status, self.error_code(data)), (503, "LEDGER_UNAVAILABLE"))
        self.assertEqual(self.upstream.count(), 0)

    def test_listener_oserror_is_sanitized(self):
        proxy = ResponsesProxy(self.ledger, self.upstream.endpoint, TOKEN)
        with patch("llmtool.codex_responses_proxy._Server", side_effect=OSError("PRIVATE_PATH")):
            with self.assertRaisesRegex(LLMToolError, "^proxy listener unavailable$"):
                proxy.start()


class ConstructionTests(ProxyTestCase):
    def test_upstream_must_be_a_loopback_http_origin(self):
        for bad in ("http://example.com:80", "https://127.0.0.1:443", "http://localhost:80",
                    "http://0.0.0.0:80", "http://127.0.0.1:80/v1", "ws://127.0.0.1:80"):
            with self.subTest(upstream=bad), self.assertRaises(LLMToolError):
                ResponsesProxy(self.ledger, bad, TOKEN)

    def test_bearer_token_must_be_a_strong_printable_secret(self):
        for bad in ("", "short", "has space in the token value", "tab\tinside-the-token-value",
                    "x" * 300, None, "ünïcode-token-value-long"):
            with self.subTest(token=bad), self.assertRaises(LLMToolError):
                ResponsesProxy(self.ledger, self.upstream.endpoint, bad)

    def test_listener_is_loopback_only_and_closes(self):
        self.assertEqual(self.proxy._server.server_address[0], "127.0.0.1")
        port = self.proxy.port
        self.proxy.close()
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=1)


class DownstreamLivenessTests(unittest.TestCase):
    """The client-liveness probe must never block, even with a socket timeout set."""

    def handler_over(self, sock):
        handler = _Handler.__new__(_Handler)
        handler.connection = sock
        return handler

    def pair(self):
        ours, theirs = socket.socketpair()
        ours.settimeout(5)                              # as the handler sets before each write
        self.addCleanup(ours.close)
        self.addCleanup(theirs.close)
        return ours, theirs

    def test_open_idle_client_is_reported_open_without_waiting(self):
        ours, _ = self.pair()
        started = time.monotonic()
        self.assertTrue(self.handler_over(ours)._downstream_open())
        self.assertLess(time.monotonic() - started, 0.5)

    def test_closed_client_is_reported_gone(self):
        ours, theirs = self.pair()
        theirs.close()
        self.assertFalse(self.handler_over(ours)._downstream_open())

    def test_reset_client_is_reported_gone(self):
        ours, theirs = self.pair()
        theirs.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        theirs.close()
        self.assertFalse(self.handler_over(ours)._downstream_open())

    def test_pending_client_bytes_do_not_count_as_gone_and_are_not_consumed(self):
        ours, theirs = self.pair()
        theirs.sendall(b"x")
        handler = self.handler_over(ours)
        self.assertTrue(handler._downstream_open())
        self.assertEqual(ours.recv(1), b"x")


class HelperTests(unittest.TestCase):
    def test_sse_usage_handles_split_events_and_crlf(self):
        parser = SSEUsage()
        event = usage_event(input_tokens=3, output_tokens=4).replace(b"\n", b"\r\n")
        for index in range(0, len(event), 7):
            parser.feed(event[index:index + 7])
        self.assertEqual((parser.terminal, parser.usage), (True, (3, 4)))

    def test_sse_usage_ignores_invalid_usage_and_junk(self):
        for data in (b"data: {not json}\n\n", b": comment\n\n",
                     sse([], response={"usage": {"input_tokens": 1, "output_tokens": 1}}),
                     sse({}, response={"usage": {"input_tokens": 1, "output_tokens": 1}}),
                     sse("response.completed",
                     response={"usage": {"input_tokens": True, "output_tokens": 1}}),
                     sse("response.completed", response={"usage": {"input_tokens": -1,
                                                                    "output_tokens": 1}})):
            parser = SSEUsage()
            parser.feed(data)
            self.assertIsNone(parser.usage, data)

    def test_sse_usage_takes_the_first_terminal_event_only(self):
        parser = SSEUsage()
        parser.feed(usage_event(output_tokens=1) + usage_event(output_tokens=999))
        self.assertEqual(parser.usage, (7, 1))

    def test_oversized_event_is_refused(self):
        with self.assertRaises(ValueError):
            SSEUsage().feed(b"data: " + b"x" * (1024 * 1024 + 1))

    def test_check_tools_accepts_absent_and_empty(self):
        self.assertIsNone(check_tools(None, frozenset()))
        self.assertIsNone(check_tools([], frozenset()))


if __name__ == "__main__":
    unittest.main()
