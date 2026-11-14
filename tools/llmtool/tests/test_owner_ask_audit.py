"""Tests for owner_ask_audit."""
from io import StringIO
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "owner_ask_audit.py"
SPEC = importlib.util.spec_from_file_location("owner_ask_audit", SCRIPT)
owner_ask_audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = owner_ask_audit
SPEC.loader.exec_module(owner_ask_audit)


def _make_comment(cid: int, created_at: str, issue_num: int, body: str) -> dict:
    return {
        "id": cid,
        "created_at": created_at,
        "issue_url": f"https://api.github.com/repos/a/b/issues/{issue_num}",
        "body": body,
    }


class OwnerAskAuditTests(unittest.TestCase):
    def setUp(self):
        self.since = "2026-11-01T00:00:00Z"
        self.until = "2026-11-08T00:00:00Z"
        self.comments = [
            _make_comment(1, "2026-10-31T23:59:59Z", 1,
                          "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->\n<!-- owner-ask: v=1 reserved_row=R-SCOPE -->"),
            _make_comment(2, "2026-11-08T00:00:00Z", 2,
                          "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->\n<!-- owner-ask: v=1 reserved_row=R-SCOPE -->"),
            _make_comment(101, "2026-11-01T01:00:00Z", 10,
                          "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->\n<!-- owner-ask: v=1 reserved_row=R-SCOPE -->\nQuestion"),
            _make_comment(102, "2026-11-01T02:00:00Z", 11,
                          "<!-- activity: type=waiting_owner actor=PM target=Owner status=BLOCKED -->\n<!-- owner-ask: v=1 reserved_row=R-OTHER -->\nBlocked"),
            _make_comment(103, "2026-11-01T03:00:00Z", 12,
                          "<!-- activity: type=waiting_owner actor=PM target=Owner status=ANSWERED -->\nMisused"),
            _make_comment(104, "2026-11-01T04:00:00Z", 13,
                          "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->\n<!-- owner-ask: v=1 reserved_row=R-SCOPE -->\nCorrected"),
            _make_comment(105, "2026-11-01T05:00:00Z", 13,
                          "<!-- activity: type=audit_correction actor=PM target=PM status=RECORDED -->\nCorrection for 104"),
            _make_comment(106, "2026-11-01T06:00:00Z", 14,
                          "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->\nUngated single line"),
            _make_comment(107, "2026-11-01T07:00:00Z", 15,
                          "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->\n<!-- owner-ask: v=2 reserved_row=R-SCOPE -->\nMalformed line 2"),
            _make_comment(108, "2026-11-01T08:00:00Z", 16,
                          "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->\n<!-- owner-ask: v=1 reserved_row=R-SCOPE -->\nWithdrawn"),
            _make_comment(109, "2026-11-01T09:00:00Z", 16,
                          "<!-- activity: type=pm_progress actor=PM target=Owner status=WITHDRAWN -->\nWithdraw 108"),
            _make_comment(110, "2026-11-01T10:00:00Z", 17,
                          "Regular comment without any marker\nSecond line"),
        ]

    def test_audit_counts_and_metrics(self):
        res = owner_ask_audit.audit(self.comments, self.since, self.until)
        self.assertEqual(res["window"], [self.since, self.until])
        self.assertEqual(res["comments"], 10)
        self.assertEqual(res["misused"], 1)
        self.assertEqual(res["corrected"], 1)
        self.assertEqual(res["asks"], 5)
        self.assertEqual(res["gated"], 3)
        self.assertEqual(res["ungated"], 2)
        self.assertEqual(res["rows"], {"R-OTHER": 1, "R-SCOPE": 2})
        self.assertAlmostEqual(res["r_other_ratio"], 0.2)
        self.assertTrue(res["r_other_over_limit"])
        expected_items = [
            ["2026-11-01T01:00:00Z", 10, 101, "R-SCOPE"],
            ["2026-11-01T02:00:00Z", 11, 102, "R-OTHER"],
            ["2026-11-01T06:00:00Z", 14, 106, "UNGATED"],
            ["2026-11-01T07:00:00Z", 15, 107, "UNGATED"],
            ["2026-11-01T08:00:00Z", 16, 108, "R-SCOPE"],
        ]
        self.assertEqual(res["items"], expected_items)

    def test_r_other_ratio_under_limit_and_empty(self):
        res_empty = owner_ask_audit.audit([], self.since, self.until)
        self.assertEqual(res_empty["asks"], 0)
        self.assertEqual(res_empty["r_other_ratio"], 0.0)
        self.assertFalse(res_empty["r_other_over_limit"])

        ten_scope = [
            _make_comment(i, f"2026-11-02T{i:02d}:00:00Z", i,
                          "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->\n<!-- owner-ask: v=1 reserved_row=R-SCOPE -->")
            for i in range(10)
        ]
        one_other = [
            _make_comment(99, "2026-11-03T00:00:00Z", 99,
                          "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->\n<!-- owner-ask: v=1 reserved_row=R-OTHER -->")
        ]
        res = owner_ask_audit.audit(ten_scope + one_other, self.since, self.until)
        self.assertEqual(res["asks"], 11)
        self.assertAlmostEqual(res["r_other_ratio"], 1 / 11)
        self.assertFalse(res["r_other_over_limit"])

    def test_cli_success(self):
        with mock.patch.object(owner_ask_audit, "fetch_comments", return_value=self.comments), \
             mock.patch("sys.stdout", new_callable=StringIO) as out:
            rc = owner_ask_audit.main(["--since", self.since, "--until", self.until, "--repo", "a/b"])
            self.assertEqual(rc, 0)
            lines = out.getvalue().splitlines()
            self.assertEqual(len(lines), 6)
            summary = json.loads(lines[0])
            self.assertEqual(summary["asks"], 5)
            self.assertNotIn("items", summary)
            self.assertEqual(lines[1], "2026-11-01T01:00:00Z\t10\t101\tR-SCOPE")
            self.assertEqual(lines[2], "2026-11-01T02:00:00Z\t11\t102\tR-OTHER")
            self.assertEqual(lines[3], "2026-11-01T06:00:00Z\t14\t106\tUNGATED")
            self.assertEqual(lines[4], "2026-11-01T07:00:00Z\t15\t107\tUNGATED")
            self.assertEqual(lines[5], "2026-11-01T08:00:00Z\t16\t108\tR-SCOPE")

    def test_cli_fetch_failure(self):
        with mock.patch.object(owner_ask_audit, "fetch_comments", side_effect=owner_ask_audit.AuditError("api down")), \
             mock.patch("sys.stderr", new_callable=StringIO) as err:
            rc = owner_ask_audit.main(["--since", self.since, "--until", self.until, "--repo", "a/b"])
            self.assertEqual(rc, 1)
            err_output = err.getvalue()
            self.assertIn("owner_ask_audit: api down", err_output)
            self.assertNotIn("Traceback", err_output)

    def test_cli_invalid_args(self):
        with mock.patch("sys.stderr", new_callable=StringIO) as err:
            rc = owner_ask_audit.main(["--repo", "a/b"])
            self.assertEqual(rc, 1)
            self.assertIn("owner_ask_audit: invalid arguments", err.getvalue())


if __name__ == "__main__":
    unittest.main()
