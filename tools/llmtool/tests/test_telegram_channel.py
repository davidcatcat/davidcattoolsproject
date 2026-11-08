"""#1160 WP6: read-only Telegram channel rendering, screening and injected delivery."""

import ast
import hashlib
import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llmtool import telegram_channel as tc  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402


def tid(n):
    """Deterministic 32-char lowercase hex Task ID with distinct 12-char prefixes."""
    return hashlib.md5(("task-%d" % n).encode("ascii")).hexdigest()


def task(n, state="RUNNING", pending=0, received_at="2026-11-07T00:00:00Z"):
    return {
        "task_id": tid(n), "pm_session_id": "pm-private-session", "state": state,
        "executor_d6": "private-executor-d6",
        "last_event": None if received_at is None else {"state": "PRIVATE_EVENT",
                                                        "received_at": received_at},
        "review_claim_state": "PRIVATE_CLAIM", "pending_inbox_count": pending,
    }


def status(*tasks, unreadable=0):
    return {"tasks": list(tasks), "unreadable": unreadable}


class FakeTransport:
    """Records every send; replies in order (an exception instance is raised)."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def send(self, chat_alias, text):
        self.calls.append((chat_alias, text))
        reply = self.replies.pop(0) if self.replies else {"status": "SENT"}
        if isinstance(reply, BaseException):
            raise reply
        return reply


class FakeClaims:
    """Claim store: claim returns only keys not already held; release frees keys."""

    def __init__(self, held=(), only=None):
        self.held = set(held)
        self.only = only
        self.claim_calls = []
        self.release_calls = []

    def claim(self, keys):
        self.claim_calls.append(list(keys))
        new = [key for key in keys if key not in self.held
               and (self.only is None or key in self.only)]
        self.held.update(new)
        return new

    def release(self, keys):
        self.release_calls.append(list(keys))
        self.held.difference_update(keys)


def event(n, text=None):
    key = hashlib.sha256(("event-%d" % n).encode("ascii")).hexdigest()
    return {"key": key, "kind": "task_state", "text": text or "Task %012d BLOCKED" % n}


PRIVATE = ("pm-private-session", "private-executor-d6", "PRIVATE_EVENT", "PRIVATE_CLAIM",
           "2026-11-07T00:00:00Z")


class RenderStatusTests(unittest.TestCase):
    def test_header_and_one_line_per_task(self):
        text = tc.render_status(status(task(1, "RUNNING", 2), task(2, "BLOCKED", 0),
                                       unreadable=3))
        self.assertEqual(text.split("\n"), [
            "Tasks: 2 unreadable=3",
            tid(1)[:12] + " RUNNING pending=2",
            tid(2)[:12] + " BLOCKED pending=0",
        ])
        for value in PRIVATE + (tid(1), tid(2)):
            self.assertNotIn(value, text)

    def test_every_recognised_state(self):
        states = ("RUNNING", "BLOCKED", "COMPLETED", "FAILED", "UNAVAILABLE", "UNKNOWN",
                  "ABANDONED")
        text = tc.render_status(status(*[task(i, s) for i, s in enumerate(states)]))
        lines = text.split("\n")
        self.assertEqual(lines[0], "Tasks: 7 unreadable=0")
        self.assertEqual(lines[1:], [tid(i)[:12] + " " + s + " pending=0"
                                     for i, s in enumerate(states)])

    def test_limit_and_more_line(self):
        tasks = [task(i) for i in range(25)]
        lines = tc.render_status(status(*tasks)).split("\n")
        self.assertEqual(lines[0], "Tasks: 25 unreadable=0")
        self.assertEqual(len(lines), 1 + 10 + 1)
        self.assertEqual(lines[-1], "+15 more")
        self.assertEqual(lines[1], tid(0)[:12] + " RUNNING pending=0")
        lines = tc.render_status(status(*tasks), limit=20).split("\n")
        self.assertEqual(len(lines), 1 + 20 + 1)
        self.assertEqual(lines[-1], "+5 more")
        lines = tc.render_status(status(*tasks[:3]), limit=1).split("\n")
        self.assertEqual(lines, ["Tasks: 3 unreadable=0", tid(0)[:12] + " RUNNING pending=0",
                                 "+2 more"])
        lines = tc.render_status(status(*tasks[:3]), limit=3).split("\n")
        self.assertNotIn("more", lines[-1])

    def test_invalid_limit(self):
        for limit in (0, 21, -1, True, "5", 5.0, None):
            with self.assertRaises(LLMToolError) as caught:
                tc.render_status(status(task(1)), limit=limit)
            self.assertEqual(str(caught.exception), "TELEGRAM_LIMIT_INVALID")

    def test_empty_status(self):
        self.assertEqual(tc.render_status(status()), "Tasks: 0 unreadable=0")

    def test_unrecognised_tasks_are_only_counted(self):
        bad_id = "SECRETVALUE" + "0" * 21
        tasks = [
            task(1),
            dict(task(2), task_id=bad_id),
            dict(task(3), task_id=tid(3).upper()),
            dict(task(4), task_id=tid(4)[:31]),
            dict(task(5), task_id=tid(5) + "\n"),
            dict(task(6), state="WEIRD_STATE_VALUE"),
            dict(task(7), state="running"),
            dict(task(8), state=None),
            "not a dict",
            None,
            {"state": "RUNNING"},
        ]
        text = tc.render_status(status(*tasks))
        lines = text.split("\n")
        self.assertEqual(lines[0], "Tasks: 11 unreadable=0")
        self.assertEqual(lines[1], tid(1)[:12] + " RUNNING pending=0")
        self.assertEqual(lines[-1], "unrecognized=10")
        self.assertEqual(len(lines), 3)
        for value in ("SECRETVALUE", "WEIRD_STATE_VALUE", "running", "not a dict",
                      tid(3).upper()[:12], tid(6)[:12], tid(4)[:12]):
            self.assertNotIn(value, text)

    def test_unrecognised_do_not_use_the_limit(self):
        tasks = [dict(task(i), state="BAD") for i in range(5)] + [task(9)]
        lines = tc.render_status(status(*tasks), limit=1).split("\n")
        self.assertEqual(lines, ["Tasks: 6 unreadable=0", tid(9)[:12] + " RUNNING pending=0",
                                 "unrecognized=5"])

    def test_bad_pending_count(self):
        for pending in (-1, True, "3", None, 1.5, [1]):
            text = tc.render_status(status(task(1, pending=pending)))
            self.assertEqual(text.split("\n")[1], tid(1)[:12] + " RUNNING pending=?")
        missing = task(1)
        del missing["pending_inbox_count"]
        self.assertIn("pending=?", tc.render_status(status(missing)))

    def test_bad_unreadable_count(self):
        for unreadable in (-1, True, "2", None, 2.0):
            text = tc.render_status(status(task(1), unreadable=unreadable))
            self.assertEqual(text.split("\n")[0], "Tasks: 1 unreadable=?")
        self.assertEqual(tc.render_status({"tasks": []}), "Tasks: 0 unreadable=?")

    def test_unavailable_store(self):
        for value in (None, [], "tasks", {"unreadable": 0}, {"tasks": None},
                      {"tasks": "x"}, {"tasks": {}}):
            self.assertEqual(tc.render_status(value), "HOST_STORE_UNAVAILABLE")

    def test_attention_counts_only(self):
        attention = {
            "items": [{"label": "PRIVATE_LABEL", "task": tid(1), "reason": "PRIVATE_REASON"},
                      {"label": "卡住", "task": tid(2), "reason": "Developer: secret"}],
            "unread": 4, "task_unreadable_count": 1, "inbox_unreadable_count": 2,
        }
        text = tc.render_status(status(task(1)), attention=attention)
        lines = text.split("\n")
        self.assertEqual(lines[0], "Tasks: 1 unreadable=0")
        self.assertEqual(lines[1], "Attention: items=2 unread=4 unreadable=3")
        self.assertEqual(lines[2], tid(1)[:12] + " RUNNING pending=0")
        for value in ("PRIVATE_LABEL", "PRIVATE_REASON", "卡住", "Developer", "secret"):
            self.assertNotIn(value, text)

    def test_attention_malformed(self):
        for attention in ("x", [], {}, {"items": "x", "unread": -1,
                                         "task_unreadable_count": True,
                                         "inbox_unreadable_count": 0}):
            text = tc.render_status(status(task(1)), attention=attention)
            self.assertEqual(text.split("\n")[1], "Attention: items=? unread=? unreadable=?")
        partial = {"items": [], "unread": 0, "task_unreadable_count": 1,
                   "inbox_unreadable_count": "1"}
        self.assertEqual(tc.render_status(status(), attention=partial).split("\n")[1],
                         "Attention: items=0 unread=0 unreadable=?")

    def test_no_attention_line_without_attention(self):
        self.assertNotIn("Attention", tc.render_status(status(task(1))))

    def test_render_is_screened(self):
        with mock.patch.object(tc, "_emitted_sensitive", return_value=True):
            self.assertEqual(tc.render_status(status(task(1))), "CONTENT_WITHHELD")
        with mock.patch.object(tc, "safe_diagnostic_text", side_effect=ValueError("x")):
            self.assertEqual(tc.render_task(status(task(1)), tid(1)[:12]), "CONTENT_WITHHELD")


class RenderTaskTests(unittest.TestCase):
    def test_found(self):
        value = status(task(1, "FAILED", 5), task(2))
        self.assertEqual(tc.render_task(value, tid(1)[:12]), tid(1)[:12] + " FAILED pending=5")
        self.assertEqual(tc.render_task(value, tid(2)[:12]), tid(2)[:12] + " RUNNING pending=0")

    def test_bad_pending(self):
        self.assertEqual(tc.render_task(status(task(1, pending="x")), tid(1)[:12]),
                         tid(1)[:12] + " RUNNING pending=?")

    def test_not_found(self):
        value = status(task(1), dict(task(2), state="BAD"))
        for code in (tid(3)[:12], tid(2)[:12], tid(1), tid(1)[:11], tid(1)[:12].upper(),
                     "SECRET_VALUE", None, 12, ""):
            self.assertEqual(tc.render_task(value, code), "TASK_NOT_FOUND")

    def test_unavailable_store(self):
        for value in (None, [], "x", {"unreadable": 1}, {"tasks": None}, {"tasks": {}}):
            self.assertEqual(tc.render_task(value, tid(1)[:12]), "HOST_STORE_UNAVAILABLE")


class NotificationEventTests(unittest.TestCase):
    def test_task_state_events(self):
        value = status(task(1, "RUNNING"), task(2, "BLOCKED"), task(3, "FAILED"),
                       task(4, "UNKNOWN"), task(5, "UNAVAILABLE"), task(6, "COMPLETED"),
                       task(7, "ABANDONED"), dict(task(8), state="BLOCKEDX"),
                       dict(task(9, "BLOCKED"), task_id="X" * 32))
        events = tc.notification_events(value)
        self.assertEqual([e["kind"] for e in events], ["task_state"] * 4)
        self.assertEqual([e["text"] for e in events], [
            "Task " + tid(2)[:12] + " BLOCKED", "Task " + tid(3)[:12] + " FAILED",
            "Task " + tid(4)[:12] + " UNKNOWN", "Task " + tid(5)[:12] + " UNAVAILABLE"])
        for item in events:
            self.assertEqual(set(item), {"key", "kind", "text"})
            self.assertRegex(item["key"], r"\A[0-9a-f]{64}\Z")
            for private in PRIVATE + ("X" * 12,):
                self.assertNotIn(private, item["text"])

    def test_unreadable_and_owner_waiting_events(self):
        events = tc.notification_events(status(task(1, "BLOCKED"), unreadable=2),
                                         owner_waiting=("private-decision-a", "private-b"))
        self.assertEqual([e["kind"] for e in events],
                         ["task_state", "unreadable", "owner_waiting"])
        self.assertEqual(events[1]["text"], "Unreadable Tasks: 2")
        self.assertEqual(events[2]["text"], "Owner waiting: 2")
        for item in events:
            self.assertNotIn("private", item["text"])

    def test_no_unreadable_event_at_zero_or_malformed(self):
        for unreadable in (0, -1, None, "3", True):
            events = tc.notification_events(status(unreadable=unreadable))
            self.assertEqual(events, [])

    def test_owner_waiting_empty_or_invalid(self):
        self.assertEqual(tc.notification_events(status(), owner_waiting=[]), [])
        self.assertEqual(tc.notification_events(status()), [])
        for value in ("abc", None, [1], ["ok", None], {"a": 1}, b"ab"):
            with self.assertRaises(LLMToolError) as caught:
                tc.notification_events(status(), owner_waiting=value)
            self.assertEqual(str(caught.exception), "TELEGRAM_OWNER_WAITING_INVALID")

    def test_unavailable_store(self):
        for value in (None, [], {"tasks": None}):
            with self.assertRaises(LLMToolError) as caught:
                tc.notification_events(value)
            self.assertEqual(str(caught.exception), "HOST_STORE_UNAVAILABLE")

    def test_keys_are_stable_and_ordered(self):
        value = status(task(1, "BLOCKED"), task(2, "FAILED"), unreadable=1)
        first = tc.notification_events(value, owner_waiting=["b", "a"])
        second = tc.notification_events(json.loads(json.dumps(value)),
                                        owner_waiting=("a", "b"))
        self.assertEqual(first, second)
        self.assertEqual(len({e["key"] for e in first}), 4)

    def test_key_inputs(self):
        base = tc.notification_events(status(task(1, "BLOCKED")))[0]["key"]
        later = tc.notification_events(
            status(task(1, "BLOCKED", received_at="2026-11-08T00:00:00Z")))[0]["key"]
        none = tc.notification_events(status(task(1, "BLOCKED", received_at=None)))[0]["key"]
        failed = tc.notification_events(status(task(1, "FAILED")))[0]["key"]
        other = tc.notification_events(status(task(2, "BLOCKED")))[0]["key"]
        pending = tc.notification_events(status(task(1, "BLOCKED", pending=9)))[0]["key"]
        self.assertEqual(len({base, later, none, failed, other}), 5)
        self.assertEqual(base, pending)  # pending is not a key input
        one = tc.notification_events(status(unreadable=1))[0]["key"]
        two = tc.notification_events(status(unreadable=2))[0]["key"]
        self.assertNotEqual(one, two)
        a = tc.notification_events(status(), owner_waiting=["a"])[0]["key"]
        ab = tc.notification_events(status(), owner_waiting=["a", "b"])[0]["key"]
        self.assertNotEqual(a, ab)

    def test_key_format_is_versioned(self):
        self.assertRegex(tc.KEY_PREFIX, r"v1")
        key = tc.notification_events(status(unreadable=2))[0]["key"]
        expected = hashlib.sha256((tc.KEY_PREFIX + json.dumps(
            ["unreadable", 2], ensure_ascii=True, separators=(",", ":"))).encode("ascii"))
        self.assertEqual(key, expected.hexdigest())

    def test_duplicate_task_gives_one_event(self):
        events = tc.notification_events(status(task(1, "BLOCKED"), task(1, "BLOCKED")))
        self.assertEqual(len(events), 1)


class ScreenTests(unittest.TestCase):
    def test_clean_text_passes(self):
        self.assertEqual(tc.outbound_text("Tasks: 1 unreadable=0\nabc RUNNING pending=1"),
                         "Tasks: 1 unreadable=0\nabc RUNNING pending=1")
        self.assertEqual(tc.outbound_text("id 123456789:" + "a" * 29),
                         "id 123456789:" + "a" * 29)

    def test_screens_are_the_core_functions(self):
        from llmtool import diagnostics, test_output_excerpts
        self.assertIs(tc.safe_diagnostic_text, diagnostics.safe_diagnostic_text)
        self.assertIs(tc._emitted_sensitive, test_output_excerpts._emitted_sensitive)

    def test_line_screen_hits(self):
        for text in ("ok\nbell \x07 here", "tab\there", "ok\n\nblank line", "", "  ",
                     "ok\r\nx", "x" * 2049):
            self.assertEqual(tc.outbound_text(text), "CONTENT_WITHHELD", repr(text))

    def test_line_screen_credential_hit(self):
        # A credential found by safe_diagnostic_text on any one line withholds it all.
        def screen(line):
            if line == "flagged line":
                raise ValueError("credential")
            return line
        with mock.patch.object(tc, "safe_diagnostic_text", side_effect=screen) as patched:
            self.assertEqual(tc.outbound_text("first\nflagged line\nlast"), "CONTENT_WITHHELD")
            self.assertIn(mock.call("flagged line"), patched.call_args_list)
            self.assertEqual(tc.outbound_text("first\nlast"), "first\nlast")

    def test_whole_text_screen_hits(self):
        # Each line passes safe_diagnostic_text; only the whole-text screen catches these.
        seen = []

        def whole(text):
            seen.append(text)
            return "\n" in text
        with mock.patch.object(tc, "_emitted_sensitive", side_effect=whole):
            self.assertEqual(tc.outbound_text("first part\nsecond part"), "CONTENT_WITHHELD")
            self.assertIn("first part\nsecond part", seen)
            self.assertEqual(tc.outbound_text("one line"), "one line")
        with mock.patch.object(tc, "_emitted_sensitive", return_value=True):
            self.assertEqual(tc.outbound_text("harmless"), "CONTENT_WITHHELD")

    def test_bot_token_hits(self):
        for text in ("123456789:" + "A" * 35, "x 1:" + "a-_9" * 8, "a\n42:" + "Z" * 30):
            self.assertEqual(tc.outbound_text(text), "CONTENT_WITHHELD", text)

    def test_non_text_is_withheld(self):
        for value in (None, 1, b"x", ["x"]):
            self.assertEqual(tc.outbound_text(value), "CONTENT_WITHHELD")

    def test_length_cut_at_line_end(self):
        original = "\n".join("line %04d of text" % i for i in range(1000))
        message = tc.outbound_text(original)
        self.assertLessEqual(len(message), 3500)
        self.assertTrue(message.endswith("\n+more"))
        body = message[:-len("\n+more")]
        self.assertTrue(original.startswith(body))
        self.assertEqual(original[len(body)], "\n")
        self.assertGreater(len(body), 3400)
        exact = "\n".join(["x" * 99] * 34 + ["x" * 100])
        self.assertEqual(len(exact), 3500)
        self.assertEqual(tc.outbound_text(exact), exact)
        over = exact + "x"
        self.assertEqual(tc.outbound_text(over), "\n".join(["x" * 99] * 34 + ["+more"]))

    def test_sensitive_line_past_cut_still_withholds(self):
        original = "\n".join(["line %04d" % i for i in range(600)] + ["123456789:" + "A" * 35])
        self.assertEqual(tc.outbound_text(original), "CONTENT_WITHHELD")


class DeliverTests(unittest.TestCase):
    def deliver(self, events, transport=None, claims=None, **kwargs):
        transport = transport or FakeTransport()
        claims = claims or FakeClaims()
        result = tc.deliver(transport, "owner-main", events, claims.claim, claims.release,
                            **kwargs)
        return result, transport, claims

    def assert_no_text(self, result, events):
        dumped = json.dumps(result)
        for item in events:
            self.assertNotIn(item["text"], dumped)

    def test_all_sent(self):
        events = [event(1), event(2)]
        result, transport, claims = self.deliver(events)
        self.assertEqual(transport.calls, [("owner-main", events[0]["text"]),
                                           ("owner-main", events[1]["text"])])
        self.assertEqual(result, {"status": "SENT", "sent": 2, "released": 0,
                                  "sent_keys": [events[0]["key"], events[1]["key"]],
                                  "released_keys": []})
        self.assertEqual(claims.release_calls, [])
        self.assert_no_text(result, events)

    def test_nothing_claimed(self):
        events = [event(1)]
        result, transport, _claims = self.deliver(
            events, claims=FakeClaims(held=[events[0]["key"]]))
        self.assertEqual(transport.calls, [])
        self.assertEqual(result["status"], "IDLE")
        self.assertEqual(result["sent"], 0)
        result, transport, claims = self.deliver([])
        self.assertEqual(result["status"], "IDLE")
        self.assertEqual(transport.calls, [])

    def test_claim_subset_in_order(self):
        events = [event(1), event(2), event(3), event(4)]
        claims = FakeClaims(only={events[3]["key"], events[1]["key"]})
        result, transport, _ = self.deliver(events, claims=claims)
        self.assertEqual([text for _alias, text in transport.calls],
                         [events[1]["text"], events[3]["text"]])
        self.assertEqual(result["sent_keys"], [events[1]["key"], events[3]["key"]])
        self.assertEqual(claims.claim_calls, [[e["key"] for e in events]])

    def test_claim_returning_unknown_keys_is_ignored(self):
        events = [event(1)]
        claims = FakeClaims()
        claims.claim = lambda keys: [events[0]["key"], "f" * 64]
        result, transport, _ = self.deliver(events, claims=claims)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(result["sent_keys"], [events[0]["key"]])

    def test_failed_releases_unsent_and_stops(self):
        events = [event(1), event(2), event(3)]
        transport = FakeTransport({"status": "SENT"}, {"status": "FAILED"})
        result, transport, claims = self.deliver(events, transport=transport)
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(claims.release_calls, [[events[1]["key"], events[2]["key"]]])
        self.assertEqual(result, {"status": "OFFLINE", "sent": 1, "released": 2,
                                  "sent_keys": [events[0]["key"]],
                                  "released_keys": [events[1]["key"], events[2]["key"]]})
        self.assertNotIn(events[1]["key"], claims.held)
        self.assertIn(events[0]["key"], claims.held)
        self.assert_no_text(result, events)

    def test_other_replies_are_failed(self):
        for reply in (None, "SENT", {"status": "OK"}, {"status": "sent"}, {}, ["SENT"],
                      {"status": "RATE_LIMITED"}, {"status": "RATE_LIMITED", "retry_after": "5"},
                      {"status": "RATE_LIMITED", "retry_after": True},
                      {"status": "RATE_LIMITED", "retry_after": 2.5}):
            events = [event(1), event(2)]
            result, transport, claims = self.deliver(events, transport=FakeTransport(reply))
            self.assertEqual(result["status"], "OFFLINE", repr(reply))
            self.assertEqual(result["sent"], 0)
            self.assertEqual(len(transport.calls), 1)
            self.assertEqual(claims.release_calls, [[events[0]["key"], events[1]["key"]]])
            self.assertNotIn("retry_after", result)

    def test_rate_limited_clamped(self):
        for given, expected in ((30, 30), (0, 1), (-5, 1), (99999, 3600), (3600, 3600), (1, 1)):
            events = [event(1), event(2)]
            transport = FakeTransport({"status": "SENT"},
                                      {"status": "RATE_LIMITED", "retry_after": given})
            result, transport, claims = self.deliver(events, transport=transport)
            self.assertEqual(result, {"status": "RATE_LIMITED", "sent": 1, "released": 1,
                                      "sent_keys": [events[0]["key"]],
                                      "released_keys": [events[1]["key"]],
                                      "retry_after": expected})
            self.assertEqual(claims.release_calls, [[events[1]["key"]]])

    def test_raising_transport_is_unknown_and_releases_nothing(self):
        events = [event(1), event(2), event(3)]
        transport = FakeTransport({"status": "SENT"}, RuntimeError("secret detail"))
        result, transport, claims = self.deliver(events, transport=transport)
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(claims.release_calls, [])
        self.assertEqual(result, {"status": "UNKNOWN", "sent": 1, "released": 0,
                                  "sent_keys": [events[0]["key"]], "released_keys": []})
        self.assertNotIn("secret detail", json.dumps(result))
        for item in events:
            self.assertIn(item["key"], claims.held)

    def test_summary_message(self):
        events = [event(i) for i in range(5)]
        result, transport, _ = self.deliver(events)
        texts = [text for _alias, text in transport.calls]
        self.assertEqual(texts, [events[0]["text"], events[1]["text"],
                                 "+3 more notifications"])
        self.assertEqual(result["sent"], 3)
        self.assertEqual(result["sent_keys"], [e["key"] for e in events])
        result, transport, _ = self.deliver(events, max_messages=1)
        self.assertEqual([t for _a, t in transport.calls], ["+5 more notifications"])
        result, transport, _ = self.deliver(events, max_messages=5)
        self.assertEqual([t for _a, t in transport.calls], [e["text"] for e in events])
        result, transport, _ = self.deliver(events[:3], max_messages=10)
        self.assertEqual(len(transport.calls), 3)

    def test_summary_failure_releases_its_keys(self):
        events = [event(i) for i in range(5)]
        transport = FakeTransport({"status": "SENT"}, {"status": "SENT"}, {"status": "FAILED"})
        result, transport, claims = self.deliver(events, transport=transport)
        self.assertEqual(result["status"], "OFFLINE")
        self.assertEqual(claims.release_calls, [[e["key"] for e in events[2:]]])

    def test_invalid_max_messages(self):
        for value in (0, 11, True, "3", None):
            claims = FakeClaims()
            with self.assertRaises(LLMToolError) as caught:
                self.deliver([event(1)], claims=claims, max_messages=value)
            self.assertEqual(str(caught.exception), "TELEGRAM_MAX_MESSAGES_INVALID")
            self.assertEqual(claims.claim_calls, [])

    def test_invalid_alias(self):
        for alias in ("", "Owner", "1owner", "-a", "a" * 33, "a b", "a\n", "-1001234567890",
                      "123456789", None, 5, "owner.main", "ówner"):
            transport, claims = FakeTransport(), FakeClaims()
            with self.assertRaises(LLMToolError) as caught:
                tc.deliver(transport, alias, [event(1)], claims.claim, claims.release)
            self.assertEqual(str(caught.exception), "TELEGRAM_CHAT_ALIAS_INVALID")
            self.assertEqual(transport.calls, [])
            self.assertEqual(claims.claim_calls, [])
        for alias in ("a", "a" * 32, "owner_main-2"):
            transport, claims = FakeTransport(), FakeClaims()
            result = tc.deliver(transport, alias, [event(1)], claims.claim, claims.release)
            self.assertEqual(result["status"], "SENT")
            self.assertEqual(transport.calls[0][0], alias)

    def test_invalid_events(self):
        good = event(1)
        for events in (None, "x", [None], [dict(good, key="ABC")], [dict(good, kind="other")],
                       [dict(good, text=None)], [{"key": good["key"]}]):
            claims = FakeClaims()
            with self.assertRaises(LLMToolError) as caught:
                self.deliver(events, claims=claims)
            self.assertEqual(str(caught.exception), "TELEGRAM_EVENTS_INVALID")
            self.assertEqual(claims.claim_calls, [])

    def test_event_text_is_screened_and_cut(self):
        events = [event(1, "token 123456789:" + "A" * 35),
                  event(2, "\n".join("line %04d" % i for i in range(800)))]
        result, transport, _ = self.deliver(events)
        texts = [text for _alias, text in transport.calls]
        self.assertEqual(texts[0], "CONTENT_WITHHELD")
        self.assertLessEqual(len(texts[1]), 3500)
        self.assertTrue(texts[1].endswith("\n+more"))
        self.assertEqual(result["status"], "SENT")

    def test_events_from_notification_events_deliver(self):
        events = tc.notification_events(status(task(1, "BLOCKED"), unreadable=1),
                                        owner_waiting=["x"])
        result, transport, _ = self.deliver(events)
        self.assertEqual(result["sent_keys"], [e["key"] for e in events])
        self.assertEqual(len(transport.calls), 3)

    def test_claim_failure_and_invalid_reply(self):
        claims = FakeClaims()

        def boom(keys):
            raise RuntimeError("secret claim detail")
        with self.assertRaises(LLMToolError) as caught:
            tc.deliver(FakeTransport(), "owner", [event(1)], boom, claims.release)
        self.assertEqual(str(caught.exception), "TELEGRAM_CLAIM_FAILED")
        with self.assertRaises(LLMToolError) as caught:
            tc.deliver(FakeTransport(), "owner", [event(1)], lambda keys: "x", claims.release)
        self.assertEqual(str(caught.exception), "TELEGRAM_CLAIM_INVALID")
        with self.assertRaises(LLMToolError) as caught:
            tc.deliver(object(), "owner", [event(1)], claims.claim, claims.release)
        self.assertEqual(str(caught.exception), "TELEGRAM_TRANSPORT_INVALID")

    def test_release_failure_reports_nothing_released(self):
        events = [event(1)]
        claims = FakeClaims()

        def release(keys):
            raise RuntimeError("release broke")
        result = tc.deliver(FakeTransport({"status": "FAILED"}), "owner", events,
                            claims.claim, release)
        self.assertEqual(result["status"], "OFFLINE")
        self.assertEqual(result["released"], 0)
        self.assertEqual(result["released_keys"], [])


class ModuleBoundaryTests(unittest.TestCase):
    def test_imports_are_bounded(self):
        source = Path(tc.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add(("." * node.level) + (node.module or ""))
                names.update(alias.name for alias in node.names)
        forbidden = {"socket", "urllib", "http", "ssl", "subprocess", "requests",
                     "asyncio", "active_pm_inbox", "pm_runtime", "task_store",
                     "task_index", "chat_tui_status", "telegram"}
        for name in names:
            parts = set(re.split(r"[.]", name.lstrip(".")))
            self.assertFalse(parts & forbidden, name)
        self.assertNotIn("__import__", source)
        self.assertNotIn("importlib", source)

    def test_no_source_line_matches_the_credential_screen(self):
        # No credential-shaped fixture exists here; credential screen hits are covered
        # by patching the screen functions, so no source line trips the diff screen.
        from llmtool.test_output_excerpts import _emitted_sensitive
        for path in (Path(tc.__file__), Path(__file__)):
            lines = path.read_text(encoding="utf-8").split("\n")
            for number, line in enumerate(lines, 1):
                self.assertFalse(_emitted_sensitive(line), "%s:%d" % (path.name, number))

    def test_error_codes_never_echo_input(self):
        alias = "SECRET-123456789:" + "A" * 35
        with self.assertRaises(LLMToolError) as caught:
            tc.deliver(FakeTransport(), alias, [], FakeClaims().claim, FakeClaims().release)
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertEqual(caught.exception.args, ("TELEGRAM_CHAT_ALIAS_INVALID",))


if __name__ == "__main__":
    unittest.main()
