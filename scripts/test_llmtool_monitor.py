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
import threading
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
    for name in ("CODEX_SESSIONS", "CODEX_HISTORY", "CODEX_STATE", "CLAUDE_PROJECTS", "CLAUDE_OTHER_PROJECTS", "OPENCODE_DB", "KILO_DB", "LLMTOOL_TASKS"):
        setattr(mon, name, os.path.join(_ISOLATED.name, name))


def tearDownModule():
    _ISOLATED.cleanup()


# The runner and worker counts every header ends with when no runner runs and
# GitHub was not read.
NO_RUNNERS = "  Mac runner 0  EC2 runner 未確認  EC2 worker 1"


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
            "/u/.local/bin/claude daemon run --origin transient": "Claude Code daemon",
            "claude bg-pty-host --bg-pty-host /tmp/d/s.pty.sock 200 50": "Claude Code daemon",
            "claude bg-spare --bg-spare /tmp/d/s.claim.sock": "Claude Code background session",
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


class MonitorTrustTests(unittest.TestCase):
    NOW = 1_800_000_000.0

    def test_owner_count_requires_a_successful_read_and_counts_only_waiting_owner(self):
        class Github:
            successful_read = False
            read_failed = False
            pending = []
        github = Github()
        self.assertEqual(mon.owner_count_line(None), ("等你決定：未讀 GitHub", ""))
        self.assertEqual(mon.owner_count_line(github), ("等你決定：尚未讀取", ""))
        github.successful_read = True
        github.pending = [mon.OwnerPending(1, "#1", "WAITING", "", "waiting_owner"),
                          mon.OwnerPending(2, "#2", "WAITING", "", "repair_gate")]
        self.assertEqual(mon.owner_count_line(github), ("等你決定：1 件", "!"))
        github.pending = []
        self.assertEqual(mon.owner_count_line(github), ("等你決定：0 件", ""))
        github.read_failed = True
        self.assertIn("GitHub 讀取失敗，可能過時", mon.owner_count_line(github)[0])

    def test_d6_phases_and_unknown_pr_state(self):
        self.assertEqual(mon.d6_phase("ACTIVE", True, "", False), "執行中")
        self.assertEqual(mon.d6_phase("ACTIVE", False, "MERGED", False), "已完成，待收尾")
        self.assertEqual(mon.d6_phase("ACTIVE", False, "CLOSED", False), "已完成，待收尾")
        self.assertEqual(mon.d6_phase("ACTIVE", False, "OPEN", False), "等審查或 CI")
        self.assertEqual(mon.d6_phase("REPAIR", False, "", False), "修復中")
        self.assertEqual(mon.d6_phase("REVIEW_PENDING", False, "", False), "等審查")
        self.assertEqual(mon.d6_phase("RETIRE_READY", False, "", False), "已驗收，可收尾")
        self.assertEqual(mon.d6_phase("RETAIN_BLOCKED", False, "", False), "保留待處置")
        self.assertEqual(mon.d6_phase("ACTIVE", False, "", False), "閒置（查不到 PR）")
        self.assertEqual(mon.d6_phase("ACTIVE", False, "", True), "審查已結束，待收尾")

    def test_task_route_summary_and_existing_task_output(self):
        route = (90.0, 3, 2, "已回覆")
        self.assertIn("Task 紀錄近 7 天 3 次，成功 2 次", mon.task_lines([], route=route)[0])
        self.assertIn("最近一次", mon.task_lines([], route=route)[0])
        self.assertEqual(mon.task_lines([]), ["近 7 天 llmtool Task 紀錄沒有 OpenCode 執行"])
        self.assertNotIn("12 小時", mon.task_lines([])[0])
        task = mon.Task("/tmp/llmtool-x", "t1", "branch", ())
        self.assertEqual(mon.task_lines([task], route=route)[0], "任務 t1   分支 branch")

    def test_main_health_line_reports_missing_and_known_runs(self):
        text, style = mon.main_health_line(None, "offline", self.NOW)
        self.assertIn("main：尚未讀取（未確認：offline）", text)
        self.assertEqual(style, "!")
        health = mon.MainHealth("74f78a4", "題目", self.NOW - 7200,
                                ("completed", "success", "74f78a4"),
                                ("completed", "success", self.NOW - 7 * 3600, "74f78a4"))
        text, style = mon.main_health_line(health, "", self.NOW)
        self.assertIn("main 74f78a4 題目（2 小時前）· Linux 成功", text)
        self.assertIn("Mac 全套 成功（7 小時前）", text)
        self.assertEqual(style, "")
        self.assertEqual(mon.main_health_line(replace(health, linux=("in_progress", None, "oldsha1")), "", self.NOW)[0].split("· Linux ")[1].split(" ·")[0], "執行中 @oldsha1")
        self.assertIn("Mac 未確認", mon.main_health_line(replace(health, mac=None), "", self.NOW)[0])
        self.assertEqual(mon.main_health_line(replace(health, linux=("completed", "failure", "74f78a4")), "", self.NOW)[1], "!")

    def test_main_refresh_ttl_failure_and_opt_in(self):
        source = mon.GitHubSource("o/r", clock=lambda: self.NOW)
        base = f"repos/o/r/"
        paths = {
            base + "commits?sha=main&per_page=1": [{"sha": "74f78a4deadbeef", "commit": {
                "message": "Health title", "committer": {"date": iso(self.NOW - 60)}}}],
            base + "actions/runs?branch=main&event=push&per_page=30": {"workflow_runs": [{
                "name": "llmtool Linux CI", "status": "completed", "conclusion": "success",
                "head_sha": "74f78a4deadbeef", "created_at": iso(self.NOW - 30)}]},
            base + "actions/workflows/llmtool-mac-ci.yml/runs?branch=main&event=workflow_dispatch&per_page=1": {
                "workflow_runs": [{"name": "llmtool Mac CI", "status": "in_progress",
                    "conclusion": None, "head_sha": "74f78a4deadbeef", "created_at": iso(self.NOW - 20)}]},
        }
        calls = []
        source._get = lambda path: calls.append(path) or paths[path]
        source.refresh_main()
        self.assertEqual(len(calls), 3)
        self.assertEqual(source.main_health.sha, "74f78a4")
        self.assertEqual(source.main_health.linux[1], "success")
        health = source.main_health
        source._get = lambda path: (_ for _ in ()).throw(RuntimeError("offline"))
        source.refresh_main()
        self.assertIs(source.main_health, health)  # TTL suppresses duplicate reads
        source.main_read_at -= 300
        source.refresh_main()
        self.assertIs(source.main_health, health)
        self.assertEqual(source.main_error, "offline")
        self.assertFalse(source.read_failed)
        self.assertEqual(source.status, "GitHub：尚未讀取")

        quiet = mon.GitHubSource("o/r", clock=lambda: self.NOW)
        seen = []
        quiet._get = lambda path: seen.append(path) or ([] if "comments?" in path else [])
        quiet.refresh_ci = lambda: None
        quiet.refresh(force=True)
        self.assertFalse(any("commits?sha=main" in path for path in seen))

    def test_github_refresh_does_not_read_main_without_opt_in(self):
        source = mon.GitHubSource("o/r", clock=lambda: self.NOW)
        seen = []
        source._get = lambda path: seen.append(path) or []
        source.refresh_ci = lambda: None
        source.refresh(force=True)
        self.assertFalse(source.want_main)
        self.assertFalse(any("commits?sha=main" in path for path in seen))

    def test_office_top_is_rendered_before_error_and_wraps(self):
        top = [("等你決定：2 件", "!"), ("main：尚未讀取", "")]
        lines = mon.office_view([], error="OSError", width=24, top=top)
        self.assertEqual(lines[1][1], "!")
        self.assertTrue(any("辦公室資料讀取失敗" in text for text, _ in lines))
        self.assertTrue(any(text.startswith("⚠") for text, _ in lines))


