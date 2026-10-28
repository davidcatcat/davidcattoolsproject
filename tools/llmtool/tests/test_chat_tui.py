"""A3.5 UX-1 full-screen chat: real widget interaction offline; no live provider."""
import asyncio
import copy
import io
import os
import signal
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmtool import chat_tui, chat_tui_pm
from llmtool.chat import ChatSession
from llmtool.config import resolve_config
from llmtool.providers.base import Reply
from llmtool.providers.cli_lifecycle import tui_cli_signal_scope

try:
    from prompt_toolkit.application.current import set_app
    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from prompt_toolkit.key_binding.key_processor import KeyPress
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.utils import get_cwidth
    HAVE_TOOLKIT = True
except ImportError:  # Optional extra; fallback tests below still run.
    HAVE_TOOLKIT = False


CONFIG = '''active_provider: first
providers:
  first:
    base_url: https://example.test
    model: test
    api_key: PRIVATE_KEY
  second:
    type: claude-cli
'''


def core_view_json(task_id, **fields):
    """One line shaped like the Task view Core's task submit/show print."""
    import json
    view = {'schema_version': 2, 'task_id': task_id, 'run_id': 'f' * 32,
            'pm_session_id': 'pm-' + 'e' * 32, 'pm_provider_profile': 'codex-pm',
            'pm_provider_type': 'openai-compatible', 'pm_model': None,
            'backend_session_semantics': 'NO_NATIVE_SESSION_ID_EXPOSED',
            'native_session_id': None, 'state': 'PM_RESPONDED',
            'created_at': '2026-10-28T00:00:00Z', 'updated_at': '2026-10-28T00:00:00Z',
            'task_contract': {}, 'task_binding': {}, 'request': 'r', 'request_sha256': None,
            'response': None, 'usage': None, 'error_code': None, 'record_sha256': '0' * 64,
            'usage_status': 'UNKNOWN', 'developer': None, 'pm_assignment': None}
    view.update(fields)
    return json.dumps(view, sort_keys=True)


class TTY(io.StringIO):
    def isatty(self):
        return True


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp_root = Path(temp.name)
        home = Path(temp.name)
        path = home / '.config/llmtool/config.yaml'
        path.parent.mkdir(parents=True)
        path.write_text(CONFIG)
        self.session = ChatSession(resolve_config(environ={}, cwd=home, home=home))
        self.client = Mock()
        self.client.complete.return_value = Reply('reply', {})
        self.factory = patch('llmtool.chat.create_client', return_value=self.client).start()
        self.addCleanup(patch.stopall)


class FallbackTests(Base):
    """Fallback is chosen before any UI object exists; never after a UI failure."""

    def run_tui(self, stdin, stdout, lines=('/exit',)):
        err = io.StringIO()
        with patch('builtins.input', side_effect=list(lines) + [EOFError()]), \
                patch.object(chat_tui, 'ChatTUI', side_effect=AssertionError('UI must not start')) as ui:
            status = chat_tui.run_chat_tui(self.session, stdin, stdout, err)
        ui.assert_not_called()
        return status, stdout.getvalue(), err.getvalue()

    def test_non_tty_falls_back_to_line_chat(self):
        for stdin, stdout in ((io.StringIO(), TTY()), (TTY(), io.StringIO()), (io.StringIO(), io.StringIO())):
            with self.subTest(stdin=type(stdin).__name__, stdout=type(stdout).__name__):
                self.session.active = True
                status, out, err = self.run_tui(stdin, stdout)
                self.assertEqual(status, 0)
                self.assertIn('LLMTool interactive chat', out)
                self.assertIn('stdin/stdout is not a terminal; using line chat', err)

    def test_missing_dependency_falls_back_to_line_chat(self):
        with patch.dict(sys.modules, {'prompt_toolkit': None}):
            self.assertIsNone(chat_tui._load_toolkit())
            status, out, err = self.run_tui(TTY(), TTY(), ['question', '/exit'])
        self.assertEqual(status, 0)
        self.assertIn('prompt-toolkit 3.x is not installed', err)
        self.assertIn('reply', out)
        self.assertEqual(len(self.session.history), 2)

    def test_partial_install_falls_back_before_ui(self):
        with patch.dict(sys.modules, {'prompt_toolkit.widgets': None}):
            status, out, err = self.run_tui(TTY(), TTY())
        self.assertEqual(status, 0)
        self.assertIn('not installed', err)

    def test_wrong_major_version_falls_back(self):
        fake = Mock(__version__='2.0.10')
        with patch.dict(sys.modules, {'prompt_toolkit': fake}):
            self.assertIsNone(chat_tui._load_toolkit())

    def test_ui_failure_does_not_enter_repl(self):
        err = io.StringIO()
        with patch.object(chat_tui, 'tui_unavailable_reason', return_value=None), \
                patch.object(chat_tui, 'ChatTUI') as ui, \
                patch.object(chat_tui, 'run_chat', side_effect=AssertionError('no REPL replay')) as repl:
            ui.return_value.run.side_effect = RuntimeError('PRIVATE terminal detail')
            status = chat_tui.run_chat_tui(self.session, TTY(), TTY(), err)
        self.assertEqual(status, 1)
        repl.assert_not_called()
        self.assertIn('chat terminal UI failed; details omitted', err.getvalue())
        self.assertNotIn('PRIVATE', err.getvalue())
        self.assertFalse(self.session.active)
        self.factory.assert_not_called()


