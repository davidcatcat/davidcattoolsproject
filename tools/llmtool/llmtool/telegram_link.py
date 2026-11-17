"""#1160 wiring slice step A: one inbound poll and one notification pass.

``run_once`` connects the injected Bot API transport to the read-only inbound
poll (``telegram_instruction.poll_once``) and, after it, to the notification
path (``telegram_channel.notification_events`` plus ``deliver`` over the Core
task status). The caller injects every effect: the HTTP call, the chat
resolver, the allowlist, the clock, the rate limiter, the status reader and
both state paths. This module holds no token or URL and imports no socket,
HTTP, URL or subprocess module; it has no CLI command and never calls the
chat resolver itself.

It never stores, returns, logs or prints a chat value, user id or message
text. Its result holds only the poll result and the deliver result, each made
of statuses, counts and keys. Error messages are fixed codes without values.
"""

from . import (
    telegram_bot_api,
    telegram_channel,
    telegram_cursor,
    telegram_instruction,
)
from .errors import LLMToolError

ARGUMENT_INVALID = 'TELEGRAM_LINK_ARGUMENT_INVALID'
HOST_STORE_UNAVAILABLE = telegram_channel.HOST_STORE_UNAVAILABLE

__all__ = ['ARGUMENT_INVALID', 'HOST_STORE_UNAVAILABLE', 'run_once']


def _require_callable(value):
    if not callable(value):
        raise LLMToolError(ARGUMENT_INVALID) from None


def _unavailable(poll):
    return {'poll': poll,
            'notify': {'status': HOST_STORE_UNAVAILABLE, 'sent': 0}}


def run_once(http_call, resolve_chat, *, alias, is_allowed, cursor_path,
             inbox_path, clock, limiter, status_reader, owner_waiting=(),
             max_updates=20, max_messages=3):
    """Poll once, then send notification events; return poll and notify results.

    Inbound updates only reach the inbox through ``telegram_instruction.poll_once``
    and its gates; this function reads no update content and takes no other
    action on updates. If the status reader or ``notification_events`` raises,
    nothing is sent and ``notify`` is the fixed HOST_STORE_UNAVAILABLE status.
    """
    _require_callable(http_call)
    _require_callable(resolve_chat)
    _require_callable(status_reader)
    transport = telegram_bot_api.BotApiTransport(http_call, resolve_chat)
    poll = telegram_instruction.poll_once(
        transport, alias=alias, is_allowed=is_allowed, cursor_path=cursor_path,
        inbox_path=inbox_path, clock=clock, limiter=limiter,
        max_updates=max_updates)
    try:
        status = status_reader()
        events = telegram_channel.notification_events(status, owner_waiting)
    except Exception:
        return _unavailable(poll)

    def claim(keys):
        return telegram_cursor.claim(cursor_path, alias, keys)

    def release(keys):
        return telegram_cursor.release(cursor_path, alias, keys)

    notify = telegram_channel.deliver(
        transport, alias, events, claim, release, max_messages)
    return {'poll': poll, 'notify': notify}
