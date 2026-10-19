"""A3.5 UX-1 full-screen chat: real widget interaction offline; no live provider."""
import asyncio
import copy
import io
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

try:
    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
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
                task = asyncio.ensure_future(ui.app.run_async())
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

    def test_ctrl_d_exits_only_with_empty_input(self):
        ui, result = self.drive(['abc', '\x04', lambda ui: self.assertFalse(ui.app.is_done),
                                 lambda ui: ui.input.buffer.reset(), '\x04'])
        self.assertEqual(result, 0)
        self.factory.assert_not_called()

    def test_enter_sends_and_renders_reply_with_status(self):
        self.client.complete.return_value = Reply('hello 測試 \U0001f642',
                                                  {'prompt_tokens': 2, 'completion_tokens': 3, 'total_tokens': 5})
        ui, result = self.drive(['問題 \U0001f642\r', self.idle,
                                 lambda ui: self.assertIn('tokens 2+3=5', ui.status_text())])
        self.assertEqual(result, 0)
        self.assertEqual(self.client.complete.call_count, 1)
        self.assertEqual(self.session.history[0]['content'], '問題 \U0001f642')
        self.assertIn('assistant:\nhello 測試 \U0001f642', ui.conversation.text)
        self.assertEqual(ui.input.text, '')

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
        self.factory.assert_not_called()

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
            self.assertIn('EXIT PENDING', ui.status_text())

        async def finish(ui):
            release.set()
            await self.idle(ui)
        ui, result = self.drive(['question\r', '\x03', pending, finish])
        self.assertEqual(result, 0)
        self.assertEqual(self.session.history[-1]['content'], 'late reply')

    def test_request_failure_is_sanitized_and_recoverable(self):
        self.client.complete.side_effect = [RuntimeError('PRIVATE stderr'), Reply('ok', {})]
        ui, _ = self.drive(['bad\r', self.idle,
                            lambda ui: self.assertIn('error (unclassified)', ui.status_text()),
                            'good\r', self.idle])
        self.assertIn('chat request failed; history unchanged', ui.conversation.text)
        self.assertNotIn('PRIVATE', ui.conversation.text + ui.status_text())
        self.assertEqual([t['content'] for t in self.session.history], ['good', 'ok'])

    def test_untrusted_output_controls_are_escaped(self):
        self.client.complete.return_value = Reply('x\x1b]52;c;secret\x07‮', {'model': 'm\x1b'})
        ui, _ = self.drive(['q\r', self.idle])
        self.assertIn('x\\x1b]52;c;secret\\x07\\u202e', ui.conversation.text)
        self.assertNotIn('\x1b', ui.conversation.text + ui.status_text())
        self.assertIn('reported UNKNOWN', ui.status_text())

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
