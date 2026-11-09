"""Tests for GitHub-hosted CI runner names and job tracking in llmtool_monitor.

Validates that ci_runner_names, ci_finished_jobs, and ci_queued_jobs handle
GitHub-hosted runner names (spaces, control character stripping, 64-character
boundary limits) as well as self-hosted runner names and jobs waiting for
runners.
"""

from __future__ import annotations

import os
import sys
import unittest

try:
    import llmtool_monitor as mon
except ImportError:
    try:
        from scripts import llmtool_monitor as mon
    except ImportError:
        import importlib.util

        _monitor_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "llmtool_monitor.py")
        )
        _spec = importlib.util.spec_from_file_location("llmtool_monitor", _monitor_path)
        mon = importlib.util.module_from_spec(_spec)
        sys.modules["llmtool_monitor"] = mon
        _spec.loader.exec_module(mon)


class TestHostedCiRunnerNames(unittest.TestCase):
    """Test suite for hosted and self-hosted runner name cleaning and tracking."""

    def test_clean_runner_name_hosted_with_spaces(self) -> None:
        """Hosted runner names containing spaces must be preserved."""
        self.assertEqual(mon.clean_runner_name("GitHub Actions 12"), "GitHub Actions 12")
        self.assertEqual(mon.clean_runner_name("Hosted Agent 4"), "Hosted Agent 4")

    def test_clean_runner_name_truncated_to_64_characters(self) -> None:
        """Runner names longer than 64 characters must be cut to 64."""
        long_name = "GitHub Actions " + ("x" * 60)
        self.assertGreater(len(long_name), 64)
        cleaned = mon.clean_runner_name(long_name)
        self.assertIsNotNone(cleaned)
        self.assertEqual(len(cleaned), 64)
        self.assertEqual(cleaned, long_name[:64])

    def test_clean_runner_name_control_characters_removed(self) -> None:
        """Control characters must be stripped while valid text is retained."""
        name_with_controls = "GitHub\x00 Actions\x1b 12\r\n"
        cleaned = mon.clean_runner_name(name_with_controls)
        self.assertEqual(cleaned, "GitHub Actions 12")

        tab_and_bell = "runner\tname\x07"
        self.assertEqual(mon.clean_runner_name(tab_and_bell), "runnername")

    def test_clean_runner_name_only_control_characters(self) -> None:
        """A name containing solely control characters should resolve to None."""
        only_controls = "\x00\x1b\x07\r\n"
        self.assertIsNone(mon.clean_runner_name(only_controls))

    def test_clean_runner_name_none_or_empty(self) -> None:
        """None or empty strings must yield None."""
        self.assertIsNone(mon.clean_runner_name(None))
        self.assertIsNone(mon.clean_runner_name(""))

    def test_clean_runner_name_self_hosted_unchanged(self) -> None:
        """Standard self-hosted runner names must remain intact."""
        self.assertEqual(
            mon.clean_runner_name("davidproject-self-action"),
            "davidproject-self-action",
        )
        self.assertEqual(
            mon.clean_runner_name("ec2-runner-arm64"),
            "ec2-runner-arm64",
        )

    def test_ci_runner_names_keeps_hosted_name_with_spaces(self) -> None:
        """ci_runner_names retains runner names with spaces for running jobs."""
        runs = [("in_progress", "PR 10", 1700000000.0, None, 101)]
        jobs_payload = {
            "jobs": [
                {
                    "name": "build",
                    "status": "in_progress",
                    "runner_name": "GitHub Actions 12",
                    "started_at": "2026-11-10T01:00:00Z",
                }
            ]
        }

        def jobs_of(run_id: int) -> dict:
            self.assertEqual(run_id, 101)
            return jobs_payload

        queued: list = []
        result = mon.ci_runner_names(runs, jobs_of, queued)
        self.assertEqual(len(result), 1)
        status, text, _started, runner_name, run_id = result[0]
        self.assertEqual(status, "in_progress")
        self.assertEqual(text, "PR 10：build")
        self.assertEqual(runner_name, "GitHub Actions 12")
        self.assertEqual(run_id, 101)
        self.assertEqual(queued, [])

    def test_ci_runner_names_cuts_runner_name_over_64_characters(self) -> None:
        """ci_runner_names cuts overlong runner names to 64 characters."""
        long_runner = "GitHub Actions " + ("y" * 60)
        runs = [("in_progress", "PR 11", 1700000000.0, None, 102)]
        jobs_payload = {
            "jobs": [
                {
                    "name": "lint",
                    "status": "in_progress",
                    "runner_name": long_runner,
                    "started_at": "2026-11-10T01:00:00Z",
                }
            ]
        }
        result = mon.ci_runner_names(runs, lambda _: jobs_payload)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0][3]), 64)
        self.assertEqual(result[0][3], long_runner[:64])

    def test_ci_runner_names_strips_control_characters(self) -> None:
        """ci_runner_names strips control characters from runner names."""
        runs = [("in_progress", "PR 12", 1700000000.0, None, 103)]
        jobs_payload = {
            "jobs": [
                {
                    "name": "test-linux",
                    "status": "in_progress",
                    "runner_name": "GitHub\x00 Actions\x1b 12\n",
                    "started_at": "2026-11-10T01:00:00Z",
                }
            ]
        }
        result = mon.ci_runner_names(runs, lambda _: jobs_payload)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][3], "GitHub Actions 12")

    def test_ci_runner_names_in_progress_without_runner_counted_as_running(self) -> None:
        """In-progress jobs without runner names are counted as running and kept."""
        runs = [("in_progress", "PR 13", 1700000000.0, None, 104)]
        jobs_payload = {
            "jobs": [
                {
                    "name": "provisioning",
                    "status": "in_progress",
                    "runner_name": None,
                    "started_at": "2026-11-10T01:00:00Z",
                }
            ]
        }
        result = mon.ci_runner_names(runs, lambda _: jobs_payload)
        self.assertEqual(len(result), 1)
        status, text, _started, runner_name, run_id = result[0]
        self.assertEqual(status, "in_progress")
        self.assertEqual(text, "PR 13：provisioning")
        self.assertIsNone(runner_name)
        self.assertEqual(run_id, 104)

    def test_ci_runner_names_self_hosted_unchanged(self) -> None:
        """ci_runner_names preserves self-hosted runner names."""
        runs = [("in_progress", "PR 14", 1700000000.0, None, 105)]
        jobs_payload = {
            "jobs": [
                {
                    "name": "test-mac",
                    "status": "in_progress",
                    "runner_name": "davidproject-self-action",
                    "started_at": "2026-11-10T01:00:00Z",
                }
            ]
        }
        result = mon.ci_runner_names(runs, lambda _: jobs_payload)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][3], "davidproject-self-action")

    def test_ci_runner_names_and_queued_jobs_extraction(self) -> None:
        """ci_runner_names populates queued jobs when waiting jobs exist."""
        runs = [("in_progress", "PR 15", 1700000000.0, None, 106)]
        jobs_payload = {
            "jobs": [
                {
                    "name": "run-step",
                    "status": "in_progress",
                    "runner_name": "GitHub Actions 12",
                    "started_at": "2026-11-10T01:00:00Z",
                },
                {
                    "name": "wait-step",
                    "status": "queued",
                    "labels": ["ubuntu-24.04", "x64"],
                },
            ]
        }
        queued: list = []
        result = mon.ci_runner_names(runs, lambda _: jobs_payload, queued)
        self.assertEqual(len(result), 2)
        # First entry: the in_progress job
        self.assertEqual(result[0][0], "in_progress")
        self.assertEqual(result[0][3], "GitHub Actions 12")
        # Second entry: queued marker because another job is waiting
        self.assertEqual(result[1][0], "queued")
        self.assertIsNone(result[1][3])
        # Queued list populated via ci_queued_jobs
        self.assertEqual(len(queued), 1)
        labels, queued_text = queued[0]
        self.assertEqual(labels, frozenset(["ubuntu-24.04", "x64"]))
        self.assertEqual(queued_text, "PR 15：wait-step")

    def test_ci_finished_jobs_hosted_with_spaces(self) -> None:
        """ci_finished_jobs includes completed jobs with hosted runner names."""
        payload = {
            "jobs": [
                {
                    "name": "build",
                    "status": "completed",
                    "conclusion": "success",
                    "runner_name": "GitHub Actions 12",
                    "completed_at": "2026-11-10T01:10:00Z",
                }
            ]
        }
        finished = mon.ci_finished_jobs(payload, "PR 20")
        self.assertEqual(len(finished), 1)
        runner, text, conclusion, completed_at = finished[0]
        self.assertEqual(runner, "GitHub Actions 12")
        self.assertEqual(text, "PR 20：build")
        self.assertEqual(conclusion, "成功")
        self.assertGreater(completed_at, 0)

    def test_ci_finished_jobs_cuts_overlong_name_and_cleans_controls(self) -> None:
        """ci_finished_jobs cleans control chars and cuts overlong runner names."""
        long_runner = "GitHub\x00 Actions\x1b " + ("z" * 60)
        payload = {
            "jobs": [
                {
                    "name": "check",
                    "status": "completed",
                    "conclusion": "failure",
                    "runner_name": long_runner,
                    "completed_at": "2026-11-10T01:10:00Z",
                }
            ]
        }
        finished = mon.ci_finished_jobs(payload, "PR 21")
        self.assertEqual(len(finished), 1)
        runner = finished[0][0]
        self.assertEqual(len(runner), 64)
        self.assertNotIn("\x00", runner)
        self.assertNotIn("\x1b", runner)
        self.assertEqual(finished[0][2], "失敗")

    def test_ci_finished_jobs_self_hosted_unchanged(self) -> None:
        """ci_finished_jobs preserves self-hosted runner names."""
        payload = {
            "jobs": [
                {
                    "name": "deploy",
                    "status": "completed",
                    "conclusion": "success",
                    "runner_name": "davidproject-self-action",
                    "completed_at": "2026-11-10T01:10:00Z",
                }
            ]
        }
        finished = mon.ci_finished_jobs(payload, "PR 22")
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0][0], "davidproject-self-action")

    def test_ci_finished_jobs_ignores_missing_or_empty_runners(self) -> None:
        """Jobs with no runner name or unresolvable names are omitted."""
        payload = {
            "jobs": [
                {
                    "name": "skipped-job",
                    "status": "completed",
                    "conclusion": "skipped",
                    "runner_name": None,
                },
                {
                    "name": "control-only",
                    "status": "completed",
                    "conclusion": "cancelled",
                    "runner_name": "\x00\x1b",
                },
            ]
        }
        finished = mon.ci_finished_jobs(payload, "PR 23")
        self.assertEqual(finished, [])

    def test_ci_queued_jobs_direct(self) -> None:
        """ci_queued_jobs returns waiting jobs and their requested labels."""
        payload = {
            "jobs": [
                {
                    "name": "pending-linux",
                    "status": "queued",
                    "labels": ["ubuntu-24.04", "x64"],
                },
                {
                    "name": "pending-mac",
                    "status": "waiting",
                    "labels": ["macos-15"],
                },
                {
                    "name": "running-job",
                    "status": "in_progress",
                    "labels": ["ubuntu-24.04"],
                },
                {
                    "name": "done-job",
                    "status": "completed",
                    "conclusion": "success",
                    "labels": ["ubuntu-24.04"],
                },
            ]
        }
        queued = mon.ci_queued_jobs(payload, "PR 30")
        self.assertEqual(len(queued), 2)
        self.assertEqual(queued[0], (frozenset(["ubuntu-24.04", "x64"]), "PR 30：pending-linux"))
        self.assertEqual(queued[1], (frozenset(["macos-15"]), "PR 30：pending-mac"))


if __name__ == "__main__":
    unittest.main()
