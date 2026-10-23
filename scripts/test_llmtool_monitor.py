"""Offline tests for scripts/llmtool_monitor.py (no processes, models or network).

Run: python3 -B -m unittest discover -s scripts -p 'test_llmtool_monitor.py'
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
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
    for name in ("CODEX_SESSIONS", "CLAUDE_PROJECTS", "OPENCODE_DB", "KILO_DB", "LLMTOOL_TASKS"):
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
        self.assertIn("GitHub 動態未啟用", lines[-1])
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
             patch("socket.socket.connect", side_effect=no_network), \
             patch("urllib.request.urlopen", side_effect=no_network), \
             patch("sys.stdout", output):
            self.assertEqual(mon.run_once(), 0)
        rendered = output.getvalue()
        self.assertIn("RECENT ACTIVITY", rendered)
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
        self.assertIn("GitHub 動態未啟用", out.getvalue())

    def test_once_keeps_github_off_by_default(self):
        snapshot = mon.Snapshot([], {}, [], frozenset())
        with patch.object(mon, "collect", return_value=snapshot), \
             patch.object(mon, "scan_tasks", return_value=[]), \
             patch.object(mon.shutil, "which", side_effect=AssertionError("GitHub must be opt-in")), \
             patch("sys.stdout", __import__("io").StringIO()) as out:
            self.assertEqual(mon.main(["--once"]), 0)
        self.assertIn("GitHub 動態未啟用", out.getvalue())

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
        activity = out.getvalue().split("RECENT ACTIVITY", 1)[1].splitlines()
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

    def _write_dispatch(self, workers, raw=None):
        path = Path(self.canonical) / "tools" / "llmtool" / "worker-dispatch.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw if raw is not None else
                        json.dumps({"schema_version": 1, "workers": workers}), encoding="utf-8")
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

    def test_one_shot_review_shows_pr_head_and_verdict_from_the_pm_record_only(self):
        ts = lambda age: mon.time.strftime("%Y-%m-%dT%H:%M:%S.000Z", mon.time.gmtime(self.NOW - age))
        out = lambda text: {"type": "custom_tool_call_output", "call_id": None,
                            "output": [{"type": "input_text", "text": text}]}
        def call(cid, text):
            return {"type": "custom_tool_call", "call_id": cid, "name": "exec", "input": text}
        result = ('{"exit_code":0,"output":"# Formal review PR #283\\n**Verdict: FAIL** at head '
                  '`18538d569d94fa7e5a06605dadf3a7305407b4de`\\nPROMPT-SECRET details"}')
        events = [
            (900, call("c1", 'gh pr diff 283 | claude --print --tools "" "Review PR #283 head '
                             '18538d569d94fa7e5a06605dadf3a7305407b4de"')),
            (899, dict(out('{"session_id":70614,"output":""}'), call_id="c1")),
            # polled together with another command that finished: still running
            (890, call("c2p", "Promise.allSettled([tools.write_stdin({session_id:70614}), "
                              "tools.exec_command({cmd:'git status'})])")),
            (889, {"type": "custom_tool_call_output", "call_id": "c2p", "output": [
                {"type": "input_text", "text": '{"status":"fulfilled","value":{"session_id":70614}}'},
                {"type": "input_text", "text": '{"status":"fulfilled","value":{"exit_code":0,'
                                               '"output":"clean"}}'}]}),
            (880, call("c2", "const r=await tools.write_stdin({session_id:70614,yield_time_ms:30000});")),
            (860, dict(out(result), call_id="c2")),
            # a verdict quoted by gh, not from a claude call, must be ignored
            (850, call("c3", "gh pr view 283 --json comments")),
            (849, dict(out('{"exit_code":0,"output":"**Verdict: PASS** PR #999 head abcdef1"}'),
                       call_id="c3")),
            # a plain-text poll format: "SESSION N" while running, raw output at the end
            (800, call("p1", "claude -p --tools \"\" --no-session-persistence 'Review PR #285 head "
                             "c40c80acae77147190684a1ea8b66d744378ff41'")),
            (799, dict(out("SESSION 81"), call_id="p1")),
            (790, call("p2", "const r=await tools.write_stdin({session_id:81});text(r.output);"
                             "if(r.session_id)text(\"SESSION \"+r.session_id)")),
            (789, {"type": "custom_tool_call_output", "call_id": "p2", "output": [
                {"type": "input_text", "text": ""}, {"type": "input_text", "text": "SESSION 81"}]}),
            (780, call("p3", "const r=await tools.write_stdin({session_id:81});text(r.output)")),
            (779, dict(out("# Formal Review: PR #285 at c40c80a\n\n**Verdict: PASS**\n- head "
                           "c40c80acae77147190684a1ea8b66d744378ff41"), call_id="p3")),
            (60, call("b1", "claude --resume abc -p 'status of PR #286'")),
            (59, dict(out("SESSION 82"), call_id="b1")),
            (58, call("b2", "const r=await tools.write_stdin({session_id:82});text(r.output)")),
            (57, {"type": "custom_tool_call_output", "call_id": "b2", "output": [
                {"type": "input_text", "text": "Error: Exceeded USD budget (0.25)"},
                {"type": "input_text", "text": "EXIT 1"}]}),
            # the reply names another PR than the call did
            (700, call("m1", "claude -p 'Review PR #287 head 1111111aaaa'")),
            (699, dict(out('{"exit_code":0,"output":"# Review PR #290 head 2222222\\n**Verdict: PASS**"}'),
                       call_id="m1")),
            # a verdict only quoted inside a sentence is not this review's verdict
            (690, call("q1", "claude -p 'Review PR #289 head 3333333bbbb'")),
            (689, dict(out('{"exit_code":1,"output":"The earlier verdict: FAIL was stale."}'),
                       call_id="q1")),
            # a call that fails at once (bad CLI option) is finished, not running
            (680, call("e1", "git diff a...b | claude --print --bad-flag 'Review PR #291 head 4444444cccc'")),
            (679, dict(out("error: unknown option '--bad-flag'"), call_id="e1")),
            # SESSION_ID= / EXIT= poll format
            (670, call("f1", "claude --print 'Review PR #292 head 5555555dddd'")),
            (669, dict(out("SESSION_ID=91"), call_id="f1")),
            (660, call("f2", "const r=await tools.write_stdin({session_id:91});text(r.output);"
                             "if(r.session_id)text(`SESSION_ID=${r.session_id}`);"
                             "if(r.exit_code!==undefined)text(`EXIT=${r.exit_code}`)")),
            (659, {"type": "custom_tool_call_output", "call_id": "f2", "output": [
                {"type": "input_text", "text": "# Review\n**Verdict: FAIL**"},
                {"type": "input_text", "text": "EXIT=0"}]}),
            # the PM's exec script: cmd=`timeout 180 claude --print ...`, output plus a
            # trailer line "exit_code=... session_id=..."
            (650, call("t1", 'const prompt=`Review PR #293 head 6666666eeee`;\n'
                             'const cmd=`timeout 180 claude --print --no-session-persistence '
                             '--max-budget-usd 1.50 "${prompt}"`;\n'
                             'const r=await tools.exec_command({cmd});text(r.output);\n'
                             'text(`exit_code=${r.exit_code ?? "running"} session_id=${r.session_id ?? ""}`);')),
            (649, {"type": "custom_tool_call_output", "call_id": "t1", "output": [
                {"type": "input_text", "text": ""},
                {"type": "input_text", "text": "exit_code=running session_id=93"}]}),
            (640, call("t2", "const r=await tools.write_stdin({session_id:93});text(r.output);"
                             'text(`exit_code=${r.exit_code ?? "running"} session_id=${r.session_id ?? ""}`);')),
            (639, {"type": "custom_tool_call_output", "call_id": "t2", "output": [
                {"type": "input_text", "text": "# Review PR #293\n**Verdict: PASS**"},
                {"type": "input_text", "text": "exit_code=0 session_id="}]}),
            # a delta review names the old head first; "## Review Verdict:" heading
            (630, call("d1", 'const cmd=`timeout 180 claude --print "Review PR #294 head 7777777ffff"`;')),
            (629, {"type": "custom_tool_call_output", "call_id": "d1", "output": [
                {"type": "input_text", "text": "## Review Verdict: PASS\nfrom head 0000000aaaa to "
                                               "head 7777777ffff on PR #294"},
                {"type": "input_text", "text": "exit_code=0 session_id="}]}),
            # killed by `timeout`
            (620, call("k1", 'const cmd=`timeout 180 claude --print "Review PR #295 head 8888888aaaa"`;')),
            (619, {"type": "custom_tool_call_output", "call_id": "k1", "output": [
                {"type": "input_text", "text": ""},
                {"type": "input_text", "text": "exit_code=124 session_id="}]}),
            # the PM's script broke before claude ran
            (610, call("s1", 'const cmd=`timeout 180 claude --print "Review PR #296 head 9999999bbbb"`;')),
            (609, {"type": "custom_tool_call_output", "call_id": "s1", "output": [
                {"type": "input_text", "text": "Script failed"},
                {"type": "input_text", "text": "Script error: x is not defined"}]}),
            # prose quoting the command is not a call
            (45, call("c6", "gh pr comment 288 --body 'shows `claude --print/-p` reviews'")),
            (44, call("c7", "const note=`see claude --print in the docs`;")),
            # searching for running reviews is not a review
            (40, call("c5", "claude --version 2>&1 | head -5; pgrep -alf "
                            "'(^|/)codex|claude -p|claude --print|claude$' | head -40")),
            (30, call("c4", "claude -p 'Review PR #284 head 1ee2f21c8caca7c66dc497268759dc8c308adebf'")),
            (29, dict(out('{"session_id":70615,"output":""}'), call_id="c4")),
        ]
        day = mon.time.strftime("%Y/%m/%d", mon.time.localtime(self.NOW))
        items = [{"type": "session_meta", "payload": {"id": "T1", "cwd": self.canonical,
                                                      "thread_source": "user"}}]
        items += [{"type": "event_msg", "payload": {"type": "task_started"}}]
        items += [{"timestamp": ts(age), "type": "response_item", "payload": payload}
                  for age, payload in events]
        self._write(self.codex / day / "rollout-pm.jsonl", items, age=5)
        run = mon.Process(40, 5718, 40, "00:20", "S", "Claude Code CLI", "claude",
                          "Codex APP-SERVER 5718")
        rows = self.rows([run])
        reviews = {row.detail: row.state for row in rows if row.name == "claude reviewer"}
        self.assertEqual(reviews, {"審 PR #283 @18538d5：FAIL": mon.DONE,
                                   "審 PR #285 @c40c80a：PASS": mon.DONE,
                                   f"審 PR #287 @1111111：{mon.MISMATCH}": mon.DONE,
                                   f"審 PR #289 @3333333：{mon.NO_VERDICT}": mon.DONE,
                                   f"審 PR #291 @4444444：{mon.NO_VERDICT}": mon.DONE,
                                   "審 PR #292 @5555555：FAIL": mon.DONE,
                                   "審 PR #293 @6666666：PASS": mon.DONE,
                                   "審 PR #294 @7777777：PASS": mon.DONE,
                                   f"審 PR #295 @8888888：{mon.TIMED_OUT_TEXT}": mon.DONE,
                                   f"審 PR #296 @9999999：{mon.SCRIPT_FAILED}": mon.DONE,
                                   "審 PR #284 @1ee2f21（單次呼叫執行中）": mon.WORKING})
        self.assertTrue(all(row.task.startswith("PR #") and row.role == "reviewer"
                            for row in rows if row.name == "claude reviewer"))
        # a status question names no head: it is a one-shot call, not a Reviewer
        unbound = [row for row in rows if row.name == "claude 單次呼叫"]
        self.assertEqual([(row.detail, row.role) for row in unbound],
                         [("單次呼叫（呼叫裡沒寫 PR 和版本）：超過預算上限，沒有判定", "")])
        text = "\n".join(mon.office_lines(rows))
        self.assertNotIn("PR #999", text)
        self.assertNotIn("PROMPT-SECRET", text)
        self.assertNotIn("單次呼叫執行中（不留 session 紀錄）", text)   # PM record explains the process

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
        self._write_dispatch({"agy": {"dispatch": "MAYBE", "decided_by": "Owner",
                                      "decided_at": "2026-10-22"},
                              "company-gw": DISPATCH["company-gw"]})
        rows = self.by_name(self.rows())
        self.assertEqual((rows["agy"].state, rows["agy"].detail),
                         (mon.UNCHECKED, mon.DISPATCH_PROBLEMS["entry"]))
        self.assertEqual(rows["opencode developer"].detail, mon.DISPATCH_PROBLEMS["absent"])
        self.assertEqual(rows["公司 gw"].state, mon.IDLE)

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
        path.write_text(json.dumps({"schema_version": 1, "workers": workers}), encoding="utf-8")
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
            "codex-reviewer-agent": {"dispatch": "bogus", "decided_by": "Owner",
                                     "decided_at": "2026-10-22"},
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
        self.assertNotIn("codex reviewer agent", rows)   # an invalid extra entry names no row
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
        self.assertEqual(lines[1], "⚠ #288 reviewer：3 次沒有判定、1 次 FAIL；目前執行中 @71db418")
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
        self.assertIn("@3f86dc6：結束但沒有判定", text)
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
                self._review("293a9d8", mon.TIMED_OUT_TEXT, 400)]
        self.assertFalse(any(line.startswith("⚠ #288") for line in mon.office_lines(rows)))
        rows.append(self._review("293a9d8", mon.SCRIPT_FAILED, 30))   # newer, and failed again
        self.assertTrue(any(line.startswith("⚠ #288 reviewer：3 次沒有判定")
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
