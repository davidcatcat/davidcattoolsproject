"""Offline CLI coverage using a fake gh executable."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools" / "llmtool" / "scripts" / "pm_owner_marker.py"
LLMTOOL_DIR = ROOT / "tools" / "llmtool"

try:
    from llmtool import github_identity
except ImportError:
    if str(LLMTOOL_DIR) not in sys.path:
        sys.path.insert(0, str(LLMTOOL_DIR))
    from llmtool import github_identity

_SPEC = importlib.util.spec_from_file_location("pm_owner_marker_under_test", SCRIPT)
marker = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(marker)

MARKER_ASK_LINE = "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->"
HEAD = "a" * 40
VALID_RECORD = {
    "--reserved-row": "R-SCOPE",
    "--blocked-action": "Add the release notes file to the Task write paths.",
    "--authority-check": "worker-dispatch.json gives the PM no scope change for this Task.",
    "--evidence": "The Task lists two write paths and the notes file is not one of them.",
    "--not-defect-not-harness": "Nothing is broken; this is a scope choice for the Owner.",
}
RECORD_FIELDS = (
    ("--reserved-row", "reserved_row"),
    ("--blocked-action", "blocked_action"),
    ("--authority-check", "authority_check"),
    ("--evidence", "evidence"),
    ("--not-defect-not-harness", "not_defect_not_harness"),
)


def record_args(**overrides):
    """CLI options for a valid ask record; overrides use option names without dashes."""
    values = dict(VALID_RECORD)
    for key, value in overrides.items():
        option = "--" + key.replace("_", "-")
        if value is None:
            values.pop(option, None)
        else:
            values[option] = value
    argv = []
    for option, value in values.items():
        argv.extend([option, value])
    return argv


class PMOwnerMarkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tmp = Path(self.temp.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        (self.home / ".config" / "gh-ai").mkdir(parents=True)
        (self.tmp / ".config" / "gh-ai").mkdir(parents=True, exist_ok=True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/a/b.git"],
                       cwd=self.repo, check=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.calls = self.tmp / "calls.jsonl"
        self.gh = self.bin / "gh"
        fake_gh_code = self.tmp / "fake_gh.py"
        fake_gh_code.write_text(f"""import json, os, pathlib, sys
args = sys.argv[1:]
if args == ['api', 'user', '--jq', '.login']:
    sys.path.insert(0, {str(LLMTOOL_DIR)!r})
    from llmtool import github_identity
    print(github_identity.ai_login())
    sys.exit(0)
with open(os.environ['FAKE_GH_CALLS'], 'a', encoding='utf-8') as f:
    row = {{'args': args}}
    if '--body-file' in args:
        path = args[args.index('--body-file') + 1]
        row['body'] = pathlib.Path(path).read_text(encoding='utf-8')
        row['private'] = (pathlib.Path(path).stat().st_mode & 0o077) == 0
    f.write(json.dumps(row) + '\\n')
if '--slurp' in args and ('--jq' in args or '--template' in args):
    print('incompatible gh api flags', file=sys.stderr)
    sys.exit(2)
if os.environ.get('FAKE_GH_FAIL') == '1':
    sys.exit(7)
if args[:2] == ['api', '--paginate']:
    endpoint = args[-1]
    if endpoint.endswith('issues/comments?per_page=100'):
        rows = [
          {{'id': 1, 'issue_url': 'https://api.github.com/repos/a/b/issues/17',
           'created_at': '2026-11-01T00:00:00Z', 'author_association': 'MEMBER',
           'body': '<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->\\nQuestion'}},
          {{'id': 2, 'issue_url': 'https://api.github.com/repos/a/b/issues/18',
           'created_at': '2026-11-01T00:00:00Z', 'author_association': 'MEMBER',
           'body': '<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->\\nQuestion'}},
          {{'id': 3, 'issue_url': 'https://api.github.com/repos/a/b/issues/18',
           'created_at': '2026-11-02T00:00:00Z', 'author_association': 'MEMBER',
           'body': '<!-- activity: type=pm_progress actor=PM target=Owner status=ANSWERED -->\\nRelayed'}},
          {{'id': 4, 'issue_url': 'https://api.github.com/repos/a/b/issues/19',
           'created_at': '2026-11-02T00:00:00Z', 'author_association': 'MEMBER',
           'body': '<!-- activity: type=waiting_owner actor=PM target=Owner status=BLOCKED -->\\nBlocked'}},
          {{'id': 5, 'issue_url': 'https://api.github.com/repos/a/b/issues/20',
           'created_at': '2026-11-02T00:00:00Z', 'author_association': 'MEMBER',
           'body': '<!-- activity: type=repair_gate actor=PM target=Owner status=OPEN -->\\nNeeds review'}},
        ]
    else:
        rows = []
    print(json.dumps([rows]))
