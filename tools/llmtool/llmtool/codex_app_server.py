"""Codex App-server JSON-RPC (JSONL over stdio) protocol adapter.

Pinned to codex-cli 0.163.0: messages are one JSON object per line and carry no
"jsonrpc" member. This module only speaks the protocol over caller-owned pipes;
process launch, CODEX_HOME construction and upstream budgeting live elsewhere.
The client never grants an approval, elicitation or tool callback, and it fails
closed if the child reports any command, file, MCP, search or other tool item.
"""

import json
import os
import re
import selectors
import time

from .errors import LLMToolError

PINNED_VERSION = "0.163.0"
MAX_LINE_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_EVENTS = 1000
_READ_SIZE = 65536
_USER_AGENT = re.compile(r"[^\s/]+/(\d+\.\d+\.\d+)(?:[\s(;-]|$)")
_EVENTS = frozenset((
    "turn/completed", "item/started", "item/completed", "item/agentMessage/delta", "error"))
_INFORMATIONAL_EVENTS = frozenset((
    "thread/started", "turn/started", "thread/tokenUsage/updated", "thread/status/changed",
    "remoteControl/status/changed", "account/rateLimits/updated"))
_TEXT_ITEMS = frozenset(("agentMessage", "userMessage", "reasoning"))
# Item kinds that mean something other than model text happened or was attempted.
_TOOL_ITEMS = frozenset((
    "commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "collabAgentToolCall",
    "subAgentActivity", "webSearch", "imageView", "imageGeneration", "functionCallOutput"))
_TURN_STATUSES = frozenset(("completed", "interrupted", "failed", "inProgress"))


class AppServerTimeout(LLMToolError):
    """The child did not answer before the deadline; the outcome is UNKNOWN."""


class AppServerClosed(LLMToolError):
    """The child closed or broke the pipe; the outcome is UNKNOWN."""


def _invalid(detail="app-server message invalid"):
    return LLMToolError(detail)


