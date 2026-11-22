#!/usr/bin/env python3
"""Tests for Claude quota reset expiration in llmtool_monitor (Issue #2411).

Acceptance criteria:
- A1: once reported reset time has passed, monitor no longer shows that account as at limit.
- A2: limit hit whose reset time has not passed still shows, with its reset time.
- A3: hit with no readable reset time keeps current behaviour.
- A4: one general rule for every Claude account; no per-account or per-shape special case.
- A5: this test module covers A1-A3; existing monitor tests still pass; exact-head Linux CI passes.
"""

from datetime import datetime, timedelta
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MONITOR_PATH = os.path.join(SCRIPT_DIR, "llmtool_monitor.py")

spec = importlib.util.spec_from_file_location("llmtool_monitor", MONITOR_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"unable to load {MONITOR_PATH}")
monitor = importlib.util.module_from_spec(spec)
sys.modules["llmtool_monitor"] = monitor
spec.loader.exec_module(monitor)


class TestClaudeQuotaResetParsing(unittest.TestCase):
    """Unit tests for helper functions _parse_reset_time and _claude_quota_reset_timestamp."""

    def test_parse_reset_time_valid(self):
        self.assertEqual(monitor._parse_reset_time("4:40am"), (4, 40))
        self.assertEqual(monitor._parse_reset_time("4:40pm"), (16, 40))
        self.assertEqual(monitor._parse_reset_time("12:00am"), (0, 0))
        self.assertEqual(monitor._parse_reset_time("12:00pm"), (12, 0))
        self.assertEqual(monitor._parse_reset_time("12:30am"), (0, 30))
        self.assertEqual(monitor._parse_reset_time("12:30pm"), (12, 30))
        self.assertEqual(monitor._parse_reset_time("4am"), (4, 0))
        self.assertEqual(monitor._parse_reset_time("4pm"), (16, 0))
        self.assertEqual(monitor._parse_reset_time("11:59am"), (11, 59))
        self.assertEqual(monitor._parse_reset_time("11:59pm"), (23, 59))
        self.assertEqual(monitor._parse_reset_time(" 4:40 AM "), (4, 40))

    def test_parse_reset_time_invalid(self):
        self.assertIsNone(monitor._parse_reset_time(""))
        self.assertIsNone(monitor._parse_reset_time("invalid"))
        self.assertIsNone(monitor._parse_reset_time("99:99am"))
        self.assertIsNone(monitor._parse_reset_time("13:00pm"))
        self.assertIsNone(monitor._parse_reset_time("4:60am"))
        self.assertIsNone(monitor._parse_reset_time(None))
        self.assertIsNone(monitor._parse_reset_time(12345))

    def test_claude_quota_reset_timestamp_same_day(self):
        hit_dt = datetime(2026, 11, 20, 3, 30, 0)
        hit_ts = hit_dt.timestamp()
        reset_ts = monitor._claude_quota_reset_timestamp("4:40am", hit_ts)
        self.assertIsNotNone(reset_ts)
        expected_dt = datetime(2026, 11, 20, 4, 40, 0)
        self.assertEqual(reset_ts, expected_dt.timestamp())

    def test_claude_quota_reset_timestamp_cross_midnight(self):
        hit_dt = datetime(2026, 11, 19, 23, 30, 0)
        hit_ts = hit_dt.timestamp()
        reset_ts = monitor._claude_quota_reset_timestamp("4:40am", hit_ts)
        self.assertIsNotNone(reset_ts)
        expected_dt = datetime(2026, 11, 20, 4, 40, 0)
        self.assertEqual(reset_ts, expected_dt.timestamp())

    def test_claude_quota_reset_timestamp_same_minute_is_next_day(self):
        # #2412 formal v2 F1: a hit at 04:40:30 (or 04:40:05) saying "resets
        # 4:40am" names the next day's reset; no one-minute tolerance.
        for second in (0, 5, 30, 59):
            with self.subTest(second=second):
                hit_ts = datetime(2026, 11, 20, 4, 40, second).timestamp()
                reset_ts = monitor._claude_quota_reset_timestamp("4:40am", hit_ts)
                self.assertEqual(reset_ts, datetime(2026, 11, 21, 4, 40, 0).timestamp())

    def test_claude_quota_reset_timestamp_one_minute_later_is_same_day(self):
        # A reset clock time later than the hit stays on the hit's day.
        hit_ts = datetime(2026, 11, 20, 4, 39, 59).timestamp()
        reset_ts = monitor._claude_quota_reset_timestamp("4:40am", hit_ts)
        self.assertEqual(reset_ts, datetime(2026, 11, 20, 4, 40, 0).timestamp())

    def test_claude_quota_reset_timestamp_next_day_within_one_hour(self):
        # Hit at 04:50 reporting resets 4:40am refers to the next day, not 10 minutes earlier.
        hit_dt = datetime(2026, 11, 20, 4, 50, 0)
        hit_ts = hit_dt.timestamp()
        reset_ts = monitor._claude_quota_reset_timestamp("4:40am", hit_ts)
        self.assertIsNotNone(reset_ts)
        expected_dt = datetime(2026, 11, 21, 4, 40, 0)
        self.assertEqual(reset_ts, expected_dt.timestamp())


