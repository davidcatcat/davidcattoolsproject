"""Local fake-server coverage for the bounded company gw deployment probe."""

import contextlib
import http.server
import importlib.util
import io
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

PROBE_PATH = Path(__file__).parents[1] / "eval" / "gw_probe" / "probe.py"
spec = importlib.util.spec_from_file_location("gw_probe", PROBE_PATH)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class FakeHandler(http.server.BaseHTTPRequestHandler):
    requests = []
    fail_models = False
    fail_large_context = False

    def log_message(self, _format, *args):
        return

    def do_GET(self):
        type(self).requests.append(("GET", self.path, None))
        if type(self).fail_models:
            self.send_error(503)
            return
        self._send({"data": [{"id": "fake-model", "max_model_len": 65536}]})

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(size))
        type(self).requests.append(("POST", self.path, body))
        content = body.get("messages", [{}])[-1].get("content", "")
        if type(self).fail_large_context and len(content) > 20000:
            self.send_error(413)
            return
        if body.get("tools"):
            message = {"content": None, "tool_calls": [{"id": "c1", "type": "function",
                        "function": {"name": "record_answer", "arguments": "{\"answer\":\"ok\"}"}}]}
        else:
            answer = "SYNTHETIC_CONTEXT_NEEDLE_7f3a" if "exact needle" in content else "4"
            message = {"content": answer, "reasoning_content": "reasoning sample"}
        limit = body.get("max_tokens")
        finish = "length" if limit in (4096, 16384, 32768) else "stop"
        completion = min(limit or 3, 12)
        self._send({"model": "fake-model", "choices": [{"message": message, "finish_reason": finish}],
                    "usage": {"prompt_tokens": max(1, len(content) // 4),
                              "completion_tokens": completion,
                              "total_tokens": max(1, len(content) // 4) + completion}})

    def _send(self, payload):
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class FakeServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = "http://127.0.0.1:" + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        FakeHandler.requests = []
        FakeHandler.fail_models = False
        FakeHandler.fail_large_context = False
        self.client = probe.ProbeClient(self.base_url, "fake-model", "sample-value")

    def test_each_probe_parses_fake_responses(self):
        results = {name: function(self.client) for name, function in probe.PROBE_FUNCTIONS.items()}
        self.assertEqual(results["models"]["models"][0]["max_model_len"], 65536)
        self.assertEqual(len(results["thinking"]["runs"]), 6)
        self.assertTrue(results["tools"]["tool_calls_returned"])
        self.assertEqual(results["determinism"]["unique_answers"], 1)
        self.assertTrue(results["context"]["runs"][0]["needle_found"])
        self.assertEqual(len(results["output"]["runs"]), 3)
        self.assertEqual(results["concurrency"]["runs"][-1]["ok_count"], 32)
        self.assertEqual(results["quality"]["off"]["total"], 20)
        self.assertTrue(all(run["status"] == 200 for run in results["thinking"]["runs"]))

    def test_http_error_is_recorded_and_later_probe_runs(self):
        FakeHandler.fail_models = True
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
                probe, "_config", return_value=(self.base_url, "fake-model", "sample-value")):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = probe.main(["--only", "models", "--only", "thinking", "--base-url", self.base_url,
                                   "--model", "fake-model", "--out-dir", directory])
            self.assertEqual(code, 0)
            saved = json.loads((Path(directory) / "gw_probe.json").read_text())
            self.assertEqual(saved["probes"]["models"]["error_code"], "HTTP_503")
            self.assertEqual(len(saved["probes"]["thinking"]["runs"]), 6)
            self.assertNotIn("sample-value", stdout.getvalue())
            self.assertNotIn("sample-value", (Path(directory) / "gw_probe.json").read_text())
            self.assertNotIn("sample-value", (Path(directory) / "gw_probe.md").read_text())

    def test_context_stops_after_first_error(self):
        FakeHandler.fail_large_context = True
        result = probe.probe_context(self.client)
        self.assertEqual(len(result["runs"]), 1)
        self.assertEqual(result["runs"][0]["error_code"], "HTTP_413")
        self.assertEqual(len([r for r in FakeHandler.requests if r[0] == "POST"]), 1)

    def test_dry_run_uses_canned_response_without_network(self):
        self.client = probe.ProbeClient("http://127.0.0.1:1", "fake-model", None, dry_run=True)
        self.assertEqual(probe.probe_models(self.client)["models"][0]["id"], "fake-model")
        self.assertEqual(FakeHandler.requests, [])


if __name__ == "__main__":
    unittest.main()
