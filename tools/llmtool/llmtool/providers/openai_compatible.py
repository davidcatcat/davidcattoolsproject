"""Single non-streaming request; never execute tools or returned content."""

import http.client
import json
import re
import socket
import time
from urllib import error, request

from ..errors import LLMToolError
from .base import Reply

RESPONSE_LIMIT = 16 * 1024 * 1024


def _reject_json_constant(value):
    raise ValueError("non-standard JSON number")


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OpenAICompatibleClient:
    @staticmethod
    def validate_ready(provider):
        for field in ("base_url", "model"):
            if provider.get(field) is None:
                raise LLMToolError("provider " + field + " is not configured")

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

    def complete(self, messages):
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
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        req = request.Request(
            self.provider["base_url"].rstrip("/") + "/v1/chat/completions",
            data=json.dumps(body, ensure_ascii=True, allow_nan=False).encode("utf-8"),
            headers=headers, method="POST",
        )
        start = time.monotonic()
        try:
            with self.opener.open(req, timeout=self.provider["timeout_seconds"]) as response:
                if not 200 <= response.status < 300:
                    raise LLMToolError("gateway returned a non-success HTTP status")
                raw = response.read(RESPONSE_LIMIT + 1)
        except error.HTTPError as exc:
            status = exc.code
            exc.close()
            raise LLMToolError("gateway HTTP " + str(status) + "; response body omitted") from None
        except (TimeoutError, socket.timeout):
            raise LLMToolError("gateway request timed out") from None
        except error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise LLMToolError("gateway request timed out") from None
            raise LLMToolError("cannot connect to gateway; check network, base_url, and TLS") from None
        except (OSError, http.client.HTTPException, ValueError, OverflowError):
            raise LLMToolError("gateway transport failed; details omitted") from None
        if len(raw) > RESPONSE_LIMIT:
            raise LLMToolError("gateway response exceeds 16 MiB")
        try:
            obj = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
        except (ValueError, UnicodeError, RecursionError):
            raise LLMToolError("gateway returned invalid JSON") from None
        if not isinstance(obj, dict):
            raise LLMToolError("gateway response must be a JSON object")
        if "error" in obj:
            raise LLMToolError("gateway returned an API error; response body omitted")
        choices = obj.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise LLMToolError("gateway response is missing valid choices")
        choice = choices[0]
        if choice.get("finish_reason") == "length":
            raise LLMToolError("incomplete result (finish_reason=length); increase max_tokens")
        if choice.get("finish_reason") != "stop":
            raise LLMToolError("gateway response did not finish normally (expected finish_reason=stop)")
        message = choice.get("message")
        if not isinstance(message, dict) or message.get("tool_calls") or message.get("function_call"):
            raise LLMToolError("gateway returned an invalid or tool-call message")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise LLMToolError("gateway returned empty or invalid final content")
        content = content.strip()
        try:
            content.encode("utf-8")
        except UnicodeError:
            raise LLMToolError("gateway returned invalid Unicode content") from None
        # No reasoning fields or raw response are retained in Reply.
        metadata = {"elapsed_seconds": round(time.monotonic() - start, 3)}
        model = obj.get("model")
        if isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9_./:-]{1,128}", model):
            metadata["model"] = "[redacted]" if self.key and self.key in model else model
        usage = obj.get("usage")
        if isinstance(usage, dict):
            for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = usage.get(field)
                if type(value) is int and 0 <= value <= 10**12:
                    metadata[field] = value
        return Reply(content, metadata)
