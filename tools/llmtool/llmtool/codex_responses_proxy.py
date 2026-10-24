"""Loopback-only Responses middleware that owns every upstream request.

The listener binds 127.0.0.1 on an ephemeral port and the upstream is one fixed
loopback origin chosen at construction (a non-loopback upstream is refused: relaying
a real Codex credential needs a separate authority decision). Only
POST /v1/responses with the run's synthetic bearer is served. Before any byte is
forwarded the request is admitted by the durable RunLedger, its output cap is
rewritten to the ledger's cap, and its tool list is checked. The upstream request is
rebuilt from parsed JSON plus a fixed header set; nothing from the client request
(headers, target, duplicate JSON keys) reaches the upstream. Redirects, upgrades and
other paths are never followed or forwarded. The run's wall deadline bounds every
network operation (per-operation timeouts plus a watchdog that shuts the upstream
socket). An entry becomes SUCCEEDED only after a terminal usage event AND the final
framing was written and flushed to a client that is still connected; any outcome that
cannot be proven (transport error after sending, truncated or unterminated stream,
timeout, downstream write failure or disconnect) is recorded UNKNOWN and never
replayed. A reservation that is not finalized at all also reads back as UNKNOWN.
"""

import hmac
import http.client
import json
import re
import select
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .codex_app_server_budget import Denial, OutputCapExceeded
from .codex_app_server_runtime import validate_loopback_endpoint
from .errors import LLMToolError

RESPONSES_PATH = "/v1/responses"
MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_STREAM_BYTES = 2 * 1024 * 1024
MAX_EVENT_BYTES = 1024 * 1024
MAX_ERROR_BYTES = 64 * 1024
CLIENT_TIMEOUT_SECONDS = 15
UPSTREAM_IDLE_SECONDS = 30.0
MAX_RECORDS = 1000
MAX_TOOL_LABELS = 64
SETTLE_SECONDS = 2.0
REQUEST_FIELDS = frozenset((
    "model", "input", "instructions", "stream", "max_output_tokens", "tools",
    "tool_choice", "parallel_tool_calls", "reasoning", "text"))
_LABEL = re.compile(r"[A-Za-z0-9_.:/-]{1,64}")
_TERMINAL_EVENTS = frozenset(
    ("response.completed", "response.incomplete", "response.failed"))
# Anything that could run commands, edit or read files, or reach MCP/code tools is
# refused even if the caller lists it as allowed.
_EXECUTION_TOOL = re.compile(
    r"shell|exec|command|bash|terminal|patch|mcp|python|computer|code_interpreter|"
    r"container|sandbox|write|file|stdin|process|spawn|run", re.IGNORECASE)


def _no_constant(_):
    raise ValueError("non-finite number")


def tool_identity(tool):
    """(type, name) of one Responses tool entry, or None when it is malformed."""
    if type(tool) is not dict:
        return None
    kind = tool.get("type")
    name = tool.get("name")
    function = tool.get("function")
    if function is not None:
        if type(function) is not dict or kind != "function":
            return None
        if name is not None and function.get("name") not in (None, name):
            return None
    if name is None and type(function) is dict:
        name = function.get("name")
    if type(kind) is not str or (name is not None and type(name) is not str):
        return None
    return kind, name


def _safe_label(text):
    return text if _LABEL.fullmatch(text) else "<redacted>"


def describe_tools(tools):
    """Sorted, sanitized "type[:name]" labels for the well-formed entries of a tool list.

    Only identities are kept (never descriptions, schemas or arguments), so this is
    safe to retain as wire evidence of exactly which tools a request transmitted.
    """
    if type(tools) is not list:
        return []
    labels = set()
    for tool in tools[:MAX_TOOL_LABELS]:
        identity = tool_identity(tool)
        if identity is not None:
            kind, name = identity
            labels.add(_safe_label(kind) + (":" + _safe_label(name) if name else ""))
    return sorted(labels)


