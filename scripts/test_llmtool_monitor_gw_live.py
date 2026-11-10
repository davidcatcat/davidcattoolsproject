#!/usr/bin/env python3
"""Tests for company-gw live Task rows in llmtool_monitor."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest

# Load the monitor module dynamically like scripts/test_llmtool_monitor_hosted_ci_read.py
MONITOR_PATH = os.path.join(os.path.dirname(__file__), "llmtool_monitor.py")
spec = importlib.util.spec_from_file_location("llmtool_monitor", MONITOR_PATH)
monitor = importlib.util.module_from_spec(spec)
sys.modules["llmtool_monitor"] = monitor
spec.loader.exec_module(monitor)


class TestLlmtoolMonitorGwLive(unittest.TestCase):
    def setUp(self):
        monitor._llmtool_records.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.now = 1700000000.0

    def tearDown(self):
        monitor._llmtool_records.clear()
        self.tmp.cleanup()

    def _write_record(self, filename: str, data: dict, mtime: float | None = None) -> str:
        path = os.path.join(self.root, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def test_fresh_company_gw_dispatching_shows_row(self):
        """A fresh company-gw Task in DEVELOPER_DISPATCHING gives one live row."""
        record = {
            "task_id": "48c0f3bc66094ac882a9ee7686accc4e",
            "developer": {
                "provider_profile": "company-gw",
                "state": "DEVELOPER_DISPATCHING",
                "model": "gpt-4o",
                "executor_task_id": "task-d6-worktree-1",
            },
        }
        self._write_record("task-1.json", record, mtime=self.now - 30.0)
        rows = monitor.gw_task_rows(self.now, self.root)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.name, "home LLM 48c0f3bc")
        self.assertEqual(row.state, monitor.WORKING)
        self.assertEqual(row.kind, "subagent")
        self.assertIn("task-d6-worktree-1", row.detail)
        self.assertIn("gpt-4o", row.detail)
        self.assertIn("工作中", row.detail)
        self.assertAlmostEqual(row.age, 30.0)

    def test_stale_record_older_than_office_done_window_gives_none(self):
        """An old record outside OFFICE_DONE_WINDOW gives no live row."""
        record = {
            "task_id": "48c0f3bc66094ac882a9ee7686accc4e",
            "developer": {
                "provider_profile": "company-gw",
                "state": "DEVELOPER_DISPATCHING",
                "model": "gpt-4o",
                "executor_task_id": "task-d6-worktree-1",
            },
        }
        stale_mtime = self.now - monitor.OFFICE_DONE_WINDOW - 60.0
        self._write_record("task-stale.json", record, mtime=stale_mtime)
        rows = monitor.gw_task_rows(self.now, self.root)
        self.assertEqual(len(rows), 0)

    def test_profile_agy_gives_none(self):
        """A record with provider_profile agy gives no company-gw live row."""
        record = {
            "task_id": "48c0f3bc66094ac882a9ee7686accc4e",
            "developer": {
                "provider_profile": "agy",
                "state": "DEVELOPER_DISPATCHING",
                "model": "gpt-4o",
                "executor_task_id": "task-d6-worktree-1",
            },
        }
        self._write_record("task-agy.json", record, mtime=self.now - 30.0)
        rows = monitor.gw_task_rows(self.now, self.root)
        self.assertEqual(len(rows), 0)

    def test_request_response_text_never_in_row(self):
        """Request and response text in a Task record never appear in any row."""
        req_text = "Please write unit tests for the calculator function"
        resp_text = "Added tests for addition and subtraction"
        record = {
            "task_id": "48c0f3bc66094ac882a9ee7686accc4e",
            "developer": {
                "provider_profile": "company-gw",
                "state": "DEVELOPER_DISPATCHING",
                "model": "gpt-4o",
                "executor_task_id": "task-d6-worktree-1",
            },
            "request": req_text,
            "response": resp_text,
            "summary": "Calculator test summary",
            "acceptance": "Should pass all tests",
        }
        self._write_record("task-with-text.json", record, mtime=self.now - 10.0)
        rows = monitor.gw_task_rows(self.now, self.root)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        combined = f"{row.name} {row.detail} {row.evidence} {row.state} {row.kind}"
        self.assertNotIn(req_text, combined)
        self.assertNotIn(resp_text, combined)
        self.assertNotIn("Calculator test summary", combined)
        self.assertNotIn("Should pass all tests", combined)


if __name__ == "__main__":
    unittest.main()
