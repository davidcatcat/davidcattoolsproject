#!/usr/bin/env python3
"""Tests for hosted_ci_rows in llmtool_monitor.py (pure, synthetic payloads, no network)."""

import importlib.util
import os
import sys
import unittest

MONITOR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llmtool_monitor.py")


def _load_monitor():
    spec = importlib.util.spec_from_file_location("llmtool_monitor", MONITOR_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load monitor from {MONITOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["llmtool_monitor"] = module
    spec.loader.exec_module(module)
    return module


mon = _load_monitor()
CiView = mon.CiView
OfficeRow = mon.OfficeRow
hosted_ci_rows = mon.hosted_ci_rows
_ci_conclusion_word = mon._ci_conclusion_word
UNCHECKED = mon.UNCHECKED
WORKING = mon.WORKING
IDLE = mon.IDLE
GITHUB_STALE = mon.GITHUB_STALE


class TestHostedCiRows(unittest.TestCase):
    def test_queued_run_gives_queued_row(self):
        now = 1000000.0
        ci_runs = [("queued", "llmtool Linux CI（PR #123 @abcdef0）", now - 120.0, None, 1001)]
        view = CiView(runners=[], seen_at=now)
        rows = hosted_ci_rows(ci_runs, view, now)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.name, "hosted ci llmtool Linux CI")
        self.assertEqual(row.state, "排隊")
        self.assertIn("排隊 1 個", row.detail)
        self.assertEqual(row.age, 120.0)
        self.assertEqual(row.kind, "worker")
        self.assertEqual(row.evidence, "GitHub")

    def test_in_progress_run_with_two_running_jobs(self):
        now = 1000000.0
        ci_runs = [
            ("in_progress", "llmtool Linux CI（PR #123 @abcdef0）：check (Linux 3.14)", now - 180.0, "runner-1", 1001),
            ("in_progress", "llmtool Linux CI（PR #123 @abcdef0）：check (docs)", now - 180.0, "runner-2", 1001),
        ]
        view = CiView(runners=[], seen_at=now)
        rows = hosted_ci_rows(ci_runs, view, now)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.name, "hosted ci llmtool Linux CI")
        self.assertEqual(row.state, WORKING)
        self.assertIn("執行中 2 個", row.detail)
        self.assertIn("check (Linux 3.14)", row.detail)
        self.assertIn("check (docs)", row.detail)
        self.assertEqual(row.age, 180.0)

    def test_run_with_no_jobs_shows_queued(self):
        now = 1000000.0
        # A run that GitHub marks in_progress but has no visible running jobs shows queued.
        ci_runs = [("in_progress", "llmtool Linux CI（PR #123 @abcdef0）", now - 60.0, None, 1001)]
        view = CiView(runners=[], seen_at=now)
        rows = hosted_ci_rows(ci_runs, view, now)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.name, "hosted ci llmtool Linux CI")
        self.assertEqual(row.state, "排隊")
        self.assertNotEqual(row.state, WORKING)
        self.assertNotEqual(row.state, "完成")
        self.assertIn("排隊", row.detail)

    def test_ci_runs_none_gives_unchecked_row(self):
        now = 1000000.0
        view = CiView(runners=[], seen_at=now, error="HTTP 500")
        rows = hosted_ci_rows(None, view, now)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.name, "hosted ci")
        self.assertEqual(row.state, UNCHECKED)
        self.assertIn("未確認", row.detail)
        self.assertIn("HTTP 500", row.detail)
        self.assertNotIn(row.state, (IDLE, WORKING, "完成", "成功"))

    def test_stale_view_gives_unchecked_row(self):
        now = 1000000.0
        seen_at = now - GITHUB_STALE - 10.0
        ci_runs = [("queued", "llmtool Linux CI（PR #123 @abcdef0）", now - 60.0, None, 1001)]
        view = CiView(runners=[], seen_at=seen_at)
        rows = hosted_ci_rows(ci_runs, view, now)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.name, "hosted ci")
        self.assertEqual(row.state, UNCHECKED)
        self.assertIn("未確認", row.detail)
        self.assertNotIn(row.state, (IDLE, WORKING, "完成", "成功"))

    def test_ci_conclusion_word_mappings(self):
        self.assertEqual(_ci_conclusion_word("success"), "成功")
        self.assertEqual(_ci_conclusion_word("SUCCESS"), "成功")
        self.assertEqual(_ci_conclusion_word("cancelled"), "取消")
        self.assertEqual(_ci_conclusion_word("timed_out"), "逾時")
        self.assertEqual(_ci_conclusion_word("skipped"), "略過")
        self.assertEqual(_ci_conclusion_word("failure"), "失敗")
        self.assertEqual(_ci_conclusion_word("unknown"), "結果不明")
        self.assertEqual(_ci_conclusion_word(None), "結果不明")
        self.assertEqual(_ci_conclusion_word(""), "結果不明")

        for val in ["cancelled", "timed_out", "skipped", "failure", "unknown", None, ""]:
            self.assertNotEqual(_ci_conclusion_word(val), "成功")


if __name__ == "__main__":
    unittest.main()
