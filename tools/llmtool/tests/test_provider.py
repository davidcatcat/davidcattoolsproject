import copy
import io
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from llmtool.config import DEFAULTS, resolve_config, user_config_path
from llmtool import diagnostics as d
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

    def test_exact_wire_unicode_surrogates_and_same_bytes_send(self):
        messages = [{'role': 'user', 'content': '中文😀\ud800\udfff"\\\n'}]
        body = {'model': self.client.provider['model'], 'temperature': self.client.generation['temperature'],
                'max_tokens': self.client.generation['max_tokens'], 'messages': messages,
                'chat_template_kwargs': {'enable_thinking': False}}
        expected = json.dumps(body, ensure_ascii=True, allow_nan=False).encode('utf-8')
        encoded = self.client.encode_request(messages)
        self.assertEqual(encoded, expected)
        self.open.return_value = FakeResponse(json.dumps(success()).encode())
        self.client.send(encoded)
        self.assertIs(self.open.call_args.args[0].data, encoded)
        self.open.return_value = FakeResponse(json.dumps(success()).encode())
        self.client.complete(messages)
        self.assertEqual(self.open.call_args.args[0].data, expected)

    def test_legacy_error_order_before_unserializable_messages(self):
        self.client.key = object()
        with self.assertRaises(TypeError) as caught:
            self.client.complete([object()])
        self.assertIn('concatenate', str(caught.exception))
        self.open.assert_not_called()
        self.client.key = None
        self.client.provider['provider_options'] = {'enable_thinking': 'bad'}
        with self.assertRaises(LLMToolError):self.client.complete([object()])
        self.open.assert_not_called()

    def test_surrogate_response_keeps_primary_failure_and_p1_usage(self):
        for content in ('\ud800', '\udfff'):
            with self.assertRaises(LLMToolError) as caught:self.answer(success(content))
            self.assertEqual(caught.exception.diagnostic.code, d.ErrorCode.MODEL_RESPONSE_INVALID)
            self.assertEqual(caught.exception.provider_metadata['total_tokens'], 25)

    def test_success_only_final_content_and_safe_metadata(self):
        reply = self.answer(success())
        self.assertEqual(reply.content, "final answer")
        self.assertEqual(reply.metadata["model"], "qwen3.6-35b-a3b")
        self.assertEqual(reply.metadata["prompt_tokens"], 20)
        self.assertEqual(reply.metadata["completion_tokens"], 5)
        self.assertEqual(reply.metadata["total_tokens"], 25)
        self.assertEqual(list(reply.metadata),
                         ['elapsed_seconds', 'model', 'served_model', 'prompt_tokens',
                          'completion_tokens', 'total_tokens'])
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

    def test_failure_usage_length_preserves_triplet_and_original_error(self):
        body = success("partial-sensitive-content")
        body["choices"][0]["finish_reason"] = "length"
        body["usage"] = {"prompt_tokens": 100, "completion_tokens": 4096, "total_tokens": 4196}
        with self.assertRaises(LLMToolError) as caught:
            self.answer(body)
        exc = caught.exception
        self.assertEqual(getattr(exc, 'provider_metadata', None), body["usage"])
        self.assertEqual(exc.diagnostic.code, d.ErrorCode.MODEL_OUTPUT_INCOMPLETE)
        self.assertEqual(str(exc), "incomplete result (finish_reason=length); increase max_tokens")
        self.assertFalse(exc.diagnostic.retryable)
        self.assertFalse(exc.diagnostic.fallback_allowed)
        self.open.assert_called_once()
        for value in ("partial-sensitive-content", "4096", "4196"):
            self.assertNotIn(value, str(exc))
            self.assertNotIn(value, repr(exc))
            self.assertNotIn(value, exc.diagnostic.to_json())

    def test_failure_usage_missing_partial_invalid_remains_unavailable(self):
        invalid = (None, {}, [], {"prompt_tokens": 20},
                   {"prompt_tokens": 20, "completion_tokens": 5},
                   {"prompt_tokens": True, "completion_tokens": 5, "total_tokens": 6},
                   {"prompt_tokens": -1, "completion_tokens": 5, "total_tokens": 4},
                   {"prompt_tokens": "20", "completion_tokens": 5, "total_tokens": 25},
                   {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 26},
                   {"prompt_tokens": d.MAX_TOKEN_COUNT + 1, "completion_tokens": 0,
                    "total_tokens": d.MAX_TOKEN_COUNT + 1})
        for usage in invalid:
            with self.subTest(usage=usage):
                body = success()
                body["choices"][0]["finish_reason"] = "length"
                body["usage"] = usage
                self.open.reset_mock()
                with self.assertRaises(LLMToolError) as caught:
                    self.answer(body)
                self.assertIsNone(caught.exception.provider_metadata)
                self.assertEqual(caught.exception.diagnostic.code, d.ErrorCode.MODEL_OUTPUT_INCOMPLETE)
                self.open.assert_called_once()
        body.pop("usage")
        with self.assertRaises(LLMToolError) as caught:
            self.answer(body)
        self.assertIsNone(caught.exception.provider_metadata)

    def test_failure_usage_parseable_invalid_responses_preserve_triplet(self):
        cases = [success(""), success([]), success("\ud800")]
        for reason in ("unexpected", "tool_calls", None):
            body = success()
            body["choices"][0]["finish_reason"] = reason
            cases.append(body)
        for field in ("tool_calls", "function_call"):
            body = success()
            body["choices"][0]["message"][field] = {"private": "RAW_TOOL_MARKER"}
            cases.append(body)
        for choices in ([], {}, [None], [{"finish_reason": "stop", "message": []}]):
            cases.append(dict(success(), choices=choices))
        cases.append(dict(success(), error={"message": "RAW_ERROR_MARKER"}))
        for index, body in enumerate(cases):
            with self.subTest(case=index):
                self.open.reset_mock()
                with self.assertRaises(LLMToolError) as caught:
                    self.answer(body)
                self.assertEqual(dict(caught.exception.provider_metadata), body["usage"])
                self.assertEqual(caught.exception.diagnostic.code, d.ErrorCode.MODEL_RESPONSE_INVALID)
                self.assertFalse(caught.exception.diagnostic.retryable)
                self.open.assert_called_once()

    def test_failure_usage_validation_failure_preserves_primary_error(self):
        for reason, code in (('length', d.ErrorCode.MODEL_OUTPUT_INCOMPLETE),
                             ('unexpected', d.ErrorCode.MODEL_RESPONSE_INVALID)):
            with self.subTest(reason=reason):
                body = success()
                body['choices'][0]['finish_reason'] = reason
                self.open.reset_mock()
                with patch.object(d, 'validate_provider_usage', side_effect=ValueError('PRIVATE_MARKER')), \
                        self.assertRaises(LLMToolError) as caught:
                    self.answer(body)
                self.assertIsNone(caught.exception.provider_metadata)
                self.assertEqual(caught.exception.diagnostic.code, code)
                self.assertNotIn('PRIVATE_MARKER', str(caught.exception))
                self.open.assert_called_once()

    def test_failure_usage_invalid_json_and_nondict_have_no_metadata(self):
        for raw in (b'not JSON', b'{"usage":{"total_tokens":123}} trailing', b'[]', b'null'):
            with self.subTest(raw=raw), self.assertRaises(LLMToolError) as caught:
                self.answer(raw)
            self.assertEqual(caught.exception.diagnostic.code, d.ErrorCode.MODEL_RESPONSE_INVALID)
            self.assertIsNone(caught.exception.provider_metadata)

    def test_failure_usage_oversize_body_is_never_decoded(self):
        with patch('llmtool.providers.openai_compatible.RESPONSE_LIMIT', 16), \
                patch('llmtool.providers.openai_compatible.json.loads') as decode, \
                self.assertRaises(LLMToolError) as caught:
            self.answer(b'x' * 17)
        self.assertEqual(caught.exception.diagnostic.code, d.ErrorCode.MODEL_RESPONSE_TOO_LARGE)
        self.assertIsNone(caught.exception.provider_metadata)
        decode.assert_not_called()

    def test_failure_usage_http_network_timeout_do_not_parse_body(self):
        for failure, code, retryable in (
                (socket.timeout('PRIVATE_TIMEOUT'), d.ErrorCode.PROVIDER_TIMEOUT, True),
                (URLError('PRIVATE_NETWORK'), d.ErrorCode.NETWORK_PROVIDER_UNREACHABLE, True),
                (OSError('PRIVATE_IO'), d.ErrorCode.PROVIDER_RUNTIME_ERROR, False),
                (HTTPError('http://private', 401, 'PRIVATE_HTTP', {}, io.BytesIO(b'RAW_BODY')),
                 d.ErrorCode.AUTH_PROVIDER_REJECTED, False)):
            with self.subTest(code=code):
                self.open.reset_mock()
                self.open.side_effect = failure
                with self.assertRaises(LLMToolError) as caught:
                    self.client.complete([])
                self.assertIsNone(caught.exception.provider_metadata)
                self.assertEqual(caught.exception.diagnostic.code, code)
                self.assertEqual(caught.exception.diagnostic.retryable, retryable)
                self.open.assert_called_once()

    def test_failure_usage_cannot_carry_raw_content_or_credentials(self):
        self.client.key = 'SYNTHETIC_CREDENTIAL_MARKER'
        body = success('RAW_CONTENT_MARKER')
        body['model'] = 'MODEL_TEXT_MARKER'
        body['choices'][0]['finish_reason'] = 'length'
        body['usage']['raw_body'] = 'RAW_USAGE_MARKER'
        with self.assertRaises(LLMToolError) as caught:
            self.answer(body)
        exc = caught.exception
        self.assertEqual(dict(exc.provider_metadata),
                         {'prompt_tokens': 20, 'completion_tokens': 5, 'total_tokens': 25})
        rendered = str(exc) + repr(exc) + repr(exc.provider_metadata) + exc.diagnostic.to_json()
        for marker in ('SYNTHETIC_CREDENTIAL_MARKER', 'RAW_CONTENT_MARKER', 'MODEL_TEXT_MARKER',
                       'RAW_USAGE_MARKER', 'private-reasoning-marker', 'Authorization', 'test evidence'):
            self.assertNotIn(marker, rendered)

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

    def test_null_error_field_is_not_an_api_error(self):
        body = success()
        body["error"] = None
        self.assertEqual(self.answer(body).content, "final answer")

    def test_metadata_does_not_inject_lines_or_emit_non_numeric_usage(self):
        obj = success()
        obj["model"] = "model\nsecret-log-line"
        obj["usage"] = {"prompt_tokens": "secret", "completion_tokens": True, "total_tokens": -1}
        reply = self.answer(obj)
        self.assertEqual(set(reply.metadata), {"elapsed_seconds"})

    def test_success_usage_is_a_complete_triplet_or_absent(self):
        """C1B: success usage uses the P1 validator; partial/inconsistent is unknown, never partial."""
        from llmtool.diagnostics_runtime import RunContext
        fields = {'prompt_tokens', 'completion_tokens', 'total_tokens'}
        for usage, expected in (
                ({'prompt_tokens': 20, 'completion_tokens': 5, 'total_tokens': 25},
                 {'prompt_tokens': 20, 'completion_tokens': 5, 'total_tokens': 25}),
                ({'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0},
                 {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}),
                ({'prompt_tokens': 20}, None),
                ({'prompt_tokens': 20, 'completion_tokens': 5}, None),
                ({'prompt_tokens': 20, 'completion_tokens': 5, 'total_tokens': 99}, None),
                ({'prompt_tokens': 20.0, 'completion_tokens': 5, 'total_tokens': 25}, None),
                ({'prompt_tokens': -1, 'completion_tokens': 1, 'total_tokens': 0}, None),
                ({'prompt_tokens': 10 ** 13, 'completion_tokens': 0, 'total_tokens': 10 ** 13}, None),
                ({'prompt_tokens': 20, 'completion_tokens': 5, 'total_tokens': 25, 'raw': 'X'},
                 {'prompt_tokens': 20, 'completion_tokens': 5, 'total_tokens': 25})):
            with self.subTest(usage=usage):
                body = success()
                body['usage'] = usage
                reply = self.answer(body)
                self.assertEqual(reply.content, 'final answer')  # never turned into a failure
                got = {k: v for k, v in reply.metadata.items() if k in fields}
                self.assertEqual(got, expected or {})
                run = RunContext(d.DiagnosticMode.VERBOSE)
                run.response(reply.metadata)
                event = next(e for e in run.events if e.type == d.EventType.PROVIDER_RESPONSE)
                self.assertEqual(event.data['token_usage_available'], expected is not None)
        self.assertEqual(self.open.call_count, 9)  # one request per answer, never a retry

    def test_request_body_bytes_are_the_exact_bytes_sent(self):
        """P0-LIVE-SEAM: one serialization for digest, cap and send; ask path unchanged."""
        messages = messages_for("ask", "test evidence")
        encoded = self.client.encode_request(messages)
        self.assertIsInstance(encoded, bytes)
        expected = json.dumps({"model": "test-model", "temperature": 0, "max_tokens": 2000,
                               "messages": messages,
                               "chat_template_kwargs": {"enable_thinking": False}},
                              ensure_ascii=True, allow_nan=False).encode("utf-8")
        self.assertEqual(encoded, expected)
        self.answer(success())  # existing complete() path
        self.assertEqual(self.open.call_args[0][0].data, expected)
        self.open.reset_mock()
        self.open.return_value = FakeResponse(json.dumps(success()).encode())
        reply = self.client.send(encoded)
        self.assertEqual(reply.content, "final answer")
        self.assertEqual(self.open.call_args[0][0].data, encoded)
        self.open.assert_called_once()
        for bad in ("text", bytearray(b"x"), None):
            with self.assertRaises(LLMToolError):
                self.client.send(bad)
        self.open.assert_called_once()

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
        config["providers"]["default"]["type"] = "future-cli"
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

    def test_explicit_company_example_user_config_and_thinking_payload(self):
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
            self.assertEqual(req.full_url, "http://10.0.0.10:8000/v1/chat/completions")
            payload = json.loads(req.data)
            self.assertEqual(payload["model"], "gw")
            self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": True})
            self.assertEqual(reply.content, "final answer")

    def test_thinking_response_returns_final_content_without_reasoning(self):
        self.client.provider["provider_options"] = {"enable_thinking": True}
        reply = self.answer(success("final answer"))
        self.assertEqual(reply.content, "final answer")
        self.assertNotIn("private-reasoning-marker", repr(reply))
        self.assertNotIn("also-private", repr(reply))
