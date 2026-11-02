"""Loopback-only fake OpenAI-compatible server tests for S1-S6."""

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import multiturn
from multiturn import (EXPECTED, TOOL_NAME, Audit, GuardedRun, StopRun, _run,
                       _tool_result, _tool_result_matches)
from probe import ProbeClient


MODEL = "local-fake-model"


class FakeHandler(BaseHTTPRequestHandler):
    status_override = None
    model_override = None
    drop_count = 0
    response_marker = None

    def log_message(self, format_string, *args):
        return

    def do_POST(self):
        if type(self).drop_count:
            type(self).drop_count -= 1
            self.close_connection = True
            return
        if type(self).status_override is not None:
            self.send_response(type(self).status_override)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        size = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(size))
        messages = body.get("messages", [])
        last = messages[-1] if messages else {}
        if last.get("role") == "user" and str(last.get("content", "")).startswith("Tool result:"):
            self._json({"model": MODEL, "choices": [{"message": {"content": EXPECTED}}],
                        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}})
            return
        if last.get("role") == "tool":
            payload = {"model": MODEL, "choices": [{"message": {"content": EXPECTED}}],
                       "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}}
            self._json(payload)
            return
        if not body.get("tools"):
            content = "TOOL_CALL: " + TOOL_NAME + " " + json.dumps({"summary": EXPECTED})
            self._json({"model": MODEL, "choices": [{"message": {"content": content}}],
                        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}})
            return
        args = json.dumps({"summary": EXPECTED}, separators=(",", ":"))
        call_id = "call_local_1"
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = [
                {"model": MODEL, "choices": [{"delta": {"tool_calls": [
                    {"index": 0, "id": call_id, "type": "function", "function": {"name": TOOL_NAME,
                     "arguments": args}}]}}]},
                {"model": MODEL, "choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            ]
            for chunk in chunks:
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            return
        self._json({"model": MODEL, "choices": [{"message": {"content": None, "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": TOOL_NAME,
             "arguments": args}}]}}], "usage": {"prompt_tokens": 12, "completion_tokens": 8,
                                                      "total_tokens": 20}})

    def _json(self, value):
        if type(self).model_override is not None:
            value["model"] = type(self).model_override
        if type(self).response_marker is not None:
            value["marker"] = type(self).response_marker
        encoded = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class MultiTurnFakeServerTests(unittest.TestCase):
    def setUp(self):
        FakeHandler.status_override = None
        FakeHandler.model_override = None
        FakeHandler.drop_count = 0
        FakeHandler.response_marker = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        FakeHandler.status_override = None
        FakeHandler.model_override = None
        FakeHandler.drop_count = 0
        FakeHandler.response_marker = None

    def test_scenarios_and_audit_with_loopback_server(self):
        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "audit.jsonl"
            client = ProbeClient("http://127.0.0.1:" + str(self.server.server_port), MODEL, None)
            result = _run(client, audit_path, MODEL)
            self.assertEqual([item["id"] for item in result["scenarios"]],
                             ["S1", "S2", "S3", "S4", "S5", "S6"])
            self.assertTrue(all(item["passed"] for item in result["scenarios"]))
            self.assertTrue(result["audit_verified"])
            self.assertEqual(result["caps"]["calls"], None)
            self.assertEqual(result["caps"]["total_tokens"], None)
            self.assertTrue(Audit(audit_path).verify())
            audit = audit_path.read_text(encoding="utf-8")
            self.assertNotIn("Authorization", audit)
            self.assertNotIn("127.0.0.1", audit)

    def test_sentinel_config_values_are_absent_from_audit_and_report(self):
        with tempfile.TemporaryDirectory() as directory:
            endpoint = "http://127.0.0.1:" + str(self.server.server_port)
            key = "FAKE_VALUE_KEY"
            header_value = "Bea" + "rer " + key
            client = ProbeClient(endpoint, MODEL, key)
            result = _run(client, Path(directory) / "audit.jsonl", MODEL)
            audit = (Path(directory) / "audit.jsonl").read_text(encoding="utf-8")
            report = json.dumps(result)
            for value in (key, header_value, endpoint):
                self.assertNotIn(value, audit)
                self.assertNotIn(value, report)

    def test_http_401_and_403_stop_with_their_own_codes(self):
        for status in (401, 403):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                FakeHandler.status_override = status
                client = ProbeClient("http://127.0.0.1:" + str(self.server.server_port), MODEL, None)
                result = _run(client, Path(directory) / "audit.jsonl", MODEL)
                self.assertEqual(result["stop_reason"], "AUTH_HTTP_" + str(status))

    def test_two_consecutive_connection_errors_stop(self):
        FakeHandler.drop_count = 2
        with tempfile.TemporaryDirectory() as directory:
            client = ProbeClient("http://127.0.0.1:" + str(self.server.server_port), MODEL, None)
            result = _run(client, Path(directory) / "audit.jsonl", MODEL)
            self.assertEqual(result["stop_reason"], "CONNECTION_ERRORS")

    def test_unexpected_served_model_stops(self):
        FakeHandler.model_override = "different-served-model"
        with tempfile.TemporaryDirectory() as directory:
            client = ProbeClient("http://127.0.0.1:" + str(self.server.server_port), MODEL, None)
            result = _run(client, Path(directory) / "audit.jsonl", MODEL)
            self.assertEqual(result["stop_reason"], "UNEXPECTED_MODEL")

    def test_response_credential_scan_stops_without_recording_value(self):
        with tempfile.TemporaryDirectory() as directory:
            key = "FAKE_VALUE_KEY"
            FakeHandler.response_marker = key
            audit_path = Path(directory) / "audit.jsonl"
            client = ProbeClient("http://127.0.0.1:" + str(self.server.server_port), MODEL, key)
            result = _run(client, audit_path, MODEL)
            self.assertEqual(result["stop_reason"], "CREDENTIAL_SCAN_HIT")
            self.assertNotIn(key, audit_path.read_text(encoding="utf-8"))
            self.assertNotIn(key, json.dumps(result))

    def test_request_credential_scan_uses_runtime_composed_value(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = Audit(Path(directory) / "audit.jsonl")
            client = ProbeClient("http://127.0.0.1:" + str(self.server.server_port), MODEL, None)
            run = GuardedRun(client, audit, MODEL, __import__("time").monotonic())
            run.current = "S1"
            value = "sk" + "-" + "harmlessplaceholder"
            with self.assertRaises(StopRun) as caught:
                run.send({"content": value})
            self.assertEqual(str(caught.exception), "CREDENTIAL_SCAN_HIT")

    def test_tool_result_id_must_match_a_returned_call(self):
        call = {"id": "call_local_1", "type": "function",
                "function": {"name": TOOL_NAME, "arguments": json.dumps({"summary": EXPECTED})}}
        result = _tool_result(call)
        self.assertTrue(_tool_result_matches(call, result))
        self.assertFalse(_tool_result_matches(call, {"role": "tool", "tool_call_id": ""}))
        self.assertFalse(_tool_result_matches(call, {"role": "tool", "tool_call_id": "other"}))

    def test_live_gate_requires_flag_and_authorization_reference(self):
        with self.assertRaises(SystemExit):
            multiturn.main(["--live"])
        with patch.object(multiturn, "_config", return_value=("https://gateway.invalid", "gw", None)):
            with self.assertRaises(SystemExit):
                multiturn.main(["--owner-authorization-url",
                                "https://github.com/example/project/issues/1#issuecomment-2"])

    def test_optional_call_limit_stops_and_scores_active_scenario(self):
        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "audit.jsonl"
            client = ProbeClient("http://127.0.0.1:" + str(self.server.server_port), MODEL, None)
            result = _run(client, audit_path, MODEL, max_calls=1)
            self.assertEqual(result["status"], "STOPPED")
            self.assertEqual(result["stop_reason"], "CALL_CAP")
            self.assertEqual(result["calls"], 1)
            self.assertEqual(result["scenarios"][0]["id"], "S1")
            self.assertEqual(result["scenarios"][0]["detail"], "STOPPED_CALL_CAP")
            self.assertTrue(result["audit_verified"])


if __name__ == "__main__":
    unittest.main()
