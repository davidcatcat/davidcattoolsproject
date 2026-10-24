import json
import os
import select
import threading
import time
import unittest
from unittest.mock import patch

from llmtool import codex_app_server
from llmtool.codex_app_server import (
    AppServerClient, AppServerClosed, AppServerTimeout, PINNED_VERSION)
from llmtool.errors import LLMToolError


class Peer:
    """The App-server side of two real pipes; the client under test is the other end."""

    def __init__(self, seconds=5):
        to_server, from_client = os.pipe()
        to_client, from_server = os.pipe()
        self.client_stdin = os.fdopen(from_client, "wb", buffering=0)
        self.client_stdout = os.fdopen(to_client, "rb", buffering=0)
        self.server_in = os.fdopen(to_server, "rb", buffering=0)
        self.server_out = os.fdopen(from_server, "wb", buffering=0)
        os.set_blocking(to_server, False)
        self.client = AppServerClient(
            self.client_stdin, self.client_stdout, time.monotonic() + seconds)

    def say(self, *messages):
        for message in messages:
            data = message if isinstance(message, bytes) else json.dumps(message).encode()
            self.server_out.write(data + b"\n")

    def heard(self):
        try:
            data = self.server_in.read() or b""
        except BlockingIOError:
            data = b""
        return [json.loads(line) for line in data.splitlines() if line]

    def close(self):
        self.client.close()
        for stream in (self.client_stdin, self.client_stdout, self.server_in, self.server_out):
            try:
                stream.close()
            except OSError:
                pass


def response(ident, **result):
    return {"id": ident, "result": result}


def event(method, **params):
    return {"method": method, "params": params}


def agent_done(text, phase="final_answer", thread="t1", turn="u1"):
    return event("item/completed", threadId=thread, turnId=turn, item={
        "type": "agentMessage", "id": "m", "text": text, "phase": phase})


def turn_done(status="completed", thread="t1", turn="u1"):
    return event("turn/completed", threadId=thread, turn={"id": turn, "status": status})


class ClientTestCase(unittest.TestCase):
    def setUp(self):
        self.peer = Peer()
        self.addCleanup(self.peer.close)
        self.client = self.peer.client


class InitializeTests(ClientTestCase):
    def good(self, agent="codex_cli/0.163.0 (Mac OS 15; x86_64)", home="/iso/codex"):
        return response(1, userAgent=agent, codexHome=home, platformFamily="unix")

    def test_handshake_sends_initialize_then_initialized_and_returns_identity(self):
        self.peer.say(self.good())
        identity = self.client.initialize(expected_codex_home="/iso/codex")
        self.assertEqual(identity, {"version": PINNED_VERSION, "codex_home": "/iso/codex"})
        self.assertEqual(self.peer.heard(), [
            {"id": 1, "method": "initialize", "params": {"clientInfo": {
                "name": "llmtool", "title": "llmtool", "version": "0"}}},
            {"method": "initialized"}])

    def test_other_versions_and_malformed_agents_are_refused(self):
        for agent in ("codex/0.164.0 (x)", "codex/0.162.9", "nonsense", "", None, 7):
            with self.subTest(agent=agent):
                peer = Peer()
                self.addCleanup(peer.close)
                peer.say(response(1, userAgent=agent, codexHome="/h"))
                with self.assertRaisesRegex(LLMToolError, "version unsupported"):
                    peer.client.initialize()
                self.assertEqual([m.get("method") for m in peer.heard()], ["initialize"])

    def test_a_foreign_codex_home_is_refused_and_initialized_is_not_sent(self):
        self.peer.say(self.good(home="/host/.codex"))
        with self.assertRaisesRegex(LLMToolError, "isolated home"):
            self.client.initialize(expected_codex_home="/iso/codex")
        self.assertNotIn("initialized", [m.get("method") for m in self.peer.heard()])

    def test_error_and_malformed_replies_are_rejected(self):
        for reply in ({"id": 1, "error": {"code": -1, "message": "x"}}, {"id": 2, "result": {}},
                      {"id": 1, "result": []}, {"id": 1}, [1], b"{not json", b"\xff\xfe"):
            with self.subTest(reply=reply):
                peer = Peer()
                self.addCleanup(peer.close)
                peer.say(reply)
                with self.assertRaises(LLMToolError):
                    peer.client.initialize()

    def test_notifications_before_the_reply_are_counted_not_mistaken_for_it(self):
        self.peer.say(event("remoteControl/status/changed"), event("thread/started"), self.good())
        self.client.initialize()
        self.assertEqual(self.client.notifications, 2)

    def test_server_requests_are_refused_without_granting_anything(self):
        self.peer.say({"id": 90, "method": "item/commandExecution/requestApproval",
                       "params": {"command": "rm -rf /"}}, self.good())
        self.client.initialize()
        refusals = [m for m in self.peer.heard() if m.get("id") == 90]
        self.assertEqual(refusals, [{"id": 90, "error": {"code": -32601, "message": "not supported"}}])

    def test_closed_pipe_is_a_distinct_unknown_outcome(self):
        self.peer.server_out.close()
        with self.assertRaises(AppServerClosed):
            self.client.initialize()

    def test_silence_past_the_deadline_is_a_distinct_timeout(self):
        peer = Peer(seconds=0.3)
        self.addCleanup(peer.close)
        started = time.monotonic()
        with self.assertRaises(AppServerTimeout):
            peer.client.initialize()
        self.assertLess(time.monotonic() - started, 3)

    def test_write_to_a_dead_child_is_a_closed_error(self):
        self.peer.server_in.close()
        with self.assertRaises(AppServerClosed):
            self.client.initialize()

    def test_oversized_line_is_refused(self):
        with patch.object(codex_app_server, "MAX_LINE_BYTES", 64):
            self.peer.say(b"x" * 200)
            with self.assertRaisesRegex(LLMToolError, "too large"):
                self.client.initialize()


