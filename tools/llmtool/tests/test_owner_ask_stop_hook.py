"""Offline tests for owner_ask_stop_hook (#2285 Phase B / C)."""
from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest

LLMTOOL_DIR = Path(__file__).resolve().parent.parent
if str(LLMTOOL_DIR) not in sys.path:
    sys.path.insert(0, str(LLMTOOL_DIR))

from llmtool import owner_ask_stop_hook as hook  # noqa: E402


VALID_ASK_BODY = (
    "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->\n"
    "<!-- owner-ask: v=1 reserved_row=R-SCOPE -->\n\n"
    "Should we expand the task scope?"
)
ANSWER_BODY = (
    "<!-- activity: type=pm_progress actor=PM target=Owner status=ANSWERED -->\n\n"
    "Owner words relayed by the PM:\n> Approved."
)
MULTI_LINE_ANSWER_BODY = (
    "<!-- activity: type=pm_progress actor=PM target=PM status=RECORDED -->\n"
    "<!-- activity: type=pm_progress actor=PM target=Owner status=ANSWERED -->\n\n"
    "Relayed answer after PM progress marker."
)
ISSUE_URL = "https://api.github.com/repos/a/b/issues/17"
COMMENT_URL = "https://github.com/a/b/issues/17#issuecomment-123"
NOW = 1_795_000_000  # 2026-11-18T...


def _iso(ts: int) -> str:
    return hook._utc_iso(ts)


def _make_comment(
    cid: int = 123,
    created_at: int = NOW - 600,
    issue_url: str = ISSUE_URL,
    body: str = VALID_ASK_BODY,
    author_association: str = "MEMBER",
) -> dict:
    return {
        "id": cid,
        "created_at": _iso(created_at),
        "issue_url": issue_url,
        "body": body,
        "author_association": author_association,
    }


