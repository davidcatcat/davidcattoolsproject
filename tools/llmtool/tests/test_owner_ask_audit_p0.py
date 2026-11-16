"""#1943 P0: owner_ask_audit day-window fetch, dedupe, ask classes, chat source, metrics."""
from io import StringIO
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "owner_ask_audit.py"
SPEC = importlib.util.spec_from_file_location("owner_ask_audit_p0", SCRIPT)
oa = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(oa)

SINCE, UNTIL = "2026-11-01T00:00:00Z", "2026-11-08T00:00:00Z"
WAIT = "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->"
GATE = "<!-- owner-ask: v=1 reserved_row=R-SCOPE -->"
OTHER = "<!-- owner-ask: v=1 reserved_row=R-OTHER -->"
PROGRESS_ASK = "<!-- activity: type=pm_progress actor=PM target=Owner status=WAITING -->"
NOTICE = "<!-- activity: type=d4_notice actor=PM target=Owner status=NOTICE -->"
ANSWERED = "<!-- activity: type=pm_progress actor=PM target=Owner status=ANSWERED -->"


def comment(cid, issue, at, *lines):
    url = "https://api.github.com/repos/a/b/issues/%d" % issue
    return dict(id=cid, issue_url=url, created_at=at, body="\n".join(lines + ("synthetic",)))


def fake_run(pages, calls):
    def run(argv, *, timeout=60):
        calls.append((argv, timeout))
        url = argv[-1]
        start = url.split("since=")[1].split("&")[0]
        page = int(url.rsplit("&page=", 1)[1])
        items = pages.get((start, page), [])
        return SimpleNamespace(returncode=0, stdout=json.dumps(items).encode())
    return run


class FetchTests(unittest.TestCase):
    def fetch(self, pages, until):
        calls = []
        with mock.patch.object(oa, "_run", fake_run(pages, calls)):
            with mock.patch.object(oa.time, "time", return_value=oa._seconds(until)):
                return oa.fetch_comments("a/b", SINCE, until), calls

    def test_seven_days_are_seven_bounded_single_page_calls(self):
        first = comment(1, 10, "2026-11-01T05:00:00Z", WAIT)
        second = comment(2, 11, "2026-11-02T05:00:00Z", WAIT)
        pages = {("2026-11-01T00:00:00Z", 1): [first]}
        pages[("2026-11-02T00:00:00Z", 1)] = [dict(first), second]
        got, calls = self.fetch(pages, UNTIL)
        self.assertEqual([item["id"] for item in got], [1, 2])
        self.assertEqual(len(calls), 7)
        self.assertTrue(all(timeout == oa.PAGE_TIMEOUT for _argv, timeout in calls))
        self.assertTrue(all("--paginate" not in argv for argv, _timeout in calls))

    def test_full_page_reads_the_next_page_and_stops_at_the_day_end(self):
        day = [comment(i, i, "2026-11-01T01:00:00Z", WAIT) for i in range(100)]
        late = [comment(500, 5, "2026-11-01T02:00:00Z", WAIT)]
        late.append(comment(501, 6, "2026-11-02T01:00:00Z", WAIT))
        pages = {("2026-11-01T00:00:00Z", 1): day, ("2026-11-01T00:00:00Z", 2): late}
        got, calls = self.fetch(pages, "2026-11-02T00:00:00Z")
        self.assertEqual((len(got), len(calls)), (101, 2))

    def test_failed_call_raises(self):
        failed = SimpleNamespace(returncode=1, stdout=b"")
        with mock.patch.object(oa, "_run", return_value=failed):
            with mock.patch.object(oa.time, "time", return_value=oa._seconds(UNTIL)):
                with self.assertRaises(oa.AuditError):
                    oa.fetch_comments("a/b", SINCE, UNTIL)


