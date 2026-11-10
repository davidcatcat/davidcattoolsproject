"""Model adapters. Phase 1 implements ``openai-chat-tools`` only.

The HTTP client uses ``http.client`` directly: no ambient proxies, no
redirects, TLS verification on, and plain HTTP only to loopback hosts.
"""
from __future__ import annotations

import errno
import http.client
import json
import ssl
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlsplit

from .contract import Profile

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_TOOL_CALLS = 16


class ModelError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class ModelReply:
    message: dict
    served_model: str
    usage: Optional[dict]
    finish_reason: Optional[str]


def _clean_message(msg: Any) -> dict:
    if not isinstance(msg, dict):
        raise ModelError('MALFORMED_RESPONSE')
    content = msg.get('content')
    if content is not None and not isinstance(content, str):
        raise ModelError('MALFORMED_RESPONSE')
    out: dict = {'role': 'assistant', 'content': content}
    calls = msg.get('tool_calls')
    if calls:
        if not isinstance(calls, list):
            raise ModelError('MALFORMED_RESPONSE')
        clean = []
        for call in calls[:MAX_TOOL_CALLS * 4]:
            if not isinstance(call, dict):
                raise ModelError('MALFORMED_RESPONSE')
            fn = call.get('function')
            cid = call.get('id')
            if (not isinstance(cid, str) or not cid or len(cid) > 256 or not isinstance(fn, dict)
                    or not isinstance(fn.get('name'), str) or not isinstance(fn.get('arguments', ''), str)):
                raise ModelError('MALFORMED_RESPONSE')
            clean.append({'id': cid, 'type': 'function',
                          'function': {'name': fn['name'][:128], 'arguments': fn.get('arguments') or ''}})
        out['tool_calls'] = clean
    return out


def _usage(raw: Any) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    pt, ct = raw.get('prompt_tokens'), raw.get('completion_tokens')
    if type(pt) is not int or type(ct) is not int or pt < 0 or ct < 0:
        return None
    return {'input_tokens': pt, 'output_tokens': ct}


class OpenAIChatTools:
    name = 'openai-chat-tools'

    def __init__(self, profile: Profile, api_key: Optional[str]):
        self.profile = profile
        self._api_key = api_key
        parts = urlsplit(profile.base_url)
        self._scheme = parts.scheme
        self._host = parts.hostname
        self._port = parts.port
        self._path = (parts.path or '') + '/chat/completions'

    def call(self, body_bytes: bytes, timeout: float) -> ModelReply:
        if self._scheme == 'https':
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                self._host, self._port, timeout=timeout, context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(self._host, self._port, timeout=timeout)
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        if self._api_key:
            headers['Authorization'] = 'Bearer ' + self._api_key
        try:
            deadline = time.monotonic() + timeout
            conn.request('POST', self._path, body=body_bytes, headers=headers)
            resp = conn.getresponse()
            status = resp.status
            # Total deadline: a server dripping bytes cannot stretch the wall budget.
            chunks, total = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ModelError('TIMEOUT')
                if conn.sock is not None:
                    conn.sock.settimeout(remaining)
                chunk = resp.read1(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    break
            data = b''.join(chunks)
        except (OSError, http.client.HTTPException) as exc:
            # Network timeout first: an errno of ETIMEDOUT is a timeout at the socket layer.
            if getattr(exc, 'errno', None) == errno.ETIMEDOUT:
                code = 'NETWORK_TIMEOUT'
            # Then plain timeouts, then any other transport failure.
            elif isinstance(exc, TimeoutError) or 'timed out' in str(exc):
                code = 'TIMEOUT'
            else:
                code = 'TRANSPORT_ERROR'
            raise ModelError(code) from exc
        finally:
            conn.close()
        if 300 <= status < 400:
            raise ModelError('HTTP_REDIRECT_REFUSED')
        if status != 200:
            raise ModelError(f'HTTP_{status}')
        if len(data) > MAX_RESPONSE_BYTES:
            raise ModelError('RESPONSE_TOO_LARGE')
        try:
            obj = json.loads(data.decode('utf-8'))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ModelError('MALFORMED_RESPONSE') from exc
        if not isinstance(obj, dict):
            raise ModelError('MALFORMED_RESPONSE')
        choices = obj.get('choices')
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ModelError('MALFORMED_RESPONSE')
        served = obj.get('model')
        served = served[:200] if isinstance(served, str) and served else 'UNKNOWN'
        reason = choices[0].get('finish_reason')
        return ModelReply(_clean_message(choices[0].get('message')), served, _usage(obj.get('usage')),
                          reason if isinstance(reason, str) else None)


def make_adapter(profile: Profile, api_key: Optional[str]):
    if profile.adapter == 'openai-chat-tools':
        return OpenAIChatTools(profile, api_key)
    # ``text-tools`` is refused at profile parse time in phase 1.
    raise NotImplementedError('text-tools adapter is not implemented in phase 1')
