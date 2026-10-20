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


class MonitorTaskBaselineTests(unittest.TestCase):
    """A failed first task scan must not replay history on the next success."""

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

    @staticmethod
    def stage(name, result="", tools=(), live=False):
        return mon.Stage(name, "deepseek", 1.0, result, "ok" if result else "",
                         1, 0, 0, "", tuple(tools), live, "ok")

    @staticmethod
    def task(directory, *stages):
        return mon.Task(directory, directory.strip("/"), "main", tuple(stages))

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


if __name__ == "__main__":
    unittest.main()
