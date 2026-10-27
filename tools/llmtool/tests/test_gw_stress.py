import contextlib
import http.server
import importlib.util
import io
import json
import threading
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
STRESS_PATH = ROOT / "eval" / "gw_probe" / "stress.py"
SPEC = importlib.util.spec_from_file_location("gw_stress", STRESS_PATH)
stress = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stress)


class FakeHandler(http.server.BaseHTTPRequestHandler):
    requests = []
    lock = threading.Lock()
    return_429 = False

    def log_message(self, _format, *args):
        return

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(size))
        with type(self).lock:
            type(self).requests.append(body)
        if type(self).return_429:
            self.send_response(429)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        prompt = body.get("messages", [{}])[-1].get("content", "")
        if "secret marker" in prompt:
            answer = "SYNTHETIC_BUDGET_NEEDLE_C84F2"
        elif "library receives" in prompt:
            answer = "126"
        else:
            answer = "42"
        limit = body.get("max_tokens")
        message = {"content": "" if limit in (256, 512) else answer,
                   "reasoning_content": "synthetic reasoning" if limit in (256, 512) else "brief"}
        finish = "length" if limit in (256, 512) else "stop"
        payload = {"choices": [{"message": message, "finish_reason": finish}],
                   "usage": {"prompt_tokens": max(1, len(prompt) // 4),
                             "completion_tokens": min(limit or 1, 8),
                             "total_tokens": max(1, len(prompt) // 4) + min(limit or 1, 8)}}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class FakeThreadingHTTPServer(http.server.ThreadingHTTPServer):
    request_queue_size = 256
    daemon_threads = True


class StressFakeServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = FakeThreadingHTTPServer(("127.0.0.1", 0), FakeHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = "http://127.0.0.1:" + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        with FakeHandler.lock:
            FakeHandler.requests = []
        FakeHandler.return_429 = False

    def _run(self, args, key="sample-secret"):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
                stress, "_config", return_value=(self.base_url, "fake-model", key)):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = stress.main(["--base-url", self.base_url, "--model", "fake-model",
                                    "--out-dir", directory] + args)
            json_text = (Path(directory) / "gw_stress.json").read_text()
            markdown_text = (Path(directory) / "gw_stress.md").read_text()
            return code, stdout.getvalue(), json.loads(json_text), json_text + markdown_text

    def test_burst_default_thinking_and_429_are_recorded_separately(self):
        code, _stdout, output, _files = self._run(["--only", "burst", "--burst-levels", "1,4"])
        self.assertEqual(code, 0)
        self.assertEqual([run["parallelism"] for run in output["modes"]["burst"]["runs"]], [1, 4])
        self.assertTrue(all(body["chat_template_kwargs"]["enable_thinking"] is True
                            for body in FakeHandler.requests))

        self.setUp()
        FakeHandler.return_429 = True
        code, _stdout, output, _files = self._run(["--only", "burst", "--burst-levels", "1"])
        run = output["modes"]["burst"]["runs"][0]
        self.assertEqual(code, 0)
        self.assertEqual(run["errors"], {"HTTP_429": 1})

    def test_sustained_runs_to_deadline_and_records_windows(self):
        code, _stdout, output, _files = self._run(["--only", "sustained", "--concurrency", "4",
                                                   "--duration", "2", "--window", "1"])
        result = output["modes"]["sustained"]
        self.assertEqual(code, 0)
        self.assertGreaterEqual(result["duration_seconds"], 2)
        self.assertTrue(result["requests"])
        self.assertTrue(result["windows"])
        self.assertIn("start_offset_seconds", result["requests"][0])

    def test_sustained_windows_follow_requested_duration(self):
        code, _stdout, output, _files = self._run(["--only", "sustained", "--concurrency", "4",
                                                   "--duration", "2", "--window", "1"])
        result = output["modes"]["sustained"]
        self.assertEqual(code, 0)
        self.assertEqual(len(result["windows"]), 2)
        self.assertEqual(output["thinking_applies_to"], ["burst", "sustained"])

    def test_budget_collects_reasoning_and_needle_results(self):
        code, _stdout, output, _files = self._run(["--only", "budget", "--thinking", "off"])
        result = output["modes"]["budget"]
        self.assertEqual(code, 0)
        self.assertEqual(len(result["arithmetic"]), 6)
        self.assertEqual(len(result["needles"]), 6)
        self.assertTrue(all(body["chat_template_kwargs"]["enable_thinking"] is True
                            for body in FakeHandler.requests))
        self.assertGreater(result["length_with_empty_content_count"], 0)

    def test_quality_has_fifty_unique_questions_and_both_thinking_modes(self):
        code, _stdout, output, _files = self._run(["--only", "quality", "--quality-parallel", "8"])
        quality = output["modes"]["quality"]
        self.assertEqual(code, 0)
        self.assertEqual(quality["off"]["total"], 50)
        self.assertEqual(quality["on"]["total"], 50)
        self.assertEqual(len({run["question"] for run in quality["off"]["runs"]}), 50)
        self.assertEqual({body["chat_template_kwargs"]["enable_thinking"] for body in FakeHandler.requests},
                         {False, True})

    def test_credentials_are_redacted_and_dry_run_does_not_call_server(self):
        code, stdout, _output, files = self._run(["--only", "burst", "--burst-levels", "1"])
        self.assertEqual(code, 0)
        self.assertNotIn("sample-secret", stdout + files)
        self.setUp()
        with tempfile.TemporaryDirectory() as directory:
            code = stress.main(["--dry-run", "--only", "burst", "--burst-levels", "1",
                                "--out-dir", directory])
        self.assertEqual(code, 0)
        self.assertEqual(FakeHandler.requests, [])


if __name__ == "__main__":
    unittest.main()
