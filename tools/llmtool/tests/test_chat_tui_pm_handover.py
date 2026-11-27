"""TUI PM Task handover tests (#683): explicit consent, fail-closed, real adoption path."""

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import uuid
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import review_trust_fixture
from llmtool import (active_pm_inbox, chat_tui, chat_tui_pm, chat_tui_status,
                     chat_tui_theme, pm_runtime, worker_dispatch)
from llmtool.chat import ChatSession
from llmtool.config import resolve_config
from llmtool.errors import LLMToolError
from llmtool.providers.base import Reply
from llmtool.task_store import TaskStore
from llmtool.task_submission_cli import run_task_delegate, run_task_record_pm

import test_task_store as store_fixture

try:
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    HAVE_TOOLKIT = True
except ImportError:
    HAVE_TOOLKIT = False


CONFIG = """active_provider: first
providers:
  first:
    base_url: https://example.test
    model: test
  second:
    type: claude-cli
"""

NOW = 1800000000.0
CLEAN_PS = "1 /sbin/launchd\n2 /usr/bin/other --flag value\n"
AT = "2026-11-07T00:00:00Z"
ACCEPTANCE = "the proposal is recorded"
ARTIFACT = "diff --git a/src.py b/src.py\n--- a/src.py\n+++ b/src.py\n@@ -1 +1 @@\n-x=1\n+x=2\n"
OWNER_REF = "https://github.com/davidcatcat/davidcattoolsproject/issues/289#issuecomment-1"
_RESPONSE = json.dumps({
    "schema_version": 1,
    "summary": "Active PM plan.",
    "developer_assignment": {
        "route": "codex",
        "risk": "LOW",
        "goal": "Produce a proposal diff.",
        "acceptance": [ACCEPTANCE],
    },
})


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


class _ImmediateLoop:
    def call_soon_threadsafe(self, callback, *args):
        callback(*args)


class _FakeRuntime:
    def __init__(self, session_id, cwd, *, runtime="claude-code", account=None):
        self.pm_session_id = session_id
        self.cwd = cwd
        self.runtime = runtime
        self.account = account
        self.closed = False

    def open(self):
        return None

    def close(self, timeout=5):
        self.closed = True


