"""#1943: owner_ask_audit reports Owner wait time per ask (mean, total, unanswered)."""
from io import StringIO
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "owner_ask_audit.py"
SPEC = importlib.util.spec_from_file_location("owner_ask_audit_wait", SCRIPT)
audit_mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_mod)

ASK = "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->"
ANSWER = "<!-- activity: type=pm_progress actor=PM target=Owner status=ANSWERED -->"
MISUSED = "<!-- activity: type=waiting_owner actor=PM target=Owner status=ANSWERED -->"
SINCE, UNTIL = "2026-11-01T00:00:00Z", "2026-11-08T00:00:00Z"


def comment(cid, issue, at, marker):
    return {"id": cid, "issue_url": f"https://api.github.com/repos/a/b/issues/{issue}",
            "created_at": at, "body": marker + "\nsynthetic text"}


class OwnerWaitTests(unittest.TestCase):
    def wait(self, comments):
        return audit_mod.audit(comments, SINCE, UNTIL)["owner_wait"]

    def test_mean_total_and_unanswered(self):
        comments = [comment(1, 10, "2026-11-02T00:00:00Z", ASK),
                    comment(2, 10, "2026-11-02T01:00:00Z", ANSWER),
                    comment(3, 11, "2026-11-03T00:00:00Z", ASK),
                    comment(4, 11, "2026-11-09T00:00:00Z", ANSWER),
                    comment(5, 12, "2026-11-04T00:00:00Z", ASK)]
        # The issue 11 answer comes after the window end and still counts.
        self.assertEqual(self.wait(comments), {
            "answered": 2, "unanswered": 1, "total_seconds": 522000, "mean_seconds": 261000.0})

    def test_earlier_other_issue_or_misused_answer_does_not_count(self):
        comments = [comment(1, 10, "2026-11-02T00:00:00Z", ANSWER),
                    comment(2, 10, "2026-11-02T01:00:00Z", ASK),
                    comment(3, 11, "2026-11-02T02:00:00Z", ANSWER),
                    comment(4, 10, "2026-11-02T03:00:00Z", MISUSED)]
        self.assertEqual(self.wait(comments), {
            "answered": 0, "unanswered": 1, "total_seconds": 0, "mean_seconds": None})

    def test_next_answer_is_used(self):
        comments = [comment(1, 10, "2026-11-02T00:00:00Z", ASK),
                    comment(3, 10, "2026-11-02T05:00:00Z", ANSWER),
                    comment(2, 10, "2026-11-02T02:00:00Z", ANSWER)]
        self.assertEqual(self.wait(comments)["total_seconds"], 7200)

    def test_cli_summary_carries_owner_wait(self):
        comments = [comment(1, 10, "2026-11-02T00:00:00Z", ASK),
                    comment(2, 10, "2026-11-02T00:30:00Z", ANSWER)]
        with mock.patch.object(audit_mod, "fetch_comments", return_value=comments), \
                mock.patch("sys.stdout", new_callable=StringIO) as out:
            code = audit_mod.main(["--since", SINCE, "--until", UNTIL, "--repo", "a/b"])
        self.assertEqual(code, 0)
        summary = json.loads(out.getvalue().splitlines()[0])
        self.assertEqual(summary["owner_wait"], {
            "answered": 1, "unanswered": 0, "total_seconds": 1800, "mean_seconds": 1800.0})


if __name__ == "__main__":
    unittest.main()