class QuotaWarningTests(unittest.TestCase):
    NOW = 1_800_000_000.0

    def _write(self, path, entries, modified=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")
        os.utime(path, (modified or self.NOW, modified or self.NOW))
        mon._tail_cache.pop(str(path), None)
        return path

    def test_codex_threshold_reset_and_invalid_number(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / time.strftime("%Y/%m/%d", time.localtime(self.NOW))
            path = folder / "rollout-a.jsonl"
            def entry(percent, reset):
                return {"type": "event_msg", "payload": {"rate_limits": {
                    "primary": {"used_percent": percent, "window_minutes": 300,
                                "resets_at": reset}}}}
            self._write(path, [entry(79, self.NOW + 3600)])
            self.assertEqual(mon.quota_warnings(self.NOW, temp, []), [])
            self._write(path, [entry(85, self.NOW + 3600)])
            self.assertIn("Codex 5 小時額度已用 85%", mon.quota_warnings(self.NOW, temp, [])[0])
            self._write(path, [entry(99, self.NOW - 1)])
            self.assertEqual(mon.quota_warnings(self.NOW, temp, []), [])
            self._write(path, [entry("90", self.NOW + 3600)])
            self.assertEqual(mon.quota_warnings(self.NOW, temp, []), [])

    def test_codex_rate_limit_reached_is_suppressed_after_all_windows_reset(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / time.strftime("%Y/%m/%d", time.localtime(self.NOW))
            path = folder / "rollout-a.jsonl"
            rate = {"type": "event_msg", "payload": {"rate_limits": {
                "rate_limit_reached_type": "primary",
                "primary": {"resets_at": self.NOW - 1},
                "secondary": {"resets_at": self.NOW}}}}
            self._write(path, [rate])
            self.assertEqual(mon.quota_warnings(self.NOW, temp, []), [])

    def test_claude_quota_hit_cleared_expired_and_reset_text_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".claude-account" / "projects"
            path = root / "project" / "session.jsonl"
            rate = {"type": "assistant", "timestamp": iso(self.NOW - 60),
                    "error": "rate_limit", "message": {"content": [{"text": "resets 8:30am private text"}]}}
            self._write(path, [rate])
            lines = mon.quota_warnings(self.NOW, codex_root=temp + "/missing",
                                       claude_root=str(root))
            self.assertEqual(lines, ["Claude（.claude-account）已到用量上限，resets 8:30am"])
            cleared = dict(rate, timestamp=iso(self.NOW - 30))
            assistant = {"type": "assistant", "timestamp": iso(self.NOW - 10),
                         "message": {"content": [{"text": "ordinary reply"}]}}
            self._write(path, [cleared, assistant])
            self.assertEqual(mon.quota_warnings(self.NOW, temp + "/missing", str(root)), [])
            old = dict(rate, timestamp=iso(self.NOW - 6 * 3600))
            self._write(path, [old])
            self.assertEqual(mon.quota_warnings(self.NOW, temp + "/missing", str(root)), [])


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

    def test_route_outcome_failure_preserves_previous_opencode_route(self):
        previous = (30.0, 4, 3, "已回覆")
        self.monitor.opencode_route = previous
        with patch.object(mon, "collect", return_value=self.snapshot), \
             patch.object(mon, "scan_tasks", return_value=[]), \
             patch.object(mon, "llmtool_task_records", return_value=[]), \
             patch.object(mon, "llmtool_route_outcomes", side_effect=OSError("offline")), \
             patch.object(mon, "quota_warnings", return_value=[]), \
             patch.object(mon, "office_rows", return_value=[]):
            self.monitor.poll()
        self.assertEqual(self.monitor.opencode_route, previous)

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
    def test_pm_last_dispatch_header_recent_stale_absent_and_wrapped(self):
        now = NOW

        class Source:
            window = 12 * 3600
            status = "GitHub：讀取完成"
            successful_read = True
            clock = staticmethod(lambda: now)
            def __init__(self, events):
                self.events = events
            def refresh(self):
                pass

        recent = mon.ActivityEvent(now - 12 * 60, "developer_dispatch", "", "",
                                   "ASSIGNED", "#763", actor="PM", target="AGY")
        lines = mon.activity_lines([], github=Source([recent]))
        self.assertEqual(lines[0], "PM 上次派工：12 分鐘前（→ AGY，#763）")

        stale = replace(recent, occurred_at=now - 31 * 60)
        lines = mon.activity_lines([], github=Source([stale]))
        self.assertEqual(lines[0], "⚠ PM 上次派工：31 分鐘前；超過 30 分鐘沒有派工（→ AGY，#763）")

        lines = mon.activity_lines([], github=Source([]))
        self.assertEqual(lines[0], "⚠ PM 上次派工：最近 12 小時沒有")

        wrapped = mon._activity_screen_lines([lines[0]], 18)
        self.assertGreater(len(wrapped), 1)
        self.assertTrue(all(mon._width(piece) <= 18 for piece in wrapped))

    def test_pm_last_dispatch_is_unknown_until_github_read_succeeds(self):
        self.assertIn("未知", mon.activity_lines([], github=None)[0])

        class Source:
            window = 12 * 3600
            status = "GitHub：尚未讀取"
            successful_read = False
            events = []
            clock = staticmethod(lambda: NOW)
            def refresh(self): pass

        self.assertIn("未知", mon.activity_lines([], github=Source())[0])

    def test_curses_activity_header_stays_visible_above_bottom_anchored_events(self):
        class Source:
            window = 12 * 3600
            status = "GitHub：讀取完成"
            successful_read = True
            ci_view = None
            pending = None
            scope = ""
            clock = staticmethod(lambda: NOW)
            def __init__(self):
                self.events = [mon.ActivityEvent(
                    NOW - i, "github_comment", "", "", "POSTED", f"#{i + 1}")
                    for i in range(80)]
            def refresh_in_background(self): pass

        monitor = mon.Monitor()
        monitor.github = Source()
        drawn = {}
        class Screen:
            def getmaxyx(self): return (40, 120)
            def erase(self): pass
            def refresh(self): pass
            def move(self, *args): pass
            def clearok(self, *args): pass
        mon._paint_screen(Screen(), monitor, "activity", 0, False, {},
                          lambda row, text, attr=0: drawn.__setitem__(row, text))
        header_text = "".join(drawn[row] for row in sorted(drawn) if row >= 2)
        self.assertIn("PM 上次派工", drawn[3])
        self.assertIn("最近 12 小時沒有", header_text)
        self.assertNotIn("目前沒有可顯示", header_text)

        monitor.stale = True
        drawn.clear()
        mon._paint_screen(Screen(), monitor, "activity", 0, False, {},
                          lambda row, text, attr=0: drawn.__setitem__(row, text))
        self.assertIn("監控程式已更新", drawn[2])
        self.assertIn("PM 上次派工", drawn[3])

    def test_curses_activity_events_do_not_overwrite_hint_on_short_terminal(self):
        class Source:
            window = 12 * 3600
            status = "GitHub：讀取完成"
            successful_read = True
            ci_view = None
            pending = None
            scope = ""
            clock = staticmethod(lambda: NOW)
            events = [mon.ActivityEvent(
                NOW - i, "github_comment", "", "", "POSTED", f"#{i + 1}")
                for i in range(20)]
            def refresh_in_background(self): pass

        monitor = mon.Monitor()
        monitor.github = Source()
        drawn = {}
        class Screen:
            def __init__(self, height): self.height = height
            def getmaxyx(self): return (self.height, 120)
            def erase(self): pass
            def refresh(self): pass
            def move(self, *args): pass
            def clearok(self, *args): pass
        for height in (5, 4):
            drawn.clear()
            mon._paint_screen(Screen(height), monitor, "activity", 0, False, {},
                              lambda row, text, attr=0: drawn.__setitem__(row, text))
            self.assertTrue(any("Tab/1-5" in text for text in drawn.values()))
            self.assertNotIn("PM 上次派工", drawn[height - 1])
            self.assertFalse(any("GitHub #" in text for row, text in drawn.items()
                                 if row >= height - 1))

    def test_developer_dispatch_activity_uses_marker_target(self):
        for target in ("Codex", "AGY", "OpenCode", "Developer"):
            body = (f"<!-- activity: type=developer_dispatch actor=PM target={target} "
                    "status=ASSIGNED -->\nTask")
            events = mon.github_events([comment(763, NOW, body)], [], NOW - 60)
            self.assertEqual([event.event_type for event in events], ["developer_dispatch"])
            line = mon._activity_line(events[0])
            self.assertIn(f"PM → {target}：", line)
            self.assertNotIn("→ Codex", line if target != "Codex" else "")

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
        # Calls beyond the scripted ones (the runner list, CI) fail like a 404.
        code, payload = self.responses.pop(0) if self.responses else (1, b"")
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
        fake = FakeRun((0, []), (0, []), (0, {"runners": []}), (0, runs))
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
            self.assertEqual(len(run.calls), 3)   # comments, pulls, the runner list
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
        self.assertEqual(len(full.calls), mon.GITHUB_PAGES + 3)
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
        self.assertEqual(len(exact.calls), mon.GITHUB_PAGES + 3)
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
                self.assertTrue(source.read_failed)
                self.assertEqual([e.reference for e in source.events], ["#1"])
            # A comment posted between page reads shifts the next page by one.
            shifted = page[-1:] + [dict(comment(200 + i, NOW - 30, "d"), id=200 + i) for i in range(3)]
            source.runner = FakeRun((0, page), (0, shifted), (0, []))
            source.refresh(force=True)
        self.assertEqual(len(source.events), 103)
        self.assertFalse(source.read_failed)

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
        self.assertEqual(pending[0].kind, "repair_gate")
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

    def test_owner_answer_marker_is_typed_and_settles_the_waiting_ask(self):
        ask = ("<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->\n"
               "## Decide G1")
        answer = ("<!-- activity: type=waiting_owner actor=Owner target=PM status=ANSWERED -->\n"
                  "Owner chose option (a)")
        waiting = [comment(304, NOW - 300, ask)]
        # Only the ask: a typed pending event and a ★ request.
        events = mon.github_events(waiting, [], NOW - 3600)
        self.assertEqual([(e.event_type, e.actor, e.target, e.status) for e in events],
                         [("waiting_owner", "PM", "Owner", "WAITING")])
        self.assertIn("PM → Owner：等你決定", mon.render_activity(events)[0])
        self.assertEqual([p.reference for p in mon.owner_pending(waiting, [])], ["#304"])
        # A later Owner answer on the same thread is a typed event and settles it.
        both = waiting + [comment(304, NOW - 60, answer)]
        events = mon.github_events(both, [], NOW - 3600)
        self.assertEqual([(e.event_type, e.actor, e.target, e.status) for e in events],
                         [("waiting_owner", "PM", "Owner", "WAITING"),
                          ("waiting_owner", "Owner", "PM", "ANSWERED")])
        lines = mon.render_activity(events)
        answered = next(line for line in lines if "Owner → PM" in line)
        self.assertIn("已回覆，這項決定不再等你（#304・自述）", answered)
        self.assertIn("Owner chose option (a)", answered)
        self.assertEqual(mon.owner_pending(both, []), [])
        # Integrator is the PM's other name in markers, as for the ask.
        integrator = comment(5, NOW - 9, "<!-- activity: type=waiting_owner actor=Owner "
                                         "target=Integrator status=ANSWERED -->")
        self.assertEqual([e.status for e in mon.github_events([integrator], [], NOW - 99)],
                         ["ANSWERED"])

    def test_invalid_owner_waiting_owner_marker_does_not_settle(self):
        ask = comment(304, NOW - 300, "<!-- activity: type=waiting_owner actor=PM target=Owner "
                                      "status=WAITING -->\n## Decide")
        for fields in ("actor=Owner target=PM status=WAITING",
                       "actor=Owner target=Owner status=ANSWERED",
                       "actor=Owner target=Reviewer status=ANSWERED",
                       "actor=PM target=Owner status=ANSWERED",
                       "actor=owner target=pm status=blocked"):
            with self.subTest(fields=fields):
                later = comment(304, NOW - 60, f"<!-- activity: type=waiting_owner {fields} -->")
                self.assertEqual([p.reference for p in mon.owner_pending([ask, later], [])],
                                 ["#304"])
        for fields in ("actor=Owner target=PM status=ANSWERED",
                       "actor=owner target=integrator status=answered"):   # case-insensitive
            with self.subTest(fields=fields):
                later = comment(304, NOW - 60, f"<!-- activity: type=waiting_owner {fields} -->")
                self.assertEqual(mon.owner_pending([ask, later], []), [])
        # Other marker types keep the loose settlement by any actor=Owner marker.
        other = comment(304, NOW - 60, "<!-- activity: type=repair_authorization actor=Owner "
                                       "target=R2 status=WAITING -->")
        self.assertEqual(mon.owner_pending([ask, other], []), [])

    def test_waiting_owner_direction_and_status_must_match(self):
        bad = ("actor=Owner target=PM status=WAITING",       # the Owner does not wait on itself
               "actor=PM target=Owner status=ANSWERED",       # only the Owner answers
               "actor=Owner target=Owner status=ANSWERED",
               "actor=Owner target=Reviewer status=ANSWERED",
               "actor=Reviewer target=Owner status=WAITING")
        for fields in bad:
            with self.subTest(fields=fields):
                body = f"<!-- activity: type=waiting_owner {fields} -->"
                events = mon.github_events([comment(6, NOW - 9, body)], [], NOW - 99)
                self.assertNotIn("waiting_owner", [e.event_type for e in events])
                actor = fields.split()[0].split("=")[1]
                target = fields.split()[1].split("=")[1]
                status = fields.split()[2].split("=")[1]
                forged = mon.ActivityEvent(NOW - 9, "waiting_owner", "", "", status, "#6",
                                           actor=actor, target=target)
                self.assertEqual(mon._activity_line(forged), "")

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
        self.assertEqual(len(run.calls), 3)
        self.assertEqual([e.reference for e in source.events], ["#6"])
        self.assertIn("每 30 秒", source.status)
        lines = mon.activity_lines([], github=source, background=True)   # not due yet
        self.assertEqual(len(run.calls), 3)
        self.assertIn("GitHub #6：新留言 — bg", lines[1])

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
        self.assertEqual(len(run.calls), 4)
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
GW_AGENT_EVIDENCE = "https://github.com/davidcatcat/davidcattoolsproject/issues/120"
GW_AGENT_ENTRY = {"dispatch": "PAUSED", "decided_by": "PM", "decided_at": "2026-11-03",
                  "reason": "test", "evidence": GW_AGENT_EVIDENCE}
GW_AGENT_OWNER_EVIDENCE = GW_AGENT_EVIDENCE + "#issuecomment-6594101520"   # #120 D1 = A
GW_AGENT_OWNER_ALLOWED = {"dispatch": "ALLOWED", "decided_by": "Owner",
                          "evidence": GW_AGENT_OWNER_EVIDENCE}


def policy(workers):
    """A whole worker-dispatch.json the llmtool gate accepts: every Worker
    present with reason and evidence; `workers` overrides entries (None drops one)."""
    entry = {"dispatch": "ALLOWED", "decided_by": "Owner", "decided_at": "2026-10-22",
             "reason": "test", "evidence": POLICY_EVIDENCE}
    full = {key: dict(entry) for key in sorted(mon.DISPATCH_WORKERS)}
    full["company-gw-agent"] = dict(GW_AGENT_ENTRY)   # pending the Owner decision (#120)
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
        patterns = worker_dispatch._build_reference_patterns({
            'slug': 'davidcatcat/davidcattoolsproject',
            'dispatch_issue': 289, 'dispatch_evidence_pr': 283,
        })
        cls.gate = staticmethod(lambda raw: worker_dispatch._parse_policy(raw, patterns))
        cls.gate_file = staticmethod(lambda path: worker_dispatch._parse_policy(
            worker_dispatch._read_policy_file(Path(path)), patterns))


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
            # company-gw-agent: only PAUSED + PM + exactly issues/120 (#120 WP3b),
            # or ALLOWED + Owner + exactly the D1 = A comment (#120)
            "gw-agent allowed by Owner D1": {"company-gw-agent": dict(GW_AGENT_OWNER_ALLOWED)},
            "gw-agent allowed by Owner D1 + assignment": {"company-gw-agent": dict(
                GW_AGENT_OWNER_ALLOWED, owner_assignments=[good])},
            "gw-agent allowed by PM D1": {"company-gw-agent": dict(
                GW_AGENT_OWNER_ALLOWED, decided_by="PM")},
            "gw-agent paused by Owner D1": {"company-gw-agent": dict(
                GW_AGENT_OWNER_ALLOWED, dispatch="PAUSED")},
            "gw-agent paused by Owner bare #120": {"company-gw-agent": {"decided_by": "Owner"}},
            "gw-agent assignment only by Owner D1": {"company-gw-agent": dict(
                GW_AGENT_OWNER_ALLOWED, dispatch="OWNER_ASSIGNMENT_ONLY")},
            "gw-agent paused by PM D1": {"company-gw-agent": {"evidence": GW_AGENT_OWNER_EVIDENCE}},
            "gw-agent allowed by Owner bare #120": {"company-gw-agent": dict(
                GW_AGENT_OWNER_ALLOWED, evidence=GW_AGENT_EVIDENCE)},
            "gw-agent allowed by Owner other comment": {"company-gw-agent": dict(
                GW_AGENT_OWNER_ALLOWED, evidence=GW_AGENT_EVIDENCE + "#issuecomment-6594101521")},
            "gw-agent allowed by Owner comment +1 digit": {"company-gw-agent": dict(
                GW_AGENT_OWNER_ALLOWED, evidence=GW_AGENT_OWNER_EVIDENCE + "0")},
            "gw-agent allowed by Owner comment -1 digit": {"company-gw-agent": dict(
                GW_AGENT_OWNER_ALLOWED, evidence=GW_AGENT_OWNER_EVIDENCE[:-1])},
            "gw-agent allowed by Owner comment trailing slash": {"company-gw-agent": dict(
                GW_AGENT_OWNER_ALLOWED, evidence=GW_AGENT_OWNER_EVIDENCE + "/")},
            "gw-agent allowed by Owner comment on #1200": {"company-gw-agent": dict(
                GW_AGENT_OWNER_ALLOWED,
                evidence=GW_AGENT_EVIDENCE + "0#issuecomment-6594101520")},
            "gw-agent allowed by Owner lower-case switch": {"company-gw-agent": dict(
                GW_AGENT_OWNER_ALLOWED, dispatch="allowed")},
            "other key Owner D1 comment": {"company-gw": {"evidence": GW_AGENT_OWNER_EVIDENCE}},
            "gw-agent missing": {"company-gw-agent": None},
            "gw-agent allowed": {"company-gw-agent": {"dispatch": "ALLOWED"}},
            "gw-agent assignment only": {"company-gw-agent": {"dispatch": "OWNER_ASSIGNMENT_ONLY"}},
            "gw-agent by Owner": {"company-gw-agent": {"decided_by": "Owner"}},
            "gw-agent by Owner #289": {"company-gw-agent": {"decided_by": "Owner",
                                                            "evidence": POLICY_EVIDENCE}},
            "gw-agent allowed by Owner #289": {"company-gw-agent": {
                "dispatch": "ALLOWED", "decided_by": "Owner", "evidence": POLICY_EVIDENCE}},
            "gw-agent PM #289": {"company-gw-agent": {"evidence": POLICY_EVIDENCE}},
            "gw-agent comment on #120": {"company-gw-agent": {
                "evidence": GW_AGENT_EVIDENCE + "#issuecomment-1"}},
            "gw-agent other issue": {"company-gw-agent": {"evidence": GW_AGENT_EVIDENCE + "0"}},
            "gw-agent decided_by pm": {"company-gw-agent": {"decided_by": "pm"}},
            "gw-agent empty reason": {"company-gw-agent": {"reason": ""}},
            "gw-agent bad date": {"company-gw-agent": {"decided_at": "2026-11-31"}},
            "gw-agent extra field": {"company-gw-agent": {"note": "x"}},
            "gw-agent assignment ok": {"company-gw-agent": {"owner_assignments": [good]}},
            "gw-agent assignment bad ref": {"company-gw-agent": {"owner_assignments": [
                dict(good, owner_assignment_ref=GW_AGENT_EVIDENCE + "#issuecomment-1")]}},
            "gw-agent unhashable switch": {"company-gw-agent": {"dispatch": []}},
            "gw-agent evidence not a string": {"company-gw-agent": {"evidence": None}},
            "other key PM #120 paused": {"company-gw": {"dispatch": "PAUSED", "decided_by": "PM",
                                                        "evidence": GW_AGENT_EVIDENCE}},
            "other key Owner #120": {"company-gw": {"evidence": GW_AGENT_EVIDENCE}},
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
        for label in ("gw-agent allowed by Owner D1", "gw-agent allowed by Owner D1 + assignment"):
            with self.subTest(must_accept=label):
                self.assertTrue(self.agree(json.dumps(policy(cases[label])).encode()))
        switches, problem = mon.worker_dispatch(
            json.dumps(policy(cases["gw-agent allowed by Owner D1"])).encode())
        self.assertEqual(problem, "")
        self.assertEqual(switches["company-gw-agent"][:2], ("ALLOWED", "Owner"))
        for label in ("gw-agent allowed", "gw-agent by Owner", "gw-agent PM #289",
                      "other key PM #120 paused", "gw-agent missing",
                      "gw-agent allowed by PM D1", "gw-agent paused by Owner D1",
                      "gw-agent paused by Owner bare #120", "gw-agent assignment only by Owner D1",
                      "gw-agent paused by PM D1", "gw-agent allowed by Owner bare #120",
                      "gw-agent allowed by Owner other comment",
                      "gw-agent allowed by Owner comment +1 digit",
                      "gw-agent allowed by Owner comment -1 digit",
                      "gw-agent allowed by Owner comment trailing slash",
                      "gw-agent allowed by Owner comment on #1200",
                      "gw-agent allowed by Owner lower-case switch",
                      "other key Owner D1 comment"):
            with self.subTest(must_reject=label):
                self.assertEqual(mon.worker_dispatch(json.dumps(policy(cases[label])).encode())[1],
                                 "rejected")

    def test_worker_keys_match_the_gate(self):
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "tools", "llmtool")
        sys.path.insert(0, root)
        try:
            from llmtool import worker_dispatch
        finally:
            sys.path.remove(root)
        self.assertEqual(mon.DISPATCH_WORKERS, worker_dispatch.WORKERS)

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

    def rows(self, processes=(), **kwargs):
        return mon.office_rows(list(processes), self.NOW, codex_root=str(self.codex),
                               claude_root=str(self.claude),
                               canonical=self.canonical, **kwargs)

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
        self.assertIn("wp-1  執行中  →  claude developer（互動） 需注意  （claude-ui-agent）", lines[1])
        self.assertNotIn("SECRET", "\n".join(lines))

    def test_agent_process_cwd_binds_transcript_free_d6_session(self):
        stamp = mon.time.strftime("%Y-%m-%dT%H:%M:%SZ", mon.time.gmtime(self.NOW - 600))
        self._register("wp-1", str(self.task), last_verified_at=stamp,
                       trusted_agent_id="codex-developer-agent")
        for role in ("Codex CLI", "Codex CLI (node launcher)", "Claude Code CLI",
                     "Claude Code CLI (node launcher)", "OpenCode CLI", "OpenCode CLIENT",
                     "OpenCode CLI (node launcher)", "OpenCode CLIENT (node launcher)"):
            with self.subTest(role=role):
                process = mon.Process(81, 1, 81, "00:12", "S", role, "cli", "")
                rows = mon.office_rows([process], self.NOW, codex_root=str(self.codex),
                                       claude_root=str(self.claude), canonical=self.canonical,
                                       cwd_of=lambda pid: str(self.task))
                task_rows = [row for row in rows if row.task == "task wp-1"]
                self.assertEqual(len(task_rows), 1)
                self.assertEqual((task_rows[0].state, task_rows[0].role, task_rows[0].evidence),
                                 (mon.WORKING, "developer", "程序"))
                task_lines = mon.d6_task_lines(self.canonical, rows, self.NOW)
                self.assertIn("wp-1", next(line for line in task_lines if "wp-1" in line))
                gone = mon.office_rows([], self.NOW, codex_root=str(self.codex),
                                       claude_root=str(self.claude), canonical=self.canonical,
                                       cwd_of=lambda pid: str(self.task))
                self.assertFalse(any(row.task == "task wp-1" for row in gone))

    def test_idle_claude_session_row_covers_its_d6_process(self):
        self._write(self.claude / "proj" / "idle.jsonl", [
            {"type": "user", "cwd": str(self.task), "entrypoint": "cli"}], age=10)
        sessions = self.claude.parent / "sessions"
        sessions.mkdir()
        (sessions / "81.json").write_text(json.dumps(
            {"pid": 81, "sessionId": "idle", "status": "idle", "name": "claude-developer"}),
            encoding="utf-8")
        claude = mon.Process(81, 1, 81, "00:12", "S", "Claude Code CLI", "claude", "")
        rows = mon.office_rows([claude], self.NOW, codex_root=str(self.codex),
                               claude_root=str(self.claude), canonical=self.canonical,
                               cwd_of=lambda pid: str(self.task))
        task_rows = [row for row in rows if row.task == "task wp-1"]
        self.assertEqual([(row.state, row.evidence) for row in task_rows],
                         [(mon.IDLE, "claude 紀錄")])

    def test_idle_codex_session_row_covers_its_d6_process(self):
        self._codex("completed", {"id": "T9", "cwd": str(self.task),
                                  "thread_source": "user", "originator": "codex-tui"},
                    ["task_started", "task_complete"], age=10)
        codex = mon.Process(82, 1, 82, "00:12", "S", "Codex CLI", "codex", "")
        rows = mon.office_rows([codex], self.NOW, codex_root=str(self.codex),
                               claude_root=str(self.claude), canonical=self.canonical,
                               cwd_of=lambda pid: str(self.task))
        task_rows = [row for row in rows if row.task == "task wp-1"]
        self.assertEqual([(row.state, row.evidence) for row in task_rows],
                         [(mon.IDLE, "codex 紀錄")])

    def test_closed_claude_session_does_not_cover_live_codex_d6_process(self):
        closed = mon.OfficeRow("claude developer", mon.CLOSED, "previous session",
                               1.0, 10, task="task wp-1")
        codex = mon.Process(82, 1, 82, "00:12", "S", "Codex CLI", "codex", "")
        rows = mon._d6_process_rows([codex], [closed], self.canonical,
                                    lambda pid: str(self.task))
        task_rows = [row for row in rows if row.task == "task wp-1"]
        self.assertEqual(len(task_rows), 1)
        self.assertEqual((task_rows[0].state, task_rows[0].evidence),
                         (mon.WORKING, "程序"))

    def test_d6_process_role_comes_from_registered_agent_id(self):
        self._register("wp-1", str(self.task), trusted_agent_id="claude-reviewer-agent")
        proc = mon.Process(81, 1, 81, "00:12", "S", "Claude Code CLI", "claude", "")
        rows = mon._d6_process_rows([proc], [], self.canonical, lambda pid: str(self.task))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].name, rows[0].role), ("claude reviewer", "reviewer"))

    def test_d6_process_role_is_read_from_the_whole_agent_id(self):
        # Owner #564: the gw ladder Developer (codex-developer-gpt-6-luna) showed
        # as a plain "codex" row because only an id ending in the role word counted.
        codex = mon.Process(82, 1, 82, "00:12", "S", "Codex CLI", "codex", "")
        claude = mon.Process(81, 1, 81, "00:12", "S", "Claude Code CLI", "claude", "")
        for proc, agent, expected in (
                (codex, "codex-developer-gpt-6-luna", ("codex developer", "developer")),
                (codex, "codex-reviewer", ("codex reviewer", "reviewer")),
                (codex, "codex-high-debug-gpt-6.1-sol", ("codex developer", "developer")),
                (codex, {"x": 1}, ("codex developer", "developer")),   # unreadable id, not a -crv- name
                (claude, "claude-developer-ui", ("claude developer", "developer")),
                (claude, "claude-pm-1ec9782e", ("claude pm", "pm")),
                (claude, {"x": 1}, ("claude", ""))):   # other families claim no role
            with self.subTest(agent=agent, family=proc.role):
                self._register("wp-1", str(self.task), trusted_agent_id=agent)
                rows = mon._d6_process_rows([proc], [], self.canonical, lambda pid: str(self.task))
                self.assertEqual([(row.name, row.role) for row in rows], [expected])
                self.assertEqual(rows[0].detail, "task wp-1（程序 cwd）")

    def test_codex_high_debug_d6_run_shows_debug_reviewer_only_for_bound_run(self):
        task_id = "diagnostic-hd2-a1b2c3d"
        root = self.task.parent / task_id
        os.makedirs(root)
        self._register(task_id, str(root), trusted_agent_id="codex-high-debug-gpt-6.1-sol")
        self._codex("high-debug", {"id": "T-HD", "cwd": str(root), "thread_source": "user",
                                   "originator": "codex-tui"}, ["task_started"], age=30)
        row = next(row for row in self.rows() if row.task == f"task {task_id}")
        self.assertEqual((row.name, row.role), ("codex debug reviewer", "debug reviewer"))

        other_id = "diagnostic-hd3-b2c3d4e"
        other_root = self.task.parent / other_id
        os.makedirs(other_root)
        self._register(other_id, str(other_root), trusted_agent_id="codex-developer-gpt-6-luna")
        self._codex("ordinary", {"id": "T-ordinary", "cwd": str(other_root),
                                  "thread_source": "user", "originator": "codex-tui"},
                    ["task_started"], age=30)
        ordinary = next(row for row in self.rows() if row.task == f"task {other_id}")
        self.assertEqual((ordinary.name, ordinary.role), ("codex developer", "developer"))

    def test_codex_session_in_a_d6_shows_the_role_its_record_binds(self):
        # Owner #564: the Codex Reviewer of PR #525 (registry trusted_agent_id
        # codex-reviewer) was shown as "codex developer".
        review = self.task.parent / "audit-20261028-crv1-06c5745"
        os.makedirs(review)
        self._register("audit-20261028-crv1-06c5745", str(review), trusted_agent_id="codex-reviewer")
        self._register("wp-1", str(self.task), trusted_agent_id="codex-developer-gpt-6-luna")
        self._codex("rev", {"id": "T1", "cwd": str(review), "thread_source": "user",
                            "originator": "codex-tui"}, ["task_started"], age=30)
        self._codex("dev", {"id": "T2", "cwd": str(self.task), "thread_source": "user",
                            "originator": "codex-tui"}, ["task_started"], age=30)
        rows = self.by_name(self.rows())
        self.assertEqual((rows["codex reviewer"].role, rows["codex reviewer"].task),
                         ("reviewer", "task audit-20261028-crv1-06c5745"))
        self.assertEqual((rows["codex developer"].role, rows["codex developer"].task),
                         ("developer", "task wp-1"))
        self.assertNotIn("codex", rows)

    def test_claude_session_in_a_d6_shows_the_role_its_record_binds(self):
        # #565 v2: the D6 record's agent beats the launcher name and the old
        # "developer by placement" default; without either, no role is claimed.
        for agent, launcher, expected in (
                ("claude-reviewer", "claude-developer-g8", ("claude reviewer", "reviewer")),
                ("claude-developer-ui-486c15a4", None, ("claude developer", "developer")),
                ("codex-developer-gpt-6-luna", None, ("claude developer", "developer")),
                ({"x": 1}, "claude-reviewer", ("claude reviewer", "reviewer")),   # launcher record
                ({"x": 1}, None, ("claude", ""))):
            with self.subTest(agent=agent, launcher=launcher):
                self._register("wp-1", str(self.task), trusted_agent_id=agent)
                entries = [{"type": "user", "cwd": str(self.task), "entrypoint": "cli"}]
                if launcher:
                    entries.append({"type": "agent-name", "agentName": launcher, "sessionId": "s"})
                entries.append({"type": "ai-title", "aiTitle": "title s"})
                self._write(self.claude / "proj" / "s.jsonl", entries, age=600)
                row = next(row for row in self.rows() if row.task == "task wp-1")
                self.assertEqual((row.name, row.role), expected)

    def test_opencode_session_in_a_d6_shows_the_role_its_record_binds(self):
        cli = mon.Process(40, 1, 40, "30:00", "S+", "OpenCode CLI", "opencode", "")
        running = (str(self.task), "Issue 271 fix", self.NOW - 10, self.NOW - 1800)
        for agent, expected in (("opencode-developer-deepseek", "developer"),
                                ("claude-reviewer", "reviewer"), ({"x": 1}, "")):
            with self.subTest(agent=agent):
                self._register("wp-1", str(self.task), trusted_agent_id=agent)
                row, = mon._cli_rows([cli], [running], self.NOW, self.canonical,
                                     {40: str(self.task)}.get, "opencode", "OpenCode CLIENT", 50, "")
                self.assertEqual((row.role, row.task, row.state), (expected, "task wp-1", mon.WORKING))

    def test_d6_role_reuses_the_registry_snapshot_the_placement_check_loaded(self):
        # #565 v2 P3: no registry I/O per row. Once _place has loaded the
        # registry for this refresh, the role comes from that snapshot even
        # when the registry can no longer be stat'ed.
        self._register("wp-1", str(self.task), trusted_agent_id="codex-reviewer")
        self.assertEqual(mon._place(str(self.task), self.canonical), ("task", "wp-1"))
        with patch.object(mon.os, "lstat", side_effect=OSError("no registry I/O expected")):
            self.assertEqual(mon._d6_role(self.canonical, "wp-1", "developer"), "reviewer")
        # With nothing loaded yet it reads the registry once itself.
        mon._registry_cache.clear()
        self.assertEqual(mon._d6_role(self.canonical, "wp-1", "developer"), "reviewer")

    def test_codex_session_in_a_d6_with_an_unreadable_agent_id_falls_back_to_the_crv_name(self):
        review = self.task.parent / "audit-20261028-crv2-06c5745"
        os.makedirs(review)
        for task_id, root, expected in (("audit-20261028-crv2-06c5745", review, "reviewer"),
                                        ("wp-1", self.task, "developer")):
            with self.subTest(task=task_id):
                self._register(task_id, str(root), trusted_agent_id={"x": 1})
                self._codex(task_id, {"id": f"T-{task_id}", "cwd": str(root), "thread_source": "user",
                                      "originator": "codex-tui"}, ["task_started"], age=30)
                row = next(row for row in self.rows() if row.task == f"task {task_id}")
                self.assertEqual((row.name, row.role), (f"codex {expected}", expected))

    def test_open_codex_cli_without_a_session_row_is_an_idle_member(self):
        launcher = mon.Process(81, 1, 81, "02:43:00", "S+", "Codex CLI (node launcher)", "node", "")
        codex = mon.Process(82, 81, 81, "02:43:00", "S+", "Codex CLI", "codex",
                            "Codex CLI (node launcher) 81")
        infra = [mon.Process(90, 1, 90, "05:00", "Ss", "Codex daemon", "codex", ""),
                 mon.Process(91, 90, 91, "05:00", "Ss", "Codex APP-SERVER", "codex", "Codex daemon 90"),
                 mon.Process(92, 91, 92, "05:00", "S", "Codex code-mode host", "codex-code-mode-host",
                             "Codex APP-SERVER 91")]
        procs = [launcher, codex] + infra
        rows = mon.office_rows(procs, self.NOW, codex_root=str(self.codex),
                               claude_root=str(self.claude), canonical=self.canonical,
                               cwd_of=lambda pid: self.canonical)
        codex_rows = [row for row in rows if row.name.startswith("codex")]
        self.assertEqual([(row.name, row.state, row.detail.split("，")[0]) for row in codex_rows],
                         [("codex cli #1", mon.IDLE, "本 repo：已開啟")])
        # The launcher pair is one CLI; the daemon, app-server and code-mode host are not members.
        self.assertEqual(mon.summary(procs, [], []), "Codex 1" + NO_RUNNERS)
        # With an active rollout the session row is that CLI: no extra row.
        self._codex("live", {"id": "T1", "cwd": self.canonical, "thread_source": "user",
                             "originator": "codex-tui"}, ["task_started"], age=5)
        rows = mon.office_rows(procs, self.NOW, codex_root=str(self.codex),
                               claude_root=str(self.claude), canonical=self.canonical,
                               cwd_of=lambda pid: self.canonical)
        self.assertEqual([(row.name, row.state) for row in rows if row.name.startswith("codex")],
                         [("codex 主對話", mon.WORKING)])
        # A running `codex exec` call is shown by its own row, not as an idle CLI.
        run = mon.Process(83, 1, 83, "00:30", "S", "Codex CLI", "codex", "")
        self._codex("exec", {"id": "T2", "cwd": self.canonical, "thread_source": "user",
                             "originator": "codex_exec"}, ["task_started"], age=5)
        rows = mon.office_rows(procs + [run], self.NOW, codex_root=str(self.codex),
                               claude_root=str(self.claude), canonical=self.canonical,
                               cwd_of=lambda pid: self.canonical)
        self.assertFalse(any(row.name.startswith("codex cli") for row in rows))

    def test_open_claude_cli_without_a_session_row_is_an_idle_member(self):
        self._write(self.claude / "proj" / "s1.jsonl", [
            {"type": "user", "cwd": self.canonical, "entrypoint": "cli"}], age=10)
        sessions = self.claude.parent / "sessions"
        sessions.mkdir()
        (sessions / "81.json").write_text(json.dumps(
            {"pid": 81, "sessionId": "s1", "status": "idle"}), encoding="utf-8")
        procs = [mon.Process(81, 1, 81, "05:00", "S+", "Claude Code CLI", "claude", ""),
                 mon.Process(82, 1, 82, "05:00", "S+", "Claude Code CLI", "claude", "")]
        rows = mon.office_rows(procs, self.NOW, codex_root=str(self.codex),
                               claude_root=str(self.claude), canonical=self.canonical,
                               cwd_of=lambda pid: self.canonical)
        self.assertEqual(sorted((row.name, row.state) for row in rows if row.name.startswith("claude")),
                         [("claude cli #1", mon.IDLE), ("claude 互動", mon.IDLE)])
        self.assertEqual(mon.summary(procs, [], str(sessions)), "Claude 2" + NO_RUNNERS)

    def test_open_agent_clis_outside_the_repo_get_a_row_like_opencode(self):
        elsewhere = os.path.join(self.tmp.name, "other-project")
        procs = [mon.Process(81, 1, 81, "05:00", "S+", "Codex CLI", "codex", ""),
                 mon.Process(82, 1, 82, "05:00", "S+", "Claude Code CLI", "claude", ""),
                 mon.Process(83, 1, 83, "05:00", "S+", "OpenCode CLI", "opencode", "")]
        rows = mon.office_rows(procs, self.NOW, codex_root=str(self.codex),
                               claude_root=str(self.claude), canonical=self.canonical,
                               opencode_db=os.path.join(self.tmp.name, "none.db"),
                               cwd_of=lambda pid: elsewhere)
        clis = sorted((row.name, row.state, row.detail) for row in rows if " cli #" in row.name)
        # Codex/Claude sessions elsewhere are not read, so only "open" is claimed;
        # OpenCode reads every folder's sessions.
        self.assertEqual(clis, [("claude cli #1", mon.UNCHECKED, "其他資料夾：已開啟"),
                                ("codex cli #1", mon.UNCHECKED, "其他資料夾：已開啟"),
                                ("opencode cli #1", mon.IDLE,
                                 "其他資料夾：已開啟，沒有執行中的任務，已開 5 分鐘")])
        self.assertEqual(mon.summary(procs, [], []), "Claude 1  Codex 1  OpenCode 1" + NO_RUNNERS)

    def test_session_that_cd_into_a_d6_keeps_its_cli_to_one_row(self):
        # The CLI was opened in the canonical checkout; its session `cd`s into the D6.
        self._write(self.claude / "proj" / "s1.jsonl", [
            {"type": "user", "cwd": str(self.task), "entrypoint": "cli"}], age=5)
        sessions = self.claude.parent / "sessions"
        sessions.mkdir()
        (sessions / "81.json").write_text(json.dumps(
            {"pid": 81, "sessionId": "s1", "status": "busy"}), encoding="utf-8")
        procs = [mon.Process(81, 1, 81, "05:00", "S+", "Claude Code CLI", "claude", "")]
        rows = mon.office_rows(procs, self.NOW, codex_root=str(self.codex),
                               claude_root=str(self.claude), canonical=self.canonical,
                               cwd_of=lambda pid: self.canonical)
        self.assertEqual([(row.state, row.task) for row in rows if row.name.startswith("claude")],
                         [(mon.WORKING, "task wp-1")])
        self.assertEqual(mon.summary(procs, [], str(sessions)), "Claude 1" + NO_RUNNERS)

    def test_session_that_cd_into_a_d6_never_takes_a_cli_outside_the_repo(self):
        self._write(self.claude / "proj" / "s1.jsonl", [
            {"type": "user", "cwd": str(self.task), "entrypoint": "cli"}], age=5)
        cwds = {70: "/elsewhere/x", 81: self.canonical, 82: self.canonical}
        cli = lambda pid: mon.Process(pid, 1, pid, "30:00", "S+", "Claude Code CLI", "claude", "")

        def claude_rows(procs):
            rows = mon.office_rows(procs, self.NOW, codex_root=str(self.codex),
                                   claude_root=str(self.claude), canonical=self.canonical,
                                   cwd_of=cwds.get)
            return sorted((row.state, row.detail) for row in rows if row.name.startswith("claude"))

        procs = [cli(70), cli(81)]
        self.assertEqual(claude_rows(procs), [(mon.WORKING, "task wp-1"),
                                              (mon.UNCHECKED, "其他資料夾：已開啟")])
        self.assertEqual(mon.summary(procs, [], []), "Claude 2" + NO_RUNNERS)
        # Two canonical CLIs for one session that moved: which is free is a guess.
        procs.append(cli(82))
        self.assertEqual(claude_rows(procs), [(mon.WORKING, "task wp-1"),
                                              (mon.UNCHECKED, "其他資料夾：已開啟"),
                                              (mon.UNCHECKED, "本 repo：已開啟")])
        self.assertEqual(mon.summary(procs, [], []), "Claude 3" + NO_RUNNERS)

    def test_process_fallback_does_not_duplicate_transcript_task_row(self):
        self._codex("dev", {"id": "T9", "cwd": str(self.task), "thread_source": "user"},
                    ["task_started"], age=5)
        process = mon.Process(81, 1, 81, "00:12", "S", "Codex CLI", "codex", "")
        rows = mon.office_rows([process], self.NOW, codex_root=str(self.codex),
                               claude_root=str(self.claude), canonical=self.canonical,
                               cwd_of=lambda pid: str(self.task))
        task_rows = [row for row in rows if row.task == "task wp-1"]
        self.assertEqual(len(task_rows), 1)
        self.assertEqual(task_rows[0].evidence, "codex 紀錄")

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
        # The same completed transcript means waiting only while a CLI is live.
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

    def test_rows_show_the_short_session_id_after_the_name(self):
        self._write(self.claude / "proj" / "8db9ba20-da22-4c36-ba78-9cd68605cc09.jsonl", [
            {"type": "user", "cwd": self.canonical, "entrypoint": "cli"}], age=60)
        # Codex ids are UUIDv7: threads of the same minute share the head, so the tail is shown.
        for name, tid in (("a", "01a173c3-2b86-7600-963f-8f9addaf5381"),
                          ("b", "01a173c3-55f9-7bb2-8aeb-da71da18fd15")):
            self._codex(name, {"id": tid, "cwd": str(self.task), "thread_source": "user",
                               "originator": "codex_exec"}, ["task_started"], age=30)
        rows = self.rows()
        self.assertEqual(sorted(row.sid for row in rows if row.name == "codex developer"),
                         ["da18fd15", "ddaf5381"])
        claude = self.by_name(rows)["claude"]
        self.assertEqual(claude.sid, "8db9ba20")
        lines = mon.office_lines(rows)
        self.assertTrue(any(line.startswith("claude 8db9ba20 ") for line in lines), lines)
        self.assertEqual(mon._codex_sid("not-a-uuid PROMPT-SECRET"), "")
        # Two sessions alike but for their id keep separate event keys.
        one = mon.OfficeRow("claude 互動", mon.IDLE, "fix monitor", 1.0, 30, sid="aaaaaaaa")
        two = mon.OfficeRow("claude 互動", mon.WORKING, "fix monitor", 1.0, 30, sid="bbbbbbbb")
        old, _ = mon.office_events({}, [one, two])
        self.assertEqual(len(old), 2)
        self.assertEqual(mon.office_events(old, [two, one])[1], [])
        _, events = mon.office_events(old, [replace(one, state=mon.WORKING), two])
        self.assertEqual(events, [("+", "claude 互動 aaaaaaaa「fix monitor」：待命 → 工作中")])

    def test_finished_codex_exec_run_is_done_then_leaves(self):
        meta = {"id": "E1", "cwd": str(self.task), "thread_source": "user", "originator": "codex_exec"}
        self._codex("exec", meta, ["task_started", "task_complete"], age=600)
        self.assertEqual(self.by_name(self.rows())["codex developer"].state, mon.DONE)
        self._codex("exec", meta, ["task_started"], age=60)
        self.assertEqual(self.by_name(self.rows())["codex developer"].state, mon.WORKING)
        self._codex("exec", meta, ["task_started", "task_complete"], age=mon.OFFICE_DONE_WINDOW + 60)
        self.assertNotIn("codex developer", self.by_name(self.rows()))
        # In the canonical checkout too it is a one-shot call, as `claude -p`
        # is: how it was started decides, not where it runs.
        self._codex("exec", dict(meta, cwd=self.canonical), ["task_started", "task_complete"], age=600)
        rows = self.by_name(self.rows())
        self.assertNotIn("codex 主對話", rows)
        row = rows["codex 單次呼叫"]
        self.assertEqual((row.state, row.main, row.kind), (mon.DONE, False, "subagent"))
        self.assertFalse(any(line.startswith("⚠") for line in mon.office_lines(self.rows())))

    def test_stalled_codex_exec_is_done_when_process_is_gone_or_silent_for_an_hour(self):
        meta = {"id": "E1", "cwd": str(self.task), "thread_source": "user",
                "originator": "codex_exec"}
        self._codex("exec", meta, ["task_started"], age=20 * 60)
        row = self.by_name(self.rows())["codex developer"]
        self.assertEqual(row.state, mon.DONE)
        self.assertTrue(row.codex_exec_started)
        self.assertFalse(any(line.startswith("⚠") for line in mon.office_lines(self.rows())))

        # Only a running `codex exec` keeps it 無回應; an open Codex TUI does not.
        tui = mon.Process(82, 1, 82, "00:30", "S", "Codex CLI", "codex", "")
        row = self.by_name(self.rows([tui]))["codex developer"]
        self.assertEqual(row.state, mon.DONE)
        self.assertIn("推測 codex exec 已結束", row.detail)
        codex = mon.Process(83, 1, 83, "00:30", "S", "Codex CLI", "codex", "", codex_exec=True)
        row = self.by_name(self.rows([codex]))["codex developer"]
        self.assertEqual(row.state, mon.STALLED)

        # Silent for an hour: ended whatever runs, and past the done window it leaves.
        self._codex("exec", meta, ["task_started"], age=61 * 60)
        self.assertNotIn("codex developer", self.by_name(self.rows([codex])))

    def test_abandoned_codex_exec_runs_are_not_stalled_beside_a_live_one(self):
        # Owner report 2026-11-03: two `codex exec` runs started together and
        # were killed; with another Codex CLI open both stayed 無回應.
        for name, tid in (("a", "01a19f05-7146-7d71-bb94-8917a877e7ad"),
                          ("b", "01a19f05-6b09-7763-9127-50d0922dfe1d")):
            self._codex(name, {"id": tid, "cwd": str(self.task), "thread_source": "user",
                               "originator": "codex_exec"}, ["task_started"], age=20 * 60)
        tui = mon.Process(82, 1, 82, "00:30", "S", "Codex CLI", "codex", "")
        states = sorted(row.state for row in self.rows([tui]) if row.name == "codex developer")
        self.assertEqual(states, [mon.DONE, mon.DONE])
        self.assertFalse(any(line.startswith("⚠") for line in mon.office_lines(self.rows([tui]))))
        # One live `codex exec`: at most one of them may be the live, silent one.
        live = mon.Process(83, 1, 83, "25:00", "S", "Codex CLI", "codex", "", codex_exec=True)
        states = sorted(row.state for row in self.rows([tui, live]) if row.name == "codex developer")
        self.assertEqual(states, sorted([mon.DONE, mon.STALLED]))
        # A run that wrote recently is working, whatever the process table says.
        self._codex("c", {"id": "01a19f05-0000-7000-8000-000000000001", "cwd": str(self.task),
                          "thread_source": "user", "originator": "codex_exec"},
                    ["task_started"], age=60)
        rows = [row for row in self.rows([tui]) if row.name == "codex developer"]
        self.assertEqual(sorted(row.state for row in rows), sorted([mon.WORKING, mon.DONE, mon.DONE]))

    def test_codex_exec_command_flag_keeps_no_arguments(self):
        secret = "CODEX-ARGV-SECRET-7f3a"   # unique: other tests cache "PROMPT-SECRET" files
        snap = mon.snapshot_from([
            (90, 1, 90, "00:10", "S", f"/opt/bin/codex exec --json {secret}"),
            (91, 1, 91, "00:10", "S", "/opt/bin/codex --model x"),
            (92, 1, 92, "00:10", "S", "node /opt/lib/bin/codex exec"),
        ], self_pid=99999)
        flags = {p.pid: (p.role, p.codex_exec) for p in snap.processes}
        self.assertEqual(flags, {90: ("Codex CLI", True), 91: ("Codex CLI", False),
                                 92: ("Codex CLI (node launcher)", True)})
        self.assertNotIn(secret, repr(snap.processes))
        # The flag is derived without retaining the command line anywhere:
        # no cache keyed by raw argv, and no module-level container holds it.
        self.assertFalse(hasattr(mon._codex_exec_command, "cache_info"))
        self.assertFalse(hasattr(mon._codex_exec_command, "__wrapped__"))
        for name, value in vars(mon).items():
            if isinstance(value, (dict, list, set, tuple, frozenset)):
                self.assertNotIn(secret, repr(value), name)

    def test_row_caches_keep_no_argv(self):
        secret = "ROW-ARGV-SECRET-91c4"   # unique to this test
        for function in (mon.safe_name, mon.classify, mon._remote_test_job):
            self.assertFalse(hasattr(function, "cache_info"), function.__name__)
            self.assertFalse(hasattr(function, "__wrapped__"), function.__name__)
        snap = mon.snapshot_from([
            (60, 1, 60, "00:10", "S", f"claude -p --model sonnet {secret}"),
            (61, 60, 60, "00:10", "S", f"/bin/zsh -c echo {secret}"),
            (62, 1, 62, "00:10", "S", f"python3 -m llmtool task remote-test abcdef12 --sha {'a' * 40}"
                                      f" --module test_x --ref {secret}"),
            (63, 1, 63, "00:10", "S", f"ssh host python3 - {'b' * 40} {secret} /repo /py test_y"),
            (64, 1, 64, "00:10", "S", f"/opt/bin/codex exec {secret}"),
        ], self_pid=99999)
        self.assertEqual({p.pid: (p.role, p.name, p.job, p.codex_exec) for p in snap.processes}, {
            60: ("Claude Code CLI", "claude", (), False),
            61: ("  child", "zsh", (), False),
            62: ("llmtool CLI", "python3", ("cli", "abcdef12", "aaaaaaa", ("test_x",)), False),
            64: ("Codex CLI", "codex", (), True)})
        self.assertNotIn(secret, repr(snap.processes))
        self.assertTrue(mon._row_facts_cache)
        for key, value in mon._row_facts_cache.items():
            self.assertIsInstance(key, bytes)
            self.assertNotIn(secret.encode(), key)
            self.assertNotIn(secret, repr(value))
        for name, value in vars(mon).items():
            if isinstance(value, (dict, list, set, tuple, frozenset)):
                self.assertNotIn(secret, repr(value), name)

    def test_row_facts_are_computed_once_per_command_and_evicted_on_exit(self):
        entries = [(100 + i, 1, 100 + i, "00:10", "S", f"/bin/proc{i % 250} --arg {i % 250}")
                   for i in range(500)] + [(900, 1, 900, "00:10", "S", "claude -p x")]
        first = mon.snapshot_from(entries, self_pid=99999)
        with patch.object(mon, "_row_facts", wraps=mon._row_facts) as facts:
            again = mon.snapshot_from(entries, self_pid=99999)
            self.assertEqual(facts.call_count, 0)   # every row reused its earlier result
            self.assertEqual(again.processes, first.processes)
            # A new command is computed once, however many rows share it.
            mon.snapshot_from(entries + [(901, 1, 901, "00:10", "S", "claude -p y"),
                                         (902, 1, 902, "00:10", "S", "claude -p y")], self_pid=99999)
            self.assertEqual(facts.call_count, 1)
        self.assertEqual(len(mon._row_facts_cache), 252)   # 250 + "claude -p x" + "claude -p y"
        # Processes that exited leave the cache on the next poll.
        mon.snapshot_from(entries[:10], self_pid=99999)
        self.assertEqual(len(mon._row_facts_cache), 10)
        with patch.object(mon, "ROW_FACTS_MAX", 5):
            mon.snapshot_from(entries[:10], self_pid=99999)
            self.assertEqual(mon._row_facts_cache, {})

    def test_codex_rows_show_what_they_do_like_claude_rows(self):
        path = self.codex.parent / "state.sqlite"
        db = sqlite3.connect(path)
        db.execute("create table threads(id, title, first_user_message)")
        db.executemany("insert into threads values(?, ?, 'PROMPT-SECRET')", [
            ("E1", "You are the Codex Reviewer (read-only) for PR #356\nPROMPT-SECRET token=abc"),
            ("T1", "Fix the monitor rows"), ("D1", "Developer task title")])
        db.commit()
        db.close()
        self._codex("exec", {"id": "E1", "cwd": self.canonical, "thread_source": "user",
                             "originator": "codex_exec"}, ["task_started", "task_complete"], age=600)
        self._codex("tui", {"id": "T1", "cwd": self.canonical, "thread_source": "user",
                            "originator": "codex-tui"}, ["task_started", "task_complete"], age=90)
        day = mon.time.strftime("%Y/%m/%d", mon.time.localtime(self.NOW))
        self._write(self.codex / day / "rollout-dev.jsonl", [
            {"type": "session_meta", "payload": {"id": "D1", "cwd": str(self.task),
                                                 "thread_source": "user", "originator": "codex_exec"}},
            {"type": "event_msg", "payload": {"type": "task_started"}},
            {"type": "event_msg", "payload": {"type": "item_completed",
                                              "item": {"type": "Reasoning", "raw_content": "REASONING-SECRET"}}}],
            age=5)
        tui = [mon.Process(8, 1, 8, "01:14", "S+", "Codex CLI", "codex", "")]
        with patch.object(mon, "CODEX_STATE", str(path)):
            rows = self.by_name(self.rows(tui))
        # The title's first line, shortened and redacted; never the rest of the prompt.
        self.assertTrue(rows["codex 單次呼叫"].detail.startswith("You are the Codex Reviewer (read-only)"),
                        rows["codex 單次呼叫"].detail)
        self.assertIn("Fix the monitor rows", rows["codex 主對話"].detail)
        # Working: what it is doing now beats the title.
        self.assertEqual(rows["codex developer"].detail, "思考中")
        self.assertEqual(rows["codex developer"].state, mon.WORKING)
        text = "\n".join(mon.office_lines(list(rows.values())))
        for secret in SECRETS:
            self.assertNotIn(secret, text)
        # Without Codex's thread list nothing changes but the missing title.
        rows = self.by_name(self.rows(tui))
        self.assertEqual(rows["codex developer"].detail, "思考中")
        self.assertNotIn("Fix the monitor rows", rows["codex 主對話"].detail)

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
        self._register("wp-1", str(self.task), trusted_agent_id="claude-developer")   # the D6 record names the role
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
        reviewer = self.by_name(rows)["claude"]
        self.assertEqual((reviewer.state, reviewer.age), (mon.WORKING, 10.0))
        self.assertNotIn("token=abc", reviewer.detail)
        developer = self.by_name(rows)["claude developer"]
        self.assertEqual((developer.state, developer.detail), (mon.IDLE, "Build env cache"))
        subs = {row.detail: row.state for row in rows if row.name == "claude subagent"}
        self.assertEqual(subs, {"Fix PR 273 review": mon.WORKING, "Old audit": mon.DONE})

    def test_claude_metadata_tail_does_not_refresh_conversation_activity(self):
        project = self.claude / "proj"
        stamp = lambda age: datetime.fromtimestamp(self.NOW - age, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        real_entries = [
            {"type": "user", "cwd": self.canonical, "timestamp": stamp(220)},
            {"type": "assistant", "timestamp": stamp(80),
             "message": {"content": "finished"}},
            {"type": "system", "subtype": "turn_duration", "timestamp": stamp(77)},
        ]
        metadata = [{"type": kind} for kind in (
            "last-prompt", "cost-state", "custom-title", "agent-name", "mode",
            "permission-mode", "atis-latch", "pr-link")]
        self._write(project / "stale.jsonl", real_entries + metadata, age=5)
        self._write(project / "fresh.jsonl", real_entries + metadata + [
            {"type": "assistant", "timestamp": stamp(5),
             "message": {"content": "new turn"}}], age=5)

        rows = {row.key: row for row in self.rows()}
        stale = rows["claude:stale.jsonl"]
        fresh = rows["claude:fresh.jsonl"]
        self.assertEqual((stale.state, stale.age), (mon.IDLE, 77.0))
        self.assertEqual((fresh.state, fresh.age), (mon.WORKING, 5.0))

    def test_claude_cli_registry_status_beats_the_session_file_guess(self):
        self._register("wp-1", str(self.task), trusted_agent_id="claude-developer")   # the D6 record names the role
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
                self.assertEqual((row.state, row.detail, row.age, row.sid),
                                 (want[0], want[1] + "｜dev-r4", want[2], ""))   # "s" is no session id
        driven = mon.Process(41, 5718, 41, "05:00", "Ss+", "Claude Code CLI", "claude",
                             "Codex APP-SERVER 5718")
        (sessions / "41.json").write_text(json.dumps(
            {"pid": 41, "sessionId": "s", "status": "waiting", "waitingFor": "permission prompt"}),
            encoding="utf-8")
        row = self.by_name(self.rows([driven]))["claude developer（互動）"]
        self.assertTrue(row.detail.startswith("等人確認權限，要PM（Codex）回"), row.detail)

    def test_quiet_claude_session_whose_tool_result_is_unanswered_waits_on_the_model(self):
        self._register("wp-1", str(self.task), trusted_agent_id="claude-developer")   # the D6 record names the role
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
                row = self.by_name(self.rows())["claude developer"]
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
        self.assertEqual([row.state for row in self.rows([cli]) if row.name == "claude"],
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
        # The background daemon, its terminal hosts and an unclaimed spare hold
        # no session, so they keep no closed session alive.
        helpers = [mon.Process(50, 1, 50, "05:00", "S", "Claude Code daemon", "claude", ""),
                   mon.Process(51, 50, 50, "05:00", "S", "Claude Code daemon", "claude", ""),
                   mon.Process(52, 51, 50, "05:00", "S", "Claude Code background session", "claude",
                               "Claude Code daemon 51")]
        rows = {row.detail: row.state for row in self.rows([cli, *helpers])}
        self.assertEqual(rows["推測 Claude CLI 已關閉（title recent）"], mon.CLOSED)

    def test_exited_claude_pm_session_ignores_stale_waiting_listing_but_live_one_waits(self):
        sid = "pm-session"
        self._write(self.claude / "proj" / f"{sid}.jsonl", [
            {"type": "user", "cwd": self.canonical, "entrypoint": "cli"},
            {"type": "agent-name", "agentName": "claude-pm"},
            {"type": "ai-title", "aiTitle": "handoff"}], age=300)
        (self.claude.parent / "sessions").mkdir()
        listed = {sid: {"sessionId": sid, "status": "waiting", "listed": True,
                        "cwd": self.canonical, "name": "claude-pm"}}
        kwargs = {"codex_root": str(self.codex), "claude_root": str(self.claude),
                  "canonical": self.canonical, "agents": listed}

        with patch.object(mon, "_pm_runtime_bindings", return_value={}):
            exited = mon.office_rows([], self.NOW, **kwargs)
        self.assertEqual([(row.name, row.state, row.detail) for row in exited if row.kind != "worker"],
                         [("claude pm（互動）", mon.CLOSED,
                           "推測 Claude CLI 已關閉（先前等人回覆｜claude-pm）")])

        live_cli = mon.Process(41, 1, 41, "05:00", "S+", "Claude Code CLI", "claude", "")
        (self.claude.parent / "sessions" / "41.json").write_text(
            json.dumps({"pid": 41, "sessionId": sid, "status": "waiting",
                        "name": "claude-pm"}), encoding="utf-8")
        with patch.object(mon, "_pm_runtime_bindings", return_value={}):
            live = mon.office_rows([live_cli], self.NOW, **kwargs)
        self.assertEqual([(row.name, row.state) for row in live if row.kind != "worker"],
                         [("claude pm（互動）", mon.ATTENTION)])

    def test_launcher_named_claude_session_shows_its_role(self):
        for sid, name in (("r", "claude-reviewer"), ("p", "claude-pm"),
                          ("d", "claude-developer-ui"), ("x", "claude-reviewers"),
                          ("y", "reviewer"), ("u", "claude-ui"), ("q", "claude-developer-g8")):
            self._write(self.claude / "proj" / f"{sid}.jsonl", [
                {"type": "user", "cwd": self.canonical, "entrypoint": "cli"},
                {"type": "ai-title", "aiTitle": f"title {sid}"}], age=60)
        (self.claude.parent / "sessions").mkdir(exist_ok=True)
        for pid, sid, name in ((61, "r", "claude-reviewer"), (62, "p", "claude-pm"),
                               (63, "d", "claude-developer-ui"), (64, "x", "claude-reviewers"),
                               (65, "y", "reviewer"), (66, "u", "claude-ui"),
                               (67, "q", "claude-developer-g8")):
            (self.claude.parent / "sessions" / f"{pid}.json").write_text(json.dumps(
                {"pid": pid, "sessionId": sid, "status": "idle", "name": name,
                 "kind": "bg" if sid == "r" else None}), encoding="utf-8")
        procs = [mon.Process(pid, 1, pid, "05:00", "S+", "Claude Code CLI", "claude", "")
                 for pid in (62, 63, 64, 65, 66, 67)]
        procs.append(mon.Process(61, 7, 7, "05:00", "S", "Claude Code background session",
                                 "claude", "Claude Code daemon 7"))
        rows = mon.office_rows(procs, self.NOW, codex_root=str(self.codex),
                               claude_root=[str(self.claude)], canonical=self.canonical)
        got = {row.detail.split("｜")[0]: (row.name, row.role) for row in rows if row.kind != "worker"}
        # Only an exact claude-<role>[-suffix] name binds a role; anything else stays unnamed.
        self.assertEqual(got, {"title r": ("claude reviewer（背景）", "reviewer"),
                               "title p": ("claude pm（互動）", "pm"),
                               "title d": ("claude developer（互動）", "developer"),
                               "title u": ("claude 互動", ""),
                               "title q": ("claude developer（互動）", "developer"),
                               "title x": ("claude 互動", ""),
                               "title y": ("claude 互動", "")})

    def test_closed_claude_session_keeps_its_launcher_role_but_claims_no_mode(self):
        # A stopped background Reviewer, a stopped Developer in its D6 task and
        # a stopped unnamed session: no CLI reports on them any more.
        for sid, cwd, name in (("r", self.canonical, "claude-reviewer"),
                               ("u", str(self.task), "claude-developer-ui"),
                               ("n", self.canonical, None)):
            entries = [{"type": "user", "cwd": cwd, "entrypoint": "cli"}]
            if name:
                entries.append({"type": "agent-name", "agentName": name, "sessionId": sid})
            entries.append({"type": "ai-title", "aiTitle": f"title {sid}"})
            self._write(self.claude / "proj" / f"{sid}.jsonl", entries, age=600)
        rows = self.rows()
        got = {row.detail.split("｜")[0]: (row.name, row.role) for row in rows if row.kind != "worker"}
        self.assertEqual(got, {"title r": ("claude reviewer", "reviewer"),
                               "title u": ("claude developer", "developer"),
                               "title n": ("claude", "")})
        # A name that is not exactly claude-<role> still binds nothing.
        self._write(self.claude / "proj" / "r.jsonl", [
            {"type": "user", "cwd": self.canonical, "entrypoint": "cli"},
            {"type": "agent-name", "agentName": "claude-reviewers"},
            {"type": "ai-title", "aiTitle": "title r"}], age=600)
        got = {row.detail: (row.name, row.role) for row in self.rows() if row.kind != "worker"}
        self.assertEqual(got["title r"], ("claude", ""))

    def _registry(self, pid, **entry):
        (self.claude.parent / "sessions").mkdir(exist_ok=True)
        (self.claude.parent / "sessions" / f"{pid}.json").write_text(
            json.dumps({"pid": pid, **entry}), encoding="utf-8")
        return mon.Process(pid, 7, 7, "05:00", "S", "Claude Code background session", "claude",
                           "Claude Code daemon 7")

    def _job(self, job, state):
        path = self.claude.parent / "jobs" / job / "state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"state": state, "intent": "PROMPT-SECRET",
                                    "providerEnv": {"KEY": "CREDENTIAL-SECRET"}}), encoding="utf-8")

    def test_background_session_needing_a_person_is_listed_without_a_session_file(self):
        self._register("wp-1", str(self.task), trusted_agent_id="claude-developer")   # the D6 record names the role
        # A `claude --bg --resume` stopped at a menu: blocked, no session file yet.
        self._job("j1", "blocked")
        self._job("j3", "working")
        procs = [self._registry(61, sessionId="aaaaaaaa-0001", kind="bg", status="busy",
                                name="claude-developer", jobId="j1", cwd=self.canonical),
                 self._registry(62, sessionId="bbbbbbbb-0002", kind="bg", status="waiting",
                                waitingFor="permission prompt", cwd=str(self.task)),
                 self._registry(63, sessionId="cccccccc-0003", kind="bg", status="busy",
                                jobId="j3", cwd=self.canonical),   # working: not news
                 self._registry(64, sessionId="dddddddd-0004", kind="bg", status="waiting",
                                cwd=self.tmp.name + "/elsewhere")]   # not this repository
        rows = [row for row in self.rows(procs) if row.kind != "worker"]
        got = {row.sid: (row.name, row.state, row.detail) for row in rows}
        self.assertEqual(got, {
            "aaaaaaaa": ("claude developer（背景）", mon.ATTENTION,
                         "等人操作，要你回（還沒有對話紀錄）｜claude-developer"),
            "bbbbbbbb": ("claude developer（背景）", mon.ATTENTION,
                         "等人確認權限，要你回（task wp-1）（還沒有對話紀錄）")})
        self.assertIn("需注意", "\n".join(mon.office_lines(rows)))
        text = "\n".join(mon.office_lines(self.rows(procs)))
        for secret in SECRETS:
            self.assertNotIn(secret, text)
        # A job id is a name under jobs/, never a path.
        self.assertIsNone(mon._job_state(str(self.claude.parent / "sessions"), {"jobId": "../j1"}))

    AGENTS_LISTING = json.dumps([
        {"sessionId": "aaaaaaaa-0001", "state": "blocked", "kind": "bg", "name": "claude-developer",
         "cwd": "{task}", "prompt": "PROMPT-SECRET", "argv": ["claude", "--bg", "ARGV-SECRET"]},
        {"sessionId": "bbbbbbbb-0002", "status": "waiting", "kind": "bg"},   # no folder named
        {"sessionId": "cccccccc-0003", "state": "working", "cwd": "{canonical}"},
        {"sessionId": "dddddddd-0004", "state": "done", "cwd": "{canonical}"},
        {"sessionId": "eeeeeeee-0005", "state": "busy", "cwd": "{canonical}"},
        {"sessionId": "ffffffff-0006", "state": "blocked", "cwd": "/elsewhere"},   # not this repo
        {"sessionId": "../x", "state": "blocked"}, "junk"])

    def _listing(self):
        return (self.AGENTS_LISTING.replace("{task}", str(self.task))
                .replace("{canonical}", self.canonical))

    def test_claude_agents_blocked_or_waiting_without_transcript_need_a_person(self):
        self._register("wp-1", str(self.task), trusted_agent_id="claude-developer")
        (self.claude.parent / "sessions").mkdir(exist_ok=True)
        asked = []
        def runner(config_dir):
            asked.append(config_dir)
            return self._listing()
        agents = mon.ClaudeAgents(runner=runner, background=False)
        listed = agents.sessions(mon.claude_config_dirs(str(self.claude)))
        self.assertEqual(asked, [str(self.claude.parent)])
        self.assertEqual(sorted(listed), ["aaaaaaaa-0001", "bbbbbbbb-0002", "ffffffff-0006"])
        running = self._registry(61, sessionId="aaaaaaaa-0001", kind="bg", status="busy",
                                 name="claude-developer", cwd=str(self.task))
        rows = [row for row in mon.office_rows(
            [running], self.NOW, codex_root=str(self.codex), claude_root=str(self.claude),
            canonical=self.canonical, agents=listed) if row.kind != "worker"]
        got = {row.sid: (row.name, row.state, row.detail) for row in rows}
        self.assertEqual(got, {
            "aaaaaaaa": ("claude developer（背景）", mon.ATTENTION,
                         "等人操作，要你回（task wp-1）（還沒有對話紀錄）｜claude-developer"),
            "bbbbbbbb": ("claude 背景", mon.ATTENTION, "等人回覆，要你回（還沒有對話紀錄）")})
        text = "\n".join(mon.office_lines(rows))
        self.assertIn("需注意", text)
        for secret in SECRETS + ("ARGV-SECRET", "--bg"):
            self.assertNotIn(secret, text)
        for entry in listed.values():
            self.assertLessEqual(set(entry), {"sessionId", "kind", "listed", "status",
                                              "jobState", "listedPid", "cwd", "name"})
        # Asked again only after the cadence, never on every office rebuild.
        agents.sessions(mon.claude_config_dirs(str(self.claude)))
        self.assertEqual(len(asked), 1)

    def test_claude_agents_blocked_session_with_a_transcript_is_not_closed(self):
        self._write(self.claude / "proj" / "aaaaaaaa-0001.jsonl", [
            {"type": "user", "cwd": self.canonical, "entrypoint": "cli"},
            {"type": "ai-title", "aiTitle": "title a"}], age=600)
        (self.claude.parent / "sessions").mkdir(exist_ok=True)   # registry readable; the CLI below is registered
        listed = mon.parse_claude_agents(json.dumps(
            [{"sessionId": "aaaaaaaa-0001", "state": "blocked", "status": "waiting",
              "pid": 61, "cwd": self.canonical}]))
        self.assertEqual((listed["aaaaaaaa-0001"]["status"],
                          listed["aaaaaaaa-0001"]["jobState"]), ("waiting", "blocked"))
        running = self._registry(61, sessionId="aaaaaaaa-0001", kind="bg", status="busy",
                                 cwd=self.canonical)
        rows = [row for row in mon.office_rows(
            [running], self.NOW, codex_root=str(self.codex), claude_root=str(self.claude),
            canonical=self.canonical, agents=listed) if row.kind != "worker"]
        self.assertEqual([(row.sid, row.state, row.detail) for row in rows],
                         [("aaaaaaaa", mon.ATTENTION, "等人操作，要你回")])

    def test_claude_agents_parses_only_positive_exact_int_pid(self):
        entries = [{"sessionId": f"s{i}-123456", "state": "blocked", "pid": pid}
                   for i, pid in enumerate((41, 0, -1, True, 1.5, "42"))]
        parsed = mon.parse_claude_agents(json.dumps(entries))
        self.assertEqual(parsed["s0-123456"]["listedPid"], 41)
        for sid in ("s1-123456", "s2-123456", "s3-123456", "s4-123456", "s5-123456"):
            self.assertNotIn("listedPid", parsed[sid])

    def test_claude_agents_blocked_without_process_or_transcript_has_no_row(self):
        (self.claude.parent / "sessions").mkdir(exist_ok=True)
        listed = mon.parse_claude_agents(json.dumps(
            [{"sessionId": "aaaaaaaa-0001", "state": "blocked"}]))
        rows = self.rows([], agents=listed)
        self.assertFalse(any(row.sid == "aaaaaaaa" for row in rows))

    def test_claude_agents_blocked_without_process_transcript_is_closed(self):
        self._write(self.claude / "proj" / "aaaaaaaa-0001.jsonl", [
            {"type": "user", "cwd": self.canonical, "entrypoint": "cli"},
            {"type": "ai-title", "aiTitle": "title a"}], age=600)
        (self.claude.parent / "sessions").mkdir(exist_ok=True)
        listed = {"aaaaaaaa-0001": {"sessionId": "aaaaaaaa-0001", "status": "busy",
                                     "jobState": "blocked"}}
        rows = [row for row in self.rows([], agents=listed) if row.kind != "worker"]
        self.assertEqual([(row.sid, row.state) for row in rows], [("aaaaaaaa", mon.CLOSED)])

    def test_claude_agents_blocked_listed_pid_must_name_a_claude_process(self):
        (self.claude.parent / "sessions").mkdir(exist_ok=True)
        listed = {"aaaaaaaa-0001": {"sessionId": "aaaaaaaa-0001", "status": "busy",
                                     "jobState": "blocked", "listedPid": 61,
                                     "listed": True, "cwd": self.canonical}}
        self.assertFalse(any(row.sid == "aaaaaaaa" for row in self.rows([], agents=listed)))
        non_claude = mon.Process(61, 1, 61, "05:00", "S", "Codex daemon", "codex", "")
        self.assertFalse(any(row.sid == "aaaaaaaa" for row in
                             self.rows([non_claude], agents=listed)))

    def test_claude_agents_blocked_listed_pid_accepts_running_background_session(self):
        (self.claude.parent / "sessions").mkdir(exist_ok=True)
        listed = {"aaaaaaaa-0001": {"sessionId": "aaaaaaaa-0001", "status": "busy",
                                     "jobState": "blocked", "listedPid": 61,
                                     "listed": True, "cwd": self.canonical}}
        background = mon.Process(61, 7, 7, "05:00", "S", "Claude Code background session",
                                 "claude", "Claude Code daemon 7")
        rows = [row for row in self.rows([background], agents=listed) if row.kind != "worker"]
        self.assertEqual([(row.sid, row.state) for row in rows], [("aaaaaaaa", mon.ATTENTION)])

    def test_claude_agents_blocked_without_readable_registry_keeps_attention(self):
        listed = {"aaaaaaaa-0001": {"sessionId": "aaaaaaaa-0001", "status": "busy",
                                     "jobState": "blocked", "listed": True,
                                     "cwd": self.canonical}}
        sessions = mon.claude_sessions(str(self.claude))
        self.assertTrue(all(not os.path.exists(directory) for directory in sessions))
        self.assertIsNone(mon._claude_registered(sessions, []))
        rows = [row for row in self.rows([], agents=listed) if row.kind != "worker"]
        self.assertEqual([(row.sid, row.state) for row in rows], [("aaaaaaaa", mon.ATTENTION)])

    def test_claude_agents_blocked_registry_cli_keeps_attention_and_pid(self):
        (self.claude.parent / "sessions").mkdir(exist_ok=True)
        proc = mon.Process(61, 1, 61, "05:00", "S+", "Claude Code CLI", "claude", "")
        (self.claude.parent / "sessions" / "61.json").write_text(json.dumps(
            {"pid": 61, "sessionId": "aaaaaaaa-0001", "status": "busy"}), encoding="utf-8")
        listed = {"aaaaaaaa-0001": {"sessionId": "aaaaaaaa-0001", "status": "busy",
                                     "jobState": "blocked", "listed": True,
                                     "cwd": self.canonical}}
        captured = {}
        original = mon.claude_rows
        def capture(now, root=None, canonical=None, live=None):
            captured.update(live or {})
            return original(now, root, canonical, live)
        with patch.object(mon, "claude_rows", side_effect=capture):
            rows = [row for row in self.rows([proc], agents=listed) if row.kind != "worker"]
        self.assertEqual([(row.sid, row.state) for row in rows], [("aaaaaaaa", mon.ATTENTION)])
        self.assertEqual(captured["aaaaaaaa-0001"][0]["pid"], 61)

    def test_claude_agents_failure_or_timeout_degrades_to_no_rows(self):
        (self.claude.parent / "sessions").mkdir(exist_ok=True)
        dirs = mon.claude_config_dirs(str(self.claude))
        def boom(config_dir):
            raise subprocess.TimeoutExpired(["claude", "agents", "--json"], 5)
        for runner in (lambda config_dir: None, lambda config_dir: "not json",
                       lambda config_dir: '{"agents": "x"}', boom):
            self.assertEqual(mon.ClaudeAgents(runner=runner, background=False).sessions(dirs), {})
        # An account that never ran a background session is not asked at all.
        asked = []
        empty = Path(self.tmp.name) / "fresh-account"
        empty.mkdir()
        mon.ClaudeAgents(runner=asked.append, background=False).sessions([str(empty)])
        self.assertEqual(asked, [])
        # The real call: a non-zero exit, a missing binary and a slow CLI all give None.
        fake = Path(self.tmp.name) / "fake-claude"
        fake.write_text("#!/bin/sh\nsleep 5\n", encoding="utf-8")
        fake.chmod(0o700)
        self.assertIsNone(mon._run_claude_agents(str(fake), str(self.claude.parent), 0.2))
        self.assertIsNone(mon._run_claude_agents("/nonexistent/claude", str(self.claude.parent), 1))
        fake.write_text("#!/bin/sh\necho '[]'\nexit 3\n", encoding="utf-8")
        self.assertIsNone(mon._run_claude_agents(str(fake), str(self.claude.parent), 2))
        fake.write_text('#!/bin/sh\necho "[{\\"sessionId\\": \\"aaaaaaaa-0001\\", '
                        '\\"state\\": \\"blocked\\", \\"dir\\": \\"$CLAUDE_CONFIG_DIR\\"}]"\n',
                        encoding="utf-8")
        out = mon._run_claude_agents(str(fake), str(self.claude.parent), 2)
        self.assertEqual(json.loads(out)[0]["dir"], str(self.claude.parent))
        # A CLI that floods more than AGENTS_OUTPUT bytes (and never stops) is
        # cut off and killed well before the timeout, and gives no rows.
        fake.write_text("#!/bin/sh\nexec yes '[{\"sessionId\": \"aaaaaaaa-0001\", "
                        "\"state\": \"blocked\"}]'\n", encoding="utf-8")
        start = time.monotonic()
        self.assertIsNone(mon._run_claude_agents(str(fake), str(self.claude.parent), 30))
        self.assertLess(time.monotonic() - start, 10)
        # Just over the cap, then a clean exit, is still an overflow.
        fake.write_text("#!/bin/sh\nhead -c %d /dev/zero | tr '\\0' ' '\necho '[]'\n"
                        % mon.AGENTS_OUTPUT, encoding="utf-8")
        self.assertIsNone(mon._run_claude_agents(str(fake), str(self.claude.parent), 10))
        fake.write_text("#!/bin/sh\nexec yes x\n", encoding="utf-8")
        flood = mon.ClaudeAgents(runner=lambda config_dir: mon._run_claude_agents(
            str(fake), config_dir, 30), background=False)
        (self.claude.parent / "sessions").mkdir(exist_ok=True)
        self.assertEqual(flood.sessions([str(self.claude.parent)]), {})
        # The office still builds with no listing.
        self.assertEqual([row for row in mon.office_rows(
            [], self.NOW, codex_root=str(self.codex), claude_root=str(self.claude),
            canonical=self.canonical, agents={}) if row.kind != "worker"], [])

    def test_claude_agents_refresh_runs_in_the_background(self):
        (self.claude.parent / "sessions").mkdir(exist_ok=True)
        clock = [100.0]
        release = threading.Event()

        def runner(config_dir):
            release.wait(5)
            return self._listing()
        agents = mon.ClaudeAgents(runner=runner, clock=lambda: clock[0], every=60.0)
        dirs = mon.claude_config_dirs(str(self.claude))
        self.assertEqual(agents.sessions(dirs), {})   # nothing yet: the call runs off the UI thread
        release.set()
        agents.thread.join(5)
        self.assertIn("aaaaaaaa-0001", agents.sessions(dirs))
        first = agents.thread
        clock[0] += 61
        agents.sessions(dirs)
        self.assertIsNot(agents.thread, first)
        agents.thread.join(5)

    def test_d6_developer_binding_uses_normal_claude_developer_label(self):
        self._register("wp-1", str(self.task), trusted_agent_id="claude-" + "developer")
        ui_name = "claude-" + "developer-" + "ui"
        ui_sid = "aaaa" + "1111-0001"
        developer_sid = "bbbb" + "2222-0002"
        # Background sessions need a recent transcript to appear in the office
        # view; keep both transcripts inside the registered D6 root.
        for sid in (ui_sid, developer_sid):
            self._write(self.claude / "proj" / f"{sid}.jsonl", [
                {"type": "user", "cwd": str(self.task), "entrypoint": "cli"},
                {"type": "ai-title", "aiTitle": f"title {sid}"}], age=600)
        ui = self._registry(71, sessionId=ui_sid, kind="bg", status="busy",
                            name=ui_name, cwd=str(self.task))
        developer = self._registry(72, sessionId=developer_sid, kind="bg",
                                   status="busy", name="claude-" + "developer",
                                   cwd=str(self.task))

        rows = [row for row in self.rows([ui, developer]) if row.kind != "worker"]

        self.assertEqual({row.sid: row.name for row in rows}, {
            "aaaa1111": "claude developer（背景）",
            "bbbb2222": "claude developer（背景）",
        })

    def test_background_job_state_decides_like_claude_agents(self):
        for sid in ("w", "b", "s"):
            self._write(self.claude / "proj" / f"{sid}.jsonl", [
                {"type": "user", "cwd": self.canonical, "entrypoint": "cli"},
                {"type": "ai-title", "aiTitle": f"title {sid}"}], age=600)
        self._job("jw", "working")
        self._job("jb", "blocked")
        self._job("js", "blocked")
        # Its registry says idle while a background command it started still
        # runs: the job is working, as `claude agents` says.
        procs = [self._registry(61, sessionId="w", kind="bg", status="idle", jobId="jw"),
                 self._registry(62, sessionId="b", kind="bg", status="idle", jobId="jb")]
        # "s" left a blocked job record behind, but its CLI is gone.
        got = {row.detail.split("｜")[0]: row.state for row in self.rows(procs) if row.kind != "worker"}
        self.assertEqual(got, {"title w": mon.WORKING, "等人操作，要你回": mon.ATTENTION,
                               "推測 Claude CLI 已關閉（title s）": mon.CLOSED})

    def test_live_session_precedence_picks_the_most_urgent_cli(self):
        # Several CLIs naming one session: the most urgent wins whatever the
        # registry order, then the newest statusUpdatedAt, then the higher PID.
        idle = {"sessionId": "s", "status": "idle", "statusUpdatedAt": 9}
        waiting = {"sessionId": "s", "status": "waiting", "statusUpdatedAt": 1}
        for registered in ({1: idle, 2: waiting}, {2: waiting, 1: idle}):
            got = mon._live_by_session(registered)["s"]
            self.assertEqual((got["status"], got["pid"], got["clis"]), ("waiting", 2, 2))
        blocked = {"sessionId": "s", "status": "busy", "jobState": "blocked"}
        busy = {"sessionId": "s", "status": "busy"}
        self.assertEqual(mon._live_by_session({1: busy, 2: blocked})["s"]["pid"], 2)
        self.assertEqual(mon._live_by_session({1: busy, 2: idle})["s"]["pid"], 1)
        self.assertEqual(mon._live_by_session({1: idle, 2: {"sessionId": "s"}})["s"]["pid"], 1)
        newer = dict(idle, statusUpdatedAt=20)
        self.assertEqual(mon._live_by_session({1: newer, 2: idle})["s"]["pid"], 1)
        self.assertEqual(mon._live_by_session({1: idle, 2: dict(idle)})["s"]["pid"], 2)
        self.assertEqual(mon._live_by_session({1: idle})["s"]["clis"], 1)
        self.assertEqual(mon._live_by_session(None), {})

    def test_live_session_shared_by_several_clis_shows_the_most_urgent(self):
        for sid in ("o1", "o2", "bk", "wk"):
            self._write(self.claude / "proj" / f"{sid}.jsonl", [
                {"type": "user", "cwd": self.canonical, "entrypoint": "cli",
                 "message": {"content": "PROMPT-SECRET"}},
                {"type": "ai-title", "aiTitle": f"title {sid}"}], age=600)
        self._job("jb", "blocked")
        procs = [
            # idle + waiting, in both registry orders
            self._registry(61, sessionId="o1", kind="bg", status="idle"),
            self._registry(62, sessionId="o1", kind="bg", status="waiting"),
            self._registry(63, sessionId="o2", kind="bg", status="waiting"),
            self._registry(64, sessionId="o2", kind="bg", status="idle"),
            # busy + blocked job
            self._registry(65, sessionId="bk", kind="bg", status="busy"),
            self._registry(66, sessionId="bk", kind="bg", status="busy", jobId="jb"),
            # busy + idle
            self._registry(67, sessionId="wk", kind="bg", status="idle"),
            self._registry(68, sessionId="wk", kind="bg", status="busy"),
            # a shared session outside this repository is still not listed
            self._registry(69, sessionId="out", kind="bg", status="waiting",
                           cwd=self.tmp.name + "/elsewhere"),
            self._registry(70, sessionId="out", kind="bg", status="idle",
                           cwd=self.tmp.name + "/elsewhere")]
        rows = [row for row in self.rows(procs) if row.kind != "worker"]
        got = sorted((row.state, row.detail) for row in rows)
        self.assertEqual(got, sorted([
            (mon.ATTENTION, "等人回覆，要你回（同一 session 有 2 個 CLI）"),
            (mon.ATTENTION, "等人回覆，要你回（同一 session 有 2 個 CLI）"),
            (mon.ATTENTION, "等人操作，要你回（同一 session 有 2 個 CLI）"),
            (mon.WORKING, "title wk（同一 session 有 2 個 CLI）")]))
        text = "\n".join(mon.office_lines(rows))
        for secret in SECRETS:
            self.assertNotIn(secret, text)

    def test_claude_agents_blocked_beats_idle_clis_sharing_the_session(self):
        procs = [self._registry(61, sessionId="aaaaaaaa-0001", kind="bg", status="idle",
                                cwd=self.canonical),
                 self._registry(62, sessionId="aaaaaaaa-0001", kind="bg", status="busy",
                                cwd=self.canonical)]
        listed = mon.parse_claude_agents(json.dumps(
            [{"sessionId": "aaaaaaaa-0001", "state": "blocked", "cwd": self.canonical}]))
        rows = [row for row in mon.office_rows(
            procs, self.NOW, codex_root=str(self.codex), claude_root=str(self.claude),
            canonical=self.canonical, agents=listed) if row.kind != "worker"]
        self.assertEqual([(row.state, row.detail) for row in rows],
                         [(mon.ATTENTION, "等人操作，要你回（同一 session 有 2 個 CLI）（還沒有對話紀錄）")])

    def test_running_cli_without_a_readable_entry_keeps_its_launcher_role(self):
        self._write(self.claude / "proj" / "r.jsonl", [
            {"type": "user", "cwd": self.canonical, "entrypoint": "cli"},
            {"type": "agent-name", "agentName": "claude-reviewer"},
            {"type": "ai-title", "aiTitle": "title r"}], age=600)
        (self.claude.parent / "sessions").mkdir()
        (self.claude.parent / "sessions" / "41.json").write_text("{", encoding="utf-8")
        cli = mon.Process(41, 1, 41, "05:00", "S+", "Claude Code CLI", "claude", "")
        row = self.by_name(self.rows([cli]))["claude reviewer"]
        # Kept as the running CLI's session, with no claimed mode.
        self.assertEqual((row.state, row.role, row.detail), (mon.IDLE, "reviewer", "title r"))

    def test_launcher_roles_only_include_supported_names(self):
        for name in ("claude-developer-", "claude-pm-x-", "claude-ui", "claude-ui-2"):
            self.assertIsNone(mon.SESSION_ROLE.fullmatch(name), name)
        for name, role in (("claude-developer-ui", "developer"),
                           ("claude-developer-ui-x", "developer"),
                           ("claude-developer-g8", "developer")):
            found = mon.SESSION_ROLE.fullmatch(name)
            self.assertEqual(found.group(1), role)

    def test_claude_sessions_of_every_account_are_shown(self):
        other = self.claude.parent / "claude-account2" / "projects"
        self._write(self.claude / "proj" / "a.jsonl", [
            {"type": "user", "cwd": self.canonical, "entrypoint": "cli"},
            {"type": "ai-title", "aiTitle": "default account"}], age=60)
        self._write(other / "proj" / "b.jsonl", [
            {"type": "user", "cwd": self.canonical, "entrypoint": "cli"},
            {"type": "ai-title", "aiTitle": "second account"}], age=60)
        self._write(other / "proj" / "c.jsonl", [
            {"type": "user", "cwd": self.canonical, "entrypoint": "cli"},
            {"type": "ai-title", "aiTitle": "moved to background"}], age=60)
        for top, pid, sid, kind in ((self.claude, 41, "a", None), (other, 42, "b", None),
                                    (other, 43, "c", "bg")):
            (top.parent / "sessions").mkdir(exist_ok=True)
            (top.parent / "sessions" / f"{pid}.json").write_text(json.dumps(
                {"pid": pid, "sessionId": sid, "status": "idle", "kind": kind}), encoding="utf-8")
        procs = [mon.Process(41, 1, 41, "05:00", "S+", "Claude Code CLI", "claude", ""),
                 mon.Process(42, 1, 42, "05:00", "S+", "Claude Code CLI", "claude", ""),
                 mon.Process(43, 7, 7, "05:00", "S", "Claude Code background session", "claude",
                             "Claude Code daemon 7")]
        rows = mon.office_rows(procs, self.NOW, codex_root=str(self.codex),
                               claude_root=[str(self.claude), str(other)], canonical=self.canonical)
        got = {row.detail.split("｜")[0]: (row.name, row.state) for row in rows if row.kind != "worker"}
        self.assertEqual(got, {"default account": ("claude 互動", mon.IDLE),
                               "second account": ("claude 互動", mon.IDLE),
                               "moved to background": ("claude 背景", mon.IDLE)})
        # By default every ~/.claude-* account is read next to ~/.claude.
        with patch.object(mon, "CLAUDE_PROJECTS", str(self.claude)), \
                patch.object(mon, "CLAUDE_OTHER_PROJECTS", str(self.claude.parent / "claude-*" / "projects")):
            self.assertEqual(mon.claude_roots(), [str(self.claude), str(other)])
            (self.claude.parent / "claude-alias").symlink_to(self.claude.parent / "claude-account2")
            self.assertEqual(mon.claude_roots(), [str(self.claude), str(other)])   # read once
        # A file another account's crashed CLI left for a reused PID loses to
        # the running CLI's fresh one.
        stale = other.parent / "sessions" / "41.json"
        stale.write_text(json.dumps({"pid": 41, "sessionId": "b"}), encoding="utf-8")
        os.utime(stale, (self.NOW - 3600, self.NOW - 3600))
        os.utime(self.claude.parent / "sessions" / "41.json", (self.NOW - 5, self.NOW - 5))
        found = mon._claude_registered(mon.claude_sessions([str(self.claude), str(other)]), procs)
        self.assertEqual(found[41]["sessionId"], "a")
        # The daemon and a spare background session holding no session yet are
        # infrastructure: listed, not counted as agents.
        daemon = mon.Process(7, 1, 7, "05:00", "S", "Claude Code daemon", "claude", "")
        spare = mon.Process(44, 7, 7, "05:00", "S", "Claude Code background session", "claude",
                            "Claude Code daemon 7")
        self.assertEqual(mon.summary(procs + [daemon, spare], [],
                                     mon.claude_sessions([str(self.claude), str(other)])), "Claude 3" + NO_RUNNERS)

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
                         ("claude", mon.IDLE, "agent", ""))
        lines = mon.office_lines(rows, expand=True)   # gray rows listed, not folded
        self.assertEqual(lines[0], "辦公室 · 2 位工作中 · 共 9 位")   # incl. the EC2 and gw agent workers
        live = lines.index(f"── {mon.LIVE_SECTION} ──")
        pm = next(i for i, line in enumerate(lines) if "codex 主對話" in line)
        self.assertLess(live, pm)
        self.assertIn("└ codex subagent Franklin", lines[pm + 1])
        self.assertTrue(any("claude 單次呼叫" in line for line in lines))
        self.assertIn(f"── {mon.STATUS_SECTION} ──", lines)
        self.assertLess(lines.index(f"── {mon.STATUS_SECTION} ──"),
                        next(i for i, line in enumerate(lines) if line.startswith("agy")))

    def test_pm_runtime_binding_labels_only_the_bound_claude_session(self):
        bound = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        other = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
        for sid, title in ((bound, "Bound PM title"), (other, "Ordinary call title")):
            self._write(self.claude / "proj" / f"{sid}.jsonl", [
                {"type": "user", "cwd": self.canonical, "entrypoint": "sdk-cli"},
                {"type": "ai-title", "aiTitle": title}], age=mon.OFFICE_BUSY + 60)
        runtime = Path(self.tmp.name) / "pm-runtime"
        runtime.mkdir(mode=0o700)
        binding = runtime / f"{bound}.binding.json"
        binding.write_text(json.dumps({"runtime": "claude-code", "native_session_id": bound,
                                       "cwd": self.canonical, "dedicated": True,
                                       "created_at": "2026-10-29T00:00:00Z",
                                       "session_started": False}), encoding="utf-8")
        os.chmod(binding, 0o600)
        with patch.object(mon, "PM_RUNTIME_DIR", str(runtime)):
            rows = [row for row in self.rows() if row.detail in
                    ("Bound PM title", "Ordinary call title")]
            self.assertEqual({row.detail: (row.name, row.state) for row in rows}, {
                "Bound PM title": ("PM（TUI）", mon.DONE),
                "Ordinary call title": ("claude 單次呼叫", mon.DONE),
            })
        with patch.object(mon, "PM_RUNTIME_DIR", str(runtime / "missing")):
            row = next(row for row in self.rows() if row.detail == "Bound PM title")
            self.assertEqual(row.name, "claude 單次呼叫")

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

        def fake(now, root=None, canonical=None, live_calls=None, verdicts=None, *,
                 codex_running=False, codex_execs=None):
            seen["calls"] = live_calls
            seen["codex_running"] = codex_running
            seen["codex_execs"] = codex_execs
            return []
        with patch.object(mon, "codex_rows", side_effect=fake):
            self.rows([run])
            self.assertEqual(seen["calls"], [-60.0])
            self.assertFalse(seen["codex_running"])
            self.rows([])   # the table was read: nothing is running
            self.assertEqual(seen["calls"], [])
            codex = mon.Process(41, 1, 41, "00:12", "S", "Codex CLI", "codex", "")
            self.rows([codex])
            self.assertTrue(seen["codex_running"])
            self.assertEqual(seen["codex_execs"], 0)
            run = mon.Process(42, 1, 42, "00:12", "S", "Codex CLI", "codex", "", codex_exec=True)
            self.rows([codex, run])
            self.assertEqual(seen["codex_execs"], 1)

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
        self.assertEqual((rows["公司 gw agent"].state, rows["公司 gw agent"].detail,
                          rows["公司 gw agent"].authority),
                         (mon.PAUSED, "PM 暫停派工（11-03）", "PM"))
        no_cli = self.by_name(mon.office_rows([], self.NOW, codex_root=str(self.codex),
                                              claude_root=str(self.claude),
                                              canonical=self.canonical,
                                              opencode_db=db, cwd_of=lambda pid: None,
                                              dispatch=switches))
        self.assertEqual(no_cli["opencode developer"].detail,
                         "Owner 暫停派工（10-22）；本 repo 最近一次：認證失敗（401），需要重新登入")
        self.assertEqual(no_cli["opencode developer"].state, mon.PAUSED)

    def test_company_gw_agent_owner_allowed_shows_as_dispatchable(self):
        # #120 D1 = A: the Owner's exact decision comment opens the gw Agent route.
        switches = self._dispatch({"company-gw-agent": dict(GW_AGENT_OWNER_ALLOWED)})
        rows = self.by_name(mon.office_rows([], self.NOW, codex_root=str(self.codex),
                                            claude_root=str(self.claude),
                                            canonical=self.canonical, opencode_db="/nonexistent",
                                            cwd_of=lambda pid: None, dispatch=switches))
        row = rows["公司 gw agent"]
        self.assertEqual((row.state, row.detail, row.authority), (mon.IDLE, "可派工", "Owner"))
        # Any other comment on #120 fails closed for every Worker.
        other = self._dispatch({"company-gw-agent": dict(
            GW_AGENT_OWNER_ALLOWED, evidence=GW_AGENT_EVIDENCE + "#issuecomment-6594101521")})
        rows = self.by_name(mon.office_rows([], self.NOW, codex_root=str(self.codex),
                                            claude_root=str(self.claude),
                                            canonical=self.canonical, opencode_db="/nonexistent",
                                            cwd_of=lambda pid: None, dispatch=other))
        self.assertEqual(rows["公司 gw agent"].state, mon.UNCHECKED)
        self.assertEqual(rows["公司 gw"].state, mon.UNCHECKED)

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

    SHA = "0123456789abcdef0123456789abcdef01234567"
    TASK = "abcdef0123456789abcdef0123456789"

    def _ec2(self, processes=(), tasks="/nonexistent"):
        rows = self.by_name(mon.office_rows(list(processes), self.NOW, codex_root=str(self.codex),
                                            claude_root=str(self.claude),
                                            canonical=self.canonical, opencode_db="/nonexistent",
                                            cwd_of=lambda pid: None, llmtool_tasks=tasks))
        return rows["EC2 遠端測試"], "\n".join(mon.office_lines(list(rows.values())))

    def _remote_tasks(self, *records):
        tasks = Path(tempfile.mkdtemp(dir=self.tmp.name))
        for name, body, age in records:
            path = tasks / f"{name}.json"
            path.write_text(body if type(body) is str else json.dumps(body), encoding="utf-8")
            os.utime(path, (self.NOW - age, self.NOW - age))
        return str(tasks)

    def _stamp(self, age, micro=False):
        return datetime.fromtimestamp(self.NOW - age, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S" + (".000000Z" if micro else "Z"))

    def test_ec2_worker_running_remote_test_shows_task_sha_and_modules_from_argv(self):
        host_words = ("ec2-user", "52.221.189.89", "example-host", "/srv/repo", "RAW-ARGV-SECRET")
        snap = mon.parse_processes(ps(
            f"  10     1    10   02:05 S    python3 -I -m llmtool task remote-test {self.TASK} "
            f"--executor-task wp-1 --sha {self.SHA} --ref RAW-ARGV-SECRET "
            "--module test_remote_test --module=test_task_store",
            f"  11    10    10   02:04 S    ssh -o BatchMode=yes ec2-user@52.221.189.89 python3 - "
            f"{self.SHA} refs/RAW-ARGV-SECRET /srv/repo /usr/bin/python3 test_remote_test"), 999)
        row, text = self._ec2(snap.processes)
        self.assertEqual((row.state, row.kind, row.age), (mon.WORKING, "worker", 125.0))
        self.assertEqual(row.detail, "執行中：task abcdef01 @0123456；test_remote_test, test_task_store")
        for word in host_words:
            self.assertNotIn(word, text)
        # a CLI without --module: its own ssh (through a shell) names them, by
        # position only, so a test_-shaped ref or path is never a module
        ssh = (f"/usr/bin/ssh ec2-user@example-host python3 - {self.SHA} test_private_branch "
               "test_repo_dir test_python test_remote_test")
        bare = mon.parse_processes(ps(
            f"  10     1    10   00:30 S    python3 -m llmtool task remote-test {self.TASK} "
            f"--executor-task wp-1 --sha {self.SHA} --ref test_private_branch",
            "  20    10    10   00:29 S    /bin/sh -c x",
            f"  21    20    10   00:29 S    {ssh}"), 999)
        row, text = self._ec2(bare.processes)
        self.assertEqual((row.state, row.detail),
                         (mon.WORKING, "執行中：task abcdef01 @0123456；test_remote_test"))
        for word in host_words + ("test_private_branch", "test_repo_dir", "test_python", "task ?"):
            self.assertNotIn(word, text)
        # the same ssh without a remote-test CLI in its parent chain is not this
        # worker, whether its parent is gone or something else
        for line in (f"  21     1    21   00:09 S    {ssh}",
                     f"  21    30    21   00:09 S    {ssh}"):
            alone = mon.parse_processes(ps("  30     1    30   01:00 S    codex", line), 999)
            row, text = self._ec2(alone.processes)
            self.assertEqual((row.state, row.detail), (mon.IDLE, "尚無遠端測試紀錄"))
            self.assertNotIn("0123456", text)
        # other llmtool commands are not remote-test runs
        other = mon.parse_processes(ps(f"  12     1    12   00:09 S    python3 -m llmtool task inspect {self.TASK}"), 999)
        self.assertEqual(self._ec2(other.processes)[0].state, mon.IDLE)

    def test_ec2_worker_idle_shows_newest_receipt_and_skips_malformed_files(self):
        def receipt(result, age, ran):
            return {"result": result, "source_sha": self.SHA, "ran_total": ran,
                    "ended_at": self._stamp(age), "final": ["TOOL-OUTPUT-SECRET"],
                    "remote_log_path": "/tmp/llmtool-remote-log.x/remote-test.log",
                    "failures": [], "modules": ["test_remote_test"]}
        tasks = self._remote_tasks(
            ("old", {"task_id": "1" * 32, "remote_test_receipts": [receipt("FAIL", 7200, 3)]}, 7200),
            ("new", {"task_id": self.TASK, "request": "PROMPT-SECRET",
                     "remote_test_receipts": [receipt("FAIL", 900, 40), receipt("PASS", 600, 42)]}, 600),
            ("broken", "{not json", 10),
            ("list", "[1, 2]", 20),
            ("odd", {"task_id": "2" * 32, "remote_test_receipts": "PASS",
                     "remote_test_outcomes": [{"result": "MADE_UP", "at": self._stamp(5)}]}, 5))
        row, text = self._ec2(tasks=tasks)
        self.assertEqual((row.state, row.age), (mon.IDLE, 600.0))
        self.assertEqual(row.detail, "最近一次：task abcdef01 PASS @0123456，跑了 42 個測試")
        for secret in SECRETS + ("remote-log",):
            self.assertNotIn(secret, text)

    def test_ec2_worker_shows_outcome_code_and_keeps_unknown(self):
        tasks = self._remote_tasks(
            ("a", {"task_id": self.TASK, "remote_test_receipts": [
                {"result": "PASS", "source_sha": self.SHA, "ran_total": 5, "ended_at": self._stamp(3600)}],
                "remote_test_outcomes": [{"result": "UNKNOWN", "code": "RECEIPT_INVALID",
                                          "sha": self.SHA, "modules": [], "executor_task": "wp-1",
                                          "at": self._stamp(120, micro=True)}]}, 120))
        row, _ = self._ec2(tasks=tasks)
        self.assertEqual((row.state, row.age), (mon.IDLE, 120.0))
        self.assertEqual(row.detail, "最近一次：task abcdef01 UNKNOWN（RECEIPT_INVALID） @0123456")
        only = self._remote_tasks(("b", {"task_id": self.TASK, "remote_test_outcomes": [
            {"result": "ADMISSION_DENIED", "code": "CONFIG_INVALID", "sha": None, "modules": [],
             "executor_task": None, "at": self._stamp(30)}]}, 30))
        self.assertEqual(self._ec2(tasks=only)[0].detail,
                         "最近一次：task abcdef01 ADMISSION_DENIED（CONFIG_INVALID）")

    def test_ec2_worker_skips_fields_of_the_wrong_type_without_breaking(self):
        wrong = ([], {}, ["PASS"], {"PASS": 1}, 7, None, True, 1.5)
        receipts = [{"result": bad, "source_sha": self.SHA, "ran_total": 1,
                     "ended_at": self._stamp(10)} for bad in wrong]
        receipts += [{"result": "PASS", "source_sha": bad, "ran_total": bad,
                      "ended_at": self._stamp(300)} for bad in wrong]
        receipts += [{"result": "FAIL", "ended_at": bad} for bad in wrong]
        outcomes = [{"result": bad, "code": "X", "at": self._stamp(5)} for bad in wrong]
        outcomes += [{"result": "UNKNOWN", "code": bad, "sha": bad, "at": self._stamp(200)}
                     for bad in wrong]
        outcomes += [{"result": "UNKNOWN", "code": "Y", "at": bad} for bad in wrong]
        tasks = self._remote_tasks(
            ("a", {"task_id": {"x": 1}, "remote_test_receipts": receipts + ["PASS", None],
                   "remote_test_outcomes": outcomes + [[1], 2]}, 10),
            ("b", {"task_id": ["abcdef0123"], "remote_test_receipts": {"result": "PASS"},
                   "remote_test_outcomes": "UNKNOWN"}, 20))
        row, _ = self._ec2(tasks=tasks)
        # the only well-typed entries left are newest-first: UNKNOWN (200 s) then PASS (300 s)
        self.assertEqual((row.state, row.age, row.detail),
                         (mon.IDLE, 200.0, "最近一次：task 未知 UNKNOWN"))

    def test_ec2_worker_reuses_its_task_listing_and_reads_a_bounded_number(self):
        root = Path(self._remote_tasks(*[(f"t{i:02}", {"task_id": self.TASK}, 1000 + i)
                                          for i in range(mon.REMOTE_TEST_FILES + 8)]))
        newest = {"task_id": self.TASK, "remote_test_receipts": [
            {"result": "PASS", "source_sha": self.SHA, "ran_total": 3, "ended_at": self._stamp(1)}]}
        listed, opened = [], []
        real_scandir, real_open = os.scandir, os.open
        with patch.object(mon.os, "scandir", side_effect=lambda p: listed.append(p) or real_scandir(p)), \
                patch.object(mon.os, "open", side_effect=lambda p, *a: opened.append(p) or real_open(p, *a)):
            self.assertEqual(mon.remote_test_latest(str(root)), (None, ""))
            self.assertEqual(len(opened), mon.REMOTE_TEST_FILES)
            self.assertEqual(mon.remote_test_latest(str(root)), (None, ""))
            self.assertEqual(len(listed), 1)   # folder unchanged: the listing is reused
            self.assertEqual(len(opened), mon.REMOTE_TEST_FILES)   # unchanged files: not read again
            # TaskStore writes by rename, which changes the folder: listed again
            temp = root / "new.tmp"
            temp.write_text(json.dumps(newest), encoding="utf-8")
            os.replace(temp, root / "new.json")
            os.utime(root / "new.json", (self.NOW - 1, self.NOW - 1))
            self.assertEqual(mon.remote_test_latest(str(root))[0][1], "task abcdef01 PASS @0123456，跑了 3 個測試")
            self.assertEqual(len(listed), 2)
            # an unchanged folder is still listed again after REMOTE_TEST_RELIST
            later = time.monotonic() + mon.REMOTE_TEST_RELIST + 1
            with patch.object(mon.time, "monotonic", return_value=later):
                mon.remote_test_latest(str(root))
            self.assertEqual(len(listed), 3)

    def test_ec2_worker_reads_at_most_its_budget_per_rebuild_and_says_unchecked(self):
        pad = "x" * 1000
        root = Path(self._remote_tasks(*[(f"t{i}", {"task_id": self.TASK, "pad": pad, "remote_test_receipts": [
            {"result": "PASS", "source_sha": self.SHA, "ran_total": i, "ended_at": self._stamp(100 + i)}]},
            100 + i) for i in range(6)]))
        size = (root / "t0.json").stat().st_size
        read, real_open = [], mon._remote_test_open

        def counted(path, budget):
            opened = real_open(path, budget)
            read.append(len(opened[1]) if opened and opened[1] else 0)
            return opened

        with patch.object(mon, "REMOTE_TEST_BUDGET", 2 * size + 10), \
                patch.object(mon, "REMOTE_TEST_SKEW", 1e9), \
                patch.object(mon, "_remote_test_open", side_effect=counted):
            first = mon.remote_test_row([], self.NOW, str(root))
            self.assertLessEqual(sum(read), 2 * size + 10)
            self.assertEqual(first.state, mon.UNCHECKED)
            self.assertEqual(first.detail, "已讀到的一次：task abcdef01 PASS @0123456，跑了 0 個測試；"
                                           + mon.REMOTE_TEST_OVER_BUDGET)
            # the files already read are reused; the rest are read on later rebuilds
            read.clear()
            mon.remote_test_row([], self.NOW, str(root))
            self.assertLessEqual(sum(read), 2 * size + 10)
            mon.remote_test_row([], self.NOW, str(root))
            done = mon.remote_test_row([], self.NOW, str(root))
        self.assertEqual((done.state, done.detail),
                         (mon.IDLE, "最近一次：task abcdef01 PASS @0123456，跑了 0 個測試"))

    def test_ec2_worker_skips_a_deeply_nested_record(self):
        deep = "[" * 200000 + "]" * 200000
        tasks = self._remote_tasks(("deep", deep, 5), ("ok", {"task_id": self.TASK, "remote_test_receipts": [
            {"result": "FAIL", "source_sha": self.SHA, "ran_total": 2, "ended_at": self._stamp(60)}]}, 60))
        raw = mon._remote_test_open(os.path.join(tasks, "deep.json"), mon.REMOTE_TEST_BUDGET)[1]
        self.assertIsNone(mon._remote_test_json(raw))
        row, _ = self._ec2(tasks=tasks)
        self.assertEqual((row.state, row.detail),
                         (mon.IDLE, "最近一次：task abcdef01 FAIL @0123456，跑了 2 個測試"))

    def test_ec2_worker_listing_stops_at_the_entry_cap_and_says_unchecked(self):
        receipt = {"task_id": self.TASK, "remote_test_receipts": [
            {"result": "PASS", "source_sha": self.SHA, "ran_total": 3, "ended_at": self._stamp(50)}]}
        root = Path(self._remote_tasks(("r", receipt, 50),
                                       *[(f"t{i:02}", {"task_id": self.TASK}, 100 + i) for i in range(24)]))
        visited, real_scandir = [], os.scandir

        class Counted:
            def __init__(self, path):
                self.inner, self.ours = real_scandir(path), os.fspath(path) == str(root)
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                self.inner.close()
            def __iter__(self):
                for entry in self.inner:
                    if self.ours:   # other office scans also list folders
                        visited.append(entry.name)
                    yield entry

        def row():   # the EC2 row alone: other office readers glob the same folder
            with patch.object(mon.os, "scandir", side_effect=Counted):
                return mon.remote_test_row([], self.NOW, str(root))

        with patch.object(mon, "REMOTE_TEST_SCAN", 10):
            capped = row()
            # entry 11 is only fetched to learn more remain; none past it is looked at
            self.assertLessEqual(len(visited), 11)
            self.assertEqual(capped.state, mon.UNCHECKED)
            self.assertTrue(capped.detail.endswith(mon.REMOTE_TEST_CAPPED))
            self.assertNotIn("最近一次", capped.detail)
            # after a directory change the rescan is bounded again
            temp = root / "new.tmp"
            temp.write_text(json.dumps({"task_id": self.TASK}), encoding="utf-8")
            os.replace(temp, root / "new.json")
            visited.clear()
            self.assertEqual(row().state, mon.UNCHECKED)
            self.assertLessEqual(len(visited), 11)
        # under the cap the newest receipt is the newest again
        os.replace(root / "new.json", root / "new2.json")
        self.assertEqual((row().state, row().detail),
                         (mon.IDLE, "最近一次：task abcdef01 PASS @0123456，跑了 3 個測試"))

    def test_ec2_worker_never_follows_a_linked_task_file_to_the_config(self):
        home = Path(tempfile.mkdtemp(dir=self.tmp.name))
        config = home / "remote-test.json"
        # shaped like a task record, so it would be shown if it were ever read
        config.write_text(json.dumps({
            "host": "example-host", "user": "ec2-user", "repo_path": "/srv/repo",
            "task_id": self.TASK, "remote_test_receipts": [
                {"result": "PASS", "source_sha": self.SHA, "ran_total": 9,
                 "ended_at": self._stamp(5)}]}), encoding="utf-8")
        tasks = Path(self._remote_tasks())
        (tasks / "x.json").symlink_to(config)
        opened, real_open = [], os.open
        with patch.object(mon.os, "open", side_effect=lambda p, *a: opened.append(p) or real_open(p, *a)):
            row, text = self._ec2(tasks=str(tasks))
        self.assertEqual((row.state, row.detail), (mon.IDLE, "尚無遠端測試紀錄"))
        self.assertEqual([p for p in opened if str(p).endswith("x.json")], [])
        for word in ("example-host", "ec2-user", "/srv/repo", "跑了 9"):
            self.assertNotIn(word, text)
        # a listed file swapped for a link before it is opened is refused too
        self.assertIsNone(mon._remote_test_open(str(tasks / "x.json"), mon.REMOTE_TEST_BUDGET))
        self.assertIn(b"example-host", mon._remote_test_open(str(config), mon.REMOTE_TEST_BUDGET)[1])
        # a hard link to the config is refused as well
        os.link(config, tasks / "y.json")
        row, text = self._ec2(tasks=str(tasks))
        self.assertEqual(row.detail, "尚無遠端測試紀錄")
        self.assertNotIn("example-host", text)

    def test_ec2_worker_without_records_never_reads_the_remote_config(self):
        home = Path(tempfile.mkdtemp(dir=self.tmp.name))
        (home / ".config" / "llmtool").mkdir(parents=True)
        (home / ".config" / "llmtool" / "remote-test.json").write_text(json.dumps(
            {"host": "example-host", "user": "ec2-user", "repo_path": "/srv/repo",
             "python": "/usr/bin/python3"}), encoding="utf-8")
        tasks = self._remote_tasks(("plain", {"task_id": self.TASK, "state": "WAITING_PM"}, 60))
        with patch.dict(os.environ, {"HOME": str(home)}):
            row, text = self._ec2(tasks=tasks)
            empty, _ = self._ec2()
        self.assertEqual((row.state, row.detail, row.age), (mon.IDLE, "尚無遠端測試紀錄", None))
        self.assertEqual(empty.detail, "尚無遠端測試紀錄")
        for word in ("example-host", "ec2-user", "/srv/repo"):
            self.assertNotIn(word, text)

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
        named, queue = mon.runner_rows([listener, worker], now, None, {30: "/nonexistent"}.get, inside, ci=ci)
        self.assertTrue(named.detail.startswith(
            "執行中：llmtool Mac CI（PR #339 @aaaaaaa）（10 分鐘）；專用帳號 ghrunner；已開 1 小時"),
            named.detail)
        # The queue is one shared line under the runner (W47), not a note on its row.
        self.assertNotIn("排隊", named.detail)
        self.assertEqual((queue.name, queue.state, queue.kind, queue.age, queue.group),
                         ("mac ci 排隊", "", "shared", None, "mac ci runner"))
        self.assertEqual(queue.detail,
                         "排隊 2 個：llmtool Mac CI（PR #334 @bbbbbbb）、Mac Runner Smoke Test（main @ddddddd）")
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
            lambda run: {"jobs": [{"status": "in_progress", "runner_name": "davidproject-self-action-2"}
                                  if run == 7 else {"status": "queued"}]})
        where = {30: roots[0], 40: roots[1]}
        rows = mon.runner_rows([one, two, job], now, roots, where.get,
                               {30: "專用帳號 ghrunner", 40: "專用帳號 ghrunner2"}.get, ci=ci)
        # Both runners share one GitHub queue: it is said once, on a line of its
        # own after the runner rows, and on neither runner (Owner, W47).
        self.assertEqual([(r.name, r.state) for r in rows],
                         [("mac ci runner #1", mon.IDLE), ("mac ci runner #2", mon.WORKING),
                          ("mac ci 兩台共用", "")])
        # Their GitHub names, so the GitHub runner rows leave them out.
        self.assertEqual([r.key for r in rows], ["davidproject-self-action", "davidproject-self-action-2", ""])
        self.assertNotIn("排隊", rows[0].detail)
        self.assertEqual((rows[2].kind, rows[2].detail), ("shared", "排隊 1 個：llmtool Mac CI（PR #341 @bbbbbbb）"))
        self.assertTrue(rows[1].detail.startswith(
            "執行中：llmtool Mac CI（PR #343 @aaaaaaa）（3 分鐘）；專用帳號 ghrunner2"), rows[1].detail)
        self.assertNotIn("排隊", rows[1].detail)
        self.assertTrue(rows[1].detail.endswith(f"日誌：{roots[1]}/_diag"))
        # Runner 2 stopped: its own row says so; runner 1 is unaffected. An
        # empty queue gets no line.
        rows = mon.runner_rows([one], now, roots, where.get, {30: "專用帳號 ghrunner"}.get, ci=[])
        self.assertEqual([(r.name, r.state) for r in rows],
                         [("mac ci runner #1", mon.IDLE), ("mac ci runner #2", mon.STOPPED)])
        self.assertEqual(rows[1].alert, "CI runner davidproject-self-action-2 沒在跑")
        # Only running jobs, none waiting: no line either.
        rows = mon.runner_rows([one, two, job], now, roots, where.get,
                               {30: "專用帳號 ghrunner", 40: "專用帳號 ghrunner2"}.get, ci=ci[:1])
        self.assertEqual([r.name for r in rows], ["mac ci runner #1", "mac ci runner #2"])

    def test_shared_ci_queue_line_names_the_runner_count_and_truncates_like_the_old_note(self):
        waiting = [f"job {n}" for n in range(1, 6)]
        line = mon.ci_queue_row(waiting, 2)
        self.assertEqual((line.name, line.detail), ("mac ci 兩台共用", "排隊 5 個：job 1、job 2、job 3…"))
        self.assertEqual(mon.ci_queue_row(waiting[:3], 2).detail, "排隊 3 個：job 1、job 2、job 3")
        self.assertEqual(mon.ci_queue_row(waiting[:1], 1).name, "mac ci 排隊")
        self.assertEqual(mon.ci_queue_row(waiting[:1], 3).name, "mac ci 3 台共用")

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
        lines = mon.office_lines(rows, expand=True)
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
        self.assertIn("audit 284 release", text)
        self.assertEqual(text.count("@3f86dc6："), 2)
        # folded (the default in the TUI): every gray row of each section is one count,
        # the alerts above still say what went wrong
        folded = mon.office_lines(rows)
        joined = "\n".join(folded)
        self.assertEqual(folded[1:4], lines[1:4])
        self.assertNotIn("audit 284 release", joined)
        self.assertNotIn("@3f86dc6：", joined)
        self.assertIn("@71db418（", joined)   # the running review stays
        self.assertTrue(any(line.startswith("… 灰色 6 項") and line.rstrip().endswith("（已摺疊，按 a 展開）")
                            for line in folded))
        self.assertTrue(any(line.startswith("… 灰色 3 項") for line in folded))
        self.assertFalse(any(line.startswith(("agy", "公司 gw")) for line in folded))

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
        wide = next(text for text, _ in mon.office_view(rows, width=240, expand=True)
                    if text.startswith("agy"))
        self.assertTrue(wide.startswith("agy ") and wide.endswith("暫停中  Owner 暫停派工（10-22）"))
        stalled = next(text for text, _ in mon.office_view(rows, width=240)
                       if text.startswith("└ codex subagent A"))
        self.assertIn("  audit  ", stalled)
        self.assertTrue(stalled.endswith("  16 分前"))
        self.assertLessEqual(mon._width(stalled), mon._width("└ codex subagent A") + 2 + 8 + mon.DETAIL_MAX + 2 + 7)
        self.assertEqual(view[next(t for t in view if t.startswith("└ codex subagent A"))], "!")
        self.assertEqual(view[next(t for t in view if t.startswith("… 灰色 1 項"))], "dim")
        self.assertEqual(next(style for text, style in mon.office_view(rows, width=80, expand=True)
                              if text.startswith("agy")), "dim")
        self.assertEqual(view[next(t for t in view if t.startswith("⚠ codex subagent A"))], "!")
        self.assertEqual(view[next(iter(view))], "title")
        # without a width (--once) nothing is cut
        self.assertIn("x" * 200, "\n".join(mon.office_lines(rows)))

    def _listed(self, rows, **kw):
        """Row lines of the office view (no title, alerts, sections or legend)."""
        lines = mon.office_lines(rows, **kw)
        start = next(i for i, line in enumerate(lines) if line.startswith("── ")) + 1
        return [line for line in lines[start:-1]
                if line and not line.startswith(("── ", "⚠", "ℹ"))]

    def test_office_orders_each_section_red_green_yellow_gray_and_keeps_order_within_a_band(self):
        self.assertEqual(mon.BANDS, ("?", "+", "!", "dim"))   # Owner 2026-10-28: 紅、綠、黃、灰
        # no real state is red yet; the "?" band is exercised in the next test
        rows = [mon.OfficeRow("codex 主對話", mon.IDLE, "等待", 30, 10, key="T1"),
                mon.OfficeRow("claude reviewer", mon.STALLED, "review", 1000, 11),
                mon.OfficeRow("codex developer", mon.WORKING, "coding", 3, 12),
                mon.OfficeRow("claude pm", mon.DONE, "finished", 100, 13, cli=True),
                mon.OfficeRow("公司 gw", mon.ATTENTION, "沒派工", 3600, 14),
                mon.OfficeRow("claude developer", mon.WORKING, "wiring", 8, 15, cli=True),
                mon.OfficeRow("EC2 remote-test", mon.PAUSED, "paused", None, 16)]
        names = [line.split("  ")[0] for line in self._listed(rows, expand=True)]
        self.assertEqual(names, ["codex developer", "claude developer",      # green, as given
                                 "claude reviewer", "公司 gw",               # yellow, as given
                                 "codex 主對話", "claude pm", "EC2 remote-test"])   # gray, as given
        # the ordering is by colour alone: states inside one band keep their order
        styles = [style for _, style in mon.office_view(rows, expand=True)
                  if style in ("+", "!", "dim")][:-1]   # the legend is dimmed too
        self.assertEqual(styles, ["!", "!", "+", "+", "!", "!", "dim", "dim", "dim"])   # 2 ⚠ lines first

    def test_office_puts_a_red_row_ahead_of_green_rows_in_both_sections(self):
        # _row_style never yields "?" today (Codex review of PR #575): to exercise
        # the red band, one sentinel state is styled "?" from outside, without
        # touching how _row_style decides the other colours.
        RED = "紅測試"
        real = mon._row_style
        rows = [mon.OfficeRow("codex developer", mon.WORKING, "coding", 3, 10),
                mon.OfficeRow("claude reviewer", mon.STALLED, "review", 1000, 11),
                mon.OfficeRow("claude pm", mon.IDLE, "等待", 100, 12, cli=True),
                mon.OfficeRow("codex 主對話", RED, "crashed", 5, 13),           # listed last, red
                mon.OfficeRow("agy", mon.WORKING, "gw task", 8, 20, kind="worker"),
                mon.OfficeRow("公司 gw", mon.ATTENTION, "沒派工", 3600, 21, kind="worker"),
                mon.OfficeRow("EC2 remote-test", RED, "down", 9, 22, kind="worker")]   # listed last, red
        with patch.object(mon, "_row_style", lambda state: "?" if state == RED else real(state)):
            listed = mon.office_view(rows, expand=True)
            folded = self._listed(rows)
        texts = [text for text, _ in listed]
        live, status = texts.index(f"── {mon.LIVE_SECTION} ──"), texts.index(f"── {mon.STATUS_SECTION} ──")
        names = [text.split("  ")[0] for text in texts if text and not text.startswith(("── ", "⚠", "ℹ"))]
        view = dict(listed)
        # red first in the live section: ahead of the green developer and the yellow reviewer
        self.assertEqual(texts[live + 1].split("  ")[0], "codex 主對話")
        self.assertEqual(view[texts[live + 1]], "?")
        self.assertEqual(names[1:5], ["codex 主對話", "codex developer", "claude reviewer", "claude pm"])
        # red first in the Worker section too, ahead of the green agy and the yellow gw
        self.assertEqual(texts[status + 1].split("  ")[0], "EC2 remote-test")
        self.assertEqual(view[texts[status + 1]], "?")
        self.assertEqual(names[5:8], ["EC2 remote-test", "agy", "公司 gw"])
        # folding gray rows leaves the red rows in place at the top of each section
        self.assertEqual([line.split("  ")[0] for line in folded],
                         ["codex 主對話", "codex developer", "claude reviewer", "… 灰色 1 項",
                          "EC2 remote-test", "agy", "公司 gw"])
        # the patch is gone: a real state still never reaches the red band
        self.assertEqual(mon._row_style(RED), "dim")

    def test_office_keeps_groups_together_and_ranks_a_group_by_its_most_urgent_member(self):
        rows = [mon.OfficeRow("codex 主對話", mon.IDLE, "等待", 30, 10, key="T1"),
                mon.OfficeRow("codex subagent Dewey", mon.DONE, "audit", 600, 20, group="T1",
                              kind="subagent"),
                mon.OfficeRow("codex subagent Huygens", mon.WORKING, "repair", 5, 21, group="T1",
                              kind="subagent"),
                mon.OfficeRow("claude pm", mon.WORKING, "dispatch", 3, 11, key="C1", cli=True),
                mon.OfficeRow("claude subagent", mon.DONE, "report", 900, 12, group="C1",
                              kind="subagent"),
                mon.OfficeRow("claude reviewer", mon.STALLED, "review", 1000, 13)]
        listed = self._listed(rows, expand=True)
        names = [line.split("  ")[0] for line in listed]
        # a dimmed parent with a working subagent is green work: it sorts with the
        # green rows, its children stay under it, working ones first
        self.assertEqual(names, ["codex 主對話", "└ codex subagent Huygens", "└ codex subagent Dewey",
                                 "claude pm", "└ claude subagent", "claude reviewer"])
        # folded: that parent is not gray (its group is not), so it stays; its
        # finished subagent and the pm's are counted in the one gray line
        folded = self._listed(rows)
        self.assertEqual([line.split("  ")[0] for line in folded],
                         ["codex 主對話", "└ codex subagent Huygens", "claude pm", "claude reviewer",
                          "… 灰色 2 項"])
        self.assertTrue(folded[-1].rstrip().endswith("（已摺疊，按 a 展開）"))
        # a whole gray group is folded with its children counted once each
        rows.append(mon.OfficeRow("codex 主對話 2", mon.IDLE, "", 40, 30, key="T2"))
        rows.append(mon.OfficeRow("codex subagent Nye", mon.DONE, "", 700, 31, group="T2", kind="subagent"))
        self.assertEqual(self._listed(rows)[-1].split("  ")[0], "… 灰色 4 項")

    def test_office_key_a_toggles_the_gray_fold_and_the_default_is_folded(self):
        rows = [mon.OfficeRow("codex developer", mon.WORKING, "coding", 3, 12),
                mon.OfficeRow("codex 主對話", mon.IDLE, "等待", 30, 10, key="T1"),
                mon.OfficeRow("claude pm", mon.DONE, "finished", 100, 13, cli=True)]
        folded = mon.office_view(rows)   # expand defaults to False: the TUI starts folded
        texts = [text for text, _ in folded]
        self.assertEqual(texts.count("codex developer" + texts[-3][len("codex developer"):]), 1)
        self.assertTrue(any(text.startswith("… 灰色 2 項") and style == "dim" for text, style in folded))
        self.assertFalse(any(text.startswith(("codex 主對話", "claude pm")) for text in texts))
        self.assertEqual(folded[0][0], "辦公室 · 1 位工作中 · 共 3 位")   # the title counts everyone
        unfolded = [text for text, _ in mon.office_view(rows, expand=True)]
        self.assertTrue(any(text.startswith("codex 主對話") for text in unfolded))
        self.assertTrue(any(text.startswith("claude pm") for text in unfolded))
        self.assertFalse(any("灰色" in text for text in unfolded))
        # nothing gray: no fold line
        self.assertFalse(any("灰色" in text for text, _ in mon.office_view(rows[:1])))
        # folded again: the same view as before
        self.assertEqual(mon.office_view(rows), folded)

    def test_status_only_section_is_ordered_and_folded_the_same_way(self):
        rows = [mon.OfficeRow("codex 主對話", mon.WORKING, "指揮", 5, 10, key="T1"),
                mon.OfficeRow("agy", mon.IDLE, "可派工", None, 60, kind="worker"),
                mon.OfficeRow("公司 gw", mon.ATTENTION, "沒派工", 3600, 70, kind="worker"),
                mon.OfficeRow("EC2 remote-test", mon.PAUSED, "paused", None, 80, kind="worker"),
                mon.OfficeRow("opencode developer", mon.WORKING, "跑中", 10, 50, kind="worker")]
        lines = mon.office_lines(rows, expand=True)
        start = lines.index(f"── {mon.STATUS_SECTION} ──") + 1
        self.assertEqual([line.split("  ")[0] for line in lines[start:-1]],
                         ["opencode developer", "公司 gw", "agy", "EC2 remote-test"])
        lines = mon.office_lines(rows)
        start = lines.index(f"── {mon.STATUS_SECTION} ──") + 1
        self.assertEqual([line.split("  ")[0] for line in lines[start:-1]],
                         ["opencode developer", "公司 gw", "… 灰色 2 項"])
        # the live section above has nothing gray and so no fold line of its own
        self.assertEqual("\n".join(lines).count("灰色"), 1)

    def test_shared_ci_queue_line_sits_under_the_runner_rows_and_is_never_folded(self):
        def names(lines):
            start = lines.index(f"── {mon.STATUS_SECTION} ──") + 1
            return [line.split("  ")[0] for line in lines[start:-1]]
        queue = mon.ci_queue_row(["llmtool Mac CI（PR #341 @bbbbbbb）"], 2)
        runners = [mon.OfficeRow("mac ci runner #1", mon.IDLE, "閒置，等工作", 60, 81, kind="worker"),
                   mon.OfficeRow("mac ci runner #2", mon.WORKING, "執行中：x", 10, 82, kind="worker")]
        rows = [mon.OfficeRow("codex 主對話", mon.WORKING, "指揮", 5, 10, key="T1"),
                mon.OfficeRow("公司 gw", mon.ATTENTION, "沒派工", 3600, 70, kind="worker"),
                *runners, queue,
                mon.OfficeRow("EC2 remote-test", mon.PAUSED, "paused", None, 80, kind="worker")]
        # Expanded: the line follows the last runner row shown (#2 works, green;
        # #1 is gray and listed later), never between them (PR #614 review);
        # the title counts members only.
        view = mon.office_view(rows, expand=True)
        lines = [text for text, _ in view]
        self.assertEqual(names(lines), ["mac ci runner #2", "公司 gw", "mac ci runner #1",
                                        "mac ci 兩台共用", "EC2 remote-test"])
        self.assertTrue(lines[0].startswith("辦公室 · 2 位工作中 · 共 5 位"), lines[0])
        shared = next(text for text, _ in view if text.startswith("mac ci 兩台共用"))
        self.assertIn("排隊 1 個：llmtool Mac CI（PR #341 @bbbbbbb）", shared)
        self.assertFalse(shared.rstrip().endswith("前"))   # no time of its own
        # Its colour is the runners' most urgent band (green while #2 works)...
        self.assertEqual(dict(view)[shared], "+")
        # Folded: #1 is gray and folded; the line stays, right under the only runner shown.
        folded = mon.office_lines(rows)
        self.assertEqual(names(folded), ["mac ci runner #2", "mac ci 兩台共用", "公司 gw", "… 灰色 2 項"])
        # Both runners idle: gray rows fold, the queue line does not (jobs wait).
        idle = [replace(runners[1], state=mon.IDLE), runners[0], queue]
        view = mon.office_view([rows[0], rows[1], *idle, rows[-1]])
        texts = [text for text, _ in view]
        self.assertEqual(names(texts), ["公司 gw", "mac ci 兩台共用", "… 灰色 3 項"])
        self.assertEqual(dict(view)[next(t for t in texts if t.startswith("mac ci 兩台共用"))], "dim")
        # One runner: no section header games, the line simply follows it.
        one = [mon.OfficeRow("mac ci runner", mon.STOPPED, "沒在跑", None, 81, kind="worker",
                             alert="CI runner 沒在跑"), mon.ci_queue_row(["a", "b"], 1)]
        view = mon.office_view(one)
        self.assertEqual(names([t for t, _ in view]), ["mac ci runner", "mac ci 排隊"])
        self.assertEqual(view[-2][1], "!")   # yellow like the stopped runner it waits for

    def test_wrap_breaks_at_punctuation_and_never_starts_with_a_closing_mark(self):
        text = "可派工；本機 llmtool 紀錄（不分 repo）最近一次：輸出被拒（回應格式錯誤）"
        for cols in range(8, 40):
            pieces = mon._wrap(text, cols)
            self.assertEqual("".join(pieces).replace(" ", ""), text.replace(" ", ""))
            self.assertTrue(all(mon._width(piece) <= cols for piece in pieces))
            self.assertFalse(any(piece[:1] in mon.NO_LINE_START for piece in pieces))
        self.assertEqual(mon._wrap(text, 30)[0], "可派工；本機 llmtool")   # at the space
        self.assertEqual(mon._wrap("短", 10), ["短"])

    def test_wrap_always_makes_progress_at_narrow_widths(self):
        import signal

        def timeout(*_):
            raise TimeoutError("_wrap hung on narrow width")

        previous = signal.signal(signal.SIGALRM, timeout)
        self.addCleanup(signal.signal, signal.SIGALRM, previous)
        cases = (
            ("ascii", "abcdefg hello world; a, b"),
            ("CJK", "可派工；本機紀錄（不分）中文字工作測試"),
            ("mixed", "a中b文c（測試）test, x；y"),
            ("combining", "e\u0301a\u0300o\u0308中\u0301文\u0302"),
            ("zero-width", "a\u200bb\u200dc\ufeff中\u200b文"),
        )
        signal.alarm(2)
        try:
            self.assertEqual(mon._wrap("中", 1), ["中"])
            self.assertEqual(mon._wrap("中文", 1), ["中", "文"])
            for kind, text in cases:
                for cols in range(1, 9):
                    with self.subTest(kind=kind, cols=cols):
                        pieces = mon._wrap(text, cols)
                        self.assertEqual("".join(pieces).replace(" ", ""), text.replace(" ", ""))
                        self.assertTrue(all(mon._width(piece) <= max(cols, 2) for piece in pieces))
        finally:
            signal.alarm(0)

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

    # #708: a newer completed one-shot run in the same D6 task and role demotes
    # an older failed run from ATTENTION to DONE; nothing else does.
    EXEC_FAILED = "01a18aef-b21c-73f0-a5b9-0000000a0001"
    EXEC_NEWER = "01a18bdd-19b2-7a21-8b5e-0000000b0002"

    def _exec_708(self, sid, age, cwd=None, events=("task_started", "task_complete")):
        self._codex(sid[-8:], {"id": sid, "cwd": str(cwd or self.task),
                               "thread_source": "user", "originator": "codex_exec"},
                    list(events), age=age)

    def _run_708(self, newer_status, newer_events=("task_started", "task_complete"),
                 cwd=None, newer_age=600):
        now = int(self.NOW)
        turns = [(self.EXEC_FAILED, "u", 0, "failed", now - 2400, now - 1800)]
        if newer_status:
            turns.append((self.EXEC_NEWER, "u", 0, newer_status, now - 1200, now - 600))
        db = self._turn_db(turns)
        self._exec_708(self.EXEC_FAILED, 1800)
        self._exec_708(self.EXEC_NEWER, newer_age, cwd, newer_events)
        rows = self._office(db)
        alerts = [line for line in mon.office_lines(rows) if line.startswith("⚠")]
        old = next(row for row in rows if row.sid == self.EXEC_FAILED[-8:])
        return old, alerts

    def test_708_completed_recovery_clears_alert_and_keeps_row(self):
        old, alerts = self._run_708("completed")
        self.assertEqual(old.state, mon.DONE)
        self.assertIn("上一輪失敗", old.detail)
        self.assertFalse(any(self.EXEC_FAILED[-8:] in line for line in alerts))
        self.assertTrue(next(self.codex.rglob(f"rollout-{self.EXEC_FAILED[-8:]}.jsonl")).exists())

    def test_708_newer_failure_keeps_alert(self):
        old, _ = self._run_708("failed")
        self.assertEqual(old.state, mon.ATTENTION)

    def test_708_newer_interrupted_keeps_alert(self):
        old, _ = self._run_708("interrupted")
        self.assertEqual(old.state, mon.ATTENTION)

    def test_708_newer_running_keeps_alert(self):
        old, _ = self._run_708("inProgress", ("task_started",), newer_age=5)
        self.assertEqual(old.state, mon.ATTENTION)

    def test_708_completion_in_other_d6_does_not_clear(self):
        other = self.task.parent / "wp-2"
        os.makedirs(other)
        self._register("wp-2", str(other))
        old, _ = self._run_708("completed", cwd=other)
        self.assertEqual(old.state, mon.ATTENTION)

    def test_708_completion_in_unregistered_folder_does_not_clear(self):
        other = self.task.parent / "loose"
        os.makedirs(other)
        old, _ = self._run_708("completed", cwd=other)
        self.assertEqual(old.state, mon.ATTENTION)