elif len(args) >= 2 and args[:2] == ['issue', 'comment']:
    print('https://github.com/a/b/issues/17#issuecomment-123')
""", encoding="utf-8")
        self.gh.write_text(
            "#!/bin/sh\nexec python3 -B \"$FAKE_GH_SCRIPT\" \"$@\"\n", encoding="utf-8")
        self.gh.chmod(0o700)

    def run_cli(self, *args, fail=False, repo=False, cwd=None):
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["PATH"] = str(self.bin) + os.pathsep + env["PATH"]
        env["FAKE_GH_CALLS"] = str(self.calls)
        env["FAKE_GH_SCRIPT"] = str(self.tmp / "fake_gh.py")
        if fail:
            env["FAKE_GH_FAIL"] = "1"
        command = [sys.executable, "-B", str(SCRIPT)]
        if repo:
            command.extend(["--repo", "a/b"])
        command.extend(args)
        return subprocess.run(command, cwd=self.repo if cwd is None else cwd,
                              env=env, capture_output=True, text=True, check=False)

    def recorded_calls(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def test_ask_posts_leading_marker_and_url(self):
        result = self.run_cli("ask", "17", "--question", "Should we proceed?", *record_args())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(),
                         "https://github.com/a/b/issues/17#issuecomment-123")
        row = self.recorded_calls()[0]
        self.assertTrue(row["body"].startswith(
            "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->"))
        self.assertTrue(row["private"])
        self.assertIn("--body-file", row["args"])

    def test_valid_ask_posts_marker_owner_ask_line_and_record(self):
        result = self.run_cli("ask", "17", "--question", "Should we proceed?", *record_args())
        self.assertEqual(result.returncode, 0, result.stderr)
        body = self.recorded_calls()[0]["body"]
        lines = body.splitlines()
        self.assertEqual(lines[0], MARKER_ASK_LINE)
        self.assertEqual(lines[1], "<!-- owner-ask: v=1 reserved_row=R-SCOPE -->")
        self.assertEqual(lines[2:4], ["", "Should we proceed?"])
        record_at = lines.index("Ask record:")
        self.assertGreater(record_at, 3)
        self.assertEqual(lines[record_at + 1:record_at + 6], [
            f"- {name}: {VALID_RECORD[option]}" for option, name in RECORD_FIELDS])
        self.assertNotIn("other_reason", body)

    def test_r_other_ask_lists_other_reason_and_keeps_sheet(self):
        sheet = self.tmp / "sheet.md"
        sheet.write_text("Decision sheet\n", encoding="utf-8")
        result = self.run_cli("ask", "17", "--question", "Which way?", "--sheet", str(sheet),
                              *record_args(reserved_row="R-OTHER",
                                           other_reason="No listed row covers this choice."))
        self.assertEqual(result.returncode, 0, result.stderr)
        body = self.recorded_calls()[0]["body"]
        lines = body.splitlines()
        self.assertEqual(lines[1], "<!-- owner-ask: v=1 reserved_row=R-OTHER -->")
        self.assertIn("- reserved_row: R-OTHER", lines)
        self.assertIn("- other_reason: No listed row covers this choice.", lines)
        self.assertLess(lines.index("- other_reason: No listed row covers this choice."),
                        lines.index("Decision sheet"))

    def test_owner_ask_line_is_not_an_activity_marker(self):
        line = marker.OWNER_ASK_LINE.format(row="R-SCOPE")
        self.assertEqual(line, "<!-- owner-ask: v=1 reserved_row=R-SCOPE -->")
        self.assertIsNone(marker.MARKER.fullmatch(line))

    def test_posted_structured_ask_is_open_row_and_answer_settles_it(self):
        ask = self.run_cli("ask", "17", "--question", "Should we proceed?", *record_args())
        self.assertEqual(ask.returncode, 0, ask.stderr)
        answer = self.run_cli("answer", "17", "--owner-words", "Proceed now.",
                              "--pm-reading", "The task may continue.")
        self.assertEqual(answer.returncode, 0, answer.stderr)
        ask_body, answer_body = (call["body"] for call in self.recorded_calls())
        issue_url = "https://api.github.com/repos/a/b/issues/17"
        asked = {"id": 10, "issue_url": issue_url, "created_at": "2026-11-03T00:00:00Z",
                 "author_association": "MEMBER", "body": ask_body}
        latest = marker._waiting_markers([asked])
        self.assertEqual(list(latest), ["17"])
        self.assertEqual(latest["17"][2:], ("waiting_owner", "WAITING"))
        self.assertEqual(marker.open_rows_from_latest(latest, []), ["17"])
        answered = {"id": 11, "issue_url": issue_url, "created_at": "2026-11-04T00:00:00Z",
                    "author_association": "MEMBER", "body": answer_body}
        settled = marker._waiting_markers([asked, answered])
        self.assertEqual(settled, {})
        self.assertEqual(marker.open_rows_from_latest(settled, []), [])

    def test_answer_quotes_owner_as_pm_relay(self):
        result = self.run_cli("answer", "17", "--owner-words", "Proceed now.",
                              "--pm-reading", "The task may continue.")
        self.assertEqual(result.returncode, 0, result.stderr)
        body = self.recorded_calls()[0]["body"]
        self.assertTrue(body.startswith(
            "<!-- activity: type=pm_progress actor=PM target=Owner status=ANSWERED -->"))
        self.assertIn("Owner words relayed by the PM:\n> Proceed now.", body)
        self.assertNotIn("actor=Owner", body)
        self.assertIn("PM reading:\nThe task may continue.", body)

    def test_gh_failure_returns_nonzero_without_url(self):
        result = self.run_cli("ask", "17", "--question", "Question?", *record_args(), fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(self.recorded_calls()), 1)

    def test_open_rows_lists_latest_unsettled_owner_markers(self):
        result = self.run_cli("open-rows")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "17 waiting_owner WAITING", "19 waiting_owner BLOCKED", "20 repair_gate OPEN"])
        self.assertTrue(all("comment" not in call["args"] for call in self.recorded_calls()))
        self.assertTrue(all(not ({"--jq", "--template"} <= set(call["args"]))
                            for call in self.recorded_calls()))

    def test_default_repo_detection_fails_closed_without_origin(self):
        no_origin = self.tmp / "no-origin"
        no_origin.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=no_origin, check=True)
        result = self.run_cli("open-rows", repo=False, cwd=no_origin)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot determine repository from origin remote", result.stderr)
        self.assertFalse(self.calls.exists())

    def test_ask_rejects_marker_injection_question(self):
        result = self.run_cli("ask", "17", "--question", "<!-- activity: injected -->",
                              *record_args())
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())

    def test_cli_refused_record_makes_no_gh_call(self):
        result = self.run_cli("ask", "17", "--question", "Should we proceed?",
                              *record_args(reserved_row="R-NOPE"))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr.startswith("pm_owner_marker: OWNER_ASK_ROW_UNKNOWN"),
                        result.stderr)
        self.assertFalse(self.calls.exists())


class AskRecordGateTests(unittest.TestCase):
    """In-process ask gate checks: GitHub and subprocess entry points are mocked."""

    def run_main(self, *record, question="Should we proceed?"):
        argv = ["--repo", "a/b", "ask", "17", "--question", question, *record]
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(marker, "_post",
                               return_value="https://github.com/a/b/issues/17#issuecomment-1"
                               ) as post, \
                mock.patch.object(marker, "verified_ai_env") as env, \
                mock.patch.object(marker, "_run") as run, \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = marker.main(argv)
        return status, stdout.getvalue(), stderr.getvalue(), post, env, run

    def assertRefused(self, code, *record, secret_free_value=None):
        status, stdout, stderr, post, env, run = self.run_main(*record)
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertTrue(stderr.startswith(f"pm_owner_marker: {code}"), stderr)
        if secret_free_value is not None:
            self.assertNotIn(secret_free_value, stderr)
        post.assert_not_called()
        env.assert_not_called()
        run.assert_not_called()

    def assertPosted(self, row, *record):
        status, stdout, stderr, post, env, run = self.run_main(*record)
        self.assertEqual(status, 0, stderr)
        self.assertEqual(stdout.strip(), "https://github.com/a/b/issues/17#issuecomment-1")
        post.assert_called_once()
        number, body, repo = post.call_args.args
        self.assertEqual((number, repo), ("17", "a/b"))
        lines = body.splitlines()
        self.assertEqual(lines[0], MARKER_ASK_LINE)
        self.assertEqual(lines[1], f"<!-- owner-ask: v=1 reserved_row={row} -->")
        run.assert_not_called()
        return body

    def test_reserved_rows_are_the_owner_ask_gate_table(self):
        self.assertEqual(marker.RESERVED_ROWS, (
            "R-SCOPE", "R-RELEASE", "R-SPEND", "R-EGRESS", "R-SECURITY", "R-CRED", "R-ROOT",
            "R-MACOS", "R-REVIEWER", "R-DISPATCH", "R-ACCEPT", "R-BUDGET", "R-STOP",
            "R-OTHER"))

    def test_valid_record_posts(self):
        self.assertPosted("R-SCOPE", *record_args())

    def test_rows_without_evidence_rule_need_only_non_empty_evidence(self):
        for row in ("R-SCOPE", "R-RELEASE", "R-SPEND", "R-EGRESS", "R-SECURITY", "R-CRED",
                    "R-ROOT", "R-MACOS", "R-BUDGET"):
            with self.subTest(row=row):
                self.assertPosted(row, *record_args(reserved_row=row))

    def test_unknown_row_is_refused(self):
        self.assertRefused("OWNER_ASK_ROW_UNKNOWN", *record_args(reserved_row="R-NOPE"),
                           secret_free_value="R-NOPE")

    def test_lower_case_row_is_refused(self):
        self.assertRefused("OWNER_ASK_ROW_UNKNOWN", *record_args(reserved_row="r-scope"),
                           secret_free_value="r-scope")

    def test_each_empty_required_field_is_refused(self):
        for option, name in RECORD_FIELDS:
            with self.subTest(field=name):
                self.assertRefused(f"OWNER_ASK_FIELD_MISSING {name}",
                                   *record_args(**{option[2:].replace("-", "_"): "   "}))

    def test_each_omitted_required_option_is_refused_by_the_parser(self):
        for option, name in RECORD_FIELDS:
            with self.subTest(field=name):
                with mock.patch.object(marker, "_post") as post, \
                        mock.patch.object(marker, "verified_ai_env") as env, \
                        mock.patch.object(marker, "_run") as run, \
                        contextlib.redirect_stderr(io.StringIO()), \
                        self.assertRaises(SystemExit) as raised:
                    marker.main(["--repo", "a/b", "ask", "17", "--question", "Q?",
                                 *record_args(**{option[2:].replace("-", "_"): None})])
                self.assertNotEqual(raised.exception.code, 0)
                post.assert_not_called()
                env.assert_not_called()
                run.assert_not_called()

    def test_comment_close_injection_in_each_field_is_refused(self):
        fields = RECORD_FIELDS + (("--other-reason", "other_reason"),)
        for option, name in fields:
            for injected in ("text --> more", "text <!-- more"):
                with self.subTest(field=name, injected=injected):
                    key = option[2:].replace("-", "_")
                    value = ("R-SCOPE " + injected) if key == "reserved_row" else injected
                    self.assertRefused(f"OWNER_ASK_FIELD_INVALID {name}",
                                       *record_args(**{key: value}),
                                       secret_free_value=injected)

    def test_newline_in_blocked_action_is_refused(self):
        self.assertRefused("OWNER_ASK_FIELD_INVALID blocked_action",
                           *record_args(blocked_action="Merge the PR\nand close the Issue"),
                           secret_free_value="close the Issue")

    def test_newline_in_reserved_row_is_refused(self):
        self.assertRefused("OWNER_ASK_FIELD_INVALID reserved_row",
                           *record_args(reserved_row="R-SCOPE\nR-SPEND"))

    def test_over_long_field_is_refused(self):
        self.assertRefused("OWNER_ASK_FIELD_INVALID evidence",
                           *record_args(evidence="x" * 2001))

    def test_field_at_length_limit_is_accepted(self):
        self.assertPosted("R-SCOPE", *record_args(evidence="x" * 2000))

    def test_r_other_without_reason_is_refused(self):
        self.assertRefused("OWNER_ASK_OTHER_REASON_REQUIRED",
                           *record_args(reserved_row="R-OTHER"))
        self.assertRefused("OWNER_ASK_OTHER_REASON_REQUIRED",
                           *record_args(reserved_row="R-OTHER", other_reason="   "))

    def test_r_other_with_reason_posts(self):
        body = self.assertPosted("R-OTHER", *record_args(
            reserved_row="R-OTHER", other_reason="No listed row covers this choice."))
        self.assertIn("- other_reason: No listed row covers this choice.", body.splitlines())

    def test_r_dispatch_evidence(self):
        self.assertRefused("OWNER_ASK_EVIDENCE_MISMATCH", *record_args(
            reserved_row="R-DISPATCH", evidence="Dispatch looks paused to me."))
        self.assertRefused("OWNER_ASK_EVIDENCE_MISMATCH", *record_args(
            reserved_row="R-DISPATCH", evidence="Refusal NOT_OWNER_PAUSED_X seen."))
        self.assertPosted("R-DISPATCH", *record_args(
            reserved_row="R-DISPATCH", evidence="llmtool task dispatch refused OWNER_PAUSED."))
        self.assertPosted("R-DISPATCH", *record_args(
            reserved_row="R-DISPATCH", evidence="Refusal code OWNER_ASSIGNMENT_REQUIRED."))

    def test_r_accept_evidence(self):
        full = f"MERGE_OWNER_ACCEPTANCE_MANUAL for PR #1950 at HEAD {HEAD}"
        for evidence in (
                f"PR #1950 at HEAD {HEAD} needs acceptance",
                f"MERGE_OWNER_ACCEPTANCE_MANUAL at HEAD {HEAD}",
                "MERGE_OWNER_ACCEPTANCE_MANUAL for PR #1950",
                f"MERGE_OWNER_ACCEPTANCE_MANUAL for PR #1950 at HEAD {HEAD[:39]}",
                f"MERGE_OWNER_ACCEPTANCE_MANUAL for PR 1950 at HEAD {HEAD}"):
            with self.subTest(evidence=evidence):
                self.assertRefused("OWNER_ASK_EVIDENCE_MISMATCH", *record_args(
                    reserved_row="R-ACCEPT", evidence=evidence))
        self.assertPosted("R-ACCEPT", *record_args(reserved_row="R-ACCEPT", evidence=full))

    def test_r_stop_evidence(self):
        for evidence in (
                "STOP recorded on the Issue.",
                "STOP at https://github.com/a/b/issues/17",
                "STOP at https://github.com/a/b/pull/17#issuecomment-123"):
            with self.subTest(evidence=evidence):
                self.assertRefused("OWNER_ASK_EVIDENCE_MISMATCH", *record_args(
                    reserved_row="R-STOP", evidence=evidence))
        self.assertPosted("R-STOP", *record_args(
            reserved_row="R-STOP",
            evidence="STOP at https://github.com/a/b/issues/17#issuecomment-123"))

    def test_r_reviewer_evidence(self):
        for evidence in ("The reviewer is out of quota.", "other-reviewer quota refused"):
            with self.subTest(evidence=evidence):
                self.assertRefused("OWNER_ASK_EVIDENCE_MISMATCH", *record_args(
                    reserved_row="R-REVIEWER", evidence=evidence))
        self.assertPosted("R-REVIEWER", *record_args(
            reserved_row="R-REVIEWER", evidence="codex-reviewer returned a quota refusal."))
        self.assertPosted("R-REVIEWER", *record_args(
            reserved_row="R-REVIEWER", evidence="claude-reviewer is unavailable."))


if __name__ == "__main__":
    unittest.main()
