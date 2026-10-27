from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import ci_mac_full_due  # noqa: E402


SHA = "a" * 40
OTHER_SHA = "b" * 40


class MacFullDueTests(unittest.TestCase):
    def api(self, runs):
        payload = io.BytesIO(json.dumps({"workflow_runs": runs}).encode("utf-8"))
        return patch("ci_mac_full_due.urlopen", return_value=payload)

    def _is_due(self, event="schedule"):
        api_value = "fixture-" + "value"
        return ci_mac_full_due.is_due("owner/repo", SHA, api_value, event)

    def test_no_prior_run_is_due(self):
        with self.api([]):
            self.assertTrue(self._is_due())

    def test_same_sha_skips(self):
        runs = [{"event": "schedule", "head_branch": "main", "conclusion": "success",
                 "head_sha": SHA, "created_at": "2026-10-27T00:00:00Z"}]
        with self.api(runs):
            self.assertFalse(self._is_due())

    def test_new_sha_is_due(self):
        runs = [{"event": "workflow_dispatch", "head_branch": "main", "conclusion": "success",
                 "head_sha": OTHER_SHA, "created_at": "2026-10-27T00:00:00Z"}]
        with self.api(runs):
            self.assertTrue(self._is_due())

    def test_api_error_fails_toward_due(self):
        with patch("ci_mac_full_due.urlopen", side_effect=OSError("unavailable")):
            self.assertTrue(self._is_due())

    def test_dispatch_is_always_due_without_api_call(self):
        with patch("ci_mac_full_due.urlopen", side_effect=AssertionError("unexpected API call")):
            self.assertTrue(ci_mac_full_due.is_due("owner/repo", SHA, None, "workflow_dispatch"))

    def test_workflow_uses_six_hour_schedule_without_main_push(self):
        workflow = (Path(__file__).resolve().parents[3] / ".github/workflows/llmtool-mac-ci.yml").read_text()
        trigger = workflow.split("on:\n", 1)[1].split("permissions:\n", 1)[0]
        self.assertIn("cron: '0 */6 * * *'", trigger)
        self.assertIn("pull_request:", trigger)
        self.assertIn("workflow_dispatch:", trigger)
        self.assertNotIn("push:", trigger)
        self.assertIn("workflow_dispatch should be used after re-enabling", workflow)
        self.assertIn("actions: read", workflow)
        self.assertIn("GH_API_VALUE: ${{ github.token }}", workflow)
        self.assertNotIn("secrets.", workflow)
        self.assertIn("no new merges since $main_sha", workflow)
        helper = (Path(__file__).resolve().parents[1] / "scripts" / "ci_mac_full_due.py").read_text()
        self.assertIn('os.environ.get("GH_API_VALUE")', helper)


if __name__ == "__main__":
    unittest.main()
