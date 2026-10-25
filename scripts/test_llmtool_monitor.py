"""Offline tests for scripts/llmtool_monitor.py (no processes, models or network).

Run: python3 -B -m unittest discover -s scripts -p 'test_llmtool_monitor.py'
"""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

_PATH = Path(__file__).with_name("llmtool_monitor.py")
_SPEC = importlib.util.spec_from_file_location("llmtool_monitor_under_test", _PATH)
mon = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = mon
_SPEC.loader.exec_module(mon)

SECRETS = ("PROMPT-SECRET", "REASONING-SECRET", "TOOL-OUTPUT-SECRET", "MODEL-OUTPUT-SECRET",
           "RAW-ARGV-SECRET", "CREDENTIAL-SECRET", "token=abc", "/Users/davidsun")


_ISOLATED = tempfile.TemporaryDirectory()


def setUpModule():
    # Never let a test read the developer's real agent sessions: a live session
    # changing state mid-test would add office events to unrelated assertions.
    for name in ("CODEX_SESSIONS", "CODEX_HISTORY", "CLAUDE_PROJECTS", "CLAUDE_SESSIONS", "OPENCODE_DB", "KILO_DB", "LLMTOOL_TASKS"):
        setattr(mon, name, os.path.join(_ISOLATED.name, name))


def tearDownModule():
    _ISOLATED.cleanup()


def ps(*rows):
    return "\n".join(rows)


class ClassifyTests(unittest.TestCase):
    def test_roles(self):
        cases = {
            "/u/lib/node_modules/@opencode/cli/bin/opencode.exe serve --service": "OpenCode SERVER",
            "/u/bin/opencode.exe run --agent a": "OpenCode CLIENT",
            "(opencode.exe)": "OpenCode CLI",
            "node /u/.nvm/versions/node/v24/bin/kilo": "Kilo CLI (node launcher)",
            "/u/lib/node_modules/@kilocode/cli/bin/.kilo": "Kilo CLI",
            "/u/.kilo/bin/kilo run --agent a": "Kilo CLIENT",
            "claude": "Claude Code CLI",
            "/Applications/Claude.app/Contents/MacOS/Claude": "Claude Desktop",
            "/Applications/Antigravity.app/Contents/MacOS/Antigravity": "Antigravity App (AGY)",
            "/Users/x/.local/bin/agy": "AGY CLI",
            "/Users/x/git/actions-runner/bin/Runner.Listener run": "Actions runner",
            "/Users/x/git/actions-runner/bin/Runner.Worker spawnclient 10 13": "Actions runner job",
            "/Applications/Antigravity.app/Contents/Frameworks/Antigravity Helper.app/Contents/MacOS/Antigravity Helper": None,
            "/Applications/Claude.app/Contents/Frameworks/Claude Helper.app/Contents/MacOS/Claude Helper --type=gpu": None,
            "/u/bin/codex app-server --listen": "Codex APP-SERVER",
            "/u/bin/codex app-server daemon pid-update-loop": "Codex daemon",
            "node /u/bin/codex": "Codex CLI (node launcher)",
            "/x/.venv/bin/python -B /tmp/l/invoke_once.py red": "llmtool HOST",
            "/x/.venv/bin/python -I -X dev /tmp/l/live_once.py live": "llmtool HOST",
            "/x/python -m llmtool doctor": "llmtool CLI",
            "/usr/bin/grep opencode": None,
            "vim invoke_once.py": None,
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(mon.classify(command), expected)

    def test_host_job_is_evidence_dir_and_stage(self):
        self.assertEqual(mon._host_job("/x/python -B /private/tmp/llmtool-a/invoke_once.py red"),
                         (os.path.realpath("/private/tmp/llmtool-a"), "red"))
        self.assertIsNone(mon._host_job("/x/python -B /tmp/l/invoke_once.py 'red;rm'"))


class ProcessGroupTests(unittest.TestCase):
    def test_pgid_reuse_is_not_blamed_on_the_old_group(self):
        first = mon.parse_processes(ps(
            "  200     1   200    00:50 S    /u/bin/opencode.exe serve",
            "  201   200   200    00:49 S    node worker.js token=abc"), 999)
        self.assertEqual(first.watched_groups[200], {200, 201})
        # Leader reaped; 201 lingers; 202 is a NEW unrelated process that reused PGID 200.
        second = mon.parse_processes(ps(
            "  201     1   200    00:52 S    node worker.js",
            "  202     1   200    00:01 S    /usr/bin/unrelated"), 999, first.watched_groups)
        roles = {p.pid: p.role.strip() for p in second.processes}
        self.assertEqual(roles, {201: "orphaned group member"})
        self.assertEqual(second.lingering, [200])
        third = mon.parse_processes(ps("  202     1   200    00:02 S    /usr/bin/unrelated"),
                                    999, second.watched_groups)
        self.assertEqual(third.processes, [])
        self.assertEqual(third.lingering, [])

    def test_unrelated_leader_group_is_not_expanded(self):
        snap = mon.parse_processes(ps(
            "  486     1   486    20:00 S    /Applications/Other.app/x",
            " 4563   486   486    19:00 S    /u/bin/codex app-server",
            " 4600   486   486    19:00 S    /Applications/Other.app/helper"), 999)
        self.assertEqual([p.pid for p in snap.processes], [4563])
        self.assertEqual(snap.lingering, [])


class TaskEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.realpath(self.tmp.name)
        self.evidence = os.path.join(self.base, "llmtool-test")
        os.mkdir(self.evidence)
        with open(os.path.join(self.evidence, "manifest.json"), "w") as handle:
            json.dump({"task_id": "t1", "root": "/fake/root", "branch": "fix/x"}, handle)
        self.pattern = os.path.join(self.base, "llmtool-*")

    def make_db(self, state_root):
        os.makedirs(os.path.join(state_root, "data", "opencode"))
        db = sqlite3.connect(os.path.join(state_root, "data", "opencode", "opencode.db"))
        db.execute("create table session_v2(id, idle_outcome, time_created)")
        db.execute("create table session_message(id, session_id, type, seq, data)")
        db.execute("insert into session_v2 values('s', NULL, 1)")
        db.execute("insert into session_message values('u','s','user',1,?)",
                   (json.dumps({"text": "PROMPT-SECRET token=abc"}),))
        message = {"tokens": {"input": 100, "output": 5, "reasoning": 7, "cache": {"read": 50}},
                   "content": [{"type": "reasoning", "text": "REASONING-SECRET"},
                               {"type": "tool", "id": "t1", "name": "read",
                                "state": {"status": "completed",
                                          "input": {"path": "/fake/root/tools/a.py"},
                                          "content": [{"type": "text", "text": "TOOL-OUTPUT-SECRET"}]}}]}
        db.execute("insert into session_message values('a','s','assistant',2,?)", (json.dumps(message),))
        db.execute("insert into session_message values('b','s','assistant',3,'not json')")
        db.commit()
        db.close()

    def attempt(self, stage, state_root):
        with open(os.path.join(self.evidence, f"{stage}-attempt.json"), "w") as handle:
            json.dump({"model": "opencode/deepseek-v4.1-flash-free", "state_root": state_root}, handle)

    def scan(self, hosts=frozenset()):
        return mon.scan_tasks(self.pattern, 12.0, hosts)[0].stages[0]

    def test_missing_db_and_result_is_unknown_not_running(self):
        self.attempt("red", os.path.join(self.evidence, "opencode-" + "a" * 32))
        stage = self.scan()
        self.assertEqual((stage.meta, stage.steps), ("none", 0))
        self.assertEqual(mon.stage_state(stage), "UNKNOWN (no result, no live host)")

    def test_running_requires_the_stage_host_to_be_alive(self):
        root = os.path.join(self.evidence, "opencode-" + "b" * 32)
        self.make_db(root)
        self.attempt("red", root)
        self.assertEqual(mon.stage_state(self.scan({(self.evidence, "other")})),
                         "UNKNOWN (no result, no live host)")
        live = self.scan({(self.evidence, "red")})
        self.assertEqual(mon.stage_state(live), "RUNNING")
        self.assertEqual((live.steps, live.tokens_in, live.tokens_out), (1, 150, 12))
        self.assertEqual([(t.name, t.path, t.status) for t in live.tools],
                         [("read", "tools/a.py", "completed")])
        self.assertFalse(any(secret in repr(live) for secret in SECRETS))

    def test_result_file_wins_and_stale_attempt_is_not_running(self):
        self.attempt("red", "")
        with open(os.path.join(self.evidence, "red-result.json"), "w") as handle:
            json.dump({"code": "OPENCODE_OUTPUT_RETURNED", "cleanup_confirmed": True}, handle)
        self.attempt("fix", "")
        stages = {s.name: s for s in mon.scan_tasks(self.pattern, 12.0)[0].stages}
        self.assertEqual(mon.stage_state(stages["red"]), "OPENCODE_OUTPUT_RETURNED")
        self.assertEqual(stages["red"].cleanup, "ok")
        self.assertEqual(mon.stage_state(stages["fix"]), "UNKNOWN (no result, no live host)")

    def test_attempt_cannot_redirect_to_an_unrelated_database(self):
        outside = os.path.join(self.base, "owner-opencode")  # not under the evidence dir
        self.make_db(outside)
        self.attempt("red", outside)
        stage = self.scan()
        self.assertTrue(stage.meta.startswith("UNKNOWN"))
        self.assertEqual((stage.steps, stage.tools), (0, ()))

    def test_symlinked_state_root_is_rejected(self):
        outside = os.path.join(self.base, "elsewhere")
        self.make_db(outside)
        link = os.path.join(self.evidence, "opencode-" + "c" * 32)
        os.symlink(outside, link)
        self.attempt("red", link)
        stage = self.scan()
        self.assertTrue(stage.meta.startswith("UNKNOWN"))
        self.assertEqual(stage.tools, ())

    def test_rendered_lines_never_contain_secrets(self):
        root = os.path.join(self.evidence, "opencode-" + "d" * 32)
        self.make_db(root)
        self.attempt("red", root)
        tasks = mon.scan_tasks(self.pattern, 12.0, frozenset({(self.evidence, "red")}))
        text = "\n".join(mon.task_lines(tasks))
        self.assertIn("RUNNING", text)
        self.assertFalse(any(secret in text for secret in SECRETS))

    def test_old_task_in_a_recently_touched_directory_is_not_recent(self):
        # The daily /tmp cleaner deleting files bumps the directory's mtime.
        self.attempt("red", "")
        old = time.time() - 4 * 86400
        for name in os.listdir(self.evidence):
            os.utime(os.path.join(self.evidence, name), (old, old))
        os.utime(self.evidence, None)
        self.assertEqual(mon.scan_tasks(self.pattern, 12.0), [])

    def test_returned_stage_whose_edits_all_failed_says_so(self):
        tools = (mon.Tool("1", "read", "a.py", "completed"), mon.Tool("2", "edit", "a.py", "error"),
                 mon.Tool("3", "edit", "?", "error"))
        stage = mon.Stage("fix", "deepseek", 1000.0, "OPENCODE_OUTPUT_RETURNED", "ok", 3, 1, 1,
                          "", tools, result_at=1010.0)
        task = mon.Task("/tmp/llmtool-x", "t1", "b", (stage,))
        self.assertIn("OPENCODE_OUTPUT_RETURNED（改檔 2 次全部失敗）", "\n".join(mon.task_lines([task])))
        self.assertIn("有輸出，但改檔全部失敗", "\n".join(mon.activity_lines([task])))
        partly = mon.Stage("fix", "deepseek", 1000.0, "OPENCODE_OUTPUT_RETURNED", "ok", 3, 1, 1,
                           "", tools + (mon.Tool("4", "write", "b.py", "completed"),), result_at=1010.0)
        lines = "\n".join(mon.task_lines([mon.Task("/tmp/llmtool-x", "t1", "b", (partly,))]))
        self.assertIn("（改檔 2/3 次失敗）", lines)

    def test_absolute_tool_path_outside_evidence_root_is_not_rendered(self):
        self.assertEqual(mon._relative("/Users/davidsun/private/secret.py", self.base), "?")


    def test_hardlink_db_rejected(self):
        outside_root = os.path.join(self.base, "opencode-" + "a" * 32)
        self.make_db(outside_root)
        valid_root = os.path.join(self.evidence, "opencode-" + "b" * 32)
        os.makedirs(os.path.join(valid_root, "data", "opencode"))
        os.link(os.path.join(outside_root, "data", "opencode", "opencode.db"),
                os.path.join(valid_root, "data", "opencode", "opencode.db"))
        self.attempt("red", valid_root)
        with patch.object(mon.sqlite3, "connect", side_effect=AssertionError("connect called")):
            stage = self.scan()
        self.assertEqual(stage.meta, "UNKNOWN (unverified task database)")
        self.assertEqual(stage.steps, 0)
        self.assertEqual(stage.tools, ())

    def test_singlelink_db_accepted(self):
        valid_root = os.path.join(self.evidence, "opencode-" + "c" * 32)
        self.make_db(valid_root)
        self.attempt("red", valid_root)
        with patch.object(mon.sqlite3, "connect", wraps=sqlite3.connect) as spy:
            stage = self.scan()
        self.assertEqual(stage.meta, "ok")
        self.assertEqual(stage.steps, 1)
        self.assertEqual(stage.tokens_in, 150)
        self.assertEqual(stage.tokens_out, 12)
        self.assertEqual(spy.call_count, 1)
        self.assertIn("mode=ro", spy.call_args[0][0])


class TaskRecordTests(unittest.TestCase):
    def test_recent_records_show_route_and_result_in_words_without_content(self):
        with tempfile.TemporaryDirectory() as root:
            now = time.time()
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 600))
            records = {
                "a": {"task_id": "ab12cd34" * 4, "updated_at": stamp, "state": "PM_RESPONDED",
                      "request": "REQUEST-SECRET", "pm_assignment": {"route": "company-gw"},
                      "developer": {"provider_profile": "company-gw", "state": "DEVELOPER_OUTPUT_REJECTED",
                                    "error_code": "DEVELOPER_OUTPUT_INVALID_ENVELOPE",
                                    "artifact": "DIFF-SECRET"},
                      "completion": {"state": "WAITING_EXECUTOR", "executor_task_id": "issue-9-dev"}},
                "b": {"task_id": "ef56" * 8, "updated_at": stamp, "state": "PM_RESPONDED"},
                "old": {"task_id": "00" * 16, "updated_at": "2026-10-01T00:00:00Z",
                        "state": "PM_RESPONDED"},
            }
            for name, record in records.items():
                with open(os.path.join(root, f"{name}.json"), "w") as handle:
                    json.dump(record, handle)
            lines = [line for _, line in mon.llmtool_task_records(now, root)]
        self.assertEqual(len(lines), 2)
        text = "\n".join(lines)
        self.assertIn("issue-9-dev", text)
        self.assertIn("派給 company-gw", text)
        self.assertIn("輸出被拒（回應格式錯誤）；等執行者接手", text)
        self.assertIn("ef56ef56", text)
        self.assertIn("PM 已規劃，還沒派給 developer", text)
        self.assertNotIn("派給 None", text)
        self.assertNotIn("SECRET", text)


class TaskEventTests(unittest.TestCase):
    def test_tool_calls_are_reported_once_with_final_status_and_path(self):
        def task(*tools):
            stage = mon.Stage("red", "deepseek", 1.0, "", "", 1, 0, 0, "", tuple(tools), True, "ok")
            return mon.Task("/d", "t", "b", (stage,))
        streaming = task(mon.Tool("1", "edit", "?", "streaming"))
        running = task(mon.Tool("1", "edit", "tests/x.py", "running"))
        done = task(mon.Tool("1", "edit", "tests/x.py", "completed"), mon.Tool("2", "read", "a.py", "error"))
        self.assertEqual(mon.task_events({"/d": task()}, [streaming]), [])
        self.assertEqual(mon.task_events({"/d": streaming}, [running]), [])
        self.assertEqual(mon.task_events({"/d": running}, [done]),
                         [("→", "t red: edit tests/x.py"), ("→", "t red: read a.py [error]")])
        self.assertEqual(mon.task_events({"/d": done}, [done]), [])


    def test_initial_true_suppresses_existing_stages(self):
        stage = mon.Stage("red", "deepseek", 1.0, "", "", 1, 0, 0, "", (), True, "ok")
        task = mon.Task("/d", "t", "b", (stage,))
        self.assertEqual(mon.task_events({}, [task], initial=True), [])

    def test_noninitial_firstseen_completed_stage_emits_events(self):
        stage = mon.Stage("red", "deepseek", 1.0, "RETURNED", "ok", 1, 0, 0, "", (), True, "ok")
        task = mon.Task("/d", "t", "b", (stage,))
        events = mon.task_events({}, [task], initial=False)
        self.assertIn(("▶", "t: stage red started (deepseek)"), events)
        self.assertIn(("■", "t: stage red finished: RETURNED, cleanup ok, 1 steps, 0.0k in / 0.0k out"), events)

    def test_repeated_same_snapshot_does_not_duplicate(self):
        stage = mon.Stage("red", "deepseek", 1.0, "", "", 1, 0, 0, "", (), True, "ok")
        task = mon.Task("/d", "t", "b", (stage,))
        self.assertEqual(mon.task_events({"/d": task}, [task], initial=False), [])


class MonitorTaskBaselineTests(unittest.TestCase):
    """Only a successful task scan may establish the baseline; history is never replayed."""

    TASK_KINDS = ("▶", "→", "■", "!")

    def setUp(self):
        self.monitor = mon.Monitor()
        self.snapshot = mon.Snapshot([], {}, [], frozenset())

    def poll(self, tasks):
        with patch.object(mon, "collect", return_value=self.snapshot):
            if isinstance(tasks, Exception):
                with patch.object(mon, "scan_tasks", side_effect=tasks):
                    return self.monitor.poll()
            with patch.object(mon, "scan_tasks", return_value=tasks):
                return self.monitor.poll()

    def poll_without_process_table(self):
        with patch.object(mon, "collect", side_effect=RuntimeError("ps unavailable")):
            return self.monitor.poll()

    def test_office_is_never_built_from_an_unread_process_table(self):
        # office_rows treats an empty list as "nothing running"; that is only
        # true because a failed ps read never reaches it.
        with patch.object(mon, "office_rows") as office:
            self.poll_without_process_table()
        office.assert_not_called()
        self.assertIn("ps unavailable", self.monitor.error)

    def test_office_failure_is_reported_and_no_stale_rows_are_kept(self):
        self.monitor.office = [mon.OfficeRow("codex 主對話", mon.WORKING, "old", 1.0, 0)]
        with patch.object(mon, "office_rows", side_effect=TypeError("boom")):
            new = self.poll([])
        self.assertEqual(self.monitor.office, [])
        self.assertIn(("?", "office view unavailable: TypeError"),
                      [(kind, text) for _, kind, text in new])
        self.assertEqual(self.monitor.office_state, {})   # recovery starts from a fresh baseline
        lines = mon.office_lines(self.monitor.office, self.monitor.office_error)
        self.assertTrue(lines[1].startswith("⚠ 辦公室資料讀取失敗（TypeError）"))
        self.monitor.office_due = 0.0
        with patch.object(mon, "office_rows", return_value=[]):
            self.poll([])
        self.assertIsNone(self.monitor.office_error)

    def test_office_is_rebuilt_at_most_every_office_every_seconds(self):
        with patch.object(mon, "office_rows", return_value=[]) as rows:
            self.poll([])
            self.poll([])
            self.assertEqual(rows.call_count, 1)   # the second tick reuses the office
            self.monitor.office_due = 0.0
            self.poll([])
            self.assertEqual(rows.call_count, 2)

    def task_kinds(self, new):
        return [kind for _, kind, _ in new if kind in self.TASK_KINDS]

    @staticmethod
    def stage(name, result="", tools=(), live=False):
        return mon.Stage(name, "deepseek", 1.0, result, "ok" if result else "",
                         1, 0, 0, "", tuple(tools), live, "ok")

    @staticmethod
    def task(directory, *stages):
        return mon.Task(directory, directory.strip("/"), "main", tuple(stages))

    def test_first_successful_scan_baselines_history_and_still_reports_later_stages(self):
        historical = [self.task("/d", self.stage(
            "red", "RETURNED", (mon.Tool("1", "read", "a.py", "completed"),)))]
        first = self.poll(historical)
        self.assertEqual([kind for _, kind, _ in first], ["i", "i"])
        self.assertIn("monitor started;", first[0][2])
        self.assertIn("1 recent llmtool task(s); latest d: red RETURNED", first[1][2])
        self.assertEqual(self.task_kinds(first), [])
        self.assertEqual(self.task_kinds(self.poll(historical)), [])
        self.assertEqual(self.monitor.task_map, {"/d": historical[0]})
        extended = [self.task("/d", self.stage(
            "red", "RETURNED", (mon.Tool("1", "read", "a.py", "completed"),)),
            self.stage("fix", live=True))]
        self.assertEqual([(kind, text) for _, kind, text in self.poll(extended)],
                         [("▶", "d: stage fix started (deepseek)")])

    def test_new_task_emits_start_tool_and_finish_once_in_order(self):
        self.poll([])
        task = self.task("/new", self.stage(
            "red", "RETURNED", (mon.Tool("1", "read", "a.py", "completed"),
                                mon.Tool("2", "edit", "src/b.py", "error"))))
        first = self.poll([task])
        self.assertEqual([kind for _, kind, _ in first], ["▶", "→", "→", "■"])
        self.assertEqual([text for _, _, text in first], [
            "new: stage red started (deepseek)",
            "new red: read a.py",
            "new red: edit src/b.py [error]",
            "new: stage red finished: RETURNED, cleanup ok, 1 steps, 0.0k in / 0.0k out",
        ])
        self.assertEqual(self.poll([task]), [])
        self.assertEqual(self.monitor.task_map, {"/new": task})

    def test_task_scan_failure_after_baseline_neither_replays_nor_rebaselines(self):
        historical = [self.task("/d", self.stage("red", "RETURNED"))]
        self.poll(historical)
        self.assertEqual(self.poll(RuntimeError("evidence unavailable")), [])
        self.assertEqual(self.task_kinds(self.poll(historical)), [])
        added = [self.task("/d", self.stage("red", "RETURNED"),
                           self.stage("fix", live=True))]
        self.assertEqual([(kind, text) for _, kind, text in self.poll(added)],
                         [("▶", "d: stage fix started (deepseek)")])

    def test_unreadable_process_table_never_establishes_a_task_baseline(self):
        self.assertEqual([kind for _, kind, _ in self.poll_without_process_table()], ["?"])
        historical = [self.task("/d", self.stage("red", "RETURNED"))]
        self.assertEqual(self.task_kinds(self.poll(historical)), [])
        self.assertEqual(self.poll(historical), [])
        added = [self.task("/d", self.stage("red", "RETURNED"),
                           self.stage("fix", live=True))]
        self.assertEqual([(kind, text) for _, kind, text in self.poll(added)],
                         [("▶", "d: stage fix started (deepseek)")])

    def test_failed_first_scan_keeps_start_summary_and_next_scan_is_baseline(self):
        first = self.poll(RuntimeError("evidence unavailable"))
        self.assertEqual([kind for _, kind, _ in first], ["i"])
        self.assertIn("monitor started;", first[0][2])
        historical = [self.task("/d", self.stage(
            "red", "RETURNED", (mon.Tool("1", "read", "a.py", "completed"),)))]
        self.assertEqual(self.poll(historical), [])
        self.assertEqual(self.poll(historical), [])
        self.assertEqual(self.monitor.task_map, {"/d": historical[0]})

    def test_after_failed_first_scan_new_stage_and_tool_emit_once(self):
        self.poll(RuntimeError("evidence unavailable"))
        running = self.task("/d", self.stage("red", live=True))
        self.assertEqual(self.poll([running]), [])
        finished = self.task("/d", self.stage(
            "red", "RETURNED", (mon.Tool("1", "edit", "src/a.py", "completed"),)))
        self.assertEqual([(kind, text) for _, kind, text in self.poll([finished])], [
            ("→", "d red: edit src/a.py"),
            ("■", "d: stage red finished: RETURNED, cleanup ok, 1 steps, 0.0k in / 0.0k out"),
        ])
        self.assertEqual(self.poll([finished]), [])

    def test_after_failed_first_scan_new_task_emits_once(self):
        self.poll(RuntimeError("evidence unavailable"))
        self.assertEqual(self.poll([self.task("/d", self.stage("red", live=True))]), [])
        added = [self.task("/d", self.stage("red", live=True)),
                 self.task("/new", self.stage("blue", live=True))]
        self.assertEqual([(kind, text) for _, kind, text in self.poll(added)],
                         [("▶", "new: stage blue started (deepseek)")])
        self.assertEqual(self.poll(added), [])

    def test_repeated_failures_still_adopt_the_next_successful_scan(self):
        self.poll(RuntimeError("evidence unavailable"))
        self.poll(RuntimeError("evidence unavailable"))
        historical = [self.task("/d", self.stage("red", live=True))]
        self.assertEqual(self.poll(historical), [])
        added = [self.task("/d", self.stage("red", live=True),
                           self.stage("fix", live=True))]
        self.assertEqual([(kind, text) for _, kind, text in self.poll(added)],
                         [("▶", "d: stage fix started (deepseek)")])
        self.assertEqual(self.poll(added), [])


