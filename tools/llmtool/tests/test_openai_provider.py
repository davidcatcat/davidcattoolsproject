import io
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import yaml

from llmtool.cli import main
from llmtool.errors import LLMToolError
from llmtool.providers import PROVIDER_REGISTRY, get_provider_capabilities
from llmtool.providers.openai import API_ORIGIN, OpenAIProvider
from llmtool.providers.openai_compatible import OpenAICompatibleClient


FAKE_KEY = "sk-test-example"


def response_object(output=None, **fields):
    value = {"status": "completed"}
    if output is not None:
        value["output"] = output
    value.update(fields)
    return value


def message_item(*blocks):
    return {"type": "message", "role": "assistant", "status": "completed",
            "content": list(blocks)}


def text_block(value):
    return {"type": "output_text", "text": value, "annotations": []}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        raw_request = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        try:
            body = json.loads(raw_request)
        except (ValueError, UnicodeError):
            body = None
        self.server.requests.append({
            "path": self.path,
            "method": self.command,
            "headers": dict(self.headers),
            "body": body,
        })
        if self.server.delay:
            time.sleep(self.server.delay)
        status, payload, headers = self.server.reply
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Length", str(len(raw)))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        try:
            self.wfile.write(raw)
        except OSError:
            pass


class GuardedEnvironment(dict):
    def get(self, name, default=None):
        if name == "OPENAI_API_KEY":
            raise AssertionError("OPENAI_API_KEY must not be implicitly read")
        return super().get(name, default)

    def __getitem__(self, name):
        if name == "OPENAI_API_KEY":
            raise AssertionError("OPENAI_API_KEY must not be implicitly read")
        return super().__getitem__(name)


class OpenAIProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.local_origin = "http://127.0.0.1:" + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.server.requests = []
        self.server.delay = 0
        self.server.reply = (200, response_object([message_item(text_block("final answer"))],
                                                  model="test-model",
                                                  usage={"input_tokens": 3, "output_tokens": 2,
                                                         "total_tokens": 5}), {})
        self.origin_patch = patch("llmtool.providers.openai.API_ORIGIN", self.local_origin)
        self.origin_patch.start()
        self.addCleanup(self.origin_patch.stop)

    def provider(self, **updates):
        provider = {
            "type": "openai", "base_url": None, "model": "test-model",
            "timeout_seconds": 1, "api_key": FAKE_KEY, "api_key_env": None,
            "provider_options": {},
        }
        provider.update(updates)
        return provider

    def client(self, provider=None, timeout=1):
        selected = self.provider(timeout_seconds=timeout) if provider is None else provider
        return OpenAIProvider(selected, {"temperature": 0, "max_tokens": 123}, FAKE_KEY)

    def test_request_uses_responses_schema_and_fixed_official_origin(self):
        self.assertEqual(API_ORIGIN, "https://api.openai.com")
        reply = self.client().complete([
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "user prompt"},
        ])
        request = self.server.requests[0]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], "/v1/responses")
        self.assertTrue(request["headers"].get("Authorization") == "Bearer " + FAKE_KEY,
                        "Authorization header did not match the fake credential")
        self.assertEqual(request["body"]["model"], "test-model")
        self.assertEqual(request["body"]["input"], [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "user prompt"},
        ])
        self.assertEqual(request["body"]["max_output_tokens"], 123)
        self.assertIs(request["body"]["store"], False)
        self.assertNotIn("temperature", request["body"])
        self.assertNotIn("reasoning", request["body"])
        for field in ("tools", "tool_choice", "web_search", "file_search", "computer", "mcp"):
            self.assertNotIn(field, request["body"])
        self.assertEqual(reply.content, "final answer")

    def test_reasoning_effort_maps_to_responses_reasoning(self):
        provider = self.provider(provider_options={"reasoning_effort": "medium"})
        self.client(provider).complete([{"role": "user", "content": "question"}])
        self.assertEqual(self.server.requests[0]["body"]["reasoning"], {"effort": "medium"})

    def test_top_level_output_text_is_supported(self):
        self.server.reply = (200, response_object(output_text="visible answer"), {})
        reply = self.client().complete([{"role": "user", "content": "question"}])
        self.assertEqual(reply.content, "visible answer")

    def test_multiple_text_items_are_joined_and_reasoning_is_discarded(self):
        hidden = "PRIVATE_REASONING_MARKER"
        self.server.reply = (200, response_object([
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": hidden}],
             "encrypted_content": hidden},
            message_item(text_block("Hello "), text_block("world")),
        ], model="test-model", usage={"input_tokens": 4, "output_tokens": 6,
                                      "total_tokens": 10}), {})
        reply = self.client().complete([{"role": "user", "content": "question"}])
        self.assertEqual(reply.content, "Hello world")
        self.assertEqual(reply.metadata["input_tokens"], 4)
        self.assertEqual(reply.metadata["output_tokens"], 6)
        self.assertEqual(reply.metadata["total_tokens"], 10)
        self.assertTrue(hidden not in repr(reply), "reasoning data escaped into Reply")

    def test_refusal_text_is_final_visible_content(self):
        self.server.reply = (200, response_object([
            message_item({"type": "refusal", "refusal": "I cannot help with that."}),
        ]), {})
        self.assertEqual(self.client().complete([{"role": "user", "content": "question"}]).content,
                         "I cannot help with that.")

    def test_missing_final_text_and_incomplete_response_fail(self):
        cases = (
            response_object([{"type": "reasoning", "summary": []}]),
            {"status": "incomplete", "output": []},
            response_object([{"type": "message", "role": "assistant", "status": "in_progress",
                              "content": [text_block("partial")]}]),
        )
        for payload in cases:
            with self.subTest(payload=payload):
                self.server.reply = (200, payload, {})
                with self.assertRaises(LLMToolError):
                    self.client().complete([{"role": "user", "content": "question"}])

    def test_invalid_usage_metadata_fails_closed(self):
        for usage in ({"input_tokens": True}, {"output_tokens": -1}, [], None):
            with self.subTest(usage=usage):
                self.server.reply = (200, response_object(
                    [message_item(text_block("visible"))], usage=usage), {})
                with self.assertRaisesRegex(LLMToolError, "invalid usage metadata"):
                    self.client().complete([{"role": "user", "content": "question"}])

    def test_malformed_or_unexpected_response_is_sanitized(self):
        for raw in (b"{broken", b"NaN", b"[]"):
            with self.subTest(raw=raw):
                self.server.reply = (200, raw, {})
                with self.assertRaises(LLMToolError) as caught:
                    self.client().complete([{"role": "user", "content": "PRIVATE_PROMPT"}])
                self.assertTrue("PRIVATE_PROMPT" not in str(caught.exception),
                                "prompt escaped into provider error")

    def test_oversized_response_fails(self):
        self.server.reply = (200, b"x" * 32, {})
        with patch("llmtool.providers.openai.RESPONSE_LIMIT", 16):
            with self.assertRaisesRegex(LLMToolError, "exceeds 16 MiB"):
                self.client().complete([{"role": "user", "content": "question"}])

    def test_http_error_body_and_fake_key_are_not_exposed(self):
        self.server.reply = (401, ("echo " + FAKE_KEY + " PRIVATE_PROMPT").encode(), {})
        with self.assertRaises(LLMToolError) as caught:
            self.client().complete([{"role": "user", "content": "PRIVATE_PROMPT"}])
        detail = str(caught.exception)
        self.assertTrue(FAKE_KEY not in detail and "PRIVATE_PROMPT" not in detail,
                        "credential or prompt escaped into HTTP error")
        self.assertTrue("Authorization" not in detail, "request header escaped into HTTP error")

    def test_timeout_is_sanitized(self):
        self.server.delay = 0.15
        with self.assertRaisesRegex(LLMToolError, "timed out") as caught:
            self.client(timeout=0.02).complete([{"role": "user", "content": "question"}])
        self.assertTrue(FAKE_KEY not in str(caught.exception), "credential escaped into timeout error")

    def test_redirect_is_not_followed(self):
        target = "http://127.0.0.1:" + str(self.server.server_port) + "/redirect-target"
        self.server.reply = (302, b"redirect body", {"Location": target})
        with self.assertRaisesRegex(LLMToolError, "HTTP 302"):
            self.client().complete([{"role": "user", "content": "question"}])
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.server.requests[0]["path"], "/v1/responses")

    def test_validation_rejects_endpoint_options_and_missing_auth_or_model(self):
        for provider in (
            self.provider(base_url="https://other.example"),
            self.provider(provider_options={"enable_thinking": False}),
            self.provider(provider_options={"reasoning_effort": "xhigh"}),
            self.provider(model=None),
            self.provider(api_key=None, api_key_env=None),
        ):
            with self.subTest(provider=provider), self.assertRaises(LLMToolError):
                OpenAIProvider.validate_config(provider)
                OpenAIProvider.validate_options(provider["provider_options"])
                OpenAIProvider.validate_ready(provider)
        self.assertEqual(self.server.requests, [])

    def test_factory_and_capability_registry_contain_only_real_providers(self):
        self.assertEqual(PROVIDER_REGISTRY.names(), ("claude-cli", "codex-cli", "openai", "openai-compatible"))
        self.assertEqual(get_provider_capabilities("openai").to_dict(), {
            "chat": True, "large_context": True, "reasoning_control": True,
            "streaming": False, "code_edit": False, "shell": False, "git": False,
        })

    def test_cli_uses_configured_fake_key_and_sanitizes_output(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"
            project = Path(temp) / "project"
            project.mkdir()
            config_path = home / ".config/llmtool/config.yaml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(yaml.safe_dump({
                "providers": {"openai-test": {
                    "type": "openai", "model": "test-model", "api_key": FAKE_KEY,
                }},
                "active_provider": "openai-test",
                "generation": {"temperature": 0, "max_tokens": 123},
            }), encoding="utf-8")
            env = GuardedEnvironment({"HOME": str(home), "PATH": os.environ.get("PATH", "")})
            self.server.reply = (401, (FAKE_KEY + " PRIVATE_PROMPT").encode(), {})
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch("llmtool.config.os.environ", env), \
                    patch("pathlib.Path.cwd", return_value=project), \
                    patch("llmtool.providers.openai.API_ORIGIN", self.local_origin), \
                    patch("subprocess.Popen", side_effect=AssertionError("subprocess invoked")), \
                    patch("os.system", side_effect=AssertionError("shell invoked")), \
                    redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["ask", "PRIVATE_PROMPT"])
            self.assertEqual(code, 1)
            output = stdout.getvalue() + stderr.getvalue()
            self.assertTrue(FAKE_KEY not in output and "PRIVATE_PROMPT" not in output,
                            "credential or prompt escaped into CLI output")
            self.assertEqual(self.server.requests[0]["path"], "/v1/responses")

    def test_openai_compatible_streaming_sse_and_response_cleanup(self):
        class FakeResponse:
            status = 200

            def __init__(self, lines):
                self.lines = iter(lines)
                self.pending = b""
                self.closed = False

            def readline(self, limit):
                while b"\n" not in self.pending:
                    chunk = next(self.lines, b"")
                    if not chunk:
                        break
                    self.pending += chunk
                if b"\n" in self.pending:
                    line, self.pending = self.pending.split(b"\n", 1)
                    line += b"\n"
                else:
                    line, self.pending = self.pending, b""
                return line[:limit]

            def close(self):
                self.closed = True

        def make_client(response):
            provider = {
                "base_url": "https://gateway.example", "model": "test-model",
                "timeout_seconds": 2, "provider_options": {},
            }
            client = OpenAICompatibleClient(provider, {"temperature": 0, "max_tokens": 20}, FAKE_KEY)
            client.opener.open = lambda req, timeout: opened.append((req, timeout)) or response
            return client

        opened = []
        response = FakeResponse([
            b'retry: 1000\n\n',
            b'data: {"choices":[{"delta":{"role":"assistant","content":""}}]}\n\n',
            b'data: {"choices":[{"delta":{"reasoning":"hidden"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"Hello "}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"world"},"finish_reason":"stop","stop_reason":null}]}\n\n',
            b'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n',
            b"data: [DONE]\n\n",
        ])
        client = make_client(response)
        self.assertEqual(list(client.stream([{"role": "user", "content": "question"}])),
                         ["Hello ", "world"])
        self.assertTrue(response.closed)
        req, timeout = opened[0]
        self.assertTrue(json.loads(req.data)["stream"])
        self.assertEqual(req.get_header("Accept"), "text/event-stream")
        self.assertEqual(timeout, 2)

        response = FakeResponse([
            b'data: {"choices":[{"delta":{"content":"answer"},"finish_reason":"stop"}]}\n\n',
            b'data: {"choices":[],"usage":{"total_tokens":1},"id":"event-1","object":"chunk","created":1,"model":"test-model","system_fingerprint":"fp"}\n\n',
        ])
        self.assertEqual(list(make_client(response).stream([{"role": "user", "content": "question"}])),
                         ["answer"])
        self.assertTrue(response.closed)

        envelope = {
            "created": 1761696000,
            "id": "chatcmpl-real-stream",
            "model": "gw-model",
            "object": "chat.completion.chunk",
        }
        captured_events = [
            {
                **envelope,
                "prompt_text": None,
                "prompt_token_ids": None,
                "choices": [{
                    "delta": {"content": "", "role": "assistant"},
                    "finish_reason": None,
                    "index": 0,
                    "logprobs": None,
                }],
            }
        ]
        expected_content = []
        for index in range(1, 67):
            content_event = index % 2 == 0
            delta_field = "content" if content_event else "reasoning"
            value = chr(ord("a") + (index // 2) % 26) if content_event else "private reasoning"
            if content_event:
                expected_content.append(value)
            captured_events.append({
                **envelope,
                "choices": [{
                    "delta": {delta_field: value},
                    "index": 0,
                    "token_ids": None,
                }],
            })
        captured_events.append({
            **envelope,
            "choices": [{
                "delta": {"content": "!"},
                "finish_reason": "stop",
                "index": 0,
                "stop_reason": None,
                "token_ids": None,
            }],
        })
        expected_content.append("!")
        captured_events.append({
            **envelope,
            "system_fingerprint": "fp-real-stream",
            "choices": [],
            "usage": {
                "completion_tokens": 67,
                "completion_tokens_details": {"reasoning_tokens": 33},
                "prompt_tokens": 12,
                "total_tokens": 79,
            },
        })
        self.assertEqual(len(captured_events), 69)
        response = FakeResponse([
            *(b"data: " + json.dumps(event).encode("utf-8") + b"\n\n"
              for event in captured_events),
            b"data: [DONE]\n\n",
        ])
        self.assertEqual(
            list(make_client(response).stream([{"role": "user", "content": "question"}])),
            expected_content,
        )
        self.assertTrue(response.closed)

        for event in (
            b'data: {"choices":[{"delta":{}}],"prompt_text":"unexpected"}\n\n',
            b'data: {"choices":[{"delta":{}}],"prompt_token_ids":[]}\n\n',
            b'data: {"choices":[{"delta":{},"token_ids":[1]}]}\n\n',
            b'data: {"choices":[]}\n\n',
            b'data: {"choices":[],"usage":{"total_tokens":1},"message":{"content":"late"}}\n\n',
            b'data: {"choices":[],"usage":{"total_tokens":1},"unexpected":"value"}\n\n',
            b'data: {"choices":[],"usage":{"total_tokens":1,"details":{"text":"late"}}}\n\n',
            b'data: {"choices":[],"usage":{"total_tokens":1,"total_tokens":2}}\n\n',
            b'data: {"choices":[],"usage":{"total_tokens":true}}\n\n',
            b'data: {"choices":[{"delta":{}}],"usage":{"total_tokens":1}}\n\n',
        ):
            response = FakeResponse([
                b'data: {"choices":[{"delta":{"content":"answer"},"finish_reason":"stop"}]}\n\n',
                event,
            ])
            with self.assertRaises(LLMToolError) as caught:
                list(make_client(response).stream([{"role": "user", "content": "question"}]))
            self.assertIn("invalid streaming data", str(caught.exception))
            self.assertTrue(response.closed)

        response = FakeResponse([
            b'data: {"choices":[{"delta":{"content":"answer"},"finish_reason":"stop"}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"late"}}]}\n\n',
        ])
        with self.assertRaises(LLMToolError) as caught:
            list(make_client(response).stream([{"role": "user", "content": "question"}]))
        self.assertIn("invalid streaming data", str(caught.exception))
        self.assertTrue(response.closed)

        response = FakeResponse([b"data: [DONE]\n\n"])
        with self.assertRaises(LLMToolError) as caught:
            list(make_client(response).stream([{"role": "user", "content": "question"}]))
        self.assertIn("ended before finish_reason=stop", str(caught.exception))
        self.assertTrue(response.closed)

        response = FakeResponse([
            b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
        ])
        with self.assertRaises(LLMToolError) as caught:
            list(make_client(response).stream([{"role": "user", "content": "question"}]))
        self.assertIn("ended before finish_reason=stop", str(caught.exception))
        self.assertTrue(response.closed)

        response = FakeResponse([
            b'data: {"choices":[],"usage":{"total_tokens":1}}\n\n',
        ])
        with self.assertRaises(LLMToolError) as caught:
            list(make_client(response).stream([{"role": "user", "content": "question"}]))
        self.assertIn("invalid streaming data", str(caught.exception))
        self.assertTrue(response.closed)

        for event in (
            b'data: {"choices":[{"delta":{},"unexpected":null}]}\n\n',
            b'data: {"choices":[{"delta":{"role":"user"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":2}}]}\n\n',
            b'data: {"choices":[{"delta":{}},{"delta":{}}]}\n\n',
            b'data: {"choices":[{"delta":{},"index":true}]}\n\n',
            b'data: {"choices":[{"delta":{},"logprobs":{}}]}\n\n',
            b'data: {"choices":[{"delta":{}}],"usage":{"total_tokens":0}}\n\n',
        ):
            response = FakeResponse([event])
            with self.assertRaises(LLMToolError) as caught:
                list(make_client(response).stream([{"role": "user", "content": "question"}]))
            self.assertIn("invalid streaming data", str(caught.exception))
            self.assertTrue(response.closed)

        response = FakeResponse([
            b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n',
        ])
        with self.assertRaises(LLMToolError) as caught:
            list(make_client(response).stream([{"role": "user", "content": "question"}]))
        self.assertIn("invalid streaming data", str(caught.exception))
        self.assertTrue(response.closed)

        response = FakeResponse([
            b'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n\n',
        ])
        with self.assertRaises(LLMToolError) as caught:
            list(make_client(response).stream([{"role": "user", "content": "question"}]))
        self.assertIn("incomplete result", str(caught.exception))
        self.assertTrue(response.closed)

        response = FakeResponse([
            b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"second"}}]}\n\n',
        ])
        chunks = make_client(response).stream([{"role": "user", "content": "question"}])
        self.assertEqual(next(chunks), "first")
        chunks.close()
        self.assertTrue(response.closed)

    def test_openai_compatible_streaming_byte_limit_and_complete_body(self):
        class FakeResponse:
            status = 200

            def __init__(self, raw):
                self.raw = raw
                self.closed = False

            def readline(self, limit):
                value, self.raw = self.raw[:limit], self.raw[limit:]
                return value

            def read(self, limit):
                value, self.raw = self.raw[:limit], b""
                return value

            def close(self):
                self.closed = True

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        provider = {
            "base_url": "https://gateway.example", "model": "test-model",
            "timeout_seconds": 2, "provider_options": {},
        }
        client = OpenAICompatibleClient(provider, {"temperature": 0, "max_tokens": 20}, FAKE_KEY)
        response = FakeResponse(b"data: " + b"x" * 20 + b"\n")
        client.opener.open = lambda req, timeout: response
        with patch("llmtool.providers.openai_compatible.RESPONSE_LIMIT", 16):
            with self.assertRaises(LLMToolError):
                list(client.stream([{"role": "user", "content": "question"}]))
        self.assertTrue(response.closed)

        response = FakeResponse(json.dumps({
            "choices": [{"finish_reason": "stop", "message": {"content": "unchanged"}}],
        }).encode())
        request_body = []
        client.opener.open = lambda req, timeout: request_body.append(req.data) or response
        reply = client.complete([{"role": "user", "content": "question"}])
        self.assertEqual(reply.content, "unchanged")
        self.assertNotIn("stream", json.loads(request_body[0]))


if __name__ == "__main__":
    unittest.main()
