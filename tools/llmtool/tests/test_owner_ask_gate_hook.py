"""Tests for scripts/owner_ask_gate_hook.py."""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
HOOK = SCRIPTS / "owner_ask_gate_hook.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import owner_ask_gate_hook

MARKER_ASK = owner_ask_gate_hook.MARKER_ASK
ASK_LINE = "<!-- owner-ask: v=1 reserved_row=R-SCOPE -->"


def payload(command: str) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})


def run_hook(stdin_text: str):
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=stdin_text,
        text=True,
        capture_output=True,
        check=False,
    )


class OwnerAskGateHookTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write_body(self, name: str, body: str) -> Path:
        path = self.dir / name
        path.write_text(body, encoding="utf-8")
        return path

    def assert_refused(self, result, body_text: str | None = None) -> None:
        self.assertEqual(2, result.returncode)
        self.assertIn("owner_ask_gate_hook", result.stderr)
        if body_text is not None:
            self.assertNotIn(body_text, result.stderr)

    def test_ungated_ask_via_body_is_refused(self) -> None:
        body = MARKER_ASK + "\n\nThe Owner must decide the next step."
        command = "gh issue comment 42 --body " + shlex.quote(body)
        result = run_hook(payload(command))
        self.assert_refused(result, "The Owner must decide the next step.")

    def test_ungated_ask_via_body_file_is_refused(self) -> None:
        body = MARKER_ASK + "\n\nThe Owner must decide the next step."
        path = self.write_body("ungated.md", body)
        command = "gh pr comment 42 --body-file " + shlex.quote(str(path))
        result = run_hook(payload(command))
        self.assert_refused(result, "The Owner must decide the next step.")

    def test_gated_ask_passes(self) -> None:
        body = MARKER_ASK + "\n" + ASK_LINE + "\n\nThe Owner must decide."
        command = "gh issue comment 42 --body " + shlex.quote(body)
        result = run_hook(payload(command))
        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stderr)

    def test_gated_ask_via_body_file_passes(self) -> None:
        path = self.write_body("gated.md", MARKER_ASK + "\n" + ASK_LINE + "\n\nDecide.")
        command = "gh pr comment 42 -F " + shlex.quote(str(path))
        result = run_hook(payload(command))
        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stderr)

    def test_non_ask_comment_passes(self) -> None:
        command = "gh issue comment 42 --body " + shlex.quote("Routine progress update.")
        result = run_hook(payload(command))
        self.assertEqual(0, result.returncode)

    def test_non_gh_command_passes(self) -> None:
        result = run_hook(payload("echo hello"))
        self.assertEqual(0, result.returncode)

    def test_non_bash_tool_passes(self) -> None:
        stdin_text = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "x"}})
        result = run_hook(stdin_text)
        self.assertEqual(0, result.returncode)

    def test_malformed_stdin_passes(self) -> None:
        result = run_hook("{not hook json")
        self.assertEqual(0, result.returncode)

    def test_unreadable_relative_body_file_without_marker_passes(self) -> None:
        command = ("cd " + shlex.quote(str(self.dir)) + " && gh issue comment 42 "
                   "--body-file unwritten-body.md")
        result = run_hook(payload(command))
        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stderr)

    def test_unreadable_relative_body_file_with_marker_is_refused(self) -> None:
        body = MARKER_ASK + "\n\nThe Owner must decide the next step."
        command = ("cd " + shlex.quote(str(self.dir)) + " && printf '%s\\n' "
                   + shlex.quote(body) + " > unwritten-body.md"
                   + " && gh issue comment 42 --body-file unwritten-body.md")
        result = run_hook(payload(command))
        self.assert_refused(result, "The Owner must decide the next step.")

    def test_unreadable_tilde_body_file_without_marker_passes(self) -> None:
        command = "gh issue comment 42 --body-file ~/owner-gate-missing-body.md"
        result = run_hook(payload(command))
        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stderr)
    def test_unreadable_variable_body_file_with_marker_is_refused(self) -> None:
        body = MARKER_ASK + "\n\nThe Owner must decide the next step."
        command = ("body=" + shlex.quote(body)
                   + " && gh issue comment 42 --body-file \"$body\"")
        result = run_hook(payload(command))
        self.assert_refused(result, "The Owner must decide the next step.")

    def test_ungated_ask_via_body_equals_is_refused(self) -> None:
        body = MARKER_ASK + "\n\nThe Owner must decide the next step."
        command = "gh issue comment 42 --body=" + shlex.quote(body)
        result = run_hook(payload(command))
        self.assert_refused(result, "The Owner must decide the next step.")

    def test_ungated_ask_via_short_body_flag_is_refused(self) -> None:
        body = MARKER_ASK + "\n\nThe Owner must decide the next step."
        command = "gh issue comment 42 -b " + shlex.quote(body)
        result = run_hook(payload(command))
        self.assert_refused(result, "The Owner must decide the next step.")

    def test_compound_command_ungated_ask_first_is_refused(self) -> None:
        body = MARKER_ASK + "\n\nThe Owner must decide the next step."
        command = ("gh issue comment 42 --body " + shlex.quote(body)
                   + " ; gh pr comment 7 --body "
                   + shlex.quote("Routine progress update."))
        result = run_hook(payload(command))
        self.assert_refused(result, "The Owner must decide the next step.")

    def test_compound_command_ungated_ask_last_is_refused(self) -> None:
        body = MARKER_ASK + "\n\nThe Owner must decide the next step."
        command = ("gh issue comment 42 --body "
                   + shlex.quote("Routine progress update.")
                   + " && gh pr comment 7 --body " + shlex.quote(body))
        result = run_hook(payload(command))
        self.assert_refused(result, "The Owner must decide the next step.")

    def test_compound_command_all_benign_passes(self) -> None:
        command = ("gh issue comment 42 --body "
                   + shlex.quote("Routine progress update.")
                   + " || gh pr comment 7 -b "
                   + shlex.quote("Another routine update."))
        result = run_hook(payload(command))
        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stderr)

    def test_compound_command_all_gated_passes(self) -> None:
        gated = MARKER_ASK + "\n" + ASK_LINE + "\n\nDecide."
        command = ("gh issue comment 42 --body " + shlex.quote(gated)
                   + " | gh pr comment 7 --body "
                   + shlex.quote("Routine progress update."))
        result = run_hook(payload(command))
        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stderr)

    def test_body_file_stdin_without_marker_passes(self) -> None:
        command = ("gh issue comment 42 --body-file - <<'EOF'\n"
                   "Routine progress update.\n"
                   "EOF")
        result = run_hook(payload(command))
        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stderr)

    def test_body_file_stdin_with_marker_is_refused(self) -> None:
        command = ("gh issue comment 42 --body-file - <<'EOF'\n"
                   + MARKER_ASK + "\n\nThe Owner must decide the next step.\n"
                   "EOF")
        result = run_hook(payload(command))
        self.assert_refused(result, "The Owner must decide the next step.")

    def test_body_file_stdin_with_gated_ask_passes(self) -> None:
        command = ("gh pr comment 42 -F - <<'EOF'\n"
                   + MARKER_ASK + "\n" + ASK_LINE + "\n\nDecide.\n"
                   "EOF")
        result = run_hook(payload(command))
        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stderr)


if __name__ == "__main__":
    unittest.main()