class GitHubRunnerTests(unittest.TestCase):
    """Self-hosted runners off this Mac, from GitHub alone (mock gh api)."""

    MAC = {"name": "davidproject-self-action", "os": "macOS", "status": "online", "busy": True,
           "labels": [{"name": "self-hosted"}, {"name": "macOS"}, {"name": "local-mac-ci"}]}

    @staticmethod
    def linux(name, status="online", busy=False, label="ec2-llmtool"):
        return {"name": name, "os": "Linux", "status": status, "busy": busy,
                "labels": [{"name": "self-hosted"}, {"name": "Linux"}, {"name": "X64"}, {"name": label}]}

    class Gh:
        """gh api by path: the first matching route answers; calls are kept."""

        def __init__(self, routes):
            self.routes, self.calls = routes, []

        def __call__(self, argv, **kwargs):
            self.calls.append(argv[4])
            for key, answer in self.routes:
                if key in argv[4]:
                    if isinstance(answer, BaseException):
                        raise answer
                    code, payload = answer
                    out = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                    return __import__("types").SimpleNamespace(
                        returncode=code, stdout=out if code == 0 else b"",
                        stderr=out if code else b"")
            return __import__("types").SimpleNamespace(returncode=1, stdout=b"", stderr=b"")

    def source(self, routes, clock=lambda: NOW):
        gh = self.Gh([("issues/comments", (0, [])), ("/pulls", (0, []))] + routes)
        with patch.object(mon.shutil, "which", return_value="/opt/gh"):
            source = mon.GitHubSource("o/r", runner=gh, clock=clock)
            source.refresh()
        return source, gh

    def ci(self, runners):
        runs = {"workflow_runs": [
            {"id": 1, "status": "in_progress", "name": "llmtool CI", "head_sha": "a" * 40,
             "pull_requests": [{"number": 350}], "run_started_at": iso(NOW - 900)},
            {"id": 2, "status": "queued", "name": "llmtool CI", "head_sha": "b" * 40,
             "pull_requests": [{"number": 351}], "created_at": iso(NOW - 60)},
            {"id": 3, "status": "completed", "name": "llmtool CI", "head_sha": "c" * 40,
             "pull_requests": [{"number": 349}]}]}
        jobs = {
            1: [{"name": "check (macOS 3.13)", "status": "in_progress", "started_at": iso(NOW - 600),
                 "runner_name": "davidproject-self-action"},
                {"name": "Linux Python Test", "status": "in_progress", "started_at": iso(NOW - 180),
                 "runner_name": "ec2-llmtool-01"}],
            2: [{"name": "Linux Python Test", "status": "queued",
                 "labels": ["self-hosted", "Linux", "ec2-llmtool"]},
                {"name": "check (macOS 3.13)", "status": "queued", "labels": ["self-hosted", "macOS"]}],
            3: [{"name": "Linux Python Test", "status": "completed", "conclusion": "failure",
                 "completed_at": iso(NOW - 1200), "runner_name": "ec2-llmtool-02"}]}
        return [("actions/runners", (0, {"runners": runners}))] + [
            (f"actions/runs/{run}/jobs", (0, {"jobs": found})) for run, found in jobs.items()] + [
            ("actions/runs?", (0, runs))]

    def test_header_counts_runners_and_the_ec2_worker_not_processes(self):
        procs = [mon.Process(30, 1, 30, "01:00", "S", "Actions runner", "Runner.Listener"),
                 mon.Process(31, 30, 30, "00:10", "S", "Actions runner job", "Runner.Worker"),
                 mon.Process(40, 1, 40, "01:00", "S", "Actions runner", "Runner.Listener"),
                 mon.Process(41, 40, 40, "00:10", "S", "Actions runner job", "Runner.Worker"),
                 mon.Process(50, 1, 50, "01:00", "S+", "Codex CLI", "codex")]
        runners = mon.ci_runners({"runners": [
            self.MAC, self.linux("ec2-llmtool-01"), self.linux("ec2-llmtool-02", "offline"),
            {"name": "ec2-self-runner-3", "os": "unknown", "status": "online", "busy": False,
             "labels": [{"name": "local-ec2-linux-ci-pending"}]}]})
        fresh = mon.CiView(runners, NOW - 30)
        self.assertEqual(mon.summary(procs, [], [], ci_view=fresh, now=NOW),
                         "Codex 1  Mac runner 2  EC2 runner 3  EC2 worker 1")
        # old, failed or never-read runner data is not counted
        for view in (mon.CiView(runners, NOW - mon.GITHUB_STALE - 1),
                     mon.CiView(runners, NOW - 30, error="逾時"),
                     mon.CiView(None, 0.0, error="逾時"), None):
            self.assertEqual(mon.summary(procs, [], [], ci_view=view, now=NOW),
                             "Codex 1  Mac runner 2  EC2 runner 未確認  EC2 worker 1")

    def test_an_ec2_runner_with_only_a_pending_label_is_a_linux_runner(self):
        runners = mon.ci_runners({"runners": [
            {"name": "ec2-self-runner-3", "os": "unknown", "status": "online", "busy": False,
             "labels": [{"name": "local-ec2-linux-ci-pending"}]}]})
        [row] = mon.github_runner_rows(mon.CiView(runners, NOW - 30), NOW)
        self.assertEqual((row.name, row.state), ("linux ci runner ec2-self-runner-3", mon.IDLE))
        self.assertIn("標籤 local-ec2-linux-ci-pending", row.detail)

    def test_idle_busy_offline_and_several_linux_runners_beside_the_mac(self):
        source, gh = self.source(self.ci([
            self.MAC, self.linux("ec2-llmtool-01", busy=True), self.linux("ec2-llmtool-02"),
            self.linux("ec2-llmtool-03", status="offline")]))
        self.assertTrue(all(call.startswith("repos/o/r/") for call in gh.calls))
        rows = mon.github_runner_rows(source.ci_view, NOW, {"davidproject-self-action"}, source.ci_runs)
        # The Mac runner keeps its own local row; every Linux runner gets one from GitHub.
        self.assertEqual([(row.name, row.state) for row in rows], [
            ("linux ci runner ec2-llmtool-01", mon.WORKING),
            ("linux ci runner ec2-llmtool-02", mon.IDLE),
            ("linux ci runner ec2-llmtool-03", mon.STOPPED),
            ("linux ci 3 台共用", "")])
        busy, idle, offline = rows[:3]
        self.assertEqual(busy.detail, "執行中：llmtool CI（PR #350 @aaaaaaa）：Linux Python Test"
                                      "（已執行 3 分鐘）；Linux；標籤 ec2-llmtool")
        self.assertEqual((busy.age, busy.alert, busy.evidence), (180.0, "", "GitHub"))
        shared, = [row for row in rows if row.kind == "shared"]
        self.assertEqual((shared.name, shared.detail),
                         ("linux ci 3 台共用", "排隊 1 個：llmtool CI（PR #351 @bbbbbbb）：Linux Python Test"))
        self.assertTrue(idle.detail.startswith(
            "閒置，等工作；Linux；標籤 ec2-llmtool；上一個工作：llmtool CI（PR #349 @ccccccc）："
            "Linux Python Test 失敗（20 分鐘前）"), idle.detail)
        self.assertEqual(offline.alert, "CI runner ec2-llmtool-03 離線")
        self.assertTrue(offline.detail.startswith("離線（排給它的 CI 會等它）；Linux"))
        # Mac and EC2 run jobs of one workflow at once: each runner names its own job.
        mac = [run for run in source.ci_runs if run[3] == "davidproject-self-action"]
        self.assertEqual([run[1] for run in mac], ["llmtool CI（PR #350 @aaaaaaa）：check (macOS 3.13)"])
        # A finished run's jobs are read once, not on every refresh.
        reads = sum("runs/3/jobs" in call for call in gh.calls)
        source.refresh(force=True)
        self.assertEqual((reads, sum("runs/3/jobs" in call for call in gh.calls)), (1, 1))

    def test_a_queued_run_with_one_job_already_running_names_that_job(self):
        # GitHub calls the run "queued" while its 3.9 job waits, though 3.14 runs.
        runs = {"workflow_runs": [{"id": 5, "status": "queued", "name": "llmtool Linux CI",
                                   "head_sha": "e" * 40, "pull_requests": [{"number": 346}],
                                   "created_at": iso(NOW - 300)}]}
        jobs = {"jobs": [
            {"name": "check (Linux 3.14)", "status": "in_progress", "started_at": iso(NOW - 120),
             "runner_name": "ec2-llmtool-01", "labels": ["self-hosted", "Linux", "ec2-llmtool"]},
            {"name": "check (Linux 3.9)", "status": "queued",
             "labels": ["self-hosted", "Linux", "ec2-llmtool"]}]}
        source, _ = self.source([("actions/runners", (0, {"runners": [self.linux("ec2-llmtool-01", busy=True)]})),
                                 ("runs/5/jobs", (0, jobs)), ("actions/runs?", (0, runs))])
        row, queue = mon.github_runner_rows(source.ci_view, NOW, (), source.ci_runs)
        self.assertEqual(row.detail, "執行中：llmtool Linux CI（PR #346 @eeeeeee）：check (Linux 3.14)"
                                     "（已執行 2 分鐘）；Linux；標籤 ec2-llmtool")
        self.assertEqual((queue.name, queue.detail),
                         ("linux ci 排隊", "排隊 1 個：llmtool Linux CI（PR #346 @eeeeeee）：check (Linux 3.9)"))
        # The run still counts once as waiting, for the Mac row's queue line.
        self.assertEqual([(run[0], run[3]) for run in source.ci_runs],
                         [("in_progress", "ec2-llmtool-01"), ("queued", None)])
        run = [("in_progress", "x", NOW, None, 5)]
        named = lambda jobs: [entry[0] for entry in mon.ci_runner_names(run, lambda _: jobs)]
        self.assertEqual(named({"jobs": [{"status": "queued"}]}), ["queued"])   # nothing runs yet
        self.assertEqual(named({"jobs": [{"status": "in_progress", "runner_name": "r"}]}), ["in_progress"])
        self.assertEqual(named({"jobs": []}), ["in_progress"])   # nothing to go by: the run's word
        self.assertEqual(named(None), ["in_progress"])           # unreadable: the same

    def test_queued_work_is_attributed_only_by_labels_and_unregistered_shows_nothing(self):
        source, _ = self.source(self.ci([self.MAC, self.linux("ec2-llmtool-01", label="other")]))
        rows = mon.github_runner_rows(source.ci_view, NOW, {"davidproject-self-action"}, source.ci_runs)
        self.assertEqual([row.name for row in rows], [
            "linux ci runner ec2-llmtool-01", "ci 未分配 排隊"])
        self.assertNotIn("排隊", rows[0].detail)   # it lacks the ec2-llmtool label the job asks for
        source, _ = self.source(self.ci([self.MAC]))   # the EC2 runner is not registered yet
        self.assertEqual(mon.github_runner_rows(source.ci_view, NOW, {"davidproject-self-action"},
                                                source.ci_runs), [])
        self.assertEqual(mon.github_runner_rows(None, NOW), [])   # --no-github: nothing claimed

    def test_queued_jobs_are_shown_once_in_platform_rows_or_unassigned(self):
        mac = ("davidproject-self-action", "macOS", "online", False,
               ("self-hosted", "macOS", "x64"))
        # CiView stores normalized runner tuples, not the raw API dictionaries
        # returned by self.linux() for source fixtures.
        linux = ("ec2-self-runner-1", "Linux", "online", False,
                 ("self-hosted", "Linux", "X64", "ec2-llmtool"))
        linux2 = ("ec2-self-runner-2", "Linux", "online", False,
                  ("self-hosted", "Linux", "X64", "ec2-llmtool"))
        mac_job = (frozenset({"self-hosted", "macos"}), "Mac workflow: check")
        linux_job = (frozenset({"self-hosted", "linux", "ec2-llmtool"}), "Linux workflow: test")
        nowhere = (frozenset({"self-hosted", "special"}), "Unmatched workflow: test")
        view = mon.CiView([mac, linux, linux2], NOW - 10,
                          queued=(mac_job, linux_job, nowhere))
        mac_jobs, linux_jobs, unassigned = mon._ci_queue_groups(view, {mac[0]})
        self.assertEqual((mac_jobs, linux_jobs, unassigned),
                         ([mac_job[1]], [linux_job[1]], [nowhere[1]]))
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, ".runner"), "w") as handle:
                json.dump({"agentName": mac[0]}, handle)
            mac_rows = mon.runner_rows([], NOW, root, ci=[], waiting_jobs=mac_jobs)
        mac_queue, = [row for row in mac_rows if row.kind == "shared"]
        self.assertEqual((mac_queue.name, mac_queue.detail),
                         ("mac ci 排隊", "排隊 1 個：Mac workflow: check"))
        rows = mon.github_runner_rows(view, NOW, {mac[0]})
        shared = [row for row in rows if row.kind == "shared"]
        self.assertEqual([(row.name, row.detail) for row in shared], [
            ("linux ci 兩台共用", "排隊 1 個：Linux workflow: test"),
            ("ci 未分配 排隊", "排隊 1 個：Unmatched workflow: test")])
        rendered = "\n".join(row.detail for row in rows) + "\n".join(
            row.detail for row in [mon.ci_queue_row(mac_jobs, 2)])
        self.assertEqual(rendered.count("Linux workflow: test"), 1)
        self.assertEqual(rendered.count("Mac workflow: check"), 1)
        self.assertEqual(rendered.count("Unmatched workflow: test"), 1)

    def test_api_failures_and_old_data_say_unconfirmed_never_offline(self):
        for answer, words in (((1, b"gh: Must have admin rights (HTTP 403)"), "沒有權限（HTTP 403）"),
                              (subprocess.TimeoutExpired("gh", 20), "逾時")):
            source, _ = self.source([("actions/runners", answer)])
            row, = mon.github_runner_rows(source.ci_view, NOW)
            self.assertEqual((row.state, row.alert), (mon.UNCHECKED, f"GitHub runner 清單讀不到（{words}）"))
            self.assertIn("狀態未確認", row.detail)
            self.assertTrue(source.status.startswith("GitHub o/r："))   # activity is unaffected
        # Seen once, then the list fails: the last word is kept, marked 未確認.
        clock = [NOW]
        source, gh = self.source(self.ci([self.linux("ec2-llmtool-01")]), clock=lambda: clock[0])
        gh.routes.insert(0, ("actions/runners", (1, b"HTTP 403")))
        clock[0] = NOW + 40
        source.refresh(force=True)
        row, = mon.github_runner_rows(source.ci_view, clock[0])
        self.assertEqual(row.state, mon.UNCHECKED)
        self.assertEqual(row.detail.split("；")[:2],
                         ["未確認：GitHub 資料 不到 1 分鐘沒更新（沒有權限（HTTP 403））", "上次看到：閒置"])
        # Data older than a few refreshes is stale even without an error.
        old = replace(source.ci_view, error="")
        row, = mon.github_runner_rows(old, NOW + 600)
        self.assertEqual((row.state, row.alert), (mon.UNCHECKED, ""))
        self.assertTrue(row.detail.startswith("未確認：GitHub 資料 10 分鐘沒更新；上次看到：閒置"))

    def test_reading_github_never_holds_up_the_screen(self):
        import threading
        gate = threading.Event()
        gh = self.Gh([("issues/comments", (0, [])), ("/pulls", (0, []))]
                     + self.ci([self.linux("ec2-llmtool-01")]))
        def slow(argv, **kwargs):
            gate.wait(5)
            return gh(argv, **kwargs)
        with patch.object(mon.shutil, "which", return_value="/opt/gh"):
            source = mon.GitHubSource("o/r", runner=slow, clock=lambda: NOW)
            source.refresh_in_background()
            # gh is still "running": rows are built from what is known, at once.
            self.assertEqual(mon.github_runner_rows(source.ci_view, NOW, (), source.ci_runs), [])
            gate.set()
            source._thread.join(5)
        self.assertEqual([row.name for row in mon.github_runner_rows(source.ci_view, NOW)],
                         ["linux ci runner ec2-llmtool-01",
                          "linux ci 排隊", "ci 未分配 排隊"])