class AppServerClient:
    """Line-oriented client over the child's stdin/stdout file objects."""

    def __init__(self, stdin, stdout, deadline, check=None):
        self._stdin = stdin
        self._stdout = stdout
        self._deadline = deadline
        self._check = check or (lambda: None)
        self._buffer = bytearray()
        self._next_id = 1
        self._selector = selectors.DefaultSelector()
        os.set_blocking(stdout.fileno(), False)
        self._selector.register(stdout, selectors.EVENT_READ)
        # Writes are deadline-bound too: a child that stops reading must not be able to
        # hold a payload larger than the pipe buffer past the monotonic deadline.
        self._write_selector = selectors.DefaultSelector()
        os.set_blocking(stdin.fileno(), False)
        self._write_selector.register(stdin, selectors.EVENT_WRITE)
        self._broken = False
        self._events = []
        self.event_overflow = 0
        self.notifications = 0

    def close(self):
        self._selector.close()
        self._write_selector.close()

    def _send(self, message):
        if self._broken:
            raise AppServerClosed("app-server connection closed")
        data = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
        view = memoryview(data)
        try:
            descriptor = self._stdin.fileno()
        except (OSError, ValueError):
            self._broken = True
            raise AppServerClosed("app-server connection closed") from None
        while view:
            self._check()
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                # A partly written line would corrupt the stream, so it is never reused.
                self._broken = self._broken or len(view) != len(data)
                raise AppServerTimeout("app-server timed out")
            try:
                sent = os.write(descriptor, view)
            except BlockingIOError:
                try:
                    self._write_selector.select(min(remaining, 0.1))
                except (OSError, ValueError):
                    self._broken = True
                    raise AppServerClosed("app-server connection closed") from None
                continue
            except InterruptedError:
                continue
            except (OSError, ValueError):
                self._broken = True
                raise AppServerClosed("app-server connection closed") from None
            view = view[sent:]

    def _read_message(self):
        while True:
            self._check()
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                if newline > MAX_LINE_BYTES:
                    raise _invalid("app-server message too large")
                line = bytes(self._buffer[:newline])
                del self._buffer[:newline + 1]
                if not line.strip():
                    continue
                try:
                    message = json.loads(line.decode("utf-8"))
                except (UnicodeError, ValueError, RecursionError):
                    raise _invalid() from None
                if type(message) is not dict:
                    raise _invalid()
                return message
            if len(self._buffer) > MAX_LINE_BYTES:
                raise _invalid("app-server message too large")
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                raise AppServerTimeout("app-server timed out")
            if not self._selector.select(min(remaining, 0.1)):
                continue
            try:
                chunk = os.read(self._stdout.fileno(), _READ_SIZE)
            except BlockingIOError:
                continue
            except OSError:
                raise AppServerClosed("app-server connection closed") from None
            if not chunk:
                raise AppServerClosed("app-server connection closed")
            self._buffer.extend(chunk)

    def _dispatch(self, message):
        """Handle a message that is not the awaited response: refuse requests, buffer events."""
        if type(message.get("method")) is not str:
            raise _invalid()        # e.g. a response nobody is waiting for
        if "id" in message:
            # No approval, elicitation or tool callback is ever granted.
            self._send({"id": message["id"], "error": {"code": -32601, "message": "not supported"}})
            return
        self.notifications += 1
        method = message["method"]
        if method not in _EVENTS and method not in _INFORMATIONAL_EVENTS:
            raise _invalid("app-server event type unsupported")
        if method in ("item/started", "item/completed"):
            params = message.get("params")
            item = params.get("item") if type(params) is dict else None
            kind = item.get("type") if type(item) is dict else None
            if type(kind) is str and kind in _TOOL_ITEMS:
                raise LLMToolError("app-server reported a tool item; refusing")
            if type(kind) is not str or kind not in _TEXT_ITEMS:
                raise _invalid("app-server item type unsupported")
        if method == "error":
            raise _invalid("app-server reported an error")
        if method in _EVENTS:
            if len(self._events) < MAX_EVENTS:
                self._events.append(message)
            else:
                self.event_overflow += 1

    def request(self, method, params):
        """Send one request and return its result; refuse any server request."""
        request_id = self._next_id
        self._next_id += 1
        self._send({"id": request_id, "method": method, "params": params})
        while True:
            message = self._read_message()
            if "method" in message:
                self._dispatch(message)
                continue
            if message.get("id") != request_id:
                raise _invalid()
            if "error" in message:
                raise LLMToolError("app-server request rejected: " + method)
            result = message.get("result")
            if type(result) is not dict:
                raise _invalid()
            return result

    def notify(self, method):
        self._send({"method": method})

    def initialize(self, expected_codex_home=None):
        """Run the initialize handshake; return the sanitized server identity."""
        result = self.request("initialize", {"clientInfo": {
            "name": "llmtool", "title": "llmtool", "version": "0"}})
        agent = result.get("userAgent")
        match = _USER_AGENT.match(agent) if type(agent) is str else None
        if match is None or match.group(1) != PINNED_VERSION:
            raise LLMToolError("app-server version unsupported; expected " + PINNED_VERSION)
        home = result.get("codexHome")
        if expected_codex_home is not None and home != expected_codex_home:
            raise LLMToolError("app-server CODEX_HOME is not the isolated home")
        self.notify("initialized")
        return {"version": match.group(1), "codex_home": home}

    def _next_event(self):
        while not self._events:
            self._dispatch(self._read_message())
        return self._events.pop(0)

    def run_turn(self, prompt, cwd):
        """One ephemeral read-only thread and exactly one turn; return the model's text."""
        if type(prompt) is not str or type(cwd) is not str:
            raise _invalid("app-server turn input invalid")
        thread = self.request("thread/start", {
            "cwd": cwd, "ephemeral": True, "approvalPolicy": "never", "sandbox": "read-only"})
        thread_id = thread["thread"].get("id") if type(thread.get("thread")) is dict else None
        if type(thread_id) is not str:
            raise _invalid()
        started = self.request("turn/start", {
            "threadId": thread_id, "input": [{"type": "text", "text": prompt}]})
        turn_id = started["turn"].get("id") if type(started.get("turn")) is dict else None
        if type(turn_id) is not str:
            raise _invalid()
        messages = []            # (phase, text) of completed agent messages
        deltas = []
        size = 0
        while True:
            if self.event_overflow:
                raise _invalid("app-server event backlog exceeded")
            event = self._next_event()
            params = event.get("params") if type(event.get("params")) is dict else {}
            if params.get("threadId") not in (None, thread_id):
                continue
            method = event["method"]
            if method in ("item/started", "item/completed"):
                item = params.get("item") if type(params.get("item")) is dict else {}
                if item.get("type") in _TOOL_ITEMS:
                    raise LLMToolError("app-server reported a tool item; refusing")
                if method == "item/completed" and item.get("type") == "agentMessage":
                    if type(item.get("text")) is str:
                        messages.append((item.get("phase"), item["text"]))
            elif method == "item/agentMessage/delta":
                if type(params.get("delta")) is str:
                    size += len(params["delta"])
                    if size > MAX_OUTPUT_BYTES:
                        raise LLMToolError("app-server output exceeded 1 MiB")
                    deltas.append(params["delta"])
            elif method == "turn/completed":
                turn = params.get("turn") if type(params.get("turn")) is dict else {}
                if turn.get("id") not in (None, turn_id):
                    continue
                status = turn.get("status")
                if status not in _TURN_STATUSES:
                    raise _invalid()
                if status != "completed":
                    raise LLMToolError("app-server turn " + status)
                break
        final = [text for phase, text in messages if phase != "commentary"] or [
            text for _, text in messages]
        text = final[-1] if final else "".join(deltas)
        if len(text.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise LLMToolError("app-server output exceeded 1 MiB")
        return text
