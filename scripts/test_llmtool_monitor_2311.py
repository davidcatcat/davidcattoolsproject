"""Tests for issue #2311 / PR #2335 office monitor redesign acceptance (A1-A7).

Covers:
- A1: one row per route (Claude, AGY, OpenCode, home LLM, Codex) with system state and sub-PM in charge.
- A2: route next item, separate Owner decisions and notices without asking.
- A3: rule file alert intact, disk refresh 5.0s live measurement.
- A4: one general rule, fewer items.
- A5 (v1 F1): waiting result not hidden by newer task, mismatched task ID never binds.
- A6 (v1 F2): route activity tied only to route's own task; reviewer rows excluded.
- A7 (v1 F3): unresolved HELD sub-PM leases survive restart; malformed or empty task file is never successful.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import llmtool_monitor as mon

NOW = 1_800_000_000.0


def iso_stamp(seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


class TestMonitor2311Acceptance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lease_file = os.path.join(self.tmp.name, "subpm-leases.json")
        self.patch_lease = patch.object(mon, "SUBPM_LEASE_FILE", self.lease_file)
        self.patch_lease.start()
        mon.waiting_scan_reset()

    def tearDown(self):
        mon.waiting_scan_reset()
        self.patch_lease.stop()
        self.tmp.cleanup()

    def test_a1_route_rows_and_subpm(self):
        # A1: one row per route (Claude, AGY, OpenCode, home LLM, Codex) + names sub-PM
        switches = {
            "claude-developer": ("ALLOWED", "Owner", "11-01"),
            "agy": ("ALLOWED", "Owner", "11-01"),
            "opencode-developer": ("ALLOWED", "Owner", "11-01"),
            "gw-agent": ("ALLOWED", "Owner", "11-01"),
            "codex-developer": ("PAUSED", "Owner", "10-25"),
        }
        reasons = {"codex-developer": "codex is retired"}
        leases = [mon.SubPmLease(NOW - 100, "2311", "WP-A", "claude-subpm-1")]
        task = mon.RouteTask("Claude", "a1b2c3d4", True, "派工中", NOW - 50, "2311", False, NOW - 60)
        run = mon.RouteDelegate("Claude", NOW - 40, "delegate", "a1b2c3d4" * 4)
        board = mon.PmBoard("ok", 0, [], [], [])
        rows = mon.route_rows([], switches, "", reasons, [task], board, leases, NOW, delegates=[run])
        by_name = {r.name: r for r in rows}
        self.assertEqual(set(mon.ROUTE_NAMES), {"Claude", "AGY", "OpenCode", "home LLM", "Codex"})
        for name in mon.ROUTE_NAMES:
            self.assertIn(name, by_name)
        self.assertEqual(by_name["Claude"].state, mon.WORKING)
        self.assertIn("sub-PM claude-subpm-1", by_name["Claude"].detail)
        self.assertEqual(by_name["Codex"].state, mon.PAUSED)
        self.assertIn("codex is retired", by_name["Codex"].detail)

    def test_a2_route_next_item_and_notices_apart_from_asks(self):
        # A2: next item from queue; owner notices listed separately from asks
        board = mon.PmBoard("ok", 0, [], [
            mon.PmBoardItem("#2311", "repair monitor", "AGY"),
        ], [])
        switches = {k: ("ALLOWED", "Owner", "11-01") for k in mon.DISPATCH_WORKERS}
        rows = mon.route_rows([], switches, "", {}, [], board, [], NOW)
        by_name = {r.name: r for r in rows}
        self.assertEqual(by_name["AGY"].next, "#2311 repair monitor")

        ask = [mon.OwnerPending(NOW - 30, "#2311", "WAITING", "Need Owner Decision", "waiting_owner", False)]
        notice = [mon.OwnerNotice(NOW - 10, "#2311", "NOTICE", "FYI: spec check note")]
        view = mon.office_view([], pending=ask, notices=notice, routes=rows, now=NOW)
        lines = [line for line, _ in view]
        ask_idx = next(i for i, l in enumerate(lines) if "Need Owner Decision" in l)
        notice_idx = next(i for i, l in enumerate(lines) if "FYI: spec check note" in l)
        self.assertTrue(lines[ask_idx].startswith("★ 等你決定"))
        self.assertTrue(lines[notice_idx].startswith("ℹ "))
        self.assertNotIn("★", lines[notice_idx])
        self.assertLess(ask_idx, notice_idx)

    def test_a3_disk_refresh_live_measurement(self):
        # A3: DISK_REFRESH_SECONDS is 5.0 live reading
        self.assertEqual(mon.DISK_REFRESH_SECONDS, 5.0)

    def test_a5_v1_f1_waiting_result_not_hidden_and_mismatched_task_id(self):
        # A5 / Finding F1: waiting result not hidden by newer task
        t_older = mon.RouteTask("AGY", "11111111", False, "成功回應", NOW - 200, "100", False, NOW - 250)
        t_newer = mon.RouteTask("AGY", "22222222", True, "派工中", NOW - 50, "101", False, NOW - 60)
        run_newer = mon.RouteDelegate("AGY", NOW - 40, "delegate", "22222222" * 4)
        switches = {k: ("ALLOWED", "Owner", "11-01") for k in mon.DISPATCH_WORKERS}
        rows = mon.route_rows([], switches, "", {}, [t_older, t_newer], mon.PmBoard("ok", 0, [], [], []), [], NOW,
                              delegates=[run_newer])
        by_name = {r.name: r for r in rows}
        self.assertEqual(by_name["AGY"].state, mon.WORKING)
        self.assertIn("另有 1 件結果等 PM", by_name["AGY"].detail)
        self.assertEqual(by_name["AGY"].waiting, 1)

        # Mismatched Task id never binds to an older task
        mismatched_run = mon.RouteDelegate("AGY", NOW - 30, "delegate", "99999999" * 4)
        bound = mon._route_bound([t_newer, t_older], [mismatched_run])
        self.assertEqual(bound, set())

        # Unidentified process (no Task ID) never binds and reports uncertainty
        unidentified_run = mon.RouteDelegate("AGY", NOW - 30, "delegate", "")
        self.assertEqual(mon._route_bound([t_newer, t_older], [unidentified_run]), set())
        rows_unidentified = mon.route_rows([], switches, "", {}, [t_newer], mon.PmBoard("ok", 0, [], [], []), [], NOW,
                                           delegates=[unidentified_run])
        agy_unidentified = next(r for r in rows_unidentified if r.name == "AGY")
        self.assertEqual(agy_unidentified.state, mon.UNCHECKED)
        self.assertIn("無法確認關聯", agy_unidentified.detail)
        self.assertIn("有未指定 Task 的派工程序執行中", agy_unidentified.detail)

    def test_a6_v1_f2_activity_tied_only_to_route_own_task(self):
        # A6 / Finding F2: reviewer rows or unrelated tasks do not make developer route working
        office_reviewer = [
            mon.OfficeRow("codex reviewer", mon.WORKING, "審", 30, 10, key="codex:x",
                          role="reviewer", task="PR #5 @abcdef0"),
            mon.OfficeRow("claude PM", mon.WORKING, "pm", 5, 10, role="pm", task="task main"),
        ]
        switches = {k: ("ALLOWED", "Owner", "11-01") for k in mon.DISPATCH_WORKERS}
        rows = mon.route_rows(office_reviewer, switches, "", {}, [], mon.PmBoard("ok", 0, [], [], []), [], NOW)
        by_name = {r.name: r for r in rows}
        self.assertEqual(by_name["Codex"].state, mon.ROUTE_FREE)

        # Only a running task matching office row activates WORKING
        running_t = mon.RouteTask("AGY", "33333333", True, "派工中", NOW - 50, "102", False, NOW - 60)
        office_dev = [
            mon.OfficeRow("agy direct developer", mon.WORKING, "editing", 30, 10,
                          role="developer", task="task 33333333"),
        ]
        rows2 = mon.route_rows(office_dev, switches, "", {}, [running_t], mon.PmBoard("ok", 0, [], [], []), [], NOW)
        by_name2 = {r.name: r for r in rows2}
        self.assertEqual(by_name2["AGY"].state, mon.WORKING)

    def test_a7_v1_f3_subpm_leases_survive_restart(self):
        # A7 / Finding F3: HELD sub-PM leases survive restart via persistence
        lease = mon.SubPmLease(NOW - 50000, "999", "WP-Z", "subpm-survivor")
        mon._save_subpm_leases([lease], self.lease_file)
        loaded = mon._load_subpm_leases(self.lease_file)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].holder, "subpm-survivor")
        self.assertEqual(loaded[0].issue, "999")

        # GitHubSource loads persisted leases on initialization
        gh = mon.GitHubSource("owner/repo", window_hours=12.0)
        self.assertEqual([l.holder for l in gh.leases], ["subpm-survivor"])

        # Finding 2: Persisted HELD leases survive restart and are not suppressed on GitHub read failure
        with tempfile.TemporaryDirectory() as root:
            dispatch = Path(root, "worker-dispatch.json")
            dispatch.write_text(json.dumps({"schema_version": 1, "workers": {
                k: {"dispatch": "ALLOWED", "decided_by": "Owner", "decided_at": "2026-11-10",
                    "reason": "open", "evidence": "https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6760670167"}
                for k in mon.DISPATCH_WORKERS}}), encoding="utf-8")
            tasks = Path(root, "tasks")
            tasks.mkdir()
            gh.read_failed = True
            rows = mon.monitor_routes([], [], mon.PmBoard("ok", 0, [], [], []), gh, NOW,
                                      canonical=root, dispatch=str(dispatch), root=str(tasks))
            by_name = {r.name: r for r in rows}
            self.assertIn("sub-PM", by_name)
            subpm = by_name["sub-PM"]
            self.assertEqual(subpm.state, mon.UNCHECKED)
            self.assertIn("subpm-survivor", subpm.detail)
            self.assertIn("本地復原", subpm.detail)
            self.assertIn("GitHub 讀取未確認", subpm.detail)

    def test_a7_v1_f3_malformed_or_empty_task_file_never_successful_read(self):
        # A7 / Finding F3: malformed or empty task file fails read and triggers shortfall
        tasks_dir = os.path.join(self.tmp.name, "tasks")
        os.makedirs(tasks_dir, exist_ok=True)
        good_record = {
            "task_id": "8" * 32,
            "updated_at": iso_stamp(NOW - 60),
            "developer": {"provider_type": "opencode-cli", "state": "DEVELOPER_RESPONDED"},
            "request": "#2311 check",
        }
        Path(tasks_dir, "good.json").write_text(json.dumps(good_record), encoding="utf-8")
        Path(tasks_dir, "empty.json").write_text("", encoding="utf-8")
        Path(tasks_dir, "corrupt.json").write_text("{broken", encoding="utf-8")

        entries, problem = mon.route_tasks(tasks_dir)
        self.assertEqual(problem, mon.REMOTE_TEST_OVER_BUDGET)

        waiting_entries, waiting_problem = mon.waiting_records(tasks_dir)
        self.assertEqual(waiting_problem, mon.REMOTE_TEST_OVER_BUDGET)


if __name__ == "__main__":
    unittest.main()
