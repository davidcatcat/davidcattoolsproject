"""#1160 WP6: read-only Telegram channel text over existing Core summaries.

Pure functions only. Input is a ``task_status()`` result (and optionally a
``read_attention()`` result); output is screened text and notification events.
``deliver`` sends through an injected transport with injected claim/release
callables. This module owns no transport, network, CLI, loop, approval, Task
creation, PM call, inbox write or STOP, and never takes, stores or prints a
Telegram chat ID or bot credential; callers name a chat by a local alias only.

Error messages are fixed codes and never contain input values.
"""

import hashlib
import json
import re

from .diagnostics import safe_diagnostic_text
from .errors import LLMToolError
from .test_output_excerpts import _emitted_sensitive

HOST_STORE_UNAVAILABLE = "HOST_STORE_UNAVAILABLE"
TASK_NOT_FOUND = "TASK_NOT_FOUND"
CONTENT_WITHHELD = "CONTENT_WITHHELD"

STATES = frozenset(("RUNNING", "BLOCKED", "COMPLETED", "FAILED", "UNAVAILABLE", "UNKNOWN",
                    "ABANDONED"))
NOTIFY_STATES = frozenset(("BLOCKED", "FAILED", "UNKNOWN", "UNAVAILABLE"))
EVENT_KINDS = frozenset(("task_state", "unreadable", "owner_waiting"))

CODE_CHARS = 12
LIMIT_MAX = 20
MAX_MESSAGE_CHARS = 3500
MORE_LINE = "+more"
MAX_MESSAGES_MAX = 10
RETRY_AFTER_MIN, RETRY_AFTER_MAX = 1, 3600
# Versioned so a future change of key inputs never collides with stored claims.
KEY_PREFIX = "llmtool.telegram_channel.notification.v1\n"

_TASK_ID = re.compile(r"[0-9a-f]{32}")
_CODE = re.compile(r"[0-9a-f]{12}")
_KEY = re.compile(r"[0-9a-f]{64}")
_CHAT_ALIAS = re.compile(r"[a-z][a-z0-9_-]{0,31}")
# Telegram bot token shape: digits, colon, 30 or more token characters.
_BOT_TOKEN = re.compile(r"[0-9]+:[A-Za-z0-9_-]{30,}")


def _error(code):
    return LLMToolError(code)


def _count(value):
    """A non-negative int, else None (bool is not a count)."""
    return value if type(value) is int and value >= 0 else None


def _shown(value):
    return "?" if value is None else str(value)


def _tasks(status):
    if type(status) is not dict or type(status.get("tasks")) is not list:
        return None
    return status["tasks"]


def _recognised(item):
    """(task_id, state, pending or None) for a recognised Task summary, else None."""
    if type(item) is not dict:
        return None
    task_id, state = item.get("task_id"), item.get("state")
    if type(task_id) is not str or _TASK_ID.fullmatch(task_id) is None:
        return None
    if type(state) is not str or state not in STATES:
        return None
    return task_id, state, _count(item.get("pending_inbox_count"))


def _line(task_id, state, pending):
    return task_id[:CODE_CHARS] + " " + state + " pending=" + _shown(pending)


def _cut(text):
    """At most MAX_MESSAGE_CHARS, cut at a line end with a final +more line."""
    if len(text) <= MAX_MESSAGE_CHARS:
        return text
    budget = MAX_MESSAGE_CHARS - len("\n" + MORE_LINE)
    kept, size = [], 0
    for line in text.split("\n"):
        extra = len(line) + (1 if kept else 0)
        if size + extra > budget:
            break
        kept.append(line)
        size += extra
    return "\n".join(kept + [MORE_LINE])


def _screen_hit(text):
    try:
        for line in text.split("\n"):
            safe_diagnostic_text(line)
    except ValueError:
        return True
    return bool(_emitted_sensitive(text)) or _BOT_TOKEN.search(text) is not None


def outbound_text(text):
    """The exact text that may leave the process, or CONTENT_WITHHELD.

    Both the full text and the cut message are screened, so a hit anywhere withholds
    the whole message rather than redacting or dropping part of it.
    """
    if type(text) is not str:
        return CONTENT_WITHHELD
    message = _cut(text)
    if _screen_hit(text) or _screen_hit(message):
        return CONTENT_WITHHELD
    return message