class _SessionFixture:
    """Shared fixture: a temp directory of per-account Claude session logs."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test-claude-quota-")
        if hasattr(monitor, "_listing_cache"):
            monitor._listing_cache.clear()
        if hasattr(monitor, "_tail_cache"):
            monitor._tail_cache.clear()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)
        if hasattr(monitor, "_listing_cache"):
            monitor._listing_cache.clear()
        if hasattr(monitor, "_tail_cache"):
            monitor._tail_cache.clear()

    def _create_account_session(self, account_name: str, items: list[dict], mtime: float):
        acct_dir = os.path.join(self.test_dir, account_name)
        top = os.path.join(acct_dir, "projects")
        proj_dir = os.path.join(top, "demo-project")
        os.makedirs(proj_dir, exist_ok=True)
        session_path = os.path.join(proj_dir, "session-1.jsonl")
        with open(session_path, "w", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item) + "\n")
        os.utime(session_path, (mtime, mtime))
        os.utime(proj_dir, (mtime, mtime))
        return top


class TestLlmtoolMonitorClaudeQuotaReset(_SessionFixture, unittest.TestCase):
    """End-to-end quota_warnings tests for Claude quota reset behavior (A1-A4)."""

    def test_a1_reset_time_passed_not_shown(self):
        # A1: once the reported reset time has passed, the monitor no longer shows that account as at its limit.
        hit_dt = datetime(2026, 11, 20, 3, 30, 0)
        hit_ts = hit_dt.timestamp()
        now_dt = datetime(2026, 11, 20, 4, 41, 0)
        now_ts = now_dt.timestamp()

        items = [
            {
                "timestamp": hit_ts,
                "error": "rate_limit",
                "message": {
                    "content": [
                        {"text": "Claude usage limit reached, resets 4:40am"}
                    ]
                },
            }
        ]
        top = self._create_account_session(".claude-account3", items, mtime=now_ts)
        warnings = monitor.quota_warnings(now_ts, claude_root=top)
        self.assertEqual(warnings, [])

    def test_a2_reset_time_not_passed_shows_with_reset_time(self):
        # A2: a limit hit whose reset time has not passed still shows, with its reset time.
        hit_dt = datetime(2026, 11, 20, 3, 30, 0)
        hit_ts = hit_dt.timestamp()
        now_dt = datetime(2026, 11, 20, 4, 20, 0)
        now_ts = now_dt.timestamp()

        items = [
            {
                "timestamp": hit_ts,
                "error": "rate_limit",
                "message": {
                    "content": [
                        {"text": "Claude usage limit reached, resets 4:40am"}
                    ]
                },
            }
        ]
        top = self._create_account_session(".claude-account3", items, mtime=now_ts)
        warnings = monitor.quota_warnings(now_ts, claude_root=top)
        self.assertEqual(warnings, ["Claude（.claude-account3）已到用量上限，resets 4:40am"])

    def test_a2_next_day_reset_within_one_hour_still_shows(self):
        # A2: hit at 04:50 with resets 4:40am (next day) must still show at 05:00.
        hit_dt = datetime(2026, 11, 20, 4, 50, 0)
        hit_ts = hit_dt.timestamp()
        now_dt = datetime(2026, 11, 20, 5, 0, 0)
        now_ts = now_dt.timestamp()

        items = [
            {
                "timestamp": hit_ts,
                "error": "rate_limit",
                "message": {
                    "content": [
                        {"text": "Claude usage limit reached, resets 4:40am"}
                    ]
                },
            }
        ]
        top = self._create_account_session(".claude-account3", items, mtime=now_ts)
        warnings = monitor.quota_warnings(now_ts, claude_root=top)
        self.assertEqual(warnings, ["Claude（.claude-account3）已到用量上限，resets 4:40am"])

    def test_a2_same_minute_reset_still_shows_a_minute_later(self):
        # #2412 formal v2 F1: hit at 04:40:30 saying resets 4:40am means the
        # next day's reset, so at 04:41 the warning must still show.
        hit_ts = datetime(2026, 11, 20, 4, 40, 30).timestamp()
        now_ts = datetime(2026, 11, 20, 4, 41, 0).timestamp()
        items = [{"timestamp": hit_ts, "error": "rate_limit",
                  "message": {"content": [{"text": "Claude usage limit reached, resets 4:40am"}]}}]
        top = self._create_account_session(".claude-account3", items, mtime=now_ts)
        warnings = monitor.quota_warnings(now_ts, claude_root=top)
        self.assertEqual(warnings, ["Claude（.claude-account3）已到用量上限，resets 4:40am"])

    def test_a3_no_readable_reset_time_keeps_current_behaviour(self):
        # A3: a hit with no readable reset time keeps the current behaviour.
        hit_dt = datetime(2026, 11, 20, 3, 30, 0)
        hit_ts = hit_dt.timestamp()
        now_dt = datetime(2026, 11, 20, 4, 50, 0)
        now_ts = now_dt.timestamp()

        # Case 3a: Message has no reset time at all
        items_no_reset = [
            {
                "timestamp": hit_ts,
                "error": "rate_limit",
                "message": {
                    "content": [
                        {"text": "Claude usage limit reached"}
                    ]
                },
            }
        ]
        top_a = self._create_account_session(".claude-account1", items_no_reset, mtime=now_ts)
        warnings_a = monitor.quota_warnings(now_ts, claude_root=top_a)
        self.assertEqual(warnings_a, ["Claude（.claude-account1）已到用量上限"])

        # Case 3b: Message has an unparseable reset time
        items_unparseable = [
            {
                "timestamp": hit_ts,
                "error": "rate_limit",
                "message": {
                    "content": [
                        {"text": "Claude usage limit reached, resets 99:99am"}
                    ]
                },
            }
        ]
        top_b = self._create_account_session(".claude-account2", items_unparseable, mtime=now_ts)
        warnings_b = monitor.quota_warnings(now_ts, claude_root=top_b)
        self.assertEqual(warnings_b, ["Claude（.claude-account2）已到用量上限，resets 99:99am"])

        # Case 3c: Past 5 hours cutoff still drops as in current behaviour
        stale_now_ts = (hit_dt + timedelta(hours=6)).timestamp()
        warnings_stale = monitor.quota_warnings(stale_now_ts, claude_root=top_a)
        self.assertEqual(warnings_stale, [])

    def test_a4_general_rule_across_multiple_accounts(self):
        # A4: one general rule for every Claude account; no per-account or per-shape special case.
        hit_dt = datetime(2026, 11, 20, 3, 30, 0)
        hit_ts = hit_dt.timestamp()
        now_dt = datetime(2026, 11, 20, 4, 45, 0)
        now_ts = now_dt.timestamp()

        # Account 1: reset 4:40am has passed at 4:45am
        top1 = self._create_account_session(".claude-account1", [
            {
                "timestamp": hit_ts,
                "error": "rate_limit",
                "message": {
                    "content": [
                        {"text": "Claude usage limit reached, resets 4:40am"}
                    ]
                },
            }
        ], mtime=now_ts)

        # Account 2: reset 5:30am has not passed at 4:45am
        top2 = self._create_account_session(".claude-account2", [
            {
                "timestamp": hit_ts,
                "error": "rate_limit",
                "message": {
                    "content": [
                        {"text": "Claude usage limit reached, resets 5:30am"}
                    ]
                },
            }
        ], mtime=now_ts)

        warnings = monitor.quota_warnings(now_ts, claude_root=[top1, top2])
        self.assertEqual(warnings, ["Claude（.claude-account2）已到用量上限，resets 5:30am"])


def _zone_available(name: str) -> bool:
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
        return True
    except Exception:
        return False


class TestClaudeQuotaResetNamedZone(_SessionFixture, unittest.TestCase):
    """A notice that names its zone is read in that zone, whatever the local zone is.
    Hit times use the ISO string form Claude session logs carry."""

    ZONE = "Asia/Taipei"
    HIT_ISO = "2026-11-19T19:30:00Z"   # 03:30 in Asia/Taipei

    def _hit(self, text: str) -> list[dict]:
        return [{
            "timestamp": self.HIT_ISO,
            "error": "rate_limit",
            "message": {"content": [{"text": text}]},
        }]

    def _utc(self, hour: int, minute: int) -> float:
        # 2026-11-19 UTC clock time; Asia/Taipei is UTC+8, so 20:41 UTC is 04:41 next day there.
        from datetime import timezone
        return datetime(2026, 11, 19, hour, minute, tzinfo=timezone.utc).timestamp()

    @unittest.skipUnless(_zone_available(ZONE), "tz database not available")
    def test_named_zone_reset_passed_not_shown(self):
        now_ts = self._utc(20, 41)   # 04:41 in Asia/Taipei
        top = self._create_account_session(
            ".claude-account3", self._hit("Limit reached, resets 4:40am (Asia/Taipei)"), mtime=now_ts)
        self.assertEqual(monitor.quota_warnings(now_ts, claude_root=top), [])

    @unittest.skipUnless(_zone_available(ZONE), "tz database not available")
    def test_named_zone_reset_not_passed_shows_without_zone_in_line(self):
        now_ts = self._utc(20, 20)   # 04:20 in Asia/Taipei
        top = self._create_account_session(
            ".claude-account3", self._hit("Limit reached, resets 4:40am (Asia/Taipei)"), mtime=now_ts)
        self.assertEqual(monitor.quota_warnings(now_ts, claude_root=top),
                         ["Claude（.claude-account3）已到用量上限，resets 4:40am"])

    @unittest.skipUnless(_zone_available(ZONE), "tz database not available")
    def test_named_zone_next_day_reset_within_one_hour_still_shows(self):
        # Hit 04:50 Asia/Taipei saying resets 4:40am means the next day; at 05:00 it still shows.
        hit = [{"timestamp": "2026-11-19T20:50:00Z", "error": "rate_limit",
                "message": {"content": [{"text": "resets 4:40am (Asia/Taipei)"}]}}]
        now_ts = self._utc(21, 0)
        top = self._create_account_session(".claude-account3", hit, mtime=now_ts)
        self.assertEqual(monitor.quota_warnings(now_ts, claude_root=top),
                         ["Claude（.claude-account3）已到用量上限，resets 4:40am"])

    def test_unresolvable_zone_keeps_current_behaviour(self):
        # A zone that cannot be resolved is not a readable reset time (A3): still shown.
        now_ts = self._utc(21, 30)
        top = self._create_account_session(
            ".claude-account3", self._hit("resets 4:40am (Mars/Olympus)"), mtime=now_ts)
        self.assertEqual(monitor.quota_warnings(now_ts, claude_root=top),
                         ["Claude（.claude-account3）已到用量上限，resets 4:40am"])

    def test_unresolvable_zone_helper_returns_none(self):
        self.assertIsNone(monitor._claude_quota_reset_timestamp(
            "4:40am", self._utc(19, 30), "Mars/Olympus"))
        self.assertIsNone(monitor._claude_quota_reset_timestamp(
            "4:40am", self._utc(19, 30), "../etc"))

    @unittest.skipUnless(_zone_available(ZONE), "tz database not available")
    def test_named_zone_helper_value(self):
        from datetime import timezone
        got = monitor._claude_quota_reset_timestamp("4:40am", self._utc(19, 30), self.ZONE)
        # 04:40 Asia/Taipei on 2026-11-20 is 20:40 UTC on 2026-11-19.
        self.assertEqual(got, datetime(2026, 11, 19, 20, 40, tzinfo=timezone.utc).timestamp())

    def test_note_without_zone_text_in_parentheses_is_read_as_local(self):
        # Parenthesised words that are not a single zone token do not turn the notice unreadable.
        hit_dt = datetime(2026, 11, 20, 3, 30, 0)
        now_ts = datetime(2026, 11, 20, 4, 41, 0).timestamp()
        top = self._create_account_session(
            ".claude-account3",
            [{"timestamp": hit_dt.timestamp(), "error": "rate_limit",
              "message": {"content": [{"text": "resets 4:40am (see usage page)"}]}}],
            mtime=now_ts)
        self.assertEqual(monitor.quota_warnings(now_ts, claude_root=top), [])


if __name__ == "__main__":
    unittest.main()