if __name__ == "__main__":
    unittest.main()


def _reference_tail(path, limit):
    """_jsonl_tail as it read a record before caching (Issue #625): the whole
    last `limit` bytes, parsed every time."""
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        handle.seek(max(0, size - limit))
        data = handle.read(limit)
    lines = data.split(b"\n")
    if size > limit:
        lines = lines[1:]
    out = []
    for raw in lines:
        if raw.strip():
            try:
                item = json.loads(raw)
            except ValueError:
                continue
            if type(item) is dict:
                out.append(item)
    return out


class RefreshCostTests(unittest.TestCase):
    """Issue #625: an office rebuild reads again only what changed, GitHub is
    read at a bounded pace, and every cached answer equals a fresh read."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for cache in (mon._tail_cache, mon._head_cache, mon._entry_cache, mon._registry_cache,
                      mon._registry_records, mon._llmtool_records, mon._runner_log_cache,
                      mon._cli_registry_cache, mon._pid_facts, mon._listing_cache):
            cache.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def _count_opens(self, path):
        """Context manager counting open() calls on one path."""
        real_open, opened = open, []

        def counting(file, *args, **kwargs):
            if file == path or str(file) == str(path):
                opened.append(file)
            return real_open(file, *args, **kwargs)
        return patch("builtins.open", counting), opened

    def test_tail_is_read_again_only_when_the_file_changed_and_then_matches_a_fresh_read(self):
        path = self.root / "s.jsonl"
        line = lambda n: json.dumps({"type": "user", "n": n, "t": "x" * (n % 7)}).encode() + b"\n"
        with open(path, "wb") as handle:
            handle.writelines(line(n) for n in range(40))
        with patch.object(mon, "OFFICE_TAIL", 600):
            first = mon._jsonl_tail(str(path))
            self.assertEqual(first, _reference_tail(str(path), 600))
            counter, opened = self._count_opens(str(path))
            with counter:
                self.assertEqual(mon._jsonl_tail(str(path)), first)   # unchanged: no read
            self.assertEqual(opened, [])
            # Appends, including a line finished in a later write, are followed.
            with open(path, "ab") as handle:
                handle.write(line(40) + b'{"type": "user", "n": 41')
            self.assertEqual(mon._jsonl_tail(str(path)), _reference_tail(str(path), 600))
            with open(path, "ab") as handle:
                handle.write(b', "done": true}\n' + line(42))
            grown = mon._jsonl_tail(str(path))
            self.assertEqual(grown, _reference_tail(str(path), 600))
            self.assertEqual(grown[-2]["n"], 41)
            # A rewrite of the same size with the mtime put back is still seen
            # (ctime), as is one that shrinks or changes the bytes before the tail.
            stamp = os.stat(path)
            with open(path, "wb") as handle:
                handle.writelines(line(n + 100) for n in range(40))
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            self.assertEqual(mon._jsonl_tail(str(path)), _reference_tail(str(path), 600))
            with open(path, "r+b") as handle:   # same size, same growth: earlier bytes differ
                handle.seek(0)
                handle.write(line(100).replace(b'"n": 100', b'"n": 900'))
                handle.seek(0, 2)
                handle.write(line(7))
            self.assertEqual(mon._jsonl_tail(str(path)), _reference_tail(str(path), 600))
            with open(path, "wb") as handle:
                handle.write(line(1))
            self.assertEqual(mon._jsonl_tail(str(path)), _reference_tail(str(path), 600))
        os.remove(path)
        self.assertEqual(mon._jsonl_tail(str(path)), [])

    def test_tail_cache_is_bounded(self):
        for n in range(mon.CACHE_FILES + 5):
            path = self.root / f"{n}.jsonl"
            path.write_bytes(b'{"n": %d}\n' % n)
            mon._jsonl_tail(str(path))
        self.assertEqual(len(mon._tail_cache), mon.CACHE_FILES)
        self.assertNotIn(str(self.root / "0.jsonl"), mon._tail_cache)   # the oldest left first

    def test_head_and_entrypoint_follow_a_changed_file(self):
        path = self.root / "r.jsonl"
        path.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": "/a"}}) + "\n"
                        + json.dumps({"entrypoint": "cli"}) + "\n")
        self.assertEqual(mon._jsonl_head(str(path))["payload"]["cwd"], "/a")
        self.assertEqual(mon._first_entrypoint(str(path)), "cli")
        counter, opened = self._count_opens(str(path))
        with counter:
            mon._jsonl_head(str(path))
            mon._first_entrypoint(str(path))
        self.assertEqual(opened, [])
        path.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": "/b"}}) + "\n"
                        + json.dumps({"entrypoint": "sdk-cli"}) + "\n")
        self.assertEqual(mon._jsonl_head(str(path))["payload"]["cwd"], "/b")
        self.assertEqual(mon._first_entrypoint(str(path)), "sdk-cli")

    def _registry(self, canonical, task_id, **fields):
        registry = canonical / ".git" / "llmtool-worktrees"
        registry.mkdir(parents=True, exist_ok=True)
        os.chmod(registry, 0o700)
        record = {"task_id": task_id, "root_realpath": os.path.realpath(self.root / task_id),
                  "lock_state": "LOCKED", "state": "ACTIVE", "session_ref": "",
                  "trusted_agent_id": "codex-developer"}
        record.update(fields)
        path = registry / f"{task_id}.json"
        draft = registry / f".{task_id}.json.tmp"   # replaced atomically, as the registry writes
        draft.write_text(json.dumps(record), encoding="utf-8")
        os.chmod(draft, 0o600)
        os.replace(draft, path)
        return path

    def test_registry_records_are_parsed_again_only_when_their_file_changed(self):
        canonical = self.root / "repo"
        a, b = self._registry(canonical, "wp-a"), self._registry(canonical, "wp-b")
        found = mon.registered_tasks(str(canonical))
        self.assertEqual(sorted(found), ["wp-a", "wp-b"])
        self.assertEqual(mon.registry_details(str(canonical))["wp-a"][0], "ACTIVE")
        # Another record changes: the folder mtime moves, only that record is opened.
        time.sleep(0.01)
        self._registry(canonical, "wp-b", state="REVIEW_PENDING")
        counter, opened_a = self._count_opens(str(a))
        with counter:
            found = mon.registered_tasks(str(canonical))
        self.assertEqual(opened_a, [])
        self.assertEqual(mon.registry_details(str(canonical))["wp-b"][0], "REVIEW_PENDING")
        self.assertEqual(sorted(found), ["wp-a", "wp-b"])
        # A record that stops qualifying leaves, one that qualifies again returns.
        self._registry(canonical, "wp-a", lock_state="UNLOCKED")
        self.assertEqual(sorted(mon.registered_tasks(str(canonical))), ["wp-b"])
        self._registry(canonical, "wp-a")
        self.assertEqual(sorted(mon.registered_tasks(str(canonical))), ["wp-a", "wp-b"])
        os.remove(b)
        self.assertEqual(sorted(mon.registered_tasks(str(canonical))), ["wp-a"])

    def test_llmtool_records_are_parsed_once_until_they_change(self):
        now = time.time()
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 600))
        record = {"task_id": "ab12cd34" * 4, "updated_at": stamp, "state": "PM_RESPONDED",
                  "request": "REQUEST-SECRET", "pm_assignment": {"route": "company-gw"},
                  "developer": {"provider_profile": "company-gw", "state": "DEVELOPER_RESPONDED",
                                "artifact": "DIFF-SECRET"},
                  "completion": {"state": "WAITING_EXECUTOR", "executor_task_id": "issue-9-dev"}}
        path = self.root / "a.json"
        path.write_text(json.dumps(record))
        (self.root / "bad.json").write_text("not json")
        first = mon.llmtool_task_records(now, str(self.root))
        outcome = mon.llmtool_route_outcomes(now, str(self.root))
        self.assertEqual(len(first), 1)
        self.assertIn("派給 company-gw", first[0][1])
        self.assertEqual(outcome["company-gw"][1:3], (1, 1))
        self.assertNotIn("SECRET", json.dumps(mon._llmtool_records[str(path)][1]))   # content never kept
        counter, opened = self._count_opens(str(path))
        with counter:
            self.assertEqual(mon.llmtool_task_records(now, str(self.root)), first)
            self.assertEqual(mon.llmtool_route_outcomes(now, str(self.root)), outcome)
        self.assertEqual(opened, [])
        record["developer"]["state"] = "DEVELOPER_OUTPUT_REJECTED"
        time.sleep(0.01)
        path.write_text(json.dumps(record))
        self.assertIn("輸出被拒", mon.llmtool_task_records(now, str(self.root))[0][1])
        self.assertEqual(mon.llmtool_route_outcomes(now, str(self.root))["company-gw"][2], 0)
        (self.root / "b.json").write_text(json.dumps(record))   # a new record is read
        self.assertEqual(len(mon.llmtool_task_records(now, str(self.root))), 2)

    def test_runner_log_is_parsed_again_only_when_it_changed(self):
        diag = self.root / "_diag"
        diag.mkdir()
        log = diag / "Runner_20261025-110000-utc.log"
        log.write_text("[x INFO Terminal] WRITE LINE: 2026-10-25 11:00:05Z: Listening for Jobs\n")
        lines = mon._runner_log(str(self.root))
        self.assertEqual([text for _, text in lines], ["Listening for Jobs"])
        with patch.object(mon.os, "read", side_effect=AssertionError("read again")):
            self.assertEqual(mon._runner_log(str(self.root)), lines)
        with open(log, "a") as handle:
            handle.write("[x INFO Terminal] WRITE LINE: 2026-10-25 11:10:00Z: Running job: check\n")
        self.assertEqual([text for _, text in mon._runner_log(str(self.root))],
                         ["Listening for Jobs", "Running job: check"])

    def test_process_user_and_isolation_are_read_once_per_living_pid(self):
        answers = iter(["ghrunner", "ghrunner2"])
        fake = lambda argv, **kwargs: __import__("types").SimpleNamespace(
            returncode=0, stdout=next(answers) + "\n", stderr="")
        with patch.object(mon.subprocess, "run", side_effect=fake):
            self.assertEqual(mon.process_user(4242), "ghrunner")
            self.assertEqual(mon.process_user(4242), "ghrunner")   # remembered
            mon.forget_pids([mon.Process(4242, 1, 4242, "01:00", "S", "Actions runner", "Runner", "", ())])
            self.assertEqual(mon.process_user(4242), "ghrunner")   # still alive: kept
            mon.forget_pids([])
            self.assertEqual(mon.process_user(4242), "ghrunner2")  # gone and back: read again

    def test_session_listing_follows_new_files(self):
        top = self.root / "projects"
        folder = top / "-Users-x-repo"
        folder.mkdir(parents=True)
        (folder / "a.jsonl").write_text("{}\n")
        (folder / ".hidden.jsonl").write_text("{}\n")
        self.assertEqual([os.path.basename(p) for p in mon._session_files(str(top))], ["a.jsonl"])
        time.sleep(0.01)
        (folder / "b.jsonl").write_text("{}\n")
        self.assertEqual(sorted(os.path.basename(p) for p in mon._session_files(str(top))),
                         ["a.jsonl", "b.jsonl"])
        self.assertEqual(mon._session_files(str(self.root / "missing")), [])

    def test_github_reads_are_spaced_from_the_end_and_back_off_while_gh_fails(self):
        clock = [1000.0]
        with patch.object(mon.time, "monotonic", lambda: clock[0]), \
             patch.object(mon.shutil, "which", return_value="/opt/gh"):
            good = FakeRun((0, []), (0, []), (1, b""))
            source = mon.GitHubSource("o/r", runner=good, clock=lambda: NOW)
            self.assertTrue(source.due())
            source.refresh()
            self.assertEqual(source.wait, mon.GITHUB_REFRESH)
            clock[0] += 29
            self.assertFalse(source.due())
            source.refresh()                      # not due: no call
            self.assertEqual(len(good.calls), 3)
            # A read that itself took 25 seconds: the next one waits 30 s after it ended.
            slow = FakeRun((0, []), (0, []), (1, b""))
            def slow_run(argv, **kwargs):
                clock[0] += 25
                return slow(argv, **kwargs)
            source.runner = slow_run
            clock[0] += 2
            source.refresh()
            ended = clock[0]
            clock[0] = ended + 29
            self.assertFalse(source.due())
            clock[0] = ended + 30
            self.assertTrue(source.due())
            # gh failing: 60, 120, 240, 300 (cap) seconds between attempts, then back to 30.
            source.runner = FakeRun()
            waits = []
            for _ in range(5):
                source.refresh(force=True)
                waits.append(source.wait)
            self.assertEqual(waits, [60.0, 120.0, 240.0, 300.0, 300.0])
            self.assertIn("讀取失敗", source.status)
            clock[0] += 299
            self.assertFalse(source.due())
            clock[0] += 1
            self.assertTrue(source.due())
            source.runner = FakeRun((0, []), (0, []), (1, b""))
            source.refresh()
            self.assertEqual(source.wait, mon.GITHUB_REFRESH)

    def test_unfinished_run_jobs_are_read_again_only_when_the_run_changed(self):
        def runs(updated):
            return {"workflow_runs": [{"id": 77, "status": "in_progress", "name": "CI", "updated_at": updated,
                                       "head_sha": "a" * 40, "pull_requests": [{"number": 7}]}]}
        jobs = {"jobs": [{"status": "in_progress", "runner_name": "mac-1", "name": "check"}]}
        clock = [NOW]
        fake = FakeRun((0, []), (0, []), (0, {"runners": []}), (0, runs("t1")), (0, jobs),
                       (0, []), (0, []), (0, {"runners": []}), (0, runs("t1")),
                       (0, []), (0, []), (0, {"runners": []}), (0, runs("t2")), (0, jobs))
        with patch.object(mon.shutil, "which", return_value="/opt/gh"):
            source = mon.GitHubSource("o/r", runner=fake, clock=lambda: clock[0])
            source.want_ci = True
            source.refresh(force=True)
            self.assertEqual(source.ci_runs[0][3], "mac-1")
            clock[0] += 31
            source.refresh(force=True)            # same updated_at: the job list is reused
            self.assertEqual(source.ci_runs[0][3], "mac-1")
            self.assertEqual(sum("/jobs" in argv[4] for argv, _ in fake.calls), 1)
            clock[0] += 31
            source.refresh(force=True)            # the run changed: read again
            self.assertEqual(sum("/jobs" in argv[4] for argv, _ in fake.calls), 2)
            self.assertEqual(source.ci_runs[0][3], "mac-1")
            # The cached list is never older than GITHUB_JOBS_TTL even when the run looks the same.
            fake.responses += [(0, []), (0, []), (0, {"runners": []}), (0, runs("t2")), (0, jobs)]
            clock[0] += mon.GITHUB_JOBS_TTL + 1
            source.refresh(force=True)
            self.assertEqual(sum("/jobs" in argv[4] for argv, _ in fake.calls), 3)

    def test_curses_screen_is_painted_after_polls_and_keys_not_every_tick(self):
        import curses

        class FakeScreen:
            def __init__(self, keys):
                self.keys, self.paints = list(keys), 0
            def getmaxyx(self): return (40, 120)
            def timeout(self, ms): pass
            def keypad(self, flag): pass
            def erase(self): pass
            def addnstr(self, *args): pass
            def refresh(self): self.paints += 1
            def move(self, *args): pass
            def clearok(self, *args): pass
            def getch(self): return self.keys.pop(0) if self.keys else ord("q")
        screen = FakeScreen([-1] * 30 + [curses.KEY_DOWN] + [-1] * 30 + [ord("q")])
        monitor = mon.Monitor()
        with patch.object(mon.Monitor, "poll", return_value=[]), \
             patch.object(curses, "curs_set", lambda n: None), \
             patch.object(curses, "has_colors", lambda: False), \
             patch.object(mon, "DRAW_EVERY", 3600.0):
            mon.run_curses(screen, 3600.0, False, monitor)
        # One paint for the first poll, one after the key: never one per getch.
        self.assertEqual(screen.paints, 2)


class FakeKernel:
    """Stands in for _Sysctl: a process list and the commands it may read."""

    def __init__(self, procs, commands):
        self.procs, self.commands, self.reads = list(procs), dict(commands), []

    def processes(self):
        return list(self.procs)

    def command(self, pid):
        self.reads.append(pid)
        return self.commands.get(pid)


def kproc(pid, ppid, start, comm=b"x", uid=None, pstat=2, pgid=None, **kwargs):
    # uid None: this account, whatever it is (CI runs as a runner account)
    uid = os.getuid() if uid is None else uid
    return mon.KProc(pid, ppid, pgid or pid, int(start), 0, pstat, comm, uid, **kwargs)


class _TableFixture:
    """A ProcessTable over FakeKernel with a fake ps, ps -U and clocks."""

    def setUp(self):
        self.clock, self.wall, self.full, self.users = [100.0], [NOW], [], []
        self.me = kproc(os.getpid(), os.getppid(), NOW - 50, b"Python", flag=mon.P_CONTROLT,
                        tpgid=os.getpid())
        self.agent = kproc(4000, 1, NOW - 3725, b"claude", flag=mon.P_CONTROLT, tpgid=4000,
                           eflag=mon.EPROC_SLEADER)
        self.kernel = FakeKernel([self.me, self.agent], {os.getpid(): "python monitor",
                                                         4000: "claude --resume"})

    def _table(self, ps_text):
        def ps():
            self.full.append(self.clock[0])
            return ps_text() if callable(ps_text) else ps_text

        def ps_users(uids):
            self.users.append(set(uids))
            return self.user_text
        self.user_text = ""
        return mon.ProcessTable(ps=ps, kernel=self.kernel, clock=lambda: self.clock[0],
                                wall=lambda: self.wall[0], watched=frozenset({450}),
                                ps_users=ps_users)

    def _rows(self, table):
        return [(pid, ppid, pgid, etime() if callable(etime) else etime, stat, command)
                for pid, ppid, pgid, etime, stat, command in table.rows()]


class ProcessTableTests(_TableFixture, unittest.TestCase):
    """Issue #625: `ps -A` runs at most every PS_FULL_EVERY seconds; between
    runs the same rows come from the kernel's process list."""

    def test_rows_between_ps_runs_equal_what_ps_would_print(self):
        table = self._table(f"{os.getpid()} {os.getppid()} {os.getpid()} 00:50 S+ python monitor\n"
                            "4000 1 4000 01:02:05 Ss+ claude --resume\n")
        first = self._rows(table)
        self.assertEqual(self.full, [100.0])
        self.assertIn((4000, 1, 4000, "01:02:05", "Ss+", "claude --resume"), first)
        self.clock[0] += 1
        self.wall[0] += 1
        self.assertEqual(self._rows(table)[1], (4000, 1, 4000, "01:02:06", "Ss+", "claude --resume"))
        self.assertEqual(self.full, [100.0])   # no second ps
        self.assertEqual(self.kernel.reads, [])   # commands ps printed are not read again
        self.clock[0] += mon.PS_FULL_EVERY
        table.rows()
        self.assertEqual(len(self.full), 2)   # STAT and other accounts refreshed

    def test_snapshot_from_kernel_rows_matches_parse_of_ps_text(self):
        text = (f"{os.getpid()} {os.getppid()} {os.getpid()} 00:50 S+ python monitor\n"
                "4000 1 4000 01:02:05 Ss+ claude --resume\n"
                "4001 4000 4000 00:10 S /bin/zsh -c make\n")
        self.kernel.procs.append(kproc(4001, 4000, NOW - 10, b"zsh", pgid=4000))
        table = self._table(text)
        table.rows()
        self.assertEqual(mon.collect(table=table), mon.parse_processes(text, os.getpid()))

    def test_new_process_command_is_read_once_and_exec_reads_it_again(self):
        table = self._table(f"{os.getpid()} {os.getppid()} {os.getpid()} 00:50 S+ python monitor\n"
                            "4000 1 4000 01:02:05 Ss+ claude --resume\n")
        table.rows()
        self.kernel.procs.append(kproc(4100, 4000, NOW - 2, b"zsh", eflag=mon.EPROC_SLEADER))
        self.kernel.commands[4100] = "/bin/zsh -c ls"
        self.clock[0] += 1
        self.wall[0] += 1
        self.assertIn((4100, 4000, 4100, "00:03", "Ss", "/bin/zsh -c ls"), self._rows(table))
        self.clock[0] += 1
        self._rows(table)
        self.assertEqual(self.kernel.reads, [4100])   # remembered
        # exec: same pid and start, another program name -> read again
        self.kernel.procs[-1] = kproc(4100, 4000, NOW - 2, b"git", eflag=mon.EPROC_SLEADER)
        self.kernel.commands[4100] = "git status"
        self.clock[0] += 1
        self.assertIn("git status", [row[5] for row in self._rows(table)])
        self.assertEqual(self.kernel.reads, [4100, 4100])

    def test_other_accounts_newcomer_is_read_with_ps_u_at_a_bounded_pace(self):
        table = self._table("4000 1 4000 01:02:05 Ss+ claude --resume\n")
        table.rows()
        self.kernel.procs += [kproc(5000, 1, NOW - 1, b"Runner.Worker", uid=450),
                              kproc(5001, 1, NOW - 1, b"mdworker", uid=89)]
        self.user_text = "5000 1 5000 00:01 S /Users/ghrunner/actions-runner/bin/Runner.Worker\n"
        self.clock[0] += 1
        rows = self._rows(table)
        self.assertEqual(self.users, [{450}])   # only the runner account, never root/system
        self.assertIn((5000, 1, 5000, "00:01", "S",
                       "/Users/ghrunner/actions-runner/bin/Runner.Worker"), rows)
        self.assertNotIn(5001, [row[0] for row in rows])   # a system daemon waits for ps -A
        self.assertEqual(len(self.full), 1)
        # Another newcomer within PS_MIN_GAP waits; after it, ps -U runs again.
        self.kernel.procs.append(kproc(5002, 5000, NOW, b"bash", uid=450))
        self.clock[0] += mon.PS_MIN_GAP / 2
        self.assertNotIn(5002, [row[0] for row in self._rows(table)])
        self.assertEqual(len(self.users), 1)
        self.user_text += "5002 5000 5000 00:00 S /bin/bash -e step.sh\n"
        self.clock[0] += mon.PS_MIN_GAP
        self.assertIn(5002, [row[0] for row in self._rows(table)])
        self.assertEqual(len(self.users), 2)
        self.assertEqual(self.kernel.reads.count(5000), 1)   # an unreadable command is asked once

    def test_a_process_that_becomes_a_zombie_is_shown_as_ps_shows_it(self):
        table = self._table("4000 1 4000 01:02:05 Ss+ claude --resume\n")
        table.rows()
        self.kernel.procs[1] = kproc(4000, 1, NOW - 3725, b"claude", pstat=mon.SZOMB,
                                     eflag=mon.EPROC_SLEADER)
        self.clock[0] += 1
        self.assertEqual([row[4:] for row in self._rows(table) if row[0] == 4000], [("Zs", "<defunct>")])

    def test_kernel_list_that_does_not_show_this_process_falls_back_to_ps(self):
        self.kernel.procs = [self.agent]   # wrong layout: our own pid is not there
        table = self._table("4000 1 4000 01:02:05 Ss+ claude --resume\n")
        table.rows()
        self.clock[0] += 1
        table.rows()
        self.assertEqual(len(self.full), 2)
        self.assertIs(table.kernel, False)

    def test_ps_elapsed_matches_ps_etime_format(self):
        self.assertEqual([mon._ps_elapsed(s) for s in (0, 59, 61, 3600, 86399, 86400 + 61, 20 * 86400)],
                         ["00:00", "00:59", "01:01", "01:00:00", "23:59:59", "01-00:01:01",
                          "20-00:00:00"])

    def test_real_kernel_rows_agree_with_ps_on_this_mac(self):
        if sys.platform != "darwin":
            self.skipTest("macOS sysctl layout")
        kernel = mon._Sysctl()
        procs = kernel.processes()
        mine = [k for k in procs if k.pid == os.getpid()]
        self.assertEqual([(k.ppid, k.uid) for k in mine], [(os.getppid(), os.getuid())])
        self.assertTrue(mine[0].comm.lower().startswith(b"python"), mine[0].comm)
        shown = subprocess.run(["/bin/ps", "-o", "pid=,ppid=,pgid=,etime=,stat=,command=", "-p",
                                str(os.getpid())], capture_output=True, text=True).stdout
        (pid, ppid, pgid, _, _, command), = mon.ps_rows(shown)
        self.assertEqual((pid, ppid, pgid, command),
                         (mine[0].pid, mine[0].ppid, mine[0].pgid, kernel.command(os.getpid())))


