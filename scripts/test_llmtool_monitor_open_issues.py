"""Offline tests for the 白板 open-Issue section in scripts/llmtool_monitor.py
(Owner 2026-11-24: 「那個監控白板,我想把還有哪些open的issue單,也進去」).
No processes, models or network; GitHub reads are stubbed and every clock is injected.

Run: python3 -B -m unittest discover -s scripts -p 'test_llmtool_monitor_open_issues.py'
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_PATH = Path(__file__).with_name("llmtool_monitor.py")
_SPEC = importlib.util.spec_from_file_location("llmtool_monitor_open_issues_under_test", _PATH)
mon = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = mon
_SPEC.loader.exec_module(mon)

NOW = 1_794_000_000

_ISOLATED = tempfile.TemporaryDirectory()
_PATCHES = (patch.object(mon, "PM_BOARD", os.path.join(_ISOLATED.name, "pm-board.json")),)


def setUpModule():
    for item in _PATCHES:
        item.start()


def tearDownModule():
    for item in reversed(_PATCHES):
        item.stop()
    _ISOLATED.cleanup()


def _iso(when: float) -> str:
    return datetime.fromtimestamp(when, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _issue(number, title="Issue", *, state="open", pr=False):
    item = {"number": number, "title": title, "state": state}
    if pr:
        item["pull_request"] = {"url": f"https://api.github.com/repos/o/r/pulls/{number}"}
    return item


def _pull(number, body=""):
    return {"number": number, "title": f"Work {number}", "state": "open", "merged_at": None,
            "updated_at": _iso(NOW), "draft": False, "body": body,
            "head": {"sha": "a" * 40, "repo": {"full_name": "o/r"}}}


def _github(pulls, issues, *, cut=False):
    return SimpleNamespace(board_pulls=mon.board_pulls(pulls, "o/r"), verdicts={}, pending=[],
                           successful_read=True, read_failed=False, ci_view=None, window=43200,
                           board_page_count=len(pulls), board_oldest_updated=NOW,
                           pr_checks=None, open_issues=issues, issues_cut=cut)


class OpenIssueParseTests(unittest.TestCase):
    def test_keeps_only_open_issues_newest_first_with_safe_titles(self):
        page = [_issue(5, "Five"), _issue(9, "Nine `code` **bold**"),
                _issue(7, "A PR", pr=True), _issue(8, "Closed", state="closed"),
                _issue(0, "Zero"), _issue(True, "Bool"), {"number": "3"}, "junk"]
        issues = mon.open_issues(page)
        self.assertEqual([issue.number for issue in issues], [9, 5])
        self.assertEqual(issues[0].title, "Nine code bold")
        self.assertEqual(issues[1].prs, ())

    def test_a_page_that_is_not_a_list_is_unknown(self):
        self.assertIsNone(mon.open_issues(None))
        self.assertIsNone(mon.open_issues({"message": "Not Found"}))
        self.assertEqual(mon.open_issues([]), [])


class OpenIssueReadTests(unittest.TestCase):
    def test_one_get_at_most_every_300_seconds_and_failure_is_unknown(self):
        source = mon.GitHubSource("o/r", clock=lambda: NOW)
        calls = []
        source._get = lambda path: calls.append(path) or [_issue(4, "Four")]
        clock = [1000.0]
        with patch.object(mon.time, "monotonic", lambda: clock[0]):
            source.refresh_open_issues()
            self.assertEqual(calls, ["repos/o/r/issues?state=open&sort=created"
                                     "&direction=desc&per_page=100"])
            self.assertEqual([issue.number for issue in source.open_issues], [4])
            self.assertFalse(source.issues_cut)
            clock[0] += 299
            source.refresh_open_issues()
            self.assertEqual(len(calls), 1)
            clock[0] += 2
            source._get = lambda path: (_ for _ in ()).throw(KeyError(path))
            source.refresh_open_issues()
            self.assertIsNone(source.open_issues)
            clock[0] += 301
            source._get = lambda path: [_issue(n) for n in range(1, 101)]
            source.refresh_open_issues()
            self.assertTrue(source.issues_cut)

    def test_refresh_reads_issues_only_when_opted_in(self):
        for wanted in (False, True):
            with self.subTest(wanted=wanted):
                source = mon.GitHubSource("o/r", clock=lambda: NOW)
                seen = []
                source._get = lambda path, seen=seen: seen.append(path) or []
                source.refresh_ci = lambda: None
                source.want_issues = wanted
                source.refresh(force=True)
                self.assertEqual(any("issues?state=open" in path for path in seen), wanted)


class OpenIssueBoardTests(unittest.TestCase):
    def test_section_lists_each_issue_and_the_open_prs_that_close_it(self):
        pulls = [_pull(30, "Closes #12"), _pull(31, "fixes #12\nresolves #13")]
        issues = mon.open_issues([_issue(12, "Twelve"), _issue(13, "Thirteen"),
                                  _issue(14, "Fourteen")])
        work = mon.work_board(_github(pulls, issues), mon.PmBoard("missing", None, [], [], []), NOW)
        lines = [text for text, _ in mon.board_view(work)]
        start = lines.index("── Open Issue（3） ──")
        self.assertEqual(lines[start + 1:start + 4], [
            "#14  Fourteen",
            "#13  Thirteen  → PR #31",
            "#12  Twelve  → PR #30、PR #31",
        ])

    def test_unknown_empty_and_cut_states(self):
        board = mon.PmBoard("missing", None, [], [], [])
        unknown = [text for text, _ in mon.board_view(
            mon.work_board(_github([], None), board, NOW))]
        self.assertIn("── Open Issue ──", unknown)
        self.assertEqual(unknown[unknown.index("── Open Issue ──") + 1], "未知")
        empty = [text for text, _ in mon.board_view(mon.work_board(_github([], []), board, NOW))]
        self.assertEqual(empty[empty.index("── Open Issue（0） ──") + 1], "無")
        cut = [text for text, _ in mon.board_view(
            mon.work_board(_github([], [mon.OpenIssue(1, "One")], cut=True), board, NOW))]
        self.assertIn("── Open Issue（1+，只讀了 100 筆） ──", cut)
        no_github = [text for text, _ in mon.board_view(mon.work_board(None, board, NOW))]
        self.assertEqual(no_github[no_github.index("── Open Issue ──") + 1], "未知")


if __name__ == "__main__":
    unittest.main()