def check_tools(tools, allowed):
    """None when acceptable, else a fixed rejection code. Deny by default."""
    if tools is None:
        return None
    if type(tools) is not list:
        return "TOOLS_INVALID"
    identities = [tool_identity(tool) for tool in tools]
    if None in identities:
        return "TOOLS_INVALID"
    # Severity does not depend on list order: execution-capable beats merely unlisted.
    if any(_EXECUTION_TOOL.search(kind + " " + (name or "")) for kind, name in identities):
        return "TOOL_EXECUTION_FORBIDDEN"
    if any((kind, name) not in allowed for kind, name in identities):
        return "TOOLS_NOT_ALLOWED"
    return None


class SSEUsage:
    """Incremental SSE scan for the first terminal Responses event and its usage."""

    def __init__(self):
        self._buffer = b""
        self.terminal = False
        self.usage = None            # (input_tokens, output_tokens) or None

    def feed(self, chunk):
        self._buffer = (self._buffer + chunk).replace(b"\r\n", b"\n")
        while b"\n\n" in self._buffer:
            event, self._buffer = self._buffer.split(b"\n\n", 1)
            if not self.terminal:
                self._event(event)
        if len(self._buffer) > MAX_EVENT_BYTES:
            raise ValueError("SSE event too large")

    def _event(self, event):
        data = b"\n".join(line[5:].lstrip() for line in event.split(b"\n")
                          if line.startswith(b"data:"))
        try:
            message = json.loads(data.decode("utf-8"), parse_constant=_no_constant)
        except (UnicodeError, ValueError, RecursionError):
            return
        if type(message) is not dict or message.get("type") not in _TERMINAL_EVENTS:
            return
        self.terminal = True
        response = message.get("response")
        usage = response.get("usage") if type(response) is dict else None
        if type(usage) is dict:
            tokens = (usage.get("input_tokens"), usage.get("output_tokens"))
            if all(type(value) is int and value >= 0 for value in tokens):
                self.usage = tokens