@unittest.skipUnless(HAVE_TOOLKIT, 'prompt-toolkit optional extra not installed')
class WidgetTests(Base):
    """Drive the real Application through a pipe input and a dummy output."""

    def drive(self, steps, size=None):
        output = DummyOutput()
        if size is not None:
            output.get_size = lambda: size[0]
        with create_pipe_input() as pipe:
            ui = chat_tui.ChatTUI(self.session, input=pipe, output=output)
            self.ui = ui

            async def main():
                task = asyncio.ensure_future(ui.app.run_async(handle_sigint=False))
                await asyncio.sleep(0.05)
                for step in steps:
                    if isinstance(step, str):
                        pipe.send_text(step)
                        await asyncio.sleep(0.05)
                    else:
                        result = step(ui)
                        if asyncio.iscoroutine(result):
                            await result
                if not task.done():
                    pipe.send_text('\x03')
                return await asyncio.wait_for(task, 5)

            with tui_cli_signal_scope(ui._handle_signal):
                return ui, asyncio.run(main())

    @staticmethod
    async def idle(ui):
        for _ in range(200):
            if not ui.busy:
                return
            await asyncio.sleep(0.01)
        raise AssertionError('request did not finish')

    def test_starts_without_provider_call_and_exits_cleanly(self):
        ui, result = self.drive([])
        self.assertEqual(result, 0)
        self.assertIn('LLMTool interactive chat (full-screen)', ui.conversation.text)
        self.assertIn('provider: first', ui.conversation.text)
        self.factory.assert_not_called()

    def test_fixed_bottom_layout_fills_80x24_and_99x36(self):
        for rows, columns in ((24, 80), (36, 99)):
            with self.subTest(rows=rows, columns=columns):
                measurements = []

                def inspect_live_layout(ui):
                    measurements.append((ui.conversation.window.render_info.window_height,
                                         ui.rule_window.render_info.window_height,
                                         ui.input.window.render_info.window_height,
                                         ui.status_window.render_info.window_height))

                ui, result = self.drive([inspect_live_layout],
                                        size=(Size(rows=rows, columns=columns),))
                self.assertEqual(result, 0)
                conversation, rule, input_height, status = measurements[0]
                self.assertEqual(status, 1)
                self.assertEqual(rule, 1)
                self.assertLessEqual(input_height, 4)
                self.assertEqual(conversation, rows - 3)
                self.assertEqual(conversation + rule + input_height + status, rows)

    def test_multiline_input_is_capped_and_footer_stays_single_line(self):
        ui, _ = self.drive(['one\ntwo\nthree\nfour\nfive\nsix'],
                           size=(Size(rows=36, columns=99),))
        self.assertLessEqual(ui.input.window.render_info.window_height, 4)
        self.assertEqual(ui.status_window.render_info.window_height, 1)
        status = ui.status_text()
        self.assertNotIn('\n', status)
        self.assertLessEqual(get_cwidth(status), 99)

    def test_status_footer_is_compact_and_single_line_at_80_columns(self):
        self.session.config = {
            'active_provider': 'a-very-long-profile-name',
            'providers': {'a-very-long-profile-name': {
                'type': 'openai-compatible', 'model': 'a-very-long-model-name',
                'base_url': 'https://example.test', 'api_key': 'PRIVATE_KEY',
            }},
        }
        ui, _ = self.drive([], size=(Size(rows=24, columns=80),))
        status = ui.status_text()
        self.assertNotIn('\n', status)
        self.assertLessEqual(get_cwidth(status), 80)
        self.assertEqual(ui.status_window.render_info.window_height, 1)

    def test_ctrl_d_exits_only_with_empty_input(self):
        ui, result = self.drive(['abc', '\x04', lambda ui: self.assertFalse(ui.app.is_done),
                                 lambda ui: ui.input.buffer.reset(), '\x04'])
        self.assertEqual(result, 0)
        self.factory.assert_not_called()

    def test_enter_sends_and_renders_reply_with_status(self):
        self.client.complete.return_value = Reply('hello 測試 \U0001f642',
                                                  {'prompt_tokens': 2, 'completion_tokens': 3, 'total_tokens': 5})
        ui, result = self.drive(['問題 \U0001f642\r', self.idle,
                                 lambda ui: self.assertIn('t:2+3=5', ui.status_text())])
        self.assertEqual(result, 0)
        self.assertEqual(self.client.complete.call_count, 1)
        self.assertEqual(self.session.history[0]['content'], '問題 \U0001f642')
        self.assertIn('assistant:\nhello 測試 \U0001f642', ui.conversation.text)
        self.assertEqual(ui.input.text, '')

    def test_codex_cli_provider_runs_from_tui_request_thread(self):
        from llmtool.providers.codex_cli import CodexCLIProvider

        executable = self.temp_root / "fake-codex"
        executable.write_text(
            "#!%s\nimport os, sys, time\nsys.stdin.read()\n"
            "open(%r, 'w').write(str(os.getpid()))\ntime.sleep(0.1)\nprint('中文回覆')\n"
            % (sys.executable, str(self.temp_root / "codex.pid")))
        executable.chmod(0o700)
        provider = {
            "type": "codex-cli", "model": "test-model", "timeout_seconds": 3,
            "base_url": None, "api_key": None, "api_key_env": None,
            "provider_options": {},
        }
        self.session.config = {
            "active_provider": "codex-pm", "providers": {"codex-pm": provider},
            "generation": {"temperature": 0, "max_tokens": 100},
        }
        self.factory.return_value = CodexCLIProvider(provider, self.session.config["generation"])
        with patch("llmtool.providers.codex_cli.shutil.which", return_value=str(executable)):
            ui, result = self.drive(["問題\r", "\x03", self.idle])
        self.assertEqual(result, 0)
        self.assertTrue(ui.quit_pending)
        self.assertIn("assistant:\n中文回覆", ui.conversation.text)
        self.assertEqual([turn["content"] for turn in self.session.history], ["問題", "中文回覆"])
        self.assertEqual(self.session.state, "idle")
        with self.assertRaises(ProcessLookupError):
            os.kill(int((self.temp_root / "codex.pid").read_text()), 0)

    @unittest.skipUnless(os.name == "posix", "SIGCHLD is POSIX-only")
    def test_tui_cli_temporarily_disables_and_restores_ignored_sigchld(self):
        from llmtool.providers.codex_cli import CodexCLIProvider

        executable = self.temp_root / "fake-codex"
        executable.write_text(
            "#!%s\nimport os, sys\nsys.stdin.read()\n"
            "open(%r, 'w').write(str(os.getpid()))\nprint('中文回覆')\n"
            % (sys.executable, str(self.temp_root / "codex.pid")))
        executable.chmod(0o700)
        provider = {
            "type": "codex-cli", "model": "test-model", "timeout_seconds": 3,
            "base_url": None, "api_key": None, "api_key_env": None,
            "provider_options": {},
        }
        self.session.config = {
            "active_provider": "codex-pm", "providers": {"codex-pm": provider},
            "generation": {"temperature": 0, "max_tokens": 100},
        }
        self.factory.return_value = CodexCLIProvider(provider, self.session.config["generation"])
        previous = signal.getsignal(signal.SIGCHLD)
        signal.signal(signal.SIGCHLD, signal.SIG_IGN)
        try:
            with patch("llmtool.providers.codex_cli.shutil.which", return_value=str(executable)):
                ui, result = self.drive(["問題\r", self.idle])
            self.assertEqual(result, 0)
            self.assertIn("assistant:\n中文回覆", ui.conversation.text)
            self.assertIs(signal.getsignal(signal.SIGCHLD), signal.SIG_IGN)
            with self.assertRaises(ProcessLookupError):
                os.kill(int((self.temp_root / "codex.pid").read_text()), 0)
        finally:
            signal.signal(signal.SIGCHLD, previous)

    def test_codex_cli_error_can_be_retried_from_tui(self):
        from llmtool.providers.codex_cli import CodexCLIProvider

        count_file = self.temp_root / "calls"
        executable = self.temp_root / "fake-codex"
        executable.write_text(
            "#!%s\nimport os, sys\nsys.stdin.read()\n"
            "count = int(open(%r).read()) if os.path.exists(%r) else 0\n"
            "open(%r, 'w').write(str(count + 1))\n"
            "if count == 0:\n sys.stderr.write('PRIVATE')\n sys.exit(7)\n"
            "print('修復後回覆')\n"
            % (sys.executable, str(count_file), str(count_file), str(count_file)))
        executable.chmod(0o700)
        provider = {
            "type": "codex-cli", "model": "test-model", "timeout_seconds": 3,
            "base_url": None, "api_key": None, "api_key_env": None,
            "provider_options": {},
        }
        self.session.config = {
            "active_provider": "codex-pm", "providers": {"codex-pm": provider},
            "generation": {"temperature": 0, "max_tokens": 100},
        }
        self.factory.return_value = CodexCLIProvider(provider, self.session.config["generation"])
        with patch("llmtool.providers.codex_cli.shutil.which", return_value=str(executable)):
            ui, result = self.drive(["失敗的問題\r", self.idle, "再次送出\r", self.idle])
        self.assertEqual(result, 0)
        self.assertIn("Codex CLI exited with status 7", ui.conversation.text)
        self.assertNotIn("PRIVATE", ui.conversation.text)
        self.assertIn("assistant:\n修復後回覆", ui.conversation.text)
        self.assertEqual(count_file.read_text(), "2")
        self.assertEqual([turn["content"] for turn in self.session.history], ["再次送出", "修復後回覆"])
        self.assertEqual(self.session.state, "idle")

    @unittest.skipUnless(os.name == "posix", "requires POSIX process signal routing")
    def test_sigterm_and_sighup_clean_tui_owned_cli_child_before_exit(self):
        from llmtool.providers.codex_cli import CodexCLIProvider

        pid_file = self.temp_root / "codex.pid"
        executable = self.temp_root / "fake-codex"
        executable.write_text(
            "#!%s\nimport os, sys, time\nsys.stdin.read()\n"
            "open(%r, 'w').write(str(os.getpid()))\ntime.sleep(10)\nprint('late')\n"
            % (sys.executable, str(pid_file)))
        executable.chmod(0o700)
        provider = {
            "type": "codex-cli", "model": "test-model", "timeout_seconds": 20,
            "base_url": None, "api_key": None, "api_key_env": None,
            "provider_options": {},
        }
        self.session.config = {
            "active_provider": "codex-pm", "providers": {"codex-pm": provider},
            "generation": {"temperature": 0, "max_tokens": 100},
        }
        self.factory.return_value = CodexCLIProvider(provider, self.session.config["generation"])

        with patch("llmtool.providers.codex_cli.shutil.which", return_value=str(executable)):
            for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                with self.subTest(signal=signal.Signals(signum).name):
                    pid_file.unlink(missing_ok=True)

                    async def send_signal_when_started(ui):
                        for _ in range(200):
                            if pid_file.exists():
                                break
                            await asyncio.sleep(0.01)
                        self.assertTrue(pid_file.exists(), "Codex child did not start")
                        os.kill(os.getpid(), signum)

                    ui, result = self.drive(["問題\r", send_signal_when_started, self.idle])
                    self.assertEqual(result, 0)
                    self.assertTrue(ui.quit_pending)
                    self.assertEqual(self.session.state, "error")
                    with self.assertRaises(ProcessLookupError):
                        os.kill(int(pid_file.read_text()), 0)

    @unittest.skipUnless(os.name == "posix", "requires POSIX process signal routing")
    def test_signal_before_cli_lifecycle_registration_prevents_spawn(self):
        from llmtool.providers.codex_cli import CodexCLIProvider

        entered = threading.Event()
        release = threading.Event()
        executable = self.temp_root / "fake-codex"
        executable.write_text("#!%s\nimport sys\nsys.stdin.read()\nprint('should not run')\n" % sys.executable)
        executable.chmod(0o700)
        provider = {
            "type": "codex-cli", "model": "test-model", "timeout_seconds": 3,
            "base_url": None, "api_key": None, "api_key_env": None,
            "provider_options": {},
        }
        self.session.config = {
            "active_provider": "codex-pm", "providers": {"codex-pm": provider},
            "generation": {"temperature": 0, "max_tokens": 100},
        }
        self.factory.return_value = CodexCLIProvider(provider, self.session.config["generation"])

        def pause_before_lifecycle(_provider):
            entered.set()
            self.assertTrue(release.wait(3), "test did not release provider validation")

        async def signal_before_registration(ui):
            for _ in range(200):
                if entered.is_set():
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(entered.is_set(), "provider did not reach pre-lifecycle seam")
            os.kill(os.getpid(), signal.SIGTERM)
            release.set()

        with patch.object(CodexCLIProvider, "validate_ready", side_effect=pause_before_lifecycle), \
                patch("llmtool.providers.codex_cli.shutil.which", return_value=str(executable)), \
                patch("llmtool.providers.codex_cli.subprocess.Popen") as spawn:
            ui, result = self.drive(["問題\r", signal_before_registration, self.idle])
        self.assertEqual(result, 0)
        self.assertTrue(ui.quit_pending)
        spawn.assert_not_called()

    def test_multiline_ctrl_j_and_bracketed_paste(self):
        ui, _ = self.drive(['line one\nline two\r', self.idle,
                            '\x1b[200~pasted 1\rpasted 2\x1b[201~',
                            lambda ui: self.assertEqual(ui.input.text, 'pasted 1\npasted 2'),
                            lambda ui: self.assertEqual(self.client.complete.call_count, 1),
                            '\r', self.idle])
        sent = [call.args[0][-1]['content'] for call in self.client.complete.call_args_list]
        self.assertEqual(sent, ['line one\nline two', 'pasted 1\npasted 2'])

    def test_input_history_recall_with_up(self):
        ui, _ = self.drive(['/status\r', '/help\r', '\x1b[A',
                            lambda ui: self.assertEqual(ui.input.text, '/help'),
                            '\x1b[A', lambda ui: self.assertEqual(ui.input.text, '/status')])
        self.factory.assert_not_called()

    def test_slash_picker_lists_only_existing_commands(self):
        labels = [c[0] for c in chat_tui.SLASH_COMMANDS]
        self.assertEqual(labels, ['/help', '/status', '/provider', '/skill', '/clear', '/history',
                                  '/exit', '/quit', '/task submit', '/task show', '/task mode', '/task list', '/task events',
                                  '/pm connect', '/pm new', '/pm disconnect', '/pm history'])
        self.assertEqual([c[0] for c in chat_tui.slash_matches('/s')][:2], ['/status', '/skill'])
        self.assertIn('/task show', [c[0] for c in chat_tui.slash_matches('/show')])
        self.assertEqual(chat_tui.slash_matches('hello'), [])
        self.assertEqual(chat_tui.slash_matches('/nope'), [])
        self.assertEqual(chat_tui.slash_matches('/s\nx'), [])

    def test_picker_completes_draft_without_executing(self):
        def open_menu(ui):
            self.assertEqual([c[0] for c in ui.menu_items], ['/status', '/skill'])
        ui, _ = self.drive(['/s', open_menu, '\x1b[B', '\r',
                            lambda ui: self.assertEqual(ui.input.text, '/skill '),
                            lambda ui: self.assertEqual(ui.menu_items, []),
                            lambda ui: self.assertNotIn('> /skill', ui.conversation.text)])
        self.factory.assert_not_called()
        self.assertNotIn('> /skill', ui.conversation.text)

    def test_picker_task_completion(self):
        ui, _ = self.drive(['/task su', '\r',
                            lambda ui: self.assertEqual(ui.input.text, '/task submit --provider '),
                            lambda ui: self.assertNotIn('usage:', ui.conversation.text)])

    def test_picker_lone_escape_closes_on_key_timeout_flush(self):
        """No wall-clock wait: feed Esc, then the same flush the timeoutlen timer sends."""
        # Private name, imported here so a rename fails this test instead of skipping all.
        from prompt_toolkit.key_binding.key_processor import _Flush as timeout_flush

        def lone_escape(ui):
            processor = ui.app.key_processor
            with set_app(ui.app):
                processor.feed(KeyPress(Keys.Escape, '\x1b'))
                processor.process_keys()
                # Esc may still start Esc Enter (newline), so the picker waits.
                self.assertEqual([c[0] for c in ui.menu_items], ['/help'])
                processor.feed(timeout_flush)
                processor.process_keys()
            self.assertEqual(ui.menu_items, [])
            self.assertEqual(ui.input.text, '/he')
            self.assertEqual(ui.app.timeoutlen, ui._default_timeoutlen)
        self.drive(['/he', lone_escape])
        self.factory.assert_not_called()

    def test_picker_esc_enter_inserts_newline_instead_of_closing_and_submitting(self):
        def open_menu(ui):
            self.assertEqual([c[0] for c in ui.menu_items], ['/status', '/skill'])
            # The short lone-Esc timeout applies only while the picker is open.
            self.assertEqual(ui.app.timeoutlen, chat_tui.ESC_SEQUENCE_TIMEOUT)
        ui, _ = self.drive(['/s', open_menu, '\x1b\r',
                            lambda ui: self.assertEqual(ui.input.text, '/s\n'),
                            lambda ui: self.assertEqual(ui.menu_items, [])])
        self.factory.assert_not_called()
        self.assertNotIn('> /s', ui.conversation.text)
        self.assertNotIn('unknown chat command', ui.conversation.text.lower())
        self.assertEqual(ui.app.timeoutlen, ui._default_timeoutlen)
        self.assertGreater(ui._default_timeoutlen, chat_tui.ESC_SEQUENCE_TIMEOUT)
        # Esc and Enter delivered as separate chunks within the timeout behave the same.
        ui, _ = self.drive(['/s', '\x1b', '\r',
                            lambda ui: self.assertEqual(ui.input.text, '/s\n'),
                            lambda ui: self.assertEqual(ui.menu_items, [])])
        self.factory.assert_not_called()
        self.assertNotIn('> /s', ui.conversation.text)

    def test_help_documents_the_slash_picker(self):
        self.assertIn('Esc closes the picker', chat_tui.KEY_HELP)

    def test_picker_enter_on_exact_typed_command_submits_like_no_picker(self):
        # Rule: Enter only completes a draft that differs from the chosen suggestion and
        # never runs it; a draft the user already typed in full runs as with no picker.
        def completed_not_run(ui):
            self.assertEqual(ui.input.text, '/status')
            self.assertNotIn('connectivity', ui.conversation.text)
        ui, _ = self.drive(['/s', '\r', completed_not_run, '\r', self.idle])
        self.assertIn('connectivity: not probed', ui.conversation.text)
        ui, _ = self.drive(['/status\r', '/zzz\r', 'plain chat\r', self.idle])
        self.assertIn('connectivity: not probed', ui.conversation.text)
        self.assertIn('unknown chat command', ui.conversation.text.lower())
        self.assertEqual(self.client.complete.call_count, 1)

    def test_picker_closed_and_esc_timeout_restored_on_ctrl_c_and_signals(self):
        def menu_open(ui):
            self.assertTrue(ui.menu_items)
            self.assertEqual(ui.app.timeoutlen, chat_tui.ESC_SEQUENCE_TIMEOUT)
        ui, result = self.drive(['/s', menu_open, '\x03'])
        self.assertEqual(result, 0)
        self.assertEqual(ui.menu_items, [])
        self.assertEqual(ui.app.timeoutlen, ui._default_timeoutlen)
        for signum in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=signum.name):
                ui, result = self.drive(['/s', menu_open,
                                         lambda ui, signum=signum: ui._handle_signal(signum)])
                self.assertEqual(result, 0)
                self.assertEqual(ui._received_signal, signum)
                self.assertEqual(ui.menu_items, [])
                self.assertEqual(ui.app.timeoutlen, ui._default_timeoutlen)
        self.factory.assert_not_called()

    def test_picker_closed_for_recalled_history_and_multiline(self):
        ui, _ = self.drive(['/status\r', '/help\r', '\x1b[A',
                            lambda ui: self.assertEqual(ui.menu_items, []),
                            '\x1b[A', lambda ui: self.assertEqual(ui.input.text, '/status')])
        ui, _ = self.drive(['/s\n',
                            lambda ui: self.assertEqual(ui.menu_items, [])])

    @staticmethod
    def menu_rows(ui):
        return ''.join(text for _style, text in ui.menu_text()).split('\n')

    @staticmethod
    def min_layout_height(ui, size):
        # HSplit shows "Window too small" when its children's minimum heights exceed the screen.
        return sum(child.preferred_height(size.columns, size.rows).min
                   for child in ui.app.layout.container.children)

    def test_picker_shrinks_with_terminal_height_instead_of_window_too_small(self):
        for rows in (4, 5, 6, 8, 9, 12):
            with self.subTest(rows=rows):
                size = Size(rows=rows, columns=30)

                def fits(ui, size=size):
                    self.assertTrue(ui.menu_items)
                    # Never needs more rows than the screen ("Window too small" otherwise).
                    self.assertLessEqual(self.min_layout_height(ui, size), size.rows)
                    self.assertLessEqual(sum(ui.picker_layout()), max(0, size.rows - 4))
                self.drive(['/', fits], size=[size])
        # No free row: the picker is not shown, so Up/Down/Enter keep their usual meaning.
        def hidden(ui):
            self.assertEqual(ui.picker_layout(), (0, 0, 0))
        ui, _ = self.drive(['/st', hidden, '\r'], size=[Size(rows=4, columns=30)])
        self.assertIn('unknown chat command', ui.conversation.text.lower())
        self.factory.assert_not_called()

    def test_tab_closes_picker_and_restores_esc_timeout(self):
        def menu_open(ui):
            self.assertTrue(ui.menu_items)
            self.assertEqual(ui.app.timeoutlen, chat_tui.ESC_SEQUENCE_TIMEOUT)

        def closed(ui):
            self.assertFalse(ui.app.layout.has_focus(ui.input))
            self.assertEqual(ui.menu_items, [])
            self.assertEqual(ui.app.timeoutlen, ui._default_timeoutlen)
        ui, _ = self.drive(['/s', menu_open, '\t', closed, '\t',
                            lambda ui: self.assertEqual(ui.input.text, '/s')])
        self.factory.assert_not_called()

    def test_picker_scrolls_and_counts_hidden_entries(self):
        def first_page(ui):
            self.assertEqual(len(ui.menu_items), 17)
            rows = self.menu_rows(ui)
            self.assertEqual(len(rows), chat_tui.MENU_LIMIT + 1)
            self.assertIn('/help', rows[0])
            self.assertIn('12 more (0 above, 12 below)', rows[-1])

        def scrolled(ui):
            rows = self.menu_rows(ui)
            self.assertEqual(ui.menu_index, 6)
            self.assertNotIn('/help', ''.join(rows))
            self.assertIn('/exit', rows[chat_tui.MENU_LIMIT - 1])
            self.assertIn('12 more (2 above, 10 below)', rows[-1])

        def wrapped(ui):
            rows = self.menu_rows(ui)
            self.assertEqual(ui.menu_index, 16)
            self.assertIn('/pm history', rows[chat_tui.MENU_LIMIT - 1])
            self.assertIn('(12 above, 0 below)', rows[-1])
        ui, _ = self.drive(['/', first_page, '\x1b[B' * 6, scrolled, '\r',
                            lambda ui: self.assertEqual(ui.input.text, '/exit'),
                            '\x7f' * 5, '/', '\x1b[A', wrapped],
                           size=[Size(rows=24, columns=80)])
        self.assertTrue(ui.session.active)
        self.factory.assert_not_called()

    def test_picker_rows_truncate_with_ellipsis_on_narrow_terminal(self):
        def narrow(ui):
            rows = self.menu_rows(ui)
            self.assertTrue(rows)
            for row in rows:
                self.assertLessEqual(get_cwidth(row), 20)
            self.assertTrue(rows[1].endswith('…'))  # '/status' summary is cut
        self.drive(['/', narrow], size=[Size(rows=24, columns=20)])
        self.assertEqual(chat_tui.fit_width('abcdef', 4, get_cwidth), 'abc…')
        self.assertEqual(chat_tui.fit_width('中文字', 4, get_cwidth), '中…')
        self.assertEqual(chat_tui.fit_width('ok', 4, get_cwidth), 'ok')

    def test_history_matches_newest_first_unique_case_insensitive(self):
        entries = ['/status', 'Hello', '/history', 'hello', '/status', '  ']
        self.assertEqual(chat_tui.history_matches(entries, ''),
                         ['/status', 'hello', '/history', 'Hello'])
        self.assertEqual(chat_tui.history_matches(entries, 'HEL'), ['hello', 'Hello'])
        self.assertEqual(chat_tui.history_matches(entries, 'zz'), [])

    def test_ctrl_r_search_puts_entry_in_draft_without_running(self):
        def searching(ui):
            self.assertTrue(ui.searching)
            self.assertEqual(ui.search_matches, ['/help', '/history', '/status'])
            self.assertEqual(ui.input.text, 'dr')  # Draft untouched while searching.
            self.assertEqual(ui.app.timeoutlen, chat_tui.ESC_SEQUENCE_TIMEOUT)
            self.assertIn('history search:', self.menu_rows(ui)[0])

        def filtered(ui):
            self.assertEqual(ui.search_query, 'st')
            self.assertEqual(ui.search_matches, ['/history', '/status'])
            self.assertEqual(ui.input.text, 'dr')
        ui, _ = self.drive(['/status\r', '/history\r', '/help\r', 'dr', '\x12', searching,
                            'st', filtered, '\x12',  # Ctrl-R again: next older match
                            lambda ui: self.assertEqual(ui.menu_index, 1), '\r',
                            lambda ui: self.assertEqual(ui.input.text, '/status'),
                            lambda ui: self.assertFalse(ui.searching),
                            lambda ui: self.assertEqual(ui.menu_items, []),
                            lambda ui: self.assertEqual(ui.app.timeoutlen, ui._default_timeoutlen)])
        # Only the three typed /status, /history and /help ran; the recalled one did not.
        self.assertEqual(ui.conversation.text.count('connectivity: not probed'), 1)
        self.factory.assert_not_called()

    def test_ctrl_r_escape_cancels_and_keeps_draft(self):
        from prompt_toolkit.key_binding.key_processor import _Flush as timeout_flush

        def lone_escape(ui):
            processor = ui.app.key_processor
            with set_app(ui.app):
                processor.feed(KeyPress(Keys.Escape, '\x1b'))
                processor.process_keys()
                self.assertTrue(ui.searching)
                processor.feed(timeout_flush)
                processor.process_keys()
            self.assertFalse(ui.searching)
            self.assertEqual(ui.input.text, 'keep me')
            self.assertEqual(ui.app.timeoutlen, ui._default_timeoutlen)
        ui, _ = self.drive(['/status\r', 'keep me', '\x12', 'sta', '\x7f',
                            lambda ui: self.assertEqual(ui.search_query, 'st'),
                            '\x1b[200~at\x1b[201~',  # bracketed paste goes to the query
                            lambda ui: self.assertEqual(ui.search_query, 'stat'),
                            '\x15',  # Ctrl-U (kill line) cannot edit the read-only draft
                            lambda ui: self.assertEqual(ui.input.text, 'keep me'),
                            lone_escape])
        self.assertEqual(ui.input.text, 'keep me')
        self.factory.assert_not_called()

    def test_ctrl_r_no_match_and_esc_enter_still_newline(self):
        def no_match(ui):
            self.assertEqual(ui.search_matches, [])
            self.assertIn('(no match)', self.menu_rows(ui)[0])
        ui, _ = self.drive(['/status\r', 'a', '\x12', 'zzz', no_match, '\r',
                            lambda ui: self.assertFalse(ui.searching),
                            lambda ui: self.assertEqual(ui.input.text, 'a'),
                            '\x12', 'st', '\x1b\r',
                            lambda ui: self.assertFalse(ui.searching),
                            lambda ui: self.assertEqual(ui.input.text, 'a\n')])
        self.assertEqual(ui.conversation.text.count('connectivity: not probed'), 1)
        self.factory.assert_not_called()

    def test_ctrl_r_entry_display_is_terminal_safe(self):
        def shown(ui):
            rows = self.menu_rows(ui)
            self.assertNotIn('\x1b', ''.join(rows))
            self.assertIn('↵', rows[1])
        ui, _ = self.drive([lambda ui: ui.input.buffer.history.append_string('a\x1b[2Jb\nc'),
                            '\x12', shown, '\x1b\r'])
        self.factory.assert_not_called()

    @staticmethod
    async def redraw(ui):
        ui.app.invalidate()
        await asyncio.sleep(0.2)

    def test_search_hidden_by_resize_ends_and_keeps_draft_after_redraw(self):
        size = [Size(rows=24, columns=80)]

        def shrink(ui):
            self.assertTrue(ui.searching)
            size[0] = Size(rows=4, columns=80)

        def ended(ui):
            self.assertFalse(ui.searching)
            self.assertEqual(ui.input.text, 'x')
            self.assertEqual(ui.app.timeoutlen, ui._default_timeoutlen)
        ui, _ = self.drive(['/status\r', 'x', '\x12', shrink, self.redraw, ended,
                            lambda ui: size.__setitem__(0, Size(rows=24, columns=80)),
                            self.redraw, lambda ui: self.assertFalse(ui.searching)], size=size)
        self.assertEqual(ui.input.text, 'x')
        self.factory.assert_not_called()

    def test_enter_in_unseen_search_keeps_draft_and_sends_nothing(self):
        """Resize then Enter before any redraw: Enter must not swap or send the draft."""
        size = [Size(rows=24, columns=80)]

        def shrink_then_enter(ui):
            self.assertTrue(ui.searching)
            size[0] = Size(rows=4, columns=80)
            processor = ui.app.key_processor
            with set_app(ui.app):
                processor.feed(KeyPress(Keys.ControlM, '\r'))
                processor.process_keys()
            self.assertFalse(ui.searching)
            self.assertEqual(ui.input.text, 'x')
        ui, _ = self.drive(['/status\r', 'x', '\x12', shrink_then_enter], size=size)
        self.assertEqual(ui.conversation.text.count('connectivity: not probed'), 1)
        self.assertNotIn('> x', ui.conversation.text)
        self.factory.assert_not_called()

    def test_esc_timeout_follows_picker_visibility(self):
        def hidden(ui):
            self.assertTrue(ui.menu_items)
            self.assertEqual(ui.picker_layout(), (0, 0, 0))
            self.assertEqual(ui.app.timeoutlen, ui._default_timeoutlen)
        self.drive(['/s', hidden], size=[Size(rows=4, columns=30)])
        size = [Size(rows=24, columns=80)]

        def short(ui):
            self.assertEqual(ui.app.timeoutlen, chat_tui.ESC_SEQUENCE_TIMEOUT)

        def default(ui):
            self.assertTrue(ui.menu_items)
            self.assertEqual(ui.app.timeoutlen, ui._default_timeoutlen)
        self.drive(['/s', short,
                    lambda ui: size.__setitem__(0, Size(rows=4, columns=80)), self.redraw, default,
                    lambda ui: size.__setitem__(0, Size(rows=24, columns=80)), self.redraw, short],
                   size=size)
        self.factory.assert_not_called()

    def test_status_bar_drops_low_priority_fields_when_narrow(self):
        size = [Size(rows=24, columns=80)]
        widths = {}

        def measure(ui):
            ui.quit_pending = True  # A long state: 'exit pending'
            for columns in (80, 50, 40, 30, 20, 12, 8, 3):
                size[0] = Size(rows=24, columns=columns)
                widths[columns] = ui.status_text()
            ui.quit_pending = False
        self.drive([measure], size=size)
        self.assertEqual(widths[80], 'p:first | m:test | exit pending | t:UNKNOWN | F1')
        self.assertEqual(widths[40], 'p:first | m:test | exit pending')
        self.assertEqual(widths[20], 'exit pending')
        self.assertEqual(widths[8], 'exit pe…')
        for columns, status in widths.items():
            with self.subTest(columns=columns):
                self.assertNotIn('\n', status)
                self.assertLessEqual(get_cwidth(status), columns)
                # The state is the last field to go; it is never dropped while others stay.
                self.assertTrue(status.startswith(('p:', 'm:', 'ex')), status)
                if columns >= 12:
                    self.assertIn('exit pending', status)

    def test_narrow_status_bar_keeps_input_row(self):
        measurements = []

        def inspect(ui):
            measurements.append((ui.input.window.render_info.window_height,
                                 ui.status_window.render_info.window_height))
        ui, _ = self.drive(['draft', inspect], size=[Size(rows=8, columns=12)])
        self.assertEqual(measurements, [(1, 1)])
        self.assertEqual(ui.input.text, 'draft')

    def test_help_toggle_and_help_command_are_offline(self):
        def help_shown(ui):
            self.assertTrue(ui.show_help)
            self.assertTrue(ui.app.layout.has_focus(ui.input))
            self.assertIn('Ctrl-J', ui.help.text)
        ui, _ = self.drive(['\t', '\x1bOP', help_shown,
                            '\t', lambda ui: self.assertTrue(ui.app.layout.has_focus(ui.help)),
                            '\x1bOP', lambda ui: self.assertFalse(ui.show_help),
                            lambda ui: self.assertTrue(ui.app.layout.has_focus(ui.input)),
                            '/help\r'])
        self.assertIn('/provider [profile]', ui.conversation.text)
        self.assertIn('/task submit --provider PROFILE REQUEST', ui.conversation.text)
        self.factory.assert_not_called()

    def test_explicit_task_submit_uses_existing_api_off_event_loop(self):
        called = []

        def submit(args, *, stdout=None):
            called.append((args.provider, args.request, threading.current_thread()))
            stdout.write(core_view_json('a' * 32) + '\n')
            stdout.flush()
            return 0

        with patch('llmtool.task_submission_cli.run_task_submit', side_effect=submit):
            ui, _ = self.drive(['/task submit --provider codex-pm add a focused test\r', self.idle])
        self.assertEqual(called[0][0:2], ('codex-pm', ['add a focused test']))
        self.assertIsNot(called[0][2], threading.main_thread())
        self.assertIn('task intake only; Developer dispatch is not implied', ui.conversation.text)
        self.assertIn('PM_RESPONDED', ui.conversation.text)
        self.assertEqual(self.session.history, [])
        self.factory.assert_not_called()

    def test_explicit_task_show_uses_existing_lookup_api(self):
        task_id = 'b' * 32
        called = []

        def show(args, *, stdout=None):
            called.append(args.task_id)
            stdout.write(core_view_json(args.task_id, state='PM_UNAVAILABLE') + '\n')
            return 0

        with patch('llmtool.task_submission_cli.run_task_show', side_effect=show):
            ui, _ = self.drive(['/task show ' + task_id + '\r', self.idle])
        self.assertEqual(called, [task_id])
        self.assertIn('PM_UNAVAILABLE', ui.conversation.text)
        self.assertEqual(self.session.history, [])
        self.factory.assert_not_called()

    def test_task_route_requires_explicit_syntax_and_plain_text_remains_chat(self):
        with patch('llmtool.task_submission_cli.run_task_submit') as submit:
            ui, _ = self.drive(['/task submit add something\r',
                                '/task show\r', 'ordinary chat\r', self.idle])
        submit.assert_not_called()
        self.assertIn('usage: /task submit', ui.conversation.text)
        self.assertIn('usage: /task submit', ui.conversation.text)
        self.assertEqual([turn['content'] for turn in self.session.history],
                         ['ordinary chat', 'reply'])

    def test_tab_switches_focus_and_scroll_preserves_caret(self):
        long_reply = '\n'.join('row %d' % i for i in range(200))
        self.client.complete.return_value = Reply(long_reply, {})

        def scrolled_up(ui):
            self.assertLess(ui.conversation.buffer.cursor_position, len(ui.conversation.text))
            self.caret = ui.conversation.buffer.cursor_position

        def caret_kept(ui):
            self.assertEqual(ui.conversation.buffer.cursor_position, self.caret)
            self.assertIn('connectivity: not probed', ui.conversation.text.splitlines()[-1])

        ui, _ = self.drive([
            'question\r', self.idle,
            '\t', lambda ui: self.assertTrue(ui.app.layout.has_focus(ui.conversation)),
            '\x1b[5~', scrolled_up,
            '\t', lambda ui: self.assertTrue(ui.app.layout.has_focus(ui.input)),
            '/status\r', caret_kept,
            '\x1b[6~' * 60,
            lambda ui: self.assertEqual(ui.conversation.buffer.cursor_position, len(ui.conversation.text)),
        ])

    def test_single_flight_keeps_input_and_blocks_provider_switch(self):
        release = threading.Event()
        started = threading.Event()

        def complete(messages):
            started.set()
            release.wait(5)
            return Reply('first reply', {})
        self.client.complete.side_effect = complete

        async def wait_started(ui):
            for _ in range(200):
                if started.is_set():
                    return
                await asyncio.sleep(0.01)

        def check_busy(ui):
            self.assertTrue(ui.busy)
            self.assertEqual(self.session.state, 'working')
            self.assertIn('working', ui.status_text())
            self.assertEqual(ui.input.text, 'second')
            self.assertEqual(self.session.provider, 'first')
            self.assertIn('request is in progress', ui.conversation.text)

        def clear_input(ui):
            ui.input.buffer.reset()

        ui, _ = self.drive(['first\r', wait_started, 'second\r', check_busy, clear_input,
                            '/provider second\r', lambda ui: self.assertEqual(self.session.provider, 'first'),
                            lambda ui: self.assertEqual(ui.input.text, '/provider second'),
                            lambda ui: release.set(), self.idle,
                            clear_input, 'second\r', self.idle])
        self.assertEqual(self.client.complete.call_count, 2)
        self.assertEqual([t['content'] for t in self.session.history],
                         ['first', 'first reply', 'second', 'first reply'])

    def test_busy_quit_waits_honestly_for_in_flight_request(self):
        release = threading.Event()
        self.client.complete.side_effect = lambda messages: (release.wait(5), Reply('late reply', {}))[1]

        def pending(ui):
            self.assertFalse(ui.app.is_done)
            self.assertTrue(ui.quit_pending)
            self.assertIn('cannot be cancelled', ui.conversation.text)
            self.assertIn('exit pending', ui.status_text())

        async def finish(ui):
            release.set()
            await self.idle(ui)
        ui, result = self.drive(['question\r', '\x03', pending, finish])
        self.assertEqual(result, 0)
        self.assertEqual(self.session.history[-1]['content'], 'late reply')

    def test_request_failure_is_sanitized_and_recoverable(self):
        self.client.complete.side_effect = [RuntimeError('PRIVATE stderr'), Reply('ok', {})]
        ui, _ = self.drive(['bad\r', self.idle,
                            lambda ui: self.assertIn('error (unclas', ui.status_text()),
                            'good\r', self.idle])
        self.assertIn('chat request failed; history unchanged', ui.conversation.text)
        self.assertNotIn('PRIVATE', ui.conversation.text + ui.status_text())
        self.assertEqual([t['content'] for t in self.session.history], ['good', 'ok'])

    def test_untrusted_output_controls_are_escaped(self):
        self.client.complete.return_value = Reply('x\x1b]52;c;secret\x07‮', {'model': 'm\x1b'})
        ui, _ = self.drive(['q\r', self.idle])
        self.assertIn('x\\x1b]52;c;secret\\x07\\u202e', ui.conversation.text)
        self.assertNotIn('\x1b', ui.conversation.text + ui.status_text())
        self.assertIn('t:UNKNOWN', ui.status_text())

    def test_exit_command_and_clear(self):
        ui, result = self.drive(['q\r', self.idle, '/clear\r',
                                 lambda ui: self.assertEqual(ui.conversation.text, 'conversation cleared'),
                                 lambda ui: self.assertEqual(self.session.history, []),
                                 '/exit\r'])
        self.assertEqual(result, 0)
        self.assertFalse(self.session.active)

    def test_invalid_command_keeps_session_and_error_is_sanitized(self):
        ui, _ = self.drive(['/provider PRIVATE_NAME\r', '/bogus\r'])
        lines = ui.conversation.text.splitlines()
        errors = [line for line in lines if line.startswith('llmtool: ')]
        self.assertEqual(len(errors), 2)
        self.assertNotIn('PRIVATE', '\n'.join(errors))
        self.assertIn('> /provider PRIVATE_NAME', lines)
        self.assertEqual(self.session.provider, 'first')
        self.assertTrue(self.session.active)
        self.factory.assert_not_called()

    def test_render_failure_after_reply_exits_sanitized_not_repl(self):
        def install(ui):
            original = ui.append

            def broken_append(text):
                if text.startswith('assistant:'):
                    raise RuntimeError('PRIVATE render detail')
                return original(text)
            ui.append = broken_append
        ui, result = self.drive([install, 'q\r', self.idle])
        self.assertEqual(result, 1)
        self.assertEqual([t['content'] for t in self.session.history], ['q', 'reply'])

    def test_resize_rerenders_without_losing_state(self):
        size = [Size(rows=24, columns=80)]

        def resize(ui):
            size[0] = Size(rows=6, columns=20)
            ui.app._on_resize()

        def resized(ui):
            ui.app.renderer.render(ui.app, ui.app.layout)
            self.assertEqual(ui.app.renderer.output.get_size(), Size(rows=6, columns=20))
            self.assertEqual(ui.input.text, 'draft 測')
        self.client.complete.return_value = Reply('寬' * 300, {})
        ui, result = self.drive(['q\r', self.idle, 'draft 測', resize, resized], size=size)
        self.assertEqual(result, 0)
        self.assertEqual(len(self.session.history), 2)



