"""Tests for GitHub write sites running under verified AI identity."""

import io
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from llmtool.ai_github_exec import AiIdentityError
from llmtool.errors import LLMToolError
from llmtool import high_debug, remote_test

_root = Path(__file__).resolve().parent.parent
_scripts_dir = _root / "scripts"
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

try:
    from scripts import pm_owner_marker
except ImportError:
    import pm_owner_marker


class TestWriteSitesAiIdentity(unittest.TestCase):
    """Verify that write operations enforce verified AI identity."""

    def test_high_debug_refusal_runs_no_subprocess(self):
        """high_debug post fails and skips subprocess on identity error."""
        with mock.patch("llmtool.ai_github_exec.verified_ai_env",
                        side_effect=AiIdentityError("AI_GH_LOGIN_MISMATCH")):
            with mock.patch("subprocess.run") as mock_run:
                with mock.patch("shutil.which", return_value="/bin/gh"):
                    with mock.patch("llmtool.task_completion._trusted_slug",
                                    return_value="test-owner/test-repo"):
                        result = high_debug._post_github_record(101, "comment body", "/repo")
                        self.assertFalse(result)
                        mock_run.assert_not_called()

    def test_high_debug_receives_gh_config_dir(self):
        """high_debug comment posting receives the verified GH_CONFIG_DIR."""
        fake_env = {"GH_CONFIG_DIR": "/fake/test/gh_config_dir"}
        with mock.patch("llmtool.ai_github_exec.verified_ai_env",
                        return_value=dict(fake_env)):
            with mock.patch("shutil.which", return_value="/bin/gh"):
                with mock.patch("llmtool.task_completion._trusted_slug",
                                return_value="test-owner/test-repo"):
                    proc = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
                    with mock.patch("subprocess.run", return_value=proc) as mock_run:
                        result = high_debug._post_github_record(101, "comment body", "/repo")
                        self.assertTrue(result)
                        self.assertTrue(mock_run.called)
                        called_env = mock_run.call_args.kwargs.get("env") or mock_run.call_args[1].get("env")
                        self.assertEqual(called_env.get("GH_CONFIG_DIR"), "/fake/test/gh_config_dir")
                        self.assertEqual(called_env.get("LC_ALL"), "C")
                        self.assertEqual(called_env.get("GH_PROMPT_DISABLED"), "1")
                        self.assertEqual(called_env.get("NO_COLOR"), "1")

    def test_remote_test_refusal_runs_no_subprocess(self):
        """remote_test workflow-dispatch POST refuses and skips subprocess."""
        path = "actions/workflows/llmtool-remote-test.yml/dispatches"
        body = {"ref": "main"}
        with mock.patch("llmtool.ai_github_exec.verified_ai_env",
                        side_effect=AiIdentityError("AI_GH_LOGIN_MISMATCH")):
            with mock.patch("subprocess.Popen") as mock_popen:
                with self.assertRaises(LLMToolError) as ctx:
                    remote_test._hosted_post(path, body)
                self.assertIn("GITHUB_AI_IDENTITY_REFUSED AI_GH_LOGIN_MISMATCH", str(ctx.exception))
                mock_popen.assert_not_called()

    def test_remote_test_dispatch_refusal_propagates(self):
        """remote_test _hosted_dispatch propagates identity refusal."""
        sha = "a" * 40
        ref = "refs/pull/1/head"
        modules = ["test_module"]
        nonce = "0" * 32
        with mock.patch("llmtool.ai_github_exec.verified_ai_env",
                        side_effect=AiIdentityError("AI_GH_LOGIN_MISMATCH")):
            with mock.patch("subprocess.Popen") as mock_popen:
                with self.assertRaises(LLMToolError) as ctx:
                    remote_test._hosted_dispatch(sha, ref, modules, nonce)
                self.assertIn("GITHUB_AI_IDENTITY_REFUSED AI_GH_LOGIN_MISMATCH", str(ctx.exception))
                mock_popen.assert_not_called()

    def test_remote_test_receives_gh_config_dir(self):
        """remote_test workflow-dispatch POST receives the verified GH_CONFIG_DIR."""
        fake_env = {"GH_CONFIG_DIR": "/fake/test/gh_config_dir"}
        path = "actions/workflows/llmtool-remote-test.yml/dispatches"
        body = {"ref": "main"}

        r_out, w_out = os.pipe()
        r_err, w_err = os.pipe()
        os.close(w_out)
        os.close(w_err)

        mock_proc = mock.MagicMock()
        mock_proc.stdout = open(r_out, "rb")
        mock_proc.stderr = open(r_err, "rb")
        mock_proc.stdin = io.BytesIO()
        mock_proc.poll.return_value = 0
        mock_proc.wait.return_value = 0

        try:
            with mock.patch("llmtool.ai_github_exec.verified_ai_env",
                            return_value=dict(fake_env)):
                with mock.patch("shutil.which", return_value="/bin/gh"):
                    with mock.patch("llmtool.remote_test._trusted_slug",
                                    return_value="test-owner/test-repo"):
                        with mock.patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
                            result = remote_test._hosted_post(path, body)
                            self.assertTrue(result)
                            self.assertTrue(mock_popen.called)
                            called_env = mock_popen.call_args.kwargs.get("env") or mock_popen.call_args[1].get("env")
                            self.assertEqual(called_env.get("GH_CONFIG_DIR"), "/fake/test/gh_config_dir")
                            self.assertEqual(called_env.get("LC_ALL"), "C")
                            self.assertEqual(called_env.get("GH_PROMPT_DISABLED"), "1")
                            self.assertEqual(called_env.get("NO_COLOR"), "1")
        finally:
            mock_proc.stdout.close()
            mock_proc.stderr.close()

    def test_pm_owner_marker_refusal_runs_no_subprocess(self):
        """pm_owner_marker comment posting refuses and skips subprocess."""
        with mock.patch.object(pm_owner_marker, "verified_ai_env",
                               side_effect=AiIdentityError("AI_GH_LOGIN_MISMATCH")):
            with mock.patch("subprocess.run") as mock_run:
                with self.assertRaises(pm_owner_marker.MarkerError) as ctx:
                    pm_owner_marker._post("101", "comment body", "test-owner/test-repo")
                self.assertIn("GITHUB_AI_IDENTITY_REFUSED AI_GH_LOGIN_MISMATCH", str(ctx.exception))
                mock_run.assert_not_called()

    def test_pm_owner_marker_receives_gh_config_dir(self):
        """pm_owner_marker comment posting receives the verified GH_CONFIG_DIR."""
        fake_env = {"GH_CONFIG_DIR": "/fake/test/gh_config_dir"}
        expected_url = "https://github.com/test-owner/test-repo/issues/101#issuecomment-999"
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=expected_url.encode("utf-8"), stderr=b""
        )
        with mock.patch.object(pm_owner_marker, "verified_ai_env",
                               return_value=dict(fake_env)):
            with mock.patch("subprocess.run", return_value=proc) as mock_run:
                url = pm_owner_marker._post("101", "comment body", "test-owner/test-repo")
                self.assertEqual(url, expected_url)
                self.assertTrue(mock_run.called)
                called_env = mock_run.call_args.kwargs.get("env") or mock_run.call_args[1].get("env")
                self.assertEqual(called_env.get("GH_CONFIG_DIR"), "/fake/test/gh_config_dir")
                self.assertEqual(called_env.get("LC_ALL"), "C")
                self.assertEqual(called_env.get("GH_PROMPT_DISABLED"), "1")
                self.assertEqual(called_env.get("NO_COLOR"), "1")


if __name__ == "__main__":
    unittest.main()