def _reference_wrap(text, cols):
    """_wrap before Issue #625: every prefix measured from scratch."""
    pieces = []
    while mon._width(text) > cols:
        fit = 0
        while mon._width(text[:fit + 1]) <= cols:
            fit += 1
        cut = max((i + 1 for i in range(fit) if text[i] in mon.BREAK_AFTER), default=0)
        if cut < fit // 2:
            cut = fit
            while 1 < cut and text[cut] in mon.NO_LINE_START:
                cut -= 1
        pieces.append(text[:cut].rstrip())
        text = text[cut:].lstrip(" ")
    return pieces + [text]


class RepaintCostTests(unittest.TestCase):
    def test_cached_wrap_equals_the_old_algorithm_and_returns_a_fresh_list(self):
        import random
        rng = random.Random(625)
        alphabet = "ab cd，；、。）」中文字工作test "
        for _ in range(300):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 120)))
            cols = rng.randint(4, 40)   # the views never wrap narrower than this
            self.assertEqual(mon._wrap(text, cols), _reference_wrap(text, cols), (text, cols))
        lines = mon._wrap("中文字工作 test，再一次", 6)
        lines.append("mutated")
        self.assertNotIn("mutated", mon._wrap("中文字工作 test，再一次", 6))


TOKEN_HEAD = b"gh" + b"o" + b"_"   # a token-shaped fixture, never a literal token


