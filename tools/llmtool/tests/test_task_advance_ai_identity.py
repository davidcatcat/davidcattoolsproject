"""Tests for AI GitHub identity enforcement in task_advance."""

from contextlib import redirect_stderr
import io
import subprocess
import unittest
from unittest.mock import patch

from llmtool.github_identity import AiIdentityError
from llmtool.task_advance import _publish_run_gh, _publish_run_git


class TaskAdvanceAiIdentityTests(unittest.TestCase):
    def test_publish_run_gh_passes_verified_env(self):
        fake_env = {"AI_GH_ISOLATED": "1", "PATH": "/bin"}
        with patch("llmtool.github_identity.verified_ai_env", return_value=fake_env):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    ["gh", "pr", "list"], 0, stdout=b"[]", stderr=b""
                )
                result = _publish_run_gh("/fake/root", ["pr", "list"])
                mock_run.assert_called_once()
                args, kwargs = mock_run.call_args
                self.assertEqual(args[0], ["gh", "pr", "list"])
                self.assertEqual(kwargs.get("cwd"), "/fake/root")
                self.assertEqual(kwargs.get("env"), fake_env)
                self.assertEqual(result.returncode, 0)

    def test_publish_run_git_passes_verified_env(self):
        fake_env = {"AI_GH_ISOLATED": "1", "PATH": "/bin"}
        with patch("llmtool.github_identity.verified_ai_env", return_value=fake_env):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    ["git", "status"], 0, stdout=b"", stderr=b""
                )
                result = _publish_run_git("/fake/root", ["status"])
                mock_run.assert_called_once()
                args, kwargs = mock_run.call_args
                self.assertEqual(args[0], ["git", "status"])
                self.assertEqual(kwargs.get("cwd"), "/fake/root")
                self.assertEqual(kwargs.get("env"), fake_env)
                self.assertEqual(result.returncode, 0)

    def test_identity_refusal_skips_subprocess_and_returns_code_2(self):
        with patch(
            "llmtool.github_identity.verified_ai_env",
            side_effect=AiIdentityError("AI_GH_LOGIN_MISMATCH"),
        ):
            with patch("subprocess.run") as mock_run:
                stderr_gh = io.StringIO()
                with redirect_stderr(stderr_gh):
                    res_gh = _publish_run_gh("/fake/root", ["pr", "list"])
                mock_run.assert_not_called()
                self.assertEqual(res_gh.returncode, 2)
                self.assertEqual(res_gh.stdout, b"")
                self.assertEqual(
                    stderr_gh.getvalue(),
                    "llmtool: GITHUB_AI_IDENTITY_REFUSED AI_GH_LOGIN_MISMATCH\n",
                )

            with patch("subprocess.run") as mock_run:
                stderr_git = io.StringIO()
                with redirect_stderr(stderr_git):
                    res_git = _publish_run_git("/fake/root", ["status"])
                mock_run.assert_not_called()
                self.assertEqual(res_git.returncode, 2)
                self.assertEqual(res_git.stdout, b"")
                self.assertEqual(
                    stderr_git.getvalue(),
                    "llmtool: GITHUB_AI_IDENTITY_REFUSED AI_GH_LOGIN_MISMATCH\n",
                )


if __name__ == "__main__":
    unittest.main()
