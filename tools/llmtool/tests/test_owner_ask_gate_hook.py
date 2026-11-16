"""Subprocess coverage for the #1943 owner-ask PreToolUse gate hook.

The gate fails closed: malformed hook input is refused, command text that
expands a variable or command substitution is refused because the posted body
cannot be known, and every body file must be a small regular non-symlink file
that is read once. Marker cases keep the ungated waiting_owner WAITING marker in
plain command text or in a regular body file. Tests use temporary files and
fixed hook JSON; the hook runs with an empty PATH to show it never calls gh.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
LLMTOOL_DIR = ROOT / "tools" / "llmtool"
SCRIPT = LLMTOOL_DIR / "scripts" / "owner_ask_gate_hook.py"

MARKER_ASK = "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->"
OWNER_ASK = "<!-- owner-ask: v=1 reserved_row=R-SCOPE -->"
GATED_BODY = "\n".join((MARKER_ASK, OWNER_ASK, "Question?"))
UNGATED_BODY = "\n".join((MARKER_ASK, "Question?"))
SENTINEL = "SENTINEL-OWNER-BODY-TEXT"

_HOOK_SPEC = importlib.util.spec_from_file_location(
    "owner_ask_gate_hook_under_test", SCRIPT)
hook = importlib.util.module_from_spec(_HOOK_SPEC)
_HOOK_SPEC.loader.exec_module(hook)

if str(LLMTOOL_DIR) not in sys.path:
    sys.path.insert(0, str(LLMTOOL_DIR))
_MARKER_SPEC = importlib.util.spec_from_file_location(
    "pm_owner_marker_for_gate_test", LLMTOOL_DIR / "scripts" / "pm_owner_marker.py")
marker = importlib.util.module_from_spec(_MARKER_SPEC)
_MARKER_SPEC.loader.exec_module(marker)


def hook_payload(command, tool_name="Bash"):
    return json.dumps({"tool_name": tool_name, "tool_input": {"command": command}})


class OwnerAskGateHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tmp = Path(self.temp.name)
        self.empty_bin = self.tmp / "empty-bin"
        self.empty_bin.mkdir()

    def run_hook(self, stdin_text):
        environment = os.environ.copy()
        # An empty PATH proves the hook runs offline and never invokes gh.
        environment["PATH"] = str(self.empty_bin)
        return subprocess.run([sys.executable, "-B", str(SCRIPT)],
                              input=stdin_text, capture_output=True, text=True,
                              check=False, env=environment, cwd=self.tmp)

    def write_body(self, name, body):
        path = self.tmp / name
        path.write_text(body, encoding="utf-8")
        return path

    def assertRefused(self, stdin_text, *hidden_texts, message=None):
        expected = hook.REFUSAL if message is None else message
        result = self.run_hook(stdin_text)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, expected + "\n")
        self.assertIn("waiting_owner", result.stderr)
        self.assertIn("owner-ask", result.stderr)
        for text in hidden_texts:
            self.assertNotIn(text, result.stderr)

    def assertAllowed(self, stdin_text):
        result = self.run_hook(stdin_text)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_gate_marker_is_the_pm_owner_marker_string(self):
        self.assertEqual(marker.MARKER_ASK, MARKER_ASK)
        self.assertEqual(hook.MARKER_ASK, marker.MARKER_ASK)

    def test_single_gated_ask_passes(self):
        self.assertAllowed(hook_payload(f'gh issue comment 17 --body "{GATED_BODY}"'))
        self.assertAllowed(hook_payload(f'gh pr comment 17 --body "{GATED_BODY}"'))

    def test_single_ungated_ask_is_refused(self):
        self.assertRefused(hook_payload(f'gh issue comment 17 --body "{UNGATED_BODY}"'))

    def test_non_bash_tool_passes(self):
        self.assertAllowed(hook_payload(f'gh issue comment 17 --body "{UNGATED_BODY}"',
                                        tool_name="Edit"))
        self.assertAllowed(json.dumps({"tool_name": "Read"}))
        self.assertAllowed(json.dumps({"tool_name": "Write", "tool_input": None}))

    def test_command_without_a_gh_comment_call_passes(self):
        for command in ('echo hello',
                        f'gh issue view 17 --body "{UNGATED_BODY}"',
                        f'gh pr view 17 --body "{UNGATED_BODY}"',
                        'gh issue view 17 --body "$SOMETHING"'):
            with self.subTest(command=command):
                self.assertAllowed(hook_payload(command))

    def test_comment_call_without_any_marker_passes(self):
        self.assertAllowed(hook_payload('gh issue comment 17 --body "Looks fine."'))
        self.assertAllowed(hook_payload('gh pr comment 17 --body "Looks fine."'))

    def test_variable_body_is_refused(self):
        for command in ('gh issue comment 17 --body "$COMMENT_BODY"',
                        f'gh issue comment 17 --body "$COMMENT_BODY {UNGATED_BODY}"',
                        f'gh pr comment 18 --body "{GATED_BODY} $COMMENT_BODY"',
                        'gh pr comment 18 --body-file "$BODY_PATH"'):
            with self.subTest(command=command):
                self.assertRefused(hook_payload(command),
                                   message=hook.REFUSAL_UNKNOWN_TEXT)

    def test_command_substitution_body_is_refused(self):
        for command in ('gh issue comment 17 --body "$(cat body.md)"',
                        f'gh pr comment 17 --body "$(cat body.md) {GATED_BODY}"'):
            with self.subTest(command=command):
                self.assertRefused(hook_payload(command),
                                   message=hook.REFUSAL_UNKNOWN_TEXT)

    def test_backtick_body_is_refused(self):
        for command in ('gh issue comment 17 --body "`cat body.md`"',
                        f'gh pr comment 17 --body "`cat body.md` {GATED_BODY}"'):
            with self.subTest(command=command):
                self.assertRefused(hook_payload(command),
                                   message=hook.REFUSAL_UNKNOWN_TEXT)

    def test_malformed_stdin_is_refused(self):
        for stdin_text in ("", "not json at all", '{"tool_name": "Bash"'):
            with self.subTest(stdin_text=stdin_text):
                self.assertRefused(stdin_text, message=hook.REFUSAL_PAYLOAD)

    def test_non_object_json_is_refused(self):
        for stdin_text in ("[]", '"text"', "5", "null"):
            with self.subTest(stdin_text=stdin_text):
                self.assertRefused(stdin_text, message=hook.REFUSAL_PAYLOAD)

    def test_bash_payload_without_a_command_is_refused(self):
        for stdin_text in (json.dumps({"tool_name": "Bash"}),
                           json.dumps({"tool_name": "Bash", "tool_input": None}),
                           json.dumps({"tool_name": "Bash", "tool_input": []}),
                           json.dumps({"tool_name": "Bash", "tool_input": {}}),
                           json.dumps({"tool_name": "Bash",
                                       "tool_input": {"command": 5}}),
                           json.dumps({"tool_name": "Bash",
                                       "tool_input": {"command": None}}),
                           json.dumps({"tool_name": "Bash",
                                       "tool_input": {"command": ""}})):
            with self.subTest(stdin_text=stdin_text):
                self.assertRefused(stdin_text, message=hook.REFUSAL_PAYLOAD)

    def test_semicolon_join_without_space_refused_ungated_ask_first(self):
        command = (f'gh issue comment 17 --body "{UNGATED_BODY}"'
                   f';gh issue comment 18 --body "{GATED_BODY}"')
        self.assertRefused(hook_payload(command))

    def test_compound_separators_with_ungated_ask_are_refused(self):
        ungated = f'gh issue comment 17 --body "{UNGATED_BODY}"'
        gated = f'gh issue comment 18 --body "{GATED_BODY}"'
        for separator in (";", " ; ", "; ", "&&", " && ", "||", " | ", "|"):
            with self.subTest(separator=separator):
                self.assertRefused(hook_payload(ungated + separator + gated))

    def test_heredoc_body_file_dash_with_apostrophe_is_refused(self):
        command = "\n".join((
            "gh issue comment 17 --body-file - <<'EOF'",
            MARKER_ASK,
            "It's an ungated ask.",
            "EOF",
        ))
        self.assertRefused(hook_payload(command), "ungated ask")

    def test_heredoc_body_file_dash_with_unmatched_quote_is_refused(self):
        command = "\n".join((
            "gh issue comment 17 --body-file - <<'EOF'",
            MARKER_ASK,
            'An unmatched " quote in the heredoc.',
            "EOF",
        ))
        self.assertRefused(hook_payload(command), "unmatched")

    def test_heredoc_body_file_dash_is_refused_even_when_gated(self):
        command = "\n".join((
            "gh issue comment 17 --body-file - <<'EOF'",
            MARKER_ASK,
            OWNER_ASK,
            "Question?",
            "EOF",
        ))
        self.assertRefused(hook_payload(command), message=hook.REFUSAL_BODY_FILE)

    def test_echo_of_a_comment_call_with_the_marker_is_refused(self):
        command = f'echo gh issue comment 17 --body "{MARKER_ASK}"'
        self.assertRefused(hook_payload(command))

    def test_gated_and_ungated_comments_in_one_command_are_refused(self):
        command = (f'gh issue comment 17 --body "{GATED_BODY}" && '
                   f'gh pr comment 18 --body "{UNGATED_BODY}"')
        self.assertRefused(hook_payload(command))

    def test_missing_body_file_is_refused(self):
        missing = self.tmp / "missing-body.md"
        for command in (f"gh issue comment 17 --body-file {missing}",
                        f"gh issue comment 17 --body-file {missing} ;",
                        f"gh pr comment 17 -F {missing}"):
            with self.subTest(command=command):
                self.assertRefused(hook_payload(command),
                                   message=hook.REFUSAL_BODY_FILE)

    def test_stdin_body_file_is_refused(self):
        for command in ("gh issue comment 17 --body-file -",
                        "gh pr comment 17 --body-file=-",
                        f'gh pr comment 17 --body "{GATED_BODY}" --body-file -'):
            with self.subTest(command=command):
                self.assertRefused(hook_payload(command),
                                   message=hook.REFUSAL_BODY_FILE)

    def test_directory_body_file_is_refused(self):
        directory = self.tmp / "body-dir"
        directory.mkdir()
        self.assertRefused(
            hook_payload(f"gh issue comment 17 --body-file {directory}"),
            message=hook.REFUSAL_BODY_FILE)

    def test_unreadable_body_file_path_is_refused(self):
        plain = self.write_body("plain.md", GATED_BODY)
        unreadable = plain / "child.md"
        self.assertRefused(
            hook_payload(f"gh issue comment 17 --body-file {unreadable}"),
            message=hook.REFUSAL_BODY_FILE)

    def test_symlinked_body_file_is_refused(self):
        target = self.write_body("gated.md", GATED_BODY)
        link = self.tmp / "linked.md"
        link.symlink_to(target)
        self.assertRefused(hook_payload(f"gh issue comment 17 --body-file {link}"),
                           message=hook.REFUSAL_BODY_FILE)

    def test_oversized_body_file_is_refused(self):
        oversized = self.tmp / "oversized.md"
        oversized.write_bytes(b"A" * (65536 + 1))
        self.assertRefused(
            hook_payload(f"gh issue comment 17 --body-file {oversized}"),
            message=hook.REFUSAL_BODY_FILE)
        self.assertRefused(hook_payload(f"gh pr comment 17 -F {oversized}"),
                           message=hook.REFUSAL_BODY_FILE)

    def test_body_file_at_the_size_limit_passes(self):
        body = self.write_body("limit.md", "A" * 65536)
        self.assertAllowed(hook_payload(f"gh issue comment 17 --body-file {body}"))

    def test_readable_body_file_ungated_is_refused(self):
        body = self.write_body("ungated.md", UNGATED_BODY + "\n" + SENTINEL)
        command = f'gh issue comment 17 --body-file "{body}"'
        self.assertRefused(hook_payload(command), SENTINEL)

    def test_readable_body_file_gated_passes(self):
        body = self.write_body("gated.md", GATED_BODY)
        self.assertAllowed(hook_payload(f"gh issue comment 17 --body-file {body}"))

    def test_body_file_text_is_not_shell_expanded(self):
        body = self.write_body("literal.md", "Cost is $5 and `code` here.")
        self.assertAllowed(hook_payload(f"gh issue comment 17 --body-file {body}"))

    def test_body_file_equals_and_short_flag(self):
        gated = self.write_body("gated.md", GATED_BODY)
        ungated = self.write_body("ungated.md", UNGATED_BODY)
        self.assertAllowed(hook_payload(f"gh pr comment 17 --body-file={gated}"))
        self.assertRefused(hook_payload(f"gh pr comment 17 -F {ungated}"))

    def test_gated_text_with_ungated_body_file_is_refused(self):
        ungated = self.write_body("ungated.md", UNGATED_BODY)
        command = (f'gh issue comment 17 --body "{GATED_BODY}" && '
                   f'gh issue comment 18 --body-file {ungated}')
        self.assertRefused(hook_payload(command))

    def test_inline_body_is_never_echoed_on_refusal(self):
        command = f'gh issue comment 17 --body "{UNGATED_BODY}\n{SENTINEL}"'
        self.assertRefused(hook_payload(command), SENTINEL)


if __name__ == "__main__":
    unittest.main()