class DirectGitHubTests(unittest.TestCase):
    """Issue #625: the running monitor reads GitHub over HTTPS with gh's own
    token (one `gh auth token`), falling back to `gh api`."""

    class Conn:
        def __init__(self, script, log):
            self.script, self.log = script, log

        def request(self, method, path, headers):
            self.log.append((method, path, headers))

        def getresponse(self):
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            status, payload = item
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            return __import__("types").SimpleNamespace(
                status=status, read=lambda n: body[:n], getheader=lambda name, default="": default)

        def close(self):
            pass

    def _source(self, script, gh):
        log = []
        source = mon.GitHubSource("o/r", runner=gh, clock=lambda: NOW, direct=True)
        source.connect = lambda: self.Conn(script, log)
        return source, log

    def test_reads_use_one_token_lookup_and_get_only(self):
        gh = FakeRun((0, TOKEN_HEAD + b"a" * 36 + b"\n"))
        source, log = self._source([(200, [comment(4, NOW - 30, "hello")]), (200, []),
                                    (200, {"runners": []})], gh)
        with patch.object(mon.shutil, "which", return_value="/opt/gh"):
            source.refresh(force=True)
        self.assertEqual([argv[1:3] for argv, _ in gh.calls], [["auth", "token"]])
        self.assertEqual([(m, p.split("?")[0]) for m, p, _ in log],
                         [("GET", "/repos/o/r/issues/comments"), ("GET", "/repos/o/r/pulls"),
                          ("GET", "/repos/o/r/actions/runners")])
        self.assertTrue(all(h["Authorization"] == "token " + TOKEN_HEAD.decode() + "a" * 36 for _, _, h in log))
        self.assertEqual(len(source.events), 1)
        self.assertNotIn(TOKEN_HEAD.decode(), source.status)

    def _after_success(self, failure, gh_answers=((0, ["from gh"]),)):
        """One good direct read, then `failure`: (reads, gh argv, direct log)."""
        gh = FakeRun((0, TOKEN_HEAD + b"b" * 36), *gh_answers)
        failures = list(failure) if isinstance(failure, list) else [failure]
        source, log = self._source([(200, []), *failures, (200, ["direct again"])], gh)
        clock = [1000.0]
        with patch.object(mon.shutil, "which", return_value="/opt/gh"), \
             patch.object(mon.time, "monotonic", lambda: clock[0]):
            reads = [source._get("repos/o/r/a"), source._get("repos/o/r/b")]
            paused = len(log)
            if len(gh.responses) == 0:
                gh.responses.append((0, ["gh while paused"]))
            reads.append(source._get("repos/o/r/c"))   # within the pause (or not paused)
            clock[0] += mon.GITHUB_DIRECT_PAUSE + 1
            gh.responses.insert(0, (0, TOKEN_HEAD + b"e" * 36))   # a dropped token is read again
            reads.append(source._get("repos/o/r/d"))
        return reads, [argv[1:5] for argv, _ in gh.calls], log, paused, source

    def test_failed_direct_reads_after_a_success_ask_gh_and_pause_direct(self):
        for failure in ((502, b""), (401, b""), (200, b"x" * (mon.GITHUB_LIMIT + 1)),
                        (200, b"not json"),
                        # the kept-alive connection is retried once, then a fresh one fails too
                        [ConnectionResetError("reset"), OSError("unreachable")]):
            with self.subTest(failure=str(failure)[:30]):
                reads, gh_calls, log, paused, source = self._after_success(failure)
                self.assertEqual(reads[:2], [[], ["from gh"]])
                self.assertIn(["api", "-X", "GET", "repos/o/r/b"], gh_calls)
                # Paused: the next read is gh's, with no direct request at all.
                self.assertEqual(reads[2], ["gh while paused"])
                self.assertIn(["api", "-X", "GET", "repos/o/r/c"], gh_calls)
                # After GITHUB_DIRECT_PAUSE the direct path is tried again.
                self.assertEqual(reads[3], ["direct again"])
                self.assertTrue(source.direct)

    def test_gh_failure_during_fallback_still_raises_for_the_refresh_backoff(self):
        gh = FakeRun((0, TOKEN_HEAD + b"b" * 36), (1, b""))
        source, _ = self._source([(200, []), (503, b"")], gh)
        with patch.object(mon.shutil, "which", return_value="/opt/gh"):
            source._get("repos/o/r/a")
            with self.assertRaisesRegex(RuntimeError, r"gh api 失敗（exit 1）"):
                source._get("repos/o/r/b")

    def test_a_404_answer_is_read_with_gh_once_without_pausing(self):
        gh = FakeRun((0, TOKEN_HEAD + b"f" * 36), (0, ["gh answer"]))
        source, log = self._source([(200, []), (404, b""), (200, ["direct"])], gh)
        with patch.object(mon.shutil, "which", return_value="/opt/gh"):
            self.assertEqual([source._get(f"repos/o/r/{n}") for n in "abc"],
                             [[], ["gh answer"], ["direct"]])
        self.assertEqual(len(log), 3)

    def test_no_connection_before_any_answer_falls_back_to_gh_for_good(self):
        gh = FakeRun((0, TOKEN_HEAD + b"d" * 36), (0, []), (0, []))
        source, _ = self._source([OSError("proxy only")], gh)
        with patch.object(mon.shutil, "which", return_value="/opt/gh"):
            self.assertEqual(source._get("repos/o/r/a"), [])
            self.assertEqual(source._get("repos/o/r/b"), [])
        self.assertFalse(source.direct)
        self.assertEqual([argv[1] for argv, _ in gh.calls], ["auth", "api", "api"])

    def test_no_token_falls_back_to_gh(self):
        gh = FakeRun((1, b""), (0, []))
        source, log = self._source([], gh)
        with patch.object(mon.shutil, "which", return_value="/opt/gh"):
            self.assertEqual(source._get("repos/o/r/a"), [])
        self.assertFalse(source.direct)
        self.assertEqual(log, [])

    def test_once_never_uses_the_direct_reader(self):
        with patch.object(mon, "run_once", return_value=0) as once:
            mon.main(["--once"])
        self.assertFalse(once.call_args[0][0].direct)

    def test_one_refresh_reads_each_run_jobs_once(self):
        runs = {"workflow_runs": [
            {"id": 7, "status": "completed", "conclusion": "success", "name": "CI",
             "head_branch": "b", "updated_at": iso(NOW - 60)},
            {"id": 8, "status": "in_progress", "name": "CI", "head_branch": "b",
             "updated_at": iso(NOW - 5)}]}
        jobs = {"jobs": [{"runner_name": "mac-1", "status": "completed", "conclusion": "success",
                          "name": "check", "completed_at": iso(NOW - 70)}]}
        fake = FakeRun((0, []), (0, []), (0, {"runners": []}), (0, runs), (0, jobs), (0, jobs), (0, jobs))
        source = mon.GitHubSource("o/r", runner=fake, clock=lambda: NOW)
        source.want_ci = True
        with patch.object(mon.shutil, "which", return_value="/opt/gh"):
            source.refresh(force=True)
        reads = [argv[4] for argv, _ in fake.calls if "/jobs" in argv[4]]
        self.assertEqual(len(reads), len(set(reads)))


class TaskScanCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        mon._task_dir_cache.clear()
        self.task = Path(self.tmp.name) / "llmtool-abc"
        self.task.mkdir()
        (self.task / "manifest.json").write_text(json.dumps({"task_id": "t-1", "branch": "b"}))
        (self.task / "dev-attempt.json").write_text(json.dumps({"model": "openai/gpt-x"}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_records_are_parsed_again_only_when_one_changed(self):
        pattern = str(Path(self.tmp.name) / "llmtool-*")
        first = mon.scan_tasks(pattern)
        self.assertEqual([(t.task_id, [s.result for s in t.stages]) for t in first], [("t-1", [""])])
        opened = []
        real_read = mon._read_json
        with patch.object(mon, "_read_json", lambda path, *a: opened.append(path) or real_read(path, *a)):
            self.assertEqual(mon.scan_tasks(pattern), first)
            self.assertEqual(opened, [])
            (self.task / "dev-result.json").write_text(json.dumps({"code": "DONE", "cleanup_confirmed": True}))
            second = mon.scan_tasks(pattern)
        self.assertEqual([(s.result, s.cleanup) for s in second[0].stages], [("DONE", "ok")])
        self.assertTrue(opened)
        mon._task_dir_cache.clear()
        self.assertEqual(mon.scan_tasks(pattern), second)   # equals a fresh read

    def test_place_is_recomputed_when_the_d6_registry_changes(self):
        mon._place_cache.clear()
        canonical = str(Path(self.tmp.name).resolve() / "repo")
        cwd = str(Path(self.tmp.name).resolve() / "elsewhere" / "wp-1")
        answers = iter([{}, {"wp-1": (cwd, "")}])
        current = [next(answers)]
        with patch.object(mon, "registered_tasks", lambda c: current[0]):
            self.assertIsNone(mon._place(cwd, canonical))
            self.assertIsNone(mon._place(cwd, canonical))   # same registry read: kept
            current[0] = next(answers)
            self.assertEqual(mon._place(cwd, canonical), ("task", "wp-1"))


class SysctlBoundsTests(unittest.TestCase):
    """Codex review #637: every sysctl length is checked against a cap before a
    buffer is allocated and against the buffer before it is parsed; any bad
    length means the monitor uses ps, without a large allocation or a crash."""

    def _kernel(self, answers):
        import ctypes
        kernel = mon._Sysctl.__new__(mon._Sysctl)
        kernel.ctypes, self.allocated, calls = ctypes, [], iter(answers)
        real = ctypes.create_string_buffer

        def buffer(size):
            self.allocated.append(size)
            return real(min(size, 1 << 16))   # never really allocate what a test claims
        kernel.ctypes = __import__("types").SimpleNamespace(create_string_buffer=buffer)
        kernel.args_buf = real(mon.PROCARGS_MAX)
        kernel._call = lambda mib, buf, size: next(calls)(buf, size)
        return kernel

    def _falls_back_to_ps(self, kernel):
        ran = []
        table = mon.ProcessTable(ps=lambda: ran.append(1) or "4000 1 4000 00:01 S claude\n",
                                 kernel=kernel, clock=lambda: 100.0 + len(ran), watched=frozenset())
        self.assertEqual(table.rows()[0][0], 4000)
        table.rows()
        self.assertEqual(len(ran), 2)   # ps both times: the kernel list was refused

    def test_oversized_size_query_allocates_nothing(self):
        kernel = self._kernel([lambda buf, size: 1 << 40])
        self.assertIsNone(kernel.processes())
        self.assertEqual(self.allocated, [])
        self._falls_back_to_ps(self._kernel([lambda buf, size: 1 << 40] * 2))

    def test_second_length_larger_than_the_buffer_is_refused(self):
        answers = [lambda buf, size: 10 * mon.KINFO_SIZE, lambda buf, size: size + mon.KINFO_SIZE]
        kernel = self._kernel(answers)
        self.assertIsNone(kernel.processes())
        self.assertTrue(all(n <= mon.KINFO_MAX_BYTES for n in self.allocated))
        self._falls_back_to_ps(self._kernel(answers * 2))

    def test_misaligned_length_is_refused(self):
        answers = [lambda buf, size: 10 * mon.KINFO_SIZE, lambda buf, size: 10 * mon.KINFO_SIZE - 1]
        self.assertIsNone(self._kernel(answers).processes())
        self._falls_back_to_ps(self._kernel(answers * 2))

    def test_size_near_the_cap_allocates_at_most_the_cap(self):
        kernel = self._kernel([lambda buf, size: mon.KINFO_MAX_BYTES, lambda buf, size: 0])
        self.assertIsNone(kernel.processes())
        self.assertEqual(self.allocated, [mon.KINFO_MAX_BYTES])

    def test_procargs_lengths_are_bounded(self):
        cap = mon.PROCARGS_MAX
        argv = (1).to_bytes(4, sys.byteorder) + b"/bin/zsh\0\0zsh\0"
        for answers in ([lambda b, s: cap + 1],                        # size query over the cap
                        [lambda b, s: 64, lambda b, s: cap + 1],       # answer longer than the buffer
                        [lambda b, s: 2]):                             # shorter than argc
            self.assertIsNone(self._kernel(answers).command(4000))

        def fill(buf, size):
            buf[:len(argv)] = argv
            return len(argv)
        self.assertEqual(self._kernel([lambda b, s: len(argv), fill]).command(4000), "zsh")


class ProcessTableFidelityTests(_TableFixture, unittest.TestCase):
    """Codex review v2 (#637): fields rebuilt between ps runs follow ps's rules."""

    def test_etime_uses_whole_seconds_like_ps(self):
        # ps: time(NULL) - p_starttime.tv_sec. Started at .70, 0.6 s later at
        # x+1.30: ps prints 00:01 (a fractional difference would say 00:00).
        self.kernel.procs.append(mon.KProc(4200, 1, 4200, int(NOW), 700_000, 2, b"sleep", os.getuid()))
        self.kernel.commands[4200] = "sleep 30"
        table = self._table("4000 1 4000 01:02:05 Ss+ claude --resume\n")
        table.rows()
        self.clock[0] += 1
        self.wall[0] = NOW + 1.3
        row, = [r for r in self._rows(table) if r[0] == 4200]
        self.assertEqual(row[3], "00:01")
        self.wall[0] = NOW + 1.99
        self.assertEqual([r[3] for r in self._rows(table) if r[0] == 4200], ["00:01"])

    def test_stat_flags_follow_the_kernel_between_ps_runs(self):
        table = self._table("4000 1 4000 01:02:05 Rs+ claude --resume\n")
        table.rows()
        self.clock[0] += 1
        self.assertEqual(self._rows(table)[1][4], "Rs+")   # ps's letter, kernel flags
        # The terminal's foreground group moves away and the process is reniced.
        self.kernel.procs[1] = self.agent._replace(tpgid=999, nice=5)
        self.clock[0] += 1
        self.assertEqual(self._rows(table)[1][4], "RNs")
        self.kernel.procs[1] = self.agent._replace(pstat=mon.SSTOP)   # SIGSTOP
        self.clock[0] += 1
        self.assertEqual(self._rows(table)[1][4], "Ts+")
        self.kernel.procs[1] = self.agent   # SIGCONT: back to the letter ps printed
        self.clock[0] += 1
        self.assertEqual(self._rows(table)[1][4], "Rs+")
        # A T that ps printed is never kept once the kernel says it runs again.
        table.known[(4000, self.agent.start, 0, b"claude")] = ("T", "claude --resume")
        self.clock[0] += 1
        self.assertEqual(self._rows(table)[1][4], "Ss+")

    def test_each_ps_flag_rule(self):
        k = kproc(1, 0, NOW)
        cases = {"<": k._replace(nice=-5), "X": k._replace(flag=mon.P_TRACED),
                 "E": k._replace(flag=mon.P_WEXIT), "V": k._replace(flag=mon.P_PPWAIT),
                 "L": k._replace(flag=mon.P_SYSTEM), "": k._replace(flag=mon.P_CONTROLT, tpgid=9)}
        self.assertEqual({flags: mon._ps_state(proc, "S")[1:] for flags, proc in cases.items()},
                         {flags: flags for flags in cases})
        self.assertEqual(mon._ps_state(k._replace(pstat=mon.SZOMB, flag=mon.P_WEXIT), "S"), "Z")

    def test_command_ps_would_escape_is_taken_from_ps_u_once(self):
        table = self._table("4000 1 4000 01:02:05 Ss+ claude --resume\n")
        table.rows()
        self.kernel.procs.append(kproc(4300, 4000, NOW - 1, b"zsh", pgid=4000))
        self.kernel.commands[4300] = None   # what command() answers for "a\nb" or "中"
        self.user_text = "4300 4000 4000 00:01 S /bin/zsh -c echo a\\012b\n"
        self.clock[0] += 1
        rows = self._rows(table)
        self.assertEqual(self.users, [{os.getuid()}])
        self.assertIn((4300, 4000, 4000, "00:01", "S", "/bin/zsh -c echo a\\012b"), rows)
        # One that ps -U did not list either is not asked for again before ps -A.
        self.kernel.procs.append(kproc(4301, 4000, NOW, b"zsh", pgid=4000))
        self.clock[0] += mon.PS_MIN_GAP + 1
        self._rows(table)
        self.clock[0] += mon.PS_MIN_GAP + 1
        self._rows(table)
        self.assertEqual(len(self.users), 2)

    def test_kernel_task_is_not_listed_as_ps_does_not(self):
        self.kernel.procs.append(kproc(0, 0, NOW - 999, b"kernel_task", uid=0))
        self.kernel.commands[0] = "kernel_task"
        table = self._table("4000 1 4000 01:02:05 Ss+ claude --resume\n")
        table.rows()
        self.clock[0] += 1
        self.assertNotIn(0, [row[0] for row in self._rows(table)])

    def test_sysctl_command_refuses_bytes_ps_would_escape(self):
        import ctypes
        kernel = mon._Sysctl.__new__(mon._Sysctl)
        kernel.args_buf = ctypes.create_string_buffer(mon.PROCARGS_MAX)
        for argv, expected in ((b"zsh\0-c\0ls", "zsh -c ls"), (b"zsh\0a\nb", None),
                               (b"zsh\0\xe4\xb8\xad", None), (b"zsh\0tab\tx", None),
                               (b"zsh\0del\x7f", None)):
            raw = (argv.count(b"\0") + 1).to_bytes(4, sys.byteorder) + b"/bin/zsh\0\0" + argv + b"\0"

            def answer(mib, buf, size, raw=raw):
                if buf is not None:
                    buf[:len(raw)] = raw
                return len(raw)
            kernel._call = answer
            self.assertEqual(kernel.command(1), expected, argv)

    def test_ps_output_with_invalid_utf8_does_not_break_the_poll(self):
        def fake(argv, **kwargs):
            self.assertEqual(kwargs.get("errors"), "replace")
            return subprocess.CompletedProcess(argv, 0, stdout="1 0 1 00:01 Ss /bin/x\ufffd\n")
        with patch.object(mon.subprocess, "run", fake):
            self.assertEqual(mon.ps_rows(mon.run_ps())[0][5], "/bin/x\ufffd")
            self.assertTrue(mon.run_ps_users({450}))


DEEP_JSON = b"[" * 10_000 + b"]" * 10_000   # json.loads: RecursionError under Python 3.13


class DeepJsonTests(unittest.TestCase):
    """Codex review v3 (#637): JSON nested too deeply is a bad answer or a
    bad record like any other, never an exception out of a refresh."""

    def test_the_payload_really_raises_recursion_error(self):
        with self.assertRaises(RecursionError):
            json.loads(DEEP_JSON)

    def test_direct_read_falls_back_to_gh_and_pauses(self):
        tests = DirectGitHubTests()
        reads, gh_calls, _, _, source = tests._after_success((200, DEEP_JSON))
        self.assertEqual(reads, [[], ["from gh"], ["gh while paused"], ["direct again"]])
        self.assertIn(["api", "-X", "GET", "repos/o/r/b"], gh_calls)

    def test_gh_answer_nested_too_deeply_is_a_failed_refresh_not_a_crash(self):
        source = mon.GitHubSource("o/r", runner=FakeRun((0, DEEP_JSON)), clock=lambda: NOW)
        with patch.object(mon.shutil, "which", return_value="/opt/gh"):
            source.refresh(force=True)
        self.assertIn("讀取失敗（ValueError）", source.status)
        self.assertEqual(source.wait, mon.GITHUB_REFRESH * 2)   # the backoff still applies

    def test_local_records_nested_too_deeply_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.json")
            with open(path, "wb") as handle:
                handle.write(DEEP_JSON)
            os.chmod(path, 0o600)
            st = os.stat(path)
            self.assertIsNone(mon._read_json(path))                     # task evidence files
            self.assertIsNone(mon._llmtool_record(path, st))            # llmtool task records
            self.assertIsNone(mon._registry_record(path, "r", st))      # D6 registry records
            tail = os.path.join(tmp, "s.jsonl")
            with open(tail, "wb") as handle:
                handle.write(b'{"type": "user"}\n' + DEEP_JSON + b"\n")
            self.assertEqual(mon._jsonl_tail(tail), [{"type": "user"}])   # session files


class PsArgvTests(unittest.TestCase):
    """Codex review v4 (#637): `ps -U` asks for the same six columns as
    `ps -A`, so ps_rows() keeps every runner-account row it prints."""

    def test_ps_u_argv_has_all_six_columns_and_rows_parse(self):
        seen = []

        def fake(argv, **kwargs):
            seen.append(list(argv))
            return subprocess.CompletedProcess(
                argv, 0, stdout="5000 1 5000 00:01 S /Users/ghrunner/actions-runner/bin/Runner.Worker\n")
        with patch.object(mon.subprocess, "run", fake):
            rows = mon.ps_rows(mon.run_ps_users({451, 450}))
            mon.run_ps()
        self.assertEqual(seen[0], ["/bin/ps", "-U", "450,451", "-o",
                                   "pid=,ppid=,pgid=,etime=,stat=,command="])
        self.assertEqual(seen[0][-1], seen[1][-1])   # the same spec as ps -A
        self.assertEqual(rows, [(5000, 1, 5000, "00:01", "S",
                                 "/Users/ghrunner/actions-runner/bin/Runner.Worker")])

    def test_real_ps_u_rows_parse_on_this_mac(self):
        if sys.platform != "darwin":
            self.skipTest("macOS ps")
        rows = mon.ps_rows(mon.run_ps_users({os.getuid()}))
        self.assertIn(os.getpid(), [row[0] for row in rows])
        mine, = [row for row in rows if row[0] == os.getpid()]
        self.assertEqual(mine[1:3], (os.getppid(), os.getpgrp()))


class DiskUsageTests(unittest.TestCase):
    @staticmethod
    def statvfs_for(used_percent):
        from types import SimpleNamespace
        return SimpleNamespace(f_blocks=100, f_bfree=100 - used_percent,
                               f_bavail=100 - used_percent, f_frsize=1024 ** 3)

    def test_home_volume_percentages_and_threshold_messages(self):
        for percent, label in ((50, ""), (91, "WARNING"), (97, "ATTENTION")):
            with self.subTest(percent=percent):
                monitor = mon.Monitor()
                fake = self.statvfs_for(percent)
                with patch.object(mon.os, "statvfs", return_value=fake) as statvfs, \
                        patch.object(mon.os.path, "expanduser", return_value="/home/test"), \
                        patch.object(mon.sys, "platform", "linux"):
                    monitor.refresh_disk_usage(now=0)
                statvfs.assert_called_once_with("/home/test")
                expected_free = fake.f_bavail * fake.f_frsize
                free_label = f"{expected_free / (1024 ** 3):.1f}G free"
                self.assertEqual(monitor.disk_usage, (percent, expected_free))
                self.assertIn(f"disk {percent}% ({free_label})", mon.disk_status(monitor.disk_usage))
                alert = mon.disk_alert(monitor.disk_usage)
                if label:
                    self.assertTrue(alert.startswith(label))
                    self.assertIn("old D6 worktrees under the workspace root", alert)
                    self.assertIn(free_label, alert)
                else:
                    self.assertEqual(alert, "")

    def test_apfs_shared_container_uses_available_space_for_percentage(self):
        monitor = mon.Monitor()
        # Darwin df-like values for this Mac: 184924200K used, 1358817 blocks
        # available, and 4096-byte blocks. APFS reports a shared container.
        block_size = 4096
        blocks = 61_202_533
        used_blocks = (184_924_200 * 1024) // block_size
        fake = (blocks, blocks - used_blocks, 1_358_817, block_size)
        with patch.object(mon, "_read_darwin_statfs", return_value=fake) as statfs, \
                patch.object(mon.os.path, "expanduser", return_value="/home/test"), \
                patch.object(mon.sys, "platform", "darwin"):
            monitor.refresh_disk_usage(now=0)
        statfs.assert_called_once_with("/home/test")
        self.assertEqual(monitor.disk_usage,
                         (98, 1_358_817 * block_size))

    @staticmethod
    def darwin_statfs_function(blocks=100, free=20, available=10, block_size=4096):
        from unittest.mock import Mock

        def fill_result(_path, pointer):
            result = pointer._obj
            result.f_blocks = blocks
            result.f_bfree = free
            result.f_bavail = available
            result.f_bsize = block_size
            return 0

        return Mock(side_effect=fill_result)

    def test_darwin_x86_64_uses_statfs64_symbol(self):
        from types import SimpleNamespace

        statfs64 = self.darwin_statfs_function()
        libc = SimpleNamespace(statfs64=statfs64)
        with patch.object(mon.ctypes, "CDLL", return_value=libc), \
                patch.object(mon.platform, "machine", return_value="x86_64"):
            result = mon._read_darwin_statfs("/home/test")
        statfs64.assert_called_once()
        self.assertEqual(result, (100, 20, 10, 4096))

    def test_darwin_arm64_uses_statfs_symbol(self):
        from types import SimpleNamespace

        statfs = self.darwin_statfs_function()
        libc = SimpleNamespace(statfs=statfs)
        with patch.object(mon.ctypes, "CDLL", return_value=libc), \
                patch.object(mon.platform, "machine", return_value="arm64"):
            result = mon._read_darwin_statfs("/home/test")
        statfs.assert_called_once()
        self.assertEqual(result, (100, 20, 10, 4096))

    def test_garbage_darwin_statfs_result_falls_back_to_statvfs(self):
        monitor = mon.Monitor()
        fallback = self.statvfs_for(91)
        with patch.object(mon, "_read_darwin_statfs", return_value=(100, 20, 101, 0)), \
                patch.object(mon.os, "statvfs", return_value=fallback) as statvfs, \
                patch.object(mon.os.path, "expanduser", return_value="/home/test"), \
                patch.object(mon.sys, "platform", "darwin"):
            monitor.refresh_disk_usage(now=0)
        statvfs.assert_called_once_with("/home/test")
        self.assertEqual(monitor.disk_usage, (91, 9 * (1024 ** 3)))

    def test_statvfs_failure_is_silent(self):
        monitor = mon.Monitor()
        with patch.object(mon.os, "statvfs", side_effect=OSError("unavailable")), \
                patch.object(mon.os.path, "expanduser", return_value="/home/test"), \
                patch.object(mon.sys, "platform", "linux"):
            monitor.refresh_disk_usage(now=0)
        self.assertIsNone(monitor.disk_usage)
        self.assertEqual(mon.disk_status(monitor.disk_usage), "")
        self.assertEqual(mon.disk_alert(monitor.disk_usage), "")

    def test_statvfs_is_limited_to_once_per_60_seconds(self):
        monitor = mon.Monitor()
        with patch.object(mon.os, "statvfs", return_value=self.statvfs_for(50)) as statvfs, \
                patch.object(mon.os.path, "expanduser", return_value="/home/test"), \
                patch.object(mon.sys, "platform", "linux"):
            monitor.refresh_disk_usage(now=0)
            monitor.refresh_disk_usage(now=59.9)
            statvfs.assert_called_once_with("/home/test")
            monitor.refresh_disk_usage(now=60)
        self.assertEqual(statvfs.call_count, 2)
