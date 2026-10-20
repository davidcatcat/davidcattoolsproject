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


def ps(*rows):
    return "\n".join(rows)


class ClassifyTests(unittest.TestCase):
    def test_roles(self):
        cases = {
            "/u/lib/node_modules/@opencode/cli/bin/opencode.exe serve --service": "OpenCode SERVER",
            "/u/bin/opencode.exe run --agent a": "OpenCode CLIENT",
            "(opencode.exe)": "OpenCode CLI",
            "claude": "Claude Code CLI",
            "/Applications/Claude.app/Contents/MacOS/Claude": "Claude Desktop",
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


class ActivityViewTests(unittest.TestCase):
    def test_five_issue_event_classes_render_in_time_order(self):
        events = [
            mon.ActivityEvent(5, "worker_result", "task-1", "stage-1",
                              "OPENCODE_PROVIDER_FAILED", "task-1/stage-1"),
            mon.ActivityEvent(1, "review_dispatch", "", "", "DISPATCHED", "ref-1"),
            mon.ActivityEvent(2, "review_verdict", "", "", "FAIL", "ref-2"),
            mon.ActivityEvent(3, "undelivered_reply", "", "", "NOT_DELIVERED", "ref-3"),
            mon.ActivityEvent(3.5, "undelivered_reply", "", "", "DELIVERED", "ref-3b"),
            mon.ActivityEvent(4, "waiting_owner", "", "", "BLOCKED", "ref-4"),
        ]
        lines = mon.render_activity(events)
        expected = ("PM → Reviewer", "Reviewer → PM", "回覆未送達",
                    "Integrator → Owner", "llmtool 主機 → 任務 task-1")
        positions = [next(i for i, line in enumerate(lines) if item in line) for item in expected]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("審查未通過", lines[positions[1]])
        self.assertIn("受阻，等待負責人決定", lines[positions[3]])
        self.assertIn("工作失敗", lines[positions[4]])
        self.assertIn("未顯示（沒有核准的本機來源）", lines[-1])
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
        self.assertIn("工作失敗", text)

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
        self.assertIn("llmtool 主機 → 任務 task-1", rendered)
        self.assertIn("工作失敗", rendered)
        self.assertNotIn("/Users/davidsun", rendered)
        self.assertNotIn("secret.py", rendered)
        self.assertFalse(any(secret in rendered for secret in SECRETS))


if __name__ == "__main__":
    unittest.main()
