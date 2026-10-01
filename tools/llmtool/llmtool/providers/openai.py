"""Pure-text OpenAI Responses API provider using the standard library."""

import http.client
import json
import re
import socket
import time
from urllib import error, request

from ..errors import LLMToolError
from .base import ProviderCapabilities, Reply

API_ORIGIN = "https://api.openai.com"
RESPONSE_LIMIT = 16 * 1024 * 1024
REASONING_EFFORTS = frozenset(("none", "low", "medium", "high"))


def _reject_json_constant(value):
    raise ValueError("non-standard JSON number")


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OpenAIProvider:
    TYPE = "openai"
    CAPABILITIES = ProviderCapabilities(
        chat=True, large_context=True, reasoning_control=True,
        streaming=False, code_edit=False, shell=False, git=False,
    )

    @staticmethod
    def validate_config(provider):
        if provider.get("base_url") is not None:
            raise LLMToolError("openai provider does not support base_url")

    @staticmethod
    def validate_options(options):
        if not isinstance(options, dict) or set(options) - {"reasoning_effort"}:
            raise LLMToolError("unsupported openai provider_options")
        effort = options.get("reasoning_effort")
        if effort is not None and (not isinstance(effort, str) or effort not in REASONING_EFFORTS):
            raise LLMToolError("reasoning_effort must be none, low, medium, high, or null")

    @staticmethod
    def validate_ready(provider):
        if provider.get("model") is None:
            raise LLMToolError("provider model is not configured")
        if not provider.get("api_key") and not provider.get("api_key_env"):
            raise LLMToolError("provider authentication is not configured")

    def __init__(self, provider, generation, key=None):
        self.provider = provider
        self.generation = generation
        self.key = key
        # The production origin is fixed, and ambient proxy settings are ignored.
        self.opener = request.build_opener(request.ProxyHandler({}), NoRedirect())

    @staticmethod
    def _input(messages):
        if not isinstance(messages, list) or not messages:
            raise LLMToolError("invalid conversation input")
        result = []
        for message in messages:
            if (not isinstance(message, dict)
                    or message.get("role") not in ("system", "developer", "user", "assistant")
                    or not isinstance(message.get("content"), str)):
                raise LLMToolError("invalid conversation input")
            result.append({"role": message["role"], "content": message["content"]})
        return result

    @staticmethod
    def _final_text(response):
        if response.get("status") != "completed":
            raise LLMToolError("OpenAI response did not complete")
        if response.get("error") is not None:
            raise LLMToolError("OpenAI returned an API error; response body omitted")

        if "output" not in response:
            output_text = response.get("output_text")
            if not isinstance(output_text, str):
                raise LLMToolError("OpenAI response is missing valid output")
            output = None
            parts = [output_text]
        else:
            output = response["output"]
            parts = []
        if not isinstance(output, list):
            if output is not None:
                raise LLMToolError("OpenAI response is missing valid output")
        for item in output or []:
            if not isinstance(item, dict):
                raise LLMToolError("OpenAI response contains invalid output")
            item_type = item.get("type")
            if item_type == "reasoning":
                continue
            if item_type != "message" or item.get("role") != "assistant":
                # This provider requests no tools and never returns tool payloads.
                raise LLMToolError("OpenAI response contains unsupported output")
            if item.get("status") not in (None, "completed"):
                raise LLMToolError("OpenAI response message did not complete")
            content = item.get("content")
            if not isinstance(content, list):
                raise LLMToolError("OpenAI response contains invalid message content")
            for block in content:
                if not isinstance(block, dict):
                    raise LLMToolError("OpenAI response contains invalid message content")
                if block.get("type") == "output_text":
                    value = block.get("text")
                elif block.get("type") == "refusal":
                    value = block.get("refusal")
                else:
                    continue
                if not isinstance(value, str):
                    raise LLMToolError("OpenAI response contains invalid text")
                parts.append(value)

        content = "".join(parts).strip()
        if not content:
            raise LLMToolError("OpenAI response is missing final text")
        try:
            content.encode("utf-8")
        except UnicodeError:
            raise LLMToolError("OpenAI response contains invalid text") from None
        return content

    @staticmethod
    def _metadata(response, key, elapsed):
        metadata = {"elapsed_seconds": round(elapsed, 3)}
        model = response.get("model")
        if (isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9_./:-]{1,128}", model)
                and (not key or key not in model)):
            metadata["model"] = model
        if "usage" in response:
            usage = response["usage"]
            if not isinstance(usage, dict):
                raise LLMToolError("OpenAI response contains invalid usage metadata")
            for field in ("input_tokens", "output_tokens", "total_tokens"):
                if field not in usage:
                    continue
                value = usage[field]
                if type(value) is not int or not 0 <= value <= 10**12:
                    raise LLMToolError("OpenAI response contains invalid usage metadata")
                metadata[field] = value
        return metadata

    def complete(self, messages):
        self.validate_config(self.provider)
        self.validate_options(self.provider.get("provider_options", {}))
        self.validate_ready(self.provider)
        if not isinstance(self.key, str) or not self.key:
            raise LLMToolError("configured OpenAI API key is missing or empty")

        body = {
            "model": self.provider["model"],
            "input": self._input(messages),
            "max_output_tokens": self.generation["max_tokens"],
            "store": False,
        }
        effort = self.provider.get("provider_options", {}).get("reasoning_effort")
        if effort is not None:
            body["reasoning"] = {"effort": effort}

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": "Bearer " + self.key,
        }
        req = request.Request(
            API_ORIGIN + "/v1/responses",
            data=json.dumps(body, ensure_ascii=True, allow_nan=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        start = time.monotonic()
        try:
            with self.opener.open(req, timeout=self.provider["timeout_seconds"]) as response:
                if not 200 <= response.status < 300:
                    raise LLMToolError("OpenAI returned a non-success HTTP status")
                raw = response.read(RESPONSE_LIMIT + 1)
        except error.HTTPError as exc:
            status = exc.code
            exc.close()
            raise LLMToolError("OpenAI API HTTP " + str(status) + "; response body omitted") from None
        except (TimeoutError, socket.timeout):
            raise LLMToolError("OpenAI API request timed out") from None
        except error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise LLMToolError("OpenAI API request timed out") from None
            raise LLMToolError("cannot connect to OpenAI API; check network and TLS") from None
        except (OSError, http.client.HTTPException, ValueError, OverflowError):
            raise LLMToolError("OpenAI API transport failed; details omitted") from None

        if len(raw) > RESPONSE_LIMIT:
            raise LLMToolError("OpenAI response exceeds 16 MiB")
        try:
            result = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
        except (ValueError, UnicodeError, RecursionError):
            raise LLMToolError("OpenAI returned invalid JSON") from None
        if not isinstance(result, dict):
            raise LLMToolError("OpenAI response must be a JSON object")
        content = self._final_text(result)
        metadata = self._metadata(result, self.key, time.monotonic() - start)
        return Reply(content, metadata)