class ActivityViewTests(unittest.TestCase):
    def test_five_issue_event_classes_render_in_time_order(self):
        events = [
            mon.ActivityEvent(5, "worker_result", "task-1", "stage-1",
                              "OPENCODE_PROVIDER_FAILED", "task-1/stage-1"),
            mon.ActivityEvent(1, "review_dispatch", "", "", "DISPATCHED", "#1",
                              actor="PM", target="Reviewer"),
            mon.ActivityEvent(2, "review_verdict", "", "", "FAIL", "#2",
                              actor="Reviewer", target="PM"),
            mon.ActivityEvent(3, "undelivered_reply", "", "", "NOT_DELIVERED", "#3",
                              actor="Reviewer", target="PM"),
            mon.ActivityEvent(3.5, "undelivered_reply", "", "", "DELIVERED", "#3",
                              actor="Reviewer", target="PM"),
            mon.ActivityEvent(4, "waiting_owner", "", "", "BLOCKED", "#4",
                              actor="Integrator", target="Owner"),
        ]
        lines = mon.render_activity(events)
        expected = ("PM → Reviewer", "Reviewer → PM", "回覆沒送到",
                    "Integrator → Owner", "llmtool → 任務 task-1")
        positions = [next(i for i, line in enumerate(lines) if item in line) for item in expected]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("審查未通過", lines[positions[1]])
        self.assertIn("卡住，等你決定", lines[positions[3]])
        self.assertIn("失敗：模型呼叫失敗", lines[positions[4]])
        self.assertIn("GitHub 動態已關閉", lines[-1])
        self.assertFalse(any("已送達" in line for line in lines))

    def test_production_events_come_only_from_local_stage_evidence(self):
        stage = mon.Stage("red", "deepseek", 10.0, "OPENCODE_PROVIDER_FAILED", "UNKNOWN",
                          1, 0, 0, "", (mon.Tool("t", "read", "src/a.py", "completed"),),
                          False, "ok", 12.0)
        task = mon.Task("/private/tmp/evidence", "task-1", "feature-x", (stage,))
        events = mon.activity_events([task])
        self.assertEqual({event.event_type for event in events}, {"stage_attempt", "worker_result"})
        self.assertEqual([event.occurred_at for event in events], [10.0, 12.0])

    def test_activity_render_redacts_untrusted_absolute_home_paths(self):
        event = mon.ActivityEvent(1, "worker_result", "/Users/davidsun", "red",
                                  "OPENCODE_PROVIDER_FAILED", "/Users/davidsun/private.txt")
        text = "\n".join(mon.render_activity([event]))
        self.assertNotIn("/Users/davidsun", text)
        self.assertNotIn("private.txt", text)
        self.assertIn("失敗", text)

    def test_once_includes_activity_and_makes_no_network_calls(self):
        stage = mon.Stage("red", "deepseek", 10.0, "OPENCODE_PROVIDER_FAILED", "UNKNOWN",
                          1, 0, 0, "", (mon.Tool("t", "read", "/Users/davidsun/secret.py", "completed"),),
                          False, "ok", 12.0)
        task = mon.Task("/private/tmp/fixture", "task-1", "feature-x", (stage,))
        snapshot = mon.Snapshot([], {}, [], frozenset())
        output = __import__("io").StringIO()
        def no_network(*args, **kwargs):
            raise AssertionError("--once must not use network")
        with patch.object(mon, "collect", return_value=snapshot), \
             patch.object(mon, "scan_tasks", return_value=[task]), \
             patch.object(mon, "RUNNER_ROOT", "/nonexistent"), \
             patch("socket.socket.connect", side_effect=no_network), \
             patch("urllib.request.urlopen", side_effect=no_network), \
             patch("sys.stdout", output):
            self.assertEqual(mon.run_once(), 0)
        rendered = output.getvalue()
        self.assertIn("\n最近動態\n", rendered)
        self.assertIn("llmtool → 任務 task-1", rendered)
        self.assertIn("失敗：模型呼叫失敗", rendered)
        self.assertNotIn("/Users/davidsun", rendered)
        self.assertNotIn("secret.py", rendered)
        self.assertFalse(any(secret in rendered for secret in SECRETS))


class FakeRun:
    """Stands in for subprocess.run inside GitHubSource; records every argv."""

    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        code, payload = self.responses.pop(0)
        out = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return __import__("types").SimpleNamespace(returncode=code, stdout=out)


NOW = 1_800_000_000.0


def iso(seconds):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def comment(number, seconds, body, kind="issues", association="OWNER"):
    return {"created_at": iso(seconds), "body": body, "author_association": association,
            "issue_url": f"https://api.github.com/repos/o/r/{kind}/{number}"}


