"""Tests for the Owner-ask Stop hook (observe default, explicit enforce)."""

import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from llmtool import owner_ask_stop_hook as hook


def _epoch(text):
    return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc).timestamp())


NOW = _epoch("2026-11-17T12:00:00Z")
CREATED = "2026-11-17T09:00:00Z"
FIRST_ASSISTANT = "2026-11-17T10:00:00.000Z"
ASK_BODY = (hook.MARKER_ASK + "\n"
            "<!-- owner-ask: v=1 reserved_row=R-SCOPE -->\n"
            "Question for the Owner")


def _url(issue, comment_id):
    return (f"https://github.com/acme/widget/issues/{issue}"
            f"#issuecomment-{comment_id}")


def _comment(comment_id, issue=10, created=CREATED, body=ASK_BODY,
             association="MEMBER"):
    return {
        "id": comment_id,
        "created_at": created,
        "issue_url": f"https://api.github.com/repos/acme/widget/issues/{issue}",
        "body": body,
        "author_association": association,
    }


def _payload(message, transcript, **extra):
    values = {
        "session_id": "synthetic-session",
        "transcript_path": str(transcript),
        "cwd": "/synthetic/repo",
        "stop_hook_active": False,
        "last_assistant_message": message,
    }
    values.update(extra)
    return values


def _fetch(comments):
    def fetch(_url):
        return comments
    return fetch


class StopHookTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.log = self.root / "state" / "owner-ask-gate.jsonl"
        self.transcript = self.root / "transcript.jsonl"
        self._write_transcript(self.transcript)

    def _write_transcript(self, path, first=FIRST_ASSISTANT):
        entries = [{"type": "user", "timestamp": "2026-11-17T09:30:00.000Z",
                    "message": {}}]
        if first is not None:
            entries.append({"type": "assistant", "timestamp": first, "message": {}})
        path.write_text(
            "\n".join(json.dumps(entry) for entry in entries) + "\n",
            encoding="utf-8")

    def _run(self, message=None, comments=None, transcript=None, **payload_changes):
        message = (hook.CHAT_ASK + " please decide " + _url(10, 101)
                   if message is None else message)
        payload = _payload(message, transcript or self.transcript, **payload_changes)
        fetch = _fetch(comments if comments is not None else [_comment(101)])
        return hook.handle(payload, fetch=fetch, now=NOW, log_path=self.log)

    def _outcomes(self):
        if not self.log.exists():
            return []
        return [json.loads(line)["outcome"] for line in
                self.log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _last_outcome(self):
        return self._outcomes()[-1]

    # -- screen ---------------------------------------------------------

    def test_detect_screens_chat_ask_row_term_and_owner_option_list(self):
        self.assertTrue(hook.detect(hook.CHAT_ASK + " choose"))
        self.assertTrue(hook.detect("The blocked action is R-SCOPE"))
        self.assertTrue(hook.detect("Owner, choose one:\nA. ship\nB. wait"))

    def test_detect_ignores_ci_fail_progress_and_info(self):
        for message in (
                "CI FAIL on PR #12: the PM will repair it.",
                "Progress: all tests pass; continuing.",
                "Info: the Owner was notified earlier.",
        ):
            with self.subTest(message=message):
                self.assertFalse(hook.detect(message))

    # -- pass and non-ask ----------------------------------------------

    def test_valid_marker_passes_without_a_log_line(self):
        self.assertEqual(self._run(), {})
        self.assertFalse(self.log.exists())

    def test_ci_fail_report_is_not_flagged(self):
        self.assertEqual(self._run("CI FAIL on PR #12: repairing."), {})
        self.assertEqual(self._last_outcome(), "NO_ASK")

    def test_progress_and_info_questions_are_not_flagged(self):
        for message in ("Progress: all tests pass.",
                        "Info: the Owner was notified earlier."):
            with self.subTest(message=message):
                self.assertEqual(self._run(message), {})
        self.assertNotIn("UNGATED", self._outcomes())

    def test_caps_exhausted_ask_without_marker_is_not_a_pass(self):
        message = hook.CHAT_ASK + " caps exhausted; the PM needs a decision"
        self.assertEqual(self._run(message), {})
        self.assertEqual(self._last_outcome(), "NO_COMMENT_URL")

    # -- each failing marker rule --------------------------------------

    def test_missing_url(self):
        self.assertEqual(self._run(hook.CHAT_ASK + " no comment URL"), {})
        self.assertEqual(self._last_outcome(), "NO_COMMENT_URL")

    def test_multiple_urls(self):
        message = (hook.CHAT_ASK + " " + _url(10, 101) + " and " + _url(10, 102))
        self.assertEqual(self._run(message), {})
        self.assertEqual(self._last_outcome(), "MULTIPLE_COMMENT_URLS")

    def test_wrong_type_or_status(self):
        body = ("<!-- activity: type=pm_progress actor=PM target=Owner "
                "status=BLOCKED -->\n"
                "<!-- owner-ask: v=1 reserved_row=R-SCOPE -->")
        self.assertEqual(self._run(comments=[_comment(101, body=body)]), {})
        self.assertEqual(self._last_outcome(), "WRONG_MARKER")

    def test_missing_line_two(self):
        self.assertEqual(self._run(comments=[_comment(101, body=hook.MARKER_ASK)]), {})
        self.assertEqual(self._last_outcome(), "MISSING_GATED_LINE")

    def test_malformed_line_two(self):
        body = hook.MARKER_ASK + "\nnot an owner-ask line"
        self.assertEqual(self._run(comments=[_comment(101, body=body)]), {})
        self.assertEqual(self._last_outcome(), "MISSING_GATED_LINE")

    def test_row_outside_reserved_rows(self):
        body = (hook.MARKER_ASK + "\n"
                "<!-- owner-ask: v=1 reserved_row=R-NOPE -->")
        self.assertEqual(self._run(comments=[_comment(101, body=body)]), {})
        self.assertEqual(self._last_outcome(), "ROW_UNKNOWN")

    def test_comment_on_another_issue(self):
        message = hook.CHAT_ASK + " " + _url(11, 101)
        self.assertEqual(self._run(message, comments=[_comment(101, issue=10)]), {})
        self.assertEqual(self._last_outcome(), "ISSUE_MISMATCH")

    def test_older_than_the_wait_horizon(self):
        comments = [_comment(101, created="2026-11-01T09:00:00Z")]
        self.assertEqual(self._run(comments=comments), {})
        self.assertEqual(self._last_outcome(), "TOO_OLD")

    def test_answered_by_a_later_marker_answer(self):
        later = _comment(102, created="2026-11-17T09:30:00Z",
                         body=hook.MARKER_ANSWER + "\nOwner words")
        self.assertEqual(self._run(comments=[_comment(101), later]), {})
        self.assertEqual(self._last_outcome(), "MARKER_ANSWER")

    def test_settled_by_a_later_answered_comment(self):
        later = _comment(
            102, created="2026-11-17T09:30:00Z",
            body=("<!-- activity: type=pm_progress actor=PM target=Owner "
                  "status=RESOLVED -->\nSettled"))
        self.assertEqual(self._run(comments=[_comment(101), later]), {})
        self.assertEqual(self._last_outcome(), "OWNER_SETTLED")

    def test_a_later_info_notice_does_not_settle_the_ask(self):
        later = _comment(
            102, created="2026-11-17T09:30:00Z",
            body=("<!-- activity: type=pm_progress actor=PM target=Owner "
                  "status=NOTICE -->\nInfo"))
        self.assertEqual(self._run(comments=[_comment(101), later]), {})
        self.assertFalse(self.log.exists())

    def test_created_after_the_turn_started(self):
        comments = [_comment(101, created="2026-11-17T10:30:00Z")]
        self.assertEqual(self._run(comments=comments), {})
        self.assertEqual(self._last_outcome(), "ORDER_VIOLATION")

    def test_untrusted_author(self):
        comments = [_comment(101, association="NONE")]
        self.assertEqual(self._run(comments=comments), {})
        self.assertEqual(self._last_outcome(), "UNTRUSTED_AUTHOR")

    # -- guards and errors ---------------------------------------------

    def test_missing_assistant_time_is_order_unknown(self):
        self._write_transcript(self.transcript, first=None)
        self.assertEqual(self._run(), {})
        self.assertEqual(self._last_outcome(), "ORDER_UNKNOWN")

    def test_missing_transcript_is_order_unknown(self):
        missing = self.root / "absent.jsonl"
        self.assertEqual(self._run(transcript=missing), {})
        self.assertEqual(self._last_outcome(), "ORDER_UNKNOWN")

    def test_tool_result_does_not_reset_the_turn_start(self):
        entries = [
            {"type": "user", "timestamp": "2026-11-17T09:30:00.000Z", "message": {}},
            {"type": "assistant", "timestamp": "2026-11-17T10:00:00.000Z",
             "message": {}},
            {"type": "user", "timestamp": "2026-11-17T10:05:00.000Z",
             "message": {"content": [{"type": "tool_result"}]}},
            {"type": "assistant", "timestamp": "2026-11-17T10:10:00.000Z",
             "message": {}},
        ]
        self.transcript.write_text(
            "\n".join(json.dumps(entry) for entry in entries) + "\n",
            encoding="utf-8")
        comments = [_comment(101, created="2026-11-17T10:02:00Z")]
        self.assertEqual(self._run(comments=comments), {})
        self.assertEqual(self._last_outcome(), "ORDER_VIOLATION")

    def test_missing_last_message_is_unknown(self):
        self.assertEqual(hook.handle({"session_id": "s"}, now=NOW,
                                    log_path=self.log), {})
        self.assertEqual(self._last_outcome(), "UNKNOWN")

    def test_stop_hook_active_never_blocks_and_logs_alerted(self):
        message = hook.CHAT_ASK + " no marker URL"
        with mock.patch.dict(os.environ, {hook.MODE_ENV: "enforce"}, clear=False):
            self.assertEqual(self._run(message, stop_hook_active=True), {})
        self.assertEqual(self._last_outcome(), "UNGATED_ALERTED")

    def test_option_list_ask_without_marker_is_blocked_once_then_alerted(self):
        message = "Owner, choose one:\nA. ship it\nB. wait"
        self.assertTrue(hook.detect(message))
        with mock.patch.dict(os.environ, {hook.MODE_ENV: "enforce"}, clear=False):
            first = self._run(message)
            second = self._run(message)
        self.assertEqual(first.get("decision"), "block")
        self.assertIn("pm_owner_marker.py ask", first.get("reason", ""))
        self.assertEqual(second, {})
        self.assertEqual(self._outcomes(),
                         ["UNGATED_BLOCKED", "UNGATED_ALERTED"])

    def test_fetch_failure_logs_hook_error_without_blocking(self):
        def fetch(_url):
            raise hook.HookError("FETCH_ERROR")
        payload = _payload(hook.CHAT_ASK + " " + _url(10, 101), self.transcript)
        self.assertEqual(hook.handle(payload, fetch=fetch, now=NOW, log_path=self.log), {})
        self.assertEqual(self._last_outcome(), "HOOK_ERROR")

    def test_enforce_does_not_block_on_a_fetch_error(self):
        def fetch(_url):
            raise hook.HookError("FETCH_ERROR")
        payload = _payload(hook.CHAT_ASK + " " + _url(10, 101), self.transcript)
        with mock.patch.dict(os.environ, {hook.MODE_ENV: "enforce"}, clear=False):
            self.assertEqual(
                hook.handle(payload, fetch=fetch, now=NOW, log_path=self.log), {})
        self.assertEqual(self._last_outcome(), "HOOK_ERROR")

    def test_enforce_does_not_block_when_order_is_unknown(self):
        self._write_transcript(self.transcript, first=None)
        with mock.patch.dict(os.environ, {hook.MODE_ENV: "enforce"}, clear=False):
            self.assertEqual(self._run(), {})
        self.assertEqual(self._last_outcome(), "ORDER_UNKNOWN")

    def test_log_records_only_metadata(self):
        self._run(hook.CHAT_ASK + " no URL")
        record = json.loads(self.log.read_text(encoding="utf-8").strip())
        self.assertEqual(set(record), {"time", "session", "issue", "outcome",
                                       "reply_sha256"})
        self.assertEqual(record["issue"], "NONE")
        self.assertNotIn("no URL", self.log.read_text(encoding="utf-8"))

    # -- main -----------------------------------------------------------

    def test_main_reads_fake_stdin_and_passes_silently(self):
        payload = _payload(hook.CHAT_ASK + " " + _url(10, 101), self.transcript)
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {
                hook.MODE_ENV: "observe", hook.LOG_ENV: str(self.log)}, clear=False), \
                mock.patch.object(hook, "_default_fetch", return_value=[_comment(101)]), \
                mock.patch("sys.stdin", io.StringIO(json.dumps(payload))), \
                mock.patch("sys.stdout", stdout):
            self.assertEqual(hook.main(), 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertFalse(self.log.exists())

    def test_main_malformed_stdin_logs_hook_error_and_exits_zero(self):
        with mock.patch.dict(os.environ, {
                hook.MODE_ENV: "observe", hook.LOG_ENV: str(self.log)}, clear=False), \
                mock.patch("sys.stdin", io.StringIO("not json")), \
                mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(hook.main(), 0)
        self.assertEqual(self._last_outcome(), "HOOK_ERROR")

    def test_main_enforce_emits_one_block_response(self):
        payload = _payload(hook.CHAT_ASK + " no marker", self.transcript)
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {
                hook.MODE_ENV: "enforce", hook.LOG_ENV: str(self.log)}, clear=False), \
                mock.patch("sys.stdin", io.StringIO(json.dumps(payload))), \
                mock.patch("sys.stdout", stdout):
            self.assertEqual(hook.main(), 0)
        response = json.loads(stdout.getvalue())
        self.assertEqual(response["decision"], "block")
        self.assertEqual(self._outcomes(), ["UNGATED_BLOCKED"])


if __name__ == "__main__":
    unittest.main()
