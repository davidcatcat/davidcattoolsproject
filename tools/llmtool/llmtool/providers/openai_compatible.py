"""OpenAI-compatible requests; never execute tools or returned content."""

import http.client
import json
import re
import socket
import time
from urllib import error, request

from ..errors import LLMToolError, coded_error, http_error_code
from .base import ProviderCapabilities, Reply

RESPONSE_LIMIT = 16 * 1024 * 1024


def _reject_json_constant(value):
    raise ValueError("non-standard JSON number")


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _is_nonnegative_int(value):
    return type(value) is int and value >= 0


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OpenAICompatibleClient:
    TYPE = "openai-compatible"
    # Reasoning control is the optional enable_thinking extension, not a
    # universal OpenAI-compatible feature. Context limits remain model-specific.
    CAPABILITIES = ProviderCapabilities(
        chat=True, large_context=True, reasoning_control=True,
        streaming=True, code_edit=False, shell=False, git=False,
    )

    @staticmethod
    def validate_config(provider):
        return None

    @staticmethod
    def validate_ready(provider):
        for field in ("base_url", "model"):
            if provider.get(field) is None:
                raise coded_error(
                    "provider " + field + " is not configured",
                    'CONFIG_PROVIDER_NOT_READY',
                    'provider.openai-compatible',
                    user_action_required=True,
                )

    @staticmethod
    def validate_options(options):
        if not isinstance(options, dict) or set(options) - {"enable_thinking"}:
            raise LLMToolError("unsupported openai-compatible provider_options")
        value = options.get("enable_thinking")
        if value is not None and type(value) is not bool:
            raise LLMToolError("enable_thinking must be a boolean or null")

    def __init__(self, provider, generation, key=None):
        self.provider = provider
        self.generation = generation
        self.key = key
        # Do not forward prompts/keys through ambient proxy settings.
        self.opener = request.build_opener(request.ProxyHandler({}), NoRedirect())

    def _request_body(self, messages):
        self.validate_ready(self.provider)
        options = self.provider.get("provider_options", {})
        self.validate_options(options)
        body = {
            "model": self.provider["model"],
            "temperature": self.generation["temperature"],
            "max_tokens": self.generation["max_tokens"],
            "messages": messages,
        }
        if options.get("enable_thinking") is not None:
            body["chat_template_kwargs"] = {"enable_thinking": options["enable_thinking"]}
        return body

    def encode_request(self, messages):
        """Exact wire bytes for callers with their own separately authorized route."""
        return json.dumps(self._request_body(messages), ensure_ascii=True, allow_nan=False).encode("utf-8")

    def complete(self, messages):
        body = self._request_body(messages)
        url, headers = self._request_target()
        # Preserve legacy order: readiness/options, headers/URL, serialization.
        data = json.dumps(body, ensure_ascii=True, allow_nan=False).encode("utf-8")
        return self._send(data, url, headers)

    def stream(self, messages):
        """Yield visible text deltas from a bounded Server-Sent Events reply."""
        body = self._request_body(messages)
        body["stream"] = True
        url, headers = self._request_target()
        headers["Accept"] = "text/event-stream"
        data = json.dumps(body, ensure_ascii=True, allow_nan=False).encode("utf-8")
        req = request.Request(url, data=data, headers=headers, method="POST")
        total = 0
        event_data = []
        finished = False
        try:
            response = self.opener.open(req, timeout=self.provider["timeout_seconds"])
            try:
                if not 200 <= response.status < 300:
                    raise coded_error(
                        "gateway returned a non-success HTTP status",
                        http_error_code(response.status),
                        'provider.openai-compatible',
                        retryable=response.status == 429,
                        user_action_required=response.status in (401, 403),
                        safe_cause={"http_status": response.status},
                    )
                while True:
                    line = response.readline(RESPONSE_LIMIT + 1)
                    if not line:
                        if event_data:
                            if event_data == ["[DONE]"]:
                                if not finished:
                                    raise self._stream_incomplete()
                                return
                            chunks, event_finished = self._stream_event(event_data, finished)
                            for chunk in chunks:
                                yield chunk
                            finished = finished or event_finished
                        if not finished:
                            raise self._stream_incomplete()
                        return
                    total += len(line)
                    if total > RESPONSE_LIMIT:
                        raise coded_error(
                            "gateway response exceeds 16 MiB",
                            'MODEL_RESPONSE_TOO_LARGE',
                            'provider.openai-compatible',
                        )
                    try:
                        line = line.decode("utf-8").rstrip("\r\n")
                    except UnicodeError:
                        raise self._stream_invalid() from None
                    if not line:
                        if event_data:
                            if event_data == ["[DONE]"]:
                                if not finished:
                                    raise self._stream_incomplete()
                                return
                            chunks, event_finished = self._stream_event(event_data, finished)
                            for chunk in chunks:
                                yield chunk
                            finished = finished or event_finished
                            event_data = []
                        continue
                    if line.startswith(":"):
                        continue
                    field, separator, value = line.partition(":")
                    if not separator and field not in ("data", "event", "id", "retry"):
                        raise self._stream_invalid()
                    if field == "data":
                        event_data.append(value[1:] if value.startswith(" ") else value)
                    elif field == "retry" and value:
                        retry_value = value[1:] if value.startswith(" ") else value
                        if not retry_value.isdecimal():
                            raise self._stream_invalid()
            finally:
                response.close()
        except error.HTTPError as exc:
            status = exc.code
            exc.close()
            raise coded_error(
                "gateway HTTP " + str(status) + "; response body omitted",
                http_error_code(status),
                'provider.openai-compatible',
                retryable=status == 429,
                user_action_required=status in (401, 403),
                safe_cause={"http_status": status},
            ) from None
        except (TimeoutError, socket.timeout):
            raise coded_error(
                "gateway request timed out", 'PROVIDER_TIMEOUT',
                'provider.openai-compatible', retryable=True,
            ) from None
        except error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise coded_error(
                    "gateway request timed out", 'PROVIDER_TIMEOUT',
                    'provider.openai-compatible', retryable=True,
                ) from None
            raise coded_error(
                "cannot connect to gateway; check network, base_url, and TLS",
                'NETWORK_PROVIDER_UNREACHABLE',
                'provider.openai-compatible', retryable=True,
            ) from None
        except (OSError, http.client.HTTPException, ValueError, OverflowError):
            raise coded_error(
                "gateway transport failed; details omitted",
                'PROVIDER_RUNTIME_ERROR', 'provider.openai-compatible',
            ) from None

    @staticmethod
    def _stream_invalid():
        return coded_error(
            "gateway returned invalid streaming data",
            'MODEL_RESPONSE_INVALID', 'provider.openai-compatible',
        )

    @staticmethod
    def _stream_incomplete():
        return coded_error(
            "gateway stream ended before finish_reason=stop",
            'MODEL_OUTPUT_INCOMPLETE', 'provider.openai-compatible',
        )

    @classmethod
    def _stream_event(cls, event_data, finished=False):
        try:
            payload = "\n".join(event_data)
            obj = json.loads(
                payload,
                parse_constant=_reject_json_constant,
                object_pairs_hook=_unique_json_object,
            )
        except (ValueError, UnicodeError, RecursionError):
            raise cls._stream_invalid() from None
        if not isinstance(obj, dict):
            raise cls._stream_invalid()
        allowed_event_fields = {
            "choices", "usage", "id", "object", "created", "model",
            "system_fingerprint", "prompt_text", "prompt_token_ids",
        }
        if set(obj) - allowed_event_fields or "choices" not in obj:
            raise cls._stream_invalid()
        if any(obj[field] is not None for field in ("prompt_text", "prompt_token_ids")
               if field in obj):
            raise cls._stream_invalid()
        for field in ("id", "object", "model"):
            if field in obj and not isinstance(obj[field], str):
                raise cls._stream_invalid()
        if "created" in obj and not _is_nonnegative_int(obj["created"]):
            raise cls._stream_invalid()
        if "system_fingerprint" in obj and obj["system_fingerprint"] is not None \
                and not isinstance(obj["system_fingerprint"], str):
            raise cls._stream_invalid()
        choices = obj.get("choices")
        if not isinstance(choices, list):
            raise cls._stream_invalid()
        if finished:
            if choices or "usage" not in obj or not cls._valid_stream_usage(obj["usage"]):
                raise cls._stream_invalid()
            return [], False
        if "usage" in obj and obj["usage"] is not None:
            raise cls._stream_invalid()
        if len(choices) != 1 or not isinstance(choices[0], dict):
            raise cls._stream_invalid()
        choice = choices[0]
        allowed_choice_fields = {
            "delta", "index", "finish_reason", "stop_reason", "logprobs", "token_ids",
        }
        if set(choice) - allowed_choice_fields or "delta" not in choice:
            raise cls._stream_invalid()
        if "token_ids" in choice and choice["token_ids"] is not None:
            raise cls._stream_invalid()
        if "index" in choice and (type(choice["index"]) is not int or choice["index"] != 0):
            raise cls._stream_invalid()
        if "stop_reason" in choice and choice["stop_reason"] is not None:
            raise cls._stream_invalid()
        if "logprobs" in choice and choice["logprobs"] is not None:
            raise cls._stream_invalid()
        finish_reason = choice.get("finish_reason")
        if finish_reason not in (None, "stop", "length"):
            raise cls._stream_invalid()
        delta = choice["delta"]
        if not isinstance(delta, dict):
            raise cls._stream_invalid()
        allowed_delta_fields = {"role", "content", "reasoning", "reasoning_content"}
        if set(delta) - allowed_delta_fields:
            raise cls._stream_invalid()
        if "role" in delta and delta["role"] != "assistant":
            raise cls._stream_invalid()
        for field in ("content", "reasoning", "reasoning_content"):
            if field in delta and delta[field] is not None and not isinstance(delta[field], str):
                raise cls._stream_invalid()
        content = delta.get("content")
        if finish_reason == "length":
            raise coded_error(
                "incomplete result (finish_reason=length); increase max_tokens",
                'MODEL_OUTPUT_INCOMPLETE', 'provider.openai-compatible',
            )
        if content:
            return [content], finish_reason == "stop"
        return [], finish_reason == "stop"

    @staticmethod
    def _valid_stream_usage(usage):
        if not isinstance(usage, dict):
            return False
        allowed_fields = {"prompt_tokens", "completion_tokens", "total_tokens",
                          "completion_tokens_details"}
        if set(usage) - allowed_fields:
            return False
        token_fields = {"prompt_tokens", "completion_tokens", "total_tokens"}
        if not token_fields.intersection(usage):
            return False
        if any(not _is_nonnegative_int(usage[field]) for field in token_fields if field in usage):
            return False
        if "completion_tokens_details" in usage:
            details = usage["completion_tokens_details"]
            if not isinstance(details, dict) or set(details) != {"reasoning_tokens"} \
                    or not _is_nonnegative_int(details["reasoning_tokens"]):
                return False
        return True

    def send(self, data):
        """POST already-encoded bytes once, so callers can digest/cap exactly what is sent."""
        if type(data) is not bytes:
            raise LLMToolError("request body must be encoded bytes")
        self.validate_ready(self.provider)
        url, headers = self._request_target()
        return self._send(data, url, headers)

    def _request_target(self):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        return self.provider["base_url"].rstrip("/") + "/v1/chat/completions", headers

    def _send(self, data, url, headers):
        req = request.Request(
            url,
            data=data, headers=headers, method="POST",
        )
        start = time.monotonic()
        try:
            with self.opener.open(req, timeout=self.provider["timeout_seconds"]) as response:
                if not 200 <= response.status < 300:
                    raise coded_error(
                        "gateway returned a non-success HTTP status",
                        http_error_code(response.status),
                        'provider.openai-compatible',
                        retryable=response.status == 429,
                        user_action_required=response.status in (401, 403),
                        safe_cause={"http_status": response.status},
                    )
                raw = response.read(RESPONSE_LIMIT + 1)
        except error.HTTPError as exc:
            status = exc.code
            exc.close()
            raise coded_error(
                "gateway HTTP " + str(status) + "; response body omitted",
                http_error_code(status),
                'provider.openai-compatible',
                retryable=status == 429,
                user_action_required=status in (401, 403),
                safe_cause={"http_status": status},
            ) from None
        except (TimeoutError, socket.timeout):
            raise coded_error(
                "gateway request timed out",
                'PROVIDER_TIMEOUT',
                'provider.openai-compatible',
                retryable=True,
            ) from None
        except error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise coded_error(
                    "gateway request timed out",
                    'PROVIDER_TIMEOUT',
                    'provider.openai-compatible',
                    retryable=True,
                ) from None
            raise coded_error(
                "cannot connect to gateway; check network, base_url, and TLS",
                'NETWORK_PROVIDER_UNREACHABLE',
                'provider.openai-compatible',
                retryable=True,
            ) from None
        except (OSError, http.client.HTTPException, ValueError, OverflowError):
            raise coded_error(
                "gateway transport failed; details omitted",
                'PROVIDER_RUNTIME_ERROR',
                'provider.openai-compatible',
            ) from None
        if len(raw) > RESPONSE_LIMIT:
            raise coded_error(
                "gateway response exceeds 16 MiB",
                'MODEL_RESPONSE_TOO_LARGE',
                'provider.openai-compatible',
            )
        try:
            obj = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
        except (ValueError, UnicodeError, RecursionError):
            raise coded_error(
                "gateway returned invalid JSON",
                'MODEL_RESPONSE_INVALID',
                'provider.openai-compatible',
            ) from None
        if not isinstance(obj, dict):
            raise coded_error(
                "gateway response must be a JSON object",
                'MODEL_RESPONSE_INVALID',
                'provider.openai-compatible',
            )
        # Only safely decoded envelopes can provide failure accounting. Select
        # numeric fields before handing metadata to the typed-error boundary.
        provider_usage = None
        usage = obj.get('usage')
        if type(usage) is dict:
            try:
                from ..diagnostics import PROVIDER_USAGE_FIELDS, validate_provider_usage
                provider_usage = validate_provider_usage(
                    {name: usage.get(name) for name in PROVIDER_USAGE_FIELDS})
            except Exception:
                pass  # Preserve failure classification even if accounting is invalid.
        if obj.get("error") is not None:
            raise coded_error(
                "gateway returned an API error; response body omitted",
                'MODEL_RESPONSE_INVALID',
                'provider.openai-compatible',
                provider_metadata=provider_usage,
            )
        choices = obj.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise coded_error(
                "gateway response is missing valid choices",
                'MODEL_RESPONSE_INVALID',
                'provider.openai-compatible',
                provider_metadata=provider_usage,
            )
        choice = choices[0]
        if choice.get("finish_reason") == "length":
            raise coded_error(
                "incomplete result (finish_reason=length); increase max_tokens",
                'MODEL_OUTPUT_INCOMPLETE',
                'provider.openai-compatible',
                provider_metadata=provider_usage,
            )
        if choice.get("finish_reason") != "stop":
            raise coded_error(
                "gateway response did not finish normally (expected finish_reason=stop)",
                'MODEL_RESPONSE_INVALID',
                'provider.openai-compatible',
                provider_metadata=provider_usage,
            )
        message = choice.get("message")
        if not isinstance(message, dict) or message.get("tool_calls") or message.get("function_call"):
            raise coded_error(
                "gateway returned an invalid or tool-call message",
                'MODEL_RESPONSE_INVALID',
                'provider.openai-compatible',
                provider_metadata=provider_usage,
            )
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise coded_error(
                "gateway returned empty or invalid final content",
                'MODEL_RESPONSE_INVALID',
                'provider.openai-compatible',
                provider_metadata=provider_usage,
            )
        content = content.strip()
        try:
            content.encode("utf-8")
        except UnicodeError:
            raise coded_error(
                "gateway returned invalid Unicode content",
                'MODEL_RESPONSE_INVALID',
                'provider.openai-compatible',
                provider_metadata=provider_usage,
            ) from None
        # No reasoning fields or raw response are retained in Reply.
        metadata = {"elapsed_seconds": round(time.monotonic() - start, 3)}
        model = obj.get("model")
        if (isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9_./:-]{1,128}", model)
                and not (self.key and self.key in model)
                and not re.search(r"(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{20,}|"
                                  r"github_pat_[A-Za-z0-9_]{20,}|xox[abprs]-[A-Za-z0-9-]{20,})",
                                  model, re.IGNORECASE)):
            metadata["model"] = model
            metadata["served_model"] = model
        # Success usage is the same complete, validated triplet as P1 failure
        # usage; partial or inconsistent usage stays unknown, never partial or 0.
        if provider_usage is not None:
            metadata.update(provider_usage)
        return Reply(content, metadata)