class TaskReceiptTextTests(unittest.TestCase):
    """Receipt/detail text uses only Core view fields; gaps stay UNKNOWN/NOT_AVAILABLE."""

    def test_missing_fields_are_unknown_and_nulls_not_available(self):
        from llmtool import chat_tui_task as text
        view = {'task_id': 'a' * 32, 'state': 'PM_RESPONDED', 'pm_model': None, 'developer': None}
        receipt = text.receipt_text(0, view, 'pm')
        self.assertIn('Run ID: UNKNOWN', receipt)
        self.assertIn('model NOT_AVAILABLE', receipt)
        self.assertIn('PM provider: UNKNOWN', receipt)
        self.assertIn('已建立／待派工', receipt)
        self.assertNotIn('running', receipt.lower())
        self.assertNotIn('已建立／待派工', text.dispatch_status({'developer': {'state': 'X'}}))
        self.assertIn('UNKNOWN', text.dispatch_status({'task_id': 'a' * 32}))

    def test_only_a_core_task_view_is_parsed(self):
        from llmtool import chat_tui_task as text
        self.assertIsNone(text.parse_core_view('assistant says task_id 0123'))
        self.assertIsNone(text.parse_core_view('{"task_id":"not-an-id"}'))
        self.assertIsNone(text.parse_core_view('[1]'))
        self.assertEqual(text.parse_core_view(core_view_json('c' * 32) + '\n')['task_id'], 'c' * 32)

    def test_core_view_found_among_other_output_and_conflicts_refused(self):
        from llmtool import chat_tui_task as text
        view = core_view_json('c' * 32)
        other = core_view_json('d' * 32)
        for raw in (view + '\nllmtool: trailing note\n{"note":1}\n',
                    'leading noise\n[1]\n' + view,
                    view + '\n' + view):
            with self.subTest(raw=raw):
                self.assertEqual(text.parse_core_view(raw)['task_id'], 'c' * 32)
        self.assertIsNone(text.parse_core_view(view + '\n' + other))

    def test_json_noise_shaped_like_a_task_is_not_a_core_view(self):
        from llmtool import chat_tui_task as text
        import json
        noise = '{"task_id":"' + 'd' * 32 + '","state":"PM_RESPONDED"}'
        self.assertIsNone(text.parse_core_view(noise))
        view = core_view_json('c' * 32)
        for raw in (noise + '\n' + view, view + '\n' + noise):
            with self.subTest(raw=raw):
                self.assertEqual(text.parse_core_view(raw)['task_id'], 'c' * 32)
        good = json.loads(view)
        for broken in (dict(good, extra=1), {k: v for k, v in good.items() if k != 'run_id'},
                       dict(good, run_id=None), dict(good, state='RUNNING'),
                       dict(good, record_sha256='x'), dict(good, usage_status='SOME'),
                       dict(good, developer='x'), dict(good, schema_version='2')):
            with self.subTest(keys=sorted(set(broken) ^ set(good))):
                self.assertFalse(text.is_core_view(broken))
                self.assertIsNone(text.parse_core_view(json.dumps(broken)))

    def test_no_view_means_no_receipt(self):
        from llmtool import chat_tui_task as text
        receipt = text.receipt_text(None, None, 'pm', error='select one valid configured PM provider profile')
        self.assertIn('FAILED (no Task view returned by Core)', receipt)
        self.assertIn('Task ID: UNKNOWN', receipt)
        self.assertIn('No Task receipt exists', receipt)

    def test_unknown_error_code_has_unknown_retry_condition(self):
        from llmtool import chat_tui_task as text
        view = {'task_id': 'a' * 32, 'state': 'PM_UNAVAILABLE', 'error_code': 'SOMETHING_NEW'}
        self.assertEqual(text.retry_condition(view, 'pm'),
                         ('Core supplies no recovery hint', 'UNKNOWN for error SOMETHING_NEW'))
        self.assertIsNone(text.retry_condition({'state': 'PM_RESPONDED'}, 'pm'))
        unreachable = dict(view, error_code='NETWORK_PROVIDER_UNREACHABLE')
        self.assertIn('endpoint of PM profile pm must be reachable',
                      text.retry_condition(unreachable, 'pm')[1])
        self.assertIn('error: none', text.receipt_text(0, {'task_id': 'a' * 32, 'error_code': None}, 'pm'))


