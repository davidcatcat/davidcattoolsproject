"""Loopback-only fake OpenAI-compatible server tests for S1-S6."""

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from multiturn import EXPECTED, TOOL_NAME, Audit, _run
from probe import ProbeClient


MODEL = "local-fake-model"


class FakeHandler(BaseHTTPRequestHandler):
    def log_message(self, format_string, *args):
        return

    def do_POST(self):
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
        encoded = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class MultiTurnFakeServerTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

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