class AuditP0Tests(unittest.TestCase):
    def test_2087_progress_marker_ask_is_counted(self):
        ask = comment(6893575916, 2087, "2026-11-01T11:23:06Z", PROGRESS_ASK)
        result = oa.audit([ask], SINCE, UNTIL)
        self.assertEqual((result["asks"], result["ungated"]), (1, 1))

    def test_code_notice_and_answered_markers_are_not_asks(self):
        found = [comment(1, 5, "2026-11-01T01:00:00Z", NOTICE)]
        found.append(comment(2, 5, "2026-11-01T02:00:00Z", ANSWERED))
        result = oa.audit(found, SINCE, UNTIL)
        self.assertEqual((result["asks"], result["notices"]), (0, 2))

    def test_duplicate_comment_and_same_hour_cross_entry_ask_count_once(self):
        first = comment(1, 7, "2026-11-01T03:00:00Z", WAIT, GATE)
        echo = comment(2, 7, "2026-11-01T03:40:00Z", PROGRESS_ASK)
        result = oa.audit([first, dict(first), echo], SINCE, UNTIL)
        self.assertEqual((result["asks"], result["duplicates"], result["gated"]), (1, 1, 1))

    def test_metrics_count_true_reserved_rows(self):
        found = [comment(1, 7, "2026-11-01T03:00:00Z", WAIT, GATE)]
        found.append(comment(2, 8, "2026-11-01T05:00:00Z", WAIT, OTHER))
        found.append(comment(3, 9, "2026-11-01T06:00:00Z", WAIT))
        got = oa.audit(found, SINCE, UNTIL)["metrics"]
        self.assertEqual((got["owner_decisions"], got["true_reserved"]), (3, 1))
        self.assertEqual((got["gate_coverage"], got["invalid_escalation_rate"]), (0.6667, 0.3333))

    def test_unknown_reserved_row_is_ungated_and_excluded_from_true_reserved(self):
        fake = "<!-- owner-ask: v=1 reserved_row=R-FAKE -->"
        ask = comment(1, 10, "2026-11-01T03:00:00Z", WAIT, fake)
        result = oa.audit([ask], SINCE, UNTIL)
        self.assertEqual((result["asks"], result["gated"], result["ungated"]), (1, 0, 1))
        self.assertEqual(result["rows"], {})
        self.assertEqual(result["items"], [["2026-11-01T03:00:00Z", 10, 1, "UNGATED"]])
        self.assertEqual(result["metrics"]["true_reserved"], 0)
        self.assertEqual(result["metrics"]["gate_coverage"], 0.0)
        self.assertEqual(result["metrics"]["invalid_escalation_rate"], 1.0)

    def test_each_valid_row_class(self):
        for i, row in enumerate(oa.RESERVED_ROWS, start=1):
            line = f"<!-- owner-ask: v=1 reserved_row={row} -->"
            ask = comment(i, i, f"2026-11-01T{i:02d}:00:00Z", WAIT, line)
            result = oa.audit([ask], SINCE, UNTIL)
            self.assertEqual((result["asks"], result["gated"], result["ungated"]), (1, 1, 0))
            self.assertEqual(result["rows"], {row: 1})
            self.assertEqual(result["items"][0][3], row)
            expected_true = 0 if row == "R-OTHER" else 1
            self.assertEqual(result["metrics"]["true_reserved"], expected_true)
            self.assertEqual(result["metrics"]["gate_coverage"], 1.0)
            self.assertEqual(result["metrics"]["invalid_escalation_rate"], 0.0)

    def test_r_other_is_gated_and_excluded_from_true_reserved(self):
        ask = comment(1, 10, "2026-11-01T03:00:00Z", WAIT, OTHER)
        result = oa.audit([ask], SINCE, UNTIL)
        self.assertEqual((result["asks"], result["gated"], result["ungated"]), (1, 1, 0))
        self.assertEqual(result["rows"], {"R-OTHER": 1})
        self.assertEqual(result["items"], [["2026-11-01T03:00:00Z", 10, 1, "R-OTHER"]])
        self.assertEqual(result["metrics"]["true_reserved"], 0)
        self.assertEqual(result["metrics"]["gate_coverage"], 1.0)
        self.assertEqual(result["metrics"]["invalid_escalation_rate"], 0.0)
        self.assertEqual(result["r_other_ratio"], 1.0)

    def test_mixed_rows_with_unknown_r_value(self):
        scope_line = "<!-- owner-ask: v=1 reserved_row=R-SCOPE -->"
        fake_line = "<!-- owner-ask: v=1 reserved_row=R-FAKE -->"
        other_line = "<!-- owner-ask: v=1 reserved_row=R-OTHER -->"
        found = [
            comment(1, 7, "2026-11-01T03:00:00Z", WAIT, scope_line),
            comment(2, 8, "2026-11-01T04:00:00Z", WAIT, other_line),
            comment(3, 9, "2026-11-01T05:00:00Z", WAIT, fake_line),
        ]
        result = oa.audit(found, SINCE, UNTIL)
        self.assertEqual((result["asks"], result["gated"], result["ungated"]), (3, 2, 1))
        self.assertEqual(result["rows"], {"R-SCOPE": 1, "R-OTHER": 1})
        self.assertNotIn("R-FAKE", result["rows"])
        self.assertEqual(result["metrics"]["true_reserved"], 1)
        self.assertEqual(result["metrics"]["gate_coverage"], 0.6667)
        self.assertEqual(result["metrics"]["invalid_escalation_rate"], 0.3333)


