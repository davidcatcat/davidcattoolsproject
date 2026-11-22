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


NOTICE = "<!-- activity: type=pm_progress actor=PM target=Owner status=NOTICE -->"
ASK = "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->"


class TestOwnerNotices(unittest.TestCase):
    """#2311: notices are listed apart from asks and never look like one."""

    def test_notice_is_listed_newest_per_thread(self):
        notices = mon.owner_notices([
            comment(201, NOW, NOTICE + "\nSpec error in Task A"),
            comment(201, NOW + 60, NOTICE + "\nSpec error in Task B"),
            comment(202, NOW + 30, "<!-- activity: type=pm_progress actor=PM target=Owner status=INFO -->\nFYI"),
        ])
        self.assertEqual([(n.reference, n.summary) for n in notices],
                         [("#202", "FYI"), ("#201", "Spec error in Task B")])
        self.assertEqual(notices[1].status, "NOTICE")

    def test_asks_sweeps_untrusted_and_owner_actor_are_not_notices(self):
        untrusted = comment(205, NOW, NOTICE + "\nfrom someone else")
        untrusted["user"] = {"login": "someone-else"}
        notices = mon.owner_notices([
            comment(203, NOW, ASK + "\nDecide"),
            comment(204, NOW, "<!-- activity: type=pm_progress actor=PM target=PM status=SWEEP -->"),
            untrusted,
            comment(206, NOW, "<!-- activity: type=pm_progress actor=Owner target=Owner status=NOTICE -->"),
            comment(207, NOW, "text first\n" + NOTICE),   # a marker counts only on leading lines
        ])
        self.assertEqual(notices, [])

    def test_office_view_shows_asks_and_no_information_notices(self):
        # Owner 2026-11-22: the screen lists only what the Owner must decide or
        # handle; information notices (D4 NOTICE, PM INFO) are never shown.
        ask = mon.owner_pending([comment(208, NOW, ASK + "\nPick a layout")], [])
        notices = mon.owner_notices([comment(209, NOW, NOTICE + "\nSpec error found")])
        view = mon.office_view([], pending=ask, notices=notices, now=NOW + 60,
                               titles={"#208": "Monitor layout choice"})
        lines = [line for line, _ in view]
        styles = dict(view)
        ask_line = next(line for line in lines if "#208" in line)
        self.assertTrue(ask_line.startswith("★ 等你決定：GitHub #208 Monitor layout choice（"))
        self.assertIn("Pick a layout", ask_line)
        self.assertEqual(styles[ask_line], "!")
        self.assertFalse(any("#209" in line or "Spec error found" in line for line in lines))
        self.assertFalse(any("通知" in line for line in lines))
        self.assertEqual(mon.office_view([], pending=ask, notices=notices, waiting=1, now=NOW)[0][0],
                         "辦公室 · 工作中 0 · 等你 1")

    def test_ask_line_drops_the_record_lead_in(self):
        ask = [mon.OwnerPending(NOW - 30, "#5", "WAITING",
                                "actor=PM (main PM d2d52271). 要不要關閉 #5？", "waiting_owner", False)]
        line = next(line for line, _ in mon.office_view([], pending=ask, now=NOW) if "#5" in line)
        self.assertTrue(line.endswith("— 要不要關閉 #5？"), line)
        self.assertNotIn("actor=", line)

    def test_ask_line_without_title_is_unchanged_in_shape(self):
        ask = [mon.OwnerPending(NOW - 30, "#6", "WAITING", "Pick one", "waiting_owner", False)]
        line = next(line for line, _ in mon.office_view([], pending=ask, now=NOW) if "#6" in line)
        self.assertTrue(line.startswith("★ 等你決定：GitHub #6（"), line)

    def test_ask_titles_use_pr_titles_fetch_issue_titles_once_and_cap(self):
        from types import SimpleNamespace
        calls = []

        def get(path):
            calls.append(path)
            if path.endswith("/issues/7"):
                raise OSError("down")
            return {"title": f"Issue {path.rsplit('/', 1)[1]} title"}
        reader = SimpleNamespace(ask_titles={}, repo="o/r", _get=get)
        pending = [mon.OwnerPending(NOW, f"#{n}", "WAITING", "x", "waiting_owner", False)
                   for n in (1, 2, 3, 4, 5, 6, 7)]
        pulls = [{"number": 3, "title": "PR three"}]
        titles = mon.GitHubSource._ask_titles(reader, pending, pulls)
        shown = [item.reference for item in pending[-mon.OWNER_SHOWN:]]
        self.assertEqual(sorted(titles), sorted(shown))
        self.assertEqual(titles["#3"], "PR three")          # from pulls, no fetch
        self.assertEqual(titles["#7"], "")                  # failure: title left out
        self.assertEqual(titles["#4"], "Issue 4 title")
        self.assertFalse(any(path.endswith("/issues/3") for path in calls))
        self.assertLessEqual(len(calls), mon.OWNER_SHOWN)
        reader.ask_titles = titles
        calls.clear()
        mon.GitHubSource._ask_titles(reader, pending, pulls)   # cached: only the failed one again
        self.assertEqual(calls, ["repos/o/r/issues/7"])

    def test_no_notices_adds_no_lines(self):
        self.assertEqual(mon.office_view([], now=NOW), mon.office_view([], notices=[], now=NOW))

    def test_notice_list_needs_a_good_trusted_read(self):
        from types import SimpleNamespace
        item = mon.OwnerNotice(NOW, "#1", "NOTICE", "x")
        self.assertEqual(mon.owner_notice_list(None), [])
        self.assertEqual(mon.owner_notice_list(SimpleNamespace(successful_read=False, notices=[item])), [])
        if not mon.GITHUB_IDENTITY_STATUS:
            self.assertEqual(mon.owner_notice_list(SimpleNamespace(successful_read=True, notices=[item])),
                             [item])


