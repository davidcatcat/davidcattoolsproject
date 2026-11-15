"""#1160: pure Telegram Bot API adapter for telegram_instruction.poll_once and
telegram_channel.deliver.

It implements their transport contract (get_updates and send) over two injected
callables. http_call(method_name, params) returns the decoded Bot API reply.
resolve_chat(alias) returns an opaque chat value. This module holds no token.
It has no URL or host and no socket or HTTP library. It never stores, prints or
returns a chat or user ID. It never surfaces provider text such as the reply
description. Every error is an LLMToolError whose message is one fixed
TELEGRAM_BOT_API_* code.

send returns the deliver contract. SENT means sent; FAILED means the reply says
it was not sent; RATE_LIMITED comes with retry_after. A failed or unreadable call
raises, because the message may have gone out.
"""

from .errors import LLMToolError

ARGUMENT_INVALID = 'TELEGRAM_BOT_API_ARGUMENT_INVALID'
CALL_FAILED = 'TELEGRAM_BOT_API_CALL_FAILED'
REPLY_INVALID = 'TELEGRAM_BOT_API_REPLY_INVALID'
RATE_LIMITED = 'TELEGRAM_BOT_API_RATE_LIMITED'
MAX_LIMIT = 100
MAX_OFFSET = 2 ** 63 - 1
MAX_TEXT = 4096


def _fail(code):
    raise LLMToolError(code) from None


def _retry_after(reply):
    params = reply.get('parameters')
    value = params.get('retry_after') if type(params) is dict else None
    return value if type(value) is int and value >= 0 else None


class BotApiTransport:
    """The poll_once and deliver transport over injected http_call and resolve_chat."""

    def __init__(self, http_call, resolve_chat):
        if not callable(http_call) or not callable(resolve_chat):
            _fail(ARGUMENT_INVALID)
        self._http_call = http_call
        self._resolve_chat = resolve_chat

    def _reply(self, method, params):
        try:
            reply = self._http_call(method, params)
        except Exception:
            _fail(CALL_FAILED)
        if type(reply) is not dict or type(reply.get('ok')) is not bool:
            _fail(REPLY_INVALID)
        return reply

    def get_updates(self, *, offset, allowed_updates, limit):
        ok_offset = type(offset) is int and 0 <= offset <= MAX_OFFSET
        ok_limit = type(limit) is int and 1 <= limit <= MAX_LIMIT
        if not ok_offset or not ok_limit or allowed_updates != ['message']:
            _fail(ARGUMENT_INVALID)
        params = {'offset': offset, 'allowed_updates': ['message'], 'limit': limit, 'timeout': 0}
        reply = self._reply('getUpdates', params)
        if reply['ok'] is not True:
            _fail(RATE_LIMITED if reply.get('error_code') == 429 else REPLY_INVALID)
        result = reply.get('result')
        if type(result) is not list:
            _fail(REPLY_INVALID)
        return result

    def send(self, alias, text):
        ok_text = type(text) is str and 0 < len(text) <= MAX_TEXT
        if type(alias) is not str or not alias or not ok_text:
            _fail(ARGUMENT_INVALID)
        try:
            chat = self._resolve_chat(alias)
        except Exception:
            chat = None
        if chat is None:
            return {'status': 'FAILED'}
        reply = self._reply('sendMessage', {'chat_id': chat, 'text': text})
        if reply['ok'] is True and type(reply.get('result')) is dict:
            return {'status': 'SENT'}
        if reply['ok'] is True:
            _fail(REPLY_INVALID)
        retry_after = _retry_after(reply)
        if reply.get('error_code') == 429 and retry_after is not None:
            return {'status': 'RATE_LIMITED', 'retry_after': retry_after}
        return {'status': 'FAILED'}