@unittest.skipUnless(HAVE_TOOLKIT, "prompt-toolkit is required for ChatTUI tests")
class TUIHandoverTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve()
        self.cwd = self.home / "repo"
        self.cwd.mkdir()
        self.state = self.home / "state"
        pm_runtime._ensure_state_dir(self.state)
        self.first = self.home / "claude-one"
        self.second = self.home / "claude-two"
        self.codex_dir = self.home / "codex-account"
        for path in (self.first, self.second, self.codex_dir):
            path.mkdir(mode=0o700)
        (self.first / "sessions").mkdir(mode=0o700)
        (self.second / "sessions").mkdir(mode=0o700)
        config = self.home / ".config" / "llmtool" / "config.yaml"
        config.parent.mkdir(parents=True)
        config.write_text(
            CONFIG
            + "accounts:\n"
            + "  claude-one:\n    kind: claude\n    config_dir: " + str(self.first) + "\n"
            + "  claude-two:\n    kind: claude\n    config_dir: " + str(self.second) + "\n"
            + "  codex-pm:\n    kind: codex\n    config_dir: " + str(self.codex_dir) + "\n",
            encoding="utf-8",
        )
        self.account_env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin"}
        self.environ = {
            **self.account_env,
            "CLAUDE_CONFIG_DIR": str(self.first),
            "CODEX_HOME": str(self.codex_dir),
        }
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, self.environ).start()
        self.store = TaskStore(self.home / "tasks")
        self.session = ChatSession(resolve_config(environ=self.environ, cwd=self.cwd, home=self.home))
        self.client = Mock()
        self.client.complete.return_value = Reply("reply", {})
        patch("llmtool.chat.create_client", return_value=self.client).start()
        patch("llmtool.chat_tui_theme.settings_path",
              return_value=self.home / "tui-settings.json").start()
        patch.object(chat_tui.ChatTUI, "_background_tick", lambda ui: None).start()
        patch.object(chat_tui_status, "read_background", return_value=[]).start()
        patch.object(chat_tui_status, "read_attention", return_value=None).start()
        self.ps_output = CLEAN_PS

    def make_ui(self, *, environ=None, process_lister=None):
        lister = process_lister or (lambda: self.ps_output)
        api = chat_tui_pm.PMRuntimeAPI(
            state_root=self.state,
            environ=self.environ if environ is None else environ,
            task_store=self.store,
            clock=lambda: NOW,
            process_lister=lister,
        )
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        pipe = stack.enter_context(create_pipe_input())
        ui = chat_tui.ChatTUI(self.session, input=pipe, output=DummyOutput(), pm_api=api)

        def sync_worker(target, *args):
            ui.pm_pending = True
            target(*args, _ImmediateLoop())

        ui._pm_worker = sync_worker
        self.addCleanup(ui.close_pm)
        return ui

    def claude_transcript(self, native, config_dir=None, age=1000):
        path = active_pm_inbox.claude_session_file(
            native, str(self.cwd), {"CLAUDE_CONFIG_DIR": str(config_dir or self.first)})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        os.utime(path, (NOW - age, NOW - age))
        return path

    def codex_transcript(self, session_id):
        path = (self.codex_dir / "sessions" / "2026" / "11" / "07"
                / ("rollout-20261107-120000-" + session_id + ".jsonl"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "timestamp": AT,
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": str(self.cwd)},
        }) + "\n", encoding="utf-8")
        return path

    def bind(self, runtime="claude-code", account="claude-one"):
        return pm_runtime.create_dedicated_pm(
            str(self.cwd), runtime=runtime, account=account, state_root=self.state,
            environ=self.account_env, home=str(self.home))["native_session_id"]

    def save_thread(self, native, thread):
        pm_runtime._write_json_atomic(
            self.state, pm_runtime._runtime_state_path(self.state, "pm-" + native),
            {"codex_thread_id": thread})

    def record_task(self, native, runtime="claude-code"):
        return run_task_record_pm(
            Namespace(stdin_json=True, pm_session_id=native, pm_runtime=runtime,
                      pm_cwd=str(self.cwd)),
            store=self.store, return_record=True,
            input_stream=io.StringIO(json.dumps({
                "request": "Add a bounded test.",
                "response": _RESPONSE,
            })))

    def stopped_claude_with_task(self, account="claude-one", config_dir=None):
        native = self.bind("claude-code", account)
        self.claude_transcript(native, config_dir=config_dir, age=1000)
        task = self.record_task(native, "claude-code")
        return native, task

    def bound_claude(self, account="claude-two", config_dir=None):
        native = self.bind("claude-code", account)
        self.claude_transcript(native, config_dir=config_dir or self.second, age=1000)
        return native

    def bound_codex(self, account="codex-pm"):
        native = self.bind("codex", account)
        thread = str(uuid.uuid4())
        self.save_thread(native, thread)
        self.codex_transcript(thread)
        return native

    def test_stopped_old_pm_offered_and_adopted_only_after_consent(self):
        old, first = self.stopped_claude_with_task()
        second = self.record_task(old)
        new = self.bound_claude(account="claude-one", config_dir=self.first)
        pm_runtime.select_binding(old, state_root=self.state)

        ui = self.make_ui()
        ui.run_pm_command("/pm use " + new)
        text = ui.conversation.text
        self.assertIn("2 open Task(s)", text)
        self.assertIn("/pm handover", text)
        self.assertNotIn("handover completed", text)
        self.assertEqual(self.store.get(first["task_id"])["pm_session_id"], "pm-" + old)
        self.assertEqual(self.store.get(second["task_id"])["pm_session_id"], "pm-" + old)
        self.assertIsNone(pm_runtime.read_session_fence(old, self.state))

        ui.run_pm_command("/pm handover")
        self.assertIn("PM Task handover completed: moved 2 open Task(s)", ui.conversation.text)
        self.assertEqual(self.store.get(first["task_id"])["pm_session_id"], "pm-" + new)
        self.assertEqual(self.store.get(second["task_id"])["pm_session_id"], "pm-" + new)
        self.assertEqual(pm_runtime.read_session_fence(old, self.state)["new_session_key"],
                         "pm-" + new)

    def test_active_old_pm_refused_with_fixed_code(self):
        old, task = self.stopped_claude_with_task()
        new = self.bound_claude(account="claude-one", config_dir=self.first)
        pm_runtime.select_binding(old, state_root=self.state)
        self.ps_output = "77 claude --resume " + old + "\n"

        ui = self.make_ui()
        ui.run_pm_command("/pm use " + new)
        self.assertIn("1 open Task(s)", ui.conversation.text)
        ui.run_pm_command("/pm handover")
        self.assertIn("PM_SESSION_ADOPTION_OLD_PM_ACTIVE", ui.conversation.text)
        self.assertNotIn("handover completed", ui.conversation.text)
        self.assertEqual(self.store.get(task["task_id"])["pm_session_id"], "pm-" + old)
        self.assertIsNone(pm_runtime.read_session_fence(old, self.state))

    def test_unknown_liveness_refused_with_fixed_code(self):
        old, task = self.stopped_claude_with_task()
        new = self.bound_claude(account="claude-one", config_dir=self.first)
        pm_runtime.select_binding(old, state_root=self.state)

        def fail_ps():
            raise OSError("ps unavailable")

        ui = self.make_ui(process_lister=fail_ps)
        ui.run_pm_command("/pm use " + new)
        ui.run_pm_command("/pm handover")
        self.assertIn("PM_SESSION_ADOPTION_LIVENESS_UNKNOWN", ui.conversation.text)
        self.assertNotIn("handover completed", ui.conversation.text)
        self.assertEqual(self.store.get(task["task_id"])["pm_session_id"], "pm-" + old)

    def test_declining_closing_or_switching_again_moves_nothing(self):
        old, task = self.stopped_claude_with_task()
        second = self.bound_claude(account="claude-one", config_dir=self.first)
        third = self.bound_claude(account="claude-one", config_dir=self.first)
        pm_runtime.select_binding(old, state_root=self.state)

        ui = self.make_ui()
        ui.run_pm_command("/pm use " + second)
        ui.run_pm_command("/pm handover decline")
        self.assertIn("declined", ui.conversation.text)
        self.assertEqual(self.store.get(task["task_id"])["pm_session_id"], "pm-" + old)
        ui.run_pm_command("/pm handover")
        self.assertIn("PM_SESSION_ADOPTION_NO_OFFER", ui.conversation.text)

        # Switching to third clears any prior offer and moves nothing on close.
        pm_runtime.select_binding(old, state_root=self.state)
        ui.run_pm_command("/pm use " + second)
        self.assertIsNotNone(ui.pm_handover_offer)
        ui.run_pm_command("/pm use " + third)
        self.assertIsNone(ui.pm_handover_offer)
        ui.close_pm()
        self.assertEqual(self.store.get(task["task_id"])["pm_session_id"], "pm-" + old)
        self.assertIsNone(pm_runtime.read_session_fence(old, self.state))

    def test_concurrent_second_attempt_leaves_one_owner_and_refuses_loser(self):
        old, task = self.stopped_claude_with_task()
        target_a = self.bound_claude(account="claude-one", config_dir=self.first)
        target_b = self.bound_claude(account="claude-one", config_dir=self.first)
        api = chat_tui_pm.PMRuntimeAPI(
            state_root=self.state, environ=self.environ, task_store=self.store,
            clock=lambda: NOW, process_lister=lambda: CLEAN_PS)
        barrier = threading.Barrier(2)
        outcomes = {}

        def adopt(target):
            barrier.wait(timeout=10)
            try:
                outcomes[target] = api.adopt_session(old, target, cwd=str(self.cwd))
            except LLMToolError as exc:
                outcomes[target] = chat_tui_pm.adoption_error_code(exc)

        threads = [threading.Thread(target=adopt, args=(t,), daemon=True)
                   for t in (target_a, target_b)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        (winner,) = [t for t, out in outcomes.items() if type(out) is dict]
        (loser,) = {target_a, target_b} - {winner}
        self.assertIn(outcomes[loser], ("PM_SESSION_ADOPTION_OLD_PM_ACTIVE",
                                        "PM_SESSION_ADOPTION_FENCED_OTHER_TARGET"))
        record = self.store.get(task["task_id"])
        self.assertEqual(record["pm_session_id"], "pm-" + winner)
        self.assertEqual(len(record["pm_session_adoptions"]), 1)

    def test_restarted_old_session_is_refused_after_handover(self):
        old, _task = self.stopped_claude_with_task()
        new = self.bound_claude(account="claude-one", config_dir=self.first)
        pm_runtime.select_binding(old, state_root=self.state)

        ui = self.make_ui()
        ui.run_pm_command("/pm use " + new)
        ui.run_pm_command("/pm handover")
        self.assertIn("PM Task handover completed", ui.conversation.text)

        runtime = pm_runtime.PMRuntime(
            old, cwd=str(self.cwd), account="claude-one", runner=self.fail,
            state_root=self.state, environ=self.account_env)
        with self.assertRaises(LLMToolError) as caught:
            runtime.open()
        self.assertEqual(caught.exception.pm_runtime_code, "PM_SESSION_TRANSFERRED")

    def test_different_account_and_cross_runtime_combinations(self):
        # Claude (claude-one) -> Claude (claude-two)
        old, task = self.stopped_claude_with_task(account="claude-one", config_dir=self.first)
        new_claude = self.bound_claude(account="claude-two", config_dir=self.second)
        new_codex = self.bound_codex()
        final_claude = self.bound_claude(account="claude-one", config_dir=self.first)
        pm_runtime.select_binding(old, state_root=self.state)

        ui = self.make_ui(environ=self.account_env)
        ui.run_pm_command("/pm use " + new_claude)
        ui.run_pm_command("/pm handover")
        self.assertIn("PM Task handover completed", ui.conversation.text)
        self.assertEqual(self.store.get(task["task_id"])["pm_session_adoptions"][-1]["new_account"],
                         "claude-two")

        # Claude (claude-two) -> Codex (codex-pm)
        before = len(ui.conversation.text)
        ui.run_pm_command("/pm use " + new_codex)
        ui.run_pm_command("/pm handover")
        self.assertIn("PM Task handover completed", ui.conversation.text[before:])
        record = self.store.get(task["task_id"])
        self.assertEqual(record["pm_session_id"], "pm-" + new_codex)
        self.assertEqual(record["pm_provenance"]["pm_runtime"], "codex")
        self.assertEqual(record["pm_session_adoptions"][-1]["new_account"], "codex-pm")

        # Codex -> Claude
        before = len(ui.conversation.text)
        ui.run_pm_command("/pm use " + final_claude)
        ui.run_pm_command("/pm handover")
        self.assertIn("PM Task handover completed", ui.conversation.text[before:])
        self.assertEqual(self.store.get(task["task_id"])["pm_session_id"], "pm-" + final_claude)

    def test_refused_codex_combination_without_thread_is_shown_with_code(self):
        old, task = self.stopped_claude_with_task()
        codex_no_thread = self.bind("codex", "codex-pm")
        pm_runtime.select_binding(old, state_root=self.state)

        ui = self.make_ui(environ=self.account_env)
        ui.run_pm_command("/pm use " + codex_no_thread)
        ui.run_pm_command("/pm handover")
        self.assertIn("PM_SESSION_ADOPTION_NEW_SESSION_INVALID", ui.conversation.text)
        self.assertNotIn("handover completed", ui.conversation.text)
        self.assertEqual(self.store.get(task["task_id"])["pm_session_id"], "pm-" + old)

    def test_running_worker_task_moves_without_second_dispatch(self):
        old, task = self.stopped_claude_with_task()
        pending = store_fixture.TaskStoreTests._developer_assignment(provider="codex")
        dispatching = {
            **pending,
            "state": "DEVELOPER_DISPATCHING",
            "provider_type": "codex-cli",
            "model": "gpt-6-luna",
        }
        with patch.object(worker_dispatch, "authorize_task_store_change", return_value=None):
            self.store.compare_and_update(task["task_id"], expected={"developer": None},
                                          developer=pending)
            developer = self.store.update(task["task_id"], developer=dispatching)["developer"]
        new = self.bound_claude(account="claude-one", config_dir=self.first)
        pm_runtime.select_binding(old, state_root=self.state)

        ui = self.make_ui()
        ui.run_pm_command("/pm use " + new)
        ui.run_pm_command("/pm handover")
        self.assertIn("PM Task handover completed", ui.conversation.text)
        moved = self.store.get(task["task_id"])
        self.assertEqual(moved["pm_session_id"], "pm-" + new)
        self.assertEqual(moved["developer"], developer)

        args = Namespace(
            task_id=task["task_id"], provider="codex", mode="proposal",
            background=False, acceptance=[ACCEPTANCE], context_stdin=True,
            selection_source="PM",
        )
        with self.assertRaisesRegex(LLMToolError, "duplicate dispatch refused"):
            run_task_delegate(args, store=self.store)

        responded = {
            **developer,
            "state": "DEVELOPER_RESPONDED",
            "artifact": ARTIFACT,
            "artifact_sha256": hashlib.sha256(ARTIFACT.encode()).hexdigest(),
        }
        with patch.object(worker_dispatch, "authorize_task_store_change", return_value=None):
            self.store.update(task["task_id"], developer=responded)
        event_id = uuid.uuid4().hex
        self.store.record_completion_event(
            task["task_id"], event_id=event_id, run_id=moved["run_id"],
            session_id=moved["developer"]["session_id"], source="DIRECT_D6_DEVELOPER",
            source_sha="a" * 40, sequence=0, payload_sha256="b" * 64)
        self.assertEqual(active_pm_inbox.read_inbox(self.store, "pm-" + old)["events"], [])
        (event,) = active_pm_inbox.read_inbox(self.store, "pm-" + new)["events"]
        self.assertEqual(event["event_id"], event_id)

    def test_partial_skip_is_never_reported_as_success(self):
        old, first = self.stopped_claude_with_task()
        second = self.record_task(old)
        new = self.bound_claude(account="claude-one", config_dir=self.first)
        pm_runtime.select_binding(old, state_root=self.state)

        ui = self.make_ui()
        ui.run_pm_command("/pm use " + new)
        with patch.object(ui.pm_api, "adopt_session",
                          return_value={"moved": [first["task_id"]],
                                        "skipped": [second["task_id"]]}):
            ui.run_pm_command("/pm handover")
        self.assertIn("PM_SESSION_ADOPTION_PARTIAL_SKIP", ui.conversation.text)
        self.assertNotIn("handover completed", ui.conversation.text)
        self.assertIsNotNone(ui.pm_handover_offer)

    def test_exception_after_first_move_reports_incomplete_and_resumes(self):
        old, first = self.stopped_claude_with_task()
        second = self.record_task(old)
        new = self.bound_claude(account="claude-one", config_dir=self.first)
        pm_runtime.select_binding(old, state_root=self.state)

        ui = self.make_ui()
        ui.run_pm_command("/pm use " + new)

        real_adopt = self.store.adopt_pm_session
        calls = []

        def fail_on_second(task_id, **kwargs):
            calls.append(task_id)
            if len(calls) == 1:
                return real_adopt(task_id, **kwargs)
            exc = LLMToolError("simulated write failure")
            exc.task_store_error_code = "TASK_STORE_WRITE_FAILED"
            raise exc

        with patch.object(self.store, "adopt_pm_session", side_effect=fail_on_second):
            ui.run_pm_command("/pm handover")

        text = ui.conversation.text
        self.assertIn("PM Task handover incomplete (TASK_STORE_WRITE_FAILED)", text)
        self.assertIn("moved 1, 1 open Task(s) remain", text)
        self.assertNotIn("handover completed", text)
        self.assertNotIn("no Tasks moved", text)
        moved_id = calls[0]
        remaining_id = ({first["task_id"], second["task_id"]} - {moved_id}).pop()
        self.assertEqual(self.store.get(moved_id)["pm_session_id"], "pm-" + new)
        self.assertEqual(self.store.get(remaining_id)["pm_session_id"], "pm-" + old)
        self.assertIsNotNone(ui.pm_handover_offer)
        self.assertEqual(ui.pm_handover_offer["count"], 1)

        before = len(ui.conversation.text)
        ui.run_pm_command("/pm handover")
        self.assertIn("PM Task handover completed: moved 1 open Task(s)",
                      ui.conversation.text[before:])
        self.assertEqual(self.store.get(remaining_id)["pm_session_id"], "pm-" + new)
        self.assertIsNone(ui.pm_handover_offer)

    def test_connect_switch_offers_handover_without_claiming_task_handover(self):
        old, task = self.stopped_claude_with_task()
        new = self.bound_claude(account="claude-one", config_dir=self.first)
        pm_runtime.select_binding(old, state_root=self.state)

        ui = self.make_ui()
        with patch.object(ui.pm_api, "runtime",
                          side_effect=lambda sid, cwd: _FakeRuntime(sid, cwd)):
            ui.run_pm_command("/pm connect " + new + " " + str(self.cwd))
        self.assertIn("PM mode:", ui.conversation.text)
        self.assertIn("1 open Task(s)", ui.conversation.text)
        self.assertNotIn("handover completed", ui.conversation.text)
        self.assertEqual(self.store.get(task["task_id"])["pm_session_id"], "pm-" + old)

    def test_pm_use_refused_while_handover_pending_and_stale_finish_does_not_clobber(self):
        old, task = self.stopped_claude_with_task()
        second = self.bound_claude(account="claude-one", config_dir=self.first)
        third = self.bound_claude(account="claude-one", config_dir=self.first)
        pm_runtime.select_binding(old, state_root=self.state)

        ui = self.make_ui()
        ui.run_pm_command("/pm use " + second)
        self.assertIsNotNone(ui.pm_handover_offer)

        deferred = []

        def hold_worker(target, *args):
            ui.pm_pending = True
            deferred.append((target, args))

        ui._pm_worker = hold_worker
        ui.run_pm_command("/pm handover")
        self.assertTrue(ui.pm_pending)

        before = len(ui.conversation.text)
        ui.run_pm_command("/pm use " + third)
        self.assertIn("in progress", ui.conversation.text[before:])
        self.assertEqual(ui.pm_api.current_binding()["native_session_id"], second)
        self.assertEqual(ui.pm_handover_offer["new_session"], second)

        target, args = deferred[0]
        target(*args, _ImmediateLoop())
        self.assertFalse(ui.pm_pending)
        self.assertEqual(self.store.get(task["task_id"])["pm_session_id"], "pm-" + second)
        self.assertIsNone(ui.pm_handover_offer)

        # A stale handover completion must not clobber a newer offer.
        new_offer = {"old_session": second, "new_session": third,
                     "cwd": str(self.cwd), "count": 1}
        ui.pm_handover_offer = dict(new_offer)
        ui.finish_pm_handover({
            "offer": {"old_session": old, "new_session": second, "cwd": str(self.cwd), "count": 1},
            "result": {"moved": [task["task_id"]], "skipped": []},
        })
        self.assertEqual(ui.pm_handover_offer, new_offer)

    def test_consecutive_selection_after_disconnect_offers_handover_for_prior_selected_session(self):
        first = self.bound_claude(account="claude-one", config_dir=self.first)
        second, task = self.stopped_claude_with_task()
        third = self.bound_claude(account="claude-one", config_dir=self.first)
        pm_runtime.select_binding(first, state_root=self.state)

        ui = self.make_ui()
        with patch.object(ui.pm_api, "runtime",
                          side_effect=lambda sid, cwd: _FakeRuntime(sid, cwd)):
            ui.run_pm_command("/pm connect " + first + " " + str(self.cwd))
            ui.run_pm_command("/pm disconnect")
            ui.run_pm_command("/pm use " + second)
            before = len(ui.conversation.text)
            ui.run_pm_command("/pm connect " + third + " " + str(self.cwd))

        self.assertIn("1 open Task(s)", ui.conversation.text[before:])
        self.assertIsNotNone(ui.pm_handover_offer)
        self.assertEqual(ui.pm_handover_offer["old_session"], second)
        self.assertEqual(ui.pm_handover_offer["new_session"], third)

        ui.run_pm_command("/pm handover")
        self.assertEqual(self.store.get(task["task_id"])["pm_session_id"], "pm-" + third)


if __name__ == "__main__":
    unittest.main()