class TestSubPmLeases(unittest.TestCase):
    """#2311: HELD sub-PM leases are lanes the monitor shows."""

    def test_held_lease_is_read_and_release_ends_it(self):
        held = comment(301, NOW, "subpm-lease wp=WP-2 holder=claude-subpm-a pm=main status=HELD")
        leases = mon.subpm_leases([held])
        self.assertEqual([(l.issue, l.wp, l.holder) for l in leases], [("301", "WP-2", "claude-subpm-a")])
        released = comment(301, NOW + 60, "subpm-lease wp=WP-2 holder=claude-subpm-a status=RELEASED")
        self.assertEqual(mon.subpm_leases([held, released]), [])
        # An older HELD record after the release does not bring the lane back.
        self.assertEqual(mon.subpm_leases([released, held]), [])

    def test_lease_fields_on_separate_lines(self):
        body = "**subpm-lease**\n- wp: WP-7\n- holder: `codex-subpm-b`\n- paths: scripts/x.py\n- status: HELD"
        leases = mon.subpm_leases([comment(302, NOW, body)])
        self.assertEqual([(l.wp, l.holder) for l in leases], [("WP-7", "codex-subpm-b")])

    def test_earlier_held_lease_stays_until_a_record_settles_it(self):
        # Reviewer F5: a lease outside the comment window is not dropped.
        earlier = mon.subpm_leases([comment(306, NOW - 13 * 3600,
                                            "subpm-lease wp=WP-3 holder=claude-subpm-c status=HELD")])
        self.assertEqual(mon.subpm_leases([], earlier), earlier)
        other = comment(307, NOW, "subpm-lease wp=WP-4 holder=codex-subpm-d status=HELD")
        self.assertEqual([l.issue for l in mon.subpm_leases([other], earlier)], ["306", "307"])
        released = comment(306, NOW, "subpm-lease wp=WP-3 holder=claude-subpm-c status=RELEASED")
        self.assertEqual(mon.subpm_leases([released], earlier), [])
        # The claim comment itself edited to RELEASED settles it too.
        edited = comment(306, NOW - 13 * 3600,
                         "subpm-lease wp=WP-3 holder=claude-subpm-c status=RELEASED")
        self.assertEqual(mon.subpm_leases([edited], earlier), [])

    def test_untrusted_or_incomplete_leases_are_ignored(self):
        untrusted = comment(303, NOW, "subpm-lease wp=WP-1 holder=x status=HELD")
        untrusted["user"] = {"login": "someone-else"}
        self.assertEqual(mon.subpm_leases([
            untrusted,
            comment(304, NOW, "subpm-lease wp=WP-1 status=HELD"),   # no holder
            comment(305, NOW, "wp=WP-1 holder=x status=HELD"),      # not a lease comment
        ]), [])


if __name__ == "__main__":
    unittest.main()
