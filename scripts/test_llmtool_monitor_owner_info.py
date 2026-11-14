import os
import sys
import time
import unittest

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import llmtool_monitor as mon

NOW = 1_800_000_000.0


def iso(seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def comment(number, seconds, body):
    return {
        "created_at": iso(seconds),
        "body": body,
        "author_association": "OWNER",
        "user": {"login": mon.OWNER_GITHUB_LOGIN},
        "issue_url": f"https://api.github.com/repos/o/r/issues/{number}",
    }


class TestLlmtoolMonitorOwnerInfo(unittest.TestCase):
    def test_lone_notice_gives_no_pending_row(self):
        # A lone target=Owner NOTICE comment gives no pending row
        body = "<!-- activity: type=pm_progress actor=PM target=Owner status=NOTICE -->"
        c = comment(101, NOW, body)
        pending = mon.owner_pending([c], [])
        self.assertEqual(pending, [])

    def test_waiting_ask_followed_by_notice_stays_pending(self):
        # A waiting_owner WAITING ask followed later by a NOTICE on the same thread stays pending
        body_ask = "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->\nNeed decision"
        body_notice = "<!-- activity: type=pm_progress actor=PM target=Owner status=NOTICE -->"
        c1 = comment(102, NOW, body_ask)
        c2 = comment(102, NOW + 60, body_notice)
        pending = mon.owner_pending([c1, c2], [])
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].reference, "#102")
        self.assertEqual(pending[0].status, "WAITING")
        self.assertEqual(pending[0].summary, "Need decision")

    def test_info_and_tracking_behave_like_notice(self):
        # INFO and TRACKING behave like NOTICE
        c_info = comment(103, NOW, "<!-- activity: type=pm_progress actor=PM target=Owner status=INFO -->")
        self.assertEqual(mon.owner_pending([c_info], []), [])

        c_tracking = comment(104, NOW, "<!-- activity: type=pm_progress actor=PM target=Owner status=TRACKING -->")
        self.assertEqual(mon.owner_pending([c_tracking], []), [])

        c_ask = comment(105, NOW, "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->\nDecide")
        c_later_info = comment(105, NOW + 60, "<!-- activity: type=pm_progress actor=PM target=Owner status=INFO -->")
        pending_info = mon.owner_pending([c_ask, c_later_info], [])
        self.assertEqual(len(pending_info), 1)
        self.assertEqual(pending_info[0].status, "WAITING")

        c_later_tracking = comment(105, NOW + 120, "<!-- activity: type=pm_progress actor=PM target=Owner status=TRACKING -->")
        pending_tracking = mon.owner_pending([c_ask, c_later_tracking], [])
        self.assertEqual(len(pending_tracking), 1)
        self.assertEqual(pending_tracking[0].status, "WAITING")

    def test_type_x_waiting_and_repair_gate_authorization_requested_give_pending_rows(self):
        # A type=x WAITING ask and a repair_gate AUTHORIZATION_REQUESTED ask still give pending rows
        c_type_x = comment(106, NOW, "<!-- activity: type=x actor=PM target=Owner status=WAITING -->\nAsk X")
        pending_x = mon.owner_pending([c_type_x], [])
        self.assertEqual(len(pending_x), 1)
        self.assertEqual(pending_x[0].reference, "#106")
        self.assertEqual(pending_x[0].status, "WAITING")
        self.assertEqual(pending_x[0].summary, "Ask X")

        c_repair = comment(107, NOW, "<!-- activity: type=repair_gate actor=PM target=Owner status=AUTHORIZATION_REQUESTED -->\nAuthorize repair")
        pending_repair = mon.owner_pending([c_repair], [])
        self.assertEqual(len(pending_repair), 1)
        self.assertEqual(pending_repair[0].reference, "#107")
        self.assertEqual(pending_repair[0].status, "AUTHORIZATION_REQUESTED")
        self.assertEqual(pending_repair[0].summary, "Authorize repair")

    def test_headline_skips_owner_ask_record(self):
        body = (
            "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->\n"
            "<!-- owner-ask: v=1 reserved_row=R-ACCEPT -->\n"
            "\n"
            "Accept the new layout?"
        )
        c = comment(108, NOW, body)
        pending = mon.owner_pending([c], [])
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].summary, "Accept the new layout?")

        lines = mon.office_lines([], pending=pending)
        self.assertIn("Accept the new layout?", lines[1])

    def test_headline_empty_with_only_markers(self):
        body = (
            "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->\n"
            "<!-- owner-ask: v=1 reserved_row=R-ACCEPT -->"
        )
        c = comment(109, NOW, body)
        pending = mon.owner_pending([c], [])
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].summary, "")


if __name__ == "__main__":
    unittest.main()
