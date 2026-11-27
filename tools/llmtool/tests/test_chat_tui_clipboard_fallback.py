"""Clipboard fallback tests for chat_tui_view.copy_to_clipboard."""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmtool import chat_tui_view


class CopyToClipboardFallbackTests(unittest.TestCase):
    """Verify Linux clipboard fallback order and macOS single-tool behavior."""

    LINUX_COMMANDS = [
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
    ]

    def _assert_run_call(self, call, command, payload):
        self.assertEqual(call.args[0], command)
        self.assertEqual(call.kwargs["input"], payload)
        self.assertEqual(call.kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(call.kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(call.kwargs["timeout"], 3)
        self.assertIs(call.kwargs["check"], False)
        self.assertFalse(call.kwargs.get("shell", False))

    def test_linux_first_nonzero_exit_falls_back_and_stops_on_first_success(self):
        text = "中文 text\nline"
        payload = text.encode("utf-8")
        with patch.object(chat_tui_view.sys, "platform", "linux"), \
                patch.object(chat_tui_view.shutil, "which",
                             side_effect=lambda name: "/usr/bin/" + name), \
                patch.object(chat_tui_view.subprocess, "run",
                             side_effect=[Mock(returncode=1), Mock(returncode=0)]) as run:
            self.assertTrue(chat_tui_view.copy_to_clipboard(text))

        self.assertEqual(run.call_count, 2)
        self._assert_run_call(run.call_args_list[0], self.LINUX_COMMANDS[0], payload)
        self._assert_run_call(run.call_args_list[1], self.LINUX_COMMANDS[1], payload)

    def test_linux_bounded_process_exceptions_fall_back_to_next_candidate(self):
        text = "fallback after exception"
        payload = text.encode("utf-8")
        with patch.object(chat_tui_view.sys, "platform", "linux"), \
                patch.object(chat_tui_view.shutil, "which",
                             side_effect=lambda name: "/usr/bin/" + name), \
                patch.object(chat_tui_view.subprocess, "run",
                             side_effect=[
                                 OSError("wl-copy failed"),
                                 subprocess.TimeoutExpired(self.LINUX_COMMANDS[1], 3),
                                 Mock(returncode=0),
                             ]) as run:
            self.assertTrue(chat_tui_view.copy_to_clipboard(text))

        self.assertEqual(run.call_count, 3)
        for call, command in zip(run.call_args_list, self.LINUX_COMMANDS):
            self._assert_run_call(call, command, payload)

    def test_linux_skips_missing_tool_and_falls_back_on_failure(self):
        text = "skip missing"
        payload = text.encode("utf-8")

        def fake_which(name):
            return None if name == "wl-copy" else "/usr/bin/" + name

        with patch.object(chat_tui_view.sys, "platform", "linux"), \
                patch.object(chat_tui_view.shutil, "which", side_effect=fake_which), \
                patch.object(chat_tui_view.subprocess, "run",
                             side_effect=[Mock(returncode=2), Mock(returncode=0)]) as run:
            self.assertTrue(chat_tui_view.copy_to_clipboard(text))

        self.assertEqual(run.call_count, 2)
        self._assert_run_call(run.call_args_list[0], self.LINUX_COMMANDS[1], payload)
        self._assert_run_call(run.call_args_list[1], self.LINUX_COMMANDS[2], payload)

    def test_linux_all_candidates_fail_returns_false(self):
        text = "all fail"
        payload = text.encode("utf-8")
        with patch.object(chat_tui_view.sys, "platform", "linux"), \
                patch.object(chat_tui_view.shutil, "which",
                             side_effect=lambda name: "/usr/bin/" + name), \
                patch.object(chat_tui_view.subprocess, "run",
                             side_effect=[
                                 Mock(returncode=1),
                                 subprocess.SubprocessError("xclip error"),
                                 Mock(returncode=1),
                             ]) as run:
            self.assertFalse(chat_tui_view.copy_to_clipboard(text))

        self.assertEqual(run.call_count, 3)
        for call, command in zip(run.call_args_list, self.LINUX_COMMANDS):
            self._assert_run_call(call, command, payload)

    def test_no_installed_tool_returns_false_without_running_subprocess(self):
        for platform in ("linux", "darwin"):
            with self.subTest(platform=platform), \
                    patch.object(chat_tui_view.sys, "platform", platform), \
                    patch.object(chat_tui_view.shutil, "which", return_value=None) as which, \
                    patch.object(chat_tui_view.subprocess, "run") as run:
                self.assertFalse(chat_tui_view.copy_to_clipboard("x"))
                self.assertGreaterEqual(which.call_count, 1)
                run.assert_not_called()

    def test_macos_preserves_single_pbcopy_behavior(self):
        text = "macOS clipboard\nline"
        payload = text.encode("utf-8")

        with patch.object(chat_tui_view.sys, "platform", "darwin"), \
                patch.object(chat_tui_view.shutil, "which",
                             side_effect=lambda name: "/usr/bin/pbcopy" if name == "pbcopy" else None), \
                patch.object(chat_tui_view.subprocess, "run",
                             return_value=Mock(returncode=0)) as run:
            self.assertTrue(chat_tui_view.copy_to_clipboard(text))
        run.assert_called_once()
        self._assert_run_call(run.call_args, ["pbcopy"], payload)

        for failure in (Mock(returncode=1), OSError("pbcopy failed"),
                        subprocess.TimeoutExpired(["pbcopy"], 3)):
            with self.subTest(failure=failure), \
                    patch.object(chat_tui_view.sys, "platform", "darwin"), \
                    patch.object(chat_tui_view.shutil, "which",
                                 side_effect=lambda name: "/usr/bin/pbcopy" if name == "pbcopy" else "/usr/bin/" + name), \
                    patch.object(chat_tui_view.subprocess, "run",
                                 side_effect=[failure]) as run:
                self.assertFalse(chat_tui_view.copy_to_clipboard(text))
            run.assert_called_once()
            self._assert_run_call(run.call_args, ["pbcopy"], payload)


if __name__ == "__main__":
    unittest.main()
