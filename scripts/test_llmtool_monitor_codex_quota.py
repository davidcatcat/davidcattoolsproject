#!/usr/bin/env python3
"""Tests for Codex quota reading, CLI flag, and warnings in llmtool_monitor."""

import contextlib
from datetime import datetime
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

TEMP_HOME = tempfile.mkdtemp(prefix="test-llmtool-codex-quota-home-")
ORIG_HOME = os.environ.get("HOME")
os.environ["HOME"] = TEMP_HOME

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MONITOR_PATH = os.path.join(SCRIPT_DIR, "llmtool_monitor.py")

spec = importlib.util.spec_from_file_location("llmtool_monitor", MONITOR_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"unable to load {MONITOR_PATH}")
monitor = importlib.util.module_from_spec(spec)
sys.modules["llmtool_monitor"] = monitor
spec.loader.exec_module(monitor)


def tearDownModule():
    if ORIG_HOME is not None:
        os.environ["HOME"] = ORIG_HOME
    else:
        os.environ.pop("HOME", None)
    shutil.rmtree(TEMP_HOME, ignore_errors=True)


class TestLlmtoolMonitorCodexQuota(unittest.TestCase):
    def setUp(self):
        self.quota_dir = os.path.join(TEMP_HOME, ".local", "state", "llmtool")
        self.quota_path = os.path.join(self.quota_dir, "codex-quota.json")
        os.makedirs(self.quota_dir, exist_ok=True)
        if os.path.lexists(self.quota_path):
            os.remove(self.quota_path)

    def tearDown(self):
        if os.path.lexists(self.quota_path):
            try:
                os.remove(self.quota_path)
            except OSError:
                pass

    def _write_quota_file(self, data, mode=0o600):
        if isinstance(data, (dict, list)):
            raw = json.dumps(data).encode("utf-8")
        elif isinstance(data, str):
            raw = data.encode("utf-8")
        else:
            raw = bytes(data)
        with open(self.quota_path, "wb") as f:
            f.write(raw)
        os.chmod(self.quota_path, mode)

    def test_read_codex_quota_fresh(self):
        now = 1800000000.0
        data = {
            "schema_version": 1,
            "primary": {
                "used_percent": 45,
                "window_minutes": 300,
                "resets_at": now + 7200,
            },
            "secondary": {
                "used_percent": 30,
                "window_minutes": 10080,
                "resets_at": now + 86400,
            },
            "read_time": now - 60,
        }
        self._write_quota_file(data, mode=0o600)
        res = monitor.read_codex_quota(now)
        self.assertEqual(res.get("state"), "FRESH")
        self.assertEqual(res.get("schema_version"), 1)
        self.assertEqual(res["primary"]["used_percent"], 45)
        self.assertEqual(res["secondary"]["used_percent"], 30)

    def test_read_codex_quota_unknown_missing(self):
        res = monitor.read_codex_quota(1800000000.0)
        self.assertEqual(res, {"state": "UNKNOWN", "reason": "missing"})

    def test_read_codex_quota_unknown_malformed_json(self):
        self._write_quota_file("{invalid json", mode=0o600)
        res = monitor.read_codex_quota(1800000000.0)
        self.assertEqual(res, {"state": "UNKNOWN", "reason": "malformed"})

    def test_read_codex_quota_unknown_wrong_mode(self):
        now = 1800000000.0
        data = {
            "schema_version": 1,
            "primary": {
                "used_percent": 10,
                "window_minutes": 300,
                "resets_at": now + 3600,
            },
            "secondary": {
                "used_percent": 20,
                "window_minutes": 10080,
                "resets_at": now + 86400,
            },
            "read_time": now - 60,
        }
        self._write_quota_file(data, mode=0o644)
        res = monitor.read_codex_quota(now)
        self.assertEqual(res, {"state": "UNKNOWN", "reason": "wrong mode"})

    def test_read_codex_quota_unknown_stale_read_time(self):
        now = 1800000000.0
        data = {
            "schema_version": 1,
            "primary": {
                "used_percent": 10,
                "window_minutes": 300,
                "resets_at": now + 3600,
            },
            "secondary": {
                "used_percent": 20,
                "window_minutes": 10080,
                "resets_at": now + 86400,
            },
            "read_time": now - 301 * 60,
        }
        self._write_quota_file(data, mode=0o600)
        res = monitor.read_codex_quota(now)
        self.assertEqual(res, {"state": "UNKNOWN", "reason": "stale"})

    def test_main_codex_quota_cli(self):
        now = monitor.time.time()
        data = {
            "schema_version": 1,
            "primary": {
                "used_percent": 50,
                "window_minutes": 300,
                "resets_at": now + 3600,
            },
            "secondary": {
                "used_percent": 25,
                "window_minutes": 10080,
                "resets_at": now + 86400,
            },
            "read_time": now - 10,
        }
        self._write_quota_file(data, mode=0o600)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ret = monitor.main(["--codex-quota"])
        self.assertEqual(ret, 0)
        lines = buf.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertEqual(payload.get("state"), "FRESH")
        self.assertEqual(payload.get("primary", {}).get("used_percent"), 50)

    def test_quota_warnings_warns_from_fresh_file(self):
        now = 1800000000.0
        reset = now + 7200
        data = {
            "schema_version": 1,
            "primary": {
                "used_percent": monitor.QUOTA_WARN_PERCENT,
                "window_minutes": 300,
                "resets_at": reset,
            },
            "secondary": {
                "used_percent": 40,
                "window_minutes": 10080,
                "resets_at": now + 86400,
            },
            "read_time": now - 120,
        }
        self._write_quota_file(data, mode=0o600)
        warnings = monitor.quota_warnings(now)
        self.assertEqual(len(warnings), 1)
        reset_local = datetime.fromtimestamp(reset)
        reset_fmt = "%H:%M" if reset_local.date() == datetime.fromtimestamp(now).date() else "%m-%d %H:%M"
        expected = f"Codex 5 小時額度已用 {monitor.QUOTA_WARN_PERCENT}%，{reset_local:{reset_fmt}} 重置（紀錄 2 分鐘前）"
        self.assertEqual(warnings[0], expected)

    def test_quota_warnings_none_for_unknown(self):
        now = 1800000000.0
        self.assertEqual(monitor.quota_warnings(now), [])

        self._write_quota_file("{bad json", mode=0o600)
        self.assertEqual(monitor.quota_warnings(now), [])

        valid_data = {
            "schema_version": 1,
            "primary": {
                "used_percent": 95,
                "window_minutes": 300,
                "resets_at": now + 3600,
            },
            "secondary": {
                "used_percent": 90,
                "window_minutes": 10080,
                "resets_at": now + 86400,
            },
            "read_time": now - 60,
        }
        self._write_quota_file(valid_data, mode=0o644)
        self.assertEqual(monitor.quota_warnings(now), [])

        stale_data = dict(valid_data)
        stale_data["read_time"] = now - 400 * 60
        self._write_quota_file(stale_data, mode=0o600)
        self.assertEqual(monitor.quota_warnings(now), [])


if __name__ == "__main__":
    unittest.main()
