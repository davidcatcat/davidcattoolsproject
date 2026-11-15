"""Owner instruction intake over Telegram updates (#1160 PR 3).

Purpose: turn one Telegram update into a fixed gate outcome or a command
reply for the private Owner instruction inbox and the local update cursor.
An injected allowlist callable decides the sender; an injected transport in
``poll_once`` is the only sender. There is no network, socket, HTTP or
subprocess use, nothing is printed or logged, and no bot token is ever read,
held or shown.

Gate order in ``handle_update`` (the first failure refuses):

1. The update must be a dict with an int ``update_id`` in 0..2**63-1
   (bool is not an int), else ``IGNORED_INVALID`` with no reply and no write.
2. ``update_id`` at or below the stored cursor offset is
   ``IGNORED_DUPLICATE`` with no reply and no write.
3. Every later outcome calls ``advance_offset`` after any inbox write, so a
   crash between the write and the offset is covered by the inbox ID dedupe.
4. Only a ``message`` update is handled; anything else is ``IGNORED_TYPE``.
5. Identity: private chat, ``from.is_bot`` false, sender equals chat, no
   forward or ``via_bot`` field, and the injected allowlist returns true;
   otherwise ``IGNORED_SENDER`` with no reply.
6. The message ``date`` must be an int within 120 s behind and 30 s ahead of
   the caller's ``now``; otherwise ``STALE_COMMAND``.
7. The injected rate limiter must allow the alias; otherwise ``RATE_LIMITED``.
8. A str ``text`` is required; without it the outcome is ``UNSUPPORTED``.

Commands are matched on the first word only; ``/cmd@botname`` is not a
command. Command outcomes are ``HANDLED`` when a command produced a reply,
``UNKNOWN_COMMAND`` for an unrecognised first word, and ``CONTENT_WITHHELD``
when the inbound screen refuses ``/do`` text. Codes and states are the only
content of a reply; stored text is data for the PM, not instructions to any
tool, and this module never runs Git, shell, merge, approval, dispatch or Task
creation.

Every error this module raises is an ``LLMToolError`` whose message is one
fixed ``TELEGRAM_INSTRUCTION_*`` code and never a value; cursor and inbox
errors keep their own fixed codes.
"""

import re

from . import owner_instruction_inbox, telegram_channel, telegram_cursor
from .errors import LLMToolError

__all__ = [
    'CODE_INVALID', 'CONTENT_WITHHELD', 'HANDLED', 'HELP_TEXT', 'IGNORED_DUPLICATE',
    'IGNORED_INVALID', 'IGNORED_SENDER', 'IGNORED_TYPE', 'MAX_FUTURE_SECONDS',
    'MAX_PAST_SECONDS', 'MAX_UPDATES', 'RATE_LIMITED', 'STALE_COMMAND',
    'UNKNOWN_COMMAND', 'UNSUPPORTED', 'RateLimiter', 'handle_update', 'poll_once',
]

MAX_PAST_SECONDS = 120
MAX_FUTURE_SECONDS = 30
MAX_UPDATES = 100
MAX_UPDATE_ID = 2 ** 63 - 1

IGNORED_INVALID = 'IGNORED_INVALID'
IGNORED_DUPLICATE = 'IGNORED_DUPLICATE'
IGNORED_TYPE = 'IGNORED_TYPE'
IGNORED_SENDER = 'IGNORED_SENDER'
STALE_COMMAND = 'STALE_COMMAND'
RATE_LIMITED = 'RATE_LIMITED'
UNSUPPORTED = 'UNSUPPORTED'
CONTENT_WITHHELD = 'CONTENT_WITHHELD'
HANDLED = 'HANDLED'
UNKNOWN_COMMAND = 'UNKNOWN_COMMAND'
CODE_INVALID = 'CODE_INVALID'

HELP_TEXT = '/do <text>\n/inbox [code]\n/revoke <code>\n/help'

ALIAS_INVALID = 'TELEGRAM_INSTRUCTION_ALIAS_INVALID'
ARGUMENT_INVALID = 'TELEGRAM_INSTRUCTION_ARGUMENT_INVALID'
TIME_INVALID = 'TELEGRAM_INSTRUCTION_TIME_INVALID'
LIMITER_INVALID = 'TELEGRAM_INSTRUCTION_LIMITER_INVALID'
LIMITER_FAILED = 'TELEGRAM_INSTRUCTION_LIMITER_FAILED'
CLOCK_INVALID = 'TELEGRAM_INSTRUCTION_CLOCK_INVALID'
TRANSPORT_INVALID = 'TELEGRAM_INSTRUCTION_TRANSPORT_INVALID'
MAX_UPDATES_INVALID = 'TELEGRAM_INSTRUCTION_MAX_UPDATES_INVALID'

