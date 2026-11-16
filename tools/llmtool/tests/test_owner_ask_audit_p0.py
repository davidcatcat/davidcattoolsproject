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


if __name__ == "__main__":
    unittest.main()
