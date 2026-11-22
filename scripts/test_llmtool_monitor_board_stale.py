"""Offline tests for #1502 WP-H (E4) and WP-G (E8) in scripts/llmtool_monitor.py:
the stale-PM-board top warning and the rule-file alert colour pair.
No processes, models or network; every clock is injected.

Run: python3 -B -m unittest discover -s scripts -p 'test_llmtool_monitor_board_stale.py'
"""
from __future__ import annotations

import curses
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_PATH = Path(__file__).with_name("llmtool_monitor.py")
_SPEC = importlib.util.spec_from_file_location("llmtool_monitor_board_stale_under_test", _PATH)
mon = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = mon
_SPEC.loader.exec_module(mon)

NOW = 1_794_000_000   # an injected wall clock (whole seconds, so mtimes compare exactly)

# Never read the developer's real PM board or rule-alert record.
_ISOLATED = tempfile.TemporaryDirectory()
_PATCHES = (patch.object(mon, "PM_BOARD", os.path.join(_ISOLATED.name, "pm-board.json")),)


def setUpModule():
    for item in _PATCHES:
        item.start()


def tearDownModule():
    for item in reversed(_PATCHES):
        item.stop()
    _ISOLATED.cleanup()


def _stamp(when: float) -> str:
    return datetime.fromtimestamp(when, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_board(path: Path, *, mtime: float, updated_at=None, raw: str | None = None) -> None:
    if raw is None:
        value = {"schema_version": 1, "updated_by": "PM",
                 "running": [], "queue": [], "blocked": []}
        if updated_at is not None:
            value["updated_at"] = updated_at
        raw = json.dumps(value)
    path.write_text(raw, encoding="utf-8")
    os.utime(path, (mtime, mtime))


class _Screen:
    def __init__(self, height=40, width=200):
        self.size = (height, width)

    def getmaxyx(self):
        return self.size

    def erase(self): pass
    def move(self, y, x): pass
    def refresh(self): pass


def _monitor(**extra):
    """The fields _paint_screen reads; nothing here reads processes or GitHub."""
    fields = dict(version="test", polls=0, disk_usage=None, error=None, processes=[],
                  lingering=[], github=None, tasks=[], stale=False, behind=False,
                  office=[], office_error=None, quota=[], pm_board_stamp=None, ec2_heads=None,
                  pm_board=mon.PmBoard("missing", None, [], [], []), incidents=[])
    fields.update(extra)
    return SimpleNamespace(**fields)


def _paint(monitor, view, colors=None, screen=None):
    drawn = {}
    with patch.object(mon, "summary", return_value="status"), \
            patch.object(mon, "stuck_lines", return_value=[]), \
            patch.object(mon, "waiting_task_lines", return_value=[]), \
            patch.object(mon.time, "time", return_value=float(NOW)):
        mon._paint_screen(screen or _Screen(), monitor, view, 0, False, colors or {},
                          lambda row, text, attr=0, x=0: drawn.__setitem__(row, (text, attr)))
    return drawn


class PmBoardStaleTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        self.path = self.root / "pm-board.json"

    def tearDown(self):
        self._dir.cleanup()

    def _line(self):
        return mon.pm_board_stale_line(mon.pm_board_stamp(NOW, str(self.path)), NOW)

    def test_threshold_is_three_sweep_intervals_of_five_minutes(self):
        self.assertEqual(mon.PM_SWEEP_INTERVAL, 300)
        self.assertEqual(mon.PM_BOARD_WARN_AGE, 3 * mon.PM_SWEEP_INTERVAL)
        self.assertEqual(mon.PM_BOARD_WARN_AGE, 15 * 60)

    def test_fresh_board_gives_no_warning_even_with_an_old_mtime(self):
        # updated_at wins over the file time when it is valid.
        _write_board(self.path, updated_at=_stamp(NOW - 60), mtime=NOW - 3600)
        self.assertEqual(mon.pm_board_stamp(NOW, str(self.path)), NOW - 60)
        self.assertEqual(self._line(), "")

    def test_stale_board_names_its_age_in_minutes(self):
        _write_board(self.path, updated_at=_stamp(NOW - 16 * 60 - 5), mtime=NOW)
        self.assertEqual(self._line(), "⚠ PM board stale 16 min")
        _write_board(self.path, updated_at=_stamp(NOW - 3 * 3600), mtime=NOW)
        self.assertEqual(self._line(), "⚠ PM board stale 180 min")

    def test_boundary_is_strictly_older_than_fifteen_minutes(self):
        self.assertEqual(mon.pm_board_stale_line(NOW - 900, NOW), "")
        self.assertEqual(mon.pm_board_stale_line(NOW - 901, NOW), "⚠ PM board stale 15 min")
        self.assertEqual(mon.pm_board_stale_line(NOW, NOW), "")

    def test_missing_board_in_a_fresh_setup_gives_no_warning(self):
        self.assertIsNone(mon.pm_board_stamp(NOW, str(self.root / "absent.json")))
        self.assertEqual(mon.pm_board_stale_line(None, NOW), "")
        # The existing board reader still reports the missing board itself.
        self.assertEqual(mon.read_pm_board(NOW, str(self.root / "absent.json")).status, "missing")

    def test_malformed_board_falls_back_to_the_file_mtime(self):
        _write_board(self.path, raw="{", mtime=NOW - 20 * 60)
        self.assertEqual(mon.pm_board_stamp(NOW, str(self.path)), NOW - 20 * 60)
        self.assertEqual(self._line(), "⚠ PM board stale 20 min")
        _write_board(self.path, raw="{", mtime=NOW - 60)
        self.assertEqual(self._line(), "")
        _write_board(self.path, raw="[1, 2]", mtime=NOW - 30 * 60)
        self.assertEqual(self._line(), "⚠ PM board stale 30 min")

    def test_absent_invalid_or_future_updated_at_falls_back_to_mtime(self):
        for label, updated_at in (("absent", None), ("not a stamp", "bad"),
                                  ("wrong type", 12345), ("impossible date", "2026-13-40T00:00:00Z"),
                                  ("future", _stamp(NOW + 3600))):
            with self.subTest(label):
                _write_board(self.path, updated_at=updated_at, mtime=NOW - 25 * 60)
                self.assertEqual(mon.pm_board_stamp(NOW, str(self.path)), NOW - 25 * 60)
                self.assertEqual(self._line(), "⚠ PM board stale 25 min")

    def test_oversized_board_falls_back_to_mtime(self):
        _write_board(self.path, raw=" " * (mon.PM_BOARD_LIMIT + 1), mtime=NOW - 40 * 60)
        self.assertEqual(self._line(), "⚠ PM board stale 40 min")

    def test_symlink_or_directory_is_not_followed(self):
        _write_board(self.path, updated_at=_stamp(NOW - 3600), mtime=NOW - 3600)
        link = self.root / "link.json"
        link.symlink_to(self.path)
        self.assertIsNone(mon.pm_board_stamp(NOW, str(link)))
        self.assertIsNone(mon.pm_board_stamp(NOW, str(self.root)))

    def test_default_path_is_the_pm_board(self):
        _write_board(self.path, updated_at=_stamp(NOW - 17 * 60), mtime=NOW)
        with patch.object(mon, "PM_BOARD", str(self.path)):
            self.assertEqual(mon.pm_board_stamp(NOW), NOW - 17 * 60)

    def test_board_refresh_cadence_stays_thirty_seconds(self):
        # The stamp is read inside the existing 30 s board block of Monitor.poll.
        source = Path(mon.__file__).read_text(encoding="utf-8")
        block = source[source.index("if now >= self.pm_board_due:"):]
        block = block[:block.index("stamp = datetime.now()")]
        self.assertIn("self.pm_board_due = now + 30.0", block)
        self.assertIn("self.pm_board_stamp = pm_board_stamp(time.time())", block)

    def test_top_row_shows_the_warning_only_when_stale(self):
        for view in ("table", "office", "board"):
            with self.subTest(view):
                drawn = _paint(_monitor(pm_board_stamp=NOW - 20 * 60), view)
                self.assertIn("⚠ PM board stale 20 min", drawn[2][0])
                drawn = _paint(_monitor(pm_board_stamp=NOW - 60), view)
                self.assertNotIn("PM board stale", "\n".join(text for text, _ in drawn.values()))
                drawn = _paint(_monitor(pm_board_stamp=None), view)
                self.assertNotIn("PM board stale", "\n".join(text for text, _ in drawn.values()))

    def test_top_row_keeps_other_warnings_with_the_board_warning(self):
        drawn = _paint(_monitor(pm_board_stamp=NOW - 20 * 60, behind=True), "table")
        self.assertIn(mon.BEHIND_WARNING, drawn[2][0])
        self.assertIn("⚠ PM board stale 20 min", drawn[2][0])

    def test_paint_without_the_attribute_gives_no_warning(self):
        monitor = _monitor()
        del monitor.pm_board_stamp
        drawn = _paint(monitor, "table")
        self.assertNotIn(2, drawn)


class _FakeCurses:
    """Just enough of the curses colour API to record the pairs."""
    COLOR_BLACK, COLOR_RED, COLOR_GREEN, COLOR_YELLOW = 0, 1, 2, 3
    COLOR_BLUE, COLOR_MAGENTA, COLOR_CYAN, COLOR_WHITE = 4, 5, 6, 7
    A_REVERSE = curses.A_REVERSE

    def __init__(self, pairs=256, colours=256):
        self.COLOR_PAIRS, self.COLORS = pairs, colours
        self.pairs = {}

    def init_pair(self, n, fg, bg):
        self.pairs[n] = (fg, bg)

    def color_pair(self, n):
        return n << 8


class RuleAlertGoneTests(unittest.TestCase):
    def test_office_paint_shows_no_rule_file_alert_even_with_an_old_record(self):
        # Owner 2026-11-22: no rule-file merge alert at the top of the monitor.
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"HOME": home}):
            state = Path(home, ".local/state/llmtool")
            state.mkdir(parents=True)
            (state / "rule-file-alerts.json").write_text(json.dumps(
                {"schema_version": 1, "baseline": 0, "checked": [],
                 "alerts": [{"pr": 1, "title": "t", "paths": ["x"]}]}), encoding="utf-8")
            drawn = _paint(_monitor(quota=["quota warning"]), "office", {})
        texts = [text for text, _ in drawn.values()]
        self.assertIn("quota warning", texts)
        self.assertFalse(any("規則檔" in text for text in texts))


if __name__ == "__main__":
    unittest.main()
