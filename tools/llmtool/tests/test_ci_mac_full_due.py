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
    def test_workflow_dispatch_is_due(self):
        self.assertTrue(ci_mac_full_due.is_due("workflow_dispatch"))

    def test_schedule_is_rejected_as_unexpected(self):
        with self.assertRaisesRegex(ValueError, "unexpected event 'schedule'"):
            ci_mac_full_due.is_due("schedule")

    def test_cli_emits_due_for_workflow_dispatch(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(
                ci_mac_full_due.main(
                    ["--repo", "owner/repo", "--sha", "a" * 40,
                     "--event", "workflow_dispatch"]
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
        self.assertIn("actions: read", workflow)
        self.assertIn("GH_API_VALUE: ${{ github.token }}", workflow)
        self.assertNotIn("secrets.", workflow)
        self.assertIn("no new merges since $main_sha", workflow)
        helper = (Path(__file__).resolve().parents[1] / "scripts" / "ci_mac_full_due.py").read_text()
        self.assertIn("only workflow_dispatch is supported", helper)
        self.assertNotIn('"schedule"', helper)


if __name__ == "__main__":
    unittest.main()