class _StrictHTTPResponse(http.client.HTTPResponse):
    """Record framing completion instead of treating any socket EOF as success."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._trailers_complete = False

    def _read_and_discard_trailer(self):
        # The stdlib accepts EOF here, including after only "0\r\n". Require the
        # trailer section's CRLF terminator before marking chunked framing complete.
        while True:
            line = self.fp.readline(65537)
            if len(line) > 65536:
                raise http.client.LineTooLong("trailer line")
            if not line or not line.endswith(b"\r\n"):
                raise http.client.IncompleteRead(b"")
            if line == b"\r\n":
                self._trailers_complete = True
                return

    def _get_chunk_left(self):
        # HTTPResponse otherwise discards two bytes after a chunk without checking
        # CRLF. Never count malformed chunk separators as complete framing.
        left = self.chunk_left
        if not left:
            if left is not None and self._safe_read(2) != b"\r\n":
                raise http.client.IncompleteRead(b"")
            line = self.fp.readline(65537)
            if len(line) > 65536:
                raise http.client.LineTooLong("chunk size")
            size = line.split(b";", 1)[0].strip()
            if not line.endswith(b"\r\n") or re.fullmatch(b"[0-9A-Fa-f]+", size) is None:
                raise http.client.IncompleteRead(b"")
            left = int(size, 16)
            if left == 0:
                self._read_and_discard_trailer()
                self._close_conn()
                left = None
            self.chunk_left = left
        return left

    def framing_complete(self):
        if self.chunked:
            return self._trailers_complete and self.chunk_left is None and self.isclosed()
        # read1() can return EOF with a positive length after a premature FIN.
        # Close-delimited/unknown framing (length=None) cannot prove completion.
        return self.length == 0


class _Abort(Exception):
    """Internal: stop relaying; the reservation is finalized from what was proven."""


class _Server(ThreadingHTTPServer):
    daemon_threads = False      # server_close() joins handlers so ledger entries are finalized
    allow_reuse_address = False
    block_on_close = True
    request_queue_size = 16

    def handle_error(self, request, client_address):
        # Never emit traceback/request data from an unexpected handler failure.
        self.proxy._record_rejection("HANDLER_FAILURE")


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = CLIENT_TIMEOUT_SECONDS
    server_version = "llmtool-responses-proxy"
    sys_version = ""

    def log_message(self, *_):
        pass

    def handle_expect_100(self):
        self._reject(417, "EXPECT_NOT_ALLOWED")
        return False

    def setup(self):
        super().setup()
        self.server.proxy._track_client(self.connection, True)

    def finish(self):
        try:
            super().finish()
        finally:
            self.server.proxy._track_client(self.connection, False)

    # -- replies -------------------------------------------------------------
    def _reply(self, status, code):
        self.close_connection = True
        body = json.dumps({"error": code}, separators=(",", ":")).encode("ascii")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except OSError:
            pass

    def _reject(self, status, code, tools=None):
        self.server.proxy._record_rejection(code, tools)
        self._reply(status, code)

    # -- request screening (nothing here touches the ledger or the upstream) --
    def _upgrade_requested(self):
        connection = ",".join(self.headers.get_all("Connection") or []).lower()
        return bool(self.headers.get("Upgrade")) or "upgrade" in connection

    def _common_checks(self):
        proxy = self.server.proxy
        if self.headers.get("Expect") is not None:
            self._reject(417, "EXPECT_NOT_ALLOWED")
            return False
        if self._upgrade_requested():
            self._reject(426, "UPGRADE_NOT_ALLOWED")
            return False
        if self.headers.get("Host") != "127.0.0.1:%d" % proxy.port:
            self._reject(421, "HOST_NOT_ALLOWED")
            return False
        return True

    def _method_not_allowed(self):
        if self._common_checks():
            self._reject(405, "METHOD_NOT_ALLOWED")

    do_GET = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _method_not_allowed

    def _read_body(self):
        lengths = self.headers.get_all("Content-Length") or []
        if self.headers.get("Transfer-Encoding") is not None or len(lengths) != 1:
            self._reject(411, "CONTENT_LENGTH_REQUIRED")
            return None
        encoding = (self.headers.get("Content-Encoding") or "identity").strip().lower()
        if encoding != "identity":
            self._reject(415, "CONTENT_ENCODING_NOT_ALLOWED")
            return None
        try:
            length = int(lengths[0])
        except ValueError:
            length = -1
        if (not 0 < length <= MAX_BODY_BYTES
                or not (lengths[0].isascii() and lengths[0].isdigit())):
            self._reject(400, "REQUEST_INVALID")
            return None
        try:
            body = self.rfile.read(length)
        except OSError:
            body = b""
        if len(body) != length:
            self._reject(400, "REQUEST_INVALID")
            return None
        return body

    def do_POST(self):
        proxy = self.server.proxy
        if not self._common_checks():
            return
        # Compare the raw request-line target: some http.server versions rewrite a
        # leading "//" in self.path, and the fixed endpoint must not depend on that.
        words = self.requestline.split()
        if len(words) != 3 or words[1] != RESPONSES_PATH or self.path != RESPONSES_PATH:
            self._reject(404, "ENDPOINT_NOT_ALLOWED")
            return
        supplied = self.headers.get("Authorization") or ""
        if not hmac.compare_digest(supplied.encode("utf-8", "replace"),
                                   proxy.authorization.encode("ascii")):
            self._reject(401, "AUTH_REQUIRED")
            return
        raw = self._read_body()
        if raw is None:
            return
        try:
            body = json.loads(raw.decode("utf-8"), parse_constant=_no_constant)
        except (UnicodeError, ValueError, RecursionError):
            self._reject(400, "REQUEST_INVALID")
            return
        if type(body) is not dict or body.get("stream") is not True:
            self._reject(400, "REQUEST_INVALID")
            return
        if set(body) - REQUEST_FIELDS:
            self._reject(400, "REQUEST_FIELDS_NOT_ALLOWED")
            return
        labels = describe_tools(body.get("tools"))
        problem = check_tools(body.get("tools"), proxy.allowed_tools)
        if problem is not None:
            tools = body.get("tools")
            self._reject(400 if problem == "TOOLS_INVALID" else 403, problem, {
                "count": len(tools) if type(tools) is list else None, "tools": labels})
            return
        requested = body.get("max_output_tokens")
        if requested is not None and (type(requested) is not int or requested <= 0):
            self._reject(400, "OUTPUT_CAP_INVALID")
            return
        if not proxy._wait_settled():
            self._reject(503, "STREAM_SETTLING_TIMEOUT")
            return
        try:
            reservation = proxy.ledger.reserve_request(requested)
        except (LLMToolError, OSError):
            self._reject(503, "LEDGER_UNAVAILABLE")
            return
        if isinstance(reservation, Denial):
            self._reject(429, reservation.code.value)
            return
        body["max_output_tokens"] = reservation.max_output_tokens
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        proxy._record_forward({
            "sequence": reservation.sequence, "phase": reservation.phase.value,
            "max_output_tokens": reservation.max_output_tokens, "tools": labels,
            "payload_bytes": len(payload)})
        self._forward(proxy, reservation, payload)

    # -- forwarding ----------------------------------------------------------
    def _downstream_open(self):
        """False once the client has closed: no more request bytes can arrive, so any
        readable EOF means it is gone and the final framing cannot be delivered."""
        try:
            # A zero-timeout select first: with a socket timeout set, recv() would wait
            # for readability itself (MSG_DONTWAIT does not prevent that).
            if not select.select([self.connection], [], [], 0)[0]:
                return True
            return self.connection.recv(1, socket.MSG_PEEK) != b""
        except (OSError, ValueError):
            return False

    def _forward(self, proxy, reservation, payload):
        # result: unknown | failed | complete. downstream goes False on write/flush failure or
        # a client that left before the final framing; SUCCEEDED needs it True.
        # Until request() is entered no upstream bytes could have been sent.
        state = {"result": "failed", "downstream": True}
        parser = SSEUsage()
        connection = None
        started_reply = False
        settling = False
        finalized = []
        expired = threading.Event()
        try:
            remaining = proxy.ledger.remaining_seconds()
        except (LLMToolError, OSError):
            remaining = 0.0
        deadline = time.monotonic() + remaining

        def finish():
            # Exactly once. Failure replies finalize before they are sent; a streamed
            # reply finalizes after its framing is flushed, and follow-up requests wait
            # on the settling gate so they never observe the entry half-finished.
            if not finalized:
                finalized.append(True)
                problem = self._finalize(proxy.ledger, reservation, state, parser)
                if problem is not None:
                    proxy._record_rejection(problem)

        def expire():
            # Hard wall: unblock whatever upstream read/send is in progress.
            expired.set()
            sock = connection.sock if connection is not None else None
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        def left():
            seconds = deadline - time.monotonic()
            if seconds <= 0 or expired.is_set():
                raise _Abort()
            return seconds

        def arm():
            # Re-derive the socket timeout from the wall deadline before each operation.
            seconds = left()
            if connection.sock is not None:
                connection.sock.settimeout(min(proxy.idle_timeout, seconds))

        def down(data):
            self.connection.settimeout(min(CLIENT_TIMEOUT_SECONDS, left()))
            try:
                self.wfile.write(data)
                self.wfile.flush()
            except OSError:
                state["downstream"] = False
                raise

        timer = threading.Timer(remaining, expire)
        timer.daemon = True
        try:
            connection = http.client.HTTPConnection(
                proxy.upstream_host, proxy.upstream_port,
                timeout=max(0.05, min(proxy.idle_timeout, left())))
            connection.response_class = _StrictHTTPResponse
            proxy._track_upstream(connection, True)
            timer.start()
            try:
                connection.connect()
            except OSError:
                state["result"] = "failed"          # nothing was sent
                finish()
                self._reply(502, "UPSTREAM_UNREACHABLE")
                return
            arm()
            state["result"] = "unknown"  # request() may send partially before raising
            connection.request("POST", RESPONSES_PATH, body=payload, headers={
                "Content-Type": "application/json", "Accept": "text/event-stream",
                "Authorization": proxy.authorization, "Connection": "close"})
            arm()
            upstream = connection.getresponse()
            if not 200 <= upstream.status < 300:    # includes 3xx: redirects are never followed
                total_error = 0
                while True:
                    arm()
                    chunk = upstream.read1(8192)
                    if not chunk:
                        if not upstream.framing_complete():
                            raise _Abort()
                        break
                    total_error += len(chunk)
                    if total_error > MAX_ERROR_BYTES:
                        raise _Abort()
                state["result"] = "failed"
                finish()
                self._reply(502, "UPSTREAM_REDIRECT_BLOCKED" if 300 <= upstream.status < 400
                            else "UPSTREAM_FAILED")
                return
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.connection.settimeout(min(CLIENT_TIMEOUT_SECONDS, left()))
            try:
                self.end_headers()
            except OSError:
                state["downstream"] = False
                raise
            started_reply = True
            total = 0
            while True:
                arm()
                chunk = upstream.read1(8192)
                if not chunk:
                    if not upstream.framing_complete():
                        raise _Abort()
                    break
                total += len(chunk)
                if total > MAX_STREAM_BYTES:
                    raise _Abort()
                parser.feed(chunk)
                if parser.usage is not None and parser.usage[1] > reservation.max_output_tokens:
                    raise _Abort()    # finalize retains the actual overrun and blocks replay
                if parser.terminal and not settling:
                    proxy._settle_begin()
                    settling = True   # before terminal bytes can reach a follow-up caller
                down(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
            # The upstream stream ended cleanly. The entry may only become SUCCEEDED once
            # the final framing is written and flushed to a client that is still there.
            if not self._downstream_open():
                state["downstream"] = False
            if not settling:
                proxy._settle_begin()
                settling = True
            if state["downstream"]:
                down(b"0\r\n\r\n")                  # only a fully relayed stream is terminated
                state["result"] = "complete"        # usage alone never proves delivery
            finish()
        except (OSError, http.client.HTTPException, ValueError, _Abort):
            finish()
            if not started_reply:
                self._reply(502, "UPSTREAM_NOT_SENT" if state["result"] == "failed"
                            else "UPSTREAM_OUTCOME_UNKNOWN")
            self.close_connection = True
        finally:
            timer.cancel()
            if connection is not None:
                proxy._track_upstream(connection, False)
                connection.close()
            finish()
            if settling:
                proxy._settle_end()

    @staticmethod
    def _finalize(ledger, reservation, state, parser):
        try:
            if parser.usage is not None and parser.usage[1] > reservation.max_output_tokens:
                ledger.complete(reservation, *parser.usage)
            elif state["result"] == "failed":
                ledger.fail(reservation)
            elif (state["result"] == "complete" and parser.usage is not None
                  and state["downstream"]):
                ledger.complete(reservation, *parser.usage)
            else:
                ledger.unknown(reservation)
        except OutputCapExceeded:
            return "OUTPUT_CAP_EXCEEDED"
        except Exception:                           # an unfinalized entry reads as UNKNOWN
            return "LEDGER_UNAVAILABLE"
        return None


class ResponsesProxy:
    """Owns the listener thread; use as a context manager."""

    def __init__(self, ledger, upstream, bearer_token, allowed_tools=(),
                 idle_timeout=UPSTREAM_IDLE_SECONDS):
        origin = validate_loopback_endpoint(upstream)
        if (not isinstance(bearer_token, str) or not 16 <= len(bearer_token) <= 256
                or not bearer_token.isascii() or not bearer_token.isprintable()
                or " " in bearer_token):
            raise LLMToolError("proxy bearer token invalid")
        self.ledger = ledger
        self.upstream_host = "127.0.0.1"
        self.upstream_port = int(origin.rsplit(":", 1)[1])
        self.authorization = "Bearer " + bearer_token
        try:
            identities = tuple(allowed_tools)
            if any(type(item) is not tuple or len(item) != 2 or type(item[0]) is not str
                   or (item[1] is not None and type(item[1]) is not str) for item in identities):
                raise ValueError
            self.allowed_tools = frozenset(identities)
        except (TypeError, ValueError):
            raise LLMToolError("proxy tool allowlist invalid") from None
        self.idle_timeout = idle_timeout
        self.port = None
        self._lock = threading.Lock()
        self._forwarded = []
        self._rejected = []
        self._rejected_tools = []
        self._settle = threading.Condition()
        self._settling = 0
        self._clients = set()
        self._upstreams = set()
        self._server = None
        self._thread = None

    # -- observation (sanitized: counts, caps and tool identities only) -------
    @property
    def forwarded(self):
        with self._lock:
            return [dict(record) for record in self._forwarded]

    @property
    def rejected(self):
        with self._lock:
            return list(self._rejected)

    def _record_forward(self, record):
        with self._lock:
            if len(self._forwarded) < MAX_RECORDS:
                self._forwarded.append(record)

    @property
    def rejected_tools(self):
        """Sanitized tool identities (and entry counts) of requests refused on tools."""
        with self._lock:
            return [dict(record, tools=list(record["tools"])) for record in self._rejected_tools]

    def _record_rejection(self, code, tools=None):
        with self._lock:
            if len(self._rejected) < MAX_RECORDS:
                self._rejected.append(code)
            if tools is not None and len(self._rejected_tools) < MAX_RECORDS:
                self._rejected_tools.append(dict(tools, code=code))

    # -- settling gate: a stream that has flushed its framing finalizes its ledger
    # entry before any follow-up request is admitted ---------------------------
    def _settle_begin(self):
        with self._settle:
            self._settling += 1

    def _settle_end(self):
        with self._settle:
            self._settling -= 1
            self._settle.notify_all()

    def _wait_settled(self):
        with self._settle:
            return self._settle.wait_for(lambda: self._settling == 0, SETTLE_SECONDS)

    def _track_client(self, connection, add):
        with self._lock:
            (self._clients.add if add else self._clients.discard)(connection)

    def _track_upstream(self, connection, add):
        with self._lock:
            (self._upstreams.add if add else self._upstreams.discard)(connection)

    # -- lifecycle -----------------------------------------------------------
    def start(self):
        if self._server is not None:
            raise LLMToolError("proxy already started")
        try:
            self._server = _Server(("127.0.0.1", 0), _Handler)
            self._server.proxy = self
            self.port = self._server.server_address[1]
            self._thread = threading.Thread(
                target=self._server.serve_forever, name="codex-responses-proxy", daemon=True)
            self._thread.start()
        except (OSError, RuntimeError):
            if self._server is not None:
                try:
                    self._server.server_close()
                except OSError:
                    pass
                self._server = None
            raise LLMToolError("proxy listener unavailable") from None
        return self

    @property
    def endpoint(self):
        return "http://127.0.0.1:%d" % self.port

    def close(self):
        server, self._server = self._server, None
        if server is None:
            return
        server.shutdown()
        with self._lock:
            live = list(self._clients) + [c.sock for c in self._upstreams if c.sock is not None]
        for sock in live:                           # unblock handlers; they finalize as UNKNOWN
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        server.server_close()
        self._thread.join(10)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()
        return False