_ALIAS = re.compile(r'[a-z][a-z0-9_-]{0,31}')
_CODE = re.compile(r'[0-9a-f]{12}')
_FORWARD_KEYS = ('forward_origin', 'forward_from', 'forward_from_chat',
                 'forward_date', 'via_bot')

_IGNORED = frozenset((IGNORED_INVALID, IGNORED_DUPLICATE, IGNORED_TYPE,
                      IGNORED_SENDER))


def _fail(code):
    return LLMToolError(code)


def _require(ok, code=ARGUMENT_INVALID):
    if not ok:
        raise _fail(code)


def _require_alias(alias):
    _require(type(alias) is str and _ALIAS.fullmatch(alias) is not None,
             ALIAS_INVALID)


def _require_now(now):
    _require(type(now) is int and now >= 0, TIME_INVALID)


def _valid_update(update):
    return (type(update) is dict and type(update.get('update_id')) is int
            and 0 <= update['update_id'] <= MAX_UPDATE_ID)


def _fresh(date, now):
    return (type(date) is int and now - date <= MAX_PAST_SECONDS
            and date - now <= MAX_FUTURE_SECONDS)


def _first_word(text):
    parts = text.split(None, 1)
    if not parts:
        return None, ''
    return parts[0], (parts[1].strip() if len(parts) > 1 else '')


class RateLimiter:
    """In-memory per-alias allowance of ``max_per_minute`` commands a minute."""

    WINDOW_SECONDS = 60

    def __init__(self, max_per_minute=10):
        _require(type(max_per_minute) is int and 1 <= max_per_minute <= 1000,
                 LIMITER_INVALID)
        self.max_per_minute = max_per_minute
        self._times = {}

    def allow(self, alias, now):
        """Record one command at ``now`` and return whether it is within rate."""
        _require_alias(alias)
        _require_now(now)
        recent = [stamp for stamp in self._times.get(alias, ())
                  if 0 <= now - stamp < self.WINDOW_SECONDS]
        if len(recent) >= self.max_per_minute:
            self._times[alias] = recent
            return False
        recent.append(now)
        self._times[alias] = recent
        return True


def _identity(update, message, is_allowed):
    chat = message.get('chat')
    sender = message.get('from')
    if type(chat) is not dict or type(sender) is not dict:
        return False
    if chat.get('type') != 'private' or sender.get('is_bot') is not False:
        return False
    chat_id, sender_id = chat.get('id'), sender.get('id')
    if type(chat_id) is not int or type(sender_id) is not int:
        return False
    if chat_id != sender_id:
        return False
    if any(name in message for name in _FORWARD_KEYS):
        return False
    try:
        return is_allowed(update) is True
    except Exception:
        return False


def _inbox_reply(code):
    if code == owner_instruction_inbox.DISABLED:
        return 'INBOX_DISABLED'
    if code == owner_instruction_inbox.FULL:
        return 'INBOX_FULL'
    return 'INBOX_UNAVAILABLE'


def _do(inbox_path, alias, update_id, arg, now):
    if not 1 <= len(arg) <= owner_instruction_inbox.MAX_TEXT:
        return HANDLED, 'DO_TEXT_INVALID'
    if telegram_channel._screen_hit(arg):
        return CONTENT_WITHHELD, 'CONTENT_WITHHELD'
    try:
        result = owner_instruction_inbox.add(inbox_path, alias, update_id, arg, now)
    except owner_instruction_inbox.OwnerInboxError as exc:
        return HANDLED, _inbox_reply(exc.code)
    return HANDLED, '%s %s' % (result['code'], result['state'])


def _inbox(inbox_path, alias, arg, now):
    code = arg or None
    if code is not None and _CODE.fullmatch(code) is None:
        return HANDLED, CODE_INVALID
    try:
        items = owner_instruction_inbox.status(inbox_path, alias, now, code=code)
    except owner_instruction_inbox.OwnerInboxError as exc:
        if exc.code == owner_instruction_inbox.NOT_FOUND:
            return HANDLED, 'NOT_FOUND'
        return HANDLED, 'INBOX_UNAVAILABLE'
    if not items:
        return HANDLED, 'EMPTY'
    return HANDLED, '\n'.join('%s %s' % (item['code'], item['state'])
                              for item in items)


def _revoke(inbox_path, alias, arg, now):
    if _CODE.fullmatch(arg) is None:
        return HANDLED, CODE_INVALID
    try:
        result = owner_instruction_inbox.revoke(inbox_path, alias, arg, now)
    except owner_instruction_inbox.OwnerInboxError as exc:
        if exc.code == owner_instruction_inbox.FULL:
            return HANDLED, 'INBOX_FULL'
        if exc.code == owner_instruction_inbox.NOT_FOUND:
            return HANDLED, 'NOT_FOUND'
        return HANDLED, 'INBOX_UNAVAILABLE'
    return HANDLED, '%s %s' % (result['code'], result['state'])