@unittest.skipUnless(HAVE_TOOLKIT, 'prompt-toolkit optional extra not installed')
class TaskModeTests(Base):
    """Task mode drives the real Core task submit/show against an isolated TaskStore."""

    drive = WidgetTests.drive
    idle = staticmethod(WidgetTests.idle)

    def setUp(self):
        super().setUp()
        # Resolved: TaskStore refuses a root reached through a symlink (/var on macOS).
        patch.dict(os.environ, {'HOME': str(self.temp_root.resolve())}).start()
        self.pm = Mock()
        self.pm.complete.return_value = Reply('PM plan: 先寫測試', {
            'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 3})
        self.pm_factory = patch('llmtool.task_submission_cli.create_client',
                                return_value=self.pm).start()

    def store_records(self):
        from llmtool.task_store import TaskStore
        store = TaskStore()
        return [store.get(task_id) for task_id in store.task_ids()]

    def test_mode_is_explicit_and_plain_text_stays_chat_until_chosen(self):
        def task_mode_on(ui):
            self.assertEqual(ui.task_profile, 'first')
            self.assertIn('TASK→PM:first', ui.status_text())
            self.assertIn('Task mode: Enter submits', ui.conversation.text)

        ui, _ = self.drive(['hello\r', self.idle,
                            lambda ui: self.assertNotIn('TASK→PM:', ui.status_text()),
                            '/task mode first\r', task_mode_on,
                            '/task mode off\r', 'again\r', self.idle])
        self.assertEqual([t['content'] for t in self.session.history],
                         ['hello', 'reply', 'again', 'reply'])
        self.pm_factory.assert_not_called()
        self.assertEqual(self.store_records(), [])
        self.assertIn('chat mode: Enter sends drafts', ui.conversation.text)

    def test_task_submit_shows_core_receipt_and_opens_core_detail(self):
        request = '請新增測試\n第二行：多行需求'
        ui, _ = self.drive(['/task mode first\r', '\x1b[200~' + request + '\x1b[201~', '\r',
                            self.idle])
        records = self.store_records()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record['request'], request)
        self.assertEqual(record['state'], 'PM_RESPONDED')
        text = ui.conversation.text
        self.assertIn('Task submit result: CREATED (llmtool Core task submit, exit 0)', text)
        self.assertIn('PM provider: first (type openai-compatible, model test)', text)
        self.assertIn('Task ID: ' + record['task_id'], text)
        self.assertIn('Run ID: ' + record['run_id'], text)
        self.assertIn('PM session: ' + record['pm_session_id'] + ' (backend NO_NATIVE_SESSION_ID_EXPOSED)', text)
        self.assertIn('it is not a PM', text)
        self.assertIn('Dispatch: 已建立／待派工', text)
        self.assertNotIn('development is running', text.lower())
        self.assertTrue(ui.show_detail)
        self.assertEqual(ui.last_task_id, record['task_id'])
        self.assertIn('Task ID: ' + record['task_id'], ui.detail.text)
        self.assertIn('Record sha256: ' + record['record_sha256'], ui.detail.text)
        self.assertIn(request, ui.detail.text)
        self.assertIn('PM plan: 先寫測試', ui.detail.text)
        self.assertEqual(self.session.history, [])
        self.factory.assert_not_called()
        self.assertEqual(self.pm.complete.call_args.args[0][-1]['content'], request)

    def test_trailing_output_after_submit_still_gives_receipt_and_detail(self):
        from llmtool import task_submission_cli
        original = task_submission_cli.run_task_submit

        def noisy_submit(args, *, stdout=None):
            stdout.write('leading notice\n')
            code = original(args, stdout=stdout)
            stdout.write('llmtool: trailing notice\n')
            return code

        with patch('llmtool.task_submission_cli.run_task_submit', side_effect=noisy_submit):
            ui, _ = self.drive(['/task mode first\r', 'noisy\r', self.idle])
        record, = self.store_records()
        self.assertIn('Task submit result: CREATED', ui.conversation.text)
        self.assertIn('Task ID: ' + record['task_id'], ui.conversation.text)
        self.assertNotIn('No Task receipt exists', ui.conversation.text)
        self.assertTrue(ui.show_detail)

    def test_large_paste_is_one_task_request(self):
        request = '\n'.join('第%d行 large paste 內容' % i for i in range(1500))
        self.assertGreater(len(request.encode('utf-8')), 40000)
        ui, _ = self.drive(['/task mode first\r', '\x1b[200~' + request + '\x1b[201~', '\r',
                            self.idle])
        records = self.store_records()
        self.assertEqual([r['request'] for r in records], [request])
        self.assertEqual(self.pm.complete.call_count, 1)

    def test_provider_unavailable_shows_core_error_and_retry_condition(self):
        ui, _ = self.drive(['/task mode missing\r', 'do work\r', self.idle])
        record, = self.store_records()
        self.assertEqual((record['state'], record['error_code']),
                         ('PM_UNAVAILABLE', 'PM_PROVIDER_NOT_CONFIGURED'))
        text = ui.conversation.text
        self.assertIn('Task submit result: FAILED (llmtool Core task submit, exit 1)', text)
        self.assertIn('Task ID: ' + record['task_id'], text)
        self.assertIn('PM state: PM_UNAVAILABLE; error: PM_PROVIDER_NOT_CONFIGURED', text)
        self.assertIn('Retry when (TUI guidance for Core error PM_PROVIDER_NOT_CONFIGURED', text)
        self.assertIn('PM profile missing must be defined', text)
        self.assertNotIn('CREATED', text)
        self.assertFalse(ui.show_detail)
        self.pm_factory.assert_not_called()

    def test_provider_failure_is_recorded_not_faked(self):
        from llmtool.errors import LLMToolError
        self.pm.complete.side_effect = LLMToolError('PRIVATE provider detail')
        ui, _ = self.drive(['/task mode first\r', 'do work\r', self.idle])
        record, = self.store_records()
        self.assertEqual(record['error_code'], 'PM_PROVIDER_UNAVAILABLE')
        self.assertIn('PM profile first must be reachable', ui.conversation.text)
        self.assertNotIn('PRIVATE', ui.conversation.text)
        self.assertNotIn('CREATED', ui.conversation.text)

    def test_invalid_profile_creates_no_task_and_shows_core_error(self):
        ui, _ = self.drive(['/task mode bad!name\r', 'do work\r', self.idle])
        self.assertEqual(self.store_records(), [])
        self.assertIn('FAILED (no Task view returned by Core)', ui.conversation.text)
        self.assertIn('select one valid configured PM provider profile', ui.conversation.text)

    def test_repeated_enter_resize_and_redraw_submit_once(self):
        release = threading.Event()
        started = threading.Event()

        def complete(messages):
            started.set()
            release.wait(5)
            return Reply('PM ok', {})
        self.pm.complete.side_effect = complete
        size = [Size(rows=24, columns=80)]

        async def wait_started(ui):
            for _ in range(200):
                if started.is_set():
                    return
                await asyncio.sleep(0.01)

        def resize_and_redraw(ui):
            size[0] = Size(rows=12, columns=40)
            ui.app._on_resize()
            ui.app.invalidate()
            ui.app.renderer.render(ui.app, ui.app.layout)

        ui, _ = self.drive(['/task mode first\r', 'one task\r', '\r\r\r', wait_started,
                            resize_and_redraw, '\r', lambda ui: self.assertTrue(ui.busy),
                            lambda ui: release.set(), self.idle, '\r\r',
                            lambda ui: self.assertTrue(ui.show_detail),
                            # Same draft again (e.g. recalled) is refused, draft kept, and the
                            # refusal is not hidden behind the open Task detail.
                            'one task\r',
                            lambda ui: self.assertEqual(ui.input.text, 'one task'),
                            lambda ui: self.assertFalse(ui.show_detail),
                            lambda ui: ui.input.buffer.reset(),
                            # The explicit command is the deliberate way to resend.
                            '/task submit --provider first one task\r', self.idle],
                           size=size)
        self.assertEqual(self.pm.complete.call_count, 2)
        self.assertEqual(len(self.store_records()), 2)
        self.assertIn('already submitted in Task mode as Task ' + ui.last_task_id,
                      ui.conversation.text)

    def test_chat_reply_is_never_task_evidence(self):
        self.client.complete.return_value = Reply(
            '{"task_id":"' + 'a' * 32 + '","run_id":"' + 'b' * 32 + '","state":"PM_RESPONDED",'
            '"developer":null}', {})
        ui, _ = self.drive(['make a task\r', self.idle, '\x1bOQ'])
        self.assertIsNone(ui.last_task_id)
        self.assertFalse(ui.show_detail)
        self.assertEqual(ui.detail.text, '')
        self.assertNotIn('Task submit result', ui.conversation.text)
        self.assertIn('no Task detail loaded yet', ui.conversation.text)
        self.assertEqual(self.store_records(), [])
        self.pm_factory.assert_not_called()

    def test_task_show_opens_detail_and_f2_escape_toggle(self):
        from prompt_toolkit.key_binding.key_processor import _Flush as timeout_flush

        def lone_escape(ui):
            processor = ui.app.key_processor
            with set_app(ui.app):
                processor.feed(KeyPress(Keys.Escape, '\x1b'))
                processor.process_keys()
                processor.feed(timeout_flush)
                processor.process_keys()
            self.assertFalse(ui.show_detail)

        ui, _ = self.drive(['/task mode first\r', 'first task\r', self.idle,
                            lambda ui: self.assertTrue(ui.show_detail), lone_escape,
                            '\x1bOQ', lambda ui: self.assertTrue(ui.show_detail),
                            '\x1bOQ', lambda ui: self.assertFalse(ui.show_detail)])
        task_id = ui.last_task_id
        ui, _ = self.drive(['/task show ' + task_id + '\r', self.idle])
        self.assertTrue(ui.show_detail)
        self.assertIn('Task ' + task_id + ' state PM_RESPONDED', ui.conversation.text)
        self.assertIn('Run ID: ' + self.store_records()[0]['run_id'], ui.detail.text)

    def test_esc_enter_still_inserts_newline_in_task_mode_with_detail_open(self):
        ui, _ = self.drive(['/task mode first\r', 'first\r', self.idle, 'a', '\x1b\r', 'b',
                            lambda ui: self.assertEqual(ui.input.text, 'a\nb'),
                            lambda ui: self.assertTrue(ui.show_detail)])
        self.assertEqual(len(self.store_records()), 1)



def _record(**fields):
    """A TaskStore-shaped record with only the fields a test sets."""
    base = {'task_id': 'e' * 32, 'run_id': 'f' * 32, 'state': 'PM_RESPONDED', 'developer': None,
            'request': '修正登入錯誤\n細節', 'updated_at': '2026-10-28T01:00:00Z',
            'created_at': '2026-10-28T00:00:00Z', 'pm_provider_profile': 'pm',
            'pm_provider_type': 'openai-compatible', 'pm_model': None, 'pm_session_id': 'pm-x',
            'usage': None, 'error_code': None}
    base.update(fields)
    return base


def _developer(**fields):
    base = {'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'codex', 'provider_type': 'codex-cli',
            'model': 'gpt-6-luna', 'session_id': 's1', 'usage': None, 'error_code': None,
            'execution': {'status': 'SUCCEEDED'}}
    base.update(fields)
    return base


class StatusProjectionTests(unittest.TestCase):
    """Stage/role/review/CI/PR/token text comes only from recorded fields."""

    def setUp(self):
        from llmtool import chat_tui_status
        self.status = chat_tui_status

    def test_intake_states(self):
        stage = self.status.stage
        self.assertEqual(stage(_record())[0], '已建立／待派工')
        self.assertEqual(stage(_record(state='PM_PENDING'))[0], 'PM_INTAKE')
        self.assertEqual(stage(_record(state='INPUT_REJECTED'))[0], 'FAILED')
        failed = _record(state='PM_UNAVAILABLE', error_code='NETWORK_PROVIDER_UNREACHABLE')
        self.assertEqual(stage(failed)[0], 'UNAVAILABLE')
        summary = self.status.summary_text(failed)
        self.assertIn('Recovery: the endpoint of PM profile pm must be reachable', summary)
        self.assertIn('Next responsible (derived): PM / Owner', summary)
        self.assertEqual(stage({'task_id': 'e' * 32, 'state': 'PM_RESPONDED'})[0], 'UNKNOWN')
        summary = self.status.summary_text(_record())
        self.assertIn('Next responsible (derived): PM (Developer dispatch)', summary)
        self.assertNotIn('RUNNING', summary)

    def test_developer_running_and_identity(self):
        record = _record(developer=_developer(state='DEVELOPER_DISPATCHING'))
        self.assertEqual(self.status.stage(record)[0], 'RUNNING')
        text = self.status.summary_text(record)
        self.assertIn('Developer: codex (type codex-cli, model gpt-6-luna), state DEVELOPER_DISPATCHING', text)
        self.assertIn('Next responsible (derived): Developer', text)
        self.assertEqual(self.status.stage(_record(developer=_developer(state='NEW')))[0], 'UNKNOWN')

    def test_blocked_reason_and_recovery(self):
        record = _record(developer=_developer(blocked_reason='needs Owner decision'))
        self.assertEqual(self.status.stage(record), ('BLOCKED', 'Developer: needs Owner decision'))
        self.assertIn('Recovery: NOT_AVAILABLE (Core records no recovery condition)',
                      self.status.summary_text(record))
        gated = _record(developer=_developer(direct_result_callback={
            'state': 'WAITING_PM', 'missing_gates': ['tests', 'review'], 'pm_usage': None}))
        self.assertEqual(self.status.stage(gated)[0], 'BLOCKED')
        self.assertIn('Recovery: Core missing_gates must pass: tests, review',
                      self.status.summary_text(gated))

    def callback(self, **handoff):
        return {'state': 'PM_RETURNED', 'missing_gates': [], 'pm_usage': None,
                'review_handoff': handoff}

    def test_review_wait_verdict_and_repair(self):
        pending = _record(developer=_developer(direct_result_callback=self.callback(
            dispatch_state='PENDING', next_step=None, receipt=None)))
        self.assertEqual(self.status.stage(pending)[0], 'WAITING_REVIEW')
        receipt = {'verdict': 'FAIL', 'reviewer_identity': 'claude-reviewer',
                   'reviewer_task_id': 'rev-1', 'execution_status': 'COMPLETED'}
        callback = self.callback(dispatch_state='RECEIVED', next_step='RETURN_TO_DEVELOPER_ONCE',
                                 receipt=receipt)
        callback['review_return'] = {'next_step': 'RETURN_TO_DEVELOPER_ONCE',
                                     'pm_usage': {'prompt_tokens': 3, 'completion_tokens': 4,
                                                  'total_tokens': 7}}
        failed = _record(developer=_developer(direct_result_callback=callback))
        self.assertEqual(self.status.stage(failed)[0], 'WAITING_PM')
        who, verdict = self.status.reviewer(failed)
        self.assertEqual(verdict, 'FAIL (review receipt)')
        self.assertIn('claude-reviewer', who)
        self.assertIn('PM review return 3+4=7', self.status.tokens(failed))
        claim = {'pr': 505, 'version': 1, 'state': 'DISPATCHED', 'attempt': 1}
        repairing = dict(failed, review_repair_claims=[claim])
        self.assertEqual(self.status.stage(repairing)[0], 'REPAIRING')
        rereview = dict(repairing, review_dispatch_claim=[
            {'pr': 505, 'version': 2, 'state': 'CLAIMED', 'reviewer_task': 'rev-2'}])
        self.assertNotEqual(self.status.stage(rereview)[0], 'REPAIRING')
        self.assertEqual(self.status.pull_request(rereview),
                         ('#505 (from repair claim, review claim)',
                          'https://github.com/davidcatcat/davidcattoolsproject/pull/505'))

    def test_pr_number_sources_are_named_and_conflicts_shown(self):
        url = 'https://github.com/davidcatcat/davidcattoolsproject/pull/'

        def with_evidence(record, number):
            completion = {'state': 'WAITING_CI', 'pm_recommendation': None, 'pm_usage': None,
                          'evidence': {'review_pr_ci': {'stage': 'INTEGRATION',
                                                        'pr_number': number}}}
            return dict(record, completion=completion)

        only = with_evidence(_record(developer=_developer()), 510)
        self.assertEqual(self.status.pull_request(only),
                         ('#510 (from completion review_pr_ci.pr_number)', url + '510'))
        claimed = _record(developer=_developer(), review_dispatch_claim=[
            {'pr': 510, 'version': 1, 'state': 'DISPATCHED', 'reviewer_task': 'r'}])
        agree = with_evidence(claimed, 510)
        self.assertEqual(self.status.pull_request(agree),
                         ('#510 (from completion review_pr_ci.pr_number, review claim)', url + '510'))
        number, link = self.status.pull_request(with_evidence(claimed, 511))
        self.assertEqual(number, 'CONFLICT: #510 (from review claim) vs '
                                 '#511 (from completion review_pr_ci.pr_number)')
        self.assertEqual(link, url + '510 | ' + url + '511')
        self.assertIn('PR: CONFLICT: #510', self.status.summary_text(with_evidence(claimed, 511)))
        mixed_claims = dict(claimed, review_repair_claims=[{'pr': 512, 'version': 1, 'attempt': 1,
                                                            'state': 'CLAIMED'}])
        self.assertTrue(self.status.pull_request(mixed_claims)[0].startswith('CONFLICT: #510'))
        for bad in ('510', 0, True, None):
            self.assertEqual(self.status.pull_request(with_evidence(_record(), bad)),
                             ('NOT_AVAILABLE', 'NOT_AVAILABLE'))

    def test_completion_ci_and_done(self):
        review_ci = {'stage': 'INTEGRATION', 'formal_review_state': 'ACCEPTED', 'ci_state': 'X',
                     'successful_checks': ['check (Linux 3.14)', 'check (macOS 3.13)'],
                     'required_checks': ['check (macOS 3.13)'], 'ci_head_sha': 'a' * 40,
                     'review_actor': 'claude', 'reviewer_d6_identity': 'rev-d6'}
        completion = {'state': 'WAITING_CI', 'evidence': {'review_pr_ci': review_ci},
                      'pm_recommendation': None, 'pm_usage': None}
        record = _record(developer=_developer(), completion=completion)
        self.assertEqual(self.status.stage(record)[0], 'WAITING_CI')
        self.assertTrue(self.status.ci(record).startswith('PASS (required check (macOS 3.13)'))
        self.assertEqual(self.status.reviewer(record)[1], 'PASS (formal review ACCEPTED)')
        done = dict(record, completion=dict(completion, state='PM_RETURNED',
                    pm_recommendation={'decision': 'ACCEPT', 'summary': 'ok'}))
        self.assertEqual(self.status.stage(done)[0], 'DONE')
        executor = dict(record, completion=dict(completion, evidence={'review_pr_ci': {
            'stage': 'EXECUTOR_RESULT', 'formal_review_state': 'NOT_STARTED',
            'ci_state': 'NOT_STARTED'}}))
        self.assertEqual(self.status.ci(executor), 'NOT_STARTED')
        self.assertTrue(self.status.ci(_record()).startswith('UNKNOWN'))
        failing = dict(record, completion=dict(completion, evidence={'review_pr_ci': dict(
            review_ci, successful_checks=[])}))
        self.assertEqual(self.status.ci(failing), 'UNKNOWN')

    def test_absent_sources_are_unknown_never_invented(self):
        text = self.status.summary_text(_record())
        self.assertIn('PR: NOT_AVAILABLE', text)
        self.assertIn('CI: UNKNOWN', text)
        self.assertIn('Latest review verdict: NOT_AVAILABLE', text)
        self.assertIn('Tokens (provider-reported): UNKNOWN', text)
        self.assertIn('model NOT_AVAILABLE', text)
        self.assertIn('Last inbox event: none', text)
        for invented in ('%', 'ETA', 'online', 'quota'):
            self.assertNotIn(invented, text)
        usage = {'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 3}
        both = _record(usage=usage, developer=_developer(usage=usage))
        self.assertEqual(self.status.tokens(both), 'PM intake 1+2=3; Developer 1+2=3')
        self.assertEqual(self.status.task_name(_record(request=None)), 'NOT_AVAILABLE')
        self.assertEqual(self.status.task_name({'task_id': 'e' * 32}), 'UNKNOWN')

    def test_unexpected_shapes_render_unknown_not_crash(self):
        odd = _record(developer={'direct_result_callback': {'state': 'WAITING_PM',
                                                            'missing_gates': [object()]}},
                      completion=['not', 'a', 'dict'], review_dispatch_claim=7)
        self.assertEqual(self.status.row('not a record')['stage'], 'UNKNOWN')
        self.assertIsInstance(self.status.summary_text(odd), str)
        self.assertIsInstance(self.status.summary_text(None), str)

    def test_load_records_counts_unreadable_newest_first(self):
        records = {'a' * 32: _record(task_id='a' * 32, updated_at='2026-10-27T00:00:00Z'),
                   'b' * 32: _record(task_id='b' * 32, updated_at='2026-10-28T00:00:00Z')}

        class Store:
            def task_ids(self):
                return sorted(records) + ['c' * 32]

            def get(self, task_id):
                return records[task_id]
        loaded, unreadable = self.status.load_records(Store())
        self.assertEqual([r['task_id'] for r in loaded], ['b' * 32, 'a' * 32])
        self.assertEqual(unreadable, 1)


@unittest.skipUnless(HAVE_TOOLKIT, 'prompt-toolkit optional extra not installed')
class TaskListTests(Base):
    """The Task list reads the real isolated TaskStore; no provider call is made by it."""

    drive = WidgetTests.drive
    idle = staticmethod(WidgetTests.idle)
    store_records = TaskModeTests.store_records

    def setUp(self):
        super().setUp()
        patch.dict(os.environ, {'HOME': str(self.temp_root.resolve())}).start()
        self.pm = Mock()
        self.pm.complete.return_value = Reply('PM plan', {})
        self.pm_factory = patch('llmtool.task_submission_cli.create_client',
                                return_value=self.pm).start()

    @staticmethod
    async def esc_settled(ui):
        """A lone Esc resolves after the key timeout; wait past it."""
        await asyncio.sleep(1.2)

    def list_rows(self, ui):
        return ''.join(text for _style, text in ui.list_text()).splitlines()

    async def listed(self, ui):
        for _ in range(200):
            if ui.list_loaded is not None and ui.list_reader is None:
                return
            await asyncio.sleep(0.01)
        raise AssertionError('list read did not finish')

    def test_list_detail_and_keys(self):
        def list_open(ui):
            self.assertTrue(ui.show_list)
            self.assertTrue(ui.app.layout.has_focus(ui.list_window))
            rows = self.list_rows(ui)
            self.assertIn('2 shown, 0 unreadable', rows[0])
            # The derived stage is labelled as derived in the pane itself (#510 review).
            self.assertIn('stage derived by the TUI', rows[0])
            self.assertIn('Stage (derived)', rows[1])
            self.assertIn('Updated (Core)', rows[1])
            self.assertNotIn('Next responsible', '\n'.join(rows))
            self.assertEqual(sum('已建立／待派工' in row for row in rows), 1)
            self.assertEqual(sum('UNAVAILABLE' in row for row in rows), 1)
            self.assertTrue(rows[2].startswith('▶ '))

        def detail_open(ui):
            self.assertTrue(ui.show_detail)
            self.assertIn('Status (from TaskStore record', ui.detail.text)
            self.assertIn('Task: ' + ui.selected_task_id(), ui.detail.text)
            self.assertIn('Task detail (llmtool Core task show', ui.detail.text)

        def back_to_list(ui):
            self.assertFalse(ui.show_detail)
            self.assertTrue(ui.show_list)
            self.assertTrue(ui.app.layout.has_focus(ui.list_window))

        calls = self.pm.complete.call_count
        ui, _ = self.drive(['/task mode first\r', 'first task\r', self.idle,
                            '/task mode missing\r', 'second task\r', self.idle,
                            lambda ui: setattr(self, 'calls', self.pm.complete.call_count),
                            '/task list\r', self.listed, list_open,
                            '\x1b[B', lambda ui: self.assertEqual(ui.list_index, 1),
                            '\r', self.idle, detail_open, '\x1b', self.esc_settled, back_to_list,
                            '\x1b', self.esc_settled, lambda ui: self.assertFalse(ui.show_list),
                            lambda ui: self.assertTrue(ui.app.layout.has_focus(ui.input)),
                            '\x1bOR', self.listed, lambda ui: self.assertTrue(ui.show_list),
                            '\x1bOR', lambda ui: self.assertFalse(ui.show_list)])
        self.assertEqual(self.pm.complete.call_count, self.calls)
        self.factory.assert_not_called()

    def test_refresh_on_key_and_interval_only(self):
        from llmtool import chat_tui_status
        original = chat_tui_status.load_records
        reads = []

        def counting(store):
            reads.append(1)
            return original(store)

        def redraw(ui):
            for _ in range(3):
                ui.app.renderer.render(ui.app, ui.app.layout)

        async def age(ui):
            # Only a stale list re-reads; the periodic redraw then starts one read.
            ui.list_loaded = (ui.list_loaded[0] - chat_tui_status.REFRESH_SECONDS, 'old')
            ui.app.invalidate()
            await asyncio.sleep(0.8)

        with patch('llmtool.chat_tui_status.load_records', side_effect=counting):
            ui, _ = self.drive(['/task mode first\r', 'a task\r', self.idle,
                                '/task list\r', self.listed, redraw,
                                lambda ui: self.assertEqual(len(reads), 1),
                                'r', self.listed, lambda ui: self.assertEqual(len(reads), 2),
                                '\x1b[15~', self.listed, lambda ui: self.assertEqual(len(reads), 3),
                                age, self.listed, lambda ui: self.assertEqual(len(reads), 4)])
        self.assertEqual(self.pm.complete.call_count, 1)

    @staticmethod
    async def settle(ui):
        ui.app.invalidate()
        await asyncio.sleep(0.8)

    def test_no_interval_read_while_detail_or_help_hides_the_list(self):
        from llmtool import chat_tui_status
        original = chat_tui_status.load_records
        reads = []

        def counting(store):
            reads.append(1)
            return original(store)

        async def stale_wait(ui):
            ui.list_loaded = (ui.list_loaded[0] - chat_tui_status.REFRESH_SECONDS, 'old')
            ui.app.invalidate()
            await asyncio.sleep(0.8)

        def hidden(ui):
            self.assertTrue(ui.show_list)
            self.assertFalse(ui.list_visible())

        with patch('llmtool.chat_tui_status.load_records', side_effect=counting):
            ui, _ = self.drive(['/task mode first\r', 'a task\r', self.idle,
                                '/task list\r', self.listed,
                                lambda ui: self.assertEqual(len(reads), 1),
                                '\r', self.idle, hidden, stale_wait,
                                lambda ui: self.assertEqual(len(reads), 1),  # detail open
                                '\x1bOP', hidden, stale_wait,
                                lambda ui: self.assertEqual(len(reads), 1),  # help open
                                '\x1bOP', lambda ui: self.assertTrue(ui.show_detail),
                                # Visible again and already stale: one read, then none until stale.
                                '\x1bOQ', lambda ui: self.assertTrue(ui.list_visible()),
                                self.settle, self.listed,
                                lambda ui: self.assertEqual(len(reads), 2),
                                self.settle, lambda ui: self.assertEqual(len(reads), 2)])

    def test_empty_store_resize_and_unseen_output(self):
        size = [Size(rows=24, columns=80)]

        def resize(ui):
            size[0] = Size(rows=8, columns=30)
            ui.app._on_resize()
            ui.app.renderer.render(ui.app, ui.app.layout)

        def check(ui):
            rows = self.list_rows(ui)
            self.assertIn('no Tasks in TaskStore', '\n'.join(rows))
            self.assertTrue(all(get_cwidth(row) <= 30 for row in rows))
            ui.append('assistant:\nlate reply')
            self.assertTrue(ui.show_list)
            self.assertIn('new conversation output', '\n'.join(self.list_rows(ui)))

        ui, _ = self.drive(['/task list\r', self.listed, resize, check,
                            '\x1b', self.esc_settled,
                            lambda ui: self.assertFalse(ui.unseen_output)], size=size)
        self.assertIn('late reply', ui.conversation.text)



def _event_record():
    sha = 'a' * 40
    return _record(
        developer=_developer(created_at='2026-10-28T01:00:00Z'),
        completion_events=[
            {'event_id': '1' * 32, 'source': 'GITHUB', 'state': 'ACKED', 'sequence': 3,
             'source_sha': sha, 'received_at': '2026-10-28T05:00:00Z',
             'consumed_at': '2026-10-28T05:01:00Z', 'acked_at': '2026-10-28T05:02:00Z',
             'dispatch_intent': None, 'issue_number': 510, 'comment_id': 99,
             'marker_type': 'review', 'marker_status': 'FAIL', 'merge_sha': None},
            {'event_id': '2' * 32, 'source': 'REVIEWER', 'state': 'PENDING', 'sequence': 4,
             'source_sha': sha, 'received_at': '2026-10-28T06:00:00Z', 'consumed_at': None,
             'acked_at': None, 'dispatch_intent': None}],
        completion_event_tombstones=[{'event_id': '0' * 32, 'source': 'HOST_WORKER',
                                      'acked_at': '2026-10-28T02:00:00Z'}],
        dispatch_audit=[{'at': '2026-10-28T00:30:00Z', 'action': 'DISPATCH',
                         'decision': 'ALLOWED', 'worker_key': 'codex-developer',
                         'reason_code': 'OWNER_ASSIGNMENT_MATCHED'}],
        remote_test_receipts=[{'result': 'FAIL', 'exit_code': 1, 'ran_total': 40,
                               'failures': ['tests.test_x'], 'source_sha': sha,
                               'started_at': '2026-10-28T03:00:00Z',
                               'ended_at': '2026-10-28T03:05:00Z'}],
        remote_test_outcomes=[{'result': 'PASS', 'code': 'OK', 'sha': sha,
                               'at': '2026-10-28T03:30:00Z'}],
        review_dispatch_claim=[{'pr': 510, 'version': 1, 'state': 'DISPATCHED',
                                'reviewer_task': 'rev-1', 'claimed_at': '2026-10-28T04:00:00Z',
                                'updated_at': '2026-10-28T04:10:00Z'}],
        review_repair_claims=[{'pr': 510, 'version': 1, 'attempt': 1, 'state': 'CLAIMED',
                               'claimed_at': '2026-10-28T05:30:00Z'}],
        high_debug=[{'status': 'RCA_CONFIRMED', 'family': 'pr-510', 'model_observed': 'gpt-6.1-sol',
                     'recorded_at': '2026-10-28T07:00:00Z'}])


class EventTimelineTests(unittest.TestCase):
    """Timelines show only what the record stores; nothing is acked, consumed or polled."""

    def setUp(self):
        from llmtool import chat_tui_events
        self.events = chat_tui_events

    def test_all_recorded_sources_newest_first(self):
        events = self.events.task_events(_event_record())
        times = [e['time'] for e in events if e['time']]
        self.assertEqual(times, sorted(times, reverse=True))
        text = '\n'.join(self.events.format_event(e) for e in events)
        for expected in (
                '[HIGH DEBUG] High Debug RCA_CONFIRMED, family pr-510, model gpt-6.1-sol',
                '[INBOX] REVIEWER event PENDING, sequence 4',
                '[INBOX] GITHUB event ACKED, sequence 3, source sha aaaaaaaaaaaa — issue/PR #510, '
                'comment 99, marker review/FAIL',
                '[INBOX] GITHUB event consumed by the PM', '[INBOX] GITHUB event acknowledged',
                '[INBOX] HOST_WORKER event acknowledged and compacted',
                '[REPAIR] repair claim PR #510 v1 attempt 1 CLAIMED',
                '[REVIEW] review claim PR #510 v1 DISPATCHED, reviewer task rev-1 (claimed)',
                '(updated)', '[TEST] remote test outcome PASS (OK)',
                '[TEST] remote test receipt FAIL, exit 1, ran 40, failed modules 1',
                '[DISPATCH] dispatch gate DISPATCH ALLOWED for codex-developer (OWNER_ASSIGNMENT_MATCHED)',
                '[DISPATCH] Developer assignment recorded: codex (model gpt-6-luna)',
                '[TASK] Task recorded (PM state now PM_RESPONDED)'):
            self.assertIn(expected, text)
        self.assertTrue(text.splitlines()[0].startswith('2026-10-28T07:00:00Z'))
        self.assertTrue(all(e['recorded'] for e in events))

    def test_derivable_event_is_marked_not_yet_recorded(self):
        record = _event_record()
        with patch('llmtool.task_submission_cli._active_pm_handoff', return_value={'x': 1}), \
                patch('llmtool.active_pm_inbox._handoff_event', return_value={'event_id': '9' * 32}):
            events = self.events.task_events(record)
        missing = [e for e in events if not e['recorded']]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]['kind'], 'NOT YET RECORDED')
        self.assertIsNone(missing[0]['time'])
        self.assertIs(events[-1], missing[0])
        self.assertIn('time UNKNOWN  [NOT YET RECORDED]', self.events.format_event(missing[0]))
        with patch('llmtool.task_submission_cli._active_pm_handoff', return_value={'x': 1}), \
                patch('llmtool.active_pm_inbox._handoff_event', return_value={'event_id': '2' * 32}):
            self.assertTrue(all(e['recorded'] for e in self.events.task_events(record)))

    def test_never_calls_inbox_writes_ack_or_poll(self):
        boom = AssertionError('write path called')
        with patch('llmtool.active_pm_inbox.read_inbox', side_effect=boom), \
                patch('llmtool.active_pm_inbox.record_ready_event', side_effect=boom), \
                patch('llmtool.active_pm_inbox.ack', side_effect=boom), \
                patch('llmtool.github_events.poll', side_effect=boom):
            self.events.timeline_text(_event_record())
            self.events.recent_text([_event_record(), _record(task_id='b' * 32)], 0, 'now')

    def test_odd_shapes_and_empty(self):
        odd = _record(completion_events='bad', dispatch_audit=[1, {'at': 5}], high_debug={'status': None})
        text = self.events.timeline_text(odd)
        self.assertIn('[TASK] Task recorded', text)
        self.assertIn('time UNKNOWN  [DISPATCH] dispatch gate UNKNOWN', text)
        self.assertEqual(self.events.task_events('x'), [])
        self.assertIn('no events in TaskStore', self.events.recent_text([], 0, 'now'))

    def test_recent_events_across_tasks_limited_newest_first(self):
        records = [_event_record(), _record(task_id='b' * 32, created_at='2026-10-29T00:00:00Z')]
        lines = self.events.recent_text(records, 1, '10:00:00').splitlines()
        self.assertIn('across 2 Tasks (1 unreadable; refreshed 10:00:00', lines[0])
        self.assertTrue(lines[2].startswith('2026-10-29T00:00:00Z  bbbbbbbb  [TASK]'))
        many = [_record(task_id='%032x' % i, created_at='2026-10-28T00:00:%02dZ' % (i % 60))
                for i in range(150)]
        self.assertEqual(len(self.events.recent_text(many, 0, 'now').splitlines()),
                         2 + self.events.RECENT_LIMIT)


