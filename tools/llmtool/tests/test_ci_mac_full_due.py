from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import sys
import unittest


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import ci_mac_full_due  # noqa: E402


class MacFullDueTests(unittest.TestCase):
    def github(self, conclusion="success", sha="a" * 40):
        due = ci_mac_full_due.mac_full_due
        runs = [{"id": 7, "status": "completed", "conclusion": conclusion, "head_sha": sha}]
        jobs = [{"name": name, "conclusion": "success"} for name in due.JOBS]
        return lambda path: {"workflow_runs": runs} if path == due.RUNS else {"jobs": jobs}

    def test_passed_sha_skips_new_or_failed_sha_is_due(self):
        self.assertFalse(ci_mac_full_due.is_due("workflow_dispatch", "a" * 40, self.github()))
        self.assertTrue(ci_mac_full_due.is_due("workflow_dispatch", "b" * 40, self.github()))
        failed = self.github("failure")
        self.assertTrue(ci_mac_full_due.is_due("workflow_dispatch", "a" * 40, failed))

    def test_rerun_of_the_same_run_is_due(self):
        self.assertTrue(ci_mac_full_due.is_due("workflow_dispatch", "a" * 40, self.github(), 7))

    def test_api_failure_is_never_skip_or_due(self):
        def broken(path):
            raise OSError("api down")
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = ci_mac_full_due.main(["--repo", "o/r", "--sha", "a" * 40,
                                         "--event", "workflow_dispatch"], broken)
        self.assertEqual((code, stdout.getvalue()), (1, ""))
        self.assertIn("unavailable", stderr.getvalue())

    def test_schedule_is_rejected_as_unexpected(self):
        with self.assertRaisesRegex(ValueError, "unexpected event 'schedule'"):
            ci_mac_full_due.is_due("schedule", "a" * 40, None)

    def test_cli_emits_due_for_workflow_dispatch(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(
                ci_mac_full_due.main(
                    ["--repo", "owner/repo", "--sha", "b" * 40,
                     "--event", "workflow_dispatch", "--run-id", "8"], self.github()
                ),
                0,
            )
        self.assertEqual(stdout.getvalue(), "DUE\n")

    def test_cli_rejects_schedule_with_usage_error(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            ci_mac_full_due.main(
                ["--repo", "owner/repo", "--sha", "a" * 40, "--event", "schedule"]
            )
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("invalid choice: 'schedule'", stderr.getvalue())
        self.assertIn("workflow_dispatch", stderr.getvalue())

    def test_workflow_uses_manual_full_suite_without_schedule_or_main_push(self):
        workflow = (Path(__file__).resolve().parents[3] / ".github/workflows/llmtool-mac-ci.yml").read_text()
        trigger = workflow.split("on:\n", 1)[1].split("permissions:\n", 1)[0]
        self.assertIn("pull_request:", trigger)
        self.assertIn("workflow_dispatch:", trigger)
        self.assertNotIn("schedule:", trigger)
        self.assertNotIn("cron:", trigger)
        self.assertNotIn("push:", trigger)
        self.assertIn("Use workflow_dispatch after each merge train", workflow)
        self.assertIn("llmtool ci full-mac-due", workflow)
        # Case M: every job tests github.sha, the SHA the run reports.
        self.assertEqual(workflow.count("ref: ${{ github.event.pull_request.head.sha || github.sha }}"), 3)
        self.assertNotIn("|| 'main' }}", workflow)
        self.assertIn('--sha "$main_sha" --event "$GITHUB_EVENT_NAME" --run-id "$GITHUB_RUN_ID"', workflow)
        self.assertIn("actions: read", workflow)
        self.assertIn("GH_API_VALUE: ${{ github.token }}", workflow)
        self.assertNotIn("secrets.", workflow)
        self.assertIn("full main suite already passed at $main_sha", workflow)
        helper = (Path(__file__).resolve().parents[1] / "scripts" / "ci_mac_full_due.py").read_text()
        self.assertIn("only workflow_dispatch is supported", helper)
        self.assertNotIn('"schedule"', helper)


if __name__ == "__main__":
    unittest.main()