class WriteDeadlineTests(unittest.TestCase):
    """Writes to a child that stopped reading are bound by the same monotonic deadline."""
    BIG = 1 << 20           # far beyond any pipe buffer (64 KiB on Darwin and Linux)

    def peer(self, seconds):
        peer = Peer(seconds=seconds)
        self.addCleanup(peer.close)
        return peer

    @staticmethod
    def drain(peer):
        data = bytearray()
        while True:
            try:
                chunk = os.read(peer.server_in.fileno(), 65536)
            except BlockingIOError:
                return bytes(data)
            if not chunk:
                return bytes(data)
            data.extend(chunk)

    def test_payload_larger_than_the_pipe_buffer_times_out_at_the_deadline(self):
        peer = self.peer(0.5)                           # nobody ever reads server_in
        started = time.monotonic()
        with self.assertRaises(AppServerTimeout):
            peer.client.request("big", {"blob": "x" * self.BIG})
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 0.4)
        self.assertLess(elapsed, 3)
        sent = self.drain(peer)
        self.assertGreater(len(sent), 0)                # part of the line did go out...
        self.assertLess(len(sent), self.BIG)            # ...but the write never completed
        self.assertFalse(sent.endswith(b"\n"))

    def test_a_partly_written_line_poisons_the_stream_so_nothing_else_is_sent(self):
        peer = self.peer(0.4)
        with self.assertRaises(AppServerTimeout):
            peer.client.request("big", {"blob": "x" * self.BIG})
        self.drain(peer)                                # free the pipe: it must still refuse
        with self.assertRaises(AppServerClosed):
            peer.client.notify("later")
        with self.assertRaises(AppServerClosed):
            peer.client.request("again", {})
        self.assertEqual(self.drain(peer), b"")

    def test_small_message_into_an_already_full_pipe_times_out_without_corruption(self):
        peer = self.peer(0.4)
        descriptor = peer.client_stdin.fileno()
        filled = 0
        while True:
            try:
                filled += os.write(descriptor, b"x" * 4096)
            except BlockingIOError:
                break
        self.assertGreater(filled, 0)
        started = time.monotonic()
        with self.assertRaises(AppServerTimeout):
            peer.client.notify("ping")
        self.assertLess(time.monotonic() - started, 3)
        received = self.drain(peer)
        self.assertEqual(received, b"x" * filled)       # not one byte of "ping" went out

    def test_slow_reader_still_receives_a_large_message_intact(self):
        peer = self.peer(10)
        received = bytearray()
        stop = threading.Event()

        def slow_reader():
            time.sleep(0.3)
            descriptor = peer.server_in.fileno()
            while not stop.is_set():
                if select.select([descriptor], [], [], 0.05)[0]:
                    try:
                        chunk = os.read(descriptor, 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        return
                    received.extend(chunk)

        reader = threading.Thread(target=slow_reader)
        reader.start()
        blob = "y" * self.BIG
        peer.say(response(1, ok=True))
        try:
            self.assertEqual(peer.client.request("big", {"blob": blob}), {"ok": True})
        finally:
            stop.set()
            reader.join(10)
        received.extend(self.drain(peer))
        self.assertEqual(json.loads(bytes(received).decode()),
                         {"id": 1, "method": "big", "params": {"blob": blob}})

    def test_owned_lifecycle_cancellation_is_checked_before_writing(self):
        peer = self.peer(5)
        def cancel():
            raise LLMToolError("owned lifecycle cancelled")
        peer.client._check = cancel
        with self.assertRaisesRegex(LLMToolError, "^owned lifecycle cancelled$"):
            peer.client.notify("should-not-send")
        self.assertEqual(peer.heard(), [])

    def test_deadline_already_passed_writes_nothing_and_leaves_the_stream_usable(self):
        peer = self.peer(0)
        with self.assertRaises(AppServerTimeout):
            peer.client.notify("late")
        self.assertEqual(peer.heard(), [])
        peer.client._deadline = time.monotonic() + 5    # a caller extending the deadline
        peer.client.notify("on-time")
        self.assertEqual(peer.heard(), [{"method": "on-time"}])


class RunTurnTests(ClientTestCase):
    def script(self, *tail, thread="t1", turn="u1"):
        self.peer.say(response(1, thread={"id": thread}), response(2, turn={"id": turn}), *tail)

    def test_text_comes_from_the_final_agent_message(self):
        self.script(event("item/agentMessage/delta", threadId="t1", delta="he"),
                    agent_done("hello"), turn_done())
        self.assertEqual(self.client.run_turn("say hi", "/iso/run"), "hello")
        sent = self.peer.heard()
        self.assertEqual(sent[0], {"id": 1, "method": "thread/start", "params": {
            "cwd": "/iso/run", "ephemeral": True, "approvalPolicy": "never",
            "sandbox": "read-only"}})
        self.assertEqual(sent[1], {"id": 2, "method": "turn/start", "params": {
            "threadId": "t1", "input": [{"type": "text", "text": "say hi"}]}})
        self.assertEqual(len(sent), 2)                  # exactly one thread and one turn

    def test_unknown_item_types_are_refused_before_any_success(self):
        for kind in ("futureTool", "futureMessage", None, 123, [], {}):
            for method in ("item/started", "item/completed"):
                with self.subTest(kind=kind, method=method):
                    peer = Peer()
                    self.addCleanup(peer.close)
                    peer.say(response(1, thread={"id": "t1"}), response(2, turn={"id": "u1"}),
                             event(method, item={"type": kind}), turn_done())
                    with self.assertRaisesRegex(LLMToolError, "item type unsupported"):
                        peer.client.run_turn("p", "/c")

    def test_unknown_notification_methods_are_refused(self):
        for method in ("item/futureTool/delta", "item/unknown", "future/notice"):
            with self.subTest(method=method):
                peer = Peer()
                self.addCleanup(peer.close)
                peer.say(response(1, thread={"id": "t1"}), response(2, turn={"id": "u1"}),
                         event(method), turn_done())
                with self.assertRaisesRegex(LLMToolError, "event type unsupported"):
                    peer.client.run_turn("p", "/c")

    def test_allowlisted_text_items_and_informational_events_remain_supported(self):
        self.script(event("item/started", item={"type": "userMessage"}),
                    event("item/completed", item={"type": "reasoning"}),
                    event("turn/started"), agent_done("answer"), turn_done())
        self.assertEqual(self.client.run_turn("p", "/c"), "answer")

    def test_reported_error_is_sanitized_and_never_success(self):
        self.script(event("error", message="PRIVATE_ERROR"), turn_done())
        with self.assertRaisesRegex(LLMToolError, "^app-server reported an error$"):
            self.client.run_turn("p", "/c")

    def test_events_that_arrive_before_the_turn_response_are_not_lost(self):
        self.peer.say(response(1, thread={"id": "t1"}), agent_done("early"), turn_done(),
                      response(2, turn={"id": "u1"}))
        self.assertEqual(self.client.run_turn("p", "/c"), "early")

    def test_deltas_are_joined_when_no_completed_message_arrives(self):
        self.script(event("item/agentMessage/delta", delta="he"),
                    event("item/agentMessage/delta", delta="llo"), turn_done())
        self.assertEqual(self.client.run_turn("p", "/c"), "hello")

    def test_commentary_never_beats_a_final_answer(self):
        self.script(agent_done("thinking", phase="commentary"), agent_done("answer"),
                    agent_done("more thinking", phase="commentary"), turn_done())
        self.assertEqual(self.client.run_turn("p", "/c"), "answer")

    def test_commentary_alone_is_used_when_nothing_else_exists(self):
        self.script(agent_done("only", phase="commentary"), turn_done())
        self.assertEqual(self.client.run_turn("p", "/c"), "only")

    def test_non_completed_turn_statuses_fail(self):
        for status in ("failed", "interrupted", "inProgress"):
            with self.subTest(status=status):
                peer = Peer()
                self.addCleanup(peer.close)
                peer.say(response(1, thread={"id": "t1"}), response(2, turn={"id": "u1"}),
                         turn_done(status))
                with self.assertRaisesRegex(LLMToolError, "turn " + status):
                    peer.client.run_turn("p", "/c")

    def test_unknown_turn_status_is_invalid(self):
        self.script(turn_done("weird"))
        with self.assertRaises(LLMToolError):
            self.client.run_turn("p", "/c")

    def test_any_tool_item_fails_closed_even_before_the_turn_finishes(self):
        for kind in ("commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall",
                     "collabAgentToolCall", "webSearch", "imageView", "imageGeneration"):
            for method in ("item/started", "item/completed"):
                with self.subTest(kind=kind, method=method):
                    peer = Peer()
                    self.addCleanup(peer.close)
                    peer.say(response(1, thread={"id": "t1"}), response(2, turn={"id": "u1"}),
                             event(method, threadId="t1", item={"type": kind, "id": "i"}),
                             agent_done("ignored"), turn_done())
                    with self.assertRaisesRegex(LLMToolError, "tool item"):
                        peer.client.run_turn("p", "/c")

    def test_server_approval_requests_during_the_turn_are_refused(self):
        self.script({"id": 77, "method": "item/fileChange/requestApproval", "params": {}},
                    agent_done("fine"), turn_done())
        self.assertEqual(self.client.run_turn("p", "/c"), "fine")
        self.assertIn({"id": 77, "error": {"code": -32601, "message": "not supported"}},
                      self.peer.heard())

    def test_events_for_other_threads_and_turns_are_ignored(self):
        self.script(agent_done("stranger", thread="other"), turn_done(thread="other"),
                    turn_done(turn="other-turn"), agent_done("mine"), turn_done())
        self.assertEqual(self.client.run_turn("p", "/c"), "mine")

    def test_event_backlog_is_bounded(self):
        with patch.object(codex_app_server, "MAX_EVENTS", 5):
            # Events can only pile up while a response is awaited (before turn/start answers).
            self.peer.say(response(1, thread={"id": "t1"}),
                          *[event("item/agentMessage/delta", delta="x") for _ in range(20)],
                          response(2, turn={"id": "u1"}), turn_done())
            with self.assertRaisesRegex(LLMToolError, "backlog"):
                self.client.run_turn("p", "/c")

    def test_output_is_bounded(self):
        with patch.object(codex_app_server, "MAX_OUTPUT_BYTES", 10):
            self.script(event("item/agentMessage/delta", delta="x" * 20), turn_done())
            with self.assertRaisesRegex(LLMToolError, "1 MiB"):
                self.client.run_turn("p", "/c")
        peer = Peer()
        self.addCleanup(peer.close)
        with patch.object(codex_app_server, "MAX_OUTPUT_BYTES", 10):
            peer.say(response(1, thread={"id": "t1"}), response(2, turn={"id": "u1"}),
                     agent_done("y" * 20), turn_done())
            with self.assertRaisesRegex(LLMToolError, "1 MiB"):
                peer.client.run_turn("p", "/c")

    def test_malformed_thread_and_turn_replies_are_invalid(self):
        for first, second in ((response(1), response(2, turn={"id": "u"})),
                              (response(1, thread={"id": 5}), response(2, turn={"id": "u"})),
                              (response(1, thread={"id": "t"}), response(2)),
                              (response(1, thread={"id": "t"}), response(2, turn={"id": 9}))):
            with self.subTest(first=first, second=second):
                peer = Peer()
                self.addCleanup(peer.close)
                peer.say(first, second)
                with self.assertRaises(LLMToolError):
                    peer.client.run_turn("p", "/c")

    def test_prompt_and_cwd_must_be_strings(self):
        for prompt, cwd in ((None, "/c"), ("p", None), (b"p", "/c")):
            with self.assertRaises(LLMToolError):
                self.client.run_turn(prompt, cwd)
        self.assertEqual(self.peer.heard(), [])

    def test_hang_before_completion_is_a_timeout_and_never_a_second_turn(self):
        peer = Peer(seconds=0.4)
        self.addCleanup(peer.close)
        peer.say(response(1, thread={"id": "t1"}), response(2, turn={"id": "u1"}))
        with self.assertRaises(AppServerTimeout):
            peer.client.run_turn("p", "/c")
        self.assertEqual([m["method"] for m in peer.heard()], ["thread/start", "turn/start"])

    def test_child_exit_mid_turn_is_a_closed_error(self):
        self.script(agent_done("partial"))
        self.peer.server_out.close()
        with self.assertRaises(AppServerClosed):
            self.client.run_turn("p", "/c")

    def test_stray_response_while_waiting_for_events_is_invalid(self):
        self.script(response(55))
        with self.assertRaises(LLMToolError):
            self.client.run_turn("p", "/c")


if __name__ == "__main__":
    unittest.main()