class GitHubActivityTests(unittest.TestCase):
    def test_marked_comment_becomes_typed_undelivered_reply(self):
        body = ("<!-- activity: type=undelivered_reply actor=Claude target=PM status=NOT_DELIVERED -->\n"
                "**Claude Reviewer → PM**: verdicts below")
        events = mon.github_events([comment(120, NOW - 60, body)], [], NOW - 3600)
        self.assertEqual([(e.event_type, e.status, e.actor, e.target, e.reference) for e in events],
                         [("undelivered_reply", "NOT_DELIVERED", "Claude", "PM", "#120")])
        line = mon.render_activity(events)[0]
        self.assertIn("Claude → PM：回覆沒送到（改看 GitHub）（#120・自述）", line)
        self.assertIn("Claude Reviewer → PM", line)

    def test_one_comment_may_carry_several_markers_in_a_stable_order(self):
        body = ("<!-- activity: type=undelivered_reply actor=Claude target=PM status=NOT_DELIVERED -->\n"
                "<!-- activity: type=review_verdict actor=Claude target=PM status=FIX -->\n"
                "<!-- activity: type=bogus status=PASS -->\n## Verdict")
        events = mon.github_events([comment(223, NOW - 9, body)], [], NOW - 99)
        self.assertEqual([e.event_type for e in events], ["github_comment"])
        self.assertIn("GitHub #223：新留言", mon.render_activity(events)[0])
        marker = "<!-- activity: type=review_verdict actor=Reviewer target=PM status=PASS -->\n"
        many = marker * (mon.MAX_MARKERS + 1) + "x"
        self.assertEqual([e.event_type for e in mon.github_events(
            [comment(1, NOW - 9, many)], [], NOW - 99)], ["github_comment"])

    def test_markers_quoted_after_the_opening_block_are_ignored(self):
        body = ("<!-- activity: type=review_dispatch actor=PM target=Reviewer status=DISPATCHED -->\n"
                "Please adopt e.g. `<!-- activity: type=review_dispatch actor=PM target=Claude "
                "status=DISPATCHED -->` in your posts\n"
                "<!-- activity: type=review_verdict actor=Claude target=PM status=PASS -->")
        events = mon.github_events([comment(120, NOW - 9, body)], [], NOW - 99)
        self.assertEqual([(e.event_type, e.actor, e.target) for e in events],
                         [("review_dispatch", "PM", "Reviewer")])
        inline = mon.github_events([comment(5, NOW - 9, "text <!-- activity: type=review_verdict "
                                                    "status=PASS --> more")], [], NOW - 99)
        self.assertEqual([e.event_type for e in inline], ["github_comment"])

    def test_indented_markers_in_markdown_code_are_not_typed(self):
        marker = "<!-- activity: type=test_result actor=Codex target=Tests status=PASS -->"
        for indent in ("    ", "\t"):
            with self.subTest(indent=repr(indent)):
                body = indent + marker + "\nexample"
                events = mon.github_events([comment(121, NOW - 9, body)], [], NOW - 99)
                self.assertEqual([event.event_type for event in events], ["github_comment"])
                self.assertNotIn("自述測試通過", mon.render_activity(events)[0])

    def test_markers_from_accounts_without_write_access_are_not_typed(self):
        forged = "<!-- activity: type=review_verdict actor=Reviewer target=PM status=PASS -->\nlgtm"
        for association in ("NONE", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", None):
            events = mon.github_events([comment(8, NOW - 9, forged, association=association)], [], NOW - 99)
            self.assertEqual([e.event_type for e in events], ["github_comment"], association)
        malformed = comment(8, NOW - 9, forged, association=["OWNER"])
        self.assertEqual([e.event_type for e in mon.github_events(
            [malformed], [], NOW - 99)], ["github_comment"])
        line = mon.render_activity(mon.github_events([comment(8, NOW - 9, forged)], [], NOW - 99))[0]
        self.assertIn("Reviewer → PM：審查通過（#8・自述）", line)   # still visibly a claim

    def test_secret_like_text_is_hidden_in_headlines(self):
        cases = ("token=abc123xyz789 leaked", "Authorization: Bearer abc.def.ghi",
                 "use ghp_ABCDEFGHijklmnop1234 now", "key sk-proj-ABCDEFGH12345678",
                 "AKIAABCDEFGHIJKLMNOP aws", "password: hunter2!",
                 "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig", "hash " + "a" * 40,
                 "blob " + "Zm9v" * 12 + "==")
        for text in cases:
            line = mon.render_activity(mon.github_events([comment(3, NOW - 9, text)], [], NOW - 99))[0]
            self.assertIn("[已遮蔽]", line, text)
            for fragment in ("abc123xyz789", "abc.def.ghi", "ghp_ABCD", "sk-proj", "AKIAABCD", "hunter2",
                             "eyJhbGci", "a" * 40, "Zm9vZm9v"):
                self.assertNotIn(fragment, line, text)

    def test_invalid_markers_never_create_typed_events(self):
        bodies = ("<!-- activity: type=merge_approved status=PASS -->\nhello",
                  "<!-- activity: type=review_verdict status=SHIPPED -->\nhello",
                  "<!-- activity: type=review_verdict actor=Reviewer target=PM status=PASS status=FAIL -->\nhello",
                  "<!-- activity: type=review_verdict actor=Reviewer target=PM status=PASS extra=value -->\nhello",
                  "<!-- activity: type=review_verdict actor=Reviewer target=PM status=PASS -->\n"
                  "<!-- activity: type=review_verdict actor=Reviewer target=PM status=PASS",
                  "<!-- activity: type=review_verdict actor=Reviewer target=Owner status=PASS -->\nhello",
                  "<!-- activity: type=review_verdict actor=Mallory target=PM status=PASS -->\nhello",
                  "<!-- activity: type=developer_progress actor=Codex target=Root status=ACTIVE -->\nhello",
                  "<!-- activity: type=test_result actor=Codex target=Tests status=ACCEPTED -->\nhello",
                  "unmarked text that says ACCEPT and NOT_DELIVERED")
        events = mon.github_events([comment(1, NOW - 10, b) for b in bodies], [], NOW - 3600)
        self.assertEqual({e.event_type for e in events}, {"github_comment"})
        spoofed = mon.github_events([comment(2, NOW - 10, "<!-- activity: type=review_verdict "
                                                    "actor=Mallory target=root status=PASS -->\nx")],
                                    [], NOW - 3600)
        self.assertEqual((spoofed[0].actor, spoofed[0].target), ("", ""))
        self.assertIn("GitHub #2：新留言", mon.render_activity(spoofed)[0])

    def test_headline_is_first_line_only_and_redacted(self):
        body = ("## Verdict for `/Users/davidsun/secret/repo.py` see https://example.com/x \x07\n"
                "PROMPT-SECRET on the second line")
        line = mon.render_activity(mon.github_events([comment(9, NOW - 5, body)], [], NOW - 99))[0]
        self.assertIn("GitHub #9：新留言 — Verdict for [路徑] see [連結]", line)
        for leaked in ("/Users/davidsun", "PROMPT-SECRET", "example.com", "\x07", "#"[:0] + "##"):
            self.assertNotIn(leaked, line)

    def test_headline_redacts_absolute_paths_from_any_root(self):
        for path in ("/Volumes/PrivateCustomer/source.py", "/etc/company.key"):
            with self.subTest(path=path):
                line = mon.render_activity(mon.github_events(
                    [comment(19, NOW - 5, "See " + path)], [], NOW - 99))[0]
                self.assertIn("[路徑]", line)
                self.assertNotIn(path, line)

    def test_pull_requests_opened_merged_closed_inside_window_only(self):
        pulls = [{"number": 7, "title": "feat: x", "created_at": iso(NOW - 50), "merged_at": iso(NOW - 10),
                  "closed_at": iso(NOW - 10)},
                 {"number": 8, "title": "old", "created_at": iso(NOW - 9999), "merged_at": None,
                  "closed_at": iso(NOW - 20)},
                 {"number": "9", "title": "bad"}]
        events = mon.github_events([], pulls, NOW - 3600)
        self.assertEqual(sorted((e.reference, e.event_type) for e in events),
                         [("#7", "pr_merged"), ("#7", "pr_opened"), ("#8", "pr_closed")])

    def test_ci_runs_are_read_only_while_a_runner_is_here_and_never_break_the_source(self):
        runs = {"workflow_runs": [{"status": "in_progress", "name": "llmtool Mac CI",
                                   "head_sha": "a" * 40, "pull_requests": [{"number": 7}]}]}
        fake = FakeRun((0, []), (0, []), (0, runs))
        source = mon.GitHubSource("o/r", runner=fake, clock=lambda: NOW)
        source.want_ci = True
        source.refresh()
        self.assertEqual([run[0] for run in source.ci_runs], ["in_progress"])
        self.assertEqual(fake.calls[-1][0][:4], [fake.calls[-1][0][0], "api", "-X", "GET"])
        self.assertIn("actions/runs", fake.calls[-1][0][4])
        broken = mon.GitHubSource("o/r", runner=FakeRun((0, []), (0, []), (1, b"")),
                                  clock=lambda: NOW)
        broken.want_ci = True
        broken.refresh()
        self.assertIsNone(broken.ci_runs)
        self.assertTrue(broken.status.startswith("GitHub o/r："))   # the source itself is fine

    def test_source_uses_read_only_gets_rate_limits_and_reports_failure(self):
        run = FakeRun((0, [comment(3, NOW - 30, "hi")]), (0, []), (1, b""))
        clock = lambda: NOW
        with patch.object(mon.shutil, "which", return_value="/opt/gh"):
            source = mon.GitHubSource("o/r", runner=run, clock=clock)
            source.refresh()
            source.refresh()                      # within the refresh window: no new call
            self.assertEqual(len(run.calls), 2)
            for argv, kwargs in run.calls:
                self.assertEqual(argv[:4], ["/opt/gh", "api", "-X", "GET"])
                self.assertTrue(argv[4].startswith("repos/o/r/"))
                self.assertEqual(kwargs["timeout"], mon.GITHUB_TIMEOUT)
            self.assertIn("GitHub o/r：最近 12 小時", source.status)
            self.assertEqual([e.reference for e in source.events], ["#3"])
            source.refresh(force=True)            # gh fails: keep old events, say so
        self.assertIn("讀取失敗", source.status)
        self.assertEqual([e.reference for e in source.events], ["#3"])
        self.assertIn("讀取失敗", mon.render_activity([], github=source)[-1])

    def test_source_reads_every_comment_page_up_to_a_limit(self):
        page = lambda start, size: [comment(start + i, NOW - 30, "c") for i in range(size)]
        run = FakeRun((0, page(100, 100)), (0, page(300, 24)), (0, []))
        with patch.object(mon.shutil, "which", return_value="/opt/gh"):
            source = mon.GitHubSource("o/r", runner=run, clock=lambda: NOW)
            source.refresh()
        self.assertEqual([argv[4].rsplit("&page=", 1)[-1] for argv, _ in run.calls[:2]], ["1", "2"])
        self.assertTrue(all("per_page=100" in argv[4] for argv, _ in run.calls[:2]))
        self.assertIn("/pulls?", run.calls[2][0][4])
        self.assertEqual(len(source.events), 124)
        self.assertNotIn("留言太多", source.status)
        full = FakeRun(*([(0, page(1, 100))] * mon.GITHUB_PAGES), (0, page(900, 1)), (0, []))
        with patch.object(mon.shutil, "which", return_value="/opt/gh"):
            source = mon.GitHubSource("o/r", runner=full, clock=lambda: NOW)
            source.refresh()
        self.assertEqual(len(full.calls), mon.GITHUB_PAGES + 2)
        self.assertIn("per_page=1&page=501", full.calls[mon.GITHUB_PAGES][0][4])
        self.assertIn("只讀了最新 500 則", source.status)
        self.assertEqual(len(source.events), 500)   # the probe's item (#900) never lands
        self.assertNotIn("#900", [e.reference for e in source.events])
        failed = FakeRun(*([(0, page(1, 100))] * mon.GITHUB_PAGES), (1, b""), (0, []))
        with patch.object(mon.shutil, "which", return_value="/opt/gh"):
            source = mon.GitHubSource("o/r", runner=failed, clock=lambda: NOW)
            source.refresh()
        self.assertIn("只讀了最新 500 則", source.status)   # unknown: stay conservative
        self.assertNotIn("讀取失敗", source.status)
        self.assertEqual(len(source.events), 500)
        exact = FakeRun(*([(0, page(1, 100))] * mon.GITHUB_PAGES), (0, []), (0, []))
        with patch.object(mon.shutil, "which", return_value="/opt/gh"):
            source = mon.GitHubSource("o/r", runner=exact, clock=lambda: NOW)
            source.refresh()
        self.assertNotIn("留言太多", source.status)   # exactly 500: nothing was cut
        self.assertEqual(len(exact.calls), mon.GITHUB_PAGES + 2)
        self.assertIn("per_page=1&page=501", exact.calls[mon.GITHUB_PAGES][0][4])

    def test_comment_page_failures_keep_old_events_and_duplicates_show_once(self):
        first = [comment(1, NOW - 30, "a")]
        run = FakeRun((0, first), (0, []))
        with patch.object(mon.shutil, "which", return_value="/opt/gh"):
            source = mon.GitHubSource("o/r", runner=run, clock=lambda: NOW)
            source.refresh()
            page = [dict(comment(10 + i, NOW - 30, "c"), id=10 + i) for i in range(100)]
            for later in ((0, {"message": "not a list"}),), ((0, page), (1, b"")):
                source.runner = FakeRun(*later)
                source.refresh(force=True)
                self.assertIn("讀取失敗", source.status)
                self.assertEqual([e.reference for e in source.events], ["#1"])
            # A comment posted between page reads shifts the next page by one.
            shifted = page[-1:] + [dict(comment(200 + i, NOW - 30, "d"), id=200 + i) for i in range(3)]
            source.runner = FakeRun((0, page), (0, shifted), (0, []))
            source.refresh(force=True)
        self.assertEqual(len(source.events), 103)

    def test_requests_addressed_to_the_owner_wait_until_settled(self):
        ask = lambda n, t, status="AUTHORIZATION_REQUESTED", kind="repair_gate": comment(
            n, NOW - t, f"<!-- activity: type={kind} actor=PM target=Owner status={status} -->\n"
                        f"## Need a decision on {n}")
        settled_by_owner = comment(11, NOW - 100,
                                   "<!-- activity: type=repair_authorization actor=Owner target=R2 status=APPROVED -->")
        comments = [
            ask(10, 500),                                   # open
            ask(11, 400), settled_by_owner,                 # answered later by the Owner
            ask(12, 300, status="APPROVED"),                # already settled status
            ask(13, 300, kind="owner_decision_request_for_a_long_task_name_20261024"),   # any type, long name
            comment(14, NOW - 300, "see\n<!-- activity: type=x actor=PM target=Owner status=WAITING -->"),  # not leading
            comment(15, NOW - 300, ask(15, 300)["body"], association="NONE"),               # untrusted
            comment(16, NOW - 300, "<!-- activity: type=x actor=PM target=PM status=WAITING -->"),  # not the Owner
            ask(17, 300, kind="repair_gate", status="WAITING"),   # its PR closed afterwards
        ]
        pulls = [{"number": 17, "title": "x", "created_at": iso(NOW - 900),
                  "closed_at": iso(NOW - 50), "merged_at": None}]
        pending = mon.owner_pending(comments, pulls)
        self.assertEqual([item.reference for item in pending], ["#10", "#13"])
        self.assertEqual(pending[0].status, "AUTHORIZATION_REQUESTED")
        self.assertEqual(pending[0].summary, "Need a decision on 10")
        lines = mon.office_lines([], pending=pending[:1])
        self.assertTrue(lines[1].startswith("★ 等你決定：GitHub #10"))
        self.assertIn("Need a decision on 10", lines[1])

    def test_owner_marker_parsing_order_and_display_edges(self):
        mark = lambda n, t, fields, text="ask": comment(n, NOW - t, f"<!-- activity: {fields} -->\n{text} {n}")
        comments = [
            mark(20, 50, "type=x actor=PM target=PM note=actor=Owner status=WAITING"),   # nested: no field
            mark(20, 60, "type=x actor=PM target=Owner status=WAITING"),
            mark(21, 50, "type=x actor=PM target=PM reason=target=Owner status=WAITING"),  # nested: not a request
            mark(22, 60, "type=x actor=pm target=owner status=waiting"),                  # case: still a request
            mark(23, 60, "type=x actor=PM target=Owner status=WAITING"),
            mark(23, 40, "type=y actor=OWNER target=R2 status=approved"),                 # case: settles
            mark(24, 60, "type=x actor=PM target=Owner status=WAITING"),
            mark(24, 50, "type=x actor=PM target=Owner status=Approved"),                  # settled status, any case
            mark(25, 90, "type=y actor=Owner target=R2 status=APPROVED"),                 # before the ask: no effect
            mark(25, 60, "type=x actor=PM target=Owner status=WAITING"),
            mark(26, 60, "type=x actor=PM target=Owner status=WAITING", "first"),
            mark(26, 30, "type=x actor=PM target=Owner status=BLOCKED", "second"),         # latest on a thread wins
            mark(27, 60, "type=x actor=PM target=Owner status=WAITING"),                  # PR closed before the ask
            mark(28, 60, "type=x actor=PM target=Owner status=WAITING"),
            comment(28, NOW - 50, "<!-- activity: type=x actor=PM target=Owner status=APPROVED-->"),  # no space
            mark(29, 60, "type=x actor=PM target=Owner target=PM status=WAITING"),        # first field counts
        ]
        pulls = [{"number": 27, "closed_at": iso(NOW - 999), "merged_at": None}]
        pending = {item.reference: item for item in mon.owner_pending(comments, pulls)}
        self.assertEqual(sorted(pending), ["#20", "#22", "#25", "#26", "#27", "#29"])
        self.assertEqual((pending["#26"].status, pending["#26"].summary), ("BLOCKED", "second 26"))
        many = [mon.OwnerPending(NOW - i, f"#{i}", "WAITING", "x") for i in range(7, 0, -1)]
        lines = mon.office_lines([], pending=many)
        self.assertEqual(sum(line.startswith("★") for line in lines), mon.OWNER_SHOWN + 1)
        self.assertIn("…另有 2 筆", lines[mon.OWNER_SHOWN + 1])
        narrow = mon.office_view([], width=3, pending=many[:1])
        self.assertTrue(narrow[1][0].startswith("★"))

    def test_owner_requests_outlive_the_comment_window_until_settled(self):
        ask = "<!-- activity: type=repair_gate actor=PM target=Owner status=AUTHORIZATION_REQUESTED -->\nplease"
        answer = "<!-- activity: type=repair_authorization actor=Owner target=R2 status=APPROVED -->\nok"
        with patch.object(mon.shutil, "which", return_value="/opt/gh"):
            source = mon.GitHubSource("o/r", runner=FakeRun((0, [comment(9, NOW - 30, ask)]), (0, [])),
                                      clock=lambda: NOW)
            source.refresh()
            self.assertEqual([item.reference for item in source.pending], ["#9"])
            self.assertIn("等你決定：看最近 12 小時", source.scope)
            source.runner = FakeRun((0, []), (0, []))       # the ask has aged out of the window
            source.refresh(force=True)
            self.assertEqual([item.reference for item in source.pending], ["#9"])
            source.runner = FakeRun((0, [comment(9, NOW - 5, answer)]), (0, []))
            source.refresh(force=True)
            self.assertEqual(source.pending, [])
        row = mon.OfficeRow("codex 主對話", mon.IDLE, "x", 1.0, 0, key="codex:T1", main=True)
        self.assertIn(source.scope, mon.office_lines([row], pending=[], scope=source.scope)[-1])

    def test_branch_prs_leave_out_forks(self):
        pulls = [{"number": 1, "head": {"ref": "fix/a", "repo": {"full_name": "o/r"}}, "state": "open"},
                 {"number": 2, "head": {"ref": "fix/a", "repo": {"full_name": "fork/r"}},
                  "state": "closed", "merged_at": None},
                 {"number": 3, "head": {"ref": "fix/b", "repo": None}, "state": "closed", "merged_at": None}]
        self.assertEqual(mon.branch_prs(pulls, "o/r"), {"fix/a": (1, "OPEN")})
        self.assertEqual(mon.branch_prs(pulls, "O/R"), {"fix/a": (1, "OPEN")})   # names compare case-insensitively

    def test_source_keeps_owner_pending_with_its_events(self):
        body = "<!-- activity: type=repair_gate actor=PM target=Owner status=AUTHORIZATION_REQUESTED -->\nplease"
        run = FakeRun((0, [comment(9, NOW - 30, body)]), (0, []))
        with patch.object(mon.shutil, "which", return_value="/opt/gh"):
            source = mon.GitHubSource("o/r", runner=run, clock=lambda: NOW)
            source.refresh()
        self.assertEqual([item.reference for item in source.pending], ["#9"])

    def test_background_refresh_never_blocks_and_runs_once_per_interval(self):
        import threading
        gate = threading.Event()
        run = FakeRun((0, [comment(6, NOW - 30, "bg")]), (0, []))
        def slow(argv, **kwargs):
            gate.wait(5)
            return run(argv, **kwargs)
        with patch.object(mon.shutil, "which", return_value="/opt/gh"):
            source = mon.GitHubSource("o/r", runner=slow, clock=lambda: NOW)
            source.refresh_in_background()           # returns at once while gh "runs"
            self.assertEqual(source.events, [])
            source.refresh_in_background()           # in flight / not due: no second thread
            gate.set()
            source._thread.join(5)
        self.assertEqual(len(run.calls), 2)
        self.assertEqual([e.reference for e in source.events], ["#6"])
        self.assertIn("每 30 秒", source.status)
        lines = mon.activity_lines([], github=source, background=True)   # not due yet
        self.assertEqual(len(run.calls), 2)
        self.assertIn("GitHub #6：新留言 — bg", lines[0])

    def test_missing_gh_and_bad_repo_fail_closed(self):
        with patch.object(mon.shutil, "which", return_value=None):
            source = mon.GitHubSource("o/r", runner=FakeRun(), clock=lambda: NOW)
            source.refresh()
        self.assertIn("找不到 gh", source.status)
        with self.assertRaises(ValueError):
            mon.GitHubSource("o/r; rm -rf /")

    def test_no_github_once_never_touches_gh(self):
        snapshot = mon.Snapshot([], {}, [], frozenset())
        with patch.object(mon, "collect", return_value=snapshot), \
             patch.object(mon, "scan_tasks", return_value=[]), \
             patch.object(mon.shutil, "which", side_effect=AssertionError("gh used with --no-github")), \
             patch("sys.stdout", __import__("io").StringIO()) as out:
            self.assertEqual(mon.main(["--once", "--no-github"]), 0)
        self.assertIn("GitHub 動態已關閉", out.getvalue())

    def test_once_reads_github_by_default(self):
        snapshot = mon.Snapshot([], {}, [], frozenset())
        run = FakeRun((0, []), (0, []))
        with patch.object(mon, "collect", return_value=snapshot), \
             patch.object(mon, "scan_tasks", return_value=[]), \
             patch.object(mon.shutil, "which", return_value="/opt/gh"), \
             patch.object(mon.subprocess, "run", run), \
             patch.object(mon.time, "time", return_value=NOW), \
             patch("sys.stdout", __import__("io").StringIO()) as out:
            self.assertEqual(mon.main(["--once"]), 0)
        self.assertTrue(run.calls)
        self.assertTrue(all(argv[1:4] == ["api", "-X", "GET"] for argv, _ in run.calls))
        self.assertNotIn("GitHub 動態已關閉", out.getvalue())

    def test_github_opt_in_uses_read_only_get_only(self):
        snapshot = mon.Snapshot([], {}, [], frozenset())
        run = FakeRun((0, [comment(4, NOW - 30, "hello")]), (0, []))
        with patch.object(mon, "collect", return_value=snapshot), \
             patch.object(mon, "scan_tasks", return_value=[]), \
             patch.object(mon.shutil, "which", return_value="/opt/gh"), \
             patch.object(mon.subprocess, "run", run), \
             patch.object(mon.time, "time", return_value=NOW), \
             patch("sys.stdout", __import__("io").StringIO()) as out:
            self.assertEqual(mon.main(["--once", "--github"]), 0)
        self.assertEqual(len(run.calls), 2)
        self.assertTrue(all(argv[1:4] == ["api", "-X", "GET"] for argv, _ in run.calls))
        self.assertTrue(all(mon.DEFAULT_GITHUB_REPO in argv[4] for argv, _ in run.calls))
        self.assertIn("GitHub #4：新留言 — hello", out.getvalue())

    def test_developer_status_markers_are_finite_and_self_reported(self):
        rows = (
            ("developer_dispatch", "PM", "Codex", "ASSIGNED", "自述已指派 Developer 任務"),
            ("developer_progress", "OpenCode", "Integrator", "ACTIVE", "回報進度：處理中"),
            ("developer_progress", "Codex", "Reviewer", "WAITING", "等待 Reviewer 的下一步"),
            ("developer_progress", "CompanyGW", "Owner", "BLOCKED", "受阻，等待 Owner 協助"),
            ("developer_progress", "OpenCode", "PM", "UNKNOWN", "工作狀態未知"),
            ("test_result", "Codex", "Tests", "PASS", "自述測試通過"),
            ("test_result", "OpenCode", "CI", "FAIL", "自述測試失敗"),
            ("test_result", "CompanyGW", "Tests", "UNKNOWN", "測試結果未知"),
        )
        comments = [comment(index + 20, NOW - 20 + index,
                            f"<!-- activity: type={kind} actor={actor} target={target} status={status} -->\n"
                            "## task status")
                    for index, (kind, actor, target, status, _) in enumerate(rows)]
        events = mon.github_events(comments, [], NOW - 99)
        self.assertEqual([event.event_type for event in events], [row[0] for row in rows])
        lines = mon.render_activity(events)
        for index, ((_, actor, target, _, outcome), line) in enumerate(zip(rows, lines)):
            with self.subTest(index=index):
                display_actor = mon.MARKER_NAMES.get(actor, actor)
                display_target = mon.MARKER_NAMES.get(target, target)
                self.assertIn(f"{display_actor} → {display_target}：", line)
                self.assertIn(outcome, line)
                self.assertIn(f"（#{20 + index}・自述）", line)

    def test_invalid_or_unauthorized_developer_markers_fall_back_to_comment(self):
        bodies = (
            "<!-- activity: type=developer_dispatch actor=Codex target=PM status=ASSIGNED -->\nTask",
            "<!-- activity: type=developer_dispatch actor=PM target=CompanyGW status=PASS -->\nTask",
            "<!-- activity: type=developer_progress actor=CompanyGW target=CompanyGW status=ACTIVE -->\nTask",
            "<!-- activity: type=test_result actor=Codex target=Tests status=PASS unexpected -->\nTask",
        )
        for body in bodies:
            with self.subTest(body=body):
                events = mon.github_events([comment(31, NOW - 9, body)], [], NOW - 99)
                self.assertEqual([event.event_type for event in events], ["github_comment"])
                self.assertIn("GitHub #31：新留言", mon.render_activity(events)[0])
        malformed_only = "<!-- activity: type=test_result actor=Codex target=Tests status=PASS"
        fallback = mon.github_events([comment(33, NOW - 9, malformed_only)], [], NOW - 99)
        self.assertEqual([event.event_type for event in fallback], ["github_comment"])
        self.assertEqual(mon.render_activity(fallback)[0].split(" — ")[0].split("：", 1)[1], "新留言")

    def test_typed_marker_headline_redacts_secrets_and_private_paths(self):
        body = ("<!-- activity: type=test_result actor=Codex target=Tests status=PASS -->\n"
                "## token=abc123xyz789 /Users/davidsun/private/task.py")
        line = mon.render_activity(mon.github_events(
            [comment(32, NOW - 9, body)], [], NOW - 99))[0]
        self.assertIn("自述測試通過", line)
        self.assertIn("[已遮蔽]", line)
        self.assertIn("[路徑]", line)
        self.assertNotIn("abc123xyz789", line)
        self.assertNotIn("/Users/davidsun", line)

    def test_once_with_github_merges_both_sources_in_time_order(self):
        stage = mon.Stage("red", "deepseek", NOW - 300, "OPENCODE_OUTPUT_RETURNED", "ok",
                          1, 0, 0, "", (), False, "ok", NOW - 100)
        task = mon.Task("/private/tmp/fixture", "task-1", "feature-x", (stage,))
        body = "<!-- activity: type=review_dispatch actor=PM target=Claude status=DISPATCHED -->\n請審查 PR #5"
        run = FakeRun((0, [comment(5, NOW - 200, body, "pull")]), (0, []))
        snapshot = mon.Snapshot([], {}, [], frozenset())
        with patch.object(mon.shutil, "which", return_value="/opt/gh"):
            source = mon.GitHubSource("o/r", runner=run, clock=lambda: NOW)
        with patch.object(mon, "collect", return_value=snapshot), \
             patch.object(mon, "scan_tasks", return_value=[task]), \
             patch.object(mon.shutil, "which", return_value="/opt/gh"), \
             patch("sys.stdout", __import__("io").StringIO()) as out:
            self.assertEqual(mon.run_once(source), 0)
        activity = out.getvalue().split("\n最近動態\n", 1)[1].splitlines()
        order = [next(i for i, l in enumerate(activity) if marker in l)
                 for marker in ("開始一次執行嘗試", "PM → Claude：派出審查任務（#5・自述） — 請審查 PR 5",
                                "完成，已拿到輸出；程序已清乾淨")]
        self.assertEqual(order, sorted(order))

    def test_local_lines_do_not_repeat_the_task_reference(self):
        event = mon.ActivityEvent(NOW, "stage_attempt", "task-1", "red", "OBSERVED", "task-1/red")
        line = mon.render_activity([event])[0]
        self.assertEqual(line.count("task-1"), 1)
        self.assertIn("llmtool → 任務 task-1 · 階段 red：開始一次執行嘗試", line)

    def test_timestamps_older_than_today_show_the_date(self):
        from datetime import datetime, timedelta
        old = (datetime.now() - timedelta(days=2)).timestamp()
        self.assertRegex(mon._when(old), r"^\d{2}/\d{2} \d{2}:\d{2}$")
        self.assertRegex(mon._when(datetime.now().timestamp()), r"^\d{2}:\d{2}:\d{2}$")


DISPATCH = {   # the Owner's switches as of 2026-10-23
    "opencode-developer": {"dispatch": "PAUSED", "decided_by": "Owner", "decided_at": "2026-10-22"},
    "agy": {"dispatch": "PAUSED", "decided_by": "Owner", "decided_at": "2026-10-22"},
    "company-gw": {"dispatch": "ALLOWED", "decided_by": "Owner", "decided_at": "2026-10-22"},
}
POLICY_EVIDENCE = "https://github.com/davidcatcat/davidcattoolsproject/issues/289"


def policy(workers):
    """A whole worker-dispatch.json the llmtool gate accepts: every Worker
    present with reason and evidence; `workers` overrides entries (None drops one)."""
    entry = {"dispatch": "ALLOWED", "decided_by": "Owner", "decided_at": "2026-10-22",
             "reason": "test", "evidence": POLICY_EVIDENCE}
    full = {key: dict(entry) for key in sorted(mon.DISPATCH_WORKERS)}
    for key, item in workers.items():
        if item is None:
            full.pop(key, None)
        elif type(item) is dict and key in full:
            full[key] = {**full[key], **item}
        else:
            full[key] = item
    return {"schema_version": 1, "workers": full}


class _GateMixin:
    """Loads the llmtool gate from this checkout for the agreement tests."""

    @classmethod
    def setUpClass(cls):
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "tools", "llmtool")
        sys.path.insert(0, root)
        try:
            from llmtool import worker_dispatch
        finally:
            sys.path.remove(root)
        # Never compare against some other installed llmtool.
        if not os.path.realpath(worker_dispatch.__file__).startswith(os.path.realpath(root) + os.sep):
            raise AssertionError(f"llmtool gate loaded from {worker_dispatch.__file__}")
        cls.gate = staticmethod(worker_dispatch._parse_policy)
        cls.gate_file = staticmethod(lambda path: worker_dispatch._parse_policy(
            worker_dispatch._read_policy_file(Path(path))))


class DispatchGateAgreementTests(_GateMixin, unittest.TestCase):
    """The monitor's reading of worker-dispatch.json must match the llmtool
    gate it describes: whatever the gate refuses, the monitor never shows as
    a usable switch."""

    def agree(self, raw: bytes):
        try:
            self.gate(raw)
            accepted = True
        except Exception:
            accepted = False
        switches, problem = mon.worker_dispatch(raw)
        self.assertEqual(problem == "", accepted, raw[:200])
        return accepted

    def test_repository_policy_is_accepted_by_both(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "tools", "llmtool", "worker-dispatch.json")
        with open(path, "rb") as handle:
            self.assertTrue(self.agree(handle.read()))

    def test_every_mutation_is_judged_the_same_way(self):
        ref = POLICY_EVIDENCE + "#issuecomment-6373198827"
        good = {"task_id": "5fd1be3ee96e42678fe1dac75a9b697a", "owner_assignment_ref": ref}
        cases = {
            "valid": {},
            "decided_by PM": {"agy": {"decided_by": "PM"}},
            "unknown switch": {"agy": {"dispatch": "MAYBE"}},
            "lower-case switch": {"agy": {"dispatch": "allowed"}},
            "evidence not #289": {"company-gw": {"evidence": "https://example.com/x"}},
            "evidence PR 283": {"company-gw": {"evidence": "https://github.com/davidcatcat/davidcattoolsproject/pull/283"}},
            "missing worker": {"agy": None},
            "extra worker": {"codex-reviewer-agent": {"dispatch": "ALLOWED", "decided_by": "Owner",
                                                      "decided_at": "2026-10-22", "reason": "r",
                                                      "evidence": POLICY_EVIDENCE}},
            "empty reason": {"agy": {"reason": ""}},
            "long reason": {"agy": {"reason": "x" * 513}},
            "bad date": {"agy": {"decided_at": "2026-02-30"}},
            "short date": {"agy": {"decided_at": "2026-1-2"}},
            "extra field": {"agy": {"note": "x"}},
            "assignment ok": {"claude-developer": {"owner_assignments": [good]}},
            "assignment bad id": {"claude-developer": {"owner_assignments": [dict(good, task_id="x")]}},
            "assignment bad ref": {"claude-developer": {"owner_assignments": [
                dict(good, owner_assignment_ref=POLICY_EVIDENCE)]}},
            "assignment duplicate": {"claude-developer": {"owner_assignments": [good, good]}},
            "assignment same task, other ref": {"claude-developer": {"owner_assignments": [
                good, dict(good, owner_assignment_ref=ref.replace("6373198827", "6373198828"))]}},
            "assignments not a list": {"claude-developer": {"owner_assignments": {}}},
            "assignments null": {"claude-developer": {"owner_assignments": None}},
            "unhashable switch": {"agy": {"dispatch": []}},
            "object switch": {"agy": {"dispatch": {}}},
            "date not a string": {"agy": {"decided_at": 20261022}},
        }
        for label, change in cases.items():
            with self.subTest(label):
                self.agree(json.dumps(policy(change)).encode())
        whole = json.dumps(policy({}))
        raws = {
            "duplicate key": whole.replace('"workers": {', '"workers": {"agy": {}, ', 1),
            "schema 2": whole.replace('"schema_version": 1', '"schema_version": 2'),
            "schema true": whole.replace('"schema_version": 1', '"schema_version": true'),
            "schema 1.0": whole.replace('"schema_version": 1', '"schema_version": 1.0'),
            "extra top key": whole[:-1] + ', "x": 1}',
            "NaN": whole.replace('"schema_version": 1', '"schema_version": NaN'),
            "not json": "{not json",
            "empty": "",
        }
        for label, raw in raws.items():
            with self.subTest(label):
                self.agree(raw.encode())
        self.assertEqual(mon.worker_dispatch(json.dumps(policy({})).encode())[1], "")

    def test_size_is_checked_on_the_bytes_too(self):
        # The gate's reader refuses > 64 KiB; the local-ref fallback has only
        # the monitor's own byte check, so it must hold on its own.
        many = [{"task_id": f"{i:032x}", "owner_assignment_ref": POLICY_EVIDENCE + f"#issuecomment-{i + 1}"}
                for i in range(700)]
        raw = json.dumps(policy({"claude-developer": {"owner_assignments": many}})).encode()
        self.assertGreater(len(raw), 64 * 1024)
        self.gate(raw)   # schema-valid: only the size is wrong
        self.assertEqual(mon.worker_dispatch(raw)[1], "invalid")