def _decide(update, update_id, alias, is_allowed, inbox_path, now, limiter):
    message = update.get('message')
    if type(message) is not dict:
        return IGNORED_TYPE, None
    if not _identity(update, message, is_allowed):
        return IGNORED_SENDER, None
    if not _fresh(message.get('date'), now):
        return STALE_COMMAND, 'STALE_COMMAND'
    try:
        allowed = limiter.allow(alias, now)
    except LLMToolError:
        raise
    except Exception:
        raise _fail(LIMITER_FAILED) from None
    if not allowed:
        return RATE_LIMITED, 'RATE_LIMITED'
    text = message.get('text')
    if type(text) is not str:
        return UNSUPPORTED, 'UNSUPPORTED'
    word, arg = _first_word(text)
    if word == '/do':
        return _do(inbox_path, alias, update_id, arg, now)
    if word == '/inbox':
        return _inbox(inbox_path, alias, arg, now)
    if word == '/revoke':
        return _revoke(inbox_path, alias, arg, now)
    if word == '/help':
        return HANDLED, HELP_TEXT
    return UNKNOWN_COMMAND, 'UNKNOWN_COMMAND'


def handle_update(update, *, alias, is_allowed, cursor_path, inbox_path, now,
                  limiter):
    """Return ``{'outcome': fixed code, 'reply': screened text or None}``."""
    _require_alias(alias)
    _require_now(now)
    _require(callable(is_allowed))
    _require(callable(getattr(limiter, 'allow', None)), LIMITER_INVALID)
    if not _valid_update(update):
        return {'outcome': IGNORED_INVALID, 'reply': None}
    update_id = update['update_id']
    if update_id <= telegram_cursor.offset(cursor_path, alias):
        return {'outcome': IGNORED_DUPLICATE, 'reply': None}
    outcome, reply = _decide(update, update_id, alias, is_allowed, inbox_path,
                             now, limiter)
    telegram_cursor.advance_offset(cursor_path, alias, update_id)
    if reply is not None:
        reply = telegram_channel.outbound_text(reply)
    return {'outcome': outcome, 'reply': reply}


def _sort_key(update):
    if type(update) is dict and type(update.get('update_id')) is int:
        return update['update_id']
    return -1


def _counts(status, received, handled, replied, ignored):
    return {'status': status, 'received': received, 'handled': handled,
            'replied': replied, 'ignored': ignored}


def poll_once(transport, *, alias, is_allowed, cursor_path, inbox_path, clock,
              limiter, max_updates=20):
    """Poll once through an injected transport and return status and counts.

    ``transport.get_updates`` receives ``offset`` = stored offset + 1,
    ``allowed_updates`` = ``['message']`` and ``limit`` = ``max_updates``.
    A raised or non-list reply means OFFLINE with zero counts and no write.
    A raised ``transport.send`` stops after the update it replied to and
    returns UNKNOWN; the cursor was already advanced, so it is not resent.
    """
    _require_alias(alias)
    _require(callable(is_allowed))
    _require(callable(clock), CLOCK_INVALID)
    _require(callable(getattr(limiter, 'allow', None)), LIMITER_INVALID)
    _require(type(max_updates) is int and 1 <= max_updates <= MAX_UPDATES,
             MAX_UPDATES_INVALID)
    _require(callable(getattr(transport, 'get_updates', None))
             and callable(getattr(transport, 'send', None)), TRANSPORT_INVALID)
    current = telegram_cursor.offset(cursor_path, alias)
    try:
        updates = transport.get_updates(offset=current + 1,
                                        allowed_updates=['message'],
                                        limit=max_updates)
    except Exception:
        return _counts('OFFLINE', 0, 0, 0, 0)
    if type(updates) is not list:
        return _counts('OFFLINE', 0, 0, 0, 0)
    received = handled = replied = ignored = 0
    for update in sorted(updates, key=_sort_key):
        now = clock()
        _require_now(now)
        result = handle_update(update, alias=alias, is_allowed=is_allowed,
                               cursor_path=cursor_path, inbox_path=inbox_path,
                               now=now, limiter=limiter)
        received += 1
        if result['outcome'] in _IGNORED:
            ignored += 1
        else:
            handled += 1
        if result['reply'] is None:
            continue
        try:
            transport.send(alias, result['reply'])
        except Exception:
            return _counts('UNKNOWN', received, handled, replied, ignored)
        replied += 1
    return _counts('OK', received, handled, replied, ignored)
