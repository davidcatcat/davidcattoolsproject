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


class TestLlmtoolMonitorUngatedAsk(unittest.TestCase):
    def test_waiting_owner_without_line_2_is_ungated(self):
        # A waiting_owner WAITING ask without line 2 is ungated and its star row contains UNGATED_OWNER_ASK
        body = "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->"
        c = comment(101, NOW, body)
        pending = mon.owner_pending([c], [])
        self.assertEqual(len(pending), 1)
        self.assertTrue(pending[0].ungated)

        view = mon.office_view([], pending=pending, now=NOW)
        star_lines = [line for line, _ in view if line.startswith("★ 等你決定")]
        self.assertEqual(len(star_lines), 1)
        self.assertIn("（UNGATED_OWNER_ASK：未經提問閘門）", star_lines[0])

    def test_waiting_owner_with_plain_line_2_is_ungated(self):
        # A waiting_owner WAITING ask without an owner-ask line 2 is ungated
        body = "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->\nNeed decision"
        c = comment(102, NOW, body)
        pending = mon.owner_pending([c], [])
        self.assertEqual(len(pending), 1)
        self.assertTrue(pending[0].ungated)

        view = mon.office_view([], pending=pending, now=NOW)
        star_lines = [line for line, _ in view if line.startswith("★ 等你決定")]
        self.assertEqual(len(star_lines), 1)
        self.assertIn("（UNGATED_OWNER_ASK：未經提問閘門）", star_lines[0])
        self.assertIn(" — Need decision", star_lines[0])

    def test_waiting_owner_with_valid_owner_ask_is_not_ungated(self):
        # With a valid '<!-- owner-ask: v=1 reserved_row=R-SCOPE -->' line 2 it is not ungated
        body = (
            "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->\n"
            "<!-- owner-ask: v=1 reserved_row=R-SCOPE -->\n"
            "\n"
            "Need decision"
        )
        c = comment(103, NOW, body)
        pending = mon.owner_pending([c], [])
        self.assertEqual(len(pending), 1)
        self.assertFalse(pending[0].ungated)

        view = mon.office_view([], pending=pending, now=NOW)
        star_lines = [line for line, _ in view if line.startswith("★ 等你決定")]
        self.assertEqual(len(star_lines), 1)
        self.assertNotIn("UNGATED_OWNER_ASK", star_lines[0])

    def test_waiting_owner_with_malformed_line_2_is_ungated(self):
        # A malformed line 2 (lower-case row) is ungated
        body = (
            "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->\n"
            "<!-- owner-ask: v=1 reserved_row=r-scope -->\n"
            "\n"
            "Need decision"
        )
        c = comment(104, NOW, body)
        pending = mon.owner_pending([c], [])
        self.assertEqual(len(pending), 1)
        self.assertTrue(pending[0].ungated)

        view = mon.office_view([], pending=pending, now=NOW)
        star_lines = [line for line, _ in view if line.startswith("★ 等你決定")]
        self.assertEqual(len(star_lines), 1)
        self.assertIn("（UNGATED_OWNER_ASK：未經提問閘門）", star_lines[0])

    def test_ask_created_before_gate_is_not_ungated(self):
        # An ask created before OWNER_ASK_GATE_SINCE (seconds 1794672000) is not ungated
        body = "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->\nNeed decision"
        c = comment(105, 1794672000.0, body)
        pending = mon.owner_pending([c], [])
        self.assertEqual(len(pending), 1)
        self.assertFalse(pending[0].ungated)

        view = mon.office_view([], pending=pending, now=NOW)
        star_lines = [line for line, _ in view if line.startswith("★ 等你決定")]
        self.assertEqual(len(star_lines), 1)
        self.assertNotIn("UNGATED_OWNER_ASK", star_lines[0])

    def test_repair_gate_waiting_ask_is_not_ungated(self):
        # A type=repair_gate WAITING ask is not ungated (only waiting_owner asks are checked)
        body = "<!-- activity: type=repair_gate actor=PM target=Owner status=WAITING -->\nNeed decision"
        c = comment(106, NOW, body)
        pending = mon.owner_pending([c], [])
        self.assertEqual(len(pending), 1)
        self.assertFalse(pending[0].ungated)

        view = mon.office_view([], pending=pending, now=NOW)
        star_lines = [line for line, _ in view if line.startswith("★ 等你決定")]
        self.assertEqual(len(star_lines), 1)
        self.assertNotIn("UNGATED_OWNER_ASK", star_lines[0])

    def test_existing_owner_pending_without_field_renders_as_before(self):
        # An existing OwnerPending built without the field renders exactly as before (no UNGATED text)
        op = mon.OwnerPending(NOW, "#310", "WAITING", "Need decision")
        self.assertFalse(op.ungated)

        view = mon.office_view([], pending=[op], now=NOW)
        star_lines = [line for line, _ in view if line.startswith("★ 等你決定")]
        self.assertEqual(len(star_lines), 1)
        self.assertNotIn("UNGATED_OWNER_ASK", star_lines[0])
        self.assertNotIn("未經提問閘門", star_lines[0])
        self.assertIn("GitHub #310", star_lines[0])
        self.assertIn(" — Need decision", star_lines[0])

        # Exact match with an explicitly ungated=False OwnerPending
        op_explicit = mon.OwnerPending(NOW, "#310", "WAITING", "Need decision", ungated=False)
        self.assertEqual(mon.office_view([], pending=[op], now=NOW),
                         mon.office_view([], pending=[op_explicit], now=NOW))


if __name__ == "__main__":
    unittest.main()