@unittest.skipUnless(HAVE_TOOLKIT, 'prompt-toolkit optional extra not installed')
class TaskEventsPaneTests(Base):
    """F4 / /task events over the real isolated TaskStore; no inbox write path is used."""

    drive = WidgetTests.drive
    idle = staticmethod(WidgetTests.idle)
    listed = TaskListTests.listed
    esc_settled = staticmethod(TaskListTests.esc_settled)
    def setUp(self):
        super().setUp()
        patch.dict(os.environ, {'HOME': str(self.temp_root.resolve())}).start()
        self.pm = Mock()
        self.pm.complete.return_value = Reply('PM plan', {})
        patch('llmtool.task_submission_cli.create_client', return_value=self.pm).start()

    def test_events_pane_keys_refresh_and_detail_timeline(self):
        boom = AssertionError('inbox write path called')
        from llmtool import chat_tui_status
        original = chat_tui_status.load_records
        reads = []

        def counting(store):
            reads.append(1)
            return original(store)

        async def stale_wait(ui):
            ui.list_loaded = (ui.list_loaded[0] - chat_tui_status.REFRESH_SECONDS, 'old')
            ui.app.invalidate()
            await asyncio.sleep(0.8)

        def unseen(ui):
            ui.append('assistant:\nlate reply')
            self.assertTrue(ui.show_events)
            self.assertIn('new conversation output', ui.events.text)

        def shown(ui):
            self.assertTrue(ui.show_events)
            self.assertTrue(ui.app.layout.has_focus(ui.events))
            self.assertIn('Recent events across 1 Tasks (0 unreadable', ui.events.text)
            self.assertIn('[TASK] Task recorded (PM state now PM_RESPONDED)', ui.events.text)
            self.assertIn(ui.last_task_id[:8], ui.events.text)

        with patch('llmtool.active_pm_inbox.read_inbox', side_effect=boom), \
                patch('llmtool.active_pm_inbox.record_ready_event', side_effect=boom), \
                patch('llmtool.active_pm_inbox.ack', side_effect=boom), \
                patch('llmtool.chat_tui_status.load_records', side_effect=counting):
            ui, _ = self.drive(['/task mode first\r', 'event task\r', self.idle,
                                lambda ui: self.assertIn('Events (newest first', ui.detail.text),
                                lambda ui: self.assertIn('[TASK] Task recorded', ui.detail.text),
                                '/task events\r', self.listed, shown, unseen,
                                lambda ui: self.assertEqual(len(reads), 1),
                                'r', self.listed, lambda ui: self.assertEqual(len(reads), 2),
                                # Help hides the pane: no interval read while it is hidden.
                                '\x1bOP', lambda ui: self.assertFalse(ui.events_visible()),
                                stale_wait, lambda ui: self.assertEqual(len(reads), 2),
                                '\x1bOP', lambda ui: self.assertTrue(ui.events_visible()),
                                lambda ui: self.assertTrue(ui.app.layout.has_focus(ui.events)),
                                self.listed,
                                '\x1b', self.esc_settled,
                                lambda ui: self.assertFalse(ui.show_events),
                                lambda ui: self.assertTrue(ui.app.layout.has_focus(ui.input)),
                                '\x1bOS', self.listed,
                                lambda ui: self.assertTrue(ui.show_events),
                                '\x1bOS', lambda ui: self.assertFalse(ui.show_events)])
        self.assertEqual(self.pm.complete.call_count, 1)
        self.factory.assert_not_called()



