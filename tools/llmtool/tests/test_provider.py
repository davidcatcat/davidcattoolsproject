import copy
import io
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

from llmtool.config import DEFAULTS, resolve_config, user_config_path
from llmtool.errors import LLMToolError
from llmtool.prompts import TEMPLATES, messages_for
from llmtool.providers import create_client


def success(content="  final answer\n"):
    return {
        "model": "qwen3.6-35b-a3b",
        "choices": [{"finish_reason": "stop", "message": {
            "content": content, "reasoning": "private-reasoning-marker",
            "reasoning_content": "also-private",
        }}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
    }


class FakeResponse(io.BytesIO):
    status = 200


class ProviderTests(unittest.TestCase):
    def setUp(self):
        config = copy.deepcopy(DEFAULTS)
        config["providers"]["default"].update({
            "base_url": "http://127.0.0.1:1234", "model": "test-model",
            "provider_options": {"enable_thinking": False},
        })
        self.client = create_client(config)
        self.open = Mock()
        self.client.opener.open = self.open

    def answer(self, body):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.open.return_value = FakeResponse(raw)
        return self.client.complete(messages_for("ask", "test evidence"))

    def test_success_only_final_content_and_safe_metadata(self):
        reply = self.answer(success())
        self.assertEqual(reply.content, "final answer")
        self.assertEqual(reply.metadata["model"], "qwen3.6-35b-a3b")
        self.assertEqual(reply.metadata["prompt_tokens"], 20)
        self.assertNotIn("private-reasoning-marker", repr(reply))
        self.assertNotIn("also-private", repr(reply))

    def test_four_prompts_and_request_contract(self):
        self.assertEqual(set(TEMPLATES), {"ask", "analyze-log", "review-diff", "context-pack"})
        for command in TEMPLATES:
            with self.subTest(command=command):
                self.open.return_value = FakeResponse(json.dumps(success()).encode())
                messages = messages_for(command, "evidence-text")
                self.client.complete(messages)
                req = self.open.call_args.args[0]
                payload = json.loads(req.data)
                self.assertEqual(req.full_url, "http://127.0.0.1:1234/v1/chat/completions")
                self.assertEqual(req.method, "POST")
                self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": False})
                self.assertEqual(payload["temperature"], 0)
                self.assertEqual(payload["max_tokens"], 2000)
                self.assertEqual(payload["model"], "test-model")
                self.assertEqual(payload["messages"], messages)
                self.assertEqual(messages[1], {"role": "user", "content": "evidence-text"})
                self.assertNotIn("tools", payload)
                self.assertNotIn("Authorization", req.headers)
                self.assertEqual(self.open.call_args.kwargs["timeout"], 120)

    def test_optional_authorization(self):
        self.client.key = "fake-test-key"
        self.answer(success())
        self.assertEqual(self.open.call_args.args[0].get_header("Authorization"), "Bearer fake-test-key")

    def test_empty_or_invalid_content(self):
        for content in ("", " \n ", None, [], 123, "\ud800"):
            with self.subTest(content=repr(content)), self.assertRaises(LLMToolError):
                self.answer(success(content))

    def test_finish_length_rejects_partial_result(self):
        obj = success("partial-sensitive-content")
        obj["choices"][0]["finish_reason"] = "length"
        with self.assertRaisesRegex(LLMToolError, "incomplete result") as caught:
            self.answer(obj)
        self.assertNotIn("partial-sensitive-content", str(caught.exception))

    def test_missing_choices_or_invalid_shapes(self):
        for obj in ({}, [], None, {"choices": []}, {"choices": {}}, {"choices": [None]},
                    {"choices": [{"finish_reason": "stop", "message": []}]}):
            with self.subTest(obj=obj), self.assertRaises(LLMToolError):
                self.answer(obj)

    def test_reject_unfinished_filtered_and_tool_results(self):
        for reason in (None, "tool_calls", "content_filter", "function_call"):
            obj = success()
            obj["choices"][0]["finish_reason"] = reason
            with self.subTest(reason=reason), self.assertRaises(LLMToolError):
                self.answer(obj)
        obj = success()
        obj["choices"][0]["message"]["tool_calls"] = [{"function": "do-not-execute"}]
        with self.assertRaises(LLMToolError):
            self.answer(obj)

    def test_http_500_never_echoes_error_body_or_url(self):
        self.open.side_effect = HTTPError("http://secret-url", 500, "secret-reason", {}, io.BytesIO(b"secret-body"))
        with self.assertRaises(LLMToolError) as caught:
            self.client.complete([])
        self.assertIn("500", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))

    def test_timeout_and_connection_errors(self):
        for error in (socket.timeout("secret"), URLError(socket.timeout("secret")), URLError("secret")):
            self.open.side_effect = error
            with self.subTest(error=type(error)), self.assertRaises(LLMToolError) as caught:
                self.client.complete([])
            self.assertNotIn("secret", str(caught.exception))

    def test_malformed_json(self):
        for raw in (b"secret not JSON", b"\xff", b"{", b'{"value": NaN}', b'{"value": Infinity}'):
            with self.subTest(raw=raw), self.assertRaisesRegex(LLMToolError, "invalid JSON"):
                self.answer(raw)

    def test_unrepresentable_timeout_is_a_safe_error(self):
        self.open.side_effect = OverflowError("private transport details")
        with self.assertRaisesRegex(LLMToolError, "transport failed"):
            self.client.complete([])

    def test_api_error_body(self):
        with self.assertRaises(LLMToolError) as caught:
            self.answer({"error": {"message": "secret input and key"}})
        self.assertNotIn("secret", str(caught.exception))

    def test_metadata_does_not_inject_lines_or_emit_non_numeric_usage(self):
        obj = success()
        obj["model"] = "model\nsecret-log-line"
        obj["usage"] = {"prompt_tokens": "secret", "completion_tokens": True, "total_tokens": -1}
        reply = self.answer(obj)
        self.assertEqual(set(reply.metadata), {"elapsed_seconds"})

    def test_metadata_masks_api_key(self):
        self.client.key = "test-secret"
        obj = success()
        obj["model"] = "prefix-test-secret"
        self.assertEqual(self.answer(obj).metadata["model"], "[redacted]")

    def test_response_size_limit(self):
        with self.assertRaisesRegex(LLMToolError, "exceeds"):
            self.answer(b" " * (16 * 1024 * 1024 + 1))

    def test_invalid_provider_options_fail_before_request(self):
        self.client.provider["provider_options"] = {"enable_thinking": "false"}
        with self.assertRaises(LLMToolError):
            self.client.complete([])
        self.open.assert_not_called()

    def test_generic_profile_omits_thinking_extension(self):
        for options in ({}, {"enable_thinking": None}):
            with self.subTest(options=options):
                self.client.provider["provider_options"] = options
                self.answer(success())
                self.assertNotIn("chat_template_kwargs", json.loads(self.open.call_args.args[0].data))

    def test_factory_selects_named_profile_without_default_credentials(self):
        config = copy.deepcopy(DEFAULTS)
        config["providers"]["local"] = {
            "type": "openai-compatible", "base_url": "http://localhost:1234",
            "model": "local-model", "timeout_seconds": 2, "provider_options": {},
        }
        config["active_provider"] = "local"
        client = create_client(config, key="fake-local-key")
        client.opener.open = Mock(return_value=FakeResponse(json.dumps(success()).encode()))
        client.complete(messages_for("ask", "test"))
        req = client.opener.open.call_args.args[0]
        self.assertEqual(req.full_url, "http://localhost:1234/v1/chat/completions")
        self.assertEqual(json.loads(req.data)["model"], "local-model")
        self.assertNotIn("chat_template_kwargs", json.loads(req.data))
        self.assertEqual(req.get_header("Authorization"), "Bearer fake-local-key")

    def test_factory_rejects_unimplemented_provider_type(self):
        config = copy.deepcopy(DEFAULTS)
        config["providers"]["default"]["type"] = "codex-cli"
        with self.assertRaises(LLMToolError):
            create_client(config)

    def test_factory_rejects_unconfigured_provider(self):
        for fields, missing in (({}, "base_url"), ({"model": "test"}, "base_url"),
                                ({"base_url": "http://localhost:1234"}, "model")):
            with self.subTest(fields=fields):
                config = copy.deepcopy(DEFAULTS)
                config["providers"]["default"].update(fields)
                with self.assertRaisesRegex(LLMToolError, "provider " + missing + " is not configured"):
                    create_client(config)

    def test_client_rejects_missing_settings_before_http(self):
        for field in ("base_url", "model"):
            with self.subTest(field=field):
                original = self.client.provider[field]
                self.client.provider[field] = None
                with self.assertRaisesRegex(LLMToolError, "provider " + field + " is not configured"):
                    self.client.complete([])
                self.open.assert_not_called()
                self.client.provider[field] = original

    def test_explicit_company_example_user_config_and_no_thinking_payload(self):
        example = Path(__file__).resolve().parents[1] / "config.example.company-qwen.yaml"
        with tempfile.TemporaryDirectory() as directory:
            user = user_config_path(directory)
            user.parent.mkdir(parents=True)
            user.write_bytes(example.read_bytes())
            config = resolve_config(environ={}, cwd=directory, home=directory, require_configured=True)
            self.assertIsNone(config["providers"]["default"]["api_key"])
            self.assertIsNone(config["providers"]["default"]["api_key_env"])
            client = create_client(config)
            client.opener.open = Mock(return_value=FakeResponse(json.dumps(success()).encode()))
            reply = client.complete(messages_for("ask", "test"))
            req = client.opener.open.call_args.args[0]
            self.assertEqual(req.full_url, "http://10.29.7.58:4000/v1/chat/completions")
            payload = json.loads(req.data)
            self.assertEqual(payload["model"], "gw")
            self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": False})
            self.assertEqual(reply.content, "final answer")