def _attention_line(attention):
    if type(attention) is not dict:
        items = unread = unreadable = None
    else:
        raw = attention.get("items")
        items = len(raw) if type(raw) is list else None
        unread = _count(attention.get("unread"))
        task_bad = _count(attention.get("task_unreadable_count"))
        inbox_bad = _count(attention.get("inbox_unreadable_count"))
        unreadable = None if task_bad is None or inbox_bad is None else task_bad + inbox_bad
    return ("Attention: items=" + _shown(items) + " unread=" + _shown(unread)
            + " unreadable=" + _shown(unreadable))


def render_status(status, attention=None, limit=10):
    """Header, optional attention counts, then one line per recognised Task."""
    if type(limit) is not int or not 1 <= limit <= LIMIT_MAX:
        raise _error("TELEGRAM_LIMIT_INVALID")
    tasks = _tasks(status)
    if tasks is None:
        return outbound_text(HOST_STORE_UNAVAILABLE)
    recognised, unrecognised = [], 0
    for item in tasks:
        found = _recognised(item)
        if found is None:
            unrecognised += 1
        else:
            recognised.append(found)
    lines = ["Tasks: %d unreadable=%s" % (len(tasks), _shown(_count(status.get("unreadable"))))]
    if attention is not None:
        lines.append(_attention_line(attention))
    lines += [_line(*found) for found in recognised[:limit]]
    if len(recognised) > limit:
        lines.append("+%d more" % (len(recognised) - limit))
    if unrecognised:
        lines.append("unrecognized=%d" % unrecognised)
    return outbound_text("\n".join(lines))


def render_task(status, code):
    """One Task line for a 12-char lowercase hex code (first match), or TASK_NOT_FOUND."""
    tasks = _tasks(status)
    if tasks is None:
        return outbound_text(HOST_STORE_UNAVAILABLE)
    if type(code) is str and _CODE.fullmatch(code) is not None:
        for item in tasks:
            found = _recognised(item)
            if found is not None and found[0][:CODE_CHARS] == code:
                return outbound_text(_line(*found))
    return outbound_text(TASK_NOT_FOUND)