PM_SESSION = '0123abcd-89ab-cdef-0123-456789abcdef'


class FakeClaude:
    """A scripted `claude -p` stream-json process: replies per user line, holds on a gate."""

    def __init__(self, turns):
        import json as _json
        self._json = _json
        self.turns = list(turns)        # one list of (stream object | 'HOLD') per user message
        self.gate = threading.Event()
        self.written = []
        read, self._write = os.pipe()
        self.stdout = os.fdopen(read, 'rb')
        self.stdin = self
        self.terminated = self.closed_stdin = False
        self.returncode = None

    # stdin side ------------------------------------------------------------
    def write(self, data):
        line = self._json.loads(data.decode('utf-8'))
        self.written.append(line)
        if line.get('type') == 'user' and self.turns:
            self.gate.clear()   # each turn holds until its own interrupt or release
            threading.Thread(target=self._play, args=(self.turns.pop(0),), daemon=True).start()
        elif line.get('type') == 'control_request':
            self.gate.set()
        return len(data)

    def flush(self):
        pass

    def close(self):
        self.closed_stdin = True
        self._end()

    # process side ----------------------------------------------------------
    def _emit(self, obj):
        try:
            os.write(self._write, (self._json.dumps(obj) + '\n').encode('utf-8'))
        except OSError:
            pass

    def _play(self, script):
        for item in script:
            if item == 'HOLD':
                self.gate.wait(5)
            elif item == 'EXIT':
                self._end()
                return
            else:
                self._emit(item)

    def _end(self):
        if self._write is not None:
            try:
                os.close(self._write)
            except OSError:
                pass
            self._write = None

    def terminate(self):
        self.terminated = True
        self._end()

    def kill(self):
        self._end()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self._end()
        self.returncode = 0
        return 0

    def users(self):
        return [line for line in self.written if line.get('type') == 'user']