class DispatchFileAgreementTests(_GateMixin, unittest.TestCase):
    """The same agreement for how the file itself is read."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def agree_file(self, path):
        try:
            self.gate_file(path)
            accepted = True
        except Exception:
            accepted = False
        raw, _ = mon.load_dispatch("/nonexistent", path)
        switches, problem = mon.worker_dispatch(raw)
        self.assertEqual(problem == "", accepted, path)
        return accepted

    def test_file_shapes_are_judged_the_same_way(self):
        base = Path(self.tmp.name)
        good = base / "good.json"
        good.write_text(json.dumps(policy({})), encoding="utf-8")
        self.assertTrue(self.agree_file(str(good)))
        (base / "link.json").symlink_to(good)
        os.link(good, base / "hard.json")   # good now has two links as well
        (base / "dir.json").mkdir()
        os.mkfifo(base / "fifo.json")
        (base / "tiny.json").write_bytes(b"{")
        many = [{"task_id": f"{i:032x}", "owner_assignment_ref": POLICY_EVIDENCE + f"#issuecomment-{i + 1}"}
                for i in range(700)]
        (base / "big.json").write_text(json.dumps(policy({"claude-developer": {"owner_assignments": many}})),
                                       encoding="utf-8")
        self.assertGreater((base / "big.json").stat().st_size, 64 * 1024)
        for name in ("link.json", "hard.json", "good.json", "dir.json", "fifo.json", "tiny.json", "big.json"):
            with self.subTest(name):
                self.assertFalse(self.agree_file(str(base / name)))   # every one refused, never blocks
        self.assertEqual(mon.worker_dispatch(mon.load_dispatch("/nonexistent", str(base / "dir.json"))[0])[1],
                         "rejected")

    def test_missing_canonical_file_falls_back_to_a_labelled_local_ref_only(self):
        raw, source = mon.load_dispatch(self.tmp.name, os.path.join(self.tmp.name, "absent.json"))
        self.assertEqual((raw, source), (None, ""))   # an explicit path never falls back
        repo = Path(self.tmp.name) / "repo"
        policy_file = repo / "tools" / "llmtool" / "worker-dispatch.json"
        policy_file.parent.mkdir(parents=True)
        committed = json.dumps(policy({})).encode()
        policy_file.write_bytes(committed)
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        for argv in (["init", "-q", "-b", "main"], ["add", "-A"],
                     ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "p"]):
            subprocess.run(["git", "-C", str(repo), *argv], check=True, env=env,
                           capture_output=True)
        canonical = str(repo)

        def load():
            mon._dispatch_cache.clear()
            raw, source = mon.load_dispatch(canonical)
            return mon.worker_dispatch(raw)[1], source
        self.assertEqual(load(), ("", "派工檔"))
        policy_file.unlink()   # missing: the labelled local main ref
        problem, source = load()
        self.assertEqual(problem, "")
        self.assertIn("本機 main", source)
        self.assertIn("可能不是最新", source)
        outside = Path(self.tmp.name) / "outside.json"
        outside.write_bytes(committed)
        for plant in (lambda: policy_file.symlink_to(outside), lambda: os.link(outside, policy_file),
                      lambda: policy_file.mkdir()):
            with self.subTest(plant=plant):
                plant()
                try:
                    self.assertEqual(load(), ("rejected", "派工檔"))   # refused, no fallback
                finally:   # a failed case must not break the next plant()
                    (policy_file.rmdir if policy_file.is_dir() and not policy_file.is_symlink()
                     else policy_file.unlink)()

    def test_roster_workers_are_gate_workers(self):
        self.assertLessEqual({key for _, key, _ in mon.WORKER_ROSTER}, mon.DISPATCH_WORKERS)


class OfficeViewTests(unittest.TestCase):
    NOW = 1_800_000_000.0

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.canonical = os.path.realpath(str(base / "repo" / "davidcattoolsproject"))
        os.makedirs(self.canonical)
        self.task = base / "llmtool-dev-workspaces" / "davidcattoolsproject" / "tasks" / "wp-1"
        os.makedirs(self.task)
        self.codex = base / "codex"
        self.claude = base / "claude"
        self._write_dispatch(DISPATCH)
        self._register("wp-1", str(self.task))

    def _set_timezone(self, zone):
        previous = os.environ.get("TZ")
        os.environ["TZ"] = zone
        mon.time.tzset()

        def restore():
            if previous is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = previous
            mon.time.tzset()

        self.addCleanup(restore)

    def _write_dispatch(self, workers, raw=None):
        path = Path(self.canonical) / "tools" / "llmtool" / "worker-dispatch.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw if raw is not None else json.dumps(policy(workers)), encoding="utf-8")
        return path

    def _register(self, task_id, root, **fields):
        registry = Path(self.canonical) / ".git" / "llmtool-worktrees"
        registry.mkdir(parents=True, exist_ok=True)
        os.chmod(registry, 0o700)
        record = {"task_id": task_id, "root_realpath": os.path.realpath(root),
                  "lock_state": "LOCKED", "state": "ACTIVE", "session_ref": ""}
        record.update(fields)
        path = registry / f"{task_id}.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        os.chmod(path, 0o600)
        mon._registry_cache.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, path: Path, items, age: float) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(item) + "\n" for item in items), encoding="utf-8")
        os.utime(path, (self.NOW - age, self.NOW - age))
        return path

    def _codex(self, name, meta, events, age):
        day = mon.time.strftime("%Y/%m/%d", mon.time.localtime(self.NOW))
        items = [{"type": "session_meta", "payload": meta}]
        items += [{"type": "response_item", "payload": {"type": "message",
                   "content": "PROMPT-SECRET MODEL-OUTPUT-SECRET /Users/davidsun/x"}}]
        items += [{"type": "event_msg", "payload": {"type": kind}} for kind in events]
        return self._write(self.codex / day / f"rollout-{name}.jsonl", items, age)

    def rows(self, processes=()):
        return mon.office_rows(list(processes), self.NOW, codex_root=str(self.codex),
                               claude_root=str(self.claude),
                               canonical=self.canonical)

    def by_name(self, rows):
        return {row.name: row for row in rows}

    def test_malformed_d6_fields_keep_the_task_placed(self):
        future = mon.time.strftime("%Y-%m-%dT%H:%M:%SZ", mon.time.gmtime(mon.time.time() + 86400))
        for value in (12, ["x"], future, None):
            with self.subTest(value=value):
                self._register("wp-1", str(self.task), last_verified_at=value, state=7,
                               trusted_agent_id={"x": 1})
                self.assertIn("wp-1", mon.registered_tasks(self.canonical))
                state, agent, _, _, _ = mon.registry_details(self.canonical)["wp-1"]
                self.assertEqual((state, agent), ("?", ""))
                lines = mon.d6_task_lines(self.canonical, [], mon.time.time())
                self.assertEqual(len(lines), 1)   # placed by its write time

    def test_d6_records_still_in_progress_after_their_pr_ended_are_flagged(self):
        stamp = lambda age: mon.time.strftime("%Y-%m-%dT%H:%M:%SZ", mon.time.gmtime(self.NOW - age))
        roots = {}
        for name in ("recent-merged", "recent-open", "recent-retired", "old-closed", "old-plain"):
            roots[name] = Path(self.tmp.name) / name
            roots[name].mkdir()
        self._register("recent-merged", str(roots["recent-merged"]), branch="fix/a",
                       last_verified_at=stamp(60))
        self._register("recent-open", str(roots["recent-open"]), branch="fix/b",
                       state="REVIEW_PENDING", last_verified_at=stamp(60))
        self._register("recent-retired", str(roots["recent-retired"]), branch="fix/c",
                       state="RETIRE_READY", last_verified_at=stamp(60))
        self._register("old-closed", str(roots["old-closed"]), branch="docs/d",
                       state="REPAIR", last_verified_at=stamp(3 * 86400))
        self._register("old-plain", str(roots["old-plain"]), branch="docs/e",
                       last_verified_at=stamp(3 * 86400))
        pulls = [{"number": 211, "head": {"ref": "fix/a"}, "merged_at": iso(self.NOW - 999), "state": "closed"},
                 {"number": 212, "head": {"ref": "fix/b"}, "merged_at": None, "state": "open"},
                 {"number": 213, "head": {"ref": "fix/c"}, "merged_at": iso(self.NOW - 999), "state": "closed"},
                 {"number": 214, "head": {"ref": "docs/d"}, "merged_at": None, "state": "closed"},
                 {"number": "x", "head": {"ref": "docs/e"}, "state": "closed"},
                 {"number": 215, "head": {"ref": "bad ref; rm"}, "state": "closed"}]
        prs = mon.branch_prs(pulls)
        self.assertEqual(prs, {"fix/a": (211, "MERGED"), "fix/b": (212, "OPEN"),
                               "fix/c": (213, "MERGED"), "docs/d": (214, "CLOSED")})
        lines = mon.d6_task_lines(self.canonical, [], self.NOW, prs)
        by_task = {next(t for t in roots if f"  {t} " in line): line for line in lines if not line.startswith("⚠")}
        self.assertIn("⚠ PR #211 已合併，登記還是進行中", by_task["recent-merged"])
        self.assertNotIn("⚠", by_task["recent-open"])      # its PR is still open
        self.assertNotIn("⚠", by_task["recent-retired"])   # not an in-progress state
        self.assertEqual(lines[-1], "⚠ 另有 1 筆較舊的 D6 登記還是進行中，但 PR 已合併或關閉：old-closed")
        shown = mon.d6_lines([f"line {i}" for i in range(20)] + [lines[-1]])
        self.assertEqual((len(shown), shown[-1]), (17, lines[-1]))   # 15 tasks, then the summary
        # Without GitHub nothing is flagged.
        self.assertFalse(any("⚠" in line for line in mon.d6_task_lines(self.canonical, [], self.NOW)))

    def test_d6_task_lines_list_recent_tasks_with_their_live_session(self):
        stamp = lambda age: mon.time.strftime("%Y-%m-%dT%H:%M:%SZ", mon.time.gmtime(self.NOW - age))
        other = Path(self.tmp.name) / "wp-2"
        other.mkdir()
        self._register("wp-1", str(self.task), state="REVIEW_PENDING", last_verified_at=stamp(600),
                       trusted_agent_id="claude-ui-agent", disposition="DISPOSITION-SECRET")
        self._register("wp-2", str(other), state="ACTIVE", last_verified_at=stamp(60))
        self._register("old", str(other), last_verified_at=stamp(2 * 86400))
        rows = [mon.OfficeRow("claude developer（互動）", mon.ATTENTION, "x", 1.0, 10, task="task wp-1"),
                mon.OfficeRow("claude 互動", mon.CLOSED, "y", 1.0, 10, task="task wp-2")]
        lines = mon.d6_task_lines(self.canonical, rows, self.NOW)
        self.assertEqual(len(lines), 2)
        self.assertIn("wp-2  進行中  →  沒有 session 在跑  （未綁定 agent）", lines[0])
        self.assertIn("wp-1  等審查  →  claude developer（互動） 需注意  （claude-ui-agent）", lines[1])
        self.assertNotIn("SECRET", "\n".join(lines))

    def _turn_db(self, turns, name="history.sqlite"):
        """A real WAL database kept open by a writer, as Codex keeps its own."""
        path = self.codex.parent / name
        db = sqlite3.connect(path)
        self.addCleanup(db.close)
        db.execute("pragma journal_mode=wal")
        db.execute("create table thread_turns(thread_id, turn_id, rollout_ordinal, status, "
                   "error_json, started_at, completed_at)")
        db.executemany("insert into thread_turns values(?, ?, ?, ?, 'ERROR-SECRET', ?, ?)", turns)
        db.commit()
        return path

    def _office(self, db_path, processes=()):
        with patch.object(mon, "CODEX_HISTORY", str(db_path)):
            return self.rows(processes)

    def _rollout(self, sid, markers, age=5, **meta):
        """markers: [(kind, seconds before NOW or None for untimed)]."""
        day = mon.time.strftime("%Y/%m/%d", mon.time.localtime(self.NOW))
        items = [{"type": "session_meta", "payload": {"id": sid, "cwd": self.canonical,
                                                      "thread_source": "user",
                                                      "timestamp": "2026-10-24T01:46:00.000Z", **meta}}]
        for kind, ago in markers:
            item = {"type": "event_msg", "payload": {"type": kind}}
            if ago is not None:
                item["timestamp"] = mon.time.strftime("%Y-%m-%dT%H:%M:%S.123Z",
                                                      mon.time.gmtime(self.NOW - ago))
            items.append(item)
        return self._write(self.codex / day / f"rollout-{sid}.jsonl", items, age)

    def test_codex_turn_record_beats_session_markers(self):
        clock = lambda seconds: mon.time.strftime("%H:%M", mon.time.localtime(seconds))
        now = int(self.NOW)
        done = [("task_started", 3000), ("task_complete", 1200)]
        cases = (
            ("inProgress", now - 600, None, [("task_started", 600)], 5,
             (mon.WORKING, f"（這輪 {clock(now - 600)} 起）", 5.0)),
            ("completed", now - 3000, now - 1200, done, 1200,
             (mon.IDLE, f"等待下一個指示（上一輪 {clock(now - 1200)} 結束）", 1200.0)),
            ("failed", now - 3000, now - 1200, done, 1200,
             (mon.ATTENTION, f"上一輪失敗，要看 Codex 畫面（上一輪 {clock(now - 1200)} 失敗）", 1200.0)),
            ("interrupted", now - 3000, now - 1200, [("task_started", 3000), ("turn_aborted", 1200)], 1200,
             (mon.IDLE, f"等待下一個指示（上一輪 {clock(now - 1200)} 被中斷）", 1200.0)),
            # The terminal marker 0-2 s after the recorded end: the same turn.
            ("failed", now - 3000, now - 1200, [("task_started", 3000), ("task_complete", 1199)], 1199,
             (mon.ATTENTION, "失敗）", 1200.0)),
            ("failed", now - 3000, now - 1200, [("task_started", 3000), ("task_complete", 1198)], 1198,
             (mon.ATTENTION, "失敗）", 1200.0)),
            # Written well after the turn ended: the fresher file age stays.
            ("completed", now - 3000, now - 1200, done, 60, (mon.IDLE, "結束）", 60.0)),
            ("completed", now - 3000, 10 ** 15, done, 5,   # out of range: no crash, no fake time
             (mon.IDLE, "等待下一個指示（上一輪結束）", 5.0)),
            ("unknownStatus", now - 3000, now - 1200, done, 5,   # not a Codex status: markers decide
             (mon.IDLE, "等待下一個指示（對話 ", 5.0)),
        )
        for n, (status, began, ended, markers, age, want) in enumerate(cases):
            with self.subTest(f"{n} {status}"):
                db_path = self._turn_db([("T1", "u", 0, status, began, ended)], f"h{n}.sqlite")
                self._rollout("T1", markers, age)
                row = self.by_name(self._office(db_path))["codex 主對話"]
                self.assertEqual(row.state, want[0], row.detail)
                self.assertIn(want[1], row.detail)
                self.assertEqual(row.age, want[2])
                self.assertNotIn("SECRET", row.detail)

    def test_newer_session_marker_beats_a_lagging_turn_record(self):
        now = int(self.NOW)
        cases = (
            # A new turn started after the recorded one ended.
            ("completed", now - 3000, now - 1200, [("task_complete", 1200), ("task_started", 60)], mon.WORKING),
            # The running turn ended before the record caught up.
            ("inProgress", now - 600, None, [("task_started", 600), ("task_complete", 30)], mon.IDLE),
            # Untimed marker beyond the tail: bounded by the file's write time.
            ("completed", now - 3000, now - 1200, [("task_started", None)], mon.WORKING),
        )
        for n, (status, began, ended, markers, want) in enumerate(cases):
            with self.subTest(n):
                db_path = self._turn_db([("T1", "u", 0, status, began, ended)], f"lag{n}.sqlite")
                self._rollout("T1", markers, age=30)
                self.assertEqual(self.by_name(self._office(db_path))["codex 主對話"].state, want)

    def test_malformed_marker_time_does_not_break_the_roster(self):
        db_path = self._turn_db([("T1", "u", 0, "completed", 100, 200)])
        day = mon.time.strftime("%Y/%m/%d", mon.time.localtime(self.NOW))
        self._write(self.codex / day / "rollout-T1.jsonl", [
            {"type": "session_meta", "payload": {"id": "T1", "cwd": self.canonical}},
            {"type": "event_msg", "timestamp": 12, "payload": {"type": "task_started"}},
            {"type": "event_msg", "timestamp": "9999-99-99T99", "payload": {"type": "task_complete"}}], 5)
        self.assertIn("codex 主對話", self.by_name(self._office(db_path)))

    def test_turn_history_failure_is_dropped_for_the_rest_of_the_refresh(self):
        class Broken:
            calls = 0
            closed = False

            def execute(self, *args):
                Broken.calls += 1
                raise sqlite3.OperationalError("database is locked")

            def close(self):
                Broken.closed = True

        history = {"db": Broken()}
        self.assertIsNone(mon.codex_turn(history, "T1"))
        self.assertIsNone(mon.codex_turn(history, "T2"))
        self.assertEqual((Broken.calls, Broken.closed, history["db"]), (1, True, None))

    def test_failed_subagent_turn_needs_a_look(self):
        now = int(self.NOW)
        db_path = self._turn_db([("S1", "u", 0, "failed", now - 900, now - 600),
                                 ("S2", "u", 0, "interrupted", now - 900, now - 600)])
        for sid in ("S1", "S2"):
            self._rollout(sid, [("task_started", 900), ("task_complete", 600)], 600,
                          thread_source="subagent", parent_thread_id="T1",
                          agent_path=f"/root/job_{sid}")
        subs = {row.detail: row for row in self._office(db_path) if row.kind == "subagent"}
        failed = next(row for detail, row in subs.items() if "job S1" in detail)
        interrupted = next(row for detail, row in subs.items() if "job S2" in detail)
        self.assertEqual(failed.state, mon.ATTENTION)
        self.assertIn("（上一輪失敗）", failed.detail)
        self.assertIn("失敗提醒最多顯示至", failed.detail)
        self.assertIn("不代表程序仍在執行", failed.detail)
        self.assertEqual(interrupted.state, mon.DONE)
        self.assertEqual(interrupted.detail, "job S2（上一輪被中斷）")

    def test_failed_subagent_expiry_uses_exact_codex_file_mtime_cutoff(self):
        self._set_timezone("PST8")
        now = int(self.NOW)
        file_age = 4 * 3600
        db_path = self._turn_db([("S1", "u", 0, "failed", now - 900, now - 600)])
        path = self._rollout("S1", [("task_started", 900), ("task_complete", 600)], file_age,
                             thread_source="subagent", parent_thread_id="T1",
                             agent_path="/root/turing")

        row = next(row for row in self._office(db_path) if row.kind == "subagent")
        self.assertEqual(row.state, mon.ATTENTION)
        self.assertIn("失敗提醒最多顯示至 2027-01-15 02:00:00", row.detail)
        self.assertIn("約 2 小時後隱藏", row.detail)
        self.assertIn("不代表程序仍在執行", row.detail)

    def test_failed_subagent_cutoff_includes_exact_six_hours_and_excludes_after(self):
        self._set_timezone("PST8")
        now = int(self.NOW)
        db_path = self._turn_db([("S1", "u", 0, "failed", now - 900, now - 600),
                                 ("S2", "u", 0, "failed", now - 900, now - 600),
                                 ("S3", "u", 0, "failed", now - 900, now - 600)])
        self._rollout("S2", [("task_started", 900), ("task_complete", 600)],
                      mon.OFFICE_WINDOW, thread_source="subagent",
                      parent_thread_id="T1", agent_path="/root/exact_cutoff")
        rows = self._office(db_path)
        exact = next(row for row in rows if row.kind == "subagent")
        self.assertEqual(exact.state, mon.ATTENTION)
        self.assertIn("失敗提醒最多顯示至 2027-01-15 00:00:00", exact.detail)
        self.assertIn("已到顯示期限", exact.detail)

        self._rollout("S3", [("task_started", 900), ("task_complete", 600)],
                      mon.OFFICE_WINDOW + 1, thread_source="subagent",
                      parent_thread_id="T1", agent_path="/root/after_cutoff")
        rows = self._office(db_path)
        self.assertEqual(sum(row.kind == "subagent" for row in rows), 1)

    def test_failed_subagent_expiry_minute_rounding_boundaries(self):
        self._set_timezone("PST8")
        cases = (
            (59, "2027-01-15 00:00:59", "約 1 分後隱藏"),
            (60, "2027-01-15 00:01:00", "約 1 分後隱藏"),
            (61, "2027-01-15 00:01:01", "約 2 分後隱藏"),
            (3599, "2027-01-15 00:59:59", "約 1 小時後隱藏"),
            (3600, "2027-01-15 01:00:00", "約 1 小時後隱藏"),
            (3601, "2027-01-15 01:00:01", "約 1 小時 1 分後隱藏"),
        )
        for remaining, absolute, relative in cases:
            with self.subTest(remaining=remaining):
                file_mtime = self.NOW - mon.OFFICE_WINDOW + remaining
                detail = mon._codex_failure_expiry_detail(file_mtime, self.NOW)
                self.assertEqual(
                    detail,
                    f"（失敗提醒最多顯示至 {absolute}；{relative}；"
                    "不代表程序仍在執行）")

    def test_failed_subagent_invalid_or_future_mtime_has_no_expiry_claim(self):
        now = int(self.NOW)
        db_path = self._turn_db([("S1", "u", 0, "failed", now - 900, now - 600)])
        path = self._rollout("S1", [("task_started", 900), ("task_complete", 600)], 600,
                             thread_source="subagent", parent_thread_id="T1",
                             agent_path="/root/future_mtime")
        db = mon.open_codex_history(str(db_path))
        self.addCleanup(db.close)
        for mtime in (float("nan"), float("inf"), float("-inf"), -1.0,
                      self.NOW + 1, 1e300, -1e300):
            with self.subTest(mtime=mtime):
                with patch.object(mon.os.path, "getmtime", return_value=mtime):
                    result = mon._codex_row(str(path), self.NOW, self.canonical,
                                            {"db": db})
                self.assertIsNotNone(result)
                row = result[2]
                self.assertEqual(row.state, mon.ATTENTION)
                self.assertIn("（上一輪失敗）", row.detail)
                self.assertNotIn("最多顯示至", row.detail)

        bounded = mon._codex_failure_expiry_detail(self.NOW, self.NOW)
        self.assertIn("約 6 小時後隱藏", bounded)

    def test_turn_history_opens_only_the_users_regular_files(self):
        self.assertEqual([c.strip() for c in mon.CODEX_TURN_SQL.split("FROM")[0][6:].split(",")],
                         ["status", "started_at", "completed_at"])
        real = self._turn_db([("T1", "u", 0, "completed", 1, 2)], "odd ?#% name.sqlite")
        before = sorted(os.listdir(self.codex.parent))
        db = mon.open_codex_history(str(real))
        self.assertIsNotNone(db)   # special characters are URI-encoded
        self.assertEqual(mon.codex_turn({"db": db}, "T1"), ("completed", 1, 2))
        db.close()
        self.assertEqual(sorted(os.listdir(self.codex.parent)), before)   # no side files made
        link = self.codex.parent / "link.sqlite"
        os.symlink(real, link)
        for suffix in ("-wal", "-shm"):
            os.symlink(str(real) + suffix, str(link) + suffix)
        self.assertIsNone(mon.open_codex_history(str(link)))
        # Codex closed it: a WAL database checkpointed into its main file.
        closed = self.codex.parent / "closed.sqlite"
        writer = sqlite3.connect(closed)
        writer.execute("pragma journal_mode=wal")
        writer.execute("create table thread_turns(thread_id, turn_id, rollout_ordinal, status, "
                       "error_json, started_at, completed_at)")
        writer.execute("insert into thread_turns values('T1', 'u', 0, 'failed', '', 5, 6)")
        writer.commit()
        writer.close()
        listing = sorted(os.listdir(self.codex.parent))
        self.assertNotIn("closed.sqlite-wal", listing)
        db = mon.open_codex_history(str(closed))
        self.assertEqual(mon.codex_turn({"db": db}, "T1"), ("failed", 5, 6))
        db.close()
        self.assertEqual(sorted(os.listdir(self.codex.parent)), listing)   # opened immutable
        Path(str(closed) + "-wal").write_bytes(b"")   # only one side file: skipped
        self.assertIsNone(mon.open_codex_history(str(closed)))
        fifo = self.codex.parent / "fifo.sqlite"
        os.mkfifo(fifo)
        for suffix in ("-wal", "-shm"):
            Path(str(fifo) + suffix).write_bytes(b"")
        import signal
        def timeout(*_):
            raise TimeoutError("open_codex_history blocked on a FIFO")
        previous = signal.signal(signal.SIGALRM, timeout)
        self.addCleanup(signal.signal, signal.SIGALRM, previous)
        signal.alarm(2)
        try:
            self.assertIsNone(mon.open_codex_history(str(fifo)))
        finally:
            signal.alarm(0)

    def test_closed_codex_row_keeps_only_its_time_label(self):
        rows = [mon.OfficeRow("codex 主對話", mon.IDLE, label, 60.0, 0, key=f"k{n}", tui=True)
                for n, label in enumerate(("等待下一個指示（上一輪 12:16 結束）",
                                           "等待下一個指示（對話 10-24 01:46 開始）",
                                           "讀 a.py（task wp-1）"))]
        closed = [row.detail for row in mon._closed_codex_cli(rows, 0)]
        self.assertEqual(closed, ["推測 Codex CLI 已關閉（上一輪 12:16 結束）",
                                  "推測 Codex CLI 已關閉（對話 10-24 01:46 開始）",
                                  "推測 Codex CLI 已關閉"])

    def test_unknown_process_owner_does_not_break_the_roster(self):
        cli = mon.Process(41, 1, 41, "05:00", "S+", "Claude Code CLI", "claude", None)
        self.assertIsInstance(self.rows([cli]), list)

    def test_codex_cli_threads_show_closed_after_the_cli_exits_then_leave(self):
        self._codex("pm", {"id": "T1", "cwd": self.canonical, "thread_source": "user",
                           "originator": "codex-tui"}, ["task_started", "task_complete"], age=180)
        self._codex("dev", {"id": "S1", "cwd": self.canonical, "thread_source": "subagent",
                            "parent_thread_id": "T1", "agent_nickname": "Bacon",
                            "originator": "codex-tui", "agent_path": "/root/issue289_runtime_dev"},
                    ["task_started", "task_complete"], age=420)
        self._codex("app", {"id": "T2", "cwd": str(self.task), "thread_source": "user",
                            "originator": "codex_vscode"}, ["task_complete"], age=60)
        cli = mon.Process(9, 1, 9, "01:00", "S+", "Codex CLI (node launcher)", "node", "")
        self.assertEqual(self.by_name(self.rows([cli]))["codex 主對話"].state, mon.IDLE)
        rows = self.by_name(self.rows())
        self.assertEqual(rows["codex 主對話"].state, mon.CLOSED)
        self.assertTrue(rows["codex 主對話"].detail.startswith("推測 Codex CLI 已關閉"))
        self.assertEqual(rows["codex subagent Bacon"].state, mon.DONE)
        self.assertEqual(rows["codex developer"].state, mon.IDLE)   # not opened from the CLI
        self.assertEqual((rows["codex developer"].task, rows["codex developer"].role),
                         ("task wp-1", "developer"))   # a registered D6 root binds the role
        self._codex("pm", {"id": "T1", "cwd": self.canonical, "thread_source": "user",
                           "originator": "codex-tui"}, ["task_complete"],
                    age=mon.OFFICE_DONE_WINDOW + 60)
        rows = self.by_name(self.rows())
        self.assertNotIn("codex 主對話", rows)
        self.assertNotIn("codex subagent Bacon", rows)   # leaves with the PM that started it

    def test_reopened_codex_cli_attaches_only_to_its_newest_thread(self):
        self._codex("old", {"id": "T1", "cwd": self.canonical, "thread_source": "user",
                            "originator": "codex-tui"}, ["task_started", "turn_aborted"], age=90)
        self._codex("new", {"id": "T2", "cwd": self.canonical, "thread_source": "user",
                            "originator": "codex-tui"}, ["task_started", "task_complete"], age=10)
        cli = [mon.Process(8, 7, 8, "01:14", "S+", "Codex CLI", "codex", "Codex CLI (node launcher) 7"),
               mon.Process(7, 1, 7, "01:14", "S+", "Codex CLI (node launcher)", "node", "")]
        pms = [row for row in self.rows(cli) if row.name == "codex 主對話"]
        self.assertEqual(sorted(row.state for row in pms), sorted([mon.IDLE, mon.CLOSED]))
        self.assertEqual(next(row for row in pms if row.state == mon.IDLE).age, 10.0)
        self.assertFalse(any(line.startswith("⚠") for line in mon.office_lines(self.rows(cli))))

    def test_only_a_registered_d6_root_counts_as_a_task(self):
        loose = Path(self.tmp.name) / "llmtool-dev-workspaces" / "davidcattoolsproject" / "tasks" / "stray"
        os.makedirs(loose)
        self.assertEqual(mon._place(str(self.task), self.canonical), ("task", "wp-1"))
        self.assertEqual(mon._place(str(loose), self.canonical), ("unregistered", "stray"))
        self._register("stray", str(loose))
        os.chmod(Path(self.canonical) / ".git" / "llmtool-worktrees" / "stray.json", 0o644)
        mon._registry_cache.clear()
        self.assertEqual(mon._place(str(loose), self.canonical)[0], "unregistered")   # not owner-only
        self._register("stray", str(loose), lock_state="UNLOCKED")
        self.assertEqual(mon._place(str(loose), self.canonical)[0], "unregistered")
        self._codex("dev", {"id": "T9", "cwd": str(loose), "thread_source": "user"},
                    ["task_started"], age=5)
        row = self.by_name(self.rows())["codex 對話"]
        self.assertEqual((row.detail, row.role, row.task), ("未登記資料夾 stray", "", ""))

    def test_a_subagent_named_by_a_d6_session_ref_is_a_bound_developer(self):
        self._register("wp-1", str(self.task), session_ref="collaboration:/root/env2_flow_integration")
        self._codex("pm", {"id": "T1", "cwd": self.canonical, "thread_source": "user"},
                    ["task_started"], age=5)
        self._codex("dev", {"id": "S1", "cwd": self.canonical, "thread_source": "subagent",
                            "parent_thread_id": "T1", "agent_nickname": "Huygens",
                            "agent_path": "/root/env2_flow_integration"}, ["task_started"], age=5)
        self._codex("rev", {"id": "S2", "cwd": self.canonical, "thread_source": "subagent",
                            "parent_thread_id": "T1", "agent_nickname": "Locke",
                            "agent_path": "/root/env2_flow"}, ["task_started"], age=5)
        rows = self.by_name(self.rows())
        dev = rows["codex developer Huygens"]
        self.assertEqual((dev.role, dev.task), ("developer", "task wp-1"))
        self.assertIn("（task wp-1）", dev.detail)
        self.assertIn("codex subagent Locke", rows)   # a name prefix is not a match

    def test_long_first_line_and_a_thread_started_days_ago_are_still_shown(self):
        day = mon.time.strftime("%Y/%m/%d", mon.time.localtime(self.NOW - 3 * 86400))
        meta = {"type": "session_meta", "payload": {
            "id": "T1", "timestamp": "2026-10-20T01:00:00.000Z", "cwd": self.canonical,
            "thread_source": "user", "base_instructions": "x" * (mon.OFFICE_HEAD + 10)}}
        path = self.codex / day / "rollout-old.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(meta) + "\n" + json.dumps(
            {"type": "event_msg", "payload": {"type": "task_started"}}) + "\n", encoding="utf-8")
        os.utime(path, (self.NOW - 5, self.NOW - 5))
        rows = self.by_name(self.rows())
        self.assertEqual(rows["codex 主對話"].state, mon.WORKING)

    def test_review_records_without_timestamps_do_not_break_sorting(self):
        day = mon.time.strftime("%Y/%m/%d", mon.time.localtime(self.NOW))
        self._write(self.codex / day / "rollout-pm.jsonl", [
            {"type": "session_meta", "payload": {"id": "T1", "cwd": self.canonical,
                                                 "thread_source": "user"}},
            {"type": "response_item", "payload": {"type": "custom_tool_call", "call_id": "a",
                                                  "input": "claude -p 'Review PR #1 head 1234567'"}},
            {"timestamp": "2026-10-23T00:00:00Z", "type": "response_item",
             "payload": {"type": "custom_tool_call", "call_id": "b",
                         "input": "claude -p 'Review PR #2 head 7654321'"}}], age=5)
        found = mon.codex_reviews(str(self.codex / day / "rollout-pm.jsonl"), self.NOW)
        self.assertEqual(found, [])   # undated calls are dropped; dated ones expire
        mon.forget_reviews(set())
        self.assertEqual(mon._review_cache, {})

    def test_codex_pm_and_subagents_show_role_task_and_state(self):
        self._codex("pm", {"id": "T1", "cwd": self.canonical, "thread_source": "user"},
                    ["task_started", "task_complete"], age=400)
        self._codex("dev", {"id": "S1", "cwd": self.canonical, "thread_source": "subagent",
                            "parent_thread_id": "T1", "agent_nickname": "Huygens",
                            "agent_path": "/root/env2_flow_integration"},
                    ["task_started"], age=30)
        self._codex("rev", {"id": "S2", "cwd": self.canonical, "thread_source": "subagent",
                            "parent_thread_id": "T1", "agent_nickname": "Locke",
                            "agent_path": "/root/formal_review_pr285"},
                    ["task_started", "task_complete"], age=120)
        self._codex("old", {"id": "S3", "cwd": self.canonical, "thread_source": "subagent",
                            "parent_thread_id": "T1", "agent_nickname": "Old",
                            "agent_path": "/root/old_review"},
                    ["task_started", "task_complete"], age=mon.OFFICE_DONE_WINDOW + 60)
        self._codex("elsewhere", {"id": "X", "cwd": "/somewhere/else"}, ["task_started"], age=1)
        rows = self.by_name(self.rows())
        self.assertEqual(rows["codex 主對話"].state, mon.WORKING)
        self.assertIn("env2 flow integration", rows["codex 主對話"].detail)
        self.assertEqual(rows["codex subagent Huygens"].state, mon.WORKING)
        self.assertEqual(rows["codex subagent Locke"].state, mon.DONE)
        self.assertNotIn("codex subagent Old", rows)
        self.assertEqual(sum(name.startswith("codex") for name in rows), 3)
        self.assertEqual((rows["codex 主對話"].role, rows["codex 主對話"].main), ("", True))
        # no role is guessed from a name or folder, so nothing is marked "?"
        self.assertFalse(any("?" in name for name in rows))
        self.assertNotIn("推測", mon.office_lines(list(rows.values()))[-1])
        self.assertNotIn("codex reviewer agent", rows)

    def test_started_but_silent_subagent_is_reported_as_unresponsive(self):
        self._codex("pm", {"id": "T1", "cwd": self.canonical, "thread_source": "user"},
                    ["task_complete"], age=10_000)
        self._codex("dev", {"id": "S1", "cwd": str(self.task), "thread_source": "subagent",
                            "parent_thread_id": "T1", "agent_nickname": "Slow",
                            "agent_path": "/root/slow_dev"},
                    ["task_started"], age=mon.OFFICE_STALL + 5)
        rows = self.by_name(self.rows())
        self.assertEqual(rows["codex subagent Slow"].state, mon.STALLED)
        self.assertEqual(rows["codex 主對話"].state, mon.IDLE)
        self.assertEqual(rows["codex 主對話"].detail, "等待下一個指示")

    def test_two_codex_main_threads_in_the_canonical_root_are_dated_and_flagged(self):
        self._codex("pm1", {"id": "T1", "cwd": self.canonical, "thread_source": "user",
                            "timestamp": "2026-10-22T11:23:25.316Z"}, ["task_started"], age=5)
        self._codex("pm2", {"id": "T2", "cwd": self.canonical, "thread_source": "user",
                            "timestamp": "2026-10-22T21:49:41.000Z"}, ["task_started"], age=5)
        rows = [row for row in self.rows() if row.name == "codex 主對話"]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all("開始）" in row.detail for row in rows))
        self.assertNotEqual(rows[0].detail, rows[1].detail)
        self.assertTrue(any(line.startswith("⚠ 有 2 個 Codex 主對話")
                            for line in mon.office_lines(self.rows())))

    def test_codex_turn_longer_than_the_tail_is_still_working(self):
        meta = {"id": "T1", "cwd": self.canonical, "thread_source": "user"}
        filler = {"type": "response_item", "payload": {"type": "reasoning", "x": "y" * 1000}}
        path = self._codex("pm", meta, ["task_started"], age=200)
        def grow(*markers):
            with open(path, "a", encoding="utf-8") as handle:
                for kind in markers:
                    handle.write(json.dumps({"type": "event_msg", "payload": {"type": kind}}) + "\n")
                handle.writelines(json.dumps(filler) + "\n"
                                  for _ in range(mon.OFFICE_TAIL // 1000 + 10))
            os.utime(path, (self.NOW - 200, self.NOW - 200))
            return self.by_name(self.rows())["codex 主對話"].state
        mon._turn_cache.clear()
        self.assertEqual(grow(), mon.WORKING)   # the start marker is now before the tail
        self.assertEqual(grow("task_complete"), mon.IDLE)
        self.assertEqual(grow("task_started"), mon.WORKING)

    def test_codex_main_shows_what_it_is_doing(self):
        item = lambda payload: {"type": "event_msg", "payload": payload}
        call = lambda name, **kw: {"type": "response_item",
                                   "payload": {"type": kind_of(name), "name": name, **kw}}
        kind_of = lambda name: "custom_tool_call" if name == "exec" else "function_call"
        done = lambda parsed, cwd=self.canonical: item({"type": "item_completed", "item": {
            "type": "CommandExecution", "cwd": cwd, "aggregated_output": "TOOL-OUTPUT-SECRET",
            "command": "sed -n 1,9p secret-token=abc", "parsed_cmd": parsed}})
        meta = {"id": "T1", "cwd": self.canonical, "thread_source": "user"}
        def detail(*events):
            self._codex("pm", meta, ["task_started"], age=5)
            path = next(self.codex.rglob("rollout-pm.jsonl"))
            with open(path, "a", encoding="utf-8") as handle:
                handle.writelines(json.dumps(event) + "\n" for event in events)
            os.utime(path, (self.NOW - 5, self.NOW - 5))
            return self.by_name(self.rows())["codex 主對話"].detail.split("（")[0]
        self.assertEqual(detail(), "處理中")
        self.assertEqual(detail(done([{"type": "read", "name": "a.py", "path": "/x/a.py"},
                                      {"type": "read", "name": "b.md", "path": "b.md"}])),
                         "讀 a.py 等 2 個檔")
        self.assertEqual(detail(done([{"type": "search", "query": "secret-token=abc"}])),
                         "搜尋程式碼")
        self.assertEqual(detail(done([{"type": "unknown"}], cwd=str(self.task))),
                         "執行指令")
        self.assertIn(f"task {self.task.name}", self.by_name(self.rows())["codex 主對話"].detail)
        self.assertEqual(detail(call("exec", input='tools.write_stdin({session_id:3})')),
                         "等指令跑完")
        self.assertEqual(detail(done([]), call("wait_agent", arguments="{}")),
                         "等 subagent 回報")
        self.assertEqual(detail(done([]), item({"type": "item_completed",
                                                "item": {"type": "FileChange", "changes": "SECRET"}})),
                         "改檔案")
        text = "".join(row.detail for row in self.rows())
        self.assertNotIn("SECRET", text)
        self.assertNotIn("secret-token", text)

    def test_claude_session_title_and_subagents(self):
        project = self.claude / "proj"
        self._write(project / "s1.jsonl", [
            {"type": "user", "cwd": self.canonical, "message": "PROMPT-SECRET"},
            {"type": "ai-title", "aiTitle": "Review PR 285 token=abc"},
        ], age=10)
        sub = project / "s1" / "subagents"
        self._write(sub / "agent-a1.jsonl", [{"type": "user", "message": "PROMPT-SECRET"}], age=20)
        (sub / "agent-a1.meta.json").write_text(json.dumps(
            {"description": "Fix PR 273 review", "finished": False}), encoding="utf-8")
        self._write(sub / "agent-a2.jsonl", [{}], age=600)
        (sub / "agent-a2.meta.json").write_text(json.dumps(
            {"description": "Old audit", "finished": True}), encoding="utf-8")
        self._write(self.claude / "dev" / "s2.jsonl", [
            {"type": "user", "cwd": str(self.task)}, {"type": "ai-title", "aiTitle": "Build env cache"},
        ], age=4000)
        self._write(self.claude / "other" / "s3.jsonl", [{"cwd": "/elsewhere"}], age=1)
        rows = self.rows()
        names = [row.name for row in rows]
        self.assertEqual(names.count("claude subagent"), 2)
        reviewer = self.by_name(rows)["claude 互動"]
        self.assertEqual((reviewer.state, reviewer.age), (mon.WORKING, 10.0))
        self.assertNotIn("token=abc", reviewer.detail)
        developer = self.by_name(rows)["claude developer（互動）"]
        self.assertEqual((developer.state, developer.detail), (mon.IDLE, "Build env cache"))
        subs = {row.detail: row.state for row in rows if row.name == "claude subagent"}
        self.assertEqual(subs, {"Fix PR 273 review": mon.WORKING, "Old audit": mon.DONE})

    def test_claude_cli_registry_status_beats_the_session_file_guess(self):
        result = {"type": "user", "cwd": str(self.task), "message": {"content": [
            {"type": "tool_result", "content": "x"}]}}
        sessions = self.claude.parent / "sessions"
        sessions.mkdir()
        cli = mon.Process(41, 1, 41, "05:00", "S+", "Claude Code CLI", "claude", "")
        for status, waiting, want in (("waiting", "permission prompt", (mon.ATTENTION, "等人確認權限，要你回（task wp-1）", 1200.0)),
                                      ("idle", None, (mon.IDLE, "task wp-1", 1200.0)),
                                      ("busy", None, (mon.WORKING, "等模型回覆（task wp-1）", 300.0))):
            with self.subTest(status):
                self._write(self.claude / "dev" / "s.jsonl", [result], age=300)
                (sessions / "41.json").write_text(json.dumps(
                    {"pid": 41, "sessionId": "s", "status": status, "waitingFor": waiting,
                     "statusUpdatedAt": (self.NOW - 1200) * 1000, "name": "dev-r4"}),
                    encoding="utf-8")
                row = self.by_name(self.rows([cli]))["claude developer（互動）"]
                self.assertEqual((row.state, row.detail, row.age),
                                 (want[0], want[1] + "｜session s dev-r4", want[2]))
        driven = mon.Process(41, 5718, 41, "05:00", "Ss+", "Claude Code CLI", "claude",
                             "Codex APP-SERVER 5718")
        (sessions / "41.json").write_text(json.dumps(
            {"pid": 41, "sessionId": "s", "status": "waiting", "waitingFor": "permission prompt"}),
            encoding="utf-8")
        row = self.by_name(self.rows([driven]))["claude developer（互動）"]
        self.assertTrue(row.detail.startswith("等人確認權限，要PM（Codex）回"), row.detail)

    def test_quiet_claude_session_whose_tool_result_is_unanswered_waits_on_the_model(self):
        result = {"type": "user", "cwd": str(self.task), "message": {"content": [
            {"type": "tool_result", "content": "FILE-SECRET"}]}}
        answered = {"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}}
        for name, entries, age, want in (
                ("wait", [result], 300, (mon.WORKING, "等模型回覆")),
                ("hung", [result], mon.OFFICE_STALL + 60, (mon.STALLED, "等模型回覆超過 15 分鐘")),
                ("idle", [result, answered], 300, (mon.IDLE, "task wp"))):
            with self.subTest(name):
                path = self.claude / "dev" / "s.jsonl"
                self._write(path, entries, age=age)
                row = self.by_name(self.rows())["claude developer（互動）"]
                self.assertEqual((row.state, row.detail[:len(want[1])]), want)
                self.assertNotIn("FILE-SECRET", row.detail)

    def test_claude_cli_sessions_show_closed_after_the_cli_exits_then_leave(self):
        for sid, age in (("live", 300), ("recent", 200), ("old", 3 * 3600)):
            self._write(self.claude / "proj" / f"{sid}.jsonl", [
                {"type": "user", "cwd": self.canonical, "entrypoint": "cli"},
                {"type": "ai-title", "aiTitle": f"title {sid}"}], age=age)
        self._write(self.claude / "proj" / "old" / "subagents" / "agent-a1.jsonl", [{}], age=60)
        (self.claude / "proj" / "old" / "subagents" / "agent-a1.meta.json").write_text(
            json.dumps({"description": "Old sub", "finished": False}), encoding="utf-8")
        self._write(self.claude / "proj" / "app.jsonl", [
            {"type": "user", "cwd": self.canonical, "entrypoint": "claude-desktop"}], age=3600)
        cli = mon.Process(41, 1, 41, "05:00", "S+", "Claude Code CLI", "claude", "")
        # No registry: nothing is judged.
        self.assertEqual([row.state for row in self.rows([cli]) if row.name == "claude 互動"],
                         [mon.IDLE] * 4)
        sessions = self.claude.parent / "sessions"
        sessions.mkdir()
        (sessions / "41.json").write_text(json.dumps({"pid": 41, "sessionId": "live"}),
                                          encoding="utf-8")
        (sessions / "77.json").write_text(json.dumps({"pid": 77, "sessionId": "recent"}),
                                          encoding="utf-8")   # stale: pid 77 is gone
        rows = {row.detail: row.state for row in self.rows([cli]) if row.kind != "worker"}
        self.assertEqual(rows, {"title live": mon.IDLE,
                                "推測 Claude CLI 已關閉（title recent）": mon.CLOSED,
                                "（無標題）": mon.IDLE})   # desktop session is not judged
        # An npm install runs the CLI as a node launcher; it is the same CLI.
        node = mon.Process(41, 1, 41, "05:00", "S+", "Claude Code CLI (node launcher)", "node", "")
        self.assertEqual({row.detail: row.state for row in self.rows([node])}["title live"], mon.IDLE)
        # A running CLI with no readable entry keeps the most recent unnamed session.
        other = mon.Process(42, 1, 42, "05:00", "S+", "Claude Code CLI", "claude", "")
        rows = {row.detail: row.state for row in self.rows([cli, other])}
        self.assertEqual(rows["title recent"], mon.IDLE)
        self.assertNotIn("Old sub", rows)

    def test_working_claude_shows_its_latest_action_description_only(self):
        call = lambda description: {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "MODEL-OUTPUT-SECRET"},
            {"type": "tool_use", "name": "Bash", "input": {
                "command": "echo RAW-ARGV-SECRET /Users/davidsun", "description": description}}]}}
        project = self.claude / "proj"
        self._write(project / "s1.jsonl", [
            {"type": "user", "cwd": self.canonical, "entrypoint": "cli"},
            {"type": "ai-title", "aiTitle": "Old title"},
            call("Run monitor tests"), call("Open Issue for gw failures token=abc"),
            {"type": "user", "message": {"content": [{"type": "tool_result",
                                                       "content": "TOOL-OUTPUT-SECRET"}]}},
        ], age=5)
        sub = project / "s1" / "subagents"
        self._write(sub / "agent-a1.jsonl", [call("Fix flaky test")], age=5)
        (sub / "agent-a1.meta.json").write_text(json.dumps(
            {"description": "PR 288 repair", "finished": False}), encoding="utf-8")
        self._write(self.claude / "idle" / "s2.jsonl", [
            {"type": "user", "cwd": self.canonical}, {"type": "ai-title", "aiTitle": "Idle chat"},
            call("Something long ago")], age=600)
        rows = self.rows()
        details = {row.detail for row in rows}
        self.assertIn("Open Issue for gw failures [已遮蔽]", details)
        self.assertIn("PR 288 repair：Fix flaky test", details)
        self.assertIn("Idle chat", details)   # idle sessions keep their title
        text = "\n".join(mon.office_lines(rows))
        for secret in SECRETS:
            self.assertNotIn(secret, text)

    def test_one_shot_claude_runs_finish_and_are_not_guessed_onto_a_pm(self):
        self._codex("pm", {"id": "T1", "cwd": self.canonical, "thread_source": "user"},
                    ["task_started"], age=5)
        self._codex("dev", {"id": "S1", "cwd": self.canonical, "thread_source": "subagent",
                            "parent_thread_id": "T1", "agent_nickname": "Franklin",
                            "agent_path": "/root/tui_dev"}, ["task_started"], age=5)
        self._write(self.claude / "a" / "run1.jsonl", [
            {"type": "user", "cwd": self.canonical, "entrypoint": "sdk-cli"},
            {"type": "ai-title", "aiTitle": "Review PR 284"}], age=300)
        self._write(self.claude / "a" / "run2.jsonl", [
            {"type": "user", "cwd": self.canonical, "entrypoint": "sdk-cli"}],
            age=mon.OFFICE_DONE_WINDOW + 60)
        # an interactive session the PM pinged with `claude --resume <id> -p`
        self._write(self.claude / "a" / "live.jsonl", [
            {"type": "user", "cwd": self.canonical, "entrypoint": "cli"},
            {"type": "ai-title", "aiTitle": "Owner chat"},
            {"type": "user", "cwd": self.canonical, "entrypoint": "sdk-cli"}], age=500)
        rows = self.rows()
        run = next(row for row in rows if row.detail == "Review PR 284")
        # the session does not say who started it, and its title binds no role
        self.assertEqual((run.name, run.state, run.kind, run.group, run.role),
                         ("claude 單次呼叫", mon.DONE, "subagent", "", ""))
        self.assertEqual(sum(row.name == "claude 單次呼叫" for row in rows), 1)   # run2 hidden
        live = next(row for row in rows if row.detail == "Owner chat")
        self.assertEqual((live.name, live.state, live.kind, live.group),
                         ("claude 互動", mon.IDLE, "agent", ""))
        lines = mon.office_lines(rows)
        self.assertEqual(lines[0], "辦公室 · 2 位工作中 · 共 7 位")
        live = lines.index(f"── {mon.LIVE_SECTION} ──")
        pm = next(i for i, line in enumerate(lines) if "codex 主對話" in line)
        self.assertLess(live, pm)
        self.assertIn("└ codex subagent Franklin", lines[pm + 1])
        self.assertTrue(any("claude 單次呼叫" in line for line in lines))
        self.assertIn(f"── {mon.STATUS_SECTION} ──", lines)
        self.assertLess(lines.index(f"── {mon.STATUS_SECTION} ──"),
                        next(i for i, line in enumerate(lines) if line.startswith("agy")))

    def test_unpersisted_claude_run_started_by_codex_is_shown_from_its_process(self):
        self._codex("pm", {"id": "T1", "cwd": self.canonical, "thread_source": "user"},
                    ["task_started"], age=5)
        run = mon.Process(40, 5718, 40, "02:10", "S", "Claude Code CLI", "claude",
                          "Codex APP-SERVER 5718")
        mine = mon.Process(41, 1, 41, "13:00:00", "S+", "Claude Code CLI", "claude", "")
        rows = self.rows([run, mine])
        runs = [row for row in rows if row.name == "claude 單次呼叫"]
        self.assertEqual(len(runs), 1)
        self.assertEqual((runs[0].state, runs[0].kind, runs[0].age, runs[0].group, runs[0].evidence),
                         (mon.WORKING, "subagent", 130.0, "", "程序"))
        self.assertIn("看不出是哪個對話", runs[0].detail)

    def test_interactive_claude_session_started_by_codex_is_not_a_one_shot_run(self):
        # Codex can drive an interactive Claude TUI; it registers its session
        # and is shown from that record, so it must not also appear as a run.
        sessions = self.claude.parent / "sessions"
        sessions.mkdir()
        (sessions / "40.json").write_text(json.dumps({"pid": 40, "sessionId": "dev"}),
                                          encoding="utf-8")
        tui = mon.Process(40, 5718, 40, "07:00:00", "Ss+", "Claude Code CLI", "claude",
                          "Codex APP-SERVER 5718")
        run = mon.Process(42, 5718, 42, "00:30", "S", "Claude Code CLI", "claude",
                          "Codex APP-SERVER 5718")
        rows = self.rows([tui, run])
        runs = [row for row in rows if row.name == "claude 單次呼叫"]
        self.assertEqual([row.age for row in runs], [30.0])
        state, _, _ = mon.track_one_shot({}, [tui, run], 1000.0, str(sessions))
        state, finished, ended = mon.track_one_shot(state, [], 1010.0, str(sessions))
        self.assertEqual((len(finished), len(ended)), (1, 1))   # only the -p run ended

    def test_claude_management_command_is_not_a_model_run(self):
        self.assertEqual(mon.classify("claude agents --json"), "Claude Code CLI command")
        self.assertEqual(mon.classify("/usr/local/bin/claude --version"), "Claude Code CLI command")
        self.assertEqual(mon.classify("claude -p --model sonnet review"), "Claude Code CLI")
        self.assertEqual(mon.classify("claude"), "Claude Code CLI")
        query = mon.Process(40, 5718, 40, "00:01", "S", "Claude Code CLI command", "claude",
                            "Codex APP-SERVER 5718")
        self.assertFalse(any(row.name == "claude 單次呼叫" for row in self.rows([query])))
        state, _, _ = mon.track_one_shot({}, [query], 1000.0)
        self.assertEqual(state, {})

    def test_review_target_reads_the_pr_a_call_fetches(self):
        self.assertEqual(mon._review_target('review_diff="$(gh -R o/r pr diff 309)" claude --print'),
                         ("309", ""))
        self.assertEqual(mon._review_target("gh --repo o/r pr diff 41"), ("41", ""))
        self.assertEqual(mon._review_target("gh pr diff --repo=o/r 42"), ("42", ""))
        self.assertEqual(mon._review_target("gh pr view #12 --json files; head abc1234"), ("12", "abc1234"))
        self.assertEqual(mon._review_target("gh pr view https://github.com/o/r/pull/77 --json files"), ("77", ""))
        self.assertEqual(mon._review_target("gh api repos/o/r/pulls/78/files"), ("78", ""))
        # The prompt may cite other PRs; the fetched one is under review.
        self.assertEqual(mon._review_target("context: PR #298 merged. gh pr diff 309"), ("309", ""))
        self.assertEqual(mon._review_target("Review PR #5 at head abc1234"), ("5", "abc1234"))
        self.assertEqual(mon._review_target("gh issue view 289"), ("", ""))

    def test_review_target_ignores_cited_urls_and_inlined_diffs(self):
        # A cited URL is not a fetch.
        self.assertEqual(mon._review_target("Review PR #312 head abc1234; related https://github.com/o/r/pull/298"),
                         ("312", "abc1234"))
        # An inlined diff is the material under review, even when it mentions fetches.
        prompt = ("Review PR #312 head abc1234\n=== DIFF ===\ndiff --git a/x b/x\n"
                  "+# `gh -R owner/repo pr diff 309`\n+see /pull/77\n")
        self.assertEqual(mon._review_target(prompt), ("312", "abc1234"))
        # Only fetches are cut at the diff; a head named after it still counts.
        self.assertEqual(mon._review_target("Review PR #312\ndiff --git a/x b/x\n+x\nExact head: abc1234"),
                         ("312", "abc1234"))
        # Fetches that disagree fall back to the prompt's own PR.
        self.assertEqual(mon._review_target("PR #5: gh pr diff 6; gh pr diff 7"), ("5", ""))
        self.assertEqual(mon._review_target("gh pr diff 6; gh pr view 6 --json headRefOid"), ("6", ""))

    def _calls(self, subs, live_calls, verdicts=None):
        main = mon.OfficeRow("codex 主對話", mon.WORKING, "", 1.0, 0, key="codex:T1", main=True)
        listed = []   # one rollout file in all, though codex_rows lists CODEX_DAYS folders

        def glob(pattern):
            if "rollout" not in pattern or listed:
                return []
            listed.append(pattern)
            return [pattern]
        with patch.object(mon, "_codex_row", return_value=("main", "T1", main, "")), \
             patch.object(mon, "_review_rows", return_value=subs), \
             patch.object(mon.glob, "glob", side_effect=glob), \
             patch.object(mon.os.path, "getmtime", return_value=self.NOW):
            rows = mon.codex_rows(self.NOW, str(self.codex), self.canonical, live_calls=live_calls,
                                  verdicts=verdicts)
        return {row.detail: row.state for row in rows if row.key == "review"}, next(row for row in rows if row.main)

    def _call(self, pr, age):
        return (mon.OfficeRow("claude reviewer", mon.WORKING, f"審 PR #{pr} @abc1234（單次呼叫執行中）",
                              age, 30, key="review", kind="subagent"), str(pr), "abc1234", self.NOW - age)

    def test_review_call_without_a_live_process_has_ended(self):
        old, newer, young = self._call(9, 360.0), self._call(8, 30.0), self._call(7, 5.0)
        # One live process started 4s ago: only the young call can own it.
        calls, boss = self._calls([old, newer, young], [-4.0])
        self.assertEqual(calls, {"審 PR #9 @abc1234：已結束（沒讀 GitHub，不知道結論）": mon.DONE,
                                 "審 PR #8 @abc1234：已結束（沒讀 GitHub，不知道結論）": mon.DONE,
                                 "審 PR #7 @abc1234（單次呼叫執行中）": mon.WORKING})
        self.assertNotIn("PR #9", boss.detail)   # an ended call is not counted as in progress

    def test_review_calls_that_finish_out_of_order(self):
        # A (old, long review) still runs; B (newer) finished and was never read.
        a, b = self._call(9, 360.0), self._call(8, 300.0)
        calls, boss = self._calls([a, b], [-359.0])
        self.assertEqual(calls, {"審 PR #9 @abc1234（單次呼叫執行中）": mon.WORKING,
                                 "審 PR #8 @abc1234：已結束（沒讀 GitHub，不知道結論）": mon.DONE})
        self.assertIn("PR #9", boss.detail)

    def test_review_call_reconciliation_edges(self):
        a, b = self._call(9, 360.0), self._call(8, 300.0)
        running = {"審 PR #9 @abc1234（單次呼叫執行中）": mon.WORKING,
                   "審 PR #8 @abc1234（單次呼叫執行中）": mon.WORKING}
        self.assertEqual(self._calls([a, b], None)[0], running)          # process table unread
        self.assertEqual(self._calls([a, b], [-359.0, -299.0, -10.0])[0], running)   # more processes than calls
        # A process started late (e.g. after a prompt was built) still belongs to its call.
        self.assertEqual(self._calls([a, b], [-200.0, -100.0])[0], running)
        ended = {"審 PR #9 @abc1234：已結束（沒讀 GitHub，不知道結論）": mon.DONE,
                 "審 PR #8 @abc1234：已結束（沒讀 GitHub，不知道結論）": mon.DONE}
        self.assertEqual(self._calls([a, b], [])[0], ended)              # nothing running
        # An ended call shows the first verdict posted on GitHub for its PR and head
        # after the call started; none posted is said plainly.
        head = frozenset({"abc1234"})
        verdicts = {"9": [(self.NOW - 400, "PASS", head), (self.NOW - 100, "CHANGES_REQUIRED", head)],
                    "8": [(self.NOW - 900, "PASS", head)]}   # older than the call
        self.assertEqual(self._calls([a, b], [], verdicts)[0],
                         {"審 PR #9 @abc1234：CHANGES_REQUIRED": mon.DONE,
                          f"審 PR #8 @abc1234：{mon.NO_VERDICT}": mon.DONE})
        # a retry for the same PR and head owns the verdict posted after it started
        retry = self._call(9, 60.0)
        verdicts["9"].append((self.NOW - 30, "PASS", head))
        self.assertEqual(self._calls([a, retry], [], verdicts)[0],
                         {"審 PR #9 @abc1234：CHANGES_REQUIRED": mon.DONE,
                          "審 PR #9 @abc1234：PASS": mon.DONE})
        verdicts["9"] = [(self.NOW - 30, "PASS", head)]
        self.assertEqual(self._calls([a, retry], [], verdicts)[0],
                         {f"審 PR #9 @abc1234：{mon.NO_VERDICT}": mon.DONE,
                          "審 PR #9 @abc1234：PASS": mon.DONE})

    def test_office_rows_reads_live_call_start_times(self):
        run = mon.Process(40, 5718, 40, "01:00", "S", "Claude Code CLI", "claude", "Codex APP-SERVER 5718")
        seen = {}

        def fake(now, root=None, canonical=None, live_calls=None, verdicts=None):
            seen["calls"] = live_calls
            return []
        with patch.object(mon, "codex_rows", side_effect=fake):
            self.rows([run])
            self.assertEqual(seen["calls"], [-60.0])
            self.rows([])   # the table was read: nothing is running
            self.assertEqual(seen["calls"], [])

    def test_monitor_version_from_git(self):
        def run(results):
            calls = iter(results)
            return lambda *a, **k: mon.subprocess.CompletedProcess(a, *next(calls))
        cases = [
            ([(0, "blob1\n"), (0, "blob1\n"), (0, "abc1234 10-24 13:50\n")], "abc1234 10-24 13:50"),
            ([(0, "blob2\n"), (0, "blob1\n"), (0, "abc1234 10-24 13:50\n")], "本機修改 fffffff"),
            ([(128, ""), (128, ""), (128, "")], "本機修改 fffffff"),
            # Both lookups failing is not a match, even when the log still answers.
            ([(128, ""), (128, ""), (0, "abc1234 10-24 13:50\n")], "本機修改 fffffff"),
        ]
        for results, expected in cases:
            mon.monitor_version.cache_clear()
            with patch.object(mon, "_GIT_RUN", side_effect=run(results)) as git, \
                 patch.object(mon, "script_digest", return_value="fffffff"):
                self.assertEqual(mon.monitor_version(), expected)
            self.assertIn("--no-filters", git.call_args_list[0].args[0])
        with patch.object(mon, "_GIT_RUN", side_effect=OSError("no git")):
            mon.monitor_version.cache_clear()
            self.assertTrue(mon.monitor_version().startswith("本機修改 "))
        mon.monitor_version.cache_clear()

    def test_monitor_notices_new_code_once(self):
        digests = iter(["aaaaaaa", "aaaaaaa", "?", "bbbbbbb", "ccccccc"])
        with patch.object(mon, "script_digest", side_effect=lambda: next(digests)), \
             patch.object(mon, "monitor_version", return_value="abc1234 10-24 13:50"), \
             patch.object(mon, "collect", return_value=mon.Snapshot([], {}, [], frozenset())), \
             patch.object(mon, "scan_tasks", return_value=[]), \
             patch.object(mon, "office_rows", return_value=[]), \
             patch.object(mon, "d6_task_lines", return_value=[]):
            monitor = mon.Monitor()
            warnings = []
            for _ in range(4):
                monitor.office_due = 0.0
                warnings.append(sum("監控程式已更新" in text for _, _, text in monitor.poll()))
                warnings[-1] = (warnings[-1], monitor.stale)
        # unchanged, unreadable, changed (warn once), changed again (no repeat)
        self.assertEqual(warnings, [(0, False), (0, False), (1, True), (0, True)])

    def test_unreadable_script_at_start_is_not_a_change(self):
        digests = iter(["?", "aaaaaaa", "aaaaaaa"])
        with patch.object(mon, "script_digest", side_effect=lambda: next(digests)), \
             patch.object(mon, "monitor_version", return_value="abc1234 10-24 13:50"), \
             patch.object(mon, "collect", return_value=mon.Snapshot([], {}, [], frozenset())), \
             patch.object(mon, "scan_tasks", return_value=[]), \
             patch.object(mon, "office_rows", return_value=[]), \
             patch.object(mon, "d6_task_lines", return_value=[]):
            monitor = mon.Monitor()
            for _ in range(2):
                monitor.office_due = 0.0
                monitor.poll()
        self.assertEqual((monitor.digest, monitor.stale), ("aaaaaaa", False))

    def test_short_one_shot_run_stays_visible_as_done_after_it_exits(self):
        run = mon.Process(40, 5718, 40, "00:05", "S", "Claude Code CLI", "claude",
                          "Codex APP-SERVER 5718")
        state, finished, ended = mon.track_one_shot({}, [run], 1000.0)
        self.assertEqual((finished, ended), ([], []))
        state, finished, ended = mon.track_one_shot(state, [run], 1012.0)
        state, finished, ended = mon.track_one_shot(state, [], 1013.0)
        self.assertEqual(ended, ["claude 單次呼叫（由 Codex 啟動）結束（約 17 秒）"])
        self.assertEqual(finished, [(17.0, 0.0)])
        state, finished, ended = mon.track_one_shot(state, [], 1013.0 + 60)
        self.assertEqual((finished, ended), ([(17.0, 60.0)], []))
        state, finished, _ = mon.track_one_shot(state, [], 1013.0 + mon.OFFICE_DONE_WINDOW + 1)
        self.assertEqual((state, finished), ({}, []))
        self._codex("pm", {"id": "T1", "cwd": self.canonical, "thread_source": "user"},
                    ["task_started"], age=5)
        rows = mon.office_rows([], self.NOW, codex_root=str(self.codex), claude_root=str(self.claude),
                               canonical=self.canonical,
                               opencode_db="/nonexistent", cwd_of=lambda pid: None,
                               finished_runs=[(45.0, 120.0)])
        done = [row for row in rows if row.name == "claude 單次呼叫"]
        self.assertEqual(len(done), 1)
        self.assertEqual((done[0].state, done[0].age, done[0].group), (mon.DONE, 120.0, ""))
        self.assertIn("跑了約 45 秒", done[0].detail)

    def test_one_shot_review_takes_pr_and_head_from_the_call_and_verdict_from_github(self):
        ts = lambda age: mon.time.strftime("%Y-%m-%dT%H:%M:%S.000Z", mon.time.gmtime(self.NOW - age))
        def call(cid, text):
            return {"type": "custom_tool_call", "call_id": cid, "name": "exec", "input": text}
        events = [
            (900, call("c1", 'gh pr diff 283 | claude --print --tools "" "Review PR #283 head '
                             '18538d569d94fa7e5a06605dadf3a7305407b4de"')),
            # whatever the call printed is not read: only GitHub gives the verdict
            (899, {"type": "custom_tool_call_output", "call_id": "c1", "output": [
                {"type": "input_text", "text": "**Verdict: FAIL** PROMPT-SECRET"}]}),
            (800, call("c2", 'const r = await tools.exec_command({cmd:"review_dir=$(mktemp -d)\\n'
                             'cd \\"$review_dir\\"\\ngh pr diff 285 | claude -p --output-format json '
                             '\'Review PR #285, head c40c80acae77147190684a1ea8b66d744378ff41.\'"})')),
            # a review longer than the done window still shows its verdict
            (mon.OFFICE_DONE_WINDOW + 600, call("l1", "claude -p 'Review PR #281 head 281818a'")),
            (70, call("v1", "claude -p 'Review PR #282 head 2828282, run unittest -v'")),   # -v in the prompt
            (60, call("b1", "claude --resume abc -p 'status of PR #286'")),   # names no head
            (45, call("c6", "gh pr comment 288 --body 'shows `claude --print/-p` reviews'")),   # prose
            (40, call("c5", "claude --version 2>&1 | head -5; pgrep -alf 'claude -p|claude --print'")),
            (35, call("h1", 'tools.exec_command({cmd:"claude -p --help | rg -n \\"budget\\""})')),
            (30, call("c4", "claude -p 'Review PR #284 head 1ee2f21c8caca7c66dc497268759dc8c308adebf'")),
        ]
        day = mon.time.strftime("%Y/%m/%d", mon.time.localtime(self.NOW))
        items = [{"type": "session_meta", "payload": {"id": "T1", "cwd": self.canonical,
                                                      "thread_source": "user"}}]
        items += [{"type": "event_msg", "payload": {"type": "task_started"}}]
        items += [{"timestamp": ts(age), "type": "response_item", "payload": payload}
                  for age, payload in events]
        self._write(self.codex / day / "rollout-pm.jsonl", items, age=5)
        stamp = lambda age: mon.time.strftime("%Y-%m-%dT%H:%M:%SZ", mon.time.gmtime(self.NOW - age))
        comment = lambda pr, age, body, who="OWNER": {
            "issue_url": f"https://api.github.com/repos/o/r/issues/{pr}", "created_at": stamp(age),
            "author_association": who, "body": body}
        verdicts = mon.review_verdicts([
            comment(283, 850, "## Review\n**Verdict: PASS** at exact head `18538d569d94fa7e5a06`"),
            comment(283, 840, "**Verdict: FAIL** at head 18538d5", who="NONE"),   # untrusted
            comment(283, 830, "The earlier verdict: FAIL was stale; head 18538d5"),   # not a verdict line
            comment(999, 820, "**Verdict: FAIL** head 18538d5"),                   # another PR
            comment(281, 60, "**Verdict: PASS** at exact head 281818a"),
        ])
        # only trusted verdict lines are kept, each with every id its comment names
        self.assertEqual(verdicts, {
            "283": [(self.NOW - 850, "PASS", frozenset({"18538d5"}))],
            "999": [(self.NOW - 820, "FAIL", frozenset({"18538d5"}))],
            "281": [(self.NOW - 60, "PASS", frozenset({"281818a"}))]})

        # a call's verdict is the first one posted on its PR after it started that
        # names its head, however the comment is worded
        def outcome(pr, head, started, *posts, retry=None):
            row = mon.OfficeRow("claude reviewer", mon.WORKING, f"審 PR #{pr} @{head}（單次呼叫執行中）",
                                started, 30, key="review")
            return mon._review_result(row, pr, head, self.NOW - started, mon.review_verdicts(list(posts)),
                                      self.NOW, None if retry is None else self.NOW - retry).outcome
        own = comment(283, 500, "**Verdict: FAIL** at head 349c6f5")
        delta = comment(283, 100, "**Verdict: PASS** at head 18538d5, delta `349c6f5..18538d5`")
        self.assertEqual(outcome("283", "349c6f5", 900, own, delta), "FAIL")   # its own, not the next round
        self.assertEqual(outcome("283", "18538d5", 300, own, delta), "PASS")
        self.assertEqual(outcome("310", "c349854", 300, comment(   # a task id and a base come first
            310, 100, "Review Task 8cbb958d-4c4a base `a8f628f` → head `c349854`\n**Verdict: FAIL**")), "FAIL")
        self.assertEqual(outcome("311", "ec73d4d", 300, comment(
            311, 10, "**Verdict: PASS**\nExact head submitted: `ec73d4d1a2b3c4d5`")), "PASS")
        self.assertEqual(outcome("283", "18538d5", 300, comment(283, 400, "**Verdict: FAIL** head 18538d5")),
                         mon.NO_VERDICT)   # posted before the call started
        self.assertEqual(outcome("283", "18538d5", 300, comment(283, 100, "**Verdict: FAIL** head 28538d5")),
                         mon.NO_VERDICT)   # names another head
        self.assertEqual(outcome("283", "18538d5", 300, comment(283, 100, "**Verdict: FAIL** head 18538d5",
                                                                 who="NONE")), mon.NO_VERDICT)   # untrusted
        # a failed call does not take the verdict of a retry for the same head
        retried = comment(283, 100, "**Verdict: PASS** head 18538d5")
        self.assertEqual(outcome("283", "18538d5", 300, retried, retry=200), mon.NO_VERDICT)
        self.assertEqual(outcome("283", "18538d5", 200, retried), "PASS")
        edited = dict(comment(283, 850, "**Verdict: FAIL** head 18538d5"), updated_at=stamp(100))
        self.assertEqual(outcome("283", "18538d5", 300, edited), "FAIL")   # counts from its last edit
        # a forked subagent starts with a copy of the PM's history, its turns included,
        # stamped with the fork time: only calls from its own first turn on are its own
        started = lambda turn: {"timestamp": ts(50), "type": "event_msg",
                                "payload": {"type": "task_started", "turn_id": turn}}
        fork = [{"type": "session_meta", "payload": {"id": "01b0-fork", "forked_from_id": "T1"}},
                started("01a0-parent-turn"),
                {"timestamp": ts(50), "type": "response_item", "payload": events[0][1]},
                started("01b1-own-turn"),
                {"timestamp": ts(40), "type": "response_item",
                 "payload": call("f1", "claude -p 'Review PR #287 head 2222222bbbb'")}]
        self._write(self.codex / day / "rollout-fork.jsonl", fork, age=5)
        self.assertEqual([call[1:] for call in mon.codex_reviews(
            str(self.codex / day / "rollout-fork.jsonl"), self.NOW)], [("287", "2222222")])
        run = mon.Process(40, 5718, 40, "00:20", "S", "Claude Code CLI", "claude",
                          "Codex APP-SERVER 5718")
        with patch.object(mon, "_one_shot_runs", return_value=[run]):
            rows = mon.office_rows([run], self.NOW, codex_root=str(self.codex),
                                   claude_root=str(self.claude), canonical=self.canonical,
                                   opencode_db="/nonexistent", cwd_of=lambda pid: None,
                                   verdicts=verdicts)
        reviews = {row.detail: row.state for row in rows if row.name == "claude reviewer"}
        self.assertEqual(reviews, {"審 PR #283 @18538d5：PASS": mon.DONE,
                                   "審 PR #281 @281818a：PASS": mon.DONE,
                                   f"審 PR #285 @c40c80a：{mon.NO_VERDICT}": mon.DONE,
                                   f"審 PR #282 @2828282：{mon.NO_VERDICT}": mon.DONE,
                                   "審 PR #284 @1ee2f21（單次呼叫執行中）": mon.WORKING})
        self.assertTrue(all(row.task.startswith("PR #") and row.role == "reviewer"
                            for row in rows if row.name == "claude reviewer"))
        unbound = [row for row in rows if row.name == "claude 單次呼叫"]
        self.assertEqual([(row.detail, row.role) for row in unbound],
                         [("單次呼叫（呼叫裡沒寫 PR 和版本）：已結束", "")])
        text = "\n".join(mon.office_lines(rows))
        self.assertNotIn("PR #999", text)
        self.assertNotIn("PROMPT-SECRET", text)

    def test_worker_rows_come_from_the_dispatch_switches(self):
        rows = self.by_name(self.rows())
        self.assertEqual((rows["opencode developer"].state, rows["opencode developer"].detail,
                          rows["opencode developer"].authority),
                         (mon.PAUSED, "Owner 暫停派工（10-22）", "Owner"))
        self.assertNotIn("codex reviewer agent", rows)   # spawned by the PM, not a standing Worker
        app = mon.Process(5, 1, 5, "2-00:00:00", "S", "Antigravity App (AGY)", "Antigravity", "")
        agy = self.by_name(self.rows([app]))["agy"]
        self.assertEqual((agy.state, agy.detail), (mon.PAUSED, "Owner 暫停派工（10-22）；App 開著"))
        self.assertEqual((rows["公司 gw"].state, rows["公司 gw"].detail), (mon.IDLE, "可派工"))
        self.assertIsNone(rows["agy"].age)

    def test_missing_or_invalid_dispatch_says_it_cannot_tell_and_never_falls_back(self):
        (Path(self.canonical) / "tools" / "llmtool" / "worker-dispatch.json").unlink()
        mon._dispatch_cache.clear()
        rows = self.by_name(self.rows())
        for name in ("opencode developer", "agy", "公司 gw"):
            self.assertEqual((rows[name].state, rows[name].detail.split("；")[0]),
                             (mon.UNCHECKED, mon.DISPATCH_PROBLEMS["missing"]))
        self._write_dispatch({}, raw="{not json")
        rows = self.by_name(self.rows())
        self.assertEqual(rows["agy"].detail, mon.DISPATCH_PROBLEMS["invalid"])
        # The llmtool gate is all or nothing: one bad entry refuses every Worker.
        for bad in ({"agy": {"dispatch": "MAYBE"}}, {"agy": {"decided_by": "PM"}},
                    {"opencode-developer": None}, {"codex-reviewer-agent": DISPATCH["agy"]}):
            with self.subTest(bad=bad):
                self._write_dispatch(bad)
                rows = self.by_name(self.rows())
                for name in ("opencode developer", "agy", "公司 gw"):
                    self.assertEqual((rows[name].state, rows[name].detail.split("；")[0]),
                                     (mon.UNCHECKED, mon.DISPATCH_PROBLEMS["rejected"]))

    def _opencode_db(self, sessions, messages=()):
        db = Path(tempfile.mkdtemp(dir=self.tmp.name)) / "opencode.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE session_v2 (id TEXT, parent_id TEXT, directory TEXT, "
                     "title TEXT, time_updated INTEGER, time_idle INTEGER, idle_outcome TEXT)")
        conn.execute("CREATE TABLE session_message (session_id TEXT, type TEXT, seq INTEGER, "
                     "data TEXT)")
        conn.executemany("INSERT INTO session_message VALUES (?,?,?,?)", messages)
        conn.execute("CREATE TABLE credential (secret TEXT)")
        conn.execute("INSERT INTO credential VALUES ('CREDENTIAL-SECRET')")
        conn.executemany("INSERT INTO session_v2 VALUES (?,?,?,?,?,?,?)",
                         [tuple(row) + (None,) * (7 - len(row)) for row in sessions])
        conn.commit()
        conn.close()
        return str(db)

    def test_opencode_server_alone_is_not_a_member_and_dispatch_row_stays(self):
        server = mon.Process(1, 0, 1, "1:00", "S", "OpenCode SERVER", "opencode.exe", "")
        rows = self.by_name(mon.office_rows([server], self.NOW, codex_root=str(self.codex),
                                            claude_root=str(self.claude),
                                            canonical=self.canonical, opencode_db="/nonexistent",
                                            cwd_of=lambda pid: None))
        self.assertEqual(rows["opencode developer"].state, mon.PAUSED)
        self.assertFalse(any(name.startswith("opencode cli") for name in rows))

    def test_latest_opencode_outcome_in_this_repo_without_message_text(self):
        self._write_dispatch(dict(DISPATCH, **{"opencode-developer": DISPATCH["company-gw"]}))
        ms = lambda age: int((self.NOW - age) * 1000)
        error = lambda kind, status: json.dumps({"error": {
            "type": kind, "status": status, "message": "PROMPT-SECRET token=abc"}})
        db = self._opencode_db(
            [("s1", None, self.canonical, "t", ms(3000), ms(3000), "failed"),
             ("s2", None, str(self.task), "t", ms(600), ms(600), "failed"),
             ("s3", None, "/other/project", "t", ms(100), ms(100), "failed")],
            [("s1", "idle", 1, error("provider.quota", 429)),
             ("s2", "idle", 1, error("provider.auth", 401)),
             ("s3", "idle", 1, error("provider.quota", 429))])
        rows = self.by_name(mon.office_rows([], self.NOW, codex_root=str(self.codex),
                                            claude_root=str(self.claude),
                                            canonical=self.canonical, opencode_db=db,
                                            cwd_of=lambda pid: None))
        row = rows["opencode developer"]   # another project's newer 429 does not count
        self.assertEqual((row.state, row.detail, row.age),
                         (mon.STOPPED, "可派工；本 repo 最近一次：認證失敗（401），需要重新登入", 600.0))
        text = "\n".join(mon.office_lines(list(rows.values())))
        self.assertNotIn("PROMPT-SECRET", text)
        self.assertNotIn("token=abc", text)
        old = self._opencode_db([("s1", None, self.canonical, "t", ms(90_000), ms(90_000), "failed")],
                                [("s1", "idle", 1, error("provider.quota", 429))])
        self.assertIsNone(mon.opencode_outcome(self.NOW, old, self.canonical))

    def _dispatch(self, workers):
        path = Path(tempfile.mkdtemp(dir=self.tmp.name)) / "worker-dispatch.json"
        path.write_text(json.dumps(policy(workers)), encoding="utf-8")
        return str(path)

    def test_owner_pause_switch_wins_over_live_evidence(self):
        ms = lambda age: int((self.NOW - age) * 1000)
        db = self._opencode_db([("s1", None, self.canonical, "t", ms(600), ms(600), "failed")],
                               [("s1", "idle", 1, json.dumps(
                                   {"error": {"type": "provider.auth", "status": 401}}))])
        switches = self._dispatch({
            "opencode-developer": {"dispatch": "PAUSED", "decided_by": "Owner",
                                   "decided_at": "2026-10-22", "reason": "quota"},
            "agy": {"dispatch": "PAUSED", "decided_by": "Owner", "decided_at": "2026-10-22"},
            "company-gw": {"dispatch": "ALLOWED", "decided_by": "Owner", "decided_at": "2026-10-22"},
        })
        tui = mon.Process(30, 1, 30, "05:00", "S+", "OpenCode CLI", "opencode", "")
        rows = self.by_name(mon.office_rows([tui], self.NOW, codex_root=str(self.codex),
                                            claude_root=str(self.claude),
                                            canonical=self.canonical, opencode_db=db,
                                            cwd_of=lambda pid: None, cpu_of=lambda pid: 0.0,
                                            dispatch=switches))
        self.assertEqual((rows["opencode developer"].state, rows["opencode developer"].detail),
                         (mon.PAUSED, "Owner 暫停派工（10-22）；有 1 個 CLI 開著（非派工）"))
        self.assertIn("opencode cli #1", rows)
        self.assertEqual(rows["agy"].detail, "Owner 暫停派工（10-22）")
        self.assertEqual((rows["公司 gw"].state, rows["公司 gw"].detail), (mon.IDLE, "可派工"))
        no_cli = self.by_name(mon.office_rows([], self.NOW, codex_root=str(self.codex),
                                              claude_root=str(self.claude),
                                              canonical=self.canonical,
                                              opencode_db=db, cwd_of=lambda pid: None,
                                              dispatch=switches))
        self.assertEqual(no_cli["opencode developer"].detail,
                         "Owner 暫停派工（10-22）；本 repo 最近一次：認證失敗（401），需要重新登入")
        self.assertEqual(no_cli["opencode developer"].state, mon.PAUSED)

    def test_company_gw_row_reads_llmtool_outcomes_never_request_or_response(self):
        tasks = Path(tempfile.mkdtemp(dir=self.tmp.name))
        def task(name, age, state, code):
            path = tasks / f"{name}.json"
            path.write_text(json.dumps({
                "request": "PROMPT-SECRET", "response": "MODEL-OUTPUT-SECRET",
                "developer": {"provider_profile": "company-gw", "state": state,
                              "error_code": code, "artifact": "TOOL-OUTPUT-SECRET"}}),
                encoding="utf-8")
            os.utime(path, (self.NOW - age, self.NOW - age))
        task("a", 7200, "DEVELOPER_OUTPUT_REJECTED", "DEVELOPER_OUTPUT_INVALID_DIFF")
        task("b", 3600, "DEVELOPER_OUTPUT_REJECTED", "DEVELOPER_OUTPUT_INVALID_ENVELOPE")
        task("c", 9 * 86400, "DEVELOPER_RESPONDED", None)   # outside the window
        rows = self.by_name(mon.office_rows([], self.NOW, codex_root=str(self.codex),
                                            claude_root=str(self.claude),
                                            canonical=self.canonical, opencode_db="/nonexistent",
                                            cwd_of=lambda pid: None, llmtool_tasks=str(tasks)))
        gw = rows["公司 gw"]
        self.assertEqual(gw.detail, "可派工；本機 llmtool 紀錄（不分 repo）最近一次：輸出被拒"
                                    "（回應格式錯誤）（近 7 天 2 次，成功 0 次）")
        self.assertEqual((gw.age, gw.state), (3600.0, mon.ATTENTION))
        text = "\n".join(mon.office_lines(list(rows.values())))
        for secret in SECRETS:
            self.assertNotIn(secret, text)

    def _runner(self, *logs):
        # The log path is shown with the home as "~"; a temp folder can sit in
        # the home (the Mac CI's does), so pin the home away from it.
        self.enterContext(patch.object(os.path, "expanduser", return_value="/nonexistent-home"))
        root = Path(tempfile.mkdtemp(dir=self.tmp.name))
        (root / ".runner").write_text("{}")
        (root / "_diag").mkdir()
        for name, lines in logs:
            (root / "_diag" / name).write_text("".join(
                f"[x INFO Terminal] WRITE LINE: {when}Z: {text}\n" for when, text in lines))
        return str(root)

    def test_runner_row_shows_job_isolation_restarts_and_logs(self):
        now = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc).timestamp()
        root = self._runner(
            ("Runner_20261024-090000-utc.log", [("2026-10-24 09:00:05", "Listening for Jobs")]),
            ("Runner_20261025-110000-utc.log", [
                ("2026-10-25 11:00:05", "Listening for Jobs"),
                ("2026-10-25 11:10:00", "Running job: check (macOS 3.13)"),
                ("2026-10-25 11:20:00", "Job check (macOS 3.13) completed with result: Failed"),
                ("2026-10-25 11:50:00", "Running job: Mac Runner Connectivity")]),
            ("Worker_20261025-111000-utc.log", [("2026-10-25 11:10:00", "Running job: other")]))
        listener = mon.Process(30, 1, 30, "01:00:00", "S", "Actions runner", "Runner.Listener")
        worker = mon.Process(31, 30, 30, "10:00", "S", "Actions runner job", "Runner.Worker")
        inside = {30: "專用帳號 ghrunner"}.get
        busy, = mon.runner_rows([listener, worker], now, None, {30: root}.get, inside)
        self.assertEqual((busy.name, busy.state, busy.age, busy.alert),
                         ("mac ci runner", mon.WORKING, 600.0, ""))
        self.assertEqual(busy.detail, f"執行中：Mac Runner Connectivity（10 分鐘）；專用帳號 ghrunner；"
                                      f"已開 1 小時；日誌：{root}/_diag")
        idle, = mon.runner_rows([listener], now, None, {30: root}.get, inside)
        self.assertEqual(idle.state, mon.IDLE)
        # A start more than a day old is not counted; the Worker log is not a runner log.
        self.assertEqual(idle.detail, "閒置，等工作；專用帳號 ghrunner；已開 1 小時；上一個工作："
                                      f"check (macOS 3.13) 失敗（40 分鐘前）；日誌：{root}/_diag")
        # GitHub names the running job and the queue (it wins over the log).
        ci = mon.ci_runs({"workflow_runs": [
            {"status": "queued", "name": "llmtool Mac CI", "head_sha": "b" * 40,
             "pull_requests": [{"number": 334}], "created_at": "2026-10-25T11:55:00Z"},
            {"status": "in_progress", "name": "llmtool Mac CI", "head_sha": "a" * 40,
             "pull_requests": [{"number": 339}], "run_started_at": "2026-10-25T11:50:00Z"},
            {"status": "completed", "name": "old", "head_sha": "c" * 40},
            {"status": "queued", "name": "Mac Runner Smoke Test", "head_sha": "d" * 40,
             "pull_requests": [], "head_branch": "main", "created_at": "2026-10-25T11:56:00Z"}]})
        named, = mon.runner_rows([listener, worker], now, None, {30: "/nonexistent"}.get, inside, ci=ci)
        self.assertTrue(named.detail.startswith(
            "執行中：llmtool Mac CI（PR #339 @aaaaaaa）（10 分鐘）；專用帳號 ghrunner；已開 1 小時；"
            "排隊 2 個：llmtool Mac CI（PR #334 @bbbbbbb）、Mac Runner Smoke Test（main @ddddddd）"),
            named.detail)
        # Logs this account cannot read: the job process alone says it is working.
        unread, = mon.runner_rows([listener, worker], now, None, {30: "/nonexistent"}.get, inside)
        self.assertEqual(unread.state, mon.WORKING)
        self.assertTrue(unread.detail.startswith("執行中（10 分鐘）（看不到工作名稱）；專用帳號 ghrunner"))
        sandboxed, = mon.runner_rows([listener], now, None, {30: root}.get, {30: "沙箱"}.get)
        self.assertEqual((sandboxed.state, sandboxed.alert), (mon.IDLE, ""))
        self.assertNotIn("啟動", idle.detail)
        # A job may plant a FIFO or a link under a log name: skipped, never read.
        os.mkfifo(os.path.join(root, "_diag", "Runner_20261025-115900-utc.log"))
        os.symlink("/dev/zero", os.path.join(root, "_diag", "Runner_20261025-115901-utc.log"))
        again, = mon.runner_rows([listener], now, None, {30: root}.get, inside)
        self.assertEqual(again.detail, idle.detail)

    def test_runner_restarts_unisolated_and_stopped_are_flagged(self):
        now = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc).timestamp()
        root = self._runner(("Runner_20261025-100000-utc.log", [
            ("2026-10-25 10:00:05", "Listening for Jobs"), ("2026-10-25 11:00:05", "Listening for Jobs")]))
        listener = mon.Process(30, 1, 30, "30:00", "S", "Actions runner", "Runner.Listener")
        row, = mon.runner_rows([listener], now, None, {30: root}.get, {30: ""}.get)
        self.assertIn("沒有隔離", row.detail)
        self.assertEqual(row.state, mon.ATTENTION)
        self.assertIn("近 24 小時啟動 2 次", row.detail)
        self.assertEqual(row.alert, "CI runner 用你的帳號在跑（可能是手動 ./run.sh）：CI 工作沒有隔離")
        os.makedirs(os.path.join(root, "isolation"))
        Path(root, "isolation", "job-started.sh").write_text("")
        row, = mon.runner_rows([listener], now, None, {30: root}.get, {30: ""}.get)
        self.assertTrue(row.alert.endswith("每個 CI 工作都會被拒"))
        row, = mon.runner_rows([listener], now, None, {30: root}.get, {30: None}.get)
        self.assertIn("無法確認是否隔離", row.detail)
        self.assertEqual(row.state, mon.IDLE)
        self.assertEqual(row.alert, "")
        stopped, = mon.runner_rows([], now, root)
        self.assertEqual((stopped.state, stopped.age, stopped.alert),
                         (mon.STOPPED, None, "CI runner 沒在跑"))
        self.assertTrue(stopped.detail.startswith("沒在跑（GitHub 上的 CI 會排隊等它）"))
        # No runner configured, or no place given: no row (the office tests stay hermetic).
        self.assertEqual(mon.runner_rows([], now, self.tmp.name), [])
        self.assertEqual(mon.runner_rows([], now, None), [])
        with patch.object(os.path, "expanduser", return_value=os.path.dirname(root)):
            row, = mon.runner_rows([], now, root)
        self.assertTrue(row.detail.endswith(f"日誌：~/{os.path.basename(root)}/_diag"))
        os.chmod(os.path.join(root, "_diag"), 0)   # e.g. the runner account's folder
        try:
            row, = mon.runner_rows([], now, root)
        finally:
            os.chmod(os.path.join(root, "_diag"), 0o755)
        self.assertNotIn("日誌", row.detail)

    def test_each_agy_cli_gets_a_row_with_cpu_estimate(self):
        procs = [mon.Process(20, 1, 20, "01:00", "S+", "AGY CLI", "agy", ""),
                 mon.Process(21, 1, 21, "02:00", "S+", "AGY CLI", "agy", ""),
                 mon.Process(22, 1, 22, "03:00", "S+", "AGY CLI", "agy", "")]
        cpu = {20: 12.0, 21: 0.0, 22: None}
        rows = self.by_name(mon.office_rows(procs, self.NOW, codex_root=str(self.codex),
                                            claude_root=str(self.claude),
                                            canonical=self.canonical, opencode_db="/nonexistent",
                                            cwd_of={20: self.canonical}.get, cpu_of=cpu.get))
        # paused by the Owner, so the switch row stays beside the open CLIs
        self.assertEqual(rows["agy"].detail, "Owner 暫停派工（10-22）；有 3 個 CLI 開著（非派工）")
        self.assertEqual(rows["agy cli #1"].state, mon.WORKING)
        self.assertEqual(rows["agy cli #1"].detail, "本 repo：CLI 執行中（依 CPU 12% 推算）")
        self.assertEqual(rows["agy cli #2"].state, mon.IDLE)
        self.assertTrue(rows["agy cli #2"].detail.startswith("位置不明："))
        self.assertEqual(rows["agy cli #3"].state, mon.UNCHECKED)

    def test_two_runners_get_a_row_each_with_their_own_job_and_one_queue(self):
        now = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc).timestamp()
        base = Path(tempfile.mkdtemp(dir=self.tmp.name))
        roots = []
        for account in ("ghrunner", "ghrunner2"):
            root = base / account / "actions-runner"
            (root / "_diag").mkdir(parents=True)
            roots.append(str(root))
        self.enterContext(patch.object(os.path, "expanduser", return_value="/nonexistent-home"))
        one = mon.Process(30, 1, 30, "01:00:00", "S", "Actions runner", "Runner.Listener")
        two = mon.Process(40, 1, 40, "05:00", "S", "Actions runner", "Runner.Listener")
        job = mon.Process(41, 40, 40, "03:00", "S", "Actions runner job", "Runner.Worker")
        ci = mon.ci_runner_names(mon.ci_runs({"workflow_runs": [
            {"id": 7, "status": "in_progress", "name": "llmtool Mac CI", "head_sha": "a" * 40,
             "pull_requests": [{"number": 343}], "run_started_at": "2026-10-25T11:57:00Z"},
            {"id": 8, "status": "queued", "name": "llmtool Mac CI", "head_sha": "b" * 40,
             "pull_requests": [{"number": 341}], "created_at": "2026-10-25T11:58:00Z"}]}),
            lambda run: {"jobs": [{"status": "in_progress",
                                   "runner_name": "davidproject-self-action-2"}]})
        where = {30: roots[0], 40: roots[1]}
        rows = mon.runner_rows([one, two, job], now, roots, where.get,
                               {30: "專用帳號 ghrunner", 40: "專用帳號 ghrunner2"}.get, ci=ci)
        self.assertEqual([(r.name, r.state) for r in rows],
                         [("mac ci runner #1", mon.IDLE), ("mac ci runner #2", mon.WORKING)])
        self.assertIn("排隊 1 個：llmtool Mac CI（PR #341 @bbbbbbb）", rows[0].detail)
        self.assertTrue(rows[1].detail.startswith(
            "執行中：llmtool Mac CI（PR #343 @aaaaaaa）（3 分鐘）；專用帳號 ghrunner2"), rows[1].detail)
        self.assertNotIn("排隊", rows[1].detail)
        self.assertTrue(rows[1].detail.endswith(f"日誌：{roots[1]}/_diag"))
        # Runner 2 stopped: its own row says so; runner 1 is unaffected.
        rows = mon.runner_rows([one], now, roots, where.get, {30: "專用帳號 ghrunner"}.get, ci=[])
        self.assertEqual([(r.name, r.state) for r in rows],
                         [("mac ci runner #1", mon.IDLE), ("mac ci runner #2", mon.STOPPED)])
        self.assertEqual(rows[1].alert, "CI runner davidproject-self-action-2 沒在跑")

    def test_cli_working_in_another_folder_gets_its_running_session(self):
        now = self.NOW
        task = str(self.task)
        cli = mon.Process(40, 1, 40, "30:00", "S+", "OpenCode CLI", "opencode", "")
        other = mon.Process(41, 1, 41, "30:00", "S+", "OpenCode CLI", "opencode", "")
        running = (task, "Issue 271 fix", now - 10, now - 1800)   # updated after it went idle
        done = (task, "old", now - 10, now - 10)
        row, = mon._cli_rows([cli], [running], now, self.canonical, {40: self.canonical}.get,
                             "opencode", "OpenCode CLIENT", 50, "")
        self.assertEqual(row.state, mon.WORKING)
        self.assertTrue(row.detail.startswith("task wp-1：Issue 271 fix"), row.detail)
        row, = mon._cli_rows([cli], [done], now, self.canonical, {40: self.canonical}.get,
                             "opencode", "OpenCode CLIENT", 50, "")
        self.assertEqual(row.state, mon.IDLE)
        # Two CLIs without their own session: which one works there is not guessed.
        rows = mon._cli_rows([cli, other], [running], now, self.canonical,
                             {40: self.canonical, 41: self.canonical}.get,
                             "opencode", "OpenCode CLIENT", 50, "")
        self.assertEqual([r.state for r in rows], [mon.IDLE, mon.IDLE])

    def test_each_opencode_cli_gets_its_own_row_with_place_and_state(self):
        ms = lambda age: int((self.NOW - age) * 1000)
        db = self._opencode_db([
            ("s1", None, self.canonical, "Fix monitor token=abc", ms(5), ms(400)),
            ("s2", None, str(self.task), "Old work", ms(3000), ms(3000)),
            ("s3", "s1", self.canonical, "child", ms(1), None),
        ])
        procs = [mon.Process(10, 1, 10, "05:00", "S+", "OpenCode CLI", "opencode", ""),
                 mon.Process(11, 1, 11, "01:02:00", "S+", "OpenCode CLI", "opencode", ""),
                 mon.Process(12, 1, 12, "00:10", "S+", "OpenCode CLI", "opencode", "")]
        cwd = {10: self.canonical, 11: str(self.task), 12: "/Users/davidsun"}
        rows = self.by_name(mon.office_rows(procs, self.NOW, codex_root=str(self.codex),
                                            claude_root=str(self.claude),
                                            canonical=self.canonical, opencode_db=db,
                                            cwd_of=cwd.get))
        self.assertEqual(rows["opencode developer"].state, mon.PAUSED)
        self.assertEqual(rows["opencode cli #1"].state, mon.WORKING)
        self.assertTrue(rows["opencode cli #1"].detail.startswith("本 repo："))
        self.assertNotIn("token=abc", rows["opencode cli #1"].detail)
        self.assertEqual(rows["opencode cli #2"].state, mon.IDLE)
        self.assertIn("task wp-1", rows["opencode cli #2"].detail)
        self.assertIn("已開 1 小時", rows["opencode cli #2"].detail)
        self.assertEqual(rows["opencode cli #3"].detail, "其他資料夾：已開啟，沒有執行中的任務")
        text = "\n".join(mon.office_lines(list(rows.values())))
        for secret in SECRETS:
            self.assertNotIn(secret, text)

    def _kilo_db(self, sessions, messages=()):
        db = Path(tempfile.mkdtemp(dir=self.tmp.name)) / "kilo.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE session (id TEXT, parent_id TEXT, directory TEXT, "
                     "title TEXT, time_updated INTEGER)")
        conn.execute("CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, "
                     "data TEXT)")
        conn.execute("CREATE TABLE credential (secret TEXT)")
        conn.execute("INSERT INTO credential VALUES ('CREDENTIAL-SECRET')")
        conn.executemany("INSERT INTO session VALUES (?,?,?,?,?)", sessions)
        conn.executemany("INSERT INTO message VALUES (?,?,?,?)", messages)
        conn.commit()
        conn.close()
        return str(db)

    def test_each_kilo_cli_gets_its_own_row_marked_as_not_dispatched(self):
        ms = lambda age: int((self.NOW - age) * 1000)
        done = json.dumps({"role": "assistant", "time": {"completed": ms(300)},
                           "text": "MODEL-OUTPUT-SECRET"})
        asked = json.dumps({"role": "user", "content": "PROMPT-SECRET"})
        db = self._kilo_db(
            [("k1", None, self.canonical, "Review token=abc", ms(4)),
             ("k2", None, str(self.task), "Finished", ms(300)),
             ("k3", "k1", str(self.task), "child", ms(1))],
            [("m1", "k1", ms(600), done), ("m2", "k1", ms(4), asked),
             ("m3", "k2", ms(400), asked), ("m4", "k2", ms(310), done)])
        procs = [mon.Process(20, 1, 20, "05:00", "S+", "Kilo CLI", ".kilo", ""),
                 mon.Process(21, 1, 21, "07:37:41", "S+", "Kilo CLI", ".kilo", ""),
                 mon.Process(22, 1, 22, "07:37:41", "S+", "Kilo CLI (node launcher)", "node", "")]
        cwd = {20: self.canonical, 21: str(self.task), 22: self.canonical}
        rows = self.by_name(mon.office_rows(procs, self.NOW, codex_root=str(self.codex),
                                            claude_root=str(self.claude),
                                            canonical=self.canonical, kilo_db=db,
                                            cwd_of=cwd.get))
        self.assertNotIn("kilo cli #3", rows)   # the npm launcher is not a second Kilo
        self.assertEqual(rows["kilo cli #1"].state, mon.WORKING)
        self.assertTrue(rows["kilo cli #1"].detail.startswith("本 repo："))
        self.assertTrue(rows["kilo cli #1"].detail.endswith("（未列入派工）"))
        self.assertEqual(rows["kilo cli #2"].state, mon.IDLE)
        self.assertIn("task wp-1", rows["kilo cli #2"].detail)
        self.assertIn("已開 7 小時", rows["kilo cli #2"].detail)
        text = "\n".join(mon.office_lines(list(rows.values())))
        for secret in SECRETS:
            self.assertNotIn(secret, text)

    def _review(self, sha, outcome, age):
        label = f"審 PR #288 @{sha}"
        return mon.OfficeRow("claude reviewer", mon.WORKING if outcome is None else mon.DONE,
                             f"{label}（單次呼叫執行中）" if outcome is None else f"{label}：{outcome}",
                             age, 30, kind="subagent", group="T1", key="review",
                             role="reviewer", task=f"PR #288 @{sha}", outcome=outcome or "")

    def test_office_lines_put_repeated_review_failures_and_alerts_first(self):
        missing = mon.DISPATCH_PROBLEMS["missing"]
        rows = [
            mon.OfficeRow("codex 主對話", mon.WORKING, "指揮", 5, 10, key="T1", main=True),
            self._review("71db418", None, 10),
            self._review("3f86dc6", mon.NO_VERDICT, 180),
            self._review("3f86dc6", mon.NO_VERDICT, 400),
            self._review("d4ed21d", mon.NO_VERDICT, 1500),
            self._review("d4ed21d", "FAIL", 1620),
            mon.OfficeRow("codex subagent Dewey", mon.DONE, "audit 284 release", 600, 20,
                          group="T1", kind="subagent"),
            mon.OfficeRow("codex subagent Huygens", mon.DONE, "audit r4 handoff", 20, 20,
                          group="T1", kind="subagent"),
            mon.OfficeRow("opencode developer", mon.UNCHECKED,
                          f"{missing}；本 repo 最近一次：認證失敗（401），需要重新登入", 7200, 50,
                          kind="worker", alert="本 repo 最近一次：認證失敗（401），需要重新登入"),
            mon.OfficeRow("agy", mon.UNCHECKED, f"{missing}；App 開著", None, 60, kind="worker"),
            mon.OfficeRow("公司 gw", mon.UNCHECKED, missing, None, 70, kind="worker"),
        ]
        lines = mon.office_lines(rows)
        text = "\n".join(lines)
        self.assertEqual(lines[1], "⚠ #288 reviewer：3 次結束後 GitHub 沒有結論、1 次 FAIL；目前執行中 @71db418")
        self.assertEqual(lines[2], "⚠ opencode developer：本 repo 最近一次：認證失敗（401），需要重新登入")
        # one dispatch line instead of the same words on every Worker row
        self.assertEqual(lines[3], f"ℹ {missing}：opencode developer、agy、公司 gw")
        self.assertEqual(text.count(missing), 1)
        gw = next(line for line in lines if line.startswith("公司 gw"))
        self.assertTrue(gw.endswith("沒有其他紀錄"))
        # name first, then state; the time is last and only where there is one
        review = next(line for line in lines if "@71db418（" in line)
        self.assertTrue(review.startswith("└ claude reviewer"))
        self.assertTrue(review.endswith("  剛剛"))
        self.assertTrue(next(line for line in lines if line.startswith("agy")).endswith("App 開著"))
        # older finished work is folded; the newest verdict of each review target stays
        self.assertNotIn("audit 284 release", text)
        self.assertNotIn("FAIL」", text)
        self.assertIn("audit r4 handoff", text)
        self.assertIn(f"@3f86dc6：{mon.NO_VERDICT}", text)
        self.assertEqual(text.count("@3f86dc6："), 1)
        folded = next(line for line in lines if "└ … 另 4 項已完成" in line)
        self.assertTrue(folded.endswith("5–30 分鐘前完成；按 a 展開"))
        # expanded (key a in the TUI, always in --once) lists every finished item
        full = "\n".join(mon.office_lines(rows, expand=True))
        self.assertIn("audit 284 release", full)
        self.assertEqual(full.count("@3f86dc6："), 2)
        self.assertNotIn("另 4 項已完成", full)

    def test_no_review_alert_once_the_newest_review_reached_a_verdict(self):
        rows = [mon.OfficeRow("codex 主對話", mon.IDLE, "", 5, 10, key="T1", main=True),
                self._review("293a9d8", "PASS", 60),
                self._review("293a9d8", mon.NO_VERDICT, 200),
                self._review("293a9d8", mon.NO_VERDICT, 400)]
        self.assertFalse(any(line.startswith("⚠ #288") for line in mon.office_lines(rows)))
        rows.append(self._review("293a9d8", mon.NO_VERDICT, 30))   # newer, and failed again
        self.assertTrue(any(line.startswith("⚠ #288 reviewer：3 次結束後 GitHub 沒有結論")
                            for line in mon.office_lines(rows)))

    def test_office_lines_say_nothing_extra_when_all_is_well(self):
        rows = [mon.OfficeRow("codex 主對話", mon.IDLE, "等待下一個指示", 30, 10, key="T1"),
                self._review("71db418", "PASS", 60),
                mon.OfficeRow("agy", mon.IDLE, "可派工", None, 60, kind="worker")]
        lines = mon.office_lines(rows)
        self.assertFalse(any(line.startswith(("⚠", "ℹ")) for line in lines))
        self.assertFalse(any("已收起" in line for line in lines))

    def test_office_lines_name_a_shared_dispatch_source_once(self):
        source = "本機 origin/main 的派工檔（可能不是最新）"
        rows = [mon.OfficeRow("opencode developer", mon.PAUSED,
                              f"Owner 暫停派工（10-22）（{source}）；App 開著", None, 50,
                              kind="worker", evidence=source),
                mon.OfficeRow("公司 gw", mon.ATTENTION, f"可派工（{source}）", 34 * 3600, 70,
                              kind="worker", evidence=source)]
        lines = mon.office_lines(rows)
        self.assertTrue(lines[-1].startswith(f"派工狀態讀自{source}；"))   # dimmed legend
        self.assertEqual("\n".join(lines).count(source), 1)
        gw = next(line for line in lines if line.startswith("公司 gw"))
        self.assertIn("可派工 ", gw)
        self.assertTrue(gw.endswith("  34 小時前"))   # the time stays in its own column

    def test_office_view_colors_by_state_and_wraps_detail_to_keep_the_time(self):
        rows = [mon.OfficeRow("codex 主對話", mon.WORKING, "x" * 200, 5, 10, key="T1"),
                mon.OfficeRow("codex subagent A", mon.STALLED, "audit", 1000, 20, group="T1",
                              kind="subagent"),
                mon.OfficeRow("agy", mon.PAUSED, "Owner 暫停派工（10-22）", None, 60,
                              kind="worker")]
        listed = mon.office_view(rows, width=80)
        view = dict(listed)
        line = next(text for text in view if text.startswith("codex 主對話"))
        self.assertEqual(view[line], "+")
        self.assertTrue(all(mon._width(text) <= 80 for text, _ in listed))
        self.assertTrue(line.endswith("x  剛剛"))
        # a long detail wraps under itself instead of being cut
        at = [text for text, _ in listed].index(line)
        rest = [text for text, _ in listed[at + 1:at + 10] if text.startswith(" ")]
        self.assertEqual(sum(text.count("x") for text in rest) + line.count("x") - 1, 200)   # 1 = "codex"
        self.assertTrue(all(view[text] == "+" for text in rest))
        # a blank line sits above the Worker section
        texts = [text for text, _ in listed]
        self.assertEqual(texts[texts.index(f"── {mon.STATUS_SECTION} ──") - 1], "")
        # on a wide screen the time stays near its text
        wide = next(text for text, _ in mon.office_view(rows, width=240)
                    if text.startswith("agy"))
        self.assertTrue(wide.startswith("agy ") and wide.endswith("暫停中  Owner 暫停派工（10-22）"))
        stalled = next(text for text, _ in mon.office_view(rows, width=240)
                       if text.startswith("└ codex subagent A"))
        self.assertIn("  audit  ", stalled)
        self.assertTrue(stalled.endswith("  16 分前"))
        self.assertLessEqual(mon._width(stalled), mon._width("└ codex subagent A") + 2 + 8 + mon.DETAIL_MAX + 2 + 7)
        self.assertEqual(view[next(t for t in view if t.startswith("└ codex subagent A"))], "!")
        self.assertEqual(view[next(t for t in view if t.startswith("agy"))], "dim")
        self.assertEqual(view[next(t for t in view if t.startswith("⚠ codex subagent A"))], "!")
        self.assertEqual(view[next(iter(view))], "title")
        # without a width (--once) nothing is cut
        self.assertIn("x" * 200, "\n".join(mon.office_lines(rows)))

    def test_wrap_breaks_at_punctuation_and_never_starts_with_a_closing_mark(self):
        text = "可派工；本機 llmtool 紀錄（不分 repo）最近一次：輸出被拒（回應格式錯誤）"
        for cols in range(8, 40):
            pieces = mon._wrap(text, cols)
            self.assertEqual("".join(pieces).replace(" ", ""), text.replace(" ", ""))
            self.assertTrue(all(mon._width(piece) <= cols for piece in pieces))
            self.assertFalse(any(piece[:1] in mon.NO_LINE_START for piece in pieces))
        self.assertEqual(mon._wrap(text, 30)[0], "可派工；本機 llmtool")   # at the space
        self.assertEqual(mon._wrap("短", 10), ["短"])

    def test_stopped_opencode_keeps_its_alert_unless_dispatch_is_paused(self):
        stopped = (mon.STOPPED, "最近一次：免費額度用完（429）", 60.0)
        with patch.object(mon, "opencode_outcome", return_value=stopped):
            unknown = self.by_name(mon.office_rows(
                [], self.NOW, codex_root=str(self.codex), claude_root=str(self.claude),
                canonical=self.canonical, dispatch=str(Path(self.tmp.name) / "no-such-dispatch.json")))
            paused = self.by_name(self.rows())
        self.assertEqual(unknown["opencode developer"].state, mon.UNCHECKED)
        self.assertEqual(unknown["opencode developer"].alert, "本 repo 最近一次：免費額度用完（429）")
        self.assertEqual(paused["opencode developer"].state, mon.PAUSED)
        self.assertEqual(paused["opencode developer"].alert, "")

    def test_rendered_office_never_shows_secrets_or_private_paths(self):
        self._codex("pm", {"id": "T1", "cwd": self.canonical, "thread_source": "user"},
                    ["task_started"], age=5)
        self._codex("bad", {"id": "S1", "cwd": self.canonical, "thread_source": "subagent",
                            "parent_thread_id": "T1", "agent_nickname": "x/../y",
                            "agent_path": "/root/rm -rf /Users/davidsun"}, ["task_started"], age=5)
        text = "\n".join(mon.office_lines(self.rows()))
        for secret in SECRETS:
            self.assertNotIn(secret, text)
        self.assertIn("（未命名工作）", text)

    def test_state_changes_become_log_events_once(self):
        first = [mon.OfficeRow("codex developer A", mon.WORKING, "wp 1", 1.0, 20)]
        state, events = mon.office_events({}, first)
        self.assertEqual(events, [])
        later = [mon.OfficeRow("codex developer A", mon.DONE, "wp 1", 1.0, 20)]
        state, events = mon.office_events(state, later)
        self.assertEqual(events, [("i", "codex developer A「wp 1」：工作中 → 完成")])
        self.assertEqual(mon.office_events(state, later)[1], [])

    def test_canonical_root_resolves_through_a_linked_worktree(self):
        common = Path(self.canonical) / ".git" / "worktrees" / "wp-1"
        common.mkdir(parents=True)
        (self.task / ".git").write_text(f"gitdir: {common}\n", encoding="utf-8")
        self.assertEqual(mon.canonical_root(str(self.task)), self.canonical)


if __name__ == "__main__":
    unittest.main()
