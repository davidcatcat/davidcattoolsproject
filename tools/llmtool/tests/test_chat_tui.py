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
from llmtool import chat_tui
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
                                  '/exit', '/quit', '/task submit', '/task show'])
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
            self.assertEqual(len(ui.menu_items), 10)
            rows = self.menu_rows(ui)
            self.assertEqual(len(rows), chat_tui.MENU_LIMIT + 1)
            self.assertIn('/help', rows[0])
            self.assertIn('5 more (0 above, 5 below)', rows[-1])

        def scrolled(ui):
            rows = self.menu_rows(ui)
            self.assertEqual(ui.menu_index, 6)
            self.assertNotIn('/help', ''.join(rows))
            self.assertIn('/exit', rows[chat_tui.MENU_LIMIT - 1])
            self.assertIn('5 more (2 above, 3 below)', rows[-1])

        def wrapped(ui):
            rows = self.menu_rows(ui)
            self.assertEqual(ui.menu_index, 9)
            self.assertIn('/task show', rows[chat_tui.MENU_LIMIT - 1])
            self.assertIn('(5 above, 0 below)', rows[-1])
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
            stdout.write('{"task_id":"' + 'a' * 32 + '","state":"PM_RESPONDED"}\n')
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
            stdout.write('{"task_id":"' + args.task_id + '","state":"PM_UNAVAILABLE"}\n')
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


if __name__ == '__main__':
    unittest.main()