def _delta(text, index=0):
    """A Claude Code stream-json partial text event, as pm_runtime S2 parses it."""
    return {'type': 'stream_event', 'event': {'type': 'content_block_delta', 'index': index,
                                              'delta': {'type': 'text_delta', 'text': text}}}


def _tool_result(tool_id, content, is_error):
    return {'type': 'user', 'message': {'content': [
        {'type': 'tool_result', 'tool_use_id': tool_id, 'content': content, 'is_error': is_error}]}}


def _assistant(*content):
    return {'type': 'assistant', 'message': {'content': list(content)}}


def _result(text='done', **extra):
    return dict({'type': 'result', 'subtype': 'success', 'result': text,
                 'usage': {'input_tokens': 5, 'output_tokens': 7}}, **extra)


def _full_turn(hold=False):
    return ([_delta('Hello ')] + (['HOLD'] if hold else []) +
            [_delta('老闆'),
             _assistant({'type': 'text', 'text': 'Hello 老闆'},
                        {'type': 'tool_use', 'id': 't1', 'name': 'Read', 'input': {'path': 'SECRET_PATH'}}),
             {'type': 'user', 'message': {'content': [
                 {'type': 'tool_result', 'tool_use_id': 't1', 'content': 'SECRET_OUTPUT',
                  'is_error': True}]}},
             _assistant({'type': 'text', 'text': 'after tool'}),
             _result('Hello 老闆 after tool')])


def _fake_key():
    """A credential-shaped literal built at runtime (diffs must not carry one)."""
    return 's' + 'k-' + ''.join('A1b2C3d4E5f6G7h8J9')


class StreamScreeningTests(unittest.TestCase):
    """Streamed PM text is redacted before display, including secrets split over deltas."""

    def setUp(self):
        from llmtool import chat_tui_pm
        self.pm = chat_tui_pm

    def progressive(self, pieces):
        raw, shown = '', []
        for piece in pieces:
            raw += piece
            shown.append(self.pm.screen_stream(raw, final=False))
        return raw, shown

    def test_key_split_over_deltas_is_never_shown(self):
        key = _fake_key()
        raw, shown = self.progressive(['status ok: ', key[:1], key[1:7], key[7:], ' done'])
        for text in shown + [self.pm.screen_stream(raw, final=True)]:
            self.assertNotIn(key[3:9], text)
            self.assertNotIn(key[:6], text)
        self.assertIn('[REDACTED]', self.pm.screen_stream(raw, final=True))

    def test_bearer_and_private_key_blocks(self):
        header_value = 'Bea' + 'rer ' + 'x' * 30
        raw, shown = self.progressive(['auth: ', header_value[:3], header_value[3:12],
                                       header_value[12:], ' end'])
        self.assertTrue(all('xxxxxx' not in text for text in shown))
        header = '-----' + 'BEGIN ' + 'PRIVATE KEY' + '-----'
        footer = '-----' + 'END ' + 'PRIVATE KEY' + '-----'
        body = 'MII' + 'q' * 40
        raw, shown = self.progressive(['key below\n', header + '\n', body + '\n', footer, '\nafter'])
        self.assertTrue(all('qqqqqq' not in text and 'BEGIN' not in text for text in shown))
        final = self.pm.screen_stream(raw, final=True)
        self.assertNotIn('qqqqqq', final)

    def test_cjk_streams_at_once_and_unclassified_is_withheld(self):
        self.assertEqual(self.pm.screen_stream('你好，進度正常', final=False), '你好，進度正常')
        self.assertEqual(self.pm.screen_stream('see the ' + 'pass' + 'word: ', final=True),
                         self.pm.WITHHELD)
        self.assertEqual(self.pm.safe_name('Read'), 'Read')
        self.assertEqual(self.pm.safe_name('tool ' + _fake_key()), '[withheld]')


def _crafted_turn(**fields):
    turn = {'turn_id': 'a' * 32, 'seq': 1, 'started_at': '2026-10-28T00:00:00+00:00',
            'ended_at': None, 'owner_text': 'hi', 'status': 'COMPLETED', 'result_text': 'ok',
            'tool_names': ['Read'], 'usage': {}}
    turn.update(fields)
    return turn


class PMDisplayScreeningTests(unittest.TestCase):
    """Every PM value shown on screen (live, stored history, codes) is screened."""

    def setUp(self):
        from llmtool import chat_tui_pm
        self.pm = chat_tui_pm
        self.key = _fake_key()

    def test_history_fields_are_screened(self):
        turn = _crafted_turn(tool_names=['Read', 'tool ' + self.key, 7],
                             owner_text='mine ' + self.key, result_text='theirs ' + self.key,
                             status='done ' + self.key, started_at='at ' + self.key)
        text = self.pm.history_text({'turns': [turn]})
        self.assertNotIn(self.key[3:9], text)
        self.assertIn('tools: Read, [withheld], UNKNOWN', text)
        self.assertIn('PM [UNKNOWN]', text)
        self.assertIn('mine [REDACTED]', text)
        self.assertIn('theirs [REDACTED]', text)
        clean = self.pm.history_text({'turns': [_crafted_turn()]})
        self.assertIn('(history) PM [COMPLETED] tools: Read:\nok', clean)

    def test_codes_ids_and_reasons_are_shape_checked(self):
        self.assertEqual(self.pm.safe_code('PM_PROCESS_EXITED'), 'PM_PROCESS_EXITED')
        for bad in ('bad code', 'x' + self.key, None, 7, 'A' * 65):
            self.assertEqual(self.pm.safe_code(bad), 'UNKNOWN')
        self.assertIn('PM runtime error UNKNOWN', self.pm.error_text('leak ' + self.key))
        self.assertEqual(self.pm.event_ids({'turn_id': self.key, 'seq': True}), (None, None))
        line = self.pm.tool_result_text({'is_error': True, 'reason': self.key}, 'Read')
        self.assertEqual(line, '  · tool Read: error (reason UNKNOWN)')
        end = self.pm.turn_end_text({'status': 'x ' + self.key, 'usage': {}}, 1.0)
        self.assertNotIn(self.key[3:9], end)
        self.assertIn('PM turn UNKNOWN', end)
        self.assertEqual(self.pm.safe_text(None), 'UNKNOWN')


class PMAdapterTests(unittest.TestCase):
    """Display text for the values pm_runtime S2 returns; anything else is UNKNOWN."""

    def test_adapter_points_and_texts(self):
        from llmtool import chat_tui_pm as pm
        self.assertEqual(pm.binding_text(None), 'NOT dedicated (no pm_runtime binding)')
        self.assertEqual(pm.binding_text({'dedicated': True, 'created_at': '2026-10-28T01:00:00+00:00'}),
                         'dedicated (pm_runtime binding created 2026-10-28T01:00:00+00:00)')
        self.assertEqual(pm.event_ids({'type': 'TEXT_DELTA'}), (None, None))
        self.assertEqual(pm.event_ids({'turn_id': 'abc', 'seq': 3}), ('abc', 3))
        # Only S2's three reason values get a meaning; a missing reason is never guessed.
        self.assertEqual(pm.tool_result_text({'reason': 'OK', 'is_error': False}, 'Read'),
                         '  · tool Read: done')
        self.assertEqual(pm.tool_result_text({'reason': 'TOOL_ERROR', 'is_error': True}, 'Read'),
                         '  · tool Read: error (pm_runtime reason TOOL_ERROR)')
        self.assertIn('auto-denied (pm_runtime reason PERMISSION_DENIED',
                      pm.tool_result_text({'reason': 'PERMISSION_DENIED', 'is_error': True}, 'Bash'))
        self.assertEqual(pm.tool_result_text({'is_error': True}, None),
                         '  · tool UNKNOWN: error (reason UNKNOWN)')
        self.assertIn('/pm new', pm.error_text('PM_SESSION_NOT_DEDICATED'))
        self.assertIn('usage limit must reset', pm.error_text('PM_QUOTA_EXHAUSTED'))
        self.assertIn('looks open in another process', pm.error_text('PM_SESSION_LIVE_ELSEWHERE'))
        self.assertIn('UNKNOWN — pm_runtime gives no recovery', pm.error_text('PM_NEW_CODE'))
        self.assertEqual(pm.usage_text({}), 'UNKNOWN')
        self.assertEqual(pm.error_code(ValueError('PRIVATE')), 'UNKNOWN')


PM_TEST_LIMIT = 120   # seconds; a PM test that runs longer dumps every stack and exits