def _write_transcript(path: Path, records: list[dict]) -> Path:
    lines = [json.dumps(r) for r in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class OwnerAskStopHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tmp = Path(self.temp.name)
        self.log_path = self.tmp / "owner-ask-gate.jsonl"
        self.transcript = _write_transcript(
            self.tmp / "transcript.jsonl",
            [
                {"type": "user", "timestamp": _iso(NOW - 300), "message": {"content": "status?"}},
                {"type": "assistant", "timestamp": _iso(NOW - 100), "message": {"content": "ok"}},
            ],
        )

    def _read_logs(self) -> list[dict]:
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines() if line]

    def _payload(self, reply: str, *, stop_hook_active: bool = False, transcript: Path | None = None) -> dict:
        return {
            "session_id": "sess-1",
            "transcript_path": str(self.transcript if transcript is None else transcript),
            "cwd": str(self.tmp),
            "stop_hook_active": stop_hook_active,
            "last_assistant_message": reply,
        }

    # C1 / A1
    def test_c1_valid_waiting_marker_passes_without_log(self):
        comments = [_make_comment(created_at=NOW - 600)]
        reply = f"你需要做：請決定 #17 範圍。Marker: {COMMENT_URL}"
        result = hook.evaluate(
            self._payload(reply),
            fetcher=lambda _r, _i, _c: comments,
            log_path=self.log_path,
            mode="enforce",
            now=NOW,
        )
        self.assertIsNone(result)
        self.assertEqual(self._read_logs(), [])

    # C2 / A2 & F6
    def test_c2_ci_fail_repair_not_flagged_and_logged_as_no_ask(self):
        reply = "CI FAIL on check (Linux 3.14); dispatching repair to Developer."
        result = hook.evaluate(
            self._payload(reply),
            fetcher=lambda _r, _i, _c: [],
            log_path=self.log_path,
            mode="enforce",
            now=NOW,
        )
        self.assertIsNone(result)
        logs = self._read_logs()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["outcome"], "NO_ASK")
        self.assertEqual(logs[0]["session"], "sess-1")
        self.assertEqual(logs[0]["issue"], "NONE")
        self.assertEqual(len(logs[0]["reply_sha256"]), 64)
        self.assertNotIn("CI FAIL", self.log_path.read_text(encoding="utf-8"))

    # C3 / A3
    def test_c3_repair_caps_ask_without_marker_blocks_with_d3_reason(self):
        reply = "你需要做：repair caps exhausted on #17, should we continue?"
        result = hook.evaluate(
            self._payload(reply),
            fetcher=lambda _r, _i, _c: [],
            log_path=self.log_path,
            mode="enforce",
            now=NOW,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["decision"], "block")
        self.assertIn("D3 REPLAN/DROP", result["reason"])
        self.assertIn("pm_owner_marker.py ask", result["reason"])

    # C4 / A3 & A7
    def test_c4_observe_default_never_blocks_and_enforce_blocks_at_most_once(self):
        reply = "你需要做：A or B"
        # Default mode is observe: never blocks, logs UNGATED_ALERTED.
        obs_res = hook.evaluate(
            self._payload(reply),
            fetcher=lambda _r, _i, _c: [],
            log_path=self.log_path,
            now=NOW,
        )
        self.assertIsNone(obs_res)
        self.assertEqual(self._read_logs()[-1]["outcome"], "UNGATED_ALERTED")

        # Enforce mode blocks once, then UNGATED_ALERTED on next stop.
        enf1 = hook.evaluate(
            self._payload(reply),
            fetcher=lambda _r, _i, _c: [],
            log_path=self.log_path,
            mode="enforce",
            now=NOW,
        )
        self.assertEqual(enf1["decision"], "block")
        self.assertEqual(self._read_logs()[-1]["outcome"], "BLOCK")

        enf2 = hook.evaluate(
            self._payload(reply, stop_hook_active=False),
            fetcher=lambda _r, _i, _c: [],
            log_path=self.log_path,
            mode="enforce",
            now=NOW,
        )
        self.assertIsNone(enf2)
        self.assertEqual(self._read_logs()[-1]["outcome"], "UNGATED_ALERTED")

    # C5 / A4
    def test_c5_natural_language_without_ask_features_allowed_and_logged(self):
        reply = "I think we might want to consider whether this architecture fits."
        result = hook.evaluate(
            self._payload(reply),
            fetcher=lambda _r, _i, _c: [],
            log_path=self.log_path,
            mode="enforce",
            now=NOW,
        )
        self.assertIsNone(result)
        self.assertEqual(self._read_logs()[-1]["outcome"], "NO_ASK")

    # C6 / A2
    def test_c6_progress_notice_and_nothing_to_do_not_flagged(self):
        for reply in (
            "Task #17 completed and merged.",
            "你需要做：目前沒有需要決定的事項。",
            "What is the current status of the Linux CI job?",
        ):
            res = hook.evaluate(
                self._payload(reply),
                fetcher=lambda _r, _i, _c: [],
                log_path=self.log_path,
                mode="enforce",
                now=NOW,
            )
            self.assertIsNone(res)
            self.assertEqual(self._read_logs()[-1]["outcome"], "NO_ASK")

    # C7 / A5
    def test_c7_all_invalid_marker_conditions_fail(self):
        reply_same_issue = f"你需要做：請決定 #17。{COMMENT_URL}"
        reply_other_issue = f"你需要做：請決定 #99。{COMMENT_URL}"
        cases = [
            ("missing", reply_same_issue, []),
            ("wrong_status", reply_same_issue, [
                _make_comment(body="<!-- activity: type=waiting_owner actor=PM target=Owner status=OPEN -->\n<!-- owner-ask: v=1 reserved_row=R-SCOPE -->")
            ]),
            ("wrong_type", reply_same_issue, [
                _make_comment(body="<!-- activity: type=pm_progress actor=PM target=Owner status=WAITING -->\n<!-- owner-ask: v=1 reserved_row=R-SCOPE -->")
            ]),
            ("missing_line2", reply_same_issue, [
                _make_comment(body="<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->\nNo gated line")
            ]),
            ("unknown_row", reply_same_issue, [
                _make_comment(body="<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->\n<!-- owner-ask: v=1 reserved_row=R-FAKE -->")
            ]),
            ("other_issue", reply_other_issue, [_make_comment()]),
            ("expired", reply_same_issue, [_make_comment(created_at=NOW - 7 * 86400)]),
            ("already_answered", reply_same_issue, [
                _make_comment(cid=123, created_at=NOW - 600),
                _make_comment(cid=124, created_at=NOW - 300, body=ANSWER_BODY),
            ]),
            ("created_after_reply", reply_same_issue, [_make_comment(created_at=NOW - 50)]),
            ("untrusted_author", reply_same_issue, [_make_comment(author_association="NONE")]),
        ]
        for label, reply, comments in cases:
            with self.subTest(label=label):
                log_file = self.tmp / f"log-{label}.jsonl"
                res = hook.evaluate(
                    self._payload(reply),
                    fetcher=lambda _r, _i, _c, c=comments: c,
                    log_path=log_file,
                    mode="enforce",
                    now=NOW,
                )
                self.assertIsNotNone(res, label)
                self.assertEqual(res["decision"], "block", label)

    # C8 / A6
    def test_c8_stop_hook_active_missing_transcript_fetcher_error_and_malformed_stdin(self):
        reply = "你需要做：請決定。"
        res_active = hook.evaluate(
            self._payload(reply, stop_hook_active=True),
            fetcher=lambda _r, _i, _c: [],
            log_path=self.log_path,
            mode="enforce",
            now=NOW,
        )
        self.assertIsNone(res_active)
        self.assertEqual(self._read_logs()[-1]["outcome"], "UNGATED_ALERTED")

        # Missing transcript -> ORDER_UNKNOWN, never PASS
        missing_tx = self.tmp / "does-not-exist.jsonl"
        res_missing_tx = hook.evaluate(
            self._payload(f"你需要做：請決定 #17。{COMMENT_URL}", transcript=missing_tx),
            fetcher=lambda _r, _i, _c: [_make_comment()],
            log_path=self.log_path,
            mode="enforce",
            now=NOW,
        )
        self.assertIsNotNone(res_missing_tx)
        self.assertEqual(self._read_logs()[-1]["detail"], "ORDER_UNKNOWN")

        # Fetcher raises -> HOOK_ERROR, exit 0, no block
        def boom(_r, _i, _c):
            raise RuntimeError("network down")

        out = StringIO()
        rc = hook.main(
            stdin=StringIO(json.dumps(self._payload(f"你需要做：請決定 #17。{COMMENT_URL}"))),
            stdout=out,
            fetcher=boom,
            log_path=self.log_path,
            mode="enforce",
            now=NOW,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(self._read_logs()[-1]["outcome"], "HOOK_ERROR")

        # Malformed stdin -> HOOK_ERROR, exit 0, no block
        out2 = StringIO()
        rc2 = hook.main(
            stdin=StringIO("{not-json"),
            stdout=out2,
            log_path=self.log_path,
            mode="enforce",
            now=NOW,
        )
        self.assertEqual(rc2, 0)
        self.assertEqual(out2.getvalue(), "")
        self.assertEqual(self._read_logs()[-1]["outcome"], "HOOK_ERROR")

    # A7: no raw reply or comment text in logs
    def test_a7_logs_contain_no_raw_reply_or_comment_text(self):
        secret_reply_phrase = "UNIQUE_REPLY_TEXT_MARKER_991"
        secret_comment_phrase = "UNIQUE_COMMENT_TEXT_MARKER_992"
        comments = [_make_comment(body=VALID_ASK_BODY + "\n" + secret_comment_phrase)]
        reply = f"你需要做：{secret_reply_phrase} (R-SCOPE)"
        hook.evaluate(
            self._payload(reply),
            fetcher=lambda _r, _i, _c: comments,
            log_path=self.log_path,
            mode="observe",
            now=NOW,
        )
        raw_log = self.log_path.read_text(encoding="utf-8")
        self.assertNotIn(secret_reply_phrase, raw_log)
        self.assertNotIn(secret_comment_phrase, raw_log)

    # F1 regression
    def test_f1_lettered_options_without_a_and_b_are_detected(self):
        reply = "Owner, choose:\nC. approve\nD. decline"
        result = hook.evaluate(
            self._payload(reply),
            fetcher=lambda _r, _i, _c: [],
            log_path=self.log_path,
            mode="enforce",
            now=NOW,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["decision"], "block")

    # F2 regression
    def test_f2_non_user_assistant_record_without_timestamp_returns_order_unknown(self):
        tx = _write_transcript(
            self.tmp / "bad-summary.jsonl",
            [
                {"type": "summary", "summary": "session summary without timestamp"},
                {"type": "user", "timestamp": _iso(NOW - 300), "message": {"content": "hi"}},
                {"type": "assistant", "timestamp": _iso(NOW - 100), "message": {"content": "ok"}},
            ],
        )
        reply = f"你需要做：請決定 #17。{COMMENT_URL}"
        result = hook.evaluate(
            self._payload(reply, transcript=tx),
            fetcher=lambda _r, _i, _c: [_make_comment()],
            log_path=self.log_path,
            mode="enforce",
            now=NOW,
        )
        self.assertIsNotNone(result)
        self.assertEqual(self._read_logs()[-1]["detail"], "ORDER_UNKNOWN")

    # F3 regression
    def test_f3_malformed_issue_url_with_valid_suffix_rejected(self):
        reply = f"你需要做：請決定 #7。https://github.com/a/b/issues/7#issuecomment-123"
        bad_comment = _make_comment(issue_url="garbage/issues/7")
        result = hook.evaluate(
            self._payload(reply),
            fetcher=lambda _r, _i, _c: [bad_comment],
            log_path=self.log_path,
            mode="enforce",
            now=NOW,
        )
        self.assertIsNotNone(result)
        self.assertEqual(self._read_logs()[-1]["detail"], "INVALID_ISSUE_URL")

    # F4 regression
    def test_f4_consecutive_leading_markers_settle_ask(self):
        reply = f"你需要做：請決定 #17。{COMMENT_URL}"
        comments = [
            _make_comment(cid=123, created_at=NOW - 600),
            _make_comment(cid=124, created_at=NOW - 300, body=MULTI_LINE_ANSWER_BODY),
        ]
        result = hook.evaluate(
            self._payload(reply),
            fetcher=lambda _r, _i, _c: comments,
            log_path=self.log_path,
            mode="enforce",
            now=NOW,
        )
        self.assertIsNotNone(result)
        self.assertEqual(self._read_logs()[-1]["detail"], "MARKER_SETTLED")

    # F5 regression
    def test_f5_block_record_outside_8mib_tail_still_prevents_second_block(self):
        reply = "你需要做：請決定。"
        # First call blocks and writes a BLOCK record at the top of the log.
        first = hook.evaluate(
            self._payload(reply),
            fetcher=lambda _r, _i, _c: [],
            log_path=self.log_path,
            mode="enforce",
            now=NOW,
        )
        self.assertEqual(first["decision"], "block")

        # Append > 8 MiB of unrelated log records after the BLOCK record.
        filler_line = json.dumps({
            "ts": _iso(NOW),
            "session": "other-session",
            "issue": "NONE",
            "outcome": "NO_ASK",
            "detail": "NO_ASK",
            "reply_sha256": "0" * 64,
            "mode": "enforce",
        }) + "\n"
        repeat_count = (8 * 1024 * 1024) // len(filler_line) + 100
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(filler_line * repeat_count)

        # Repeating the same reply in the same session with stop_hook_active=False must NOT block again.
        second = hook.evaluate(
            self._payload(reply, stop_hook_active=False),
            fetcher=lambda _r, _i, _c: [],
            log_path=self.log_path,
            mode="enforce",
            now=NOW,
        )
        self.assertIsNone(second)
        self.assertEqual(self._read_logs()[-1]["outcome"], "UNGATED_ALERTED")

    # Security v91 Low regression
    def test_security_v91_untrusted_settling_comment_ignored(self):
        reply = f"你需要做：請決定 #17。{COMMENT_URL}"
        comments = [
            _make_comment(cid=123, created_at=NOW - 600),
            _make_comment(
                cid=124,
                created_at=NOW - 300,
                body=ANSWER_BODY,
                author_association="NONE",
            ),
        ]
        result = hook.evaluate(
            self._payload(reply),
            fetcher=lambda _r, _i, _c: comments,
            log_path=self.log_path,
            mode="enforce",
            now=NOW,
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
