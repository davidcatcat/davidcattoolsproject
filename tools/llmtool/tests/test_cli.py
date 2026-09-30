import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from test_provider import success
from llmtool.context import format_context_packet, parse_context_packet

ENTRYPOINT = Path(__file__).resolve().parents[1] / "llmtool.py"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.requests.append((self.path, dict(self.headers), json.loads(body)))
        status, payload, delay, headers = self.server.reply
        if delay:
            time.sleep(delay)
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass


class CLITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = "http://127.0.0.1:" + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cwd = Path(self.temp.name)
        self.home = self.cwd / "home"
        self.home.mkdir()
        # Do not inherit user config, provider overrides, credentials or proxies.
        self.env = {
            "HOME": str(self.home), "PATH": os.environ.get("PATH", ""),
            "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
            "LLMTOOL_BASE_URL": self.base_url,
            "LLMTOOL_MODEL": "test-model",
        }
        self.server.requests = []
        self.server.reply = (200, success(), 0, {})

    def run_cli(self, *args, data=b""):
        return subprocess.run(
            [sys.executable, str(ENTRYPOINT), *args], input=data,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=self.cwd, env=self.env, timeout=10, check=False,
        )

    def assert_failure(self, result):
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertTrue(result.stderr.startswith(b"llmtool:"), result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)

    def test_help_and_version_without_config(self):
        for args in (("--help",), ("ask", "--help"), ("config", "--help"),
                     ("configure", "--help"), ("--version",)):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(b"llmtool", result.stdout)
        self.assertEqual(self.server.requests, [])

    def test_configure_noninteractive_fails_without_touching_real_home_or_network(self):
        result = self.run_cli("configure")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"input ended", result.stderr)
        self.assertNotIn(b"API key", result.stdout + result.stderr)
        self.assertFalse((self.home / ".config/llmtool/config.yaml").exists())
        self.assertEqual(self.server.requests, [])

    def test_providers_without_config_or_auth_and_ignores_hostile_config(self):
        self.env.pop("LLMTOOL_BASE_URL")
        self.env.pop("LLMTOOL_MODEL")
        user = self.home / ".config/llmtool/config.yaml"
        for hostile in (False, True):
            if hostile:
                user.parent.mkdir(parents=True)
                user.write_text("providers: {default: {api_key_env: PRIVATE_TEST_KEY}}")
                (self.cwd / ".llmtool.yaml").write_text("invalid: [SECRET_PROJECT")
                self.env["PRIVATE_TEST_KEY"] = "SECRET_CREDENTIAL"
            before = user.read_bytes() if hostile else None
            for args in (("providers",), ("providers", "--json")):
                result = self.run_cli(*args, data=b"SECRET_INPUT")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, b"")
                self.assertIn(b"openai-compatible", result.stdout)
                self.assertNotIn(b"SECRET", result.stdout)
                self.assertNotIn(b"PRIVATE_TEST_KEY", result.stdout)
                if "--json" in args:
                    self.assertEqual(list(json.loads(result.stdout)),
                                     ["claude-cli", "codex-cli", "openai", "openai-compatible"])
                self.assertEqual(self.server.requests, [])
                self.assertEqual(user.read_bytes() if user.exists() else None, before)

    def test_direct_argument(self):
        result = self.run_cli("ask", "why did this fail?")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"final answer\n")
        self.assertEqual(result.stderr, b"")
        self.assertEqual(self.server.requests[0][2]["messages"][1]["content"], "why did this fail?")

    def test_stdin_all_commands_and_unicode(self):
        for command in ("ask", "analyze-log", "review-diff", "context-pack"):
            with self.subTest(command=command):
                packet_text = json.dumps({name: [] for name in
                                          ("facts", "evidence", "inferences", "hypotheses", "unknowns", "verify_next")})
                self.server.reply = (200, success(packet_text if command == "context-pack" else "final answer"), 0, {})
                result = self.run_cli(command, data="測試 evidence\n".encode())
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = format_context_packet(parse_context_packet(packet_text)) if command == "context-pack" else "final answer"
                self.assertEqual(result.stdout.decode(), expected + "\n")
                path, headers, payload = self.server.requests[-1]
                self.assertEqual(path, "/v1/chat/completions")
                self.assertEqual(payload["messages"][1]["content"], "測試 evidence\n")
                self.assertNotIn("chat_template_kwargs", payload)
                self.assertEqual(headers["Content-Type"], "application/json")

    def test_file(self):
        path = self.cwd / "app.log"
        path.write_text("ERROR evidence\n")
        result = self.run_cli("analyze-log", "--file", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.server.requests[0][2]["messages"][1]["content"], "ERROR evidence\n")

    def test_ambiguous_inputs_never_send_request(self):
        path = self.cwd / "app.log"
        path.write_text("private log")
        for args, stdin in (
            (("ask", "private-text", "--file", str(path)), b""),
            (("ask", "private-text"), b"private-pipe"),
            (("analyze-log", "--file", str(path)), b"private-pipe"),
        ):
            with self.subTest(args=args):
                result = self.run_cli(*args, data=stdin)
                self.assert_failure(result)
                self.assertIn(b"only one", result.stderr)
                self.assertNotIn(b"private", result.stderr)
        self.assertEqual(self.server.requests, [])

    def test_binary_and_empty_input(self):
        for data in (b"", b" \n\t", b"text\x00binary", b"\xff\xfe", b"\x01"):
            with self.subTest(data=data):
                self.assert_failure(self.run_cli("ask", data=data))
        path = self.cwd / "binary"
        path.write_bytes(b"\x00data")
        self.assert_failure(self.run_cli("ask", "--file", str(path)))
        self.assertEqual(self.server.requests, [])

    def test_missing_file_directory_and_fifo(self):
        fifo = self.cwd / "fifo"
        os.mkfifo(fifo)
        for path in (self.cwd / "secret-filename", self.cwd, fifo):
            with self.subTest(path=path):
                result = self.run_cli("ask", "--file", str(path))
                self.assert_failure(result)
                self.assertNotIn(b"secret-filename", result.stderr)
        self.assertEqual(self.server.requests, [])

    def test_verbose_stdout_stderr_separation(self):
        result = self.run_cli("ask", "evidence", "--verbose")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"final answer\n")
        for text in (b"model=qwen3.6-35b-a3b", b"prompt_tokens=20", b"completion_tokens=5", b"elapsed_seconds="):
            self.assertIn(text, result.stderr)
            self.assertNotIn(text, result.stdout)
        self.assertNotIn(b"private-reasoning", result.stdout + result.stderr)

    def test_provider_failures_never_emit_partial_content(self):
        truncated = success("partial-private-content")
        truncated["choices"][0]["finish_reason"] = "length"
        for status, body in (
            (500, {"error": "private-input-and-key"}),
            (200, b"private-malformed-json"), (200, {}),
            (200, success("")), (200, truncated),
            (200, {"error": {"message": "private-input-and-key"}}),
            (204, b""),
        ):
            with self.subTest(status=status, body=body):
                self.server.reply = (status, body, 0, {})
                result = self.run_cli("ask", "private-input", "--verbose")
                self.assert_failure(result)
                self.assertNotIn(b"private", result.stderr)

    def test_socket_timeout(self):
        self.server.reply = (200, success(), 0.2, {})
        result = self.run_cli("ask", "test", "--timeout", "0.03")
        self.assert_failure(result)
        self.assertIn(b"timed out", result.stderr)

    def test_redirect_does_not_forward_prompt_or_key(self):
        self.server.reply = (307, b"private-redirect", 0, {"Location": self.base_url + "/other"})
        result = self.run_cli("ask", "test")
        self.assert_failure(result)
        self.assertIn(b"307", result.stderr)
        self.assertEqual(len(self.server.requests), 1)

    def test_no_execution_or_automatic_file_read(self):
        sentinel = self.cwd / "must-not-exist"
        output = f"touch {sentinel}; git reset --hard; cat ~/.ssh/id_rsa"
        self.server.reply = (200, success(output), 0, {})
        result = self.run_cli("review-diff", data=b"diff evidence only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.decode().strip(), output)
        self.assertFalse(sentinel.exists())
        self.assertEqual(self.server.requests[0][2]["messages"][1]["content"], "diff evidence only")

    def test_config_commands_use_temporary_home_and_no_network(self):
        user = self.home / ".config/llmtool/config.yaml"
        result = self.run_cli("config", "path")
        self.assertEqual(result.stdout.decode().strip(), str(user))
        self.assertEqual(result.returncode, 0)
        result = self.run_cli("config", "init")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(user.stat().st_mode & 0o777, 0o600)
        created = yaml.safe_load(user.read_text())
        self.assertIsNone(created["providers"]["default"]["base_url"])
        self.assertIsNone(created["providers"]["default"]["model"])
        self.assertNotIn("10.29.7.58", user.read_text())
        self.assertNotIn("gw", user.read_text())
        user.write_text("generation: {max_tokens: 42}")
        self.assert_failure(self.run_cli("config", "init"))
        self.assertIn("42", user.read_text())
        self.assertEqual(self.run_cli("config", "init", "--force").returncode, 0)
        shown = self.run_cli("config", "show", "--max-tokens", "17")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(yaml.safe_load(shown.stdout)["generation"]["max_tokens"], 17)
        self.assertEqual(self.server.requests, [])

    def test_config_show_redacts_key_and_auth_is_sent_only_when_configured(self):
        user = self.home / ".config/llmtool/config.yaml"
        user.parent.mkdir(parents=True)
        user.write_text("providers:\n  default:\n    api_key_env: TEST_KEY\n")
        self.env["TEST_KEY"] = "fake-private-key"
        result = self.run_cli("config", "show")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(b"fake-private-key", result.stdout + result.stderr)
        self.assertIn(b"REDACTED", result.stdout)
        result = self.run_cli("ask", "test")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.server.requests[0][1]["Authorization"], "Bearer fake-private-key")

    def test_cli_overrides_reach_wire(self):
        self.env["LLMTOOL_MODEL"] = "env-route"
        result = self.run_cli("ask", "test", "--model", "cli-route", "--max-tokens", "37", "--temperature", "0.2")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.server.requests[0][2]
        self.assertEqual(payload["model"], "cli-route")
        self.assertEqual(payload["max_tokens"], 37)
        self.assertEqual(payload["temperature"], 0.2)

    def test_invalid_arguments_and_yaml_do_not_echo_sensitive_input(self):
        result = self.run_cli("ask", "--unknown-secret-flag", "sensitive-input")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(b"sensitive", result.stderr)
        self.assertNotIn(b"secret", result.stderr)
        (self.cwd / ".llmtool.yaml").write_text("generation: [private-secret")
        result = self.run_cli("ask", "test")
        self.assert_failure(result)
        self.assertNotIn(b"private-secret", result.stderr)

    def test_input_limit(self):
        result = self.run_cli("context-pack", data=b"a" * (16 * 1024 * 1024 + 1))
        self.assert_failure(result)
        self.assertIn(b"16 MiB", result.stderr)
        self.assertEqual(self.server.requests, [])

    def test_ambient_proxy_is_ignored(self):
        self.env.update({"http_proxy": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1", "no_proxy": ""})
        result = self.run_cli("ask", "test")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_malicious_project_fails_before_any_http_or_secret_output(self):
        self.env.pop("LLMTOOL_BASE_URL")
        user = self.home / ".config/llmtool/config.yaml"
        user.parent.mkdir(parents=True)
        user.write_text(yaml.safe_dump({"providers": {"default": {
            "base_url": "http://127.0.0.1:1", "api_key": "FAKE_TEST_SECRET",
        }}}))
        self.env["AWS_SECRET_ACCESS_KEY"] = "FAKE_ENV_SECRET"
        project = self.cwd / ".llmtool.yaml"
        for fragment in (
            {"providers": {"default": {"base_url": self.base_url}}},
            {"providers": {"default": {"api_key": "FAKE_PROJECT_SECRET"}}},
            {"providers": {"default": {"api_key_env": "AWS_SECRET_ACCESS_KEY"}}},
            {"providers": {"default": {"type": "openai-compatible"}}},
            {"active_provider": "default"},
        ):
            with self.subTest(fragment=fragment):
                project.write_text(yaml.safe_dump(fragment))
                result = self.run_cli("ask", "private-prompt", "--verbose")
                self.assert_failure(result)
                self.assertIn(b"project", result.stderr)
                for secret in (b"FAKE_TEST_SECRET", b"FAKE_ENV_SECRET", b"FAKE_PROJECT_SECRET",
                               b"AWS_SECRET_ACCESS_KEY", b"private-prompt", self.base_url.encode()):
                    self.assertNotIn(secret, result.stdout + result.stderr)
                self.assertEqual(self.server.requests, [])

    def test_safe_project_overrides_still_reach_selected_endpoint(self):
        self.env.pop("LLMTOOL_MODEL")
        (self.cwd / ".llmtool.yaml").write_text("""providers:
  default:
    model: project-model
    timeout_seconds: 3
generation:
  max_tokens: 39
  temperature: 0.4
""")
        result = self.run_cli("ask", "test")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.server.requests[0][2]
        self.assertEqual(payload["model"], "project-model")
        self.assertEqual(payload["max_tokens"], 39)
        self.assertEqual(payload["temperature"], 0.4)

    def test_named_profile_cli_selection_and_optional_thinking_payload(self):
        self.env.pop("LLMTOOL_BASE_URL")
        self.env.pop("LLMTOOL_MODEL")
        user = self.home / ".config/llmtool/config.yaml"
        user.parent.mkdir(parents=True)
        user.write_text(yaml.safe_dump({"providers": {
            "default": {"base_url": "http://127.0.0.1:1", "api_key": "fake-other-key"},
            "local": {"base_url": self.base_url, "model": "local-model", "api_key_env": "LOCAL_TEST_KEY"},
        }}))
        self.env["LOCAL_TEST_KEY"] = "fake-selected-key"
        self.env["LLMTOOL_PROVIDER"] = "default"
        result = self.run_cli("ask", "test", "--provider", "local")
        self.assertEqual(result.returncode, 0, result.stderr)
        path, headers, payload = self.server.requests[0]
        self.assertEqual(headers["Authorization"], "Bearer fake-selected-key")
        self.assertEqual(payload["model"], "local-model")
        self.assertNotIn("chat_template_kwargs", payload)
        shown = self.run_cli("config", "show", "--provider", "local")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(yaml.safe_load(shown.stdout)["active_provider"], "local")
        self.assertNotIn(b"fake-other-key", shown.stdout)
        self.assertNotIn(b"fake-selected-key", shown.stdout)

    def test_no_user_config_ask_fails_and_show_remains_available(self):
        self.env.pop("LLMTOOL_BASE_URL")
        self.env.pop("LLMTOOL_MODEL")
        result = self.run_cli("ask", "test")
        self.assert_failure(result)
        self.assertEqual(result.stderr, b"llmtool: provider base_url is not configured\n")
        shown = self.run_cli("config", "show")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        provider = yaml.safe_load(shown.stdout)["providers"]["default"]
        self.assertIsNone(provider["base_url"])
        self.assertIsNone(provider["model"])
        self.assertEqual(self.server.requests, [])

    def test_missing_selected_field_fails_without_leaking_key(self):
        self.env.pop("LLMTOOL_BASE_URL")
        self.env.pop("LLMTOOL_MODEL")
        user = self.home / ".config/llmtool/config.yaml"
        user.parent.mkdir(parents=True)
        for fields, missing in (({"model": "test"}, "base_url"),
                                ({"base_url": self.base_url}, "model")):
            with self.subTest(fields=fields):
                user.write_text(yaml.safe_dump({"providers": {"default": {
                    **fields, "api_key": "FAKE_PRIVATE_KEY",
                }}}))
                result = self.run_cli("ask", "private-input", "--verbose")
                self.assert_failure(result)
                self.assertEqual(result.stderr.decode(), "llmtool: provider " + missing + " is not configured\n")
                self.assertEqual(self.server.requests, [])

    def test_explicit_user_config_thinking_false_still_sent(self):
        self.env.pop("LLMTOOL_BASE_URL")
        self.env.pop("LLMTOOL_MODEL")
        user = self.home / ".config/llmtool/config.yaml"
        user.parent.mkdir(parents=True)
        user.write_text(yaml.safe_dump({"providers": {"default": {
            "base_url": self.base_url, "model": "explicit-model",
            "provider_options": {"enable_thinking": False},
        }}}))
        result = self.run_cli("ask", "test")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.server.requests[0][2]
        self.assertEqual(payload["model"], "explicit-model")
        self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": False})


if __name__ == "__main__":
    unittest.main()