class BoundedPMTest(Base):
    """Every PM test is bounded: past PM_TEST_LIMIT the process dumps all thread stacks
    and exits, so CI fails with a traceback instead of hanging. Fake processes, their
    pipes and the TUI's PM worker threads are cleaned up after every test."""

    def setUp(self):
        super().setUp()
        import faulthandler
        faulthandler.dump_traceback_later(PM_TEST_LIMIT, exit=True)
        self.addCleanup(faulthandler.cancel_dump_traceback_later)
        self.fakes = []
        self.addCleanup(self._release_fakes)

    def _release_fakes(self):
        for fake in self.fakes:
            fake.gate.set()            # a held turn ends
            fake._end()                # its stdout reaches EOF
        for thread in threading.enumerate():
            if thread.name.startswith('llmtool-chat-pm'):
                thread.join(5)
        for fake in self.fakes:
            try:
                fake.stdout.close()
            except OSError:
                pass

    def run_ui(self, main):
        """Run one TUI session on its own loop, bounded; never waits on executor shutdown."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(asyncio.wait_for(main(), 60))
        finally:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.wait(pending, timeout=5))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()


@unittest.skipUnless(HAVE_TOOLKIT, 'prompt-toolkit optional extra not installed')
class PMConversationTests(BoundedPMTest):
    """The real pm_runtime S2 against a scripted fake `claude`; ChatSession is never used.

    The dedicated binding is created by pm_runtime.create_dedicated_pm (its runner only
    records the argv), so open() passes S2's binding check for real."""

    idle = staticmethod(WidgetTests.idle)

    def setUp(self):
        super().setUp()
        from llmtool import pm_runtime
        self.pm_runtime = pm_runtime
        self.state = self.temp_root.resolve() / 'pm-state'
        self.observe = patch.object(pm_runtime.active_pm_inbox, 'observe_claude_session',
                                    side_effect=lambda session, cwd, environ=None: cwd)
        self.observe.start()
        self.addCleanup(self.observe.stop)
        self.turns = [_full_turn()]
        self.created = []
        test = self

        class API(chat_tui_pm.PMRuntimeAPI):
            def runtime(self, session_id, cwd):
                return test.make_runtime(session_id, cwd)

        self.api = API(state_root=self.state, environ={'HOME': str(self.temp_root)},
                       create_runner=lambda argv, cwd, env: self.created.append(argv))
        self.binding = self.api.create_dedicated(str(self.temp_root))
        self.session_id = self.binding['native_session_id']

    def make_runtime(self, session_id, cwd):
        def runner(argv, run_cwd, env):
            fake = FakeClaude(self.turns)
            fake.argv = argv
            self.fakes.append(fake)
            return fake
        return self.pm_runtime.PMRuntime(session_id, cwd=cwd, runner=runner,
                                         state_root=self.state, environ={'HOME': str(self.temp_root)})

    def drive(self, steps, size=None):
        output = DummyOutput()
        if size is not None:
            output.get_size = lambda: size[0]
        with create_pipe_input() as pipe:
            ui = chat_tui.ChatTUI(self.session, input=pipe, output=output, pm_api=self.api)
            self.ui = ui

            async def main():
                task = asyncio.ensure_future(ui.app.run_async(handle_sigint=False))
                await asyncio.sleep(0.05)
                for step in steps:
                    if isinstance(step, str):
                        pipe.send_text(step)
                        await asyncio.sleep(0.05)
                    else:
                        result = step(ui)
                        if asyncio.iscoroutine(result):
                            await result
                if not task.done() and not ui.app.is_done:
                    ui.app.exit(result=0)
                result = await asyncio.wait_for(task, 5)
                ui.close_pm()
                return result

            with tui_cli_signal_scope(ui._handle_signal):
                return ui, self.run_ui(main)

    @staticmethod
    async def until(ui, condition, what='condition'):
        for _ in range(300):
            if condition(ui):
                return
            await asyncio.sleep(0.01)
        raise AssertionError(what + ' not reached')

    def connected(self, ui):
        return self.until(ui, lambda ui: ui.pm_state == 'idle' and ui.pm_runtime is not None,
                          'PM connected')

    def turn_done(self, ui):
        return self.until(ui, lambda ui: not ui.pm_turn_active, 'PM turn end')

    def connect(self):
        return '/pm connect\r'   # uses the saved dedicated binding

    def test_connect_stream_tools_and_turn_end(self):
        self.turns = [_full_turn(hold=True)]

        def connected_view(ui):
            self.assertTrue(ui.pm_mode)
            self.assertEqual(ui.status_text(), 'PM:' + self.session_id[:8] + ' idle | F1')   # no chat fields
            self.assertIn('PM session ' + self.session_id, ui.conversation.text)
            self.assertIn('dedicated (pm_runtime binding created', ui.conversation.text)
            self.assertEqual(self.created, [['claude', '--session-id', self.session_id]])
            self.assertIn('PM history: no stored turns', ui.conversation.text)
            argv = self.fakes[0].argv
            self.assertEqual(argv[argv.index('--resume') + 1], self.session_id)
            self.assertIn('--include-partial-messages', argv)

        async def partial(ui):
            await self.until(ui, lambda ui: 'PM:\nHello' in ui.conversation.text, 'first delta')
            self.assertTrue(ui.pm_turn_active)
            self.assertIn('working', ui.status_text())
            self.assertNotIn('Hello 老闆', ui.conversation.text)   # streamed, not yet complete
            self.fakes[0].gate.set()

        ui, _ = self.drive([self.connect(), self.connected, connected_view,
                            '請問進度？\r', partial, self.turn_done])
        text = ui.conversation.text
        self.assertIn('owner → PM:\n請問進度？', text)
        self.assertEqual(text.count('Hello 老闆'), 1)                # assistant text not doubled
        self.assertIn('  · tool Read', text)
        self.assertIn('  · tool Read: error (pm_runtime reason TOOL_ERROR)', text)
        self.assertNotIn('auto-denied', text)
        self.assertIn('PM:\nafter tool', text)                      # new block after the tool
        self.assertIn('— PM turn COMPLETED in', text)
        self.assertIn('usage: input 5, output 7 (provider-reported)', text)
        self.assertNotIn('SECRET', text)
        self.assertEqual(ui.pm_state, 'idle')
        self.assertEqual([u['message']['content'][0]['text'] for u in self.fakes[0].users()],
                         ['請問進度？'])
        self.assertEqual(self.session.history, [])
        self.client.complete.assert_not_called()

    def test_repeated_enter_resize_and_redraw_send_one_turn(self):
        self.turns = [_full_turn(hold=True)]
        size = [Size(rows=24, columns=80)]

        def resize(ui):
            size[0] = Size(rows=10, columns=40)
            ui.app._on_resize()
            ui.app.renderer.render(ui.app, ui.app.layout)

        async def busy(ui):
            await self.until(ui, lambda ui: 'PM:\nHello' in ui.conversation.text)
            self.assertIn('a PM turn is in progress; input kept', ui.conversation.text)
            self.assertEqual(ui.input.text, 'second')
            ui.input.buffer.reset()
            self.fakes[0].gate.set()

        ui, _ = self.drive([self.connect(), self.connected, 'one\r', '\r\r', resize,
                            'second\r', busy, self.turn_done], size=size)
        self.assertEqual(len(self.fakes[0].users()), 1)

    def test_escape_interrupts_and_ctrl_c_interrupts_then_exits(self):
        self.turns = [[_delta('working…'), 'HOLD', _result('stopped')],
                      [_delta('again…'), 'HOLD', _result('stopped')]]
        from prompt_toolkit.key_binding.key_processor import _Flush as timeout_flush

        def lone_escape(ui):
            processor = ui.app.key_processor
            with set_app(ui.app):
                processor.feed(KeyPress(Keys.Escape, '\x1b'))
                processor.process_keys()
                processor.feed(timeout_flush)
                processor.process_keys()

        async def streaming(ui):
            await self.until(ui, lambda ui: ui.pm_turn_active and ui.pm_block_raw is not None)
            self.assertEqual(ui.app.timeoutlen, chat_tui.ESC_SEQUENCE_TIMEOUT)

        def interrupted(ui):
            self.assertEqual(ui.pm_state, 'idle')
            self.assertIn('interrupt sent to the PM', ui.conversation.text)
            self.assertIn('— PM turn INTERRUPTED', ui.conversation.text)
            controls = [l for l in self.fakes[0].written if l.get('type') == 'control_request']
            self.assertEqual([c['request']['subtype'] for c in controls], ['interrupt'])

        async def first_ctrl_c(ui):
            ui.request_quit()
            self.assertFalse(ui.app.is_done)
            self.assertIn('Ctrl-C again exits', ui.conversation.text)

        ui, result = self.drive([self.connect(), self.connected, 'stop me\r', streaming,
                                 lone_escape, self.turn_done, interrupted,
                                 'again\r', streaming, first_ctrl_c,
                                 lambda ui: ui.request_quit(),
                                 lambda ui: self.assertTrue(ui.app.is_done)])
        self.assertEqual(result, 0)
        self.assertIsNone(ui.pm_runtime)
        self.assertTrue(self.fakes[0].closed_stdin)   # close() ran on exit

    def test_pm_status_field_fits_a_narrow_terminal(self):
        size = [Size(rows=24, columns=80)]

        def narrow(ui):
            size[0] = Size(rows=24, columns=12)
            text = ui.status_text()
            self.assertLessEqual(get_cwidth(text), 12)
            self.assertTrue(text.startswith('PM:'))

        self.drive([self.connect(), self.connected, narrow], size=size)

    def test_split_secret_in_a_real_turn_is_never_displayed(self):
        key = _fake_key()
        self.turns = [[_delta('your key is '), _delta(key[:5]), _delta(key[5:]), _delta(' — rotate it'),
                       _result('')]]
        seen = []

        def watch(ui):
            original = ui._pm_render

            def render(final):
                original(final)
                seen.append(ui.conversation.text)
            ui._pm_render = render

        ui, _ = self.drive([self.connect(), self.connected, watch, 'show key\r', self.turn_done])
        self.assertTrue(seen)
        for text in seen + [ui.conversation.text]:
            self.assertNotIn(key[3:9], text)
        self.assertIn('your key is [REDACTED] — rotate it', ui.conversation.text)

    def test_crafted_history_file_is_screened_on_connect_and_history(self):
        import json
        key = _fake_key()
        root = self.state
        root.mkdir(parents=True, exist_ok=True)
        (root / ('pm-' + self.session_id + '.history.json')).write_text(json.dumps(
            {'pm_session_id': self.session_id, 'turns': [_crafted_turn(
                tool_names=['tool ' + key], owner_text='o ' + key, result_text='r ' + key,
                status='s ' + key)]}))
        ui, _ = self.drive([self.connect(), self.connected, '/pm history\r',
                            lambda ui: self.until(ui, lambda ui: ui.conversation.text.count(
                                'PM history (stored') == 2)])
        text = ui.conversation.text
        self.assertNotIn(key[3:9], text)
        self.assertIn('tools: [withheld]', text)

    def test_runtime_error_code_from_events_is_shape_checked(self):
        key = _fake_key()
        ui, _ = self.drive([self.connect(), self.connected,
                            lambda ui: ui.pm_event({'type': 'RUNTIME_ERROR', 'code': 'x ' + key}),
                            lambda ui: ui.pm_event({'type': 'TOOL_USE', 'tool_use_id': 't',
                                                    'name': 'tool ' + key})])
        self.assertNotIn(key[3:9], ui.conversation.text + ui.status_text())
        self.assertEqual(ui.pm_state, 'error(UNKNOWN)')

    def lock_is_free(self):
        probe = self.make_runtime(self.session_id, str(self.temp_root)).open()   # raises if still locked
        probe.close()

    def test_exit_while_open_runs_closes_the_runtime_it_opens(self):
        entered, release = threading.Event(), threading.Event()
        make = self.make_runtime

        def slow_runtime(session_id, cwd):
            runtime = make(session_id, cwd)
            runner = runtime.runner

            def blocking(argv, run_cwd, env):
                entered.set()
                release.wait(5)
                return runner(argv, run_cwd, env)
            runtime.runner = blocking
            return runtime

        self.make_runtime = slow_runtime
        ui, _ = self.drive([self.connect(), lambda ui: self.until(ui, lambda ui: entered.is_set()),
                            lambda ui: ui.app.exit(result=0)])
        self.make_runtime = make
        release.set()
        for thread in threading.enumerate():
            if thread.name == 'llmtool-chat-pm':
                thread.join(5)
        self.assertIsNone(ui.pm_runtime)
        self.assertTrue(self.fakes[0].closed_stdin)
        self.lock_is_free()

    def test_exit_before_connect_completion_is_delivered_closes_the_runtime(self):
        opened, release = threading.Event(), threading.Event()

        def stuck_history(session_id, runtime):
            opened.set()
            release.wait(5)
            return {'turns': []}, None

        def install(ui):
            ui._pm_read_history = stuck_history

        ui, _ = self.drive([install, self.connect(),
                            lambda ui: self.until(ui, lambda ui: opened.is_set()),
                            lambda ui: ui.app.exit(result=0)])
        # close_pm ran on exit and closed the opened-but-unadopted runtime.
        self.assertTrue(self.fakes[0].closed_stdin)
        self.lock_is_free()
        release.set()   # the worker's late completion cannot reach the closed loop
        for thread in threading.enumerate():
            if thread.name == 'llmtool-chat-pm':
                thread.join(5)
        self.assertIsNone(ui.pm_runtime)

    def pending_done(self, ui):
        return self.until(ui, lambda ui: not ui.pm_pending, 'PM request done')

    def test_no_binding_new_and_explicit_connect(self):
        self.state = self.temp_root.resolve() / 'empty-state'   # no binding saved here
        self.api.state_root = self.state
        ui, _ = self.drive([self.connect(), self.pending_done])
        self.assertIn('no dedicated PM binding is saved. /pm new [CWD]', ui.conversation.text)
        self.assertIsNone(ui.pm_runtime)
        cwd = str(self.temp_root)
        ui, _ = self.drive(['/pm new ' + cwd + '\r', self.pending_done,
                            self.connect(), self.connected])
        created = self.created[-1][2]
        self.assertEqual(self.created[-1][:2], ['claude', '--session-id'])
        self.assertIn('Dedicated PM session ' + created + ' created by pm_runtime', ui.conversation.text)
        self.assertIn('the TUI has not verified that session', ui.conversation.text)
        self.assertEqual(ui.pm_target[0], created)
        argv = self.fakes[-1].argv
        self.assertEqual(argv[argv.index('--resume') + 1], created)
        ui, _ = self.drive(['/pm connect ' + created + ' ' + cwd + '\r', self.connected])
        self.assertEqual(ui.pm_target, (created, cwd))

    def test_not_dedicated_and_live_elsewhere_are_refused(self):
        import uuid as _uuid
        other = str(_uuid.uuid4())
        ui, _ = self.drive(['/pm connect ' + other + ' ' + str(self.temp_root) + '\r',
                            self.pending_done])
        self.assertIn('PM runtime error PM_SESSION_NOT_DEDICATED', ui.conversation.text)
        self.assertIn('/pm new [CWD] creates one', ui.conversation.text)
        # A transcript written after the binding, within S2's 20 s window, means live elsewhere.
        from llmtool import active_pm_inbox
        transcript = active_pm_inbox.claude_session_file(
            self.session_id, self.binding['cwd'], {'HOME': str(self.temp_root)})
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text('{}\n')
        ui, _ = self.drive([self.connect(), self.pending_done])
        self.assertIn('PM runtime error PM_SESSION_LIVE_ELSEWHERE', ui.conversation.text)
        self.assertIsNone(ui.pm_runtime)
        self.assertEqual(self.fakes, [])   # no claude process was started

    def test_permission_denied_reason_comes_from_the_stream(self):
        self.turns = [[_assistant({'type': 'tool_use', 'id': 't9', 'name': 'Bash', 'input': {}}),
                       _tool_result('t9', 'Permission denied: Bash is not allowed', True),
                       _assistant({'type': 'tool_use', 'id': 't10', 'name': 'Read', 'input': {}}),
                       _tool_result('t10', 'file content', False),
                       _result('done')]]
        ui, _ = self.drive([self.connect(), self.connected, 'run it\r', self.turn_done])
        text = ui.conversation.text
        self.assertIn('  · tool Bash: auto-denied (pm_runtime reason PERMISSION_DENIED', text)
        self.assertIn('  · tool Read: done', text)
        self.assertNotIn('file content', text)

    def test_same_send_key_is_replayed_not_resent(self):
        fixed = Mock(hex='k' * 32)
        self.turns = [[_delta('first answer '), _result('first answer')], [_result('never')]]
        with patch.object(chat_tui.uuid, 'uuid4', return_value=fixed):
            ui, _ = self.drive([self.connect(), self.connected, 'one\r', self.turn_done,
                                'two\r', self.turn_done])
        self.assertIn('not sent again: pm_runtime already has this message as turn', ui.conversation.text)
        self.assertIn('(COMPLETED)', ui.conversation.text)
        self.assertEqual(len(self.fakes[0].users()), 1)

    def test_quota_error_and_block_index(self):
        self.turns = [[_delta('part A ', 0), _delta('part B ', 1), _delta('end', 1),
                       _result('You have hit your limit', is_error=True, subtype='error')]]
        ui, _ = self.drive([self.connect(), self.connected, 'go\r', self.turn_done])
        text = ui.conversation.text
        self.assertIn('PM:\npart A', text)
        self.assertIn('PM:\npart B end', text)          # a new content block opens a new PM block
        self.assertIn('PM runtime error PM_QUOTA_EXHAUSTED; retry when (TUI guidance): the PM', text)
        self.assertIn('— PM turn ERROR', text)
        self.assertRegex(text, r'turn [0-9a-f]{8} event #\d+')

    def test_esc_enter_is_still_a_newline_when_no_turn_runs(self):
        ui, _ = self.drive([self.connect(), self.connected, 'a', '\x1b\r', 'b',
                            lambda ui: self.assertEqual(ui.input.text, 'a\nb')])
        self.assertEqual(self.fakes[0].users(), [])

    def test_connect_errors_show_code_and_recovery(self):
        self.observe.stop()
        with patch.object(self.pm_runtime.active_pm_inbox, 'observe_claude_session',
                          side_effect=chat_tui.LLMToolError('PRIVATE path detail')):
            ui, _ = self.drive([self.connect(), lambda ui: self.until(
                ui, lambda ui: not ui.pm_pending)])
        self.assertIn('PM not connected. PM runtime error PM_SESSION_INVALID', ui.conversation.text)
        self.assertNotIn('PRIVATE', ui.conversation.text)
        self.assertFalse(ui.pm_mode)
        self.observe.start()
        holder = self.make_runtime(self.session_id, str(self.temp_root)).open()   # another driver holds it
        try:
            ui, _ = self.drive([self.connect(), lambda ui: self.until(
                ui, lambda ui: not ui.pm_pending)])
        finally:
            holder.close()
        self.assertIn('PM runtime error PM_SESSION_BUSY; retry when (TUI guidance): another '
                      'llmtool driver holds this PM session', ui.conversation.text)
        self.assertIsNone(ui.pm_runtime)

    def test_process_exit_mid_turn_is_an_error_not_a_reply(self):
        self.turns = [[_delta('partial'), 'EXIT']]
        ui, _ = self.drive([self.connect(), self.connected, 'go\r', self.turn_done])
        text = ui.conversation.text
        self.assertIn('PM runtime error PM_PROCESS_EXITED', text)
        self.assertIn('— PM turn ERROR', text)
        self.assertEqual(ui.pm_state, 'error(PM_PROCESS_EXITED)')

    def test_history_disconnect_and_chat_is_never_the_pm(self):
        def history_shown(ui):
            return self.until(ui, lambda ui: '(history)' in ui.conversation.text)

        ui, _ = self.drive([self.connect(), self.connected, 'first\r', self.turn_done,
                            '/pm history\r', history_shown,
                            '/pm disconnect\r', lambda ui: self.until(ui, lambda ui: not ui.pm_pending),
                            lambda ui: self.assertFalse(ui.pm_mode),
                            'chat now\r', self.idle])
        text = ui.conversation.text
        self.assertIn('(history) ', text)
        self.assertIn('owner → PM:\nfirst', text)
        self.assertIn('[COMPLETED] tools: Read', text)
        self.assertIn('PM disconnected; chat mode', text)
        self.assertIn('assistant:\nreply', text)            # chat goes to the chat provider
        self.assertEqual([t['content'] for t in self.session.history], ['chat now', 'reply'])
        self.assertEqual(len(self.fakes[0].users()), 1)       # nothing more reached the PM
        self.assertTrue(self.fakes[0].closed_stdin)

    def test_run_closes_the_pm_runtime_even_when_the_app_fails(self):
        with create_pipe_input() as pipe:   # never the real stdin (CI has no terminal)
            ui = chat_tui.ChatTUI(self.session, input=pipe, output=DummyOutput(), pm_api=self.api)
        runtime = Mock()
        ui.pm_runtime = runtime
        ui.app.run = Mock(side_effect=RuntimeError('ui broke'))
        with self.assertRaises(RuntimeError):
            ui.run()
        runtime.close.assert_called_once()
        self.assertIsNone(ui.pm_runtime)


if __name__ == '__main__':
    unittest.main()