def _key(kind, *inputs):
    payload = json.dumps([kind, *inputs], ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256((KEY_PREFIX + payload).encode("ascii")).hexdigest()


def _received_at(item):
    event = item.get("last_event")
    if type(event) is dict and type(event.get("received_at")) is str:
        return event["received_at"]
    return None


def notification_events(status, owner_waiting=()):
    """Deterministic [{key, kind, text}]: Task states, then unreadable, then owner_waiting.

    ``owner_waiting`` is a list or tuple of opaque strings; only their count is shown.
    """
    if type(owner_waiting) not in (list, tuple) or any(type(item) is not str
                                                       for item in owner_waiting):
        raise _error("TELEGRAM_OWNER_WAITING_INVALID")
    tasks = _tasks(status)
    if tasks is None:
        raise _error(HOST_STORE_UNAVAILABLE)
    events, seen = [], set()

    def add(key, kind, text):
        if key not in seen:
            seen.add(key)
            events.append({"key": key, "kind": kind, "text": outbound_text(text)})

    for item in tasks:
        found = _recognised(item)
        if found is None or found[1] not in NOTIFY_STATES:
            continue
        task_id, state, _pending = found
        add(_key("task_state", task_id, state, _received_at(item)), "task_state",
            "Task " + task_id[:CODE_CHARS] + " " + state)
    unreadable = _count(status.get("unreadable"))
    if unreadable:
        add(_key("unreadable", unreadable), "unreadable", "Unreadable Tasks: %d" % unreadable)
    if owner_waiting:
        add(_key("owner_waiting", sorted(owner_waiting)), "owner_waiting",
            "Owner waiting: %d" % len(owner_waiting))
    return events


def _validated_events(events):
    if type(events) not in (list, tuple):
        raise _error("TELEGRAM_EVENTS_INVALID")
    result, seen = [], set()
    for item in events:
        if (type(item) is not dict or type(item.get("key")) is not str
                or _KEY.fullmatch(item["key"]) is None
                or type(item.get("kind")) is not str or item["kind"] not in EVENT_KINDS
                or type(item.get("text")) is not str):
            raise _error("TELEGRAM_EVENTS_INVALID")
        if item["key"] not in seen:
            seen.add(item["key"])
            result.append((item["key"], item["text"]))
    return result


def _result(status, sent, sent_keys, released_keys, retry_after=None):
    result = {"status": status, "sent": sent, "released": len(released_keys),
              "sent_keys": list(sent_keys), "released_keys": list(released_keys)}
    if retry_after is not None:
        result["retry_after"] = retry_after
    return result


def deliver(transport, chat_alias, events, claim, release, max_messages=3):
    """Send newly claimed events in order through ``transport.send(chat_alias, text)``.

    ``claim(keys)`` returns the newly claimed keys; only those events are sent. Above
    ``max_messages`` the last message is one ``+N more notifications`` summary covering
    the rest. Result: status (SENT, IDLE, OFFLINE, RATE_LIMITED or UNKNOWN), ``sent``
    messages, ``released`` keys, the keys themselves and, for RATE_LIMITED,
    ``retry_after``. It never holds message text.

    FAILED or any unrecognised reply releases the keys not yet sent and stops (OFFLINE);
    RATE_LIMITED does the same with a clamped retry_after. If send raises, nothing is
    released, so a message that may have gone out is never resent (UNKNOWN).
    """
    if type(chat_alias) is not str or _CHAT_ALIAS.fullmatch(chat_alias) is None:
        raise _error("TELEGRAM_CHAT_ALIAS_INVALID")
    if type(max_messages) is not int or not 1 <= max_messages <= MAX_MESSAGES_MAX:
        raise _error("TELEGRAM_MAX_MESSAGES_INVALID")
    if not callable(getattr(transport, "send", None)):
        raise _error("TELEGRAM_TRANSPORT_INVALID")
    if not callable(claim) or not callable(release):
        raise _error("TELEGRAM_CLAIM_INVALID")
    pending = _validated_events(events)
    if not pending:
        return _result("IDLE", 0, [], [])
    try:
        claimed = claim([key for key, _text in pending])
    except Exception:
        raise _error("TELEGRAM_CLAIM_FAILED") from None
    if (type(claimed) not in (list, tuple, set, frozenset)
            or any(type(key) is not str for key in claimed)):
        raise _error("TELEGRAM_CLAIM_INVALID")
    claimed = set(claimed)
    selected = [(key, text) for key, text in pending if key in claimed]
    if not selected:
        return _result("IDLE", 0, [], [])

    if len(selected) > max_messages:
        head, rest = selected[:max_messages - 1], selected[max_messages - 1:]
        messages = [([key], text) for key, text in head]
        messages.append(([key for key, _text in rest], "+%d more notifications" % len(rest)))
    else:
        messages = [([key], text) for key, text in selected]

    sent, sent_keys = 0, []
    for index, (keys, text) in enumerate(messages):
        try:
            reply = transport.send(chat_alias, outbound_text(text))
        except Exception:
            return _result("UNKNOWN", sent, sent_keys, [])
        state = reply.get("status") if type(reply) is dict else None
        if state == "SENT":
            sent += 1
            sent_keys += keys
            continue
        retry_after = reply.get("retry_after") if type(reply) is dict else None
        unsent = [key for item_keys, _text in messages[index:] for key in item_keys]
        released = _release(release, unsent)
        if state == "RATE_LIMITED" and type(retry_after) is int:
            clamped = min(RETRY_AFTER_MAX, max(RETRY_AFTER_MIN, retry_after))
            return _result("RATE_LIMITED", sent, sent_keys, released, clamped)
        return _result("OFFLINE", sent, sent_keys, released)
    return _result("SENT", sent, sent_keys, [])


def _release(release, keys):
    """Released keys; none are reported when release raises (state then unknown)."""
    try:
        release(list(keys))
    except Exception:
        return []
    return list(keys)