def transcript(directory, *entries):
    path = Path(directory) / "session.jsonl"
    path.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")


def said(at, text, kind="assistant"):
    return dict(type=kind, timestamp=at, message=dict(content=[dict(type="text", text=text)]))


class ChatSourceTests(unittest.TestCase):
    def test_wp8_chat_ask_without_marker_is_counted(self):
        ask = oa.CHAT_ASK + "choose option A or B"
        entries = [said("2026-11-02T04:00:00.123Z", ask)]
        entries.append(said("2026-11-02T05:00:00.000Z", oa.CHAT_ASK + oa.CHAT_NOTHING))
        entries.append(said("2026-11-02T06:00:00Z", ask, "user"))
        with tempfile.TemporaryDirectory() as tmp:
            transcript(tmp, *entries)
            chat = oa.chat_asks(tmp, SINCE, UNTIL)
        self.assertEqual(chat, [oa._seconds("2026-11-02T04:00:00Z")])
        result = oa.audit([], SINCE, UNTIL, chat, 50)
        counted = dict(status="COUNTED", asks=1, with_marker=0, without_marker=1)
        self.assertEqual(result["chat"], counted)
        got = result["metrics"]
        self.assertEqual((got["owner_decisions"], got["invalid_escalation_rate"]), (1, 1.0))
        self.assertEqual(got["per_100_effective_tasks"], 2.0)

    def test_chat_ask_near_a_github_marker_is_not_a_second_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            transcript(tmp, said("2026-11-02T04:30:00Z", oa.CHAT_ASK + "pick A"))
            chat = oa.chat_asks(tmp, SINCE, UNTIL)
        marked = comment(1, 9, "2026-11-02T04:00:00Z", WAIT, GATE)
        result = oa.audit([marked], SINCE, UNTIL, chat)
        self.assertEqual(result["chat"]["with_marker"], 1)
        got = result["metrics"]
        self.assertEqual((got["owner_decisions"], got["gate_coverage"]), (1, 1.0))

    def test_missing_chat_source_is_unknown_not_zero(self):
        self.assertIsNone(oa.chat_asks("/nonexistent/llmtool-transcripts", SINCE, UNTIL))
        result = oa.audit([], SINCE, UNTIL)
        self.assertEqual(result["chat"], dict(status="UNKNOWN"))
        got = result["metrics"]
        self.assertEqual((got["complete"], got["per_100_effective_tasks"]), (False, "UNKNOWN"))
        self.assertEqual(got["not_counted"][0], dict(entry="pm_chat", count="UNKNOWN"))

    def test_cli_passes_chat_and_effective_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            transcript(tmp, said("2026-11-02T04:00:00Z", oa.CHAT_ASK + "pick A"))
            args = ["--since", SINCE, "--until", UNTIL, "--repo", "a/b", "--chat-transcripts", tmp]
            with mock.patch.object(oa, "fetch_comments", return_value=[]) as fetch:
                with mock.patch("sys.stdout", new_callable=StringIO) as out:
                    code = oa.main(args + ["--effective-tasks", "10"])
        self.assertEqual(code, 0)
        fetch.assert_called_once_with("a/b", SINCE, UNTIL)
        summary = json.loads(out.getvalue().splitlines()[0])
        self.assertEqual(summary["metrics"]["per_100_effective_tasks"], 10.0)


if __name__ == "__main__":
    unittest.main()
