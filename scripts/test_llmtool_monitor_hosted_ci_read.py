#!/usr/bin/env python3
"""Tests for GitHubSource hosted CI reading in scripts/llmtool_monitor.py.

Verifies that:
1. The default constructor does not read runs without runners.
2. With want_hosted_ci=True, it reads runs and the jobs of an in_progress run.
3. A failed runs read sets CI runs to None (never an older answer).
4. The monitor app sets source.want_hosted_ci = True.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

# Load monitor module like scripts/test_llmtool_monitor.py
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

MONITOR_PATH = os.path.join(HERE, "llmtool_monitor.py")
spec = importlib.util.spec_from_file_location("llmtool_monitor", MONITOR_PATH)
if spec is None or spec.loader is None:
    raise ImportError("failed to load llmtool_monitor")
mon = importlib.util.module_from_spec(spec)
sys.modules["llmtool_monitor"] = mon
spec.loader.exec_module(mon)

GitHubSource = mon.GitHubSource
Monitor = mon.Monitor
Snapshot = mon.Snapshot

_ISOLATED = tempfile.TemporaryDirectory()
_RULE_ALERT_ISOLATED = tempfile.TemporaryDirectory()
_RULE_ALERT_PATCH = mock.patch.object(
    mon, "RULE_ALERT_FILE",
    os.path.join(_RULE_ALERT_ISOLATED.name, "rule-file-alerts.json"))


def setUpModule():
    # Isolate session directories so tests never read developer agent sessions
    for name in (
        "CODEX_SESSIONS", "CODEX_HISTORY", "CODEX_STATE", "CLAUDE_PROJECTS",
        "CLAUDE_OTHER_PROJECTS", "OPENCODE_DB", "KILO_DB", "LLMTOOL_TASKS", "GW_AUDIT_ROOT"
    ):
        setattr(mon, name, os.path.join(_ISOLATED.name, name))
    _RULE_ALERT_PATCH.start()


def tearDownModule():
    _RULE_ALERT_PATCH.stop()
    _RULE_ALERT_ISOLATED.cleanup()
    _ISOLATED.cleanup()


class TestHostedCiRead(unittest.TestCase):
    """Test suite for hosted CI workflow runs and jobs reading."""

    def setUp(self):
        self.repo = "davidcatcat/davidcattoolsproject"
        self.recorded_calls: list[list[str]] = []
        self.runners_payload = {"runners": []}
        self.runs_payload = {
            "workflow_runs": [
                {
                    "id": 1001,
                    "name": "llmtool Linux CI",
                    "head_sha": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
                    "status": "in_progress",
                    "head_branch": "work-branch",
                    "created_at": "2026-11-10T00:00:00Z",
                    "updated_at": "2026-11-10T00:05:00Z",
                }
            ]
        }
        self.jobs_payload = {
            "jobs": [
                {
                    "id": 2001,
                    "name": "check (Linux 3.14)",
                    "status": "in_progress",
                    "runner_name": "hosted-runner-ubuntu",
                    "started_at": "2026-11-10T00:01:00Z",
                }
            ]
        }
        self.runs_error: str | None = None

        # Patch shutil.which so gh binary lookup succeeds
        self.patcher = mock.patch("shutil.which", return_value="/fake/bin/gh")
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def fake_runner(self, argv: list[str], **_kwargs):
        self.recorded_calls.append(list(argv))
        path = argv[4] if len(argv) > 4 else ""

        if "actions/runners" in path:
            stdout = json.dumps(self.runners_payload).encode("utf-8")
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout=stdout, stderr=b"")

        if "actions/runs?per_page=30" in path:
            if self.runs_error is not None:
                return subprocess.CompletedProcess(
                    args=argv, returncode=1, stdout=b"", stderr=self.runs_error.encode("utf-8")
                )
            stdout = json.dumps(self.runs_payload).encode("utf-8")
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout=stdout, stderr=b"")

        if "actions/runs/1001/jobs" in path:
            stdout = json.dumps(self.jobs_payload).encode("utf-8")
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout=stdout, stderr=b"")

        return subprocess.CompletedProcess(
            args=argv, returncode=1, stdout=b"", stderr=b"HTTP 404 Not Found"
        )

    def test_default_constructor_does_not_read_runs_without_runners(self):
        source = GitHubSource(self.repo, runner=self.fake_runner)
        self.assertFalse(source.want_hosted_ci)
        self.assertFalse(source.want_ci)

        source.refresh_ci()

        # Runners endpoint was called, but runs endpoint was not called
        paths = [call[4] for call in self.recorded_calls if len(call) > 4]
        self.assertTrue(any("actions/runners" in p for p in paths))
        self.assertFalse(any("actions/runs" in p for p in paths))
        self.assertIsNone(source.ci_runs)

    def test_want_hosted_ci_reads_runs_and_in_progress_jobs(self):
        source = GitHubSource(self.repo, runner=self.fake_runner, want_hosted_ci=True)
        self.assertTrue(source.want_hosted_ci)

        source.refresh_ci()

        paths = [call[4] for call in self.recorded_calls if len(call) > 4]
        self.assertTrue(any("actions/runners" in p for p in paths))
        self.assertTrue(any("actions/runs?per_page=30" in p for p in paths))
        self.assertTrue(any("actions/runs/1001/jobs" in p for p in paths))

        self.assertIsNotNone(source.ci_runs)
        self.assertEqual(len(source.ci_runs), 1)
        run_entry = source.ci_runs[0]
        status, text, _started, runner_name, run_id = run_entry
        self.assertEqual(status, "in_progress")
        self.assertIn("llmtool Linux CI", text)
        self.assertIn("check (Linux 3.14)", text)
        self.assertEqual(runner_name, "hosted-runner-ubuntu")
        self.assertEqual(run_id, 1001)

    def test_failed_runs_read_leaves_runs_none(self):
        source = GitHubSource(self.repo, runner=self.fake_runner, want_hosted_ci=True)

        # First refresh succeeds
        source.refresh_ci()
        self.assertIsNotNone(source.ci_runs)

        # Second refresh fails to read runs
        self.runs_error = "HTTP 500 Server Error"
        source.refresh_ci()

        # Must be set to None, never retaining the older answer
        self.assertIsNone(source.ci_runs)

    def test_monitor_sets_want_hosted_ci(self):
        source = GitHubSource(self.repo, runner=self.fake_runner)
        self.assertFalse(source.want_hosted_ci)

        monitor = Monitor()
        monitor.github = source

        empty_snapshot = Snapshot(processes=[], watched_groups={}, lingering=[])
        with mock.patch.object(mon, "collect", return_value=empty_snapshot), \
             mock.patch.object(mon, "scan_tasks", return_value=[]), \
             mock.patch.object(mon, "office_rows", return_value=[]), \
             mock.patch.object(mon, "llmtool_task_records", return_value=[]), \
             mock.patch.object(mon, "llmtool_route_outcomes", return_value={}), \
             mock.patch.object(mon, "quota_warnings", return_value=[]), \
             mock.patch.object(mon, "behind_main", return_value=[]):
            monitor.poll()

        self.assertTrue(source.want_hosted_ci)


if __name__ == "__main__":
    unittest.main()
