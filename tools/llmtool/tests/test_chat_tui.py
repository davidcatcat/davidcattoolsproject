"""A3.5 UX-1 full-screen chat: real widget interaction offline; no live provider."""
import asyncio
import copy
import io
import os
import re
import signal
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import review_trust_fixture
from llmtool import (chat_tui, chat_tui_pm, chat_tui_status, chat_tui_task, chat_tui_theme,
                     chat_tui_view)
from llmtool.chat import ChatSession
from llmtool.config import resolve_config
from llmtool.providers.base import ProviderCapabilities, Reply
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

OWNER_SLUG = 'davidcatcat/davidcattoolsproject'
OWNER_PR_URL = 'https://github.com/' + OWNER_SLUG + '/pull/'
_MODULE_FIXTURE_ACTIVE = False


def _reset_test_patches():
    # patch.stopall() also stops the module trust fixture. Re-arm it only while
    # this module's fixtures are active; layout/theme subclasses call this too.
    review_trust_fixture.stop_module()
    patch.stopall()
    if _MODULE_FIXTURE_ACTIVE:
        review_trust_fixture.start_module()


def setUpModule():
    global _MODULE_FIXTURE_ACTIVE
    _MODULE_FIXTURE_ACTIVE = True
    review_trust_fixture.start_module()


def tearDownModule():
    global _MODULE_FIXTURE_ACTIVE
    _MODULE_FIXTURE_ACTIVE = False
    review_trust_fixture.stop_module()


class TestPatchLifecycleTests(unittest.TestCase):
    def test_reset_does_not_rearm_trust_after_module_fixture(self):
        global _MODULE_FIXTURE_ACTIVE
        from llmtool import trust

        _MODULE_FIXTURE_ACTIVE = False
        try:
            _reset_test_patches()
            self.assertNotIsInstance(trust.load_trust, Mock)
        finally:
            _MODULE_FIXTURE_ACTIVE = True
            review_trust_fixture.start_module()


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


STREAMING = ProviderCapabilities(chat=True, large_context=False, reasoning_control=False,
                                 streaming=True, code_edit=False, shell=False, git=False)


class FakeStream:
    """A client that streams: the first chunk at once, the rest after gate is set."""

    CAPABILITIES = STREAMING

    def __init__(self, chunks, gate=None):
        self.chunks = chunks
        self.gate = gate
        self.closed = False
        self.yielded = []
        self.complete = Mock(side_effect=AssertionError('complete() must not be used'))

    def stream(self, messages):
        try:
            for index, chunk in enumerate(self.chunks):
                if index == 1 and self.gate is not None:
                    self.gate.wait(5)
                self.yielded.append(chunk)
                yield chunk
        finally:
            self.closed = True
        return {'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 3}


def lone_escape(ui):
    """Feed a lone Esc and the key-processor timeout flush, as a terminal would."""
    from prompt_toolkit.key_binding.key_processor import _Flush as timeout_flush
    processor = ui.app.key_processor
    with set_app(ui.app):
        processor.feed(KeyPress(Keys.Escape, '\x1b'))
        processor.process_keys()
        processor.feed(timeout_flush)
        processor.process_keys()


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
        self.addCleanup(_reset_test_patches)
        # W56d: the TUI settings file and the PM background read never reach the real HOME;
        # tests that exercise the background read stop this patch.
        patch('llmtool.chat_tui_theme.settings_path',
              return_value=self.temp_root / 'tui-settings.json').start()
        self.background_tick = patch.object(chat_tui.ChatTUI, '_background_tick',
                                            lambda ui: None)
        self.background_tick.start()
        self.background_read_patch = patch.object(chat_tui_status, 'read_background',
                                                  return_value=[])
        self.background_read_patch.start()
        patch.object(chat_tui_status, 'read_attention', return_value=None).start()
        # W57: Task mode is behind the developer flag; these tests exercise it, and
        # NonDevTaskTests checks the default without the flag.
        patch.dict(os.environ, {chat_tui.DEV_ENV: '1'}).start()

    def test_attention_reader_is_patched_in_tui_base(self):
        self.assertTrue(hasattr(chat_tui_status.read_attention, 'assert_not_called'))


class FinishRedrawTests(Base):
    """_finish invalidates the app so the status row leaves working immediately."""

    def test_finish_invalidates_app(self):
        with create_pipe_input() as pipe:
            ui = chat_tui.ChatTUI(self.session, input=pipe, output=DummyOutput())
            ui.app.invalidate = Mock()
            ui._finish(lambda: None)
            ui.app.invalidate.assert_called_once()
            self.assertFalse(ui.busy)


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

    def drive(self, steps, size=None, dev=None):
        output = DummyOutput()
        if size is not None:
            output.get_size = lambda: size[0]
        with create_pipe_input() as pipe:
            ui = chat_tui.ChatTUI(self.session, input=pipe, output=output, dev=dev)
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

    @staticmethod
    async def until(ui, condition, what='condition'):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if condition(ui):
                return
            await asyncio.sleep(0.01)
        raise AssertionError(what + ' not reached')

    def test_starts_without_provider_call_and_exits_cleanly(self):
        ui, result = self.drive([])
        self.assertEqual(result, 0)
        self.assertTrue(ui.conversation.text.startswith('LLMTool ' + chat_tui.__version__))
        self.assertIn('interactive chat (full-screen)', ui.conversation.text)
        self.assertIn('History stays in memory.', ui.conversation.text)
        self.assertIn('provider: first', ui.conversation.text)
        self.factory.assert_not_called()

    def test_opening_banner_has_version_and_optional_source_revision(self):
        with patch.object(chat_tui, '_source_revision', return_value=None):
            ui, _ = self.drive([])
        self.assertEqual(ui.conversation.text.splitlines()[0],
                         'LLMTool ' + chat_tui.__version__ + ' interactive chat (full-screen)')
        with patch.object(chat_tui, '_source_revision', return_value='12345678'):
            ui, _ = self.drive([])
        self.assertEqual(ui.conversation.text.splitlines()[0],
                         'LLMTool ' + chat_tui.__version__
                         + ' (12345678) interactive chat (full-screen)')
        self.assertNotIn(str(Path(__file__).resolve().parents[2]), ui.conversation.text)

    def test_source_revision_reads_git_metadata_without_running_a_process(self):
        revision = '1234567890abcdef1234567890abcdef12345678'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'source'
            package = root / 'tools' / 'llmtool' / 'llmtool'
            package.mkdir(parents=True)
            module = package / 'chat_tui.py'
            module.touch()
            git = root / '.git'
            git.mkdir()
            (git / 'HEAD').write_text('ref: refs/heads/main\n')
            ref = git / 'refs' / 'heads' / 'main'
            ref.parent.mkdir(parents=True)
            ref.write_text(revision + '\n')
            with patch('subprocess.run', side_effect=AssertionError('must not run Git')):
                self.assertEqual(chat_tui._source_revision(module), '12345678')

            ref.unlink()
            (git / 'packed-refs').write_text('# pack-refs\n' + revision + ' refs/heads/main\n')
            with patch('subprocess.run', side_effect=AssertionError('must not run Git')):
                self.assertEqual(chat_tui._source_revision(module), '12345678')

            (git / 'HEAD').unlink()
            (git / 'packed-refs').unlink()
            (git / 'refs' / 'heads' / 'main').unlink(missing_ok=True)
            (git / 'refs' / 'heads').rmdir()
            (git / 'refs').rmdir()
            git.rmdir()
            worktree_git = Path(temp) / 'worktree-git'
            worktree_git.mkdir()
            (worktree_git / 'HEAD').write_text(revision + '\n')
            (root / '.git').write_text('gitdir: ' + str(worktree_git) + '\n')
            self.assertEqual(chat_tui._source_revision(module), '12345678')

            (root / '.git').unlink()
            (root / '.git').symlink_to(worktree_git, target_is_directory=True)
            self.assertIsNone(chat_tui._source_revision(module))

    def test_source_revision_is_omitted_when_git_metadata_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'source'
            package = root / 'llmtool'
            package.mkdir(parents=True)
            module = package / 'chat_tui.py'
            module.touch()
            self.assertIsNone(chat_tui._source_revision(module))

    def test_pm_ghost_hints_render_after_the_cursor_without_editing_input(self):
        seen = {}

        def screen_text(ui):
            screen = ui.app.renderer.last_rendered_screen
            if screen is None:
                return ''
            size = ui.app.output.get_size()
            return "".join(screen.data_buffer[y][x].char
                           for y in range(size.rows) for x in range(size.columns))

        def capture(key, draft, hint=None):
            # Wait for the typed draft, then a render after it, then the hint (if any).
            # No fixed sleep decides what the screen shows.
            async def step(ui):
                await self.until(ui, lambda ui: ui.input.text == draft, 'draft ' + repr(draft))
                start = ui.app.render_counter
                await self.until(ui, lambda ui: ui.app.render_counter > start,
                                 'render after ' + repr(draft))
                if hint is not None:
                    await self.until(ui, lambda ui: hint in screen_text(ui), 'hint ' + repr(hint))
                seen[key] = screen_text(ui)
                seen[key + "_draft"] = ui.input.text
            return step

        ui, _ = self.drive(['/pm ', capture('subcommands', '/pm ', 'new | list [--all] | use N|SESSION'),
                            'new ', capture('new', '/pm new ',
                                            '[--runtime claude|codex] [--account NAME] [CWD]'),
                            '--runtime', capture('argument', '/pm new --runtime')])
        self.assertIn('new | list [--all] | use N|SESSION', seen['subcommands'])
        self.assertIn('[--runtime claude|codex] [--account NAME] [CWD]', seen['new'])
        self.assertNotIn('[--runtime claude|codex]', seen['argument'])
        self.assertEqual(seen['subcommands_draft'], '/pm ')
        self.assertEqual(seen['new_draft'], '/pm new ')
        self.assertEqual(seen['argument_draft'], '/pm new --runtime')
        self.assertEqual(ui.input.text, '/pm new --runtime')

    def test_settings_ghost_hints_render_without_editing_input(self):
        seen = {}

        def screen_text(ui):
            screen = ui.app.renderer.last_rendered_screen
            if screen is None:
                return ''
            size = ui.app.output.get_size()
            return "".join(screen.data_buffer[y][x].char
                           for y in range(size.rows) for x in range(size.columns))

        def capture(key, draft, hint=None):
            # Wait for the typed draft, then a render after it, then the hint (if any).
            # No fixed sleep decides what the screen shows.
            async def step(ui):
                await self.until(ui, lambda ui: ui.input.text == draft, 'draft ' + repr(draft))
                start = ui.app.render_counter
                await self.until(ui, lambda ui: ui.app.render_counter > start,
                                 'render after ' + repr(draft))
                if hint is not None:
                    await self.until(ui, lambda ui: hint in screen_text(ui), 'hint ' + repr(hint))
                seen[key] = screen_text(ui)
                seen[key + "_draft"] = ui.input.text
            return step

        ui, _ = self.drive(['/settings', capture('command', '/settings', 'auto-resume on|off'),
                            ' ', capture('command_space', '/settings ', 'auto-resume on|off'),
                            'auto-resume', capture('setting', '/settings auto-resume', 'on|off'),
                            ' ', capture('setting_space', '/settings auto-resume ', 'on|off'),
                            'on', capture('value', '/settings auto-resume on')])
        self.assertIn('auto-resume on|off', seen['command'])
        self.assertIn('auto-resume on|off', seen['command_space'])
        self.assertIn('on|off', seen['setting'])
        self.assertIn('on|off', seen['setting_space'])
        self.assertNotIn('on|off', seen['value'])
        self.assertEqual(seen['command_draft'], '/settings')
        self.assertEqual(seen['command_space_draft'], '/settings ')
        self.assertEqual(seen['setting_draft'], '/settings auto-resume')
        self.assertEqual(seen['setting_space_draft'], '/settings auto-resume ')
        self.assertEqual(seen['value_draft'], '/settings auto-resume on')

    def test_saved_history_is_identified_in_opening_text(self):
        history_path = self.temp_root / '.local/state/llmtool/chat/history.json'
        self.session = ChatSession(self.session.config, history_path=history_path)
        ui, result = self.drive([])
        self.assertEqual(result, 0)
        self.assertIn('History is stored locally.', ui.conversation.text)
        self.assertNotIn('History stays in memory.', ui.conversation.text)

    def test_history_notice_is_shown_once_as_llmtool_line(self):
        self.session.history_notice = 'chat history was unavailable; continuing without saved history'
        ui, result = self.drive([])
        self.assertEqual(result, 0)
        self.assertEqual(ui.conversation.text.count('llmtool: chat history was unavailable; continuing without saved history'), 1)
        self.assertIsNone(self.session.history_notice)

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
        self.assertEqual(ui.input.window.render_info.window_height, 4)
        self.assertEqual(ui.status_window.render_info.window_height, 1)
        status = ui.status_text()
        self.assertNotIn('\n', status)
        self.assertLessEqual(get_cwidth(status), 99)

    def test_shift_enter_terminal_sequences_insert_and_enter_sends_full_draft(self):
        def first_line_inserted(ui):
            self.assertEqual(ui.input.text, 'first\n')
            self.assertEqual(self.client.complete.call_count, 0)

        def second_line_inserted(ui):
            self.assertEqual(ui.input.text, 'first\nsecond\n')
            self.assertEqual(self.client.complete.call_count, 0)

        # CSI-u and modifyOtherKeys are sent by terminals configured to report
        # Shift+Enter; drive() feeds both encodings through the real input parser.
        ui, _ = self.drive(['first\x1b[13;2u', first_line_inserted,
                            'second\x1b[27;2;13~', second_line_inserted,
                            'third\r', self.idle])
        sent = [call.args[0][-1]['content'] for call in self.client.complete.call_args_list]
        self.assertEqual(sent, ['first\nsecond\nthird'])
        self.assertEqual(ui.input.text, '')

    def test_up_down_move_within_multiline_draft(self):
        def inspect_movement(ui):
            buffer = ui.input.buffer
            buffer.text = 'first line\nsecond line'
            buffer.cursor_position = len(buffer.text)
            with set_app(ui.app):
                ui.app.key_processor.feed(KeyPress(Keys.Up))
                ui.app.key_processor.process_keys()
            self.assertEqual(buffer.document.current_line, 'first line')
            self.assertEqual(buffer.text, 'first line\nsecond line')
            with set_app(ui.app):
                ui.app.key_processor.feed(KeyPress(Keys.Down))
                ui.app.key_processor.process_keys()
            self.assertEqual(buffer.document.current_line, 'second line')
            self.assertEqual(buffer.text, 'first line\nsecond line')

        self.drive([inspect_movement])

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
            ui, result = self.drive(["問題\r", "\x03", "\x03", self.idle])
        self.assertEqual(result, 0)
        self.assertTrue(ui.quit_pending)
        self.assertIn("assistant:\n中文回覆", ui.conversation.text)
        self.assertEqual([turn["content"] for turn in self.session.history], ["問題", "中文回覆"])
        # The first Ctrl-C asked for an interrupt; the one-piece reply is kept, marked.
        self.assertEqual(self.session.state, "interrupted")
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
                        deadline = time.monotonic() + 30
                        while not pid_file.exists() and time.monotonic() < deadline:
                            await asyncio.sleep(min(0.01, max(0, deadline - time.monotonic())))
                        self.assertTrue(pid_file.exists(), "Codex child PID file was not ready within 30 seconds")
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
        import subprocess

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

        real_popen = subprocess.Popen
        cli_spawns = []

        def track_cli_spawn(*args, **kwargs):
            command = args[0] if args else kwargs.get("args")
            if command and os.fspath(command[0]) == str(executable):
                cli_spawns.append(command)
            # repo_info uses subprocess.run for its best-effort git lookup. Let that
            # unrelated child run normally while observing only this provider's CLI.
            return real_popen(*args, **kwargs)

        with patch.object(CodexCLIProvider, "validate_ready", side_effect=pause_before_lifecycle), \
                patch("llmtool.providers.codex_cli.shutil.which", return_value=str(executable)), \
                patch("llmtool.providers.codex_cli.subprocess.Popen", side_effect=track_cli_spawn):
            ui, result = self.drive(["問題\r", signal_before_registration, self.idle])
        self.assertEqual(result, 0)
        self.assertTrue(ui.quit_pending)
        self.assertEqual(cli_spawns, [])

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
        self.assertEqual(labels, ['/pm connect', '/pm new', '/pm', '/pm disconnect',
                                  '/pm history', '/pm queue', '/help', '/status',
                                  '/provider', '/skill',
                                  '/clear', '/history', '/exit', '/quit', '/task submit',
                                  '/task show', '/task mode', '/task list', '/task events',
                                  '/theme', '/links', '/settings', '/pm list', '/pm use',
                                  '/pm accounts', '/logs', '/attention'])
        self.assertEqual([c[0] for c in chat_tui.slash_matches('/s')][:3],
                         ['/status', '/skill', '/settings'])
        self.assertEqual([c[0] for c in chat_tui.slash_matches('/p')][:3],
                         ['/pm connect', '/pm new', '/pm'])
        self.assertEqual([c[0] for c in chat_tui.slash_matches('/ory')][:2],
                         ['/pm history', '/history'])
        self.assertIn('/task show', [c[0] for c in chat_tui.slash_matches('/show')])
        self.assertEqual([c[0] for c in chat_tui.slash_matches('/', dev=False)][:5],
                         ['/pm connect', '/pm new', '/pm', '/pm disconnect', '/pm history'])
        self.assertEqual(chat_tui.slash_matches('hello'), [])
        self.assertEqual(chat_tui.slash_matches('/nope'), [])
        self.assertEqual(chat_tui.slash_matches('/s\nx'), [])

    def test_picker_completes_draft_without_executing(self):
        def open_menu(ui):
            self.assertEqual([c[0] for c in ui.menu_items],
                             ['/status', '/skill', '/settings'])
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

    def test_bare_pm_enter_does_not_become_pm_connect(self):
        # Bare /pm selects its exact command match so Enter runs it without expanding.
        def check_menu(ui):
            self.assertEqual(ui.menu_items[ui.menu_index][0], '/pm')

        ui, _ = self.drive(['/pm', check_menu, '\r',
                            lambda ui: self.assertEqual(ui.input.text, '')])
        self.assertIn('PM: not connected', ui.conversation.text)
        self.assertNotEqual(ui.input.text, '/pm connect')
        self.assertNotIn('> /pm connect', ui.conversation.text)

    def test_pm_down_enter_completes_moved_to_entry(self):
        # Moving down selects the next command and Enter completes it into the draft.
        ui, _ = self.drive(['/pm', '\x1b[B', '\r',
                            lambda ui: self.assertEqual(ui.input.text, '/pm disconnect'),
                            lambda ui: self.assertEqual(ui.menu_items, []),
                            lambda ui: self.assertNotIn('> /pm disconnect', ui.conversation.text)])

    def test_prefix_draft_keeps_first_match_selected(self):
        # A prefix draft keeps the first matching candidate selected.
        def check_menu(ui):
            self.assertEqual(ui.menu_index, 0)
            self.assertEqual(ui.menu_items[0][0], '/status')

        ui, _ = self.drive(['/s', check_menu, '\r',
                            lambda ui: self.assertEqual(ui.input.text, '/status'),
                            lambda ui: self.assertEqual(ui.menu_items, []),
                            lambda ui: self.assertNotIn('> /status', ui.conversation.text)])

    def test_tab_completes_unique_slash_command_and_argument_spacing(self):
        ui, _ = self.drive(['/pm c', '\t',
                            lambda ui: self.assertEqual(ui.input.text, '/pm connect'),
                            lambda ui: self.assertEqual(ui.menu_items, []),
                            '\x15', '/provider', '\t',
                            lambda ui: self.assertEqual(ui.input.text, '/provider ')])
        self.assertEqual(ui.menu_items, [])
        self.factory.assert_not_called()

    def test_settings_exact_command_still_runs_from_the_draft(self):
        ui, _ = self.drive(['/settings\r',
                            lambda ui: self.assertEqual(ui.input.text, '')])
        self.assertIn('Settings: auto-resume', ui.conversation.text)

    def test_settings_with_trailing_space_runs_from_the_draft(self):
        ui, _ = self.drive(['/settings ', '\r',
                            lambda ui: self.assertEqual(ui.input.text, '')])
        self.assertIn('Settings: auto-resume', ui.conversation.text)

    def test_tab_keeps_settings_command_draft_and_closes_picker(self):
        ui, _ = self.drive(['/settings', '\t',
                            lambda ui: self.assertEqual(ui.input.text, '/settings '),
                            lambda ui: self.assertEqual(ui.menu_items, [])])
        self.factory.assert_not_called()

    def test_tab_keeps_settings_prefix_draft_and_closes_picker(self):
        ui, _ = self.drive(['/sett', '\t',
                            lambda ui: self.assertEqual(ui.input.text, '/settings '),
                            lambda ui: self.assertEqual(ui.menu_items, [])])
        self.factory.assert_not_called()

    def test_tab_extends_ambiguous_prefix_and_lists_candidates(self):
        def candidates(ui):
            self.assertEqual(ui.input.text, '/task ')
            self.assertEqual([item[0] for item in ui.menu_items],
                             ['/task submit', '/task show', '/task mode',
                              '/task list', '/task events'])
            self.assertEqual(ui.app.output.get_size().columns, 50)
            self.assertTrue(self.menu_rows(ui))

        self.drive(['/ta', '\t', candidates], size=[Size(rows=24, columns=50)], dev=True)
        self.factory.assert_not_called()

    def test_tab_does_not_offer_dev_commands_without_dev_mode(self):
        def unchanged(ui):
            self.assertEqual(ui.input.text, '/ta')
            self.assertEqual(ui.menu_items, [])

        self.drive(['/ta', '\t', unchanged], dev=False)
        self.factory.assert_not_called()

    def test_tab_without_match_keeps_slash_draft_and_non_slash_tab_behavior(self):
        ui, _ = self.drive(['/nope', '\t',
                            lambda ui: self.assertEqual(ui.input.text, '/nope'),
                            lambda ui: self.assertTrue(ui.app.layout.has_focus(ui.input))])
        self.assertEqual(ui.menu_items, [])

        ui, _ = self.drive(['hello', '\t',
                            lambda ui: self.assertEqual(ui.input.text, 'hello'),
                            lambda ui: self.assertFalse(ui.app.layout.has_focus(ui.input))])
        self.assertEqual(ui.menu_items, [])

        ui, _ = self.drive(['cat', '\t',
                            lambda ui: self.assertEqual(ui.input.text, 'cat'),
                            lambda ui: self.assertFalse(ui.app.layout.has_focus(ui.input))])
        self.assertEqual(ui.menu_items, [])
        self.factory.assert_not_called()

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
                self.assertEqual([c[0] for c in ui.menu_items], ['/help', '/theme'])
                processor.feed(timeout_flush)
                processor.process_keys()
            self.assertEqual(ui.menu_items, [])
            self.assertEqual(ui.input.text, '/he')
            self.assertEqual(ui.app.timeoutlen, ui._default_timeoutlen)
        self.drive(['/he', lone_escape])
        self.factory.assert_not_called()

    def test_picker_esc_enter_inserts_newline_instead_of_closing_and_submitting(self):
        def open_menu(ui):
            self.assertEqual([c[0] for c in ui.menu_items],
                             ['/status', '/skill', '/settings'])
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

    def test_help_lists_pm_first_and_marks_task_submit_as_dev_test_only(self):
        # Owner decision 022:A (#525): /pm is the product entry; /task submit stays, labelled.
        help_text = chat_tui.key_help(True)
        self.assertIn('/attention            Show what needs attention', help_text)
        self.assertLess(help_text.index('Talk to the PM'), help_text.index('/task submit'))
        self.assertLess(help_text.index('/pm new'), help_text.index('/task submit'))
        self.assertIn('開發測試用：只登記，不會派工', help_text.split('/task submit', 1)[1][:200])
        self.assertIn('Esc (during a reply)', help_text)
        self.assertNotIn('A request in progress cannot be cancelled', help_text)
        picker = chat_tui.SLASH_COMMANDS
        labels = [c[0] for c in picker]
        self.assertLess(labels.index('/pm connect'), labels.index('/task submit'))
        self.assertIn('開發測試用：只登記，不會派工', dict((c[0], c[2]) for c in picker)['/task submit'])
        self.assertEqual(dict((c[0], c[1]) for c in picker)['/task submit'], '/task submit --provider ')
        ui, _ = self.drive(['/help\r'])
        shown = ui.conversation.text.split('> /help', 1)[1]
        self.assertIn('  /attention              Show what needs attention', shown)
        self.assertLess(shown.index('/pm new'), shown.index('/status'))
        self.assertIn('Talk to the PM', shown)
        self.assertIn('Type / to choose a command', shown)
        self.assertIn('/task ...', shown)
        self.assertNotIn('Ctrl-J', shown)
        self.factory.assert_not_called()

    def test_help_points_to_pm_setup_guide_before_chat_heading(self):
        ui, _ = self.drive(['/help\r'])
        shown = ui.conversation.text.split('> /help', 1)[1]
        guide = 'docs/guides/llmtool-pm-setup-zh-tw.md'
        self.assertIn(guide, shown)
        self.assertLess(shown.index(guide), shown.index('Chat'))

    def test_key_help_points_to_pm_setup_guide(self):
        help_text = chat_tui.key_help(False)
        guide = 'docs/guides/llmtool-pm-setup-zh-tw.md'
        self.assertIn(guide, help_text)
        self.assertGreater(help_text.index(guide), help_text.index('/pm queue'))

    def test_picker_enter_on_exact_typed_command_submits_like_no_picker(self):
        # Rule: Enter only completes a draft that differs from the chosen suggestion and
        # never runs it; a draft the user already typed in full runs as with no picker.
        def completed_not_run(ui):
            self.assertEqual(ui.input.text, '/status')
            self.assertNotIn('connectivity', ui.conversation.text)
        ui, _ = self.drive(['/s', '\r', completed_not_run, '\r', self.idle])
        self.assertIn('連線：未檢查', ui.conversation.text)
        ui, _ = self.drive(['/status\r', '/zzz\r', 'plain chat\r', self.idle])
        self.assertIn('連線：未檢查', ui.conversation.text)
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

    def test_tab_completing_unique_command_closes_picker_and_restores_esc_timeout(self):
        def menu_open(ui):
            self.assertTrue(ui.menu_items)
            self.assertEqual(ui.app.timeoutlen, chat_tui.ESC_SEQUENCE_TIMEOUT)

        def completed(ui):
            self.assertEqual(ui.input.text, '/pm connect')
            self.assertEqual(ui.menu_items, [])
            self.assertEqual(ui.app.timeoutlen, ui._default_timeoutlen)
        self.drive(['/pm c', menu_open, '\t', completed])
        self.factory.assert_not_called()

    def test_picker_scrolls_and_counts_hidden_entries(self):
        def first_page(ui):
            self.assertEqual(len(ui.menu_items), 27)
            rows = self.menu_rows(ui)
            self.assertEqual(len(rows), chat_tui.MENU_LIMIT + 1)
            self.assertIn('/pm connect', rows[0])
            self.assertIn('22 more (0 above, 22 below)', rows[-1])

        def scrolled(ui):
            rows = self.menu_rows(ui)
            self.assertEqual(ui.menu_index, 6)
            self.assertNotIn('/pm connect', ''.join(rows))
            self.assertIn('/help', rows[chat_tui.MENU_LIMIT - 1])
            self.assertIn('22 more (2 above, 20 below)', rows[-1])

        def wrapped(ui):
            rows = self.menu_rows(ui)
            self.assertEqual(ui.menu_index, 26)
            self.assertIn('/attention', rows[chat_tui.MENU_LIMIT - 1])
            self.assertIn('(22 above, 0 below)', rows[-1])
        ui, _ = self.drive(['/', first_page, '\x1b[B' * 6, scrolled, '\r',
                            lambda ui: self.assertEqual(ui.input.text, '/help'),
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
        self.assertEqual(ui.conversation.text.count('連線：未檢查'), 1)
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
        self.assertEqual(ui.conversation.text.count('連線：未檢查'), 1)
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
        self.assertEqual(ui.conversation.text.count('連線：未檢查'), 1)
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
        self.assertEqual(widths[80], 'p:first | m:test | exit pending | t:UNKNOWN | 最新 | F6 滑鼠:開 | F1')
        # QA O2: the mouse state outlives the F1 hint, usage, model and provider fields.
        self.assertEqual(widths[50], 'p:first | exit pending | 最新 | F6 滑鼠:開')
        self.assertEqual(widths[40], 'exit pending | 最新 | F6 滑鼠:開')
        self.assertEqual(widths[30], 'exit pending | F6 滑鼠:開')
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
            self.assertIn('換行 = Shift+Enter', ui.help.text)
            self.assertIn('Shift+Enter', ui.help.text)
            self.assertIn('Option+Enter', ui.help.text)
            self.assertIn('terminal must send it', ui.help.text)
            self.assertIn('Ctrl-J', ui.help.text)
            self.assertIn('macOS Terminal.app sends plain Enter for Shift+Enter', ui.help.text)
            self.assertIn(r'Send Text \033[13;2u', ui.help.text)
        ui, _ = self.drive(['\t', '\x1bOP', help_shown,
                            '\t', lambda ui: self.assertTrue(ui.app.layout.has_focus(ui.help)),
                            '\x1bOP', lambda ui: self.assertFalse(ui.show_help),
                            lambda ui: self.assertTrue(ui.app.layout.has_focus(ui.input)),
                            '/help\r'])
        self.assertIn('/provider [PROFILE]', ui.conversation.text)
        self.assertIn('/task ...', ui.conversation.text)
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

        def interrupting(ui):
            self.assertFalse(ui.app.is_done)
            self.assertFalse(ui.quit_pending)
            self.assertTrue(ui.send_interrupted)
            self.assertIn('interrupt requested', ui.conversation.text)
            self.assertIn('Ctrl-C again exits', ui.conversation.text)
            self.assertIn('interrupting', ui.status_text())

        def pending(ui):
            self.assertFalse(ui.app.is_done)
            self.assertTrue(ui.quit_pending)
            self.assertIn('exit requested', ui.conversation.text)
            self.assertIn('exit pending', ui.status_text())

        async def finish(ui):
            release.set()
            await self.idle(ui)
        ui, result = self.drive(['question\r', '\x03', interrupting, '\x03', pending, finish])
        self.assertEqual(result, 0)
        self.assertEqual(self.session.history[-1]['content'], 'late reply')
        self.assertIn('assistant:\nlate reply', ui.conversation.text)
        self.assertIn('answered in one piece', ui.conversation.text)

    def test_escape_interrupts_a_streaming_reply_and_keeps_the_partial_text(self):
        # Owner #304: Esc during a reply must stop it, like the PM pane does.
        gate = threading.Event()
        fake = FakeStream(['Hello', ' world', ' never shown'], gate)
        self.factory.return_value = fake

        async def streaming(ui):
            await self.until(ui, lambda ui: ui.stream_raw is not None)
            await self.until(ui, lambda ui: ui.app.timeoutlen == chat_tui.ESC_SEQUENCE_TIMEOUT,
                             'Esc timeout while streaming')
            self.assertIn('assistant:\nHello', ui.conversation.text)
            self.assertEqual(ui.app.timeoutlen, chat_tui.ESC_SEQUENCE_TIMEOUT)
            self.assertIn('working', ui.status_text())

        async def escaped(ui):
            self.assertTrue(ui.send_interrupted)
            self.assertIn('interrupting', ui.status_text())
            gate.set()                      # one more chunk arrives, then the stream stops
            await self.idle(ui)

        def after(ui):
            self.assertEqual(ui.input.text, '')
            self.assertFalse(ui.busy)
            self.assertIn('assistant:\nHello world\n\nllmtool: reply interrupted after the text above',
                          ui.conversation.text)
            self.assertNotIn('never shown', ui.conversation.text)
            self.assertIn('interrupted', ui.status_text())
            self.assertGreater(ui._default_timeoutlen, chat_tui.ESC_SEQUENCE_TIMEOUT)

        ui, result = self.drive(['question\r', streaming, lone_escape, escaped, after,
                                 lambda ui: self.assertEqual(ui.app.timeoutlen, ui._default_timeoutlen),
                                 '/status\r'])
        self.assertEqual(result, 0)
        self.assertEqual([t['content'] for t in self.session.history], ['question', 'Hello world'])
        self.assertEqual(self.session.state, 'interrupted')
        self.assertTrue(self.session.last_interrupted)
        self.assertTrue(fake.closed)
        self.assertEqual(fake.yielded, ['Hello', ' world'])
        self.assertIn('狀態：已中斷', ui.conversation.text)
        fake.complete.assert_not_called()

    def test_escape_interrupts_a_chat_reply_with_focus_in_the_conversation_pane(self):
        # W52 B2: Tab to read the reply, then Esc: it interrupts and keeps a selection.
        copies = []
        patch('llmtool.chat_tui_view.copy_to_clipboard', side_effect=copies.append).start()
        gate = threading.Event()
        fake = FakeStream(['Hello', ' world', ' never shown'], gate)
        self.factory.return_value = fake

        async def streaming(ui):
            await self.until(ui, lambda ui: ui.stream_raw is not None)

        def reading(ui):
            self.assertTrue(ui.app.layout.has_focus(ui.conversation))
            ui.view.anchor, ui.view.head = 0, 6     # a selection W42 keeps

        async def escaped(ui):
            self.assertTrue(ui.send_interrupted)
            self.assertEqual(ui.view.selected_range(), (0, 6))
            gate.set()
            await self.idle(ui)

        ui, result = self.drive(['question\r', streaming, '\t', reading, lone_escape, escaped,
                                 lambda ui: ui.view.clear_selection()])
        self.assertEqual(copies, [])
        self.assertEqual(result, 0)
        self.assertNotIn('never shown', ui.conversation.text)
        self.assertEqual(self.session.state, 'interrupted')

    def test_escape_in_the_conversation_pane_without_a_reply_keeps_the_selection(self):
        copies = []
        patch('llmtool.chat_tui_view.copy_to_clipboard', side_effect=copies.append).start()
        def reading(ui):
            self.assertTrue(ui.app.layout.has_focus(ui.conversation))
            ui.view.anchor, ui.view.head = 0, 6

        def kept(ui):
            self.assertEqual(ui.view.selected_range(), (0, 6))
            self.assertFalse(ui.send_interrupted)

        ui, result = self.drive(['\t', reading, lone_escape, kept,
                                 lambda ui: ui.view.clear_selection()])
        self.assertEqual(result, 0)
        self.assertEqual(copies, [])
        self.factory.assert_not_called()

    def test_ctrl_c_interrupts_a_streaming_reply_and_a_second_ctrl_c_exits(self):
        gates = [threading.Event(), threading.Event()]
        fakes = [FakeStream(['one', ' two', ' three'], gates[0]),
                 FakeStream(['uno', ' dos', ' tres'], gates[1])]
        self.factory.side_effect = fakes

        async def streaming(ui):
            await self.until(ui, lambda ui: ui.stream_raw is not None)

        async def first_ctrl_c(ui):
            ui.request_quit()
            self.assertFalse(ui.app.is_done)
            self.assertFalse(ui.quit_pending)
            self.assertIn('Ctrl-C again exits', ui.conversation.text)
            gates[0].set()
            await self.idle(ui)
            self.assertIn('reply interrupted after the text above', ui.conversation.text)
            self.assertFalse(ui.quit_pending)

        def second_ctrl_c(ui):
            ui.request_quit()
            ui.request_quit()
            self.assertTrue(ui.quit_pending)
            self.assertFalse(ui.app.is_done)
            gates[1].set()

        ui, result = self.drive(['first\r', streaming, first_ctrl_c,
                                 'second\r', streaming, second_ctrl_c, self.idle])
        self.assertEqual(result, 0)
        self.assertTrue(ui.quit_pending)
        self.assertEqual([t['content'] for t in self.session.history],
                         ['first', 'one two', 'second', 'uno dos'])
        self.assertEqual(ui.conversation.text.count('assistant:\n'), 2)
        # The Ctrl-C notices went above the reply block, which stayed in one piece.
        self.assertIn('llmtool: Ctrl-C again exits\n\nassistant:\none two\n\nllmtool: reply interrupted',
                      ui.conversation.text)
        self.assertTrue(all(fake.closed for fake in fakes))

    def test_streaming_reply_completes_normally_without_interrupt(self):
        fake = FakeStream(['Hel', 'lo ', '測試'])
        self.factory.return_value = fake
        ui, result = self.drive(['q\r', self.idle,
                                 lambda ui: self.assertIn('t:1+2=3', ui.status_text())])
        self.assertEqual(result, 0)
        self.assertEqual(ui.conversation.text.count('assistant:'), 1)
        self.assertIn('assistant:\nHello 測試', ui.conversation.text)
        self.assertNotIn('interrupted', ui.conversation.text)
        self.assertEqual(self.session.history[-1]['content'], 'Hello 測試')
        self.assertEqual(self.session.state, 'idle')
        self.assertFalse(self.session.last_interrupted)

    def test_escape_before_any_streamed_text_leaves_history_unchanged(self):
        gate = threading.Event()

        class SlowStart(FakeStream):
            def stream(self, messages):
                gate.wait(5)                  # blocks before the first chunk is requested
                return super().stream(messages)

        fake = SlowStart(['late'])
        self.factory.return_value = fake

        async def busy(ui):
            await self.until(ui, lambda ui: ui.busy)
            lone_escape(ui)
            self.assertIn('interrupt requested', ui.conversation.text)
            gate.set()
            await self.idle(ui)

        ui, result = self.drive(['q\r', busy])
        self.assertEqual(result, 0)
        self.assertIn('llmtool: reply interrupted before any text arrived; history unchanged',
                      ui.conversation.text)
        self.assertNotIn('assistant:', ui.conversation.text)
        self.assertEqual(self.session.history, [])
        self.assertEqual(self.session.state, 'interrupted')
        self.assertEqual(fake.yielded, [])      # cancel seen before the first chunk was asked for

    def test_escape_during_a_one_piece_reply_keeps_it_marked_interrupted(self):
        # All current providers answer in one piece: Esc is acknowledged at once, the
        # reply is kept when it lands, and the prompt is free again.
        release = threading.Event()
        self.client.complete.side_effect = lambda messages: (release.wait(5), Reply('whole', {}))[1]

        async def busy(ui):
            await self.until(ui, lambda ui: ui.busy)
            await self.until(ui, lambda ui: ui.app.timeoutlen == chat_tui.ESC_SEQUENCE_TIMEOUT)
            lone_escape(ui)
            self.assertIn('interrupt requested', ui.conversation.text)
            self.assertIn('interrupting', ui.status_text())
            lone_escape(ui)              # a repeat is a no-op
            self.assertEqual(ui.conversation.text.count('interrupt requested'), 1)
            release.set()
            await self.idle(ui)

        ui, result = self.drive(['q\r', busy, 'next\r', self.idle])
        self.assertEqual(result, 0)
        self.assertIn('assistant:\nwhole\n\nllmtool: reply interrupted; the provider answered in one piece',
                      ui.conversation.text)
        self.assertEqual([t['content'] for t in self.session.history], ['q', 'whole', 'next', 'whole'])
        self.assertEqual(self.session.state, 'idle')   # the next request reset the status

    def test_escape_while_idle_or_during_a_task_does_nothing_new(self):
        ui, _ = self.drive([lone_escape, lambda ui: self.assertFalse(ui.send_interrupted)])
        self.assertNotIn('interrupt requested', ui.conversation.text)
        self.assertFalse(ui.interrupt_send())
        self.factory.assert_not_called()

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

            def broken_append(text, *args, **kwargs):
                if text.startswith('assistant:'):
                    raise RuntimeError('PRIVATE render detail')
                return original(text, *args, **kwargs)
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
                            '/task mode profile first\r', task_mode_on,
                            '/task mode off\r', 'again\r', self.idle])
        self.assertEqual([t['content'] for t in self.session.history],
                         ['hello', 'reply', 'again', 'reply'])
        self.pm_factory.assert_not_called()
        self.assertEqual(self.store_records(), [])
        self.assertIn('chat mode: Enter sends drafts', ui.conversation.text)

    def test_task_submit_shows_core_receipt_and_opens_core_detail(self):
        request = '請新增測試\n第二行：多行需求'
        ui, _ = self.drive(['/task mode profile first\r', '\x1b[200~' + request + '\x1b[201~', '\r',
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
            ui, _ = self.drive(['/task mode profile first\r', 'noisy\r', self.idle])
        record, = self.store_records()
        self.assertIn('Task submit result: CREATED', ui.conversation.text)
        self.assertIn('Task ID: ' + record['task_id'], ui.conversation.text)
        self.assertNotIn('No Task receipt exists', ui.conversation.text)
        self.assertTrue(ui.show_detail)

    def test_large_paste_is_one_task_request(self):
        request = '\n'.join('第%d行 large paste 內容' % i for i in range(1500))
        self.assertGreater(len(request.encode('utf-8')), 40000)
        ui, _ = self.drive(['/task mode profile first\r', '\x1b[200~' + request + '\x1b[201~', '\r',
                            self.idle])
        records = self.store_records()
        self.assertEqual([r['request'] for r in records], [request])
        self.assertEqual(self.pm.complete.call_count, 1)

    def test_provider_unavailable_shows_core_error_and_retry_condition(self):
        ui, _ = self.drive(['/task mode profile missing\r', 'do work\r', self.idle])
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
        ui, _ = self.drive(['/task mode profile first\r', 'do work\r', self.idle])
        record, = self.store_records()
        self.assertEqual(record['error_code'], 'PM_PROVIDER_UNAVAILABLE')
        self.assertIn('PM profile first must be reachable', ui.conversation.text)
        self.assertNotIn('PRIVATE', ui.conversation.text)
        self.assertNotIn('CREATED', ui.conversation.text)

    def test_invalid_profile_creates_no_task_and_shows_core_error(self):
        ui, _ = self.drive(['/task mode profile bad!name\r', 'do work\r', self.idle])
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

        ui, _ = self.drive(['/task mode profile first\r', 'one task\r', '\r\r\r', wait_started,
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

        ui, _ = self.drive(['/task mode profile first\r', 'first task\r', self.idle,
                            lambda ui: self.assertTrue(ui.show_detail), lone_escape,
                            '\x1bOQ', lambda ui: self.assertTrue(ui.show_detail),
                            '\x1bOQ', lambda ui: self.assertFalse(ui.show_detail)])
        task_id = ui.last_task_id
        ui, _ = self.drive(['/task show ' + task_id + '\r', self.idle])
        self.assertTrue(ui.show_detail)
        self.assertIn('Task ' + task_id + ' state PM_RESPONDED', ui.conversation.text)
        self.assertIn('Run ID: ' + self.store_records()[0]['run_id'], ui.detail.text)

    def test_esc_enter_still_inserts_newline_in_task_mode_with_detail_open(self):
        ui, _ = self.drive(['/task mode profile first\r', 'first\r', self.idle, 'a', '\x1b\r', 'b',
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


@unittest.skipUnless(HAVE_TOOLKIT, 'prompt-toolkit optional extra not installed')
class W57ChatTests(Base):
    """W57: /status in chat mode, and Task mode behind the developer flag."""

    drive = WidgetTests.drive
    idle = staticmethod(WidgetTests.idle)

    def status(self, ui):
        return ui.conversation.text.rsplit('> /status', 1)[1]

    def test_chat_status_is_chinese_and_says_what_is_not_known_yet(self):
        ui, _ = self.drive(['/status\r'])
        status = self.status(ui)
        self.assertIn('狀態（聊天模式，不是 PM）', status)
        self.assertIn('供應商（本機設定）：first（openai-compatible）', status)
        self.assertIn('設定的模型（本機設定）：test', status)
        self.assertIn('實際模型：尚未呼叫', status)
        self.assertIn('連線：未檢查', status)
        self.assertIn('狀態：待命', status)
        self.assertNotIn('not probed', status)
        self.assertNotIn('connectivity', status)
        self.assertNotIn('用量（本 session）', status)

    def test_chat_status_shows_the_served_model_of_the_last_reply(self):
        self.client.complete.return_value = Reply('reply', {'served_model': 'qwen3-coder-480b'})
        ui, _ = self.drive(['hi\r', self.idle, '/status\r'])
        status = self.status(ui)
        self.assertIn('實際模型：qwen3-coder-480b', status)
        self.assertIn('對話：1 輪', status)
        self.assertEqual(self.session.last_served_model, 'qwen3-coder-480b')
        self.client.complete.return_value = Reply('reply', {'served_model': 'bad model!'})
        ui, _ = self.drive(['hi\r', self.idle, '/status\r'])
        self.assertIn('實際模型：上次回覆沒有回報', self.status(ui))

    def test_without_the_dev_flag_task_mode_is_hidden(self):
        os.environ.pop(chat_tui.DEV_ENV, None)
        with patch('llmtool.task_submission_cli.run_task_submit') as submit:
            def hidden(ui):
                self.assertFalse(ui.show_list)
                self.assertFalse(ui.show_events)

            ui, _ = self.drive(['/task mode profile first\r', '/task submit --provider x y\r',
                                '/task\r', 'plain\r', self.idle,
                                '\x1bOR', hidden,
                                '\x1bOS', hidden,
                                '/tas', lambda ui: self.assertEqual(ui.menu_items, [])])
        submit.assert_not_called()
        self.assertFalse(ui.dev)
        self.assertIsNone(ui.task_profile)
        self.assertEqual(ui.conversation.text.count('llmtool: ' + chat_tui.TASK_HIDDEN), 3)
        self.assertEqual([t['content'] for t in self.session.history], ['plain', 'reply'])
        help_text = ui.help_text()
        self.assertNotIn('/task', help_text)
        self.assertNotIn('/task', ui.command_help_text())
        self.assertNotIn('F3', help_text)
        self.assertNotIn('Task mode', help_text)
        self.assertIn('/pm connect', help_text)
        self.assertNotIn('/task submit is for development testing', ui.conversation.text)
        self.assertEqual([c for c in chat_tui.slash_matches('/', dev=False)
                          if c[0].startswith('/task')], [])

    def test_w60_up_down_with_nothing_to_open_say_why_once(self):
        os.environ.pop(chat_tui.DEV_ENV, None)
        hint = 'llmtool: 沒有 Task 面板；/pm connect 連 PM 後 ↓ 看背景 Task'

        def once(ui):
            self.assertEqual(ui.conversation.text.count(hint), 1)
            self.assertNotIn('F3', ui.conversation.text)       # F3 exists only with --dev
            self.assertEqual(ui.input.text, '')

        ui, _ = self.drive(['\x1b[B', once, '\x1b[B', once, '\x1b[A', once,
                            'hello\r', self.idle, '\x1b[A',
                            lambda ui: self.assertEqual(ui.input.text, 'hello')])  # history

    def test_dev_flag_from_env_or_option(self):
        self.assertTrue(chat_tui.dev_enabled({chat_tui.DEV_ENV: '1'}))
        self.assertFalse(chat_tui.dev_enabled({chat_tui.DEV_ENV: '0'}))
        self.assertFalse(chat_tui.dev_enabled({}))
        self.assertIn('/task mode profile PROFILE', chat_tui.key_help(True))
        self.assertNotIn('/task', chat_tui.key_help(False))
        with patch('llmtool.chat_tui.run_chat_tui', return_value=0) as run:
            from llmtool import cli
            home = self.temp_root
            with patch.dict(os.environ, {'HOME': str(home)}), \
                    patch('llmtool.cli.Path.home', return_value=home):
                self.assertEqual(cli.main(['chat', '--tui', '--dev', '--no-history']), 0)
        self.assertTrue(run.call_args.kwargs['dev'])

    def test_task_mode_needs_the_word_profile_and_a_name(self):
        with patch('llmtool.task_submission_cli.run_task_submit') as submit:
            ui, _ = self.drive(['/task mode profile\r',
                                lambda ui: self.assertIsNone(ui.task_profile),
                                '/task mode first\r',
                                lambda ui: self.assertIsNone(ui.task_profile),
                                '/task mode profile first\r',
                                lambda ui: self.assertEqual(ui.task_profile, 'first'),
                                '/task mode off\r',
                                lambda ui: self.assertIsNone(ui.task_profile)])
        submit.assert_not_called()
        self.assertEqual(ui.conversation.text.count('       /task mode profile PROFILE\n'), 2)
        self.assertNotIn('profile profile', ui.conversation.text)


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

    def test_latest_high_debug_attempt_is_shown_without_changing_the_stage(self):
        def attempt(status, report):
            return {'status': status, 'report': report, 'family': 'pr-17'}
        plain = _record(developer=_developer(state='DEVELOPER_DISPATCHING'))
        failed = dict(plain, high_debug=[attempt('RCA_CONFIRMED', 'r'),
                                         attempt('RUN_FAILED', 'HIGH_DEBUG_RUN_FAILED')])
        self.assertEqual(self.status.stage(failed), self.status.stage(plain))
        self.assertIsNone(self.status.blocked_reason(failed))
        self.assertIn('High Debug (latest): RUN_FAILED HIGH_DEBUG_RUN_FAILED (family pr-17)',
                      self.status.summary_text(failed))
        odd = dict(plain, high_debug=[attempt('RUN_FAILED', 'free text\nfrom a report')])
        self.assertEqual(self.status.high_debug_text(odd), 'RUN_FAILED (family pr-17)')
        done = dict(plain, high_debug=[attempt('RUN_FAILED', 'HIGH_DEBUG_RUN_FAILED'),
                                       attempt('RCA_CONFIRMED', 'r')])
        self.assertEqual(self.status.high_debug_text(done), 'RCA_CONFIRMED (family pr-17)')
        self.assertIsNone(self.status.high_debug_text(plain))
        self.assertNotIn('High Debug', self.status.summary_text(plain))

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
                          OWNER_PR_URL + '505'))

    def test_pr_number_sources_are_named_and_conflicts_shown(self):
        url = OWNER_PR_URL

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

    def test_pull_request_and_linker_follow_current_trusted_repository(self):
        from review_trust_fixture import make_trust, use_trust

        slug = 'example-org/other'
        url = 'https://github.com/' + slug + '/pull/'
        record = _record(developer=_developer(), review_dispatch_claim=[
            {'pr': 510, 'version': 1, 'state': 'DISPATCHED', 'reviewer_task': 'r'}])
        with use_trust(make_trust(slug)):
            self.assertEqual(self.status.pull_request(record),
                             ('#510 (from review claim)', url + '510'))
            text = 'PR: ' + url + '510'
            links = chat_tui._TaskViewLinker(None).links(text, 0, len(text))
            self.assertEqual([item[2] for item in links], [url + '510'])

    def test_pr_link_unavailable_without_trust(self):
        from llmtool.trust import TrustError

        record = _record(developer=_developer(), review_dispatch_claim=[
            {'pr': 510, 'version': 1, 'state': 'DISPATCHED', 'reviewer_task': 'r'}])
        with patch.object(chat_tui_status.trust, 'load_trust',
                          side_effect=TrustError('TRUST_MISSING')):
            self.assertEqual(self.status.pull_request(record),
                             ('#510 (from review claim)', 'NOT_AVAILABLE'))
            text = 'PR: ' + OWNER_PR_URL + '510'
            self.assertEqual(chat_tui._TaskViewLinker(None).links(text, 0, len(text)), [])

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

    def test_attention_items_states_and_readiness(self):
        blocked = _record(task_id='task-blocked', developer=_developer(
            state='DEVELOPER_OUTCOME_UNKNOWN', error_code='CODEX_BLOCKED'))
        unavailable = _record(task_id='task-unavailable',
                              developer=_developer(state='DEVELOPER_UNAVAILABLE'))
        failed = _record(task_id='task-failed',
                         developer=_developer(state='DEVELOPER_OUTPUT_REJECTED'))
        running = _record(task_id='task-running',
                          developer=_developer(state='DEVELOPER_DISPATCHING'))
        evidence_ready = _record(task_id='task-evidence', developer=_developer(
            state='DEVELOPER_RESPONDED', direct_result_callback={'state': 'PM_EVIDENCE_READY'}))
        self.assertEqual(self.status.attention_items([blocked, unavailable, failed, running]), [
            {'label': '卡住', 'task': 'task-blocked',
             'reason': 'Developer outcome unknown (CODEX_BLOCKED)'},
            {'label': '供應商不可用', 'task': 'task-unavailable', 'reason': ''},
            {'label': '失敗', 'task': 'task-failed', 'reason': ''}])
        ready = self.status.attention_items([running], {'task-running'})
        self.assertEqual(ready, [{'label': '證據已就緒，等 PM 處理',
                                  'task': 'task-running', 'reason': ''}])
        self.assertEqual(self.status.attention_items([running, evidence_ready]), [])
        self.assertEqual(self.status.attention_items([evidence_ready], {'task-evidence'}), [
            {'label': '證據已就緒，等 PM 處理', 'task': 'task-evidence', 'reason': ''}])
        terminal = _record(task_id='task-done', completion={'state': 'DONE'})
        self.assertEqual(self.status.attention_items([terminal], {'task-done'}), [])

    def test_read_attention_counts_unreadable_sources_independently(self):
        session = '11111111-1111-4111-8111-111111111111'
        records = [_record(task_id='task-running',
                           developer=_developer(state='DEVELOPER_DISPATCHING'))]
        cases = ((1, 0), (0, 1), (1, 1))
        for task_bad, inbox_bad in cases:
            with self.subTest(task_bad=task_bad, inbox_bad=inbox_bad), \
                 patch('llmtool.active_pm_inbox._session_records',
                       return_value=(records, task_bad)), \
                 patch('llmtool.active_pm_inbox.peek_inbox', return_value={
                     'events': [], 'pending': [], 'unreadable_task_count': inbox_bad}):
                result = self.status.read_attention(session, store=object())
            self.assertEqual(result['task_unreadable_count'], task_bad)
            self.assertEqual(result['inbox_unreadable_count'], inbox_bad)
            self.assertEqual(result['unread'], 0)
            self.assertEqual(result['items'], [])

    def test_read_attention_counts_pending_current_and_stale_events(self):
        session = '11111111-1111-4111-8111-111111111111'
        ready = _record(task_id='task-running',
                        developer=_developer(state='DEVELOPER_DISPATCHING'))
        inbox = {'events': [
            {'task_id': 'task-running', 'current': True, 'state': 'DELIVERED'},
            {'task_id': 'task-stale', 'current': False, 'state': 'DELIVERED'},
            {'task_id': 'task-acked', 'current': True, 'state': 'ACKED'}],
            'pending': [{'task_id': 'task-pending', 'state': 'NOT_YET_RECORDED'}]}
        with patch('llmtool.active_pm_inbox._session_records', return_value=([ready], 0)), \
             patch('llmtool.active_pm_inbox.peek_inbox', return_value=inbox):
            result = self.status.read_attention(session, store=object())
        self.assertEqual(result['unread'], 4)
        self.assertEqual(result['items'], [{'label': '證據已就緒，等 PM 處理',
                                            'task': 'task-running', 'reason': ''}])
        with patch('llmtool.active_pm_inbox._session_records', return_value=([ready], 0)), \
             patch('llmtool.active_pm_inbox.peek_inbox', return_value={
                 'events': [{'task_id': 'task-running', 'current': True, 'state': 'ACKED'}],
                 'pending': []}):
            acked = self.status.read_attention(session, store=object())
        self.assertEqual(acked['unread'], 1)
        self.assertEqual(acked['items'], [])


@unittest.skipUnless(HAVE_TOOLKIT, 'prompt-toolkit optional extra not installed')
class AttentionReportTests(Base):
    drive = WidgetTests.drive

    def connected_ui(self, ui):
        ui.pm_target = ('11111111-1111-4111-8111-111111111111', str(self.temp_root))
        ui.pm_runtime = Mock()
        ui.pm_mode = True
        return ui

    def test_attention_command_disconnected_and_never_read(self):
        ui, _ = self.drive(['/attention\r'])
        self.assertEqual(ui.conversation.text.splitlines()[-1],
                         '注意事項：尚未連線 PM。先 /pm connect。')
        ui, _ = self.drive([lambda ui: self.connected_ui(ui), '/attention\r'])
        self.assertEqual(ui.conversation.text.splitlines()[-1],
                         '注意事項：讀取中，稍後再試。')

    def test_attention_ignores_a_stale_session_snapshot(self):
        def stale(ui):
            self.connected_ui(ui)
            ui.finish_attention('22222222-2222-4222-8222-222222222222', {
                'items': [{'label': '卡住', 'task': 'oldtask', 'reason': 'old fact'}],
                'unread': 1, 'task_unreadable_count': 0, 'inbox_unreadable_count': 0})

        ui, _ = self.drive([stale, '/attention\r'])
        self.assertEqual(ui.conversation.text.splitlines()[-1],
                         '注意事項：讀取中，稍後再試。')
        self.assertEqual(ui.attention_lines(), [])

    def test_attention_chip_drops_first_at_narrow_status_width(self):
        def check(ui):
            self.connected_ui(ui)
            ui.app.output.get_size = lambda: Size(rows=24, columns=140)
            ui.finish_attention(ui.pm_target[0], {'items': [
                {'label': '卡住', 'task': 'task-blocked', 'reason': ''}], 'unread': 0,
                'task_unreadable_count': 0, 'inbox_unreadable_count': 0})
            self.assertIn('注意 1', ui.status_text())
            ui.app.output.get_size = lambda: Size(rows=24, columns=70)
            self.assertNotIn('注意 1', ui.status_text())

        self.drive([check])

    def test_attention_failed_snapshot_keeps_local_facts(self):
        ui, _ = self.drive([])
        self.connected_ui(ui)
        ui.pm_outbox.append('plain queued text')
        ui.pm_outbox_paused = 'interrupted'
        ui.pm_state = 'error(PM_FAILED)'
        ui.finish_attention(ui.pm_target[0], None)
        text = ui.attention_report()
        self.assertIn('注意事項：UNKNOWN（TaskStore 無法讀取）', text)
        self.assertIn('PM 佇列暫停：interrupted', text)
        self.assertIn('最近的 PM 回合出錯', text)
        self.assertTrue(text.rstrip().endswith('需要你決定：無法提供（Core 尚未記錄）'))

    def test_attention_report_partial_read_and_safe_single_rows(self):
        snapshot = {'items': [{'label': '卡住', 'task': 'attention-task',
                               'reason': 'line\nwith\tcontrols\rand ' + 'x' * 200}],
                    'unread': 3, 'task_unreadable_count': 1,
                    'inbox_unreadable_count': 1}
        ui, _ = self.drive([lambda ui: self.connected_ui(ui),
                            lambda ui: ui.finish_attention(ui.pm_target[0], snapshot),
                            '/attention\r'])
        text = ui.conversation.text.split('> /attention', 1)[1]
        self.assertIn('注意事項：UNKNOWN（TaskStore 部分無法讀取）', text)
        self.assertIn('部分讀取失敗：Tasks 1；PM inbox 1', text)
        rows = [line for line in text.splitlines() if '卡住｜attentio' in line]
        self.assertEqual(len(rows), 1)
        self.assertLessEqual(len(rows[0].strip()), 162)
        self.assertTrue(text.rstrip().endswith('需要你決定：無法提供（Core 尚未記錄）'))

    def test_attention_report_limits_facts_but_keeps_full_count(self):
        items = [{'label': '卡住', 'task': 'task-%02d' % i, 'reason': ''} for i in range(25)]

        def show(ui):
            self.connected_ui(ui)
            ui.app.output.get_size = lambda: Size(rows=24, columns=140)
            ui.finish_attention(ui.pm_target[0], {'items': items, 'unread': 0,
                'task_unreadable_count': 0, 'inbox_unreadable_count': 0})
            self.assertIn('注意 25', ui.pm_status_fields(lambda value, _width: value)['attention'])

        ui, _ = self.drive([show, '/attention\r'])
        text = ui.conversation.text.split('> /attention', 1)[1]
        self.assertIn('注意事項（25 項）', text)
        self.assertEqual(sum('卡住｜' in row for row in text.splitlines()), 20)
        self.assertIn('另有 5 項未顯示', text)
        self.assertTrue(text.rstrip().endswith('需要你決定：無法提供（Core 尚未記錄）'))

    def test_attention_read_exception_still_posts_background_completion(self):
        posted = []

        class Loop:
            def call_soon_threadsafe(_self, callback, *args):
                posted.append((callback, args))

        def exercise(ui):
            with patch.object(chat_tui_status, 'read_attention',
                              side_effect=RuntimeError('private')):
                ui._read_background(Loop(), '11111111-1111-4111-8111-111111111111')

        ui, _ = self.drive([exercise])
        self.assertEqual([callback.__name__ for callback, _args in posted],
                         ['finish_attention', 'finish_background'])
        self.assertEqual(posted[0][1][1], None)
        self.assertEqual(posted[1][1][1], ([], None))


@unittest.skipUnless(HAVE_TOOLKIT, 'prompt-toolkit optional extra not installed')
class TaskListTests(Base):
    """The Task list reads the real isolated TaskStore; no provider call is made by it."""

    drive = WidgetTests.drive
    idle = staticmethod(WidgetTests.idle)
    until = staticmethod(WidgetTests.until)
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
        seen = {}

        def list_open(ui):
            self.assertTrue(ui.show_list)
            self.assertTrue(ui.app.layout.has_focus(ui.list_window))
            rows = self.list_rows(ui)
            self.assertIn('2 shown, 0 hidden, 0 unreadable', rows[0])
            # The derived stage is labelled as derived in the pane itself (#510 review).
            self.assertIn('stage derived by the TUI', rows[0])
            self.assertIn('Stage (derived)', rows[1])
            self.assertIn('PR', rows[1])
            self.assertNotIn('Next responsible', '\n'.join(rows))
            self.assertEqual(sum('已建立／待派工' in row for row in rows), 1)
            self.assertEqual(sum('UNAVAILABLE' in row for row in rows), 1)
            self.assertTrue(rows[2].startswith('▶ '))
            ui.list_index = next(index for index, record in enumerate(ui.list_records)
                                 if chat_tui_status.row(record)['stage'] == '已建立／待派工')

            seen['list_copies'] = []
            ui.list_view._copier = lambda text: seen['list_copies'].append(text) or True

        def list_copied(ui):
            ui.list_view.wait_copy()
            self.assertEqual(seen['list_copies'], [ui.selected_task_id()])

        def detail_open(ui):
            self.assertTrue(ui.show_detail)
            self.assertEqual(ui.detail_view.text.splitlines()[:3], [
                '狀態：等待 Developer 派送', '下一步輪到誰：PM', '你需要做：目前不用'])
            self.assertTrue(ui.detail_view.spans)
            self.assertIn('Status (from TaskStore record', ui.detail.text)
            self.assertIn('Task: ' + ui.selected_task_id(), ui.detail.text)
            self.assertIn('Task detail (llmtool Core task show', ui.detail.text)
            seen['detail_copies'] = []
            ui.detail_view._copier = lambda text: seen['detail_copies'].append(text) or True

        def detail_copied(ui):
            ui.detail_view.wait_copy()
            self.assertEqual(seen['detail_copies'], [ui.selected_task_id()])

        def back_to_list(ui):
            self.assertFalse(ui.show_detail)
            self.assertTrue(ui.show_list)
            self.assertTrue(ui.app.layout.has_focus(ui.list_window))

        def back_to_chat(ui):
            self.assertFalse(ui.show_list)
            self.assertTrue(ui.app.layout.has_focus(ui.input))

        def assert_list_visible(ui):
            self.assertTrue(ui.show_list)

        calls = self.pm.complete.call_count
        ui, _ = self.drive(['/task mode profile first\r', 'first task\r', self.idle,
                            '/task mode profile missing\r', 'second task\r', self.idle,
                            lambda ui: setattr(self, 'calls', self.pm.complete.call_count),
                            '/task list\r', self.listed, list_open,
                            lambda ui: self.assertEqual(
                                chat_tui_status.row(ui.list_records[ui.list_index])['stage'],
                                '已建立／待派工'),
                            'c', list_copied, '\r', self.idle, detail_open, 'c', detail_copied,
                            '\x1b', self.esc_settled, back_to_list,
                            '\x1b', self.esc_settled, back_to_chat,
                            '\x1bOR', assert_list_visible, self.listed,
                            '\x1bOR', back_to_chat])
        self.assertEqual(self.pm.complete.call_count, self.calls)
        self.factory.assert_not_called()

    def test_copy_dev_task_id_ignores_placeholder_rows(self):
        async def placeholders(ui):
            ui.show_list = True
            ui.list_records = [{'task_id': chat_tui_task.UNKNOWN},
                               {'task_id': chat_tui_task.NOT_AVAILABLE}]
            ui.list_index = 0
            ui.list_view.copy = Mock()
            ui.copy_dev_task_id()
            ui.list_view.copy.assert_not_called()
            ui.list_index = 1
            ui.copy_dev_task_id()
            ui.list_view.copy.assert_not_called()

        self.drive([placeholders])

    # -- W60 Owner acceptance: F3 list, F4 events and the F2 detail select and copy ------
    @staticmethod
    async def w60_drag_row(ui, row, length=12):
        """Drag over ``length`` cells of the shown pane's ``row``; returns after release."""
        left, top, _height = ui._pane_geometry()
        for event in (press(left + 1, top + row + 1), drag(left + length, top + row + 1),
                      release(left + length, top + row + 1)):
            ui.app.input.send_text(event)
            await asyncio.sleep(0.05)

    def w60_copier(self, view):
        copies = []
        view._copier = lambda text: copies.append(text) or True
        return copies

    def test_w60_f3_list_drag_selects_copies_and_shift_c_copies_the_whole_list(self):
        seen = {}

        async def render(ui):
            await self.until(ui, lambda ui: ui.list_view.text == ''.join(
                text for _style, text in ui._list_fragments(full=True)), 'list view synced')
            ui.app.renderer.render(ui.app, ui.app.layout)

        async def dragged(ui):
            self.assertIs(ui._mouse_view(), ui.list_view)
            self.assertIn('拖曳即複製', ui.status_text())
            seen['copies'] = copies = self.w60_copier(ui.list_view)
            await self.w60_drag_row(ui, 2)
            await self.until(ui, lambda _ui: bool(copies), 'drag copy')
            self.assertTrue(ui.list_view.wait_copy())
            row = ui.list_view.text.splitlines()[2]
            self.assertTrue(copies and row.startswith(copies[-1]), (copies, row))
            self.assertIn('Cmd+V 貼上', ui.status_text())
            self.assertNotIn('Cmd+C', ui.status_text())
            ui.app.input.send_text('\x03')                      # Ctrl-C copies it again
            await self.until(ui, lambda _ui: len(copies) >= 2, 'ctrl-c copy')
            self.assertTrue(ui.list_view.wait_copy())
            self.assertEqual(copies[-1], copies[-2])
            self.assertFalse(ui.quit_pending)
            self.assertTrue(ui.show_list)

        async def copied_all(ui):
            copies = seen['copies']
            ui.app.input.send_text('C')
            await self.until(ui, lambda _ui: len(copies) >= 3, 'shift-c copy all')
            self.assertTrue(ui.list_view.wait_copy())
            text = copies[-1]
            for record in ui.list_records:
                self.assertIn(record['task_id'], text)          # full IDs, every Task
            self.assertIn('Stage (derived)', text)
            self.assertNotIn('\x1b', text)
            self.assertIn('全文 已複製', ui.status_text())
            self.assertTrue(ui.show_list)

        self.drive(['/task mode profile first\r', 'first task\r', self.idle,
                    '/task mode profile first\r', 'second task\r', self.idle,
                    '/task list\r', self.listed, render, dragged, copied_all])

    def test_w60_detail_opened_from_the_f3_list_drags_and_copies_like_the_panel(self):
        async def detail(ui):
            self.assertTrue(ui.show_detail)
            render = ui.app.renderer.render
            render(ui.app, ui.app.layout)
            self.assertIs(ui._mouse_view(), ui.detail_view)
            copies = self.w60_copier(ui.detail_view)
            await self.w60_drag_row(ui, 0, 10)
            self.assertTrue(ui.detail_view.wait_copy())
            first = ui.detail_view.text.splitlines()[0]
            self.assertTrue(copies and first.startswith(copies[-1]), (copies, first))
            self.assertFalse(ui.view.selected_range())          # never the hidden chat
            ui.app.input.send_text('C')
            await asyncio.sleep(0.1)
            self.assertTrue(ui.detail_view.wait_copy())
            self.assertEqual(copies[-1], ui.detail_view.text)   # the whole detail
            self.assertIn('Task detail (llmtool Core task show', copies[-1])
            self.assertNotIn('\x1b', copies[-1])
            self.assertEqual(ui.input.text, '')

        async def back(ui):
            await asyncio.sleep(1.2)
            self.assertFalse(ui.show_detail)
            self.assertTrue(ui.show_list)
            self.assertFalse(ui.detail_view.selected_range())

        self.drive(['/task mode profile first\r', 'first task\r', self.idle,
                    '/task list\r', self.listed, '\r', self.idle, detail, '\x1b', back])

    def test_w60_f4_events_drag_and_shift_c(self):
        async def events(ui):
            await asyncio.sleep(0.2)
            ui.app.renderer.render(ui.app, ui.app.layout)
            self.assertIs(ui._mouse_view(), ui.events_view)
            copies = self.w60_copier(ui.events_view)
            await self.w60_drag_row(ui, 0, 10)
            self.assertTrue(ui.events_view.wait_copy())
            self.assertTrue(copies and ui.events_view.text.startswith(copies[-1]), copies)
            ui.app.input.send_text('C')
            await asyncio.sleep(0.1)
            self.assertTrue(ui.events_view.wait_copy())
            self.assertEqual(copies[-1], ui.events_view.text)
            self.assertIn('Recent events', copies[-1])

        self.drive(['/task mode profile first\r', 'first task\r', self.idle,
                    '\x1bOS', self.listed, events])

    def test_w60_f3_list_longer_than_the_screen_scrolls_and_shift_c_copies_every_row(self):
        """Owner 3rd acceptance: C copied only the first page; the list must scroll."""
        ids = [('%02d' % n) * 16 for n in range(40)]          # distinct first 8 characters
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        records = [_record(task_id=task, request='W60 第%02d列' % n,
                           updated_at=(now - timedelta(minutes=40 - n)).strftime('%Y-%m-%dT%H:%M:%SZ'))
                   for n, task in enumerate(ids)]
        size = [Size(rows=24, columns=80)]
        seen = {}

        def render(ui):
            ui.app.renderer.render(ui.app, ui.app.layout)

        def shown(ui):
            _left, _top, height = ui._pane_geometry()
            view = ui.list_view
            return [ui.list_view.text.splitlines()[row] for row in
                    range(view.top, min(view.top + height, len(view.text.splitlines())))]

        async def wait_for(ui, condition):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                render(ui)
                if condition():
                    return
                await asyncio.sleep(0.01)
            render(ui)

        async def first(ui):
            await wait_for(ui, lambda: (
                len(ui.list_view.text.splitlines()) == 2 + 40
                and ui.app.layout.has_focus(ui.list_window)
                and ids[39][:8] not in '\n'.join(shown(ui))
            ))
            self.assertTrue(ui.app.layout.has_focus(ui.list_window))
            self.assertEqual(len(ui.list_view.text.splitlines()), 2 + 40)   # every row
            self.assertNotIn(ids[39][:8], '\n'.join(shown(ui)))         # beyond the screen
            seen['copies'] = self.w60_copier(ui.list_view)

        async def down_past_edge(ui):
            for _ in range(30):
                ui.app.input.send_text('\x1b[B')
            await wait_for(ui, lambda: (
                ui.list_index == 30
                and ui.list_view.top > 0
                and '▶ ' + ids[30][:8] in '\n'.join(shown(ui))
            ))
            self.assertEqual(ui.list_index, 30)
            self.assertGreater(ui.list_view.top, 0)                      # scrolled
            self.assertIn('▶ ' + ids[30][:8], '\n'.join(shown(ui)))     # selection on screen

        async def page_down(ui):
            before = ui.list_index
            ui.app.input.send_text('\x1b[6~')                            # PgDn
            await wait_for(ui, lambda: (
                ui.list_index > before
                and '▶ ' + ids[ui.list_index][:8] in '\n'.join(shown(ui))
            ))
            self.assertGreater(ui.list_index, before)
            self.assertIn('▶ ' + ids[ui.list_index][:8], '\n'.join(shown(ui)))
            before = ui.list_index
            ui.app.input.send_text('\x1b[5~')                            # PgUp
            await wait_for(ui, lambda: (
                ui.list_index < before
                and '▶ ' + ids[ui.list_index][:8] in '\n'.join(shown(ui))
            ))
            self.assertLess(ui.list_index, 39)
            self.assertIn('▶ ' + ids[ui.list_index][:8], '\n'.join(shown(ui)))

        async def wheel(ui):
            top = ui.list_view.top
            ui.app.input.send_text(WHEEL_UP * 3)
            await wait_for(ui, lambda: ui.list_view.top < top)
            self.assertLess(ui.list_view.top, top)

        async def copy_all(ui):
            copies = seen['copies']
            previous_count = len(copies)
            ui.app.input.send_text('C')
            await wait_for(ui, lambda: len(copies) > previous_count)
            self.assertTrue(ui.list_view.wait_copy())
            text = seen['copies'][-1]
            for task in ids:
                self.assertIn(task, text)                                # all 40, full IDs
            self.assertEqual(len(text.splitlines()), 2 + 40)
            self.assertNotIn('\x1b', text)

        with patch('llmtool.chat_tui_status.load_records', return_value=(records, 0)):
            self.drive(['/task list\r', self.listed, first, down_past_edge, page_down, wheel,
                        copy_all], size=size)

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
            ui, _ = self.drive(['/task mode profile first\r', 'a task\r', self.idle,
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
            ui, _ = self.drive(['/task mode profile first\r', 'a task\r', self.idle,
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
    until = staticmethod(WidgetTests.until)
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

        async def shown(ui):
            await self.until(ui, lambda ui: ui.show_events
                             and ui.app.layout.has_focus(ui.events)
                             and 'Recent events across 1 Tasks (0 unreadable' in ui.events.text
                             and '[TASK] Task recorded (PM state now PM_RESPONDED)' in ui.events.text
                             and ui.last_task_id[:8] in ui.events.text, 'events pane shown')
            self.assertTrue(ui.show_events)
            self.assertTrue(ui.app.layout.has_focus(ui.events))
            self.assertIn('Recent events across 1 Tasks (0 unreadable', ui.events.text)
            self.assertIn('[TASK] Task recorded (PM state now PM_RESPONDED)', ui.events.text)
            self.assertIn(ui.last_task_id[:8], ui.events.text)

        async def refreshed(ui):
            await self.until(ui, lambda ui: len(reads) == 2, 'events refreshed')
            self.assertEqual(len(reads), 2)

        async def hidden(ui):
            await self.until(ui, lambda ui: not ui.events_visible(), 'events pane hidden')
            self.assertFalse(ui.events_visible())

        async def visible(ui):
            await self.until(ui, lambda ui: ui.events_visible(), 'events pane visible')
            self.assertTrue(ui.events_visible())

        async def focused(ui):
            await self.until(ui, lambda ui: ui.app.layout.has_focus(ui.events),
                             'events pane focused')
            self.assertTrue(ui.app.layout.has_focus(ui.events))

        async def closed(ui):
            await self.until(ui, lambda ui: not ui.show_events
                             and ui.app.layout.has_focus(ui.input), 'events pane closed')
            self.assertFalse(ui.show_events)
            self.assertTrue(ui.app.layout.has_focus(ui.input))

        async def detail_loaded(ui):
            await self.until(ui, lambda ui: 'Events (newest first' in ui.detail.text,
                             'event detail loaded')
            self.assertIn('Events (newest first', ui.detail.text)

        async def timeline_loaded(ui):
            await self.until(ui, lambda ui: '[TASK] Task recorded' in ui.detail.text,
                             'event timeline loaded')
            self.assertIn('[TASK] Task recorded', ui.detail.text)

        async def one_read(ui):
            await self.until(ui, lambda ui: len(reads) == 1, 'initial events read')
            self.assertEqual(len(reads), 1)

        async def events_open(ui):
            await self.until(ui, lambda ui: ui.show_events, 'events pane open')
            self.assertTrue(ui.show_events)

        async def events_closed(ui):
            await self.until(ui, lambda ui: not ui.show_events, 'events pane closed')
            self.assertFalse(ui.show_events)

        with patch('llmtool.active_pm_inbox.read_inbox', side_effect=boom), \
                patch('llmtool.active_pm_inbox.record_ready_event', side_effect=boom), \
                patch('llmtool.active_pm_inbox.ack', side_effect=boom), \
                patch('llmtool.chat_tui_status.load_records', side_effect=counting):
            ui, _ = self.drive(['/task mode profile first\r', 'event task\r', self.idle,
                                detail_loaded, timeline_loaded,
                                '/task events\r', self.listed, shown, unseen,
                                one_read,
                                'r', self.listed, refreshed,
                                # Help hides the pane: no interval read while it is hidden.
                                '\x1bOP', hidden,
                                stale_wait, lambda ui: self.assertEqual(len(reads), 2),
                                '\x1bOP', visible, focused,
                                self.listed,
                                '\x1b', self.esc_settled, closed,
                                '\x1bOS', self.listed,
                                events_open,
                                '\x1bOS', events_closed])
        self.assertEqual(self.pm.complete.call_count, 1)
        self.factory.assert_not_called()




BG_WATCH = 'PM（背景通知） Claude Code 自行開始一輪（背景工作的通知）'   # header, W56a layout
BG_SUB = 'PM（背景通知） Claude Code 先處理一則背景通知；這則訊息等它結束後才開始'

PM_SESSION = '0123abcd-89ab-cdef-0123-456789abcdef'


class _FakeClaudeGate:
    """Test-controlled release handle for every playback turn currently holding."""

    def __init__(self):
        self._lock = threading.Lock()
        self._events = set()

    def new_turn(self):
        event = threading.Event()
        with self._lock:
            self._events.add(event)
        return event

    def finish_turn(self, event):
        with self._lock:
            self._events.discard(event)

    def set(self):
        with self._lock:
            events = tuple(self._events)
        for event in events:
            event.set()


class FakeClaude:
    """A scripted `claude -p` stream-json process: replies per user line, holds on a gate.

    Like Claude Code 2.1.312 it reports `command_lifecycle` frames (queued, started,
    completed) for a message sent with a uuid, and runs one turn at a time: a message
    with priority "later" starts only after a running (background) turn ends."""

    def __init__(self, turns):
        import json as _json
        self._json = _json
        self.turns = list(turns)        # one list of (stream object | 'HOLD') per user message
        self.gate = _FakeClaudeGate()
        self._turns_lock = threading.Lock()
        self._unfinished_turns = []
        self._all_turns = []
        self.written = []
        self.threads = []               # playback threads, joined by the test cleanup
        read, self._write = os.pipe()
        self.stdout = os.fdopen(read, 'rb')
        self.stdin = self
        self.terminated = self.closed_stdin = False
        self.returncode = None
        self.turn_lock = threading.Lock()   # one Claude Code turn at a time
        self.withhold = set()               # lifecycle states this fake never reports
        self.rewrite = {}                   # lifecycle state -> the state reported instead
        self.fail_writes = False            # a user line fails while being written

    # stdin side ------------------------------------------------------------
    def write(self, data):
        line = self._json.loads(data.decode('utf-8'))
        self.written.append(line)
        if self.fail_writes and line.get('type') == 'user':
            raise BrokenPipeError()
        if line.get('type') == 'user' and self.turns:
            self._lifecycle(line, 'queued')
            gate = self.gate.new_turn()
            turn = self._register_turn(gate)
            thread = threading.Thread(target=self._play_user,
                                      args=(line, self.turns.pop(0), turn),
                                      name='fake-claude-play', daemon=True)
            self.threads.append(thread)
            thread.start()
        elif line.get('type') == 'control_request':
            with self._turns_lock:
                turn = next((turn for turn in self._unfinished_turns
                             if turn['started'].is_set()), None)
                if turn is None and self._unfinished_turns:
                    turn = self._unfinished_turns[0]
                if turn is not None:
                    turn['gate'].set()
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
        except (OSError, TypeError):   # closed (TypeError: _end() ran between check and write)
            pass

    def _lifecycle(self, line, state):
        if line.get('uuid') and state not in self.withhold:
            self._emit({'type': 'command_lifecycle', 'command_uuid': line['uuid'],
                        'state': self.rewrite.get(state, state), 'uuid': 'frame'})

    def _register_turn(self, gate):
        turn = {'gate': gate, 'started': threading.Event(), 'finished': threading.Event()}
        with self._turns_lock:
            self._unfinished_turns.append(turn)
            self._all_turns.append(turn)
        return turn

    def _start_turn(self, turn):
        turn['started'].set()

    def _finish_turn(self, turn):
        with self._turns_lock:
            if turn in self._unfinished_turns:
                self._unfinished_turns.remove(turn)
        turn['finished'].set()
        self.gate.finish_turn(turn['gate'])

    def _play_user(self, line, script, turn):
        with self.turn_lock:
            try:
                self._start_turn(turn)
                self._lifecycle(line, 'started')
                self._play(script, turn['gate'])
                self._lifecycle(line, 'completed')
            finally:
                self._finish_turn(turn)

    def _play(self, script, gate):
        for item in script:
            if item == 'HOLD':
                gate.wait(5)
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

    def unsolicited(self, script, delay=0.0):
        """Play a turn Claude Code starts by itself (a background task notification)."""
        gate = self.gate.new_turn()
        turn = self._register_turn(gate)
        def play():
            time.sleep(delay)
            with self.turn_lock:
                try:
                    self._start_turn(turn)
                    self._play(script, gate)
                finally:
                    self._finish_turn(turn)
        thread = threading.Thread(target=play, name='fake-claude-play', daemon=True)
        self.threads.append(thread)
        thread.start()


class FakeClaudeGateTests(unittest.TestCase):
    HOLD_WAIT = 2

    @staticmethod
    def control(fake):
        fake.write((fake._json.dumps({'type': 'control_request'}) + '\n').encode('utf-8'))

    def release_and_join(self, fake):
        fake.gate.set()
        for thread in fake.threads:
            thread.join(self.HOLD_WAIT)
        fake._end()
        fake.stdout.close()

    def test_release_before_turn_playback_does_not_leak_to_next_turn(self):
        fake = FakeClaude([['HOLD'], ['HOLD']])
        first = {'type': 'user', 'uuid': 'first'}
        second = {'type': 'user', 'uuid': 'second'}

        fake.turn_lock.acquire()
        try:
            fake.write((fake._json.dumps(first) + '\n').encode('utf-8'))
            first_thread = fake.threads[0]
            self.control(fake)
        finally:
            fake.turn_lock.release()

        first_turn = fake._all_turns[0]
        first_released_promptly = first_turn['finished'].wait(self.HOLD_WAIT)

        fake.write((fake._json.dumps(second) + '\n').encode('utf-8'))
        second_turn = fake._unfinished_turns[0]
        second_started = second_turn['started'].wait(self.HOLD_WAIT)
        next_turn_still_holds = not second_turn['finished'].is_set()
        self.release_and_join(fake)

        self.assertTrue(first_released_promptly, 'release before gate.clear was lost')
        self.assertTrue(second_started, 'second turn did not start')
        self.assertTrue(next_turn_still_holds, 'an earlier interrupt released the next turn')
        self.assertFalse(first_thread.is_alive())
        self.assertFalse(fake.threads[1].is_alive())

    def test_control_request_releases_an_unsolicited_holding_turn(self):
        fake = FakeClaude([])
        fake.unsolicited(['HOLD'])
        turn = fake._unfinished_turns[0]
        started = turn['started'].wait(self.HOLD_WAIT)

        self.control(fake)
        released_promptly = turn['finished'].wait(self.HOLD_WAIT)
        self.release_and_join(fake)

        self.assertTrue(started, 'unsolicited turn did not start')
        self.assertTrue(released_promptly, 'control request did not release unsolicited turn')
        self.assertFalse(fake.threads[0].is_alive())

    def test_control_request_releases_running_user_turn_before_queued_turn(self):
        fake = FakeClaude([['HOLD'], ['HOLD']])
        first = {'type': 'user', 'uuid': 'first'}
        second = {'type': 'user', 'uuid': 'second'}

        fake.write((fake._json.dumps(first) + '\n').encode('utf-8'))
        first_turn = fake._unfinished_turns[0]
        first_started = first_turn['started'].wait(self.HOLD_WAIT)
        fake.write((fake._json.dumps(second) + '\n').encode('utf-8'))
        second_turn = fake._unfinished_turns[1]

        self.control(fake)
        first_released = first_turn['finished'].wait(self.HOLD_WAIT)
        second_started = second_turn['started'].wait(self.HOLD_WAIT)
        second_still_holds = not second_turn['finished'].is_set()
        self.control(fake)
        second_released = second_turn['finished'].wait(self.HOLD_WAIT)
        self.release_and_join(fake)

        self.assertTrue(first_started, 'first user turn did not start')
        self.assertTrue(first_released, 'control request did not release running turn 1')
        self.assertTrue(second_started, 'queued turn 2 did not start after turn 1')
        self.assertTrue(second_still_holds, 'control request released queued turn 2 early')
        self.assertTrue(second_released, 'second control request did not release turn 2')
        self.assertFalse(fake.threads[0].is_alive())
        self.assertFalse(fake.threads[1].is_alive())

    def test_retiring_turn_is_removed_before_the_next_turn_handoff(self):
        class PostUnlockBarrier:
            def __init__(self):
                self.lock = threading.Lock()
                self.unlocked = threading.Event()
                self.resume = threading.Event()
                self.first_thread = None
                self.resume_wait_succeeded = None

            def __enter__(self):
                self.lock.acquire()
                return self

            def __exit__(self, _kind, _value, _traceback):
                is_first = threading.current_thread() is self.first_thread
                self.lock.release()
                if is_first:
                    self.unlocked.set()
                    self.resume_wait_succeeded = self.resume.wait(
                        FakeClaudeGateTests.HOLD_WAIT)

        for retiring_kind in ('user', 'unsolicited'):
            for running_kind in ('user', 'unsolicited'):
                with self.subTest(retiring=retiring_kind, running=running_kind):
                    user_scripts = ([[], ['HOLD'], ['HOLD']]
                                    if retiring_kind == 'user' else [['HOLD'], ['HOLD']])
                    fake = FakeClaude(user_scripts)
                    barrier = PostUnlockBarrier()
                    fake.turn_lock = barrier
                    try:
                        barrier.lock.acquire()
                        try:
                            if retiring_kind == 'user':
                                fake.write((fake._json.dumps({'type': 'user', 'uuid': 'retiring'})
                                             + '\n').encode('utf-8'))
                                retiring_thread = fake.threads[0]
                            else:
                                fake.unsolicited([])
                                retiring_thread = fake.threads[0]
                            barrier.first_thread = retiring_thread
                        finally:
                            barrier.lock.release()

                        barrier_waited = barrier.unlocked.wait(self.HOLD_WAIT)

                        if running_kind == 'user':
                            fake.write((fake._json.dumps({'type': 'user', 'uuid': 'running'})
                                         + '\n').encode('utf-8'))
                        else:
                            fake.unsolicited(['HOLD'])
                        running_turn = fake._all_turns[1]
                        running_started = running_turn['started'].wait(self.HOLD_WAIT)
                        fake.write((fake._json.dumps({'type': 'user', 'uuid': 'later'})
                                     + '\n').encode('utf-8'))
                        later_turn = fake._all_turns[2]

                        self.control(fake)
                        selected_running_gate = running_turn['gate'].is_set()
                        later_gate_untouched = not later_turn['gate'].is_set()
                    finally:
                        barrier.resume.set()
                        fake.gate.set()
                        for thread in fake.threads:
                            thread.join(self.HOLD_WAIT)
                        fake._end()
                        fake.stdout.close()

                    self.assertTrue(barrier_waited, 'retiring turn did not reach post-unlock barrier')
                    self.assertTrue(barrier.resume_wait_succeeded,
                                    'retiring turn timed out at the post-unlock barrier')
                    self.assertTrue(running_started, 'next turn did not start')
                    self.assertTrue(selected_running_gate, 'control request missed the running turn')
                    self.assertTrue(later_gate_untouched, 'control request released a later queued turn')
                    self.assertFalse(retiring_thread.is_alive())
                    self.assertTrue(all(not thread.is_alive() for thread in fake.threads))


def _delta(text, index=0):
    """A Claude Code stream-json partial text event, as pm_runtime S2 parses it."""
    return {'type': 'stream_event', 'event': {'type': 'content_block_delta', 'index': index,
                                              'delta': {'type': 'text_delta', 'text': text}}}


def _thinking(index=0, text='PRIVATE_REASONING'):
    """A thinking block start and delta, as Claude Code streams them before the answer."""
    return [{'type': 'stream_event', 'event': {'type': 'content_block_start', 'index': index,
                                               'content_block': {'type': 'thinking', 'thinking': ''}}},
            {'type': 'stream_event', 'event': {'type': 'content_block_delta', 'index': index,
                                               'delta': {'type': 'thinking_delta', 'thinking': text}}},
            {'type': 'stream_event', 'event': {'type': 'content_block_stop', 'index': index}}]


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
                        {'type': 'tool_use', 'id': 't1', 'name': 'Read', 'input': {'file_path': 'docs/plan.md', 'command': 'SECRET_COMMAND'}}),
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


@unittest.skipUnless(HAVE_TOOLKIT, 'prompt-toolkit optional extra not installed')
class AutoResumeWidgetTests(Base):
    """Auto-resume decisions use a fake PM client and never start a provider stream."""

    def make_ui(self):
        self.pipe = create_pipe_input()
        self.pipe.__enter__()
        self.addCleanup(self.pipe.__exit__, None, None, None)
        return chat_tui.ChatTUI(self.session, input=self.pipe, output=DummyOutput())

    def prepare(self, ui):
        ui.pm_runtime = Mock(turn_active=Mock(return_value=False),
                             automatic_sends_blocked=Mock(return_value=False))
        ui.pm_mode, ui.pm_state = True, 'idle'
        ui.pm_target = ('session', '.')
        ui._auto_resume_connection = object()
        ui._pm_start_turn = Mock()

    def test_no_work_does_not_send(self):
        ui = self.make_ui()
        self.prepare(ui)
        ui._auto_resume_pending = False
        ui._maybe_auto_resume()
        ui._pm_start_turn.assert_not_called()

    def test_busy_pm_defers_resume_until_idle(self):
        ui = self.make_ui()
        self.prepare(ui)
        ui.pm_runtime.turn_active.return_value = True
        ui._auto_resume_pending = True
        ui._maybe_auto_resume()
        ui._pm_start_turn.assert_not_called()
        self.assertTrue(ui._auto_resume_pending)
        ui.pm_runtime.turn_active.return_value = False
        ui._pm_background_waiting.append({'runtime': ui.pm_runtime})
        ui._maybe_auto_resume()
        ui._pm_start_turn.assert_not_called()
        ui._pm_background_waiting.clear()
        ui._maybe_auto_resume()
        ui._pm_start_turn.assert_called_once()

    def test_busy_chat_request_defers_then_non_pm_finish_rechecks(self):
        ui = self.make_ui()
        self.prepare(ui)
        ui.busy = True
        ui._auto_resume_pending = True
        ui._maybe_auto_resume()
        ui._pm_start_turn.assert_not_called()
        self.assertTrue(ui._auto_resume_pending)
        ui._finish(lambda: None)
        ui._pm_start_turn.assert_called_once()

    def test_held_drafts_cancel_candidate_and_turning_setting_back_on_does_not_revive(self):
        for paused in (False, True):
            with self.subTest(paused=paused):
                ui = self.make_ui()
                self.prepare(ui)
                ui._auto_resume_pending = True
                if paused:
                    ui.pm_outbox_paused = 'Esc 中斷'
                else:
                    ui.pm_outbox.append('held draft')
                ui._maybe_auto_resume()
                self.assertFalse(ui._auto_resume_pending)
                ui.pm_outbox.clear()
                ui.pm_outbox_paused = None
                ui._maybe_auto_resume()
                ui._pm_start_turn.assert_not_called()

    def test_one_queued_draft_cancels_resume_before_background_turn_drains(self):
        ui = self.make_ui()
        self.prepare(ui)
        ui._auto_resume_pending = True
        ui.pm_turn_active = True
        ui.submit_pm('draft one')

        ui.pm_turn_status = 'COMPLETED'
        ui.finish_pm_turn()

        ui._pm_start_turn.assert_called_once_with(
            'draft one', 'owner → PM（自佇列送出，剩 0 則）:\n')
        self.assertFalse(ui._auto_resume_pending)

    def test_two_queued_drafts_cancel_resume_before_each_background_drain(self):
        ui = self.make_ui()
        self.prepare(ui)
        ui._auto_resume_pending = True
        ui.pm_turn_active = True
        ui.submit_pm('draft one')
        ui.submit_pm('draft two')

        ui.pm_turn_status = 'COMPLETED'
        ui.finish_pm_turn()
        ui.pm_turn_active = True
        ui.pm_turn_status = 'COMPLETED'
        ui.finish_pm_turn()

        self.assertEqual([call.args[0] for call in ui._pm_start_turn.call_args_list],
                         ['draft one', 'draft two'])
        self.assertFalse(ui._auto_resume_pending)

    def test_immediate_draft_cancels_resume_without_changing_sent_latch(self):
        ui = self.make_ui()
        self.prepare(ui)
        ui._auto_resume_pending = True
        ui._auto_resume_sent = True

        ui.submit_pm('immediate draft')

        ui._pm_start_turn.assert_called_once_with('immediate draft', 'owner → PM:\n')
        self.assertFalse(ui._auto_resume_pending)
        self.assertTrue(ui._auto_resume_sent)

    def test_settings_off_cancels_candidate_permanently(self):
        ui = self.make_ui()
        self.prepare(ui)
        ui._auto_resume_pending = True
        ui.run_display_command('/settings auto-resume off')
        self.assertFalse(ui._auto_resume_pending)
        ui.run_display_command('/settings auto-resume on')
        ui._maybe_auto_resume()
        ui._pm_start_turn.assert_not_called()

    def test_disconnect_and_close_cancel_candidate(self):
        disconnected = self.make_ui()
        self.prepare(disconnected)
        disconnected._auto_resume_pending = True
        disconnected.finish_pm_disconnect()
        self.assertFalse(disconnected._auto_resume_pending)

        closing = self.make_ui()
        self.prepare(closing)
        closing._auto_resume_pending = True
        closing.close_pm()
        self.assertFalse(closing._auto_resume_pending)

    def test_unreadable_or_blocked_runtime_safety_latch_cancels(self):
        for blocked in (True, None, RuntimeError('unreadable')):
            with self.subTest(blocked=blocked):
                ui = self.make_ui()
                self.prepare(ui)
                ui.pm_runtime.automatic_sends_blocked = Mock(
                    side_effect=blocked if isinstance(blocked, Exception) else None,
                    return_value=blocked)
                ui._auto_resume_pending = True
                ui._maybe_auto_resume()
                ui._pm_start_turn.assert_not_called()
                self.assertFalse(ui._auto_resume_pending)

    def test_unknown_turn_guard_cancels_without_sending(self):
        ui = self.make_ui()
        self.prepare(ui)
        del ui.pm_runtime.turn_active
        ui._auto_resume_pending = True
        ui._maybe_auto_resume()
        ui._pm_start_turn.assert_not_called()
        self.assertFalse(ui._auto_resume_pending)

    def test_interrupt_and_unsafe_turn_outcomes_cancel_before_background_drain(self):
        cases = (("esc", None, False, "COMPLETED"),
                 ("ctrl-c", None, False, "COMPLETED"),
                 (None, "E_RUNTIME", False, "COMPLETED"),
                 (None, None, True, "COMPLETED"),
                 (None, None, False, "FAILED"))
        for interrupt, error, refused, status in cases:
            with self.subTest(interrupt=interrupt, error=error, refused=refused, status=status):
                ui = self.make_ui()
                self.prepare(ui)
                ui._auto_resume_pending = True
                ui.pm_turn_active = True
                ui.pm_turn_status, ui.pm_turn_error = status, error
                ui.pm_turn_refused, ui.pm_interrupt_by = refused, interrupt
                ui._pm_background_waiting.append({'runtime': ui.pm_runtime})
                ui._pm_background_show = Mock(side_effect=lambda _turn: ui._pm_begin('bg', 'background'))
                ui.finish_pm_turn()
                self.assertFalse(ui._auto_resume_pending)
                ui._pm_start_turn.assert_not_called()

    def test_interrupt_cancels_before_runtime_interrupt(self):
        ui = self.make_ui()
        self.prepare(ui)
        ui.pm_turn_active = True
        ui._auto_resume_pending = True
        ui.pm_runtime.interrupt = Mock(side_effect=lambda: self.assertFalse(ui._auto_resume_pending))
        ui.interrupt_pm('esc')
        ui.pm_runtime.interrupt.assert_called_once()

    def test_resume_sends_once_per_connect_and_shows_system_line(self):
        ui = self.make_ui()
        self.prepare(ui)
        ui._auto_resume_pending = True
        ui._pm_start_turn.side_effect = lambda *_args: self.assertTrue(ui._auto_resume_sent)
        ui._maybe_auto_resume()
        ui._maybe_auto_resume()
        ui._pm_start_turn.assert_called_once_with(
            '（自動）重新連線：請接續進行中的工作並回報狀態', 'owner → PM（自動恢復）:\n')
        self.assertIn('（自動）重新連線：請接續進行中的工作並回報狀態',
                      ui.conversation.text)
        self.assertFalse(ui._auto_resume_pending)
        self.assertTrue(ui._auto_resume_sent)

    def test_unconfirmed_candidate_waits_for_reader_confirmation(self):
        ui = self.make_ui()
        self.prepare(ui)
        ui._auto_resume_pending = ui._auto_resume_unconfirmed = True
        ui._maybe_auto_resume()
        ui._pm_start_turn.assert_not_called()
        ui.finish_auto_resume_read(ui._auto_resume_connection, ui.pm_runtime, 'session', [{}])
        ui.finish_auto_resume_read(ui._auto_resume_connection, ui.pm_runtime, 'session', [{}])
        ui._maybe_auto_resume()
        ui._pm_start_turn.assert_called_once()

    def test_finish_pm_turn_cancels_unconfirmed_candidate_with_outbox(self):
        ui = self.make_ui()
        self.prepare(ui)
        ui._auto_resume_pending = ui._auto_resume_unconfirmed = True
        ui.pm_outbox.append('draft')
        ui.pm_turn_status = 'COMPLETED'
        ui.finish_pm_turn()
        self.assertFalse(ui._auto_resume_pending)
        ui.finish_auto_resume_read(ui._auto_resume_connection, ui.pm_runtime, 'session', [{}])
        ui._pm_start_turn.assert_called_once()
        self.assertEqual(ui._pm_start_turn.call_args.args[0], 'draft')

    def test_late_hold_permanently_cancels_resume_before_queue_actions(self):
        for action in ('clear', 'send', 'enter'):
            with self.subTest(action=action):
                ui = self.make_ui()
                self.prepare(ui)
                ui._auto_resume_pending = ui._auto_resume_unconfirmed = True
                ui._pm_hold('held draft', 1, 'HELD')
                self.assertFalse(ui._auto_resume_pending)
                if action == 'clear':
                    ui.run_queue_command('clear')
                elif action == 'send':
                    ui.run_queue_command('send')
                else:
                    ui._pm_resume(everything=False)
                ui.finish_auto_resume_read(ui._auto_resume_connection, ui.pm_runtime,
                                           'session', [{}])
                ui._maybe_auto_resume()
                self.assertFalse(ui._auto_resume_pending)
                self.assertFalse(ui._auto_resume_sent)
                if action == 'clear':
                    ui._pm_start_turn.assert_not_called()
                else:
                    ui._pm_start_turn.assert_called_once()
                    self.assertEqual(ui._pm_start_turn.call_args.args[0], 'held draft')

    def test_stale_resume_read_is_ignored_after_reconnect_for_either_session(self):
        for session in ('session', 'new-session'):
            with self.subTest(session=session):
                ui = self.make_ui()
                self.prepare(ui)
                old_connection, old_runtime = object(), Mock()
                ui._auto_resume_connection = object()
                ui.pm_target = (session, '.')
                ui.background_key = session
                ui.background_loaded = time.monotonic()
                ui.background = [{'role': 'Developer', 'task': 'cached', 'state': 'RUNNING'}]
                ui._auto_resume_pending = ui._auto_resume_unconfirmed = True
                ui.finish_auto_resume_read(old_connection, old_runtime, 'session', [{}])
                self.assertTrue(ui._auto_resume_pending)
                self.assertTrue(ui._auto_resume_unconfirmed)
                ui._pm_start_turn.assert_not_called()

    def test_exception_none_and_empty_resume_results_cancel(self):
        for result in (None, [], RuntimeError('read failed')):
            with self.subTest(result=result):
                ui = self.make_ui()
                self.prepare(ui)
                ui._auto_resume_pending = ui._auto_resume_unconfirmed = True
                ui.finish_auto_resume_read(ui._auto_resume_connection, ui.pm_runtime,
                                           'session', result)
                self.assertFalse(ui._auto_resume_pending)
                self.assertFalse(ui._auto_resume_unconfirmed)
                ui._maybe_auto_resume()
                ui._pm_start_turn.assert_not_called()

    def test_reader_converts_exception_to_none_and_only_posts_result(self):
        ui = self.make_ui()
        self.prepare(ui)
        ui._auto_resume_pending = ui._auto_resume_unconfirmed = True
        post = Mock(return_value=True)
        ui._post = post
        loop = object()
        with patch.object(chat_tui_status, 'read_background', side_effect=RuntimeError('private')):
            ui._read_auto_resume(ui._auto_resume_connection, ui.pm_runtime, 'session', loop)
        post.assert_called_once_with(loop, ui.finish_auto_resume_read,
                                     ui._auto_resume_connection, ui.pm_runtime, 'session', None)
        self.assertTrue(ui._auto_resume_pending)
        self.assertTrue(ui._auto_resume_unconfirmed)

    def test_closed_loop_drops_auto_resume_result_without_side_effects(self):
        ui = self.make_ui()
        self.prepare(ui)
        ui._auto_resume_pending = ui._auto_resume_unconfirmed = True
        loop = asyncio.new_event_loop()
        loop.close()
        before = (ui._auto_resume_pending, ui._auto_resume_unconfirmed,
                  ui._auto_resume_connection, ui.pm_runtime, ui.pm_target,
                  list(ui.pm_outbox), ui.pm_state, ui.conversation.text)
        returns = []
        real_post = ui._post
        post = Mock(side_effect=lambda *args: returns.append(real_post(*args)) or returns[-1])
        finish = Mock(wraps=ui.finish_auto_resume_read)
        errors = Mock()
        reader = threading.Thread(
            target=ui._read_auto_resume,
            args=(ui._auto_resume_connection, ui.pm_runtime, 'session', loop),
            name='test-closed-loop-resume-reader')
        with patch.object(chat_tui_status, 'read_background', return_value=[{}]), \
                patch.object(ui, '_post', post), \
                patch.object(ui, 'finish_auto_resume_read', finish), \
                patch.object(threading, 'excepthook', errors):
            reader.start()
            reader.join(5)
        self.assertFalse(reader.is_alive())
        self.assertEqual(returns, [False])
        errors.assert_not_called()
        finish.assert_not_called()
        ui._pm_start_turn.assert_not_called()
        self.assertEqual((ui._auto_resume_pending, ui._auto_resume_unconfirmed,
                          ui._auto_resume_connection, ui.pm_runtime, ui.pm_target,
                          list(ui.pm_outbox), ui.pm_state, ui.conversation.text), before)

    def test_current_connection_and_runtime_accept_resume_read(self):
        ui = self.make_ui()
        self.prepare(ui)
        ui._auto_resume_pending = ui._auto_resume_unconfirmed = True
        old_connection = object()
        ui.finish_auto_resume_read(old_connection, ui.pm_runtime, 'session', [{}])
        self.assertTrue(ui._auto_resume_pending)
        self.assertTrue(ui._auto_resume_unconfirmed)
        ui._pm_start_turn.assert_not_called()

    def test_background_end_sends_deferred_resume_once(self):
        ui = self.make_ui()
        self.prepare(ui)
        ui._auto_resume_pending = ui._auto_resume_unconfirmed = True
        turn = dict(runtime=ui.pm_runtime, seq=None,
                    events=[], ended=False)
        ui.pm_background_start(turn)
        self.assertTrue(ui.pm_turn_active)
        ui.finish_auto_resume_read(ui._auto_resume_connection, ui.pm_runtime, 'session', [{}])
        ui._maybe_auto_resume()
        ui._pm_start_turn.assert_not_called()
        self.assertTrue(ui._auto_resume_pending)
        ui.pm_background_event(
            turn, {"type": "TURN_END", "status": "COMPLETED"})
        ui.pm_background_end(turn)
        ui.pm_background_end(turn)
        ui._pm_start_turn.assert_called_once()
        self.assertIn("自動恢復",
                      ui._pm_start_turn.call_args.args[1])
        self.assertFalse(ui._auto_resume_pending)
        self.assertTrue(ui._auto_resume_sent)

    def test_settings_switch_persists_off_and_prevents_resume(self):
        ui = self.make_ui()
        self.prepare(ui)
        self.assertEqual(ui.settings['auto_resume'], 'on')
        ui._auto_resume_pending = True
        ui.run_display_command('/settings auto-resume off')
        ui._maybe_auto_resume()
        ui._pm_start_turn.assert_not_called()
        self.assertEqual(chat_tui_theme.load_settings()['auto_resume'], 'off')


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
        turns, total = self.pm.history_turns({'turns': [turn, 'not a turn']})
        self.assertEqual(total, 1)
        self.assertNotIn(self.key[3:9], repr(turns))
        self.assertEqual(turns[0]['tools'], ['Read', '[withheld]', 'UNKNOWN'])
        self.assertEqual(turns[0]['status'], 'UNKNOWN')
        self.assertEqual(turns[0]['time'], '時間不明')
        self.assertIn('mine [REDACTED]', turns[0]['owner'])
        self.assertIn('theirs [REDACTED]', turns[0]['reply'])
        clean, _total = self.pm.history_turns({'turns': [_crafted_turn()]})
        self.assertEqual((clean[0]['status'], clean[0]['tools'], clean[0]['reply']),
                         ('COMPLETED', ['Read'], 'ok'))
        self.assertEqual(self.pm.history_turns({'turns': None}), ([], 0))

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

    THREAD_JOIN_LIMIT = 10   # seconds, shared by all of one test's threads

    def _release_fakes(self):
        """Stop every fake playback thread and TUI PM worker thread, joined within one
        bound; any thread still alive afterwards fails the test (it must not outlive it)."""
        for fake in self.fakes:
            fake.gate.set()            # a held turn ends
            fake._end()                # its stdout reaches EOF
        threads = [thread for fake in self.fakes for thread in fake.threads]
        threads += [thread for thread in threading.enumerate()
                    if thread.name.startswith('llmtool-chat-pm') and thread not in threads]
        deadline = time.monotonic() + self.THREAD_JOIN_LIMIT
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        for fake in self.fakes:
            try:
                fake.stdout.close()
            except OSError:
                pass
        alive = sorted(thread.name for thread in threads if thread.is_alive())
        if alive:
            self.fail('threads still alive %.0fs after the test: %s'
                      % (self.THREAD_JOIN_LIMIT, ', '.join(alive)))

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


class BoundedPMCleanupTests(unittest.TestCase):
    """The bounded cleanup itself: a thread that outlives a test fails that test."""

    def test_stuck_fake_or_pm_thread_fails_the_test(self):
        release = threading.Event()

        class Stuck(BoundedPMTest):
            THREAD_JOIN_LIMIT = 0.2

            def test_fake(self):
                fake = FakeClaude([])
                thread = threading.Thread(target=release.wait, args=(10,),
                                          name='fake-claude-play', daemon=True)
                fake.threads.append(thread)
                thread.start()
                self.fakes.append(fake)

            def test_pm_worker(self):
                threading.Thread(target=release.wait, args=(10,),
                                 name='llmtool-chat-pm-stuck', daemon=True).start()

            def test_clean(self):
                self.fakes.append(FakeClaude([]))

        try:
            # Clean first: the stuck cases leave live threads that any later case would see.
            for name, failed in (('test_clean', False), ('test_fake', True),
                                 ('test_pm_worker', True)):
                with self.subTest(name=name):
                    result = unittest.TestResult()
                    Stuck(name).run(result)
                    problems = result.failures + result.errors
                    self.assertEqual(bool(problems), failed, problems)
                    if failed:
                        self.assertIn('threads still alive', problems[0][1])
        finally:
            release.set()
            for thread in threading.enumerate():
                if thread.name in ('fake-claude-play', 'llmtool-chat-pm-stuck'):
                    thread.join(5)


@unittest.skipUnless(HAVE_TOOLKIT, 'prompt-toolkit optional extra not installed')
class PMTypewriterTests(Base):
    """Received PM text is queued and released at ticks, in order, and flushed at once on
    block end, turn end and interrupt; without a running loop the tick is driven by hand."""

    def setUp(self):
        super().setUp()
        self.pipe = create_pipe_input()
        self.pipe.__enter__()
        self.addCleanup(self.pipe.__exit__, None, None, None)
        self.ui = chat_tui.ChatTUI(self.session, input=self.pipe, output=DummyOutput())
        self.ui.pm_turn_active, self.ui.pm_turn_started = True, time.monotonic()
        self.ui.pm_state, self.ui.pm_mode = 'working', True

    def delta(self, text, index=0):
        self.ui.pm_event({'type': 'TEXT_DELTA', 'text': text, 'block_index': index})

    def shown(self):
        """Text of the last PM block as the pane shows it (None: no block opened)."""
        text = self.ui.conversation.text
        if text.endswith('\nPM'):
            return ''                                # the label opened; no text yet
        if '\nPM\n' not in text:
            return None
        rest = text[text.rindex('\nPM\n') + 4:]
        return rest[2:] if rest.startswith('  ') else rest   # the body indent (W56a)

    def test_text_is_released_in_order_a_few_characters_per_tick(self):
        # CJK text: an ASCII word is shown only once complete (screen_stream), as before.
        ui = self.ui
        self.delta('一二三四五六')
        self.assertEqual(self.shown(), '')            # the PM: header opens at once
        self.assertEqual(ui.pm_queue, '一二三四五六')
        ui._pm_tick()
        self.assertEqual(self.shown(), '一二')
        self.delta('七八九十')                        # a later burst joins the queue in order
        self.assertEqual(ui.pm_queue, '三四五六七八九十')
        ui._pm_tick()
        ui._pm_tick()
        self.assertEqual(self.shown(), '一二三四五六')
        self.ui.pm_event({'type': 'ASSISTANT_TEXT', 'text': '一二三四五六七八九十'})
        self.assertEqual(self.shown(), '一二三四五六七八九十')  # block end flushes the rest at once
        self.assertEqual(ui.pm_queue, '')
        self.assertIsNone(ui.pm_block_raw)
        self.assertEqual(ui.conversation.text.count('一二三四五六七八九十'), 1)   # no duplication

    def test_long_queue_drains_within_the_lag_bound(self):
        ui = self.ui
        text = ''.join(chr(0x4e00 + i % 200) for i in range(330))   # 330 CJK characters
        self.delta(text)
        ticks = 0
        while ui.pm_queue:
            ui._pm_tick()
            ticks += 1
            self.assertLessEqual(ticks, chat_tui.PM_MAX_LAG_TICKS)
        self.assertEqual(ticks, chat_tui.PM_MAX_LAG_TICKS)   # 10 per tick, never 2
        self.assertEqual(self.shown(), text)
        self.assertEqual(ui._pm_rate, chat_tui.PM_CHARS_PER_TICK)   # back to the base rate
        ui._pm_tick()                                         # an idle tick is harmless
        self.assertEqual(self.shown(), text)
        self.delta('甲乙')
        self.delta('丙' * 100)                                # a burst while behind raises it
        self.assertEqual(ui._pm_rate, 4)
        self.assertGreaterEqual(chat_tui.PM_CHARS_PER_TICK / chat_tui.PM_TICK_SECONDS, 40)
        self.assertLessEqual(chat_tui.PM_CHARS_PER_TICK / chat_tui.PM_TICK_SECONDS, 80)
        self.assertLessEqual(chat_tui.PM_MAX_LAG_TICKS * chat_tui.PM_TICK_SECONDS, 1.0)

    def test_turn_end_flushes_everything_before_the_summary(self):
        ui = self.ui
        self.delta('hello world')
        ui.pm_event({'type': 'TURN_END', 'status': 'COMPLETED', 'usage': {}})
        self.assertEqual(ui.pm_queue, '')
        self.assertIsNone(ui.pm_block_raw)
        text = ui.conversation.text
        self.assertIn('PM\n  hello world\n\n0.0 秒', text)   # W56a: the short usage line

    def test_interrupt_and_turn_finish_flush_the_queue(self):
        ui = self.ui
        ui.pm_runtime = Mock()
        self.delta('partial answer')
        ui.interrupt_pm()
        ui.pm_runtime.interrupt.assert_called_once_with()
        # The notice shows below the still-open block (W55); an unfinished word waits.
        self.assertIn('PM\n  partial\n\nllmtool: 已中斷，等待 PM 結束本輪…', ui.conversation.text)
        self.assertEqual(ui.pm_queue, '')
        ui.pm_turn_active, ui.pm_interrupted = True, False
        self.delta('tail', index=1)
        self.assertTrue(ui.conversation.text.endswith(   # same turn: a new block, no new label
            'PM\n  partial answer\n\nllmtool: 已中斷，等待 PM 結束本輪…\n\n'))
        ui.finish_pm_turn()                           # a turn that ends without TURN_END
        self.assertIn('\n  tail', ui.conversation.text)   # same turn: no second label
        self.assertEqual(ui.pm_queue, '')
        self.assertIsNone(ui.pm_block_raw)
        self.assertEqual(ui.pm_state, 'idle')

    def test_queue_notice_during_streaming_never_splits_the_reply(self):
        # W55 F2-b (Owner Warp): "…不同的" | queued notice | "是我們如何運用…" split the reply.
        ui = self.ui
        ui.pm_runtime = Mock()
        self.delta('這是不同的')
        ui._pm_tick()
        ui.input.buffer.text = 'test'
        ui.submit()
        self.assertEqual(list(ui.pm_outbox), ['test'])
        self.delta('，是我們如何運用')
        while ui.pm_queue:
            ui._pm_tick()
        notice = '你（已排入佇列（第 1 則））\n  test'   # W56a: the owner note as 「你」
        self.assertTrue(ui.conversation.text.endswith(
            'PM\n  這是不同的，是我們如何運用\n\n' + notice))
        ui.pm_event({'type': 'TURN_END', 'status': 'COMPLETED', 'usage': {}})
        text = ui.conversation.text
        self.assertIn('PM\n  這是不同的，是我們如何運用\n\n' + notice + '\n\n0.0 秒', text)
        self.assertEqual(text.count(notice), 1)
        self.assertEqual(ui.pm_tail, [])

    def _command_during_streaming(self, command):
        ui = self.ui
        ui.pm_runtime = Mock()
        ui.pm_outbox.extend(['q1', 'q2'])
        self.delta('這是不同的')
        ui._pm_tick()
        ui.input.buffer.text = command
        ui.submit()
        self.assertIsNotNone(ui.pm_block_raw)             # the reply block is still open
        self.delta('，是我們如何運用')
        while ui.pm_queue:
            ui._pm_tick()
        ui.pm_event({'type': 'TURN_END', 'status': 'COMPLETED', 'usage': {}})
        text = ui.conversation.text
        self.assertIn('PM\n  這是不同的，是我們如何運用\n\n> ' + command + '\n\n', text)
        self.assertEqual(text.count('\nPM\n'), 1)
        return text

    def test_queue_list_during_streaming_never_splits_the_reply(self):
        # #652 Codex v1 P2: /pm queue appended mid-reply and split it.
        text = self._command_during_streaming('/pm queue')
        self.assertIn('> /pm queue\n\nPM 佇列：2 則\n  1. q1\n  2. q2', text)

    def test_queue_clear_during_streaming_never_splits_the_reply(self):
        text = self._command_during_streaming('/pm queue clear')
        self.assertIn('llmtool: PM 佇列已清除 2 則（未送出）', text)
        self.assertEqual(list(self.ui.pm_outbox), [])

    def test_queue_send_during_streaming_never_splits_the_reply(self):
        self.ui.pm_outbox_paused = 'Esc 中斷'
        text = self._command_during_streaming('/pm queue send')
        self.assertIn('llmtool: PM 佇列繼續：目前回合結束後送出下一則（共 2 則）', text)
        self.assertIsNone(self.ui.pm_outbox_paused)

    def test_other_commands_and_ctrl_c_during_streaming_never_split_the_reply(self):
        self.ui.pm_target = ('pm-session', '/tmp/pm')
        text = self._command_during_streaming('/pm')
        self.assertIn('> /pm\n\nPM: claude-code session pm-session', text)
        ui = self.ui
        ui.pm_turn_active, ui.pm_interrupted = True, False
        self.delta('第二段', index=1)
        ui._pm_tick()
        ui.request_quit()                                 # Ctrl-C: interrupt + "again exits"
        self.delta('結尾', index=1)
        while ui.pm_queue:
            ui._pm_tick()
        self.assertTrue(ui.conversation.text.endswith(
            '\n  第二段結尾\n\nllmtool: 已中斷，等待 PM 結束本輪…\n\n'
            'llmtool: Ctrl-C again exits (the PM connection is closed on exit)'))

    def test_escape_with_two_queued_pauses_at_once_and_the_reply_stays_whole(self):
        # W55 F2-a: Esc interrupts and pauses the queue; feedback shows before the turn ends.
        ui = self.ui
        ui.pm_runtime = Mock()
        ui.pm_outbox.extend(['q1', 'q2'])
        self.delta('前半')
        ui.interrupt_pm()
        ui.pm_runtime.interrupt.assert_called_once_with()
        self.assertEqual(ui.pm_outbox_paused, 'Esc 中斷')
        self.assertEqual(ui.pm_state, 'interrupting')
        self.assertTrue(ui.pm_turn_active)                # the turn has not ended yet
        self.delta('後半')                                 # text the CLI sent before stopping
        while ui.pm_queue:
            ui._pm_tick()
        self.assertTrue(ui.conversation.text.endswith(
            'PM\n  前半後半\n\nllmtool: 已中斷，等待 PM 結束本輪…\n\n'
            'llmtool: 已中斷；佇列已暫停（剩 2 則）：Enter 送下一則，/pm queue send 全部送出，'
            '/pm queue clear 清空'))
        ui.pm_turn_status = 'INTERRUPTED'
        with patch.object(ui, '_pm_start_turn') as start:
            ui.finish_pm_turn()
            start.assert_not_called()                     # nothing is sent after Esc
            self.assertEqual(list(ui.pm_outbox), ['q1', 'q2'])
            self.assertEqual(ui.conversation.text.count('佇列已暫停'), 1)
            ui.input.buffer.text = 'q3'                   # typed while paused: queued last
            ui.submit()
            start.assert_not_called()
            self.assertEqual(list(ui.pm_outbox), ['q1', 'q2', 'q3'])
            ui.submit()                                   # Enter on an empty draft
            start.assert_called_once()
            self.assertEqual(start.call_args[0][0], 'q1')
            self.assertEqual(list(ui.pm_outbox), ['q2', 'q3'])
            self.assertEqual(ui.pm_outbox_paused, 'Esc 中斷')   # the rest stays paused

    def test_escape_with_an_empty_queue_pauses_drafts_queued_while_interrupting(self):
        ui = self.ui
        ui.pm_runtime = Mock()
        ui.interrupt_pm()
        self.assertIn('llmtool: 已中斷，等待 PM 結束本輪…', ui.conversation.text)
        self.assertIsNone(ui.pm_outbox_paused)            # nothing to pause yet
        ui.input.buffer.text = 'later'
        ui.submit()
        ui.pm_turn_status = 'INTERRUPTED'
        with patch.object(ui, '_pm_start_turn') as start:
            ui.finish_pm_turn()
            start.assert_not_called()
        self.assertEqual(list(ui.pm_outbox), ['later'])
        self.assertEqual(ui.pm_outbox_paused, 'Esc 中斷')
        self.assertIn('佇列已暫停（剩 1 則）', ui.conversation.text)

    def test_enter_on_an_empty_draft_during_the_interrupted_turn_sends_nothing(self):
        ui = self.ui
        ui.pm_runtime = Mock()
        ui.pm_outbox.append('q1')
        ui.interrupt_pm()
        with patch.object(ui, '_pm_start_turn') as start:
            ui.submit()
            start.assert_not_called()
        self.assertIn('PM 本輪尚未結束；結束後按 Enter 送下一則', ui.conversation.text)
        self.assertEqual(list(ui.pm_outbox), ['q1'])

    def test_new_block_tool_and_other_output_flush_the_previous_block(self):
        ui = self.ui
        self.delta('first block', index=0)
        self.delta('second', index=1)                  # a new content block
        self.assertIn('PM\n  first block\n\n', ui.conversation.text)
        self.assertEqual(ui.conversation.text.count('\nPM\n'), 1)   # one label per turn
        self.assertEqual(ui.pm_queue, 'second')
        ui.pm_event({'type': 'TOOL_USE', 'tool_use_id': 't', 'name': 'Read'})
        self.assertIn('\n  second\n\n  ▸ 執行中：Read… 0s', ui.conversation.text)
        self.assertEqual(ui.pm_queue, '')

    def test_split_secret_is_screened_at_every_tick(self):
        key = _fake_key()
        ui = self.ui
        seen = []
        original = ui._pm_render

        def render(final):
            original(final)
            seen.append(ui.conversation.text)
        ui._pm_render = render
        self.delta('your key is ' + key + ' — rotate it')
        while ui.pm_queue:
            ui._pm_tick()
        ui.pm_event({'type': 'ASSISTANT_TEXT', 'text': 'your key is ' + key + ' — rotate it'})
        self.assertTrue(seen)
        for text in seen:
            self.assertNotIn(key[3:9], text)
        self.assertIn('your key is [REDACTED] — rotate it', ui.conversation.text)

    def test_tick_is_scheduled_on_the_running_loop(self):
        ui = self.ui
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)

        async def main():
            self.delta('甲乙丙丁')
            self.assertIsNotNone(ui._pm_tick_handle)
            await asyncio.sleep(chat_tui.PM_TICK_SECONDS * 6)
            self.assertEqual(self.shown(), '甲乙丙丁')
            self.assertIsNone(ui._pm_tick_handle)      # nothing left: no tick pending
            self.delta('戊己')
            ui.close_pm()                              # exit cancels a pending tick
            self.assertIsNone(ui._pm_tick_handle)
        loop.run_until_complete(main())
        self.assertEqual(ui.pm_queue, '戊己')          # exit path leaves the queue; no render


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
        self.observe_patcher = patch.object(pm_runtime.active_pm_inbox, 'observe_claude_session',
                                            side_effect=lambda session, cwd, environ=None: cwd)
        self.observe = self.observe_patcher.start()
        self.addCleanup(self.observe_patcher.stop)
        self.turns = [_full_turn()]
        test = self

        class API(chat_tui_pm.PMRuntimeAPI):
            def runtime(self, session_id, cwd):
                return test.make_runtime(session_id, cwd)

        self.api = API(state_root=self.state, environ={'HOME': str(self.temp_root)})
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
                try:
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
                    return await asyncio.wait_for(task, 5)
                finally:
                    ui.close_pm()

            with tui_cli_signal_scope(ui._handle_signal):
                return ui, self.run_ui(main)

    @staticmethod
    async def until(ui, condition, what='condition'):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if condition(ui):
                return
            await asyncio.sleep(0.01)
        raise AssertionError(what + ' not reached')

    def connected(self, ui):
        return self.until(ui, lambda ui: ui.pm_state == 'idle' and ui.pm_runtime is not None,
                          'PM connected')

    def turn_done(self, ui):
        return self.until(ui, lambda ui: not ui.pm_turn_active, 'PM turn end')

    def join_resume_readers(self):
        readers = [thread for thread in threading.enumerate()
                   if thread.name == 'llmtool-chat-pm-resume']
        for reader in readers:
            reader.join(5)
        self.assertFalse(any(reader.is_alive() for reader in readers),
                         'resume reader did not finish before its patch ended')

    def connect(self):
        return '/pm connect\r'   # uses the saved dedicated binding

    def test_connect_auto_resumes_once_when_taskstore_has_in_flight_work(self):
        self.turns = [_full_turn()]
        record = {'task_id': 'task-1', 'state': 'PM_RESPONDED',
                  'developer': {'state': 'DEVELOPER_DISPATCHING'},
                  'backend_session_semantics': 'ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL',
                  'pm_provenance': {'source': 'ACTIVE_PM_RUNTIME', 'provider_invoked': False}}
        items = chat_tui_status.background_items([record])
        self.assertEqual(items[0]['state'], 'RUNNING')
        with patch.object(chat_tui_status, 'read_background', return_value=items):
            async def resumed(ui):
                await self.connected(ui)
                await self.until(ui, lambda _ui: len(self.fakes[0].users()) == 1,
                                 'resume sent after confirmation')
                await self.turn_done(ui)
                self.join_resume_readers()
            ui, _ = self.drive([self.connect(), resumed])
        self.assertEqual([u['message']['content'][0]['text'] for u in self.fakes[0].users()],
                         ['（自動）重新連線：請接續進行中的工作並回報狀態'])
        self.assertEqual(ui.conversation.text.count(
            '（自動）重新連線：請接續進行中的工作並回報狀態'), 2)  # system line and sent turn

    def test_connect_does_not_auto_resume_when_taskstore_is_empty(self):
        with patch.object(chat_tui_status, 'read_background', return_value=[]):
            async def confirmed_empty(ui):
                await self.connected(ui)
                await self.until(ui, lambda _ui: not ui._auto_resume_unconfirmed,
                                 'empty snapshot confirmation')
                self.join_resume_readers()
            ui, _ = self.drive([self.connect(), confirmed_empty])
        self.assertEqual(self.fakes[0].users(), [])
        self.assertNotIn('（自動）重新連線', ui.conversation.text)

    def test_resume_reader_waits_until_pm_mode_and_history_are_visible(self):
        entered, release = threading.Event(), threading.Event()

        def blocked(session):
            entered.set()
            release.wait(5)
            return [{'role': 'Developer', 'task': 'task-1', 'state': 'RUNNING'}]

        try:
            with patch.object(chat_tui_status, 'read_background', side_effect=blocked):
                async def ready(ui):
                    await self.connected(ui)
                    await self.until(ui, lambda _ui: entered.is_set(), 'resume reader started')
                    self.assertIn('PM mode:', ui.conversation.text)
                    self.assertIn('上次對話', ui.conversation.text)
                    self.assertEqual(self.fakes[0].users(), [])
                    release.set()
                    await self.until(ui, lambda _ui: len(self.fakes[0].users()) == 1,
                                     'confirmed resume')
                    self.join_resume_readers()
                ui, _ = self.drive([self.connect(), ready, self.turn_done])
        finally:
            release.set()
        self.assertEqual(len(self.fakes[0].users()), 1)

    def test_waiting_background_turn_does_not_replace_empty_resume_snapshot(self):
        entered, release = threading.Event(), threading.Event()

        def empty_after_release(_session):
            entered.set()
            release.wait(5)
            return []

        try:
            with patch.object(chat_tui_status, 'read_background', side_effect=empty_after_release):
                async def ready(ui):
                    await self.connected(ui)
                    await self.until(ui, lambda _ui: entered.is_set(), 'resume reader started')
                    ui._pm_background_waiting.append({'runtime': ui.pm_runtime})
                    self.assertTrue(ui._auto_resume_unconfirmed)
                    release.set()
                    await self.until(ui, lambda _ui: not ui._auto_resume_unconfirmed,
                                     'empty snapshot confirmation')
                    self.join_resume_readers()
                    self.assertFalse(ui._auto_resume_pending)
                    self.assertEqual(self.fakes[0].users(), [])
                self.drive([self.connect(), ready])
        finally:
            release.set()

    def run_blocked_resume_cancellation(self, action):
        entered, release = threading.Event(), threading.Event()

        def blocked(_session):
            entered.set()
            release.wait(20)
            return [{'role': 'Developer', 'task': 'task-1', 'state': 'RUNNING'}]

        with patch.object(chat_tui_status, 'read_background', side_effect=blocked):
            try:
                async def act(ui):
                    await self.connected(ui)
                    await self.until(ui, lambda _ui: entered.is_set(), 'reader started')
                    if action == 'draft':
                        ui.submit_pm('owner draft')
                        await self.until(ui, lambda _ui: len(self.fakes[0].users()) == 1,
                                         'draft sent')
                    else:
                        ui.run_display_command('/settings auto-resume off')
                        ui.run_display_command('/settings auto-resume on')
                    release.set()
                    await asyncio.sleep(0.1)
                    self.join_resume_readers()
                steps = [self.connect(), act]
                if action == 'draft':
                    steps.append(self.turn_done)
                self.drive(steps)
            finally:
                release.set()
                self.join_resume_readers()
        texts = [user['message']['content'][0]['text'] for user in self.fakes[0].users()]
        self.assertEqual(texts, ['owner draft'] if action == 'draft' else [])

    def test_draft_cancels_blocked_resume_reader(self):
        self.run_blocked_resume_cancellation('draft')

    def test_settings_off_then_on_cancels_blocked_resume_reader(self):
        self.run_blocked_resume_cancellation('settings')

    def test_draft_queued_during_background_turn_suppresses_confirmed_resume(self):
        self.turns = [[_delta('收到'), _result('收到')]]
        entered, release = threading.Event(), threading.Event()
        readers = []
        items = [{'role': 'Developer', 'task': 'task-1', 'state': 'RUNNING'}]

        def blocked(session):
            entered.set()
            release.wait(20)
            return items

        original_read = chat_tui.ChatTUI._read_auto_resume

        def track_reader(ui, *args):
            readers.append(threading.current_thread())
            entered.set()
            return original_read(ui, *args)

        with patch.object(chat_tui_status, 'read_background', side_effect=blocked), \
                patch.object(chat_tui.ChatTUI, '_read_auto_resume', track_reader):
            try:
                async def flow(ui):
                    await self.connected(ui)
                    await self.until(ui, lambda _ui: bool(readers), 'resume reader entered')
                    turn = dict(runtime=ui.pm_runtime, seq=None, events=[], ended=False)
                    ui.pm_background_start(turn)
                    release.set()
                    await self.until(ui, lambda _ui: not ui._auto_resume_unconfirmed,
                                     'resume result confirmed')
                    readers[0].join(5)
                    self.assertFalse(readers[0].is_alive())
                    self.assertTrue(ui._auto_resume_pending)
                    self.assertFalse(ui._auto_resume_unconfirmed)
                    self.assertEqual(self.fakes[0].users(), [])
                    ui.submit_pm('owner draft')
                    self.assertEqual(list(ui.pm_outbox), ['owner draft'])
                    self.assertFalse(ui._auto_resume_pending)
                    self.assertEqual(self.fakes[0].users(), [])
                    ui.pm_background_event(
                        turn, {'type': 'TURN_END', 'status': 'COMPLETED'})
                    ui.pm_background_end(turn)
                    await self.until(ui, lambda _ui: len(self.fakes[0].users()) == 1,
                                     'queued owner draft sent')
                    await self.turn_done(ui)
                    ui._maybe_auto_resume()
                ui, _ = self.drive([self.connect(), flow])
            finally:
                release.set()
                for reader in readers:
                    reader.join(5)
        texts = [user['message']['content'][0]['text'] for user in self.fakes[0].users()]
        self.assertEqual(texts, ['owner draft'])
        self.assertEqual(list(ui.pm_outbox), [])
        self.assertFalse(ui._auto_resume_sent)
        self.assertNotIn('（自動）重新連線', ui.conversation.text)

    def test_setting_on_connect_uses_isolated_task_store_without_extra_root_read(self):
        from llmtool import task_store
        entered, readers = threading.Event(), []
        calls = []
        original_read = chat_tui.ChatTUI._read_auto_resume

        def track_reader(ui, *args):
            readers.append(threading.current_thread())
            entered.set()
            return original_read(ui, *args)

        def record_root():
            calls.append(True)
            return self.temp_root / 'isolated-task-store'

        with patch.object(task_store, 'default_root', side_effect=record_root), \
                patch.object(chat_tui.ChatTUI, '_read_auto_resume', track_reader):
            try:
                async def on(ui):
                    await self.connected(ui)
                    await self.until(ui, lambda _ui: entered.is_set(), 'resume reader entered')
                    await self.until(ui, lambda _ui: not ui._auto_resume_unconfirmed,
                                     'empty resume result confirmed')
                    readers[0].join(5)
                    self.assertFalse(readers[0].is_alive())
                    self.assertEqual(self.fakes[0].users(), [])
                    self.assertEqual(calls, [])
                self.drive([self.connect(), on])
            finally:
                for reader in readers:
                    reader.join(5)

    def test_setting_off_at_adoption_skips_resume_reader(self):
        resume_starts = []
        real_start = threading.Thread.start

        def track_start(thread, *args, **kwargs):
            if thread.name == 'llmtool-chat-pm-resume':
                resume_starts.append(thread)
            return real_start(thread, *args, **kwargs)

        with patch.object(threading.Thread, 'start', new=track_start):
            def off(ui):
                ui.run_display_command('/settings auto-resume off')

            async def connected(ui):
                await self.connected(ui)
                await asyncio.sleep(0.1)
                self.assertEqual(resume_starts, [])
                self.assertEqual(self.fakes[0].users(), [])
            self.drive([off, self.connect(), connected])

    def test_adoption_with_empty_paused_queue_does_not_arm_resume(self):
        resume_starts = []
        real_start = threading.Thread.start

        def track_start(thread, *args, **kwargs):
            if thread.name == 'llmtool-chat-pm-resume':
                resume_starts.append(thread)
            return real_start(thread, *args, **kwargs)

        def pause_empty_queue(ui):
            ui.pm_outbox_paused = 'PM 已斷線'

        with patch.object(threading.Thread, 'start', new=track_start):
            async def connected(ui):
                await self.connected(ui)
                self.assertEqual(list(ui.pm_outbox), [])
                self.assertIsNotNone(ui.pm_outbox_paused)
                self.assertFalse(ui._auto_resume_pending)
                self.assertEqual(resume_starts, [])
                self.assertEqual(self.fakes[0].users(), [])
            self.drive([pause_empty_queue, self.connect(), connected])

    def test_held_draft_released_during_or_after_resume_read_never_auto_resumes(self):
        for release_during_turn in (True, False):
            with self.subTest(release_during_turn=release_during_turn):
                self.turns = [[_delta('draft reply'), 'HOLD', _result('draft done')],
                              [_delta('resume reply'), _result('resume done')]]
                read_entered, read_release, read_callback = (
                    threading.Event(), threading.Event(), threading.Event())
                resume_starts = []
                real_start = threading.Thread.start
                original_callback = chat_tui.ChatTUI.finish_auto_resume_read

                def controlled_read(_session):
                    if threading.current_thread().name == 'llmtool-chat-pm-resume':
                        read_entered.set()
                        if not read_release.wait(20):
                            raise AssertionError('resume read was not released')
                        return [{'role': 'Developer', 'task': 'task-1', 'state': 'RUNNING'}]
                    return []

                def track_start(thread, *args, **kwargs):
                    if thread.name == 'llmtool-chat-pm-resume':
                        resume_starts.append(thread)
                    return real_start(thread, *args, **kwargs)

                def track_callback(ui, *args):
                    try:
                        return original_callback(ui, *args)
                    finally:
                        read_callback.set()

                def prepare_disconnected(ui):
                    ui.settings['auto_resume'] = 'on'
                    ui.pm_outbox.append('please stop all work')
                    ui.finish_pm_disconnect()
                    self.assertIsNone(ui.pm_runtime)
                    self.assertFalse(ui.pm_turn_active)
                    self.assertEqual(ui.pm_outbox_paused, 'PM 已斷線')

                async def exercise(ui):
                    await self.connected(ui)
                    self.assertEqual(list(ui.pm_outbox), ['please stop all work'])
                    if resume_starts:
                        await self.until(ui, lambda _ui: read_entered.is_set(),
                                         'forbidden resume reader entered')
                    else:
                        self.assertFalse(read_entered.is_set())
                    ui.run_queue_command('send')
                    await self.until(ui, lambda _ui: len(self.fakes[-1].users()) == 1,
                                     'held draft sent')
                    self.assertFalse(ui._auto_resume_pending)
                    fake = self.fakes[-1]
                    await self.until(ui, lambda _ui: bool(fake.gate._events),
                                     'draft turn reached its gate')
                    if release_during_turn:
                        read_release.set()
                        if resume_starts:
                            await self.until(ui, lambda _ui: read_callback.is_set(),
                                             'resume read callback')
                        fake.gate.set()
                    else:
                        fake.gate.set()
                        await self.turn_done(ui)
                        read_release.set()
                        if resume_starts:
                            await self.until(ui, lambda _ui: read_callback.is_set(),
                                             'resume read callback')
                    await self.turn_done(ui)
                    if ui._auto_resume_sent:
                        await self.until(ui, lambda _ui: len(fake.users()) == 2,
                                         'incorrect automatic resume exposed')

                try:
                    with patch.object(chat_tui_status, 'read_background',
                                      side_effect=controlled_read), \
                            patch.object(threading.Thread, 'start', new=track_start), \
                            patch.object(chat_tui.ChatTUI, 'finish_auto_resume_read',
                                         track_callback):
                        ui, _ = self.drive([prepare_disconnected, self.connect(), exercise])
                finally:
                    read_release.set()
                    for fake in self.fakes:
                        fake.gate.set()
                        for thread in fake.threads:
                            thread.join(5)
                    for reader in resume_starts:
                        reader.join(5)
                    self.join_resume_readers()
                texts = [user['message']['content'][0]['text']
                         for user in self.fakes[-1].users()]
                self.assertEqual(texts, ['please stop all work'])
                self.assertFalse(ui._auto_resume_pending)
                self.assertFalse(ui._auto_resume_sent)
                self.assertEqual(resume_starts, [])

    def run_parallel_background_and_resume_reads(self, result):
        background_entered, background_release = threading.Event(), threading.Event()
        resume_entered, resume_release = threading.Event(), threading.Event()
        seen = []
        items = [{'role': 'Developer', 'task': 'cached-task', 'state': 'RUNNING'}]
        readers = []

        def controlled_read(session):
            thread = threading.current_thread()
            seen.append((thread, session))
            if thread.name == 'llmtool-chat-pm-background':
                background_entered.set()
                background_release.wait(20)
                return items
            if thread.name == 'llmtool-chat-pm-resume':
                resume_entered.set()
                resume_release.wait(20)
                return result
            raise AssertionError('unexpected background reader: ' + thread.name)

        try:
            with patch.object(chat_tui_status, 'read_background', side_effect=controlled_read):
                async def start_status(ui):
                    ui.background_key = self.session_id
                    ui.background_loaded = time.monotonic()
                    ui.background = list(items)
                    ui._loop = asyncio.get_running_loop()
                    ui.background_reader = threading.Thread(
                        target=ui._read_background, args=(ui._loop, self.session_id),
                        name='llmtool-chat-pm-background', daemon=True)
                    readers.append(ui.background_reader)
                    ui.background_reader.start()
                    await self.until(ui, lambda _ui: background_entered.is_set(),
                                     'status reader entered')

                async def connected(ui):
                    await self.connected(ui)
                    await self.until(ui, lambda _ui: resume_entered.is_set(),
                                     'resume reader entered independently')
                    self.assertTrue(any(thread.name == 'llmtool-chat-pm-background'
                                       and session == self.session_id
                                       for thread, session in seen))
                    self.assertTrue(any(thread.name == 'llmtool-chat-pm-resume'
                                       and session == self.session_id
                                       for thread, session in seen))
                    self.assertTrue(ui._auto_resume_pending)
                    self.assertTrue(ui._auto_resume_unconfirmed)
                    self.assertEqual(self.fakes[0].users(), [])
                    resume_release.set()
                    await self.until(ui, lambda _ui: not ui._auto_resume_unconfirmed,
                                     'resume result confirmed')
                    self.join_resume_readers()
                    if result:
                        await self.until(ui, lambda _ui: len(self.fakes[0].users()) == 1,
                                         'non-empty snapshot resumes once')
                        await self.turn_done(ui)
                    else:
                        self.assertFalse(ui._auto_resume_pending)
                        self.assertEqual(self.fakes[0].users(), [])

                try:
                    ui, _ = self.drive([start_status, self.connect(), connected])
                    texts = [user['message']['content'][0]['text'] for user in self.fakes[0].users()]
                    self.assertEqual(texts, ['（自動）重新連線：請接續進行中的工作並回報狀態']
                                     if result else [])
                finally:
                    background_release.set()
                    resume_release.set()
                    for reader in readers:
                        reader.join(5)
                    self.join_resume_readers()
        except BaseException:
            background_release.set()
            resume_release.set()
            for reader in readers:
                reader.join(5)
            self.join_resume_readers()
            raise

    def test_parallel_status_reader_and_empty_resume_ignore_cached_items(self):
        self.run_parallel_background_and_resume_reads([])

    def test_parallel_status_reader_and_nonempty_resume_send_once(self):
        self.run_parallel_background_and_resume_reads(
            [{'role': 'Developer', 'task': 'task-1', 'state': 'RUNNING'}])

    def test_connect_stream_tools_and_turn_end(self):
        self.turns = [_full_turn(hold=True)]

        def connected_view(ui):
            self.assertTrue(ui.pm_mode)
            self.assertEqual(ui.status_text(), '▸▸ PM 模式 · auto | claude/default | ' + self.session_id[:8]
                         + ' | 暖機中… | 最新 | F6 滑鼠:開 | F1')   # no chat fields
            self.assertEqual(ui.pm_state, 'idle')   # 暖機中… is the cold-start view of idle
            self.assertIn('PM session ' + self.session_id, ui.conversation.text)
            self.assertIn('dedicated (pm_runtime binding created', ui.conversation.text)
            self.assertIn('上次對話（時間不明）\n  還沒有存下的對話', ui.conversation.text)
            argv = self.fakes[0].argv
            self.assertEqual(argv[argv.index('--session-id') + 1], self.session_id)
            self.assertIn('--include-partial-messages', argv)

        async def partial(ui):
            # The fake holds after 'Hello ': the text reached the pane through typewriter
            # ticks on the loop, not through an end-of-block flush.
            await self.until(ui, lambda ui: 'PM\n  Hello' in ui.conversation.text, 'first delta')
            self.assertTrue(ui.pm_turn_active)
            self.assertIn('回答中', ui.status_text())
            self.assertIn('Esc 中斷', ui.status_text())   # the keys valid now
            self.assertNotIn('暖機中', ui.status_text())   # first output ended the warm-up view
            self.assertNotIn('Hello 老闆', ui.conversation.text)   # streamed, not yet complete
            self.fakes[0].gate.set()

        ui, _ = self.drive([self.connect(), self.connected, connected_view,
                            '請問進度？\r', partial, self.turn_done])
        text = ui.conversation.text
        self.assertRegex(text, '── [0-9]{2}:[0-9]{2}\n你\n  請問進度？')   # rule, label, body
        self.assertEqual(text.count('Hello 老闆'), 1)                # assistant text not doubled
        self.assertRegex(text, '  ▸ ✗ Read docs/plan.md（[0-9]+s，失敗）')   # what it did, one line
        self.assertNotIn('SECRET_COMMAND', text)                    # never another input field
        self.assertNotIn('SECRET_OUTPUT', text)                     # never the tool output
        self.assertNotIn('· tool', text)
        self.assertNotIn('自動拒絕', text)
        self.assertIn('\n  after tool', text)                       # new block after the tool
        self.assertEqual(text.count('\nPM\n'), 1)                  # one PM label per turn
        self.assertRegex(text, '\n[0-9]+\\.[0-9] 秒 · 12 tokens')   # short usage line
        self.assertNotIn('provider-reported', text)
        self.assertNotIn('SECRET', text)
        self.assertEqual(ui.pm_state, 'idle')
        self.assertEqual([u['message']['content'][0]['text'] for u in self.fakes[0].users()],
                         ['請問進度？'])
        self.assertEqual(self.session.history, [])
        self.client.complete.assert_not_called()

    def test_warm_up_status_lasts_until_the_first_turns_first_output(self):
        # Owner W40/W41 (A: no warm-up message is sent): the status shows 暖機中… from connect
        # until the first turn produces output; later turns show working as before.
        self.turns = [['HOLD', _delta('早安'), _assistant({'type': 'text', 'text': '早安'}),
                       _result('早安')],
                      [_delta('再次'), 'HOLD', _assistant({'type': 'text', 'text': '再次'}),
                       _result('再次')]]

        def warm_idle(ui):
            self.assertTrue(ui.pm_warm)
            self.assertIn('暖機中…', ui.status_text())
            self.assertNotIn('待命', ui.status_text())
            self.assertNotIn('暖機', ui.conversation.text)   # status only; nothing was sent
            self.assertEqual(len(self.fakes[0].users()), 0)

        async def warm_working(ui):
            await self.until(ui, lambda ui: len(self.fakes[0].users()) == 1, 'turn sent')
            self.assertTrue(ui.pm_turn_active)
            self.assertRegex(ui.status_text(), '暖機中… [0-9]+\\.[0-9]s')
            self.assertNotIn('回答中', ui.status_text())
            self.fakes[0].gate.set()

        def answered(ui):
            self.assertFalse(ui.pm_warm)
            self.assertIn('PM\n  早安', ui.conversation.text)
            self.assertIn(' 待命 ', ui.status_text())
            self.assertNotIn('暖機', ui.status_text())

        async def second(ui):
            await self.until(ui, lambda ui: 'PM\n  再次' in ui.conversation.text, 'second delta')
            self.assertIn('回答中', ui.status_text())
            self.assertNotIn('暖機', ui.status_text())
            self.fakes[0].gate.set()

        ui, _ = self.drive([self.connect(), self.connected, warm_idle, '早安\r', warm_working,
                            self.turn_done, answered, '再來\r', second, self.turn_done,
                            '/pm disconnect\r',
                            lambda ui: self.until(ui, lambda ui: ui.pm_runtime is None)])
        self.assertFalse(ui.pm_warm)
        self.assertEqual(len(self.fakes[0].users()), 2)   # the Owner's two drafts, nothing else

    def test_repeated_enter_resize_and_redraw_send_one_turn_and_queue_the_draft(self):
        self.turns = [_full_turn(hold=True), [_delta('ok'), _result('ok')]]
        size = [Size(rows=24, columns=80)]

        def resize(ui):
            size[0] = Size(rows=10, columns=40)
            ui.app._on_resize()
            ui.app.renderer.render(ui.app, ui.app.layout)

        async def busy(ui):
            await self.until(ui, lambda ui: 'PM\n  Hello' in ui.conversation.text)
            self.assertIn('你（已排入佇列（第 1 則））\n  second', ui.conversation.text)
            self.assertEqual(ui.input.text, '')            # the draft left the editor
            self.assertEqual(list(ui.pm_outbox), ['second'])
            self.assertEqual(len(self.fakes[0].users()), 1)   # empty Enters sent nothing
            self.fakes[0].gate.set()

        ui, _ = self.drive([self.connect(), self.connected, 'one\r', '\r\r', resize,
                            'second\r', busy, self.turn_done], size=size)
        self.assertEqual([u['message']['content'][0]['text'] for u in self.fakes[0].users()],
                         ['one', 'second'])
        self.assertIn('你（自佇列送出，剩 0 則）\n  second', ui.conversation.text)

    def test_three_drafts_typed_during_a_turn_are_sent_in_order_one_turn_at_a_time(self):
        # W52 F2: Enter during a turn queues (FIFO); each COMPLETED turn sends the next.
        self.turns = [[_delta('第一'), 'HOLD', _result('第一')],
                      [_delta('A'), 'HOLD', _result('A')],
                      [_delta('B'), _result('B')],
                      [_delta('C'), _result('C')]]

        async def queued(ui):
            await self.until(ui, lambda ui: len(ui.pm_outbox) == 3, 'three queued')
            text = ui.conversation.text
            for number, draft in ((1, 'a'), (2, 'b'), (3, 'c')):
                self.assertIn('你（已排入佇列（第 %d 則））\n  %s' % (number, draft), text)
            self.assertIn('佇列 3', ui.status_text())
            self.assertEqual(len(self.fakes[0].users()), 1)
            self.fakes[0].gate.set()

        async def second_turn(ui):
            await self.until(ui, lambda ui: len(self.fakes[0].users()) == 2, 'a sent')
            self.assertTrue(ui.pm_turn_active)
            self.assertEqual(list(ui.pm_outbox), ['b', 'c'])   # one turn at a time
            self.assertIn('佇列 2', ui.status_text())
            self.fakes[0].gate.set()

        ui, _ = self.drive([self.connect(), self.connected, 'first\r', 'a\r', 'b\r', 'c\r',
                            queued, second_turn,
                            lambda ui: self.until(ui, lambda ui: not ui.pm_turn_active
                                                  and not ui.pm_outbox, 'queue drained')])
        self.assertEqual([u['message']['content'][0]['text'] for u in self.fakes[0].users()],
                         ['first', 'a', 'b', 'c'])
        self.assertEqual(len(re.findall('\n[0-9]+\\.[0-9] 秒 · 12 tokens', ui.conversation.text)), 4)
        self.assertNotIn('佇列', ui.status_text())

    def test_failed_turn_pauses_the_queue_until_queue_send(self):
        self.turns = [[_delta('x'), 'HOLD', _result('boom', is_error=True, subtype='error')],
                      [_delta('later'), _result('later')]]

        async def queued(ui):
            await self.until(ui, lambda ui: len(ui.pm_outbox) == 1, 'queued')
            self.fakes[0].gate.set()

        async def paused(ui):
            await self.turn_done(ui)
            await asyncio.sleep(0.1)
            self.assertEqual(len(self.fakes[0].users()), 1)     # nothing more was sent
            self.assertEqual(list(ui.pm_outbox), ['next one'])
            text = ui.conversation.text
            self.assertIn('PM 佇列暫停，沒有送出：1 則，暫停（上一回合失敗 PM_PROCESS_EXITED）', text)
            self.assertIn('  1. next one', text)
            self.assertIn('/pm queue send 繼續送出', text)
            self.assertIn('佇列 1 暫停', ui.status_text())

        ui, _ = self.drive([self.connect(), self.connected, 'go\r', 'next one\r', queued, paused,
                            '/pm queue\r',
                            lambda ui: self.assertIn('PM 佇列：1 則，暫停', ui.conversation.text),
                            '/pm queue send\r',
                            lambda ui: self.until(ui, lambda ui: not ui.pm_outbox
                                                  and not ui.pm_turn_active, 'resumed')])
        self.assertEqual([u['message']['content'][0]['text'] for u in self.fakes[0].users()],
                         ['go', 'next one'])

    def test_permission_denied_tool_pauses_the_queue_even_when_the_turn_completes(self):
        # #636 Codex review v1: a refused tool is a refused turn; the queue must not go on.
        denied = {'type': 'user', 'message': {'content': [
            {'type': 'tool_result', 'tool_use_id': 't1', 'content': 'Permission denied',
             'is_error': True, 'permission_denial': True}]}}
        self.turns = [[_delta('x'), 'HOLD',
                       _assistant({'type': 'tool_use', 'id': 't1', 'name': 'Bash', 'input': {}}),
                       denied, _assistant({'type': 'text', 'text': 'could not run it'}),
                       _result('could not run it')],
                      [_delta('later'), _result('later')]]

        async def queued(ui):
            await self.until(ui, lambda ui: len(ui.pm_outbox) == 1, 'queued')
            self.fakes[0].gate.set()

        async def paused(ui):
            await self.turn_done(ui)
            await asyncio.sleep(0.1)
            text = ui.conversation.text
            self.assertRegex(text, '▸ ✗ Bash（[0-9]+s，自動拒絕）')
            self.assertRegex(text, '\n[0-9]+\\.[0-9] 秒')
            self.assertEqual(len(self.fakes[0].users()), 1)     # nothing more was sent
            self.assertEqual(list(ui.pm_outbox), ['next one'])
            self.assertIn('PM 佇列暫停，沒有送出：1 則，暫停（權限被拒）', text)
            self.assertIn('  1. next one', text)
            self.assertIn('佇列 1 暫停', ui.status_text())

        ui, _ = self.drive([self.connect(), self.connected, 'go\r', 'next one\r', queued, paused,
                            '/pm queue send\r',
                            lambda ui: self.until(ui, lambda ui: not ui.pm_outbox
                                                  and not ui.pm_turn_active, 'resumed')])
        self.assertEqual([u['message']['content'][0]['text'] for u in self.fakes[0].users()],
                         ['go', 'next one'])
        self.assertIsNone(ui.pm_outbox_paused)

    def _race(self, interrupt):
        """A turn that ends COMPLETED although an interrupt was requested meanwhile."""
        self.turns = [[_delta('一'), 'HOLD', _result('done')], [_delta('二'), _result('next')]]

        async def queued(ui):
            await self.until(ui, lambda ui: len(ui.pm_outbox) == 1
                             and 'PM\n  一' in ui.conversation.text, 'queued mid-turn')
            ui.pm_runtime.interrupt = Mock()   # pm_runtime never hears it: the turn completes
            interrupt(ui)
            self.assertTrue(ui.pm_interrupted)
            self.fakes[0].gate.set()

        ui, _ = self.drive([self.connect(), self.connected, 'go\r', 'next one\r', queued,
                            lambda ui: self.until(ui, lambda ui: not ui.pm_turn_active
                                                  and (ui.pm_outbox_paused or not ui.pm_outbox),
                                                  'turn end'),
                            lambda ui: asyncio.sleep(0.1)])
        self.assertRegex(ui.conversation.text, '\n[0-9]+\\.[0-9] 秒')
        return ui

    def test_ctrl_c_then_a_completed_turn_end_still_pauses_the_queue(self):
        # #636 Codex review v2 finding 1: a COMPLETED that raced Ctrl-C sent the next one.
        ui = self._race(lambda ui: ui.request_quit())
        self.assertEqual(len(self.fakes[0].users()), 1)
        self.assertEqual(list(ui.pm_outbox), ['next one'])
        self.assertIn('暫停（Ctrl-C 中斷）', ui.conversation.text)

    def test_escape_then_a_completed_turn_end_still_pauses_the_queue(self):
        # W55 (Owner decision A): after Esc nothing is sent, even if the turn COMPLETED.
        ui = self._race(lambda ui: ui.interrupt_pm())
        self.assertEqual(len(self.fakes[0].users()), 1)
        self.assertEqual(list(ui.pm_outbox), ['next one'])
        self.assertEqual(ui.pm_outbox_paused, 'Esc 中斷')

    def test_disconnect_mid_turn_leaves_no_busy_timer_and_ignores_the_old_worker(self):
        # #636 Codex review v2 finding 2: after a disconnect mid-turn, append() redrew the
        # busy line and scheduled another tick.
        self.turns = [_thinking() + ['HOLD', _result('late')]]
        seen = {}

        async def thinking(ui):
            await self.until(ui, lambda ui: '思考中…（' in ui._pm_busy_line(),
                             'busy line')
            self.assertIsNotNone(ui._pm_busy_handle)
            seen['seq'] = ui._pm_turn_seq

        async def disconnected(ui):
            await self.until(ui, lambda ui: ui.pm_runtime is None and not ui.pm_pending,
                             'disconnected')
            self.assertFalse(ui.pm_turn_active)
            self.assertFalse(ui._pm_busy_wanted())
            self.assertIsNone(ui._pm_busy_handle)
            ui.append('later output')
            self.assertIsNone(ui._pm_busy_handle)
            self.assertTrue(ui.conversation.text.endswith('later output'))
            await asyncio.sleep(1.3)                  # a surviving tick would redraw by now
            self.assertTrue(ui.conversation.text.endswith('later output'))
            # The old turn's worker posting late is ignored.
            before = ui.conversation.text
            ui.pm_event({'type': 'TEXT_DELTA', 'text': 'stale', 'block_index': 0}, seen['seq'])
            ui.finish_pm_turn(seen['seq'])
            self.assertEqual(ui.conversation.text, before)

        ui, _ = self.drive([self.connect(), self.connected, 'think\r', thinking,
                            '/pm disconnect\r', disconnected])
        self.assertNotIn('思考中', ui.conversation.text)
        self.assertNotIn('處理中', ui.conversation.text)

    def test_exit_mid_turn_cancels_every_timer(self):
        self.turns = [_thinking() + ['HOLD', _result('late')]]

        async def thinking(ui):
            await self.until(ui, lambda ui: ui._pm_busy_handle is not None, 'busy line')

        ui, _ = self.drive([self.connect(), self.connected, 'think\r', thinking])
        # drive() exits the app and runs close_pm(), as run() does on exit.
        self.assertFalse(ui.pm_turn_active)
        self.assertIsNone(ui._pm_busy_handle)
        self.assertIsNone(ui._pm_tick_handle)
        self.assertIsNone(ui._autoscroll_handle)
        self.assertIsNone(ui._drag_copy_handle)

    def test_queue_send_during_a_pm_request_worker_does_not_resume_into_a_stall(self):
        self.turns = [[_delta('x'), 'HOLD', _result('boom', is_error=True, subtype='error')]]

        async def paused(ui):
            await self.until(ui, lambda ui: len(ui.pm_outbox) == 1, 'queued')
            self.fakes[0].gate.set()
            await self.turn_done(ui)
            await self.until(ui, lambda ui: ui.pm_outbox_paused, 'paused')
            ui.pm_pending = True               # a /pm history worker is running
            ui.run_queue_command('send')
            self.assertTrue(ui.pm_outbox_paused)
            self.assertIn('/pm queue send again when it ends', ui.conversation.text)
            ui.pm_pending = False

        ui, _ = self.drive([self.connect(), self.connected, 'go\r', 'wait\r', paused])
        self.assertEqual(len(self.fakes[0].users()), 1)

    def test_escape_pauses_the_queue_enter_sends_one_ctrl_c_pauses_and_clear_counts(self):
        # W55 (Owner decision A): Esc with two queued drafts pauses the queue at once.
        self.turns = [[_delta('one'), 'HOLD', _result('stopped')],
                      [_delta('two'), 'HOLD', _result('stopped')]]

        async def streaming(ui):
            await self.until(ui, lambda ui: ui.pm_turn_active and ui.pm_block_raw is not None)

        def at_escape(ui):
            # Feedback at once, before the turn ends; the reply above it stays whole.
            self.assertTrue(ui.pm_turn_active)
            self.assertEqual(ui.pm_outbox_paused, 'Esc 中斷')
            self.assertIn('llmtool: 已中斷，等待 PM 結束本輪…', ui.conversation.text)
            self.assertIn('佇列已暫停（剩 2 則）：Enter 送下一則', ui.conversation.text)

        async def after_escape(ui):
            await self.turn_done(ui)
            await asyncio.sleep(0.2)
            self.assertEqual(len(self.fakes[0].users()), 1)   # nothing was auto-sent
            self.assertEqual(list(ui.pm_outbox), ['q1', 'q2'])
            self.assertIn('佇列 2 暫停', ui.status_text())

        async def after_enter(ui):
            await self.until(ui, lambda ui: len(self.fakes[0].users()) == 2, 'Enter sent one')
            self.assertEqual(list(ui.pm_outbox), ['q2'])
            self.assertEqual(ui.pm_outbox_paused, 'Esc 中斷')

        async def after_ctrl_c(ui):
            await self.turn_done(ui)
            await asyncio.sleep(0.1)
            self.assertEqual(len(self.fakes[0].users()), 2)
            self.assertEqual(list(ui.pm_outbox), ['q2'])
            self.assertFalse(ui.app.is_done)

        ui, _ = self.drive([self.connect(), self.connected, 'start\r', streaming, 'q1\r', 'q2\r',
                            lone_escape, at_escape, after_escape, '\r', after_enter, streaming,
                            lambda ui: ui.request_quit(), after_ctrl_c,
                            '/pm queue clear\r',
                            lambda ui: self.assertIn('PM 佇列已清除 1 則（未送出）',
                                                     ui.conversation.text)])
        self.assertEqual(list(ui.pm_outbox), [])

    def test_disconnect_keeps_the_queue_listed_and_exit_reports_it(self):
        self.turns = [[_delta('one'), 'HOLD', _result('one')]]

        async def queued(ui):
            await self.until(ui, lambda ui: len(ui.pm_outbox) == 1, 'queued')

        async def disconnected(ui):
            # hd665: 'start' was mid-turn, so its fate is unconfirmed: held UNKNOWN at the
            # head of the queue (never re-sent by itself), ahead of the waiting draft.
            await self.until(ui, lambda ui: ui.pm_runtime is None and not ui.pm_turn_active
                             and len(ui.pm_outbox) == 2, 'disconnected')
            self.assertEqual(list(ui.pm_outbox), ['start', 'wait'])
            self.assertIn('（UNKNOWN），未自動重送', ui.conversation.text)
            self.assertIn('PM disconnected; chat mode', ui.conversation.text)
            self.assertEqual(ui.pm_outbox_paused, 'UNKNOWN')   # stays paused: nothing sent

        def first_ctrl_c(ui):
            ui.request_quit()
            self.assertFalse(ui.app.is_done)
            self.assertIn('PM 佇列還有 2 則未送出', ui.conversation.text)

        ui, result = self.drive([self.connect(), self.connected, 'start\r', 'wait\r', queued,
                                 '/pm disconnect\r', disconnected, first_ctrl_c,
                                 lambda ui: ui.request_quit()])
        self.assertEqual(result, 0)
        self.assertEqual(len(self.fakes[0].users()), 1)
        self.assertIn('  1. start', ui.unsent_queue_text())
        self.assertIn('  2. wait', ui.unsent_queue_text())

    def test_mode_switch_never_starts_a_chat_or_task_request_during_a_pm_turn(self):
        # W52 B1 (Owner QA probe, ported): /task mode off, then Enter during a PM turn
        # started a chat request beside it. Now the draft is kept and nothing is sent.
        self.turns = [_full_turn(hold=True)]

        async def active(ui):
            await self.until(ui, lambda ui: ui.pm_turn_active and ui.pm_block_raw is not None)

        async def refused(ui):
            await asyncio.sleep(0.1)
            self.assertTrue(ui.pm_turn_active)
            self.assertEqual(self.factory.call_count, 0)
            self.assertEqual(ui.input.text, 'chat while PM active')     # draft kept
            self.assertIn('a PM turn is in progress; this draft was not sent (input kept)',
                          ui.conversation.text)
            self.assertFalse(ui.busy)
            self.assertEqual(list(ui.pm_outbox), [])                    # not queued for the PM

        async def task_refused(ui):
            await asyncio.sleep(0.1)
            self.assertIn('a PM turn is in progress; /task submit was not sent',
                          ui.conversation.text)
            self.assertFalse(ui.busy)
            ui.input.buffer.reset()
            ui.input.buffer.insert_text('chat while PM active')
            self.fakes[0].gate.set()

        ui, _ = self.drive([self.connect(), self.connected, 'PM request\r', active,
                            '/task mode off\r', 'chat while PM active\r', refused,
                            lambda ui: ui.input.buffer.reset(),
                            '/task submit --provider second hello\r', task_refused,
                            self.turn_done, '\r', self.idle])
        self.assertEqual(self.factory.call_count, 1)        # sent once the PM turn ended
        self.assertEqual(len(self.fakes[0].users()), 1)

    def test_escape_interrupts_the_pm_turn_with_focus_in_the_conversation_pane(self):
        # W52 B2 (Owner QA probe, ported): Tab into the conversation, then a lone Esc.
        patch('llmtool.chat_tui_view.copy_to_clipboard', side_effect=AssertionError).start()
        self.turns = [[_delta('one'), 'HOLD', _result('stopped')],
                      [_delta('two'), _result('two')]]

        async def active(ui):
            await self.until(ui, lambda ui: ui.pm_turn_active and ui.pm_block_raw is not None)

        def reading(ui):
            self.assertTrue(ui.app.layout.has_focus(ui.conversation))
            ui.view.anchor, ui.view.head = 0, 6

        def interrupted(ui):
            self.assertTrue(ui.pm_interrupted)
            self.assertEqual(ui.view.selected_range(), (0, 6))   # the selection stays

        ui, _ = self.drive([self.connect(), self.connected, 'PM request\r', active, 'queued\r',
                            '\t', reading, lone_escape, interrupted, self.turn_done,
                            lambda ui: self.assertEqual(ui.pm_outbox_paused, 'Esc 中斷'),
                            lambda ui: ui.run_queue_command('send'),
                            lambda ui: self.until(ui, lambda ui: not ui.pm_turn_active
                                                  and not ui.pm_outbox, 'queue resumed')])
        self.assertIn('\n已中斷 · ', ui.conversation.text)
        self.assertEqual([u['message']['content'][0]['text'] for u in self.fakes[0].users()],
                         ['PM request', 'queued'])     # Esc paused the queue; send resumed it

    def test_busy_line_ticks_while_reply_streams_and_disappears_at_turn_end(self):
        self.turns = [_thinking() + ['HOLD', _delta('答案', index=1), 'HOLD',
                                     _assistant({'type': 'text', 'text': '答案'}), _result('答案')]]
        seen = {}

        async def working(ui):
            await self.until(ui, lambda ui: ui.pm_state == 'thinking', 'thinking')
            self.assertIn('思考中…（0 秒）', ui._pm_busy_line())
            await asyncio.sleep(1.2)
            self.assertIn('思考中…（1 秒）', ui._pm_busy_line())
            self.assertNotIn('思考中', ui.conversation.text)
            self.assertNotIn('PRIVATE_REASONING', ui.conversation.text)
            self.fakes[0].gate.set()

        async def text_arrived(ui):
            await self.until(ui, lambda ui: 'PM\n  答案' in ui.conversation.text, 'text')
            seen['text'] = ui.conversation.text
            self.fakes[0].gate.set()

        ui, _ = self.drive([self.connect(), self.connected, '想一想\r', working, text_arrived,
                            self.turn_done])
        self.assertNotIn('思考中', seen['text'])
        self.assertFalse(ui._pm_busy_wanted())
        self.assertNotIn('思考中', ui.conversation.text)
        self.assertNotIn('處理中', ui.conversation.text)
        self.assertIsNone(ui._pm_busy_handle)

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
            self.assertIn('已中斷，等待 PM 結束本輪…', ui.conversation.text)
            self.assertIn('\n已中斷 · ', ui.conversation.text)
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

    def test_pm_thinking_is_status_only_and_never_shown_as_the_answer(self):
        # Owner #304: reasoning must not render as the PM answer; at most a 思考中… status.
        self.turns = [_thinking() + ['HOLD', _delta('台北101', index=1),
                      _assistant({'type': 'thinking', 'thinking': 'PRIVATE_REASONING', 'signature': 's'},
                                 {'type': 'text', 'text': '台北101'}),
                      _result('台北101')]]

        async def thinking(ui):
            await self.until(ui, lambda ui: ui.pm_state == 'thinking', 'thinking status')
            self.assertIn('思考中', ui.status_text())
            self.assertNotIn('\nPM\n', ui.conversation.text)   # nothing reached the pane
            self.assertNotIn('PRIVATE_REASONING', ui.conversation.text)
            self.fakes[0].gate.set()

        ui, _ = self.drive([self.connect(), self.connected, '說出台北觀光地點\r', thinking,
                            self.turn_done])
        text = ui.conversation.text
        self.assertIn('PM\n  台北101', text)
        self.assertNotIn('PRIVATE_REASONING', text)
        self.assertNotIn('思考中', text)
        self.assertEqual(ui.pm_state, 'idle')
        self.assertNotIn('思考中', ui.status_text())

    def test_pm_status_field_fits_a_narrow_terminal(self):
        size = [Size(rows=24, columns=80)]

        def narrow(ui):
            size[0] = Size(rows=24, columns=12)
            text = ui.status_text()
            self.assertLessEqual(get_cwidth(text), 12)
            self.assertTrue(text.startswith('▸▸ PM'))

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
                                '上次對話（') == 2)])
        text = ui.conversation.text
        self.assertNotIn(key[3:9], text)
        self.assertIn('▸ 用過的工具：[withheld]', text)

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
        self.assertIn('no dedicated PM binding is saved. /pm new', ui.conversation.text)
        self.assertIsNone(ui.pm_runtime)
        cwd = str(self.temp_root)
        ui, _ = self.drive(['/pm new ' + cwd + '\r', self.pending_done,
                            self.connect(), self.connected])
        created = self.api.current_binding()['native_session_id']
        self.assertIn('Dedicated PM session ' + created + ' dedicated binding created',
                      ui.conversation.text)
        self.assertIn('the session starts on the first message', ui.conversation.text)
        self.assertEqual(ui.pm_target[0], created)
        argv = self.fakes[-1].argv
        self.assertEqual(argv[argv.index('--session-id') + 1], created)
        ui, _ = self.drive(['/pm connect ' + created + ' ' + cwd + '\r', self.connected])
        self.assertEqual(ui.pm_target, (created, cwd))

    def test_pm_list_is_compact_bounded_and_selectable_by_index_or_prefix(self):
        repo_root = (self.temp_root / 'repo').resolve()
        repo_root.mkdir()
        self.api = type(self.api)(state_root=self.temp_root / 'pm-list-state',
                                  environ={'HOME': str(self.temp_root)})
        bindings = [self.api.create_dedicated(str(repo_root))]
        for _index in range(11):
            bindings.append(self.api.create_dedicated(str(repo_root)))
        with patch.object(chat_tui_theme, 'repo_info',
                          return_value=(str(repo_root), 'test-repo')):
            ui, _ = self.drive(['/pm list\r'])
        lines = [line for line in ui.conversation.text.splitlines() if line.startswith('  ')]
        self.assertEqual(len([line for line in lines if line[2:3].isdigit()]), 10)
        self.assertIn('… 2 more (/pm list --all)', ui.conversation.text)
        self.assertIn('cwd .', ui.conversation.text)
        self.assertNotIn('created ', ui.conversation.text)
        self.assertNotIn(bindings[0]['native_session_id'], ui.conversation.text)
        self.assertIn(bindings[-1]['native_session_id'][:8], lines[0])
        self.assertRegex(lines[0], r'^  1\. claude-code · account default · [0-9a-f]{8} · [0-9]+[smhd] ago · cwd \.')
        with patch.object(chat_tui_theme, 'repo_info',
                          return_value=(str(repo_root), 'test-repo')):
            ui, _ = self.drive(['/pm list --all\r'])
        self.assertEqual(len([line for line in ui.conversation.text.splitlines()
                              if re.match(r'^  \d+\.', line)]), 12)
        ui, _ = self.drive(['/pm use 1\r'])
        self.assertEqual(self.api.current_binding()['native_session_id'],
                         bindings[-1]['native_session_id'])
        first_id = bindings[-1]['native_session_id']
        ui, _ = self.drive(['/pm use ' + first_id[:8] + '\r'])
        self.assertEqual(self.api.current_binding()['native_session_id'], first_id)
        self.assertIn('PM selected: claude-code account default · ' + first_id[:8]
                      + '; /pm connect to connect', ui.conversation.text)
        self.assertNotIn(first_id, ui.conversation.text.split('PM selected:')[-1])

    def test_pm_accounts_guidance_named_checks_and_invalid_options(self):
        ui, _ = self.drive(['/pm accounts\r', '/pm accounts --check\r'])
        for text in ('accounts in ~/.config/llmtool/config.yaml', 'claude-NAME', 'codex-NAME',
                     'built-in default Claude account is used meanwhile'):
            self.assertIn(text, ui.conversation.text)

        self.api.accounts = lambda: [{'name': 'claude-test', 'kind': 'claude', 'status': 'private'},
                                     {'name': 'codex-test', 'kind': 'codex', 'status': 'private'}]
        self.api.account_checks = lambda: [True, False]
        ui, _ = self.drive(['/pm accounts\r'])
        self.assertIn('claude-test · claude · folder private', ui.conversation.text)
        self.assertIn('codex-test · codex · folder private', ui.conversation.text)
        ui, _ = self.drive(['/pm accounts --check\r'])
        self.assertIn('claude-test: logged in yes', ui.conversation.text)
        self.assertIn('codex-test: logged in no', ui.conversation.text)

        fake_count = len(self.fakes)
        ui, _ = self.drive(['/pm new --acount name\r', '/pm accounts --acount\r',
                            '/pm accounts --check extra\r', '/pm use --acount value\r'])
        self.assertIn('unknown option --acount', ui.conversation.text)
        self.assertIn('usage: /pm new [--runtime claude|codex]', ui.conversation.text)
        self.assertEqual(ui.conversation.text.count('unknown option --acount'), 3)
        self.assertNotIn('unknown option --check', ui.conversation.text)
        self.assertIn('usage: /pm accounts [--check]', ui.conversation.text)
        self.assertEqual(ui.conversation.text.count('usage: /pm accounts [--check]'), 2)
        self.assertEqual(ui.conversation.text.count(
            'usage: /pm new [--runtime claude|codex] [--account NAME] [CWD]'), 1)
        self.assertEqual(ui.conversation.text.count('usage: /pm use N|SESSION'), 1)
        self.assertNotIn('       /pm list [--all]', ui.conversation.text)
        self.assertEqual(len(self.fakes), fake_count)

    def test_pm_paths_with_spaces_are_quoted_or_escaped_and_bad_quotes_are_explained(self):
        # W52 B3: text.split() broke "/private/tmp/tui qa" into two words.
        spaced = self.temp_root / 'tui qa'
        spaced.mkdir()
        calls = []
        real = self.api.create_dedicated
        self.api.create_dedicated = lambda cwd, runtime='claude-code', account=None: (
            calls.append((cwd, runtime, account)) or real(cwd, runtime, account))
        ui, _ = self.drive(['/pm new "%s"\r' % spaced, self.pending_done,
                            '/pm new %s\r' % str(spaced).replace(' ', '\\ '), self.pending_done,
                            '/pm new "%s\r' % spaced, self.pending_done])
        self.assertEqual(calls, [(str(spaced), 'claude-code', None),
                                 (str(spaced), 'claude-code', None)])
        text = ui.conversation.text
        self.assertEqual(text.count('dedicated binding created (cwd ' + str(spaced)), 2)
        self.assertIn('llmtool: /pm: unbalanced quote or trailing backslash', text)
        self.assertIn('usage: /pm new [--runtime claude|codex]', text)
        self.assertNotIn('Traceback', text)
        created = self.api.current_binding()['native_session_id']
        ui, _ = self.drive(['/pm connect %s "%s"\r' % (created, spaced), self.connected])
        self.assertEqual(ui.pm_target, (created, str(spaced)))

    def test_not_dedicated_and_live_elsewhere_are_refused(self):
        import uuid as _uuid
        other = str(_uuid.uuid4())
        ui, _ = self.drive(['/pm connect ' + other + ' ' + str(self.temp_root) + '\r',
                            self.pending_done])
        self.assertIn('PM runtime error PM_SESSION_NOT_DEDICATED', ui.conversation.text)
        self.assertIn('/pm new', ui.conversation.text)
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
        self.assertRegex(text, '  ▸ 使用工具 2 次（1 次失敗）：✗ Bash（[0-9]+s，自動拒絕）')
        ui.toggle_tools()                                  # F7: one line per tool
        text = ui.conversation.text
        self.assertRegex(text, '  ▾ 使用工具 2 次（1 次失敗）\n'
                               '      ✗ Bash（[0-9]+s，自動拒絕）\n      ✓ Read（[0-9]+s）')
        self.assertNotIn('file content', text)

    def test_fresh_binding_connects_without_transcript(self):
        ui, _ = self.drive([self.connect(), self.connected])
        self.assertTrue(ui.pm_mode)
        self.assertFalse(self.observe.called)
        self.assertFalse(self.binding['session_started'])
        self.assertIn('--session-id', self.fakes[0].argv)

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
        self.assertIn('PM\n  part A', text)
        self.assertIn('\n\n  part B end', text)          # a new content block opens a new PM block
        self.assertIn('PM runtime error PM_QUOTA_EXHAUSTED; retry when (TUI guidance): the PM', text)
        self.assertIn('\n錯誤 · ', text)

    def test_esc_enter_is_still_a_newline_when_no_turn_runs(self):
        ui, _ = self.drive([self.connect(), self.connected, 'a', '\x1b\r', 'b',
                            lambda ui: self.assertEqual(ui.input.text, 'a\nb')])
        self.assertEqual(self.fakes[0].users(), [])

    def test_connect_errors_show_code_and_recovery(self):
        binding = self.pm_runtime.current_binding(self.session_id, state_root=self.state)
        binding['session_started'] = True
        self.pm_runtime._write_json_atomic(
            self.state, self.pm_runtime._binding_path(self.state, self.session_id), binding)
        self.observe_patcher.stop()
        with patch.object(self.pm_runtime.active_pm_inbox, 'observe_claude_session',
                          side_effect=chat_tui.LLMToolError('PRIVATE path detail')):
            ui, _ = self.drive([self.connect(), lambda ui: self.until(
                ui, lambda ui: not ui.pm_pending)])
        self.assertIn('PM not connected. PM runtime error PM_SESSION_INVALID', ui.conversation.text)
        self.assertNotIn('PRIVATE', ui.conversation.text)
        self.assertFalse(ui.pm_mode)
        self.observe_patcher.start()
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
        # hd665: the error is shown, and the draft is held UNKNOWN (paused), not lost.
        self.turns = [[_delta('partial'), 'EXIT']]
        ui, _ = self.drive([self.connect(), self.connected, 'go\r', self.turn_done])
        text = ui.conversation.text
        self.assertIn('PM runtime error PM_PROCESS_EXITED', text)
        self.assertIn('\nUNKNOWN · ', text)   # the W56a turn-end line of an UNKNOWN turn
        self.assertEqual(ui.pm_state, 'error(PM_PROCESS_EXITED)')
        self.assertEqual(list(ui.pm_outbox), ['go'])
        self.assertEqual(ui.pm_outbox_paused, 'UNKNOWN')

    def test_history_disconnect_and_chat_is_never_the_pm(self):
        def history_shown(ui):
            return self.until(ui, lambda ui: '上次對話（' in ui.conversation.text)

        ui, _ = self.drive([self.connect(), self.connected, 'first\r', self.turn_done,
                            '/pm history\r', history_shown,
                            '/pm disconnect\r', lambda ui: self.until(ui, lambda ui: not ui.pm_pending),
                            lambda ui: self.assertFalse(ui.pm_mode),
                            'chat now\r', self.idle])
        text = ui.conversation.text
        history = text[text.index('上次對話（'):]
        self.assertNotIn('(history)', text)
        self.assertRegex(history, '── [0-9]{2}:[0-9]{2}\n你\n  first\n\nPM\n'
                                  '  ▸ 用過的工具：Read\n')
        self.assertRegex(history, '\n\n[0-9]+\\.[0-9] 秒 · [0-9.k]+ tokens\n')
        self.assertIn('PM disconnected; chat mode', text)
        self.assertIn('assistant:\nreply', text)            # chat goes to the chat provider
        self.assertEqual([t['content'] for t in self.session.history], ['chat now', 'reply'])
        self.assertEqual(len(self.fakes[0].users()), 1)       # nothing more reached the PM
        self.assertTrue(self.fakes[0].closed_stdin)

    def test_bash_line_says_what_it_does_never_the_command(self):
        # W57 (Owner 2026-10-30): 「Bash ✓ … 也看不出是在跑什麼」.
        self.turns = [[_assistant({'type': 'tool_use', 'id': 'b1', 'name': 'Bash', 'input': {
                           'command': 'SECRET_COMMAND --token x', 'description': '列出 PR 狀態'}}),
                       _tool_result('b1', 'SECRET_OUTPUT', False),
                       _assistant({'type': 'tool_use', 'id': 'b2', 'name': 'Bash',
                                   'input': {'command': 'SECRET_COMMAND'}}),
                       _tool_result('b2', 'boom', True),
                       _result('done')]]
        ui, _ = self.drive([self.connect(), self.connected, 'go\r', self.turn_done])
        text = ui.conversation.text
        self.assertRegex(text, '  ▸ 使用工具 2 次（1 次失敗）：✗ Bash（[0-9]+s，失敗）')
        ui.toggle_tools()
        self.assertRegex(ui.conversation.text, '      ✓ 列出 PR 狀態（[0-9]+s）\n'
                                               '      ✗ Bash（[0-9]+s，失敗）')
        self.assertNotIn('SECRET_COMMAND', ui.conversation.text)
        self.assertNotIn('SECRET_OUTPUT', ui.conversation.text)

    def test_pm_tool_label_preserves_descriptions_with_paths(self):
        for description in ('scp host:/Users/x/k.pem .',
                            'fetch https://example.com/a/b',
                            'export PATH=/a:/b', 'cat ~/notes/x.md'):
            with self.subTest(description=description):
                self.assertEqual(chat_tui_pm.tool_label({
                    'name': 'Bash', 'description': description}), description)
        self.assertEqual(chat_tui_pm.tool_label({
            'name': 'Read', 'description': 'Read docs/a.md'}), 'Read docs/a.md')

    def test_status_in_pm_mode_shows_the_pm_in_chinese(self):
        ui, _ = self.drive([self.connect(), self.connected, '/status\r',
                            lambda ui: self.until(ui, lambda ui: '狀態（PM 模式）' in
                                                  ui.conversation.text)])
        status = ui.conversation.text.split('> /status', 1)[1]
        self.assertIn('PM session：' + self.session_id + '（claude-code，cwd .）', status)
        # Owner #683: TUI shows paths like Claude Code (repo-relative, ~/, else full path).
        # Full path, or ~/... when the temp dir sits under home (CI runners).
        self.assertRegex(status, r'Claude 帳號目錄：(/|~/)\S*\.claude\n')
        self.assertIn('權限模式（本機設定）：auto（自動，不跳出確認）', status)
        self.assertIn('PM 狀態：暖機中…', status)
        self.assertIn('佇列（本機設定）：沒有', status)
        self.assertRegex(status, '背景：(讀取中|UNKNOWN（TaskStore：.*）|[0-9]+ 件進行中)')
        self.assertIn('主題（本機設定）：', status)
        ordered = ['狀態（PM 模式）', 'PM session：', 'Claude 帳號目錄：',
                   '權限模式（本機設定）：', 'PM 狀態：', '背景：', '佇列（本機設定）：',
                   '主題（本機設定）：', '用量（本 session）：', 'Context 與額度：']
        positions = [status.index(line) for line in ordered]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(status.rstrip().splitlines()[-2:], [
            '  用量（本 session）：尚無回合',
            '  Context 與額度：UNAVAILABLE（llmtool 沒有這項資料）'])
        self.assertNotIn('供應商', status)
        self.assertNotIn('provider:', status)

    def bind_codex(self, account):
        binding = dict(self.binding, runtime='codex', account=account)
        self.api.current_binding = lambda session_id=None: binding

    def connect_and_status(self):
        """(connect text, /status text, status bar fields) of one connected PM."""
        fields = {}
        ui, _ = self.drive([self.connect(), self.connected,
                            lambda ui: fields.update(
                                ui.pm_status_fields(lambda value, _width: value)),
                            '/status\r',
                            lambda ui: self.until(ui, lambda ui: '狀態（PM 模式）' in
                                                  ui.conversation.text)])
        connect, status = ui.conversation.text.split('> /status', 1)
        return connect, status, fields

    def test_connect_line_keeps_the_claude_text(self):
        connect, _, fields = self.connect_and_status()
        self.assertIn('PM mode: Enter sends each draft in real time to the PM session '
                      + self.session_id + ' (claude-code, cwd ', connect)
        self.assertIn(') through pm_runtime; ', connect)
        self.assertIn('; claude-code account default. pm_runtime checked at connect time '
                      'that the session did not look open elsewhere. Esc interrupts a turn; '
                      '/pm disconnect ends the connection. Chat replies are never the PM.',
                      connect)
        self.assertEqual(fields['identity'], 'claude/default')

    def test_codex_binding_names_codex_on_the_connect_line_and_status(self):
        self.bind_codex('codex-test')
        connect, status, fields = self.connect_and_status()
        self.assertIn('the PM session ' + self.session_id + ' (codex, cwd ', connect)
        self.assertIn('; codex account codex-test. pm_runtime checked', connect)
        self.assertNotIn('(claude-code, cwd', connect)
        self.assertNotIn('claude-code account', connect)
        self.assertIn('PM session：' + self.session_id + '（codex，cwd .）', status)
        self.assertIn('  帳號：codex-test\n', status)
        self.assertNotIn('Claude', status)
        self.assertNotIn('claude-code', status)
        self.assertNotIn('權限模式', status)
        ordered = ['狀態（PM 模式）', 'PM session：', '帳號：', 'PM 狀態：', '背景：',
                   '佇列（本機設定）：', '主題（本機設定）：', '用量（本 session）：',
                   'Context 與額度：']
        positions = [status.index(line) for line in ordered]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(fields['identity'], 'codex/codex-test')

    def assert_codex_account_unknown(self, account):
        self.bind_codex(account)
        _, status, _ = self.connect_and_status()
        self.assertIn('  帳號：UNKNOWN\n', status)
        self.assertNotIn('Claude 帳號目錄', status)

    def test_status_shows_unknown_for_a_codex_binding_with_no_account(self):
        self.assert_codex_account_unknown(None)

    def test_status_shows_unknown_for_a_codex_binding_with_an_empty_account(self):
        self.assert_codex_account_unknown('')

    def test_status_without_a_binding_takes_the_runtime_from_the_runtime_object(self):
        def check(ui):
            real, ui.pm_runtime, ui.pm_binding = ui.pm_runtime, Mock(runtime='codex'), None
            try:
                status = ui.status_report()
                identity = ui.pm_status_fields(lambda value, _width: value)['identity']
            finally:
                ui.pm_runtime = real
            self.assertIn('PM session：' + self.session_id + '（codex，cwd .）', status)
            self.assertIn('  帳號：UNKNOWN\n', status)
            self.assertNotIn('權限模式', status)
            self.assertEqual(identity, 'codex/default')

        self.drive([self.connect(), self.connected, check])

    def test_status_pm_history_read_failure_shows_unknown_and_returns(self):
        ui, _ = self.drive([self.connect(), self.connected,
                            lambda ui: setattr(ui.pm_api, 'read_all_turns',
                                               Mock(side_effect=OSError('history unavailable'))),
                            '/status\r',
                            lambda ui: self.until(ui, lambda ui: '狀態（PM 模式）' in
                                                  ui.conversation.text)])
        status = ui.conversation.text.split('> /status', 1)[1]
        self.assertIn('用量（本 session）：UNKNOWN（PM 歷史無法讀取）', status)
        self.assertIn('Context 與額度：UNAVAILABLE（llmtool 沒有這項資料）', status)

    def test_logs_disconnected_and_picker_enter_on_exact_command_runs(self):
        expected = 'PM 紀錄：尚未連線 PM。先 /pm connect。'
        ui, _ = self.drive(['/logs\r'])
        self.assertEqual(ui.conversation.text.splitlines()[-1], expected)
        ui, _ = self.drive(['/logs', '\r'])
        self.assertEqual(ui.conversation.text.splitlines()[-1], expected)

    def test_logs_read_failure_and_empty_history_have_exact_text(self):
        ui, _ = self.drive([self.connect(), self.connected,
                            lambda ui: setattr(ui.pm_api, 'read_recent_turns',
                                               Mock(side_effect=OSError('history unavailable'))),
                            '/logs\r'])
        self.assertEqual(ui.conversation.text.splitlines()[-1],
                         'PM 紀錄：UNKNOWN（PM 歷史無法讀取）')
        ui, _ = self.drive([self.connect(), self.connected,
                            lambda ui: setattr(ui.pm_api, 'read_recent_turns',
                                               Mock(return_value=[])),
                            '/logs\r'])
        self.assertEqual(ui.conversation.text.splitlines()[-1], 'PM 紀錄：尚無回合')

    def test_logs_formatter_failure_shows_unknown(self):
        ui, _ = self.drive([self.connect(), self.connected,
                            lambda ui: setattr(ui.pm_api, 'read_recent_turns',
                                               Mock(return_value=[])),
                            lambda ui: patch.object(chat_tui_pm, 'log_lines',
                                                    side_effect=RuntimeError('plain failure')).start(),
                            '/logs\r'])
        self.assertEqual(ui.conversation.text.splitlines()[-1],
                         'PM 紀錄：UNKNOWN（PM 歷史無法讀取）')

    def test_logs_shows_oldest_first_and_requests_at_most_twenty(self):
        turns = [
            {'started_at': '2026-10-28T00:00:00+00:00', 'status': 'COMPLETED',
             'origin': 'foreground', 'tool_names': ['first-tool']},
            {'started_at': '2026-10-28T00:01:00+00:00', 'status': 'INTERRUPTED',
             'origin': 'background', 'tool_names': ['second-tool'], 'reason': 'USER_INTERRUPT'},
        ]
        read = Mock(return_value=turns)
        ui, _ = self.drive([self.connect(), self.connected,
                            lambda ui: setattr(ui.pm_api, 'read_recent_turns', read),
                            '/logs\r'])
        text = ui.conversation.text.split('> /logs', 1)[1]
        self.assertEqual(read.call_args.args[1], chat_tui_pm.LOGS_SHOWN)
        self.assertEqual(read.call_args.args[0], self.session_id)
        self.assertLess(text.index('first-tool'), text.index('second-tool'))
        self.assertIn('PM 紀錄（最近 2 回合）', text)
        self.assertIn('  不顯示工具指令與輸出', text)

    def test_status_background_read_failure_shows_unknown_source_and_reason(self):
        reason = 'TaskStore read denied'
        ui, _ = self.drive([self.connect(), self.connected,
                            lambda ui: (setattr(ui, 'background_key', self.session_id),
                                        setattr(ui, 'background_error', reason)),
                            '/status\r',
                            lambda ui: self.until(ui, lambda ui: '狀態（PM 模式）' in
                                                  ui.conversation.text)])
        status = ui.conversation.text.split('> /status', 1)[1]
        self.assertIn('背景：UNKNOWN（TaskStore：' + reason + '）', status)
        self.assertNotIn('背景：讀取失敗', status)

    def test_history_turns_use_the_live_layout(self):
        import json
        root = self.state
        root.mkdir(parents=True, exist_ok=True)
        turns = [_crafted_turn(owner_text='看 **進度**', result_text='## 結論\n- 一\n- 二',
                               ended_at='2026-10-28T00:00:03+00:00',
                               usage={'input_tokens': 1000, 'output_tokens': 200}),
                 _crafted_turn(turn_id='b' * 32, seq=2, origin='background', result_text='背景完成',
                               tool_names=[], status='INTERRUPTED')]
        (root / ('pm-' + self.session_id + '.history.json')).write_text(json.dumps(
            {'pm_session_id': self.session_id, 'turns': turns}))
        ui, _ = self.drive([self.connect(), self.connected])
        text = ui.conversation.text
        history = text[text.index('上次對話（'):]
        self.assertRegex(history, r'^上次對話（[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}）')
        self.assertNotIn('(history)', history)
        self.assertRegex(history, '\n\n── (10-2[78] )?[0-9]{2}:[0-9]{2}\n你\n  看 \\*\\*進度\\*\\*\n\nPM\n'
                                  '  ▸ 用過的工具：Read\n\n  ▍ 結論\n  • 一\n  • 二\n\n'
                                  '3.0 秒 · 1.2k tokens\n\n── ')
        self.assertIn('PM（背景通知）\n  背景完成\n\n已中斷', history)
        view = ui.view
        rule = text.index('── ', text.index('上次對話'))
        self.assertEqual(view.token_at(rule), 'rule')
        self.assertEqual(view.token_at(text.index('\n你\n', rule) + 1), 'role.owner')
        self.assertEqual(view.token_at(text.index('3.0 秒')), 'meta.usage')
        self.assertEqual(view.token_at(text.index('▸ 用過的工具')), 'tool.summary')

    def test_background_turn_between_owner_sends_is_shown_live_as_its_own_turn(self):
        # P0-3 (Owner TUI run 2026-10-29): task-notification turns were not shown until the
        # next Owner send, which then ended on the stale background result.
        self.turns = [[_delta('已派工'), _result('已派工', usage={'output_tokens': 1})],
                      [_delta('進度：'), 'HOLD', _delta('PR 已開'),
                       _assistant({'type': 'text', 'text': '進度：PR 已開'}),
                       _result('進度：PR 已開', usage={'output_tokens': 3})]]
        background = [_delta('Codex 回報'), 'HOLD',
                      _assistant({'type': 'text', 'text': 'Codex 回報'},
                                 {'type': 'tool_use', 'id': 'b1', 'name': 'Bash', 'input': {}}),
                      _tool_result('b1', 'SECRET_OUTPUT', False),
                      _result('Codex 回報', usage={'output_tokens': 2})]

        async def live(ui):
            self.fakes[0].unsolicited(background, delay=0.2)
            await self.until(ui, lambda ui: BG_WATCH + '\n  Codex 回報' in ui.conversation.text,
                             'background text shown live')
            self.assertTrue(ui.pm_turn_active)
            self.assertIn(BG_WATCH, ui.conversation.text)
            self.assertEqual(len(self.fakes[0].users()), 1)   # nothing was sent for it
            self.fakes[0].gate.set()
            await self.turn_done(ui)
            text = ui.conversation.text
            self.assertRegex(text, '  ▸ ✓ Bash（[0-9]+s）')
            self.assertIn('秒 · 2 tokens', text)
            self.assertNotIn('SECRET', text)

        async def owner_reply(ui):
            await self.until(ui, lambda ui: 'PM\n  進度：' in ui.conversation.text, 'reply')
            self.fakes[0].gate.set()
            await self.turn_done(ui)

        ui, _ = self.drive([self.connect(), self.connected, '派工\r', self.turn_done, live,
                            '回報進度\r', owner_reply])
        text = ui.conversation.text
        self.assertEqual(len(re.findall(r'\n[0-9]+\.[0-9] 秒 · ', text)), 3)
        owner = text.index('你\n  回報進度')
        self.assertIn('進度：PR 已開', text[owner:])
        self.assertIn('秒 · 3 tokens', text[owner:])
        self.assertNotIn('Codex 回報', text[owner:])   # the background reply stays its own turn
        turns = self.api.read_history(self.session_id)['turns']
        shown, total = chat_tui_pm.history_turns({'turns': turns})
        self.assertEqual(total, 3)
        self.assertTrue(shown[1]['background'])
        self.assertEqual(shown[1]['reply'], 'Codex 回報')
        self.assertFalse(shown[2]['background'])
        self.assertEqual(shown[2]['owner'], '回報進度')
        self.assertEqual([(t['owner_text'], t.get('origin'), t['usage']) for t in turns],
                         [('派工', None, {'output_tokens': 1}),
                          ('', 'background', {'output_tokens': 2}),
                          ('回報進度', None, {'output_tokens': 3})])

    def test_owner_message_during_a_background_turn_waits_in_the_queue_and_is_answered(self):
        # P0-3: the Owner message sent mid background turn was absorbed by Claude Code
        # (queue-operation remove, absorbed_mid_turn) and never got a turn of its own.
        self.turns = [[_delta('收到'), _result('收到')]]

        async def background(ui):
            self.fakes[0].unsolicited([_delta('背景'), 'HOLD', _result('背景')])
            await self.until(ui, lambda ui: BG_WATCH + '\n  背景' in ui.conversation.text)

        async def queued(ui):
            await asyncio.sleep(0.2)
            self.assertEqual(list(ui.pm_outbox), ['繼續推進，回報進度'])
            self.assertIn('你（已排入佇列（第 1 則））\n  繼續推進，回報進度',
                          ui.conversation.text)
            self.assertEqual(self.fakes[0].users(), [])   # held while the background turn runs
            self.fakes[0].gate.set()

        ui, _ = self.drive([self.connect(), self.connected, background, '繼續推進，回報進度\r', queued,
                            lambda ui: self.until(ui, lambda ui: 'PM\n  收到' in ui.conversation.text
                                                  and not ui.pm_turn_active, 'answered')])
        self.assertEqual([u['message']['content'][0]['text'] for u in self.fakes[0].users()],
                         ['繼續推進，回報進度'])
        self.assertIn('你（自佇列送出，剩 0 則）\n  繼續推進，回報進度', ui.conversation.text)
        self.assertEqual(len(re.findall(r'\n[0-9]+\.[0-9] 秒 · ', ui.conversation.text)), 2)

    def test_send_refused_by_a_background_turn_is_held_then_sent_when_it_ends(self):
        # The race: pm_runtime claims a background turn just before the Owner's Enter is
        # shown as one. The refused draft goes back to the queue, never to Claude Code.
        self.turns = [[_delta('收到'), _result('收到')]]
        claimed = {}

        async def race(ui):
            self.fakes[0].unsolicited([_delta('背景'), _result('背景')])
            # Claim it the way the watcher does, before the TUI shows it.
            claimed['events'] = ui.pm_runtime.background_turn(timeout=5)
            self.assertIsNotNone(claimed['events'])

        async def held(ui):
            await self.until(ui, lambda ui: not ui.pm_turn_active, 'held turn ended')
            self.assertEqual(list(ui.pm_outbox), ['回報進度'])
            self.assertIn('這則訊息沒有送出，保留在佇列第 1 則', ui.conversation.text)
            self.assertIsNone(ui.pm_outbox_paused)
            self.assertEqual(self.fakes[0].users(), [])
            turn = {'runtime': ui.pm_runtime, 'seq': None, 'events': [], 'ended': False}
            ui.pm_background_start(turn)
            for event in claimed['events']:
                ui.pm_background_event(turn, event)
            ui.pm_background_end(turn)

        with patch.object(chat_tui.ChatTUI, '_pm_watch', lambda self, runtime, loop: None):
            ui, _ = self.drive([self.connect(), self.connected, race, '回報進度\r', held,
                                lambda ui: self.until(ui, lambda ui: 'PM\n  收到' in ui.conversation.text
                                                      and not ui.pm_turn_active, 'answered')])
        self.assertEqual([u['message']['content'][0]['text'] for u in self.fakes[0].users()],
                         ['回報進度'])
        self.assertIn(BG_WATCH + '\n  背景', ui.conversation.text)

    def test_turn_started_before_claude_reads_the_message_is_shown_and_never_absorbs_it(self):
        # Codex v1 P1: the background turn began in Claude Code before the Owner line was
        # read and before the watcher saw any output. The message is sent with priority
        # "later", the TUI shows that turn as background inside the Owner turn, and the
        # Owner turn begins only at the message's own `started` frame.
        self.turns = [[_delta('進度：PR 已開'), _result('進度：PR 已開', usage={'output_tokens': 3})]]

        async def race(ui):
            self.fakes[0].unsolicited([_delta('Codex 回報'), 'HOLD',
                                       _result('Codex 回報', usage={'output_tokens': 2})])
            await asyncio.sleep(0.2)   # its output is in flight; nobody has claimed it

        async def shown(ui):
            await self.until(ui, lambda ui: BG_SUB + '\n  Codex 回報' in ui.conversation.text,
                             'background turn shown inside the Owner turn')
            sent = self.fakes[0].users()
            self.assertEqual([(u['priority'], len(u['uuid'])) for u in sent], [('later', 36)])
            self.assertNotIn('進度：', ui.conversation.text)   # not started yet
            self.fakes[0].gate.set()

        with patch.object(chat_tui.ChatTUI, '_pm_watch', lambda self, runtime, loop: None):
            ui, _ = self.drive([self.connect(), self.connected, race, '回報進度\r', shown,
                                self.turn_done])
        text = ui.conversation.text
        background = text.index(BG_SUB)
        accepted = text.index('Claude Code 開始處理你的訊息')
        self.assertLess(text.index('你\n  回報進度'), background)
        self.assertLess(background, text.index('秒 · 2 tokens'))
        self.assertLess(text.index('秒 · 2 tokens'), accepted)
        self.assertLess(accepted, text.index('PM\n  進度：PR 已開'))
        self.assertLess(text.index('PM\n  進度：PR 已開'), text.index('秒 · 3 tokens'))
        self.assertEqual(len(self.fakes[0].users()), 1)
        self.assertEqual(ui.pm_turn_status, 'COMPLETED')
        turns = self.api.read_history(self.session_id)['turns']
        self.assertEqual(sorted((t['owner_text'], t.get('origin'), t['usage']) for t in turns),
                         [('', 'background', {'output_tokens': 2}),
                          ('回報進度', None, {'output_tokens': 3})])

    def test_held_draft_is_sent_when_the_background_turn_ended_before_the_hold(self):
        # Codex v1 P2: send() was refused (PM_TURN_ACTIVE), then the short background turn
        # ended before _pm_hold ran and no background turn is left to drain the queue.
        self.turns = [[_delta('收到'), _result('收到')]]
        claimed = {}
        original = chat_tui.ChatTUI._pm_hold

        def late_hold(ui, text, seq, status):
            list(claimed.pop('events', ()))   # that background turn ends first
            return original(ui, text, seq, status)

        async def claim(ui):
            self.fakes[0].unsolicited([_delta('短'), _result('短')])
            claimed['events'] = ui.pm_runtime.background_turn(timeout=5)
            self.assertIsNotNone(claimed['events'])

        with patch.object(chat_tui.ChatTUI, '_pm_watch', lambda self, runtime, loop: None), \
                patch.object(chat_tui.ChatTUI, '_pm_hold', late_hold):
            ui, _ = self.drive([self.connect(), self.connected, claim, '回報進度\r',
                                lambda ui: self.until(ui, lambda ui: 'PM\n  收到' in ui.conversation.text
                                                      and not ui.pm_turn_active, 'drained')])
        self.assertIn('保留在佇列第 1 則', ui.conversation.text)
        self.assertEqual([u['message']['content'][0]['text'] for u in self.fakes[0].users()],
                         ['回報進度'])
        self.assertEqual(list(ui.pm_outbox), [])

    def test_unconfirmed_message_ends_unknown_and_is_resent_only_by_the_owner(self):
        # Codex v2 P1: `queued` but no `started` within the bound: UNKNOWN, never re-sent
        # automatically; the draft waits, paused, for /pm queue send.
        self.turns = [[], [_delta('收到'), _result('收到')]]

        def withhold(ui):
            self.fakes[0].withhold = {'started', 'completed'}

        async def unknown(ui):
            await self.until(ui, lambda ui: 'UNKNOWN' in ui.conversation.text
                             and not ui.pm_turn_active, 'UNKNOWN shown')
            self.assertIn('無法確認 PM 是否收到這則訊息（UNKNOWN），未自動重送；可用 /pm queue send 重送',
                          ui.conversation.text)
            self.assertEqual(list(ui.pm_outbox), ['回報進度'])
            self.assertEqual(ui.pm_outbox_paused, 'UNKNOWN')
            await asyncio.sleep(0.3)
            self.assertEqual(len(self.fakes[0].users()), 1)   # nothing re-sent by itself
            self.fakes[0].withhold = set()

        with patch.object(self.pm_runtime, 'PM_STARTED_GRACE', 0.3):
            ui, _ = self.drive([self.connect(), self.connected, withhold, '回報進度\r', unknown,
                                '/pm queue send\r',
                                lambda ui: self.until(ui, lambda ui: 'PM\n  收到' in ui.conversation.text
                                                      and not ui.pm_turn_active, 'resent')])
        self.assertEqual([u['message']['content'][0]['text'] for u in self.fakes[0].users()],
                         ['回報進度', '回報進度'])
        self.assertEqual(list(ui.pm_outbox), [])

    # -- hd665 (High Debug): every unconfirmed or refused attempt keeps its draft -----------
    def _held(self, ui, paused):
        return self.until(ui, lambda ui: not ui.pm_turn_active and list(ui.pm_outbox) == ['回報進度']
                          and ui.pm_outbox_paused == paused, 'draft held, paused ' + paused)

    def test_invalid_lifecycle_holds_draft_unknown_without_resend(self):
        self.turns = [[_delta('x'), _result('x')]]

        def rewrite(ui):
            self.fakes[0].rewrite = {'started': 'future'}   # not a lifecycle state

        async def held(ui):
            await self._held(ui, 'UNKNOWN')
            await asyncio.sleep(0.3)
            self.assertEqual(len(self.fakes[0].users()), 1)

        ui, _ = self.drive([self.connect(), self.connected, rewrite, '回報進度\r', held])
        text = ui.conversation.text
        self.assertIn('PM runtime error PM_STREAM_INVALID', text)
        self.assertIn('無法確認 PM 是否收到這則訊息（UNKNOWN），未自動重送', text)
        owner = [t for t in self.api.read_history(self.session_id)['turns'] if t['owner_text']]
        self.assertEqual([(t['status'], t['delivery']) for t in owner], [('UNKNOWN', 'UNKNOWN')])

    def test_lifecycle_refusal_retains_reason_and_pauses_queue(self):
        self.turns = [[]]

        def refuse(ui):
            self.fakes[0].rewrite = {'started': 'refused'}
            self.fakes[0].withhold = {'completed'}

        async def held(ui):
            await self._held(ui, 'Claude Code refused')
            await asyncio.sleep(0.3)
            self.assertEqual(len(self.fakes[0].users()), 1)   # never re-sent by itself

        ui, _ = self.drive([self.connect(), self.connected, refuse, '回報進度\r', held])
        self.assertIn('Claude Code 沒有處理這則訊息（refused），保留在佇列第 1 則並暫停',
                      ui.conversation.text)
        self.assertIn('\n未送出 · ', ui.conversation.text)

    def test_child_exit_and_write_failure_retain_current_draft(self):
        for case in ('exit', 'write'):
            with self.subTest(case=case):
                self.turns = [['EXIT']] if case == 'exit' else [[]]

                def arm(ui, case=case):
                    self.fakes[-1].fail_writes = case == 'write'

                ui, _ = self.drive([self.connect(), self.connected, arm, '回報進度\r',
                                    lambda ui: self._held(ui, 'UNKNOWN')])
                self.assertIn('PM runtime error PM_PROCESS_EXITED', ui.conversation.text)
                self.assertIn('（UNKNOWN），未自動重送', ui.conversation.text)
                self.assertEqual(len(self.fakes[-1].users()), 1)

    def test_esc_completion_race_never_drains_queue(self):
        # Esc with the real pm_runtime.interrupt: the turn still completes (the race), and
        # the queue stays paused.
        self.turns = [[_delta('一'), 'HOLD', _result('done')], [_result('next')]]

        async def race(ui):
            await self.until(ui, lambda ui: len(ui.pm_outbox) == 1
                             and 'PM\n  一' in ui.conversation.text, 'queued mid-turn')
            ui.interrupt_pm()   # the fake releases its turn on the control request

        ui, _ = self.drive([self.connect(), self.connected, 'go\r', 'next one\r', race,
                            lambda ui: self.until(ui, lambda ui: not ui.pm_turn_active, 'end'),
                            lambda ui: asyncio.sleep(0.3)])
        self.assertIn('\n已中斷 · ', ui.conversation.text)
        self.assertEqual([u['message']['content'][0]['text'] for u in self.fakes[0].users()],
                         ['go'])
        self.assertEqual(list(ui.pm_outbox), ['next one'])
        self.assertEqual(ui.pm_outbox_paused, 'Esc 中斷')

    def test_late_hold_is_applied_once_to_original_attempt(self):
        self.turns = [[], [_delta('收到'), _result('收到')]]
        seen = {}

        def withhold(ui):
            self.fakes[0].withhold = {'started', 'completed'}

        async def held(ui):
            await self._held(ui, 'UNKNOWN')
            turns = self.api.read_history(self.session_id)['turns']
            seen['turn_id'] = [t for t in turns if t['status'] == 'UNKNOWN'][0]['turn_id']
            seen['seq'] = ui._pm_turn_seq
            ui._pm_hold('回報進度', seen['seq'], 'UNKNOWN', None, seen['turn_id'])   # late, again
            self.assertEqual(list(ui.pm_outbox), ['回報進度'])
            self.fakes[0].withhold = set()

        def late(ui):
            self.assertEqual(ui.pm_turn_status, 'COMPLETED')   # the resent message's turn
            ui._pm_hold('回報進度', seen['seq'], 'UNKNOWN', None, seen['turn_id'])
            self.assertEqual(list(ui.pm_outbox), [])            # applied once only
            ui._pm_hold('其他', seen['seq'], 'HELD')               # stale: draft kept, status not
            self.assertEqual(ui.pm_turn_status, 'COMPLETED')
            self.assertEqual(list(ui.pm_outbox), ['其他'])

        with patch.object(self.pm_runtime, 'PM_STARTED_GRACE', 0.3):
            ui, _ = self.drive([self.connect(), self.connected, withhold, '回報進度\r', held,
                                '/pm queue send\r',
                                lambda ui: self.until(ui, lambda ui: 'PM\n  收到' in ui.conversation.text
                                                      and not ui.pm_turn_active, 'resent'),
                                late])
        self.assertEqual(len(self.fakes[0].users()), 2)

    def test_unknown_stays_paused_across_late_background_events(self):
        self.turns = [[]]

        def withhold(ui):
            self.fakes[0].withhold = {'started', 'completed'}

        async def background(ui):
            await self._held(ui, 'UNKNOWN')
            self.fakes[0].unsolicited([_delta('背景'), _result('背景')])
            await self.until(ui, lambda ui: BG_WATCH + '\n  背景' in ui.conversation.text
                             and not ui.pm_turn_active, 'background turn ended')
            await asyncio.sleep(0.3)

        with patch.object(self.pm_runtime, 'PM_STARTED_GRACE', 0.3):
            ui, _ = self.drive([self.connect(), self.connected, withhold, '回報進度\r', background])
        self.assertEqual(len(self.fakes[0].users()), 1)
        self.assertEqual(list(ui.pm_outbox), ['回報進度'])
        self.assertEqual(ui.pm_outbox_paused, 'UNKNOWN')

    def test_background_turns_feed_the_status_count_and_the_panel(self):
        # W56d hook: pm_background_turns() reports the running background turn (on its own
        # or inside an Owner turn), so 背景 N counts it and the ↓ panel lists it.
        self.turns = [[_delta('收到'), _result('收到')]]

        def panel(ui):
            return ''.join(text for _style, text in ui.background_text())

        async def watched(ui):
            ui.finish_background(self.session_id, ([], None))   # TaskStore: no Tasks
            self.assertIn('背景 0', ui.status_text())
            self.fakes[0].unsolicited([_delta('背景'), 'HOLD', _result('背景')])
            await self.until(ui, lambda ui: BG_WATCH + '\n  背景' in ui.conversation.text)
            self.assertIn('背景 1 · ↓ 查看', ui.status_text())
            self.assertEqual([(i['role'], i['state']) for i in ui.background_all()],
                             [('PM', '背景回合')])
            ui.show_background = True
            self.assertTrue(ui.background_visible())
            self.assertRegex(panel(ui), r'背景工作 1 項[^\n]*\n  PM +背景通知 +背景回合 +[0-9]+s')
            self.fakes[0].gate.set()
            await self.until(ui, lambda ui: not ui.pm_turn_active, 'background turn ended')
            self.assertIn('背景 0', ui.status_text())
            self.assertEqual(ui.pm_background_turns(), [])

        async def inside(ui):
            await self.until(ui, lambda ui: BG_SUB + '\n  Codex' in ui.conversation.text,
                             'background turn inside the Owner turn')
            self.assertEqual([i['state'] for i in ui.pm_background_turns()], ['背景回合'])
            self.assertIn('背景 1', ui.status_text())
            self.fakes[-1].gate.set()
            await self.until(ui, lambda ui: 'PM\n  收到' in ui.conversation.text
                             and not ui.pm_turn_active, 'answered')
            self.assertEqual(ui.pm_background_turns(), [])

        def race(ui):
            ui.finish_background(self.session_id, ([], None))   # TaskStore: no Tasks
            self.fakes[-1].unsolicited([_delta('Codex 回報'), 'HOLD', _result('Codex 回報')])

        with patch.object(chat_tui_status, 'read_background', return_value=[]):
            ui, _ = self.drive([self.connect(), self.connected, watched])
        with patch.object(chat_tui_status, 'read_background', return_value=[]), \
                patch.object(chat_tui.ChatTUI, '_pm_watch', lambda self, runtime, loop: None):
            ui, _ = self.drive([self.connect(), self.connected, race,
                                lambda ui: asyncio.sleep(0.2), '回報進度\r', inside])

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

    # -- W59: read-only Task detail from the ↓ background panel ---------------------------
    W59_TASK_A = 'a' * 32
    W59_TASK_B = 'b' * 32

    def w59_items(self):
        return [{'role': 'Developer', 'task': self.W59_TASK_A, 'pr': '#654', 'state': 'RUNNING',
                 'updated_at': '2026-11-01T00:00:00+00:00'},
                {'role': 'Reviewer', 'task': self.W59_TASK_B, 'pr': '#661',
                 'state': 'WAITING_REVIEW', 'updated_at': '2026-11-01T00:01:00+00:00'}]

    def w59_record(self, task_id, pr=661):
        return {'task_id': task_id, 'request': 'W59 詳情測試\n第二行', 'state': 'PM_RESPONDED',
                'created_at': '2026-11-01T00:00:00+00:00',
                'updated_at': '2026-11-01T00:01:00+00:00',
                'pm_session_id': 'pm-test', 'developer': None,
                'review_dispatch_claim': {'pr': pr, 'state': 'CLAIMED', 'version': 1}}

    def w59_listed(self, ui):
        ui.finish_background(self.session_id, (self.w59_items(), None))

    def w59_text(self, ui):
        return ui.task_view.text

    def test_w59_enter_on_the_background_panel_opens_a_read_only_detail_off_the_ui(self):
        reads = []

        def read(task_id):
            reads.append((task_id, threading.current_thread().name))
            return self.w59_record(task_id)

        async def opened(ui):
            await self.until(ui, lambda ui: self.W59_TASK_B in self.w59_text(ui), 'detail read')
            self.assertTrue(ui.show_task_view)
            self.assertFalse(ui.busy)                      # a reader thread, never busy
            self.assertFalse(ui.conversation_visible())
            text = self.w59_text(ui)
            self.assertIn('Task 詳情（唯讀）', text)
            self.assertRegex(text, '來源：TaskStore（本機）｜讀取於 [0-9]{2}:[0-9]{2}:[0-9]{2}')
            self.assertIn('Core 狀態：RUNNING（task_status.derive_state）', text)
            self.assertIn('W59 詳情測試', text)
            self.assertIn(OWNER_PR_URL + '661', text)
            self.assertNotIn('"task_id"', text)              # no raw JSON outside dev mode
            self.assertEqual(reads, [(self.W59_TASK_B, 'llmtool-chat-task-detail')])
            self.assertIn('Esc 返回', ui.status_text())

        with review_trust_fixture.use_trust():
            with patch.object(chat_tui_status, 'read_task_detail', side_effect=read):
                ui, _ = self.drive([self.connect(), self.connected, self.w59_listed,
                                    '\x1b[B', lambda ui: self.assertTrue(ui.background_visible()),
                                    '\x1b[B', lambda ui: self.assertEqual(ui.background_index, 1),
                                    '\r', opened])
        self.assertEqual(self.fakes[0].users(), [])          # Enter sent nothing to the PM

    def test_w59_detail_opens_during_a_pm_turn_and_the_stream_keeps_arriving(self):
        self.turns = [_full_turn(hold=True)]

        async def streaming(ui):
            await self.until(ui, lambda ui: 'PM\n  Hello' in ui.conversation.text, 'first delta')
            self.assertTrue(ui.pm_turn_active)

        async def opened(ui):
            await self.until(ui, lambda ui: self.W59_TASK_A in self.w59_text(ui), 'detail read')
            self.assertTrue(ui.pm_turn_active)
            self.fakes[0].gate.set()                          # the PM turn goes on
            await self.until(ui, lambda ui: 'Hello 老闆' in ui.conversation.text
                             and not ui.pm_turn_active, 'turn finished while the detail is open')
            self.assertTrue(ui.show_task_view)                # PM output never closed it

        with patch.object(chat_tui_status, 'read_task_detail',
                          side_effect=lambda task_id: self.w59_record(task_id)):
            ui, _ = self.drive([self.connect(), self.connected, self.w59_listed,
                                '請問進度？\r', streaming, '\x1b[B', '\r', opened])
        self.assertNotIn('已中斷', ui.conversation.text)

    def test_w59_esc_during_a_turn_closes_the_detail_without_interrupting(self):
        self.turns = [_full_turn(hold=True)]

        async def opened(ui):
            await self.until(ui, lambda ui: self.W59_TASK_A in self.w59_text(ui), 'detail read')

        async def closed(ui):
            await self.until(ui, lambda ui: not ui.show_task_view, 'Esc closed the detail')
            self.assertTrue(ui.pm_turn_active)                # not interrupted
            self.assertFalse(ui.pm_interrupted)
            self.fakes[0].gate.set()

        with patch.object(chat_tui_status, 'read_task_detail',
                          side_effect=lambda task_id: self.w59_record(task_id)):
            ui, _ = self.drive([self.connect(), self.connected, self.w59_listed,
                                '請問進度？\r',
                                lambda ui: self.until(ui, lambda ui: 'PM\n  Hello'
                                                      in ui.conversation.text),
                                '\x1b[B', '\r', opened, '\x1b', lambda ui: asyncio.sleep(0.6),
                                closed, self.turn_done])
        self.assertNotIn('已中斷', ui.conversation.text)

    def test_w59_esc_returns_to_the_reading_position_and_keeps_the_draft(self):
        long_text = '\n'.join('第%03d行' % n for n in range(80))
        before = {}

        def scrolled(ui):
            ui.append(long_text)
            ui.app.invalidate()

        async def back(ui):
            await asyncio.sleep(0.1)
            ui.view.scroll_rows(-10)
            await asyncio.sleep(0.1)
            before['top'], before['offset'] = ui.view.top, ui.view.top_offset
            self.assertFalse(ui.view.following)

        async def opened(ui):
            await self.until(ui, lambda ui: self.W59_TASK_A in self.w59_text(ui), 'detail read')
            ui.append('llmtool: 詳情開著時的新輸出')         # output arriving while it is open
            self.assertTrue(ui.show_task_view)

        def kept(ui):
            self.assertEqual(ui.input.text, '草稿 不見不行')
            self.assertTrue(ui.show_task_view)

        async def closed(ui):
            await self.until(ui, lambda ui: not ui.show_task_view, 'Esc closed the detail')
            await asyncio.sleep(0.1)
            self.assertEqual(ui.input.text, '草稿 不見不行')    # the draft is kept, not sent
            self.assertEqual(ui.view.top_offset, before['offset'])
            self.assertEqual(ui.view.top, before['top'])
            self.assertFalse(ui.view.following)
            self.assertTrue(ui.app.layout.has_focus(ui.input))

        with patch.object(chat_tui_status, 'read_task_detail',
                          side_effect=lambda task_id: self.w59_record(task_id)):
            ui, _ = self.drive([self.connect(), self.connected, self.w59_listed, scrolled, back,
                                '\x1b[B', '\r', opened, '\t', '草稿 不見不行',
                                '\x1b[A', '\x1b[B', '\x1b[6~', '\r', kept,
                                '\x1b', lambda ui: asyncio.sleep(0.6), closed])
        self.assertEqual(self.fakes[0].users(), [])          # Enter in the detail sent nothing
        self.assertIn('詳情開著時的新輸出', ui.conversation.text)

    def test_w59_missing_data_shows_unknown_with_a_reason(self):
        def failing(task_id):
            raise OSError('disk gone')

        async def failed(ui):
            await self.until(ui, lambda ui: 'UNKNOWN（' in self.w59_text(ui), 'read failure shown')
            self.assertIn('Core 狀態：UNKNOWN（TaskStore 讀取失敗', self.w59_text(ui))
            self.assertNotIn('disk gone', self.w59_text(ui))   # details omitted

        def turn_row(ui):
            ui.finish_background(self.session_id, ([], None))
            ui._pm_background_track(('turn', 99))
            ui.app.invalidate()

        async def turn_detail(ui):
            await self.until(ui, lambda ui: ui.show_task_view, 'turn row detail')
            self.assertIn('UNKNOWN（這一列是 PM 自行開始的背景回合，沒有 Task 記錄）',
                          self.w59_text(ui))

        with patch.object(chat_tui_status, 'read_task_detail', side_effect=failing):
            self.drive([self.connect(), self.connected, self.w59_listed, '\x1b[B', '\r', failed])
        with patch.object(chat_tui_status, 'read_task_detail', side_effect=failing) as read:
            self.drive([self.connect(), self.connected, turn_row, '\x1b[B', '\r', turn_detail])
            read.assert_not_called()

    def test_w59_pr_url_in_the_detail_is_an_osc8_link(self):
        with review_trust_fixture.use_trust():
            with create_pipe_input() as pipe:
                ui = chat_tui.ChatTUI(self.session, input=pipe, output=DummyOutput(), pm_api=self.api)
            ui.links_supported = True
            ui._linker = chat_tui_theme.Linker(str(self.temp_root), 'o/r')
            text = 'PR: #661  ' + OWNER_PR_URL + '661\nPR: #7'
            links = ui.task_view_linker().links(text, 0, len(text))
            urls = [url for _low, _high, url in links]
            self.assertEqual(urls, [OWNER_PR_URL + '661'])   # never the cwd repo's #N
            low, high, _url = links[0]
            self.assertEqual(text[low:high], OWNER_PR_URL + '661')
            wrapped = ui.task_view_linker().links(text, low + 5, low + 9)   # a row inside the URL
            self.assertEqual(wrapped, links)
            self.assertTrue(ui.task_view_linker().open_sequence(urls[0]).startswith('\x1b]8;id='))
            ui._linker = None                                       # repo lookup not finished
            self.assertEqual([url for _l, _h, url in ui.task_view_linker().links(text, 0, len(text))],
                             [OWNER_PR_URL + '661'])
            ui.settings['links'] = 'off'
            self.assertIsNone(ui.task_view_linker())

    def test_w59_pr_without_trust_shows_not_available_and_has_no_osc8_link(self):
        from llmtool.trust import TrustError

        async def opened(ui):
            await self.until(ui, lambda ui: self.W59_TASK_B in self.w59_text(ui), 'detail read')
            text = self.w59_text(ui)
            self.assertIn('PR: #661 (from review claim)  NOT_AVAILABLE', text)
            linker = ui.task_view_linker()
            self.assertTrue(linker is None or linker.links(text, 0, len(text)) == [])

        with patch.object(chat_tui_status.trust, 'load_trust',
                          side_effect=TrustError('TRUST_MISSING')):
            with patch.object(chat_tui_status, 'read_task_detail', side_effect=self.w59_record):
                self.drive([self.connect(), self.connected, self.w59_listed,
                            '\x1b[B', '\x1b[B', '\r', opened])


    def test_w59_a_late_read_is_dropped_and_esc_after_closing_interrupts(self):
        self.turns = [_full_turn(hold=True)]
        gate, results = threading.Event(), []

        def slow(task_id):
            gate.wait(5)
            return self.w59_record(task_id)

        async def closed_before_the_read(ui):
            await self.until(ui, lambda ui: not ui.show_task_view, 'Esc closed it')
            self.assertTrue(ui.pm_turn_active)
            original = ui.finish_task_view
            ui.finish_task_view = lambda *args: (results.append(args), original(*args))
            gate.set()
            await self.until(ui, lambda ui: results, 'late read result')
            self.assertFalse(ui.show_task_view)              # the stale result reopened nothing
            self.assertNotIn('W59 詳情測試', ui.task_view.text)   # the late record never drawn

        async def interrupted(ui):
            await self.until(ui, lambda ui: not ui.pm_turn_active, 'second Esc interrupted')
            self.assertTrue(ui.pm_interrupted or ui.pm_last_status == 'INTERRUPTED')

        with patch.object(chat_tui_status, 'read_task_detail', side_effect=slow):
            self.drive([self.connect(), self.connected, self.w59_listed, '請問進度？\r',
                        lambda ui: self.until(ui, lambda ui: 'PM\n  Hello' in ui.conversation.text),
                        '\x1b[B', '\r', lambda ui: self.assertIn('讀取中', ui.task_view.text),
                        '\x1b', closed_before_the_read, '\x1b', interrupted])

    def test_w59_enter_opens_the_highlighted_row_after_a_refresh_shifted_it(self):
        async def opened(ui):
            await self.until(ui, lambda ui: self.W59_TASK_B in self.w59_text(ui), 'detail read')
            self.assertNotIn(self.W59_TASK_A, self.w59_text(ui))

        def shifted(ui):
            ui.background_text()                              # row 1 (Task B) drawn highlighted
            ui.finish_background(self.session_id, (            # B moves to row 0, A to row 1
                self.w59_items()[1:] + self.w59_items()[:1], None))

        with patch.object(chat_tui_status, 'read_task_detail',
                          side_effect=lambda task_id: self.w59_record(task_id)):
            ui, _ = self.drive([self.connect(), self.connected, self.w59_listed, '\x1b[B',
                                '\x1b[B', shifted, self.w59_redraw, '\r', opened])

    W59_TASK_C = 'c' * 32

    def w59_redraw(self, ui):
        """Force panel draws (the real app's and a direct one) after a refresh."""
        ui.app.invalidate()
        ui.background_text()
        return asyncio.sleep(0.1)

    @staticmethod
    def w59_highlighted(ui):
        return [text for style, text in ui.background_text() if style == 'class:menu.current']

    def w59_gone_case(self, refreshes):
        reads = []

        def read(task_id):
            reads.append(task_id)
            return self.w59_record(task_id)

        steps = [self.connect(), self.connected, self.w59_listed, '\x1b[B', '\x1b[B',
                 lambda ui: self.assertEqual(ui.background_selected, ('Reviewer', self.W59_TASK_B))]
        for items in refreshes:
            steps += [lambda ui, items=items: ui.finish_background(self.session_id, (items, None)),
                      self.w59_redraw, self.w59_redraw]

        def before(ui):
            self.assertEqual(ui.background_selected, ('Reviewer', self.W59_TASK_B))   # kept
            self.assertEqual(self.w59_highlighted(ui), [])        # nothing else highlighted
            self.assertIn('已不在清單', ''.join(t for _s, t in ui.background_text()))

        async def shown(ui):
            await self.until(ui, lambda ui: ui.show_task_view, 'detail view')
            await asyncio.sleep(0.1)
            self.assertIn('Core 狀態：UNKNOWN（選中的 Task ' + self.W59_TASK_B[:8]
                          + ' 已不在背景清單', ui.task_view.text)
            self.assertEqual(reads, [])                           # no other Task was opened
            self.assertEqual(ui.input.text, '')
        steps += [before, '\r', shown]
        with patch.object(chat_tui_status, 'read_task_detail', side_effect=read):
            self.drive(steps)

    def test_w59_selected_task_removed_then_redrawn_never_opens_another(self):
        self.w59_gone_case([self.w59_items()[:1]])

    def test_w59_selected_task_removed_across_two_refreshes_never_opens_another(self):
        task_c = {'role': 'Developer', 'task': self.W59_TASK_C, 'pr': '', 'state': 'RUNNING',
                  'updated_at': '2026-11-01T00:02:00+00:00'}
        self.w59_gone_case([self.w59_items()[:1], self.w59_items()[:1] + [task_c]])

    def test_w59_down_after_the_selected_task_left_moves_to_a_real_row(self):
        async def opened(ui):
            await self.until(ui, lambda ui: self.W59_TASK_A in self.w59_text(ui), 'detail read')

        def gone(ui):
            ui.finish_background(self.session_id, (self.w59_items()[:1], None))   # B is gone

        with patch.object(chat_tui_status, 'read_task_detail',
                          side_effect=lambda task_id: self.w59_record(task_id)):
            self.drive([self.connect(), self.connected, self.w59_listed, '\x1b[B', '\x1b[B',
                        gone, self.w59_redraw, '\x1b[B',
                        lambda ui: self.assertEqual(ui.background_selected,
                                                    ('Developer', self.W59_TASK_A)),
                        lambda ui: self.assertEqual(len(self.w59_highlighted(ui)), 1),
                        '\r', opened])

    def test_w59_enter_never_opens_another_task_when_the_highlighted_one_left_the_list(self):
        reads = []

        def read(task_id):
            reads.append(task_id)
            return self.w59_record(task_id)

        def gone(ui):
            ui.background_text()                              # Task B drawn highlighted
            ui.finish_background(self.session_id, (self.w59_items()[:1], None))   # B is gone

        async def shown(ui):
            await self.until(ui, lambda ui: ui.show_task_view, 'detail view')
            await asyncio.sleep(0.1)
            self.assertIn('Core 狀態：UNKNOWN（選中的 Task ' + self.W59_TASK_B[:8]
                          + ' 已不在背景清單（清單剛更新）', ui.task_view.text)
            self.assertNotIn(self.W59_TASK_A, ui.task_view.text)
            self.assertEqual(reads, [])                       # Task A was never opened
            self.assertEqual(ui.input.text, '')
            self.assertFalse(ui.busy)

        with patch.object(chat_tui_status, 'read_task_detail', side_effect=read):
            self.drive([self.connect(), self.connected, self.w59_listed, '\x1b[B', '\x1b[B',
                        gone, '\r', shown])
    # -- W59 v4: the panel's open state is the Owner's, never the list's (High Debug) ------
    W59_ERROR = 'TaskStore read failed; details omitted'

    @staticmethod
    async def w59_esc(ui):
        """A lone Esc, then the Esc timeouts, so the key has been handled."""
        ui.app.input.send_text('\x1b')
        await asyncio.sleep(1.2)

    def w59_refresh(self, items, error=None):
        async def step(ui):
            previous_load = ui.background_loaded
            ui.finish_background(self.session_id, (items, error))
            ui.app.invalidate()
            ui.background_text()
            await self.until(ui, lambda ui: ui.background_loaded != previous_load,
                             'background refresh')
        return step

    def w59_sent(self):
        return sum(len(fake.users()) for fake in self.fakes)

    def test_w59_refresh_selection_transition_matrix(self):
        a, b = self.w59_items()
        b_role = dict(b, role='Developer')
        task_b = ('Reviewer', self.W59_TASK_B)

        def check(selected=task_b, highlighted=None, index=1):
            def step(ui):
                self.assertTrue(ui.show_background)               # still open (Owner's)
                self.assertEqual(ui.background_selected, selected)
                self.assertEqual(ui.background_index, index)
                rows = self.w59_highlighted(ui)
                if highlighted is None:
                    self.assertEqual(rows, [])
                else:
                    self.assertEqual(len(rows), 1)
                    self.assertIn(highlighted[:8], rows[0])
            return step

        self.drive([self.connect(), self.connected, self.w59_listed, '\x1b[B', '\x1b[B',
                    check(highlighted=self.W59_TASK_B),
                    self.w59_refresh([b, a]), check(highlighted=self.W59_TASK_B),   # reorder
                    self.w59_refresh([a]), check(), self.w59_redraw, check(),       # removal
                    self.w59_refresh([a, b]), check(highlighted=self.W59_TASK_B),   # reappears
                    self.w59_refresh([a, b_role]), check(),                         # role change
                    self.w59_refresh([]), self.w59_redraw, self.w59_redraw, check(),   # empty
                    self.w59_refresh([], self.W59_ERROR), check(),                  # failed read
                    self.w59_refresh([b, a]), check(highlighted=self.W59_TASK_B),   # recovery
                    lambda ui: self.assertTrue(ui.background_visible()),
                    self.w59_refresh([a]), '\x1b[A',                                # explicit only
                    check(selected=('Developer', self.W59_TASK_A), highlighted=self.W59_TASK_A,
                          index=0)])

    def w59_enter_unknown(self, setup, reason):
        reads = []

        def read(task_id):
            reads.append(task_id)
            return self.w59_record(task_id)

        def queued(ui):
            ui.pm_outbox.append('排隊的訊息')
            ui.pm_outbox_paused = 'esc'

        def before(ui):
            self.assertTrue(ui.show_background)                   # survives until Enter

        async def shown(ui):
            await self.until(ui, lambda ui: ui.show_task_view, 'UNKNOWN detail')
            await asyncio.sleep(0.1)
            self.assertIn('Core 狀態：UNKNOWN（', ui.task_view.text)
            self.assertIn(reason, ui.task_view.text)
            self.assertEqual(reads, [])                           # no Task read
            self.assertEqual(self.w59_sent(), 0)                  # nothing sent to the PM
            self.assertEqual(list(ui.pm_outbox), ['排隊的訊息'])  # the queue was not resumed
            self.assertFalse(ui.show_background)

        with patch.object(chat_tui_status, 'read_task_detail', side_effect=read):
            self.drive([self.connect(), self.connected, queued] + setup
                       + [self.w59_redraw, self.w59_redraw, before, '\r', shown])

    def test_w59_empty_panel_enter_unknown(self):
        selected_b = [self.w59_listed, '\x1b[B', '\x1b[B']
        self.w59_enter_unknown(selected_b + [self.w59_refresh([])],
                               '選中的 Task bbbbbbbb 已不在背景清單')
        self.w59_enter_unknown(selected_b + [self.w59_refresh([], self.W59_ERROR)],
                               '背景清單讀取失敗：' + self.W59_ERROR)

        def turn_row(ui):
            ui.finish_background(self.session_id, ([], None))
            ui._pm_background_track(('turn', 99))
            ui.app.invalidate()

        def turn_gone(ui):
            ui._pm_background_live.clear()                        # the last synthetic row ends
            ui.app.invalidate()
        self.w59_enter_unknown([turn_row, '\x1b[B', turn_gone], '已不在背景清單')

    def test_w59_none_selection_enter_unknown(self):
        def unselect(ui):
            ui.background_selected = None
        self.w59_enter_unknown([self.w59_listed, '\x1b[B', unselect], '沒有選中的列')
        self.w59_enter_unknown([self.w59_listed, '\x1b[B', unselect, self.w59_refresh([])],
                               '沒有選中的列')

    def test_w59_user_close_survives_refresh(self):
        a, b = self.w59_items()

        def closed(ui):
            self.assertFalse(ui.show_background)
            self.assertFalse(ui.background_visible())

        def reopened(ui):
            self.assertTrue(ui.show_background)
            self.assertEqual(ui.background_selected, ('Developer', self.W59_TASK_A))

        self.drive([self.connect(), self.connected, self.w59_listed, '\x1b[B', '\x1b[B',
                    self.w59_esc, closed, self.w59_refresh([]), closed,
                    self.w59_refresh([a, b]), closed, '\x1b[B', reopened])
        self.drive([self.connect(), self.connected, self.w59_listed, '\x1b[B', 'x', closed,
                    self.w59_refresh([]), closed, self.w59_refresh([a, b]), closed,
                    '\x7f', closed, '\x1b[B', reopened])

    def test_w59_modal_escape_priority_after_refresh(self):
        self.turns = [_full_turn(hold=True)]
        interrupts = []

        def counting(ui):
            original = ui.interrupt_pm
            ui.interrupt_pm = lambda *a: (interrupts.append(1), original(*a))

        def layer(panel=None, detail=None, searching=None, count=0):
            async def step(ui):
                await self.until(ui, lambda ui: (panel is None or ui.show_background == panel)
                                 and (detail is None or ui.show_task_view == detail)
                                 and (searching is None or ui.searching == searching)
                                 and len(interrupts) == count
                                 and (count or ui.pm_turn_active), 'modal layer')
                if panel is not None:
                    self.assertEqual(ui.show_background, panel)
                if detail is not None:
                    self.assertEqual(ui.show_task_view, detail)
                if searching is not None:
                    self.assertEqual(ui.searching, searching)
                self.assertEqual(len(interrupts), count)
                if not count:
                    self.assertTrue(ui.pm_turn_active)
            return step

        streaming = lambda ui: self.until(ui, lambda ui: 'PM\n  Hello' in ui.conversation.text)
        with patch.object(chat_tui_status, 'read_task_detail',
                          side_effect=lambda task_id: self.w59_record(task_id)):
            self.drive([self.connect(), self.connected, counting, self.w59_listed,
                        '請問進度？\r', streaming,
                        # search -> panel -> PM, with an empty refresh under the panel
                        '\x1b[B', self.w59_refresh([]), '\x12', layer(True, searching=True),
                        self.w59_esc, layer(True, searching=False),
                        self.w59_esc, layer(False),
                        self.w59_esc, layer(False, count=1)])
        self.turns = [_full_turn(hold=True)]
        interrupts.clear()
        with patch.object(chat_tui_status, 'read_task_detail',
                          side_effect=lambda task_id: self.w59_record(task_id)):
            self.drive([self.connect(), self.connected, counting, self.w59_listed,
                        '請問進度？\r', streaming,
                        # search -> detail -> PM, with an empty refresh under the detail
                        '\x1b[B', '\r', self.w59_refresh([]), '\t', '\x12',
                        layer(detail=True, searching=True),
                        self.w59_esc, layer(detail=True, searching=False),
                        self.w59_esc, layer(False, detail=False),
                        self.w59_esc, layer(False, detail=False, count=1)])

    def test_w59_zero_space_retains_modal_ownership(self):
        self.turns = [_full_turn(hold=True)]
        interrupts, reads = [], []
        tiny, normal = Size(rows=4, columns=80), Size(rows=24, columns=80)

        def size(value):
            def step(ui):
                ui.app.output.get_size = lambda: value
                ui.app.invalidate()
                return asyncio.sleep(0.1)
            return step

        def counting(ui):
            original = ui.interrupt_pm
            ui.interrupt_pm = lambda *a: (interrupts.append(1), original(*a))

        def hidden(ui):
            self.assertEqual(ui.background_rows(), 0)
            self.assertFalse(ui.background_visible())
            self.assertTrue(ui.show_background)                   # still the Owner's panel

        def esc_closed(ui):
            self.assertFalse(ui.show_background)
            self.assertEqual(interrupts, [])
            self.assertTrue(ui.pm_turn_active)

        async def unknown(ui):
            await self.until(ui, lambda ui: ui.show_task_view, 'UNKNOWN detail')
            self.assertIn('已不在背景清單', ui.task_view.text)
            self.assertEqual(reads, [])
            self.assertEqual(self.w59_sent(), 1)                  # only the turn's own message

        def restored(ui):
            self.assertTrue(ui.show_task_view)
            self.assertTrue(ui.task_view_visible())
            self.assertEqual(interrupts, [])

        def read(task_id):
            reads.append(task_id)
            return self.w59_record(task_id)

        streaming = lambda ui: self.until(ui, lambda ui: 'PM\n  Hello' in ui.conversation.text)
        with patch.object(chat_tui_status, 'read_task_detail', side_effect=read):
            self.drive([self.connect(), self.connected, counting, self.w59_listed,
                        '請問進度？\r', streaming,
                        '\x1b[B', size(tiny), hidden, self.w59_esc, esc_closed, size(normal),
                        '\x1b[B', '\x1b[B', size(tiny), self.w59_refresh(self.w59_items()[:1]),
                        hidden, '\r', unknown, size(normal), restored,
                        lambda ui: self.fakes[0].gate.set(), self.turn_done])

    def test_w59_refresh_during_detail_preserves_snapshot(self):
        self.turns = [_full_turn(hold=True)]
        snap = {}

        async def opened(ui):
            await self.until(ui, lambda ui: self.W59_TASK_A in self.w59_text(ui), 'detail read')
            ui.task_view.scroll_rows(3)
            snap.update(text=ui.task_view.text, top=ui.task_view.top,
                        offset=ui.view.top_offset, seq=ui.task_view_seq)

        def same(ui):
            self.assertTrue(ui.show_task_view)
            self.assertEqual(ui.task_view.text, snap['text'])
            self.assertEqual(ui.task_view.top, snap['top'])
            self.assertEqual(ui.view.top_offset, snap['offset'])
            self.assertEqual(ui.input.text, '草稿')

        def stale(ui):
            ui.finish_task_view(snap['seq'] - 1, 'STALE 舊結果')     # superseded read
            self.assertNotIn('STALE', ui.task_view.text)

        async def streamed(ui):
            self.fakes[0].gate.set()
            await self.until(ui, lambda ui: 'Hello 老闆' in ui.conversation.text
                             and not ui.pm_turn_active, 'PM output while the detail is open')

        def closed_stale(ui):
            ui.finish_task_view(snap['seq'], 'LATE 結果')            # current seq, but closed
            self.assertFalse(ui.show_task_view)
            self.assertNotIn('LATE', ui.task_view.text)

        async def close_detail(ui):
            ui.app.input.send_text('\x1b')
            await self.until(ui, lambda ui: not ui.show_task_view, 'detail closed')

        streaming = lambda ui: self.until(ui, lambda ui: 'PM\n  Hello' in ui.conversation.text)
        with patch.object(chat_tui_status, 'read_task_detail',
                          side_effect=lambda task_id: self.w59_record(task_id)):
            self.drive([self.connect(), self.connected, self.w59_listed,
                        '請問進度？\r', streaming, '\x1b[B', '\r', opened, '\t', '草稿', same,
                        self.w59_refresh([]), same, self.w59_refresh([], self.W59_ERROR), same,
                        self.w59_refresh(self.w59_items()), same, stale, same,
                        streamed, same, close_detail, closed_stale])

    # -- W60: evidence and provenance in the Task detail; c copies the Task ID --------------
    def w60_copies(self, ui):
        copies = []
        ui.task_view._copier = lambda text: copies.append(text) or True
        return copies

    def test_w60_c_copies_the_opened_task_id_even_after_a_refresh(self):
        a, _b = self.w59_items()
        seen = {}

        async def opened(ui):
            await self.until(ui, lambda ui: self.W59_TASK_B in self.w59_text(ui), 'detail read')
            self.assertIn('c 複製 Task ID', ui.status_text())
            self.assertIn('c 複製 Task ID', self.w59_text(ui).splitlines()[0])
            seen['copies'] = self.w60_copies(ui)

        async def copied(ui):
            await self.until(ui, lambda ui: seen['copies'], 'Task ID copied')
            ui.task_view.wait_copy()
            self.assertEqual(seen['copies'], [self.W59_TASK_B])   # the opened Task, not A
            self.assertEqual(ui.input.text, '')                    # c never typed
            self.assertTrue(ui.show_task_view)
            self.assertIn('已複製 32 字', ui.status_keys())
            self.assertIn('Task ID', ui.status_keys())

        with patch.object(chat_tui_status, 'read_task_detail',
                          side_effect=lambda task_id: self.w59_record(task_id)):
            self.drive([self.connect(), self.connected, self.w59_listed, '\x1b[B', '\x1b[B',
                        '\r', opened, self.w59_refresh([a]), 'c', copied])
        self.assertEqual(self.w59_sent(), 0)

    def test_w60_c_types_into_a_draft_and_after_the_detail_closes(self):
        seen = {}

        async def opened(ui):
            await self.until(ui, lambda ui: self.W59_TASK_A in self.w59_text(ui), 'detail read')
            seen['copies'] = self.w60_copies(ui)

        def drafted(ui):
            self.assertEqual(ui.input.text, '草c')                  # a draft keeps typing
            self.assertNotIn('c 複製 Task ID', ui.status_text())
            ui.input.text = ''

        async def closed(ui):
            await self.until(ui, lambda ui: not ui.show_task_view, 'Esc closed the detail')

        def typed(ui):
            self.assertEqual(ui.input.text, 'c')                    # chat typing as before
            self.assertEqual(seen['copies'], [])

        with patch.object(chat_tui_status, 'read_task_detail',
                          side_effect=lambda task_id: self.w59_record(task_id)):
            self.drive([self.connect(), self.connected, self.w59_listed, '\x1b[B', '\r', opened,
                        '\t', '草', 'c', drafted, self.w59_esc, closed, 'c', typed])

    def test_w60_c_and_shift_c_type_on_the_input_line_and_act_only_in_the_detail(self):
        """Owner 3rd acceptance (HIGH): "cat" after pm> must stay "cat"; c/C act only while the
        detail pane has focus (it has focus when it opens; Tab goes to the input and back)."""
        seen = {}

        def typed_cat(ui):
            self.assertEqual(ui.input.text, 'cat')               # no detail open: plain typing
            ui.input.text = ''

        async def opened(ui):
            await self.until(ui, lambda ui: self.W59_TASK_A in self.w59_text(ui), 'detail read')
            self.assertTrue(ui.app.layout.has_focus(ui.task_pane))
            seen['copies'] = self.w60_copies(ui)

        def on_input(ui):
            self.assertTrue(ui.app.layout.has_focus(ui.input))
            self.assertIn('Tab 回詳情', ui.status_keys())

        async def drafted(ui):
            await asyncio.sleep(0.1)
            self.assertEqual(ui.input.text, 'cat Cat')            # typed, never copied
            self.assertEqual(seen['copies'], [])
            self.assertTrue(ui.show_task_view)

        async def copied(ui):
            await self.until(ui, lambda ui: seen['copies'], 'Task ID copied')
            ui.task_view.wait_copy()
            self.assertEqual(seen['copies'], [self.W59_TASK_A])
            self.assertEqual(ui.input.text, 'cat Cat')            # the draft is untouched

        with patch.object(chat_tui_status, 'read_task_detail',
                          side_effect=lambda task_id: self.w59_record(task_id)):
            self.drive([self.connect(), self.connected, 'cat', typed_cat, self.w59_listed,
                        '\x1b[B', '\r', opened, '\t', on_input, 'cat Cat', drafted,
                        '\t', lambda ui: self.assertTrue(ui.app.layout.has_focus(ui.task_pane)),
                        'c', copied])
        self.assertEqual(self.w59_sent(), 0)

    def test_w60_slow_copy_still_reports_its_result(self):
        seen = {}

        async def opened(ui):
            await self.until(ui, lambda ui: self.W59_TASK_A in self.w59_text(ui), 'detail read')
            seen['copies'] = self.w60_copies(ui)
            ui.copy_task_id()
            ui.task_view.wait_copy()
            ui.task_copy_at -= 10                 # the key press was long ago (slow pbcopy)
            self.assertIn('Task ID 已複製 32 字', ui.status_keys())
            ui.task_view.copied = (32, time.monotonic() - 10)
            self.assertNotIn('已複製', ui.status_keys())
            self.assertIn('c 複製 Task ID', ui.status_keys())

        with patch.object(chat_tui_status, 'read_task_detail',
                          side_effect=lambda task_id: self.w59_record(task_id)):
            self.drive([self.connect(), self.connected, self.w59_listed, '\x1b[B', '\r', opened])

    # Hostile TaskStore text (request, review actor, abandonment, events): OSC 52 clipboard
    # write, screen clear, OSC 8 link to another site, C1 CSI and a bidi override.
    W60_HOSTILE = ('\x1b]52;c;c2VjcmV0\x07 \x1b[2J \x1b]8;;https://evil.example/\x1b\\點我'
                   '\x1b]8;;\x1b\\ \x9b31m \u202e')

    def w60_hostile_record(self, task_id):
        hostile, url = self.W60_HOSTILE, OWNER_PR_URL
        return {'task_id': task_id, 'state': 'PM_RESPONDED',
                'request': 'W60 惡意' + hostile,
                'created_at': '2026-11-01T00:00:00Z', 'updated_at': '2026-11-01T00:01:00Z' + hostile,
                'pm_session_id': 'pm-test', 'developer': {'state': 'DEVELOPER_PENDING'},
                'abandonment': {'reason': 'r' + hostile, 'pm_session_id': 'pm-test',
                                'abandoned_at': '2026-11-01T00:02:00Z'},
                'completion': {'state': 'WAITING_CI', 'evidence': {'review_pr_ci': {
                    'stage': 'REVIEW_PR_CI', 'pr_number': 661, 'pr_state': 'OPEN',
                    'ci_head_sha': 'ab' * 20, 'review_source': 'PR_COMMENT',
                    'review_actor': ('bot' + hostile + ' ' + url
                                     + '9 https://evil.example/pull/1 #77')}}},
                'completion_events': [{'source': 'LOCAL' + hostile, 'state': 'S' + hostile,
                                       'received_at': '2026-11-01T00:03:00Z'}]}

    def test_w60_hostile_taskstore_text_is_escaped_and_links_only_full_pr_urls(self):
        import unicodedata

        async def opened(ui):
            await self.until(ui, lambda ui: '證據與出處' in self.w59_text(ui), 'detail read')
            text = self.w59_text(ui)
            controls = [char for char in text if char != '\n' and (
                unicodedata.category(char) in ('Cc', 'Cf'))]
            self.assertEqual(controls, [])                     # no live ESC, BEL, C1, bidi
            self.assertIn('\\x1b]52;c;c2VjcmV0\\x07', text)    # shown, escaped
            self.assertIn('\\x9b31m', text)
            self.assertIn('\\u202e', text)
            for row in ('  Reviewer 來源：', 'Stage: ABANDONED', '  讀取時間'):
                self.assertIn(row, text)
            self.assertNotIn('RUNNING', text)
            links = chat_tui._TaskViewLinker(None).links(text, 0, len(text))
            urls = sorted({url for _low, _high, url in links})
            self.assertEqual(urls, [OWNER_PR_URL + '661', OWNER_PR_URL + '9'])
            self.assertNotIn('\x1b', ui.status_text())

        with patch.object(chat_tui_status, 'read_task_detail', side_effect=self.w60_hostile_record):
            self.drive([self.connect(), self.connected, self.w59_listed, '\x1b[B', '\r', opened])

    # -- W60 Owner acceptance: drag-select and copy-all in the Task detail ----------------
    def w60_open(self, steps, record=None, size=None):
        """Open the first row's detail, run ``steps`` (functions of ui and copies)."""
        seen = {}

        async def opened(ui):
            await self.until(ui, lambda ui: '證據與出處' in self.w59_text(ui), 'detail read')
            seen['copies'] = self.w60_copies(ui)
            for step in steps:
                result = step(ui, seen['copies'])
                if asyncio.iscoroutine(result):
                    await result

        with patch.object(chat_tui_status, 'read_task_detail',
                          side_effect=record or (lambda task_id: self.w59_record(task_id))):
            ui, _ = self.drive([self.connect(), self.connected, self.w59_listed, '\x1b[B', '\r',
                                opened], size=size)
        return ui, seen['copies']

    @staticmethod
    async def w60_mouse(ui, *events, pause=0.05):
        for event in events:
            ui.app.input.send_text(event)
            await asyncio.sleep(pause)

    def test_w60_drag_in_the_detail_selects_and_copies_like_the_conversation(self):
        async def dragged(ui, copies):
            left, top, _height = ui._pane_geometry()
            self.assertEqual(ui._mouse_view(), ui.task_view)
            lines = ui.task_view.text.splitlines()
            row = next(n for n, line in enumerate(lines)
                       if line.startswith('來源：TaskStore'))
            y = top + 1 + row
            await self.w60_mouse(ui, press(left + 1, y), drag(left + 16, y))
            self.assertTrue(ui.task_view.dragging)
            self.assertTrue(ui.task_view.selected_range())          # highlighted while dragging
            self.assertFalse(ui.view.selected_range())               # never the hidden chat
            await self.w60_mouse(ui, release(left + 16, y))
            self.assertTrue(ui.task_view.wait_copy())
            line = lines[row]
            self.assertTrue(copies and line.startswith(copies[-1]), (copies, line))
            self.assertTrue(copies[-1].startswith('來源：TaskStore'))
            self.assertIn('已複製 %d 字' % len(copies[-1]), ui.status_keys())
            self.assertNotIn('Task ID 已複製', ui.status_keys())
            count = len(copies)
            ui.app.input.send_text('\x03')                          # Ctrl-C: copy again
            await asyncio.sleep(0.1)
            self.assertTrue(ui.task_view.wait_copy())
            self.assertEqual(copies[count:], [copies[count - 1]])
            self.assertFalse(ui.task_view.selected_range())
            self.assertFalse(ui.quit_pending)
            self.assertTrue(ui.show_task_view)
            self.assertEqual(ui.input.text, '')

        ui, _copies = self.w60_open([dragged])
        self.assertEqual(self.w59_sent(), 0)

    def test_w60_double_click_in_the_detail_selects_a_word(self):
        async def clicked(ui, copies):
            left, top, _height = ui._pane_geometry()
            lines = ui.task_view.text.splitlines()
            row = next(n for n, line in enumerate(lines) if line.startswith('Status (from'))
            self.assertTrue(all(get_cwidth(line) < 80 for line in lines[:row]))  # no wrap above
            x = left + 1 + get_cwidth(lines[row][:lines[row].index('TaskStore')]) + 2
            y = top + 1 + row
            await self.w60_mouse(ui, press(x, y), release(x, y), press(x, y), release(x, y))
            self.assertTrue(ui.task_view.wait_copy())
            self.assertEqual(copies[-1:], ['TaskStore'])

        self.w60_open([clicked])

    def test_w60_drag_past_the_detail_edge_autoscrolls(self):
        async def edge(ui, copies):
            left, top, height = ui._pane_geometry()
            self.assertEqual(ui.task_view.top, 0)
            await self.w60_mouse(ui, press(left + 1, top + 3), drag(left + 5, top + 4),
                                 drag(left + 5, top + height + 3))   # below the pane
            await asyncio.sleep(0.5)                                 # held: keeps scrolling
            self.assertGreater(ui.task_view.top, 1)
            self.assertEqual(ui.view.top_offset, before['offset'])   # the chat never moved
            await self.w60_mouse(ui, release(left + 5, top + height + 3))
            self.assertTrue(ui.task_view.wait_copy())
            self.assertIn('\n', copies[-1])                          # several rows copied
            self.assertIn(copies[-1], ui.task_view.text)

        before = {}

        def remember(ui, copies):
            before['offset'] = ui.view.top_offset

        self.w60_open([remember, edge],
                      record=lambda task_id: dict(self.w59_record(task_id), completion_events=[
                          {'source': 'LOCAL', 'state': 'S%d' % n,
                           'received_at': '2026-11-01T00:%02d:00Z' % n} for n in range(10)]))

    def test_w60_shift_c_copies_the_whole_multi_screen_detail_as_plain_text(self):
        async def copied_all(ui, copies):
            _left, _top, height = ui._pane_geometry()
            self.assertIn('C 複製全文', self.w59_text(ui).splitlines()[0])
            self.assertIn('c 複製 Task ID · C 全文', ui.status_keys())
            ui.app.input.send_text('C')
            await asyncio.sleep(0.1)
            self.assertTrue(ui.task_view.wait_copy())
            text = copies[-1]
            self.assertEqual(text, ui.task_view.text)                 # every line, not the screen
            self.assertGreater(len(text.splitlines()), height)
            self.assertIn('最近事件', text)
            self.assertNotIn('\x1b', text)
            self.assertNotIn('\x07', text)
            self.assertNotIn('\x9b', text)
            self.assertIn('\\x1b]52', text)                          # hostile text, escaped
            self.assertNotIn('RUNNING', text)
            self.assertEqual(ui.input.text, '')
            self.assertIn('全文 已複製 %d 字' % len(text), ui.status_keys())
            self.assertEqual(ui.task_view.top, 0)                     # reading place unchanged

        self.w60_open([copied_all], record=lambda task_id: dict(
            self.w60_hostile_record(task_id), completion_events=[
                {'source': 'LOCAL', 'state': 'S%d' % n,
                 'received_at': '2026-11-01T00:%02d:00Z' % n} for n in range(10)]),
            size=[Size(rows=24, columns=80)])

    def test_w60_esc_ends_a_detail_selection_and_keeps_chat_position(self):
        async def selected(ui, copies):
            left, top, _height = ui._pane_geometry()
            await self.w60_mouse(ui, press(left + 1, top + 2), drag(left + 10, top + 2))
            self.assertTrue(ui.task_view.selected_range())
            await self.w59_esc(ui)
            self.assertFalse(ui.show_task_view)
            self.assertFalse(ui.task_view.selected_range())
            self.assertFalse(ui.task_view.dragging and ui._autoscroll_handle)
            self.assertEqual(ui._mouse_view(), ui.view)

        self.w60_open([selected])

    def test_w60_down_in_pm_mode_without_background_rows_says_why(self):
        def hinted(ui):
            self.assertFalse(ui.show_background)
            self.assertIn('llmtool: 沒有背景 Task；PM 派工後 ↓ 可看', ui.conversation.text)

        self.drive([self.connect(), self.connected,
                    lambda ui: ui.finish_background(self.session_id, ([], None)),
                    '\x1b[B', hinted])

    def test_w60_down_right_after_pm_connect_says_loading_not_no_tasks(self):
        """Owner 3rd acceptance: before the first background read finishes, ↓ says 讀取中…"""
        def unread(ui):
            ui.background_key, ui.background_loaded = None, None
            ui.background_reader = object()          # a read is still running

        def loading(ui):
            self.assertIn('背景 Task 讀取中…', ui.conversation.text)
            self.assertNotIn('沒有背景 Task', ui.conversation.text)

        def finished(ui):
            ui.background_reader = None
            ui.finish_background(self.session_id, ([], None))
            ui.background_reader = object()          # no further read during the test

        def empty(ui):
            self.assertIn('llmtool: 沒有背景 Task；PM 派工後 ↓ 可看', ui.conversation.text)

        self.drive([self.connect(), self.connected, unread, '\x1b[B', loading,
                    finished, '\x1b[B', empty])

    def test_w60_c_without_a_task_id_copies_nothing_and_says_why(self):
        seen = {}

        async def unknown(ui):
            await self.until(ui, lambda ui: 'Core 狀態：UNKNOWN' in self.w59_text(ui), 'UNKNOWN')
            self.assertNotIn('c 複製 Task ID', ui.status_text())
            self.assertNotIn('c 複製 Task ID', self.w59_text(ui))
            seen['copies'] = self.w60_copies(ui)

        def refused(ui):
            self.assertEqual(seen['copies'], [])
            self.assertEqual(ui.input.text, '')
            self.assertIn('沒有 Task ID 可複製', ui.status_keys())

        self.drive([self.connect(), self.connected, self.w59_listed, '\x1b[B', '\x1b[B',
                    self.w59_refresh([]), '\r', unknown, 'c', refused])


class W59DetailTextTests(unittest.TestCase):
    """chat_tui_status.task_detail_text: Core state, source/time, at most 10 events."""

    def test_detail_text_uses_core_state_and_limits_the_timeline(self):
        record = {'task_id': 'c' * 32, 'request': '詳情', 'state': 'PM_RESPONDED',
                  'created_at': '2026-11-01T00:00:00+00:00', 'developer': None,
                  'abandonment': {'at': '2026-11-01T00:02:00+00:00'},
                  'completion_events': [{'source': 'LOCAL', 'state': 'S%d' % n,
                                         'received_at': '2026-11-01T00:%02d:00+00:00' % n}
                                        for n in range(20)]}
        text = chat_tui_status.task_detail_text(record, '12:34:56')
        self.assertIn('來源：TaskStore（本機）｜讀取於 12:34:56', text)
        self.assertIn('Core 狀態：ABANDONED（task_status.derive_state）', text)
        events = text.split('最近事件', 1)[1].splitlines()[1:]
        self.assertLessEqual(len([line for line in events if line.startswith('  ')]), 10)

    def test_detail_text_without_a_record_is_unknown_with_the_reason(self):
        text = chat_tui_status.task_detail_text(None, '12:34:56', reason='TaskStore 讀取失敗')
        self.assertIn('Core 狀態：UNKNOWN（TaskStore 讀取失敗）', text)
        self.assertNotIn('最近事件', text)


class TaskOwnerSummaryTests(unittest.TestCase):
    """The three detail orientation lines use only the persisted Task projection."""

    def detail(self, record):
        return chat_tui_status.task_detail_text(record, '12:34:56').splitlines()[1:4]

    def test_copy_header_stays_first_and_summary_follows_it(self):
        text = chat_tui_status.task_detail_text(
            {'task_id': 'a' * 32, 'request': '需求',
             'developer': {'state': 'DEVELOPER_PENDING'}},
            '12:34:56', copy_hint=True)
        lines = text.splitlines()
        self.assertIn('c 複製 Task ID', lines[0])
        self.assertIn('C 複製全文', lines[0])
        self.assertEqual(lines[1:4], [
            '狀態：Developer 執行中', '下一步輪到誰：Developer', '你需要做：目前不用'])

    def test_summary_lines_lead_and_describe_running_and_waiting_stages(self):
        running = {'task_id': 'a' * 32, 'request': '需求',
                   'developer': {'state': 'DEVELOPER_PENDING'}}
        waiting = {'task_id': 'b' * 32, 'request': '需求',
                   'developer': {'state': 'DEVELOPER_RESPONDED'},
                   'completion': {'state': 'WAITING_EXECUTOR'}}
        self.assertEqual(self.detail(running), [
            '狀態：Developer 執行中', '下一步輪到誰：Developer', '你需要做：目前不用'])
        self.assertEqual(self.detail(waiting), [
            '狀態：等待執行證據', '下一步輪到誰：Developer', '你需要做：目前不用'])

    def test_blocked_and_unknown_developer_outcome_do_not_guess(self):
        blocked = {'task_id': 'c' * 32, 'request': '需求',
                   'developer': {'state': 'DEVELOPER_PENDING',
                                 'blocked_reason': 'needs Owner decision'}}
        unknown = {'task_id': 'd' * 32, 'request': '需求',
                   'developer': {'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                                 'error_code': 'CODEX_BLOCKED'}}
        self.assertEqual(self.detail(blocked), [
            '狀態：已阻塞', '下一步輪到誰：PM', '你需要做：目前不用'])
        self.assertEqual(self.detail(unknown), [
            '狀態：Developer 結果不明', '下一步輪到誰：PM', '你需要做：目前不用'])

    def test_owner_decision_is_explicit_and_completed_task_has_no_guessed_actor(self):
        waiting_owner = {'task_id': 'e' * 32, 'request': '需求',
                         'developer': {'state': 'DEVELOPER_PENDING',
                                       'waiting_owner': True}}
        completed = {'task_id': 'f' * 32, 'request': '需求',
                     'developer': {'state': 'DEVELOPER_RESPONDED'},
                     'completion': {'state': 'PM_RETURNED',
                                    'pm_recommendation': {'decision': 'ACCEPT'}}}
        self.assertEqual(self.detail(waiting_owner), [
            '狀態：Developer 執行中', '下一步輪到誰：Owner', '你需要做：請 Owner 提供決定'])
        self.assertEqual(self.detail(completed), [
            '狀態：已完成', '下一步輪到誰：不明', '你需要做：目前不用'])

    def test_recorded_merge_does_not_look_like_running_work(self):
        merged = {'task_id': 'g' * 32, 'request': '需求',
                  'developer': {'state': 'DEVELOPER_PENDING'},
                  'completion_events': [{'source': 'GITHUB', 'marker_type': 'merge',
                                         'marker_status': 'MERGED', 'state': 'MERGED'}]}
        self.assertEqual(self.detail(merged), [
            '狀態：已完成', '下一步輪到誰：不明', '你需要做：目前不用'])

    def test_failed_task_does_not_guess_owner_without_an_owner_field(self):
        failed = {'task_id': 'h' * 32, 'request': '需求',
                  'state': 'PM_RESPONSE_REJECTED', 'developer': None}
        self.assertEqual(self.detail(failed), [
            '狀態：已失敗', '下一步輪到誰：不明', '你需要做：目前不用'])

    def test_missing_record_summary_is_unknown_in_traditional_chinese(self):
        self.assertEqual(self.detail(None), [
            '狀態：不明', '下一步輪到誰：不明', '你需要做：目前不用'])


class W60EvidenceTextTests(unittest.TestCase):
    """chat_tui_status.task_detail_text: the W60 evidence and provenance rows."""

    SHA = '0123456789abcdef0123456789abcdef01234567'

    def record(self, **changes):
        record = {'task_id': 'd' * 32, 'request': '證據測試', 'state': 'PM_RESPONDED',
                  'created_at': '2026-11-02T01:00:00Z', 'updated_at': '2026-11-02T01:02:03Z',
                  'pm_session_id': 'pm-test', 'developer': {'state': 'DEVELOPER_PENDING'}}
        record.update(changes)
        return record

    def completion(self, **review_ci):
        evidence = {'stage': 'REVIEW_PR_CI', 'pr_number': 661, 'pr_state': 'OPEN',
                    'ci_head_sha': self.SHA, 'review_source': 'PR_COMMENT',
                    'review_actor': 'reviewer-bot', 'formal_review_state': 'ACCEPTED',
                    'required_checks': ['ci'], 'successful_checks': ['ci']}
        evidence.update(review_ci)
        return {'state': 'WAITING_CI', 'evidence': {'review_pr_ci': evidence}}

    @staticmethod
    def rows(text):
        block = text.split('證據與出處', 1)[1].split('\n\n', 1)[0].splitlines()[1:]
        return {row.split('：', 1)[0].strip(): row for row in block}

    def test_evidence_rows_name_their_source_and_align_in_display_columns(self):
        text = chat_tui_status.task_detail_text(
            self.record(completion=self.completion(pr_state='MERGED')), '12:34:56')
        rows = self.rows(text)
        self.assertEqual(list(rows), ['PR', 'PR 狀態', 'CI head SHA', 'Reviewer 來源', '讀取時間'])
        self.assertIn('#661', rows['PR'])
        self.assertIn('completion review_pr_ci.pr_number', rows['PR'])
        self.assertIn('MERGED', rows['PR 狀態'])
        self.assertIn('review_pr_ci.pr_state', rows['PR 狀態'])
        self.assertIn(self.SHA, rows['CI head SHA'])              # the full head, not cut
        self.assertIn('review_pr_ci.ci_head_sha', rows['CI head SHA'])
        self.assertIn('PR_COMMENT', rows['Reviewer 來源'])
        self.assertIn('reviewer-bot', rows['Reviewer 來源'])
        self.assertIn('12:34:56', rows['讀取時間'])
        self.assertIn('2026-11-02T01:02:03Z', rows['讀取時間'])
        columns = {get_cwidth(row.split('：', 1)[0]) for row in rows.values()}
        self.assertEqual(len(columns), 1, rows)                   # values start in one column

    def test_disagreeing_pr_numbers_are_shown_as_conflict(self):
        record = self.record(completion=self.completion(),
                             review_dispatch_claim={'pr': 654, 'state': 'CLAIMED', 'version': 1})
        rows = self.rows(chat_tui_status.task_detail_text(record, '12:34:56'))
        self.assertIn('CONFLICT', rows['PR'])
        self.assertIn('#654', rows['PR'])
        self.assertIn('#661', rows['PR'])
        self.assertIn('review claim', rows['PR'])
        self.assertIn('CONFLICT', rows['PR 狀態'])               # no single PR state is chosen
        self.assertIn('#661', rows['PR 狀態'])                   # what review_pr_ci did store

    def test_missing_evidence_is_unknown_with_a_reason(self):
        rows = self.rows(chat_tui_status.task_detail_text(self.record(), '12:34:56'))
        for label in ('PR', 'PR 狀態', 'CI head SHA', 'Reviewer 來源'):
            self.assertRegex(rows[label], '：UNKNOWN（.+）$', label)
        self.assertNotIn('NOT_AVAILABLE', '\n'.join(rows.values()))

    def test_evidence_rows_use_no_east_asian_ambiguous_characters(self):
        import unicodedata
        record = self.record(completion=self.completion(),
                             review_dispatch_claim={'pr': 654, 'state': 'CLAIMED', 'version': 1})
        evidence = chat_tui_status.evidence_text(record, '12:34:56')
        self.assertEqual([char for char in evidence
                          if unicodedata.east_asian_width(char) == 'A'], [])

    def test_ci_not_started_and_claim_only_reviewer_stay_unknown(self):
        record = self.record(
            completion={'state': 'WAITING_REVIEW', 'evidence': {'review_pr_ci': {
                'stage': 'EXECUTOR_RESULT', 'formal_review_state': 'NOT_STARTED',
                'ci_state': 'NOT_STARTED'}}},
            review_dispatch_claim={'pr': 654, 'state': 'CLAIMED', 'version': 1,
                                   'reviewer_task': 'e' * 32})
        rows = self.rows(chat_tui_status.task_detail_text(record, '12:34:56'))
        self.assertRegex(rows['CI head SHA'], 'UNKNOWN（.*NOT_STARTED.*）')
        self.assertRegex(rows['Reviewer 來源'], 'UNKNOWN（.*claim.*）')
        self.assertNotIn('：PASS', rows['Reviewer 來源'])          # a claim is not a PASS
        self.assertRegex(rows['PR 狀態'], 'UNKNOWN（.+）')

    def test_review_receipt_is_the_reviewer_source(self):
        record = self.record(developer={'state': 'DEVELOPER_RESPONDED', 'direct_result_callback': {
            'state': 'PM_RETURNED', 'review_handoff': {'dispatch_state': 'DISPATCHED', 'receipt': {
                'reviewer_identity': 'codex-reviewer', 'reviewer_task_id': 'f' * 32,
                'execution_status': 'COMPLETED', 'verdict': 'PASS',
                'provenance': 'TRUSTED_REVIEW_ADAPTER'}}}})
        rows = self.rows(chat_tui_status.task_detail_text(record, '12:34:56'))
        self.assertIn('review receipt', rows['Reviewer 來源'])
        self.assertIn('TRUSTED_REVIEW_ADAPTER', rows['Reviewer 來源'])
        self.assertIn('codex-reviewer', rows['Reviewer 來源'])

    def test_a_merge_event_outranks_pr_state_collected_while_open(self):
        record = self.record(completion=self.completion(), completion_events=[{
            'source': 'GITHUB', 'marker_type': 'merge', 'marker_status': 'MERGED',
            'state': 'MERGED', 'received_at': '2026-11-02T01:03:00Z'}])
        rows = self.rows(chat_tui_status.task_detail_text(record, '12:34:56'))
        self.assertRegex(rows['PR 狀態'], '：MERGED（GitHub merge')
        self.assertIn('#661 為 OPEN', rows['PR 狀態'])            # the older evidence, named

    def test_out_of_range_pr_number_has_no_pr_state(self):
        record = self.record(completion=self.completion(pr_number=0))
        rows = self.rows(chat_tui_status.task_detail_text(record, '12:34:56'))
        self.assertRegex(rows['PR'], '：UNKNOWN（')
        self.assertRegex(rows['PR 狀態'], '：UNKNOWN（')

    def test_a_github_merge_event_is_a_stored_pr_state(self):
        record = self.record(completion_events=[{
            'source': 'GITHUB', 'marker_type': 'merge', 'marker_status': 'MERGED',
            'state': 'MERGED', 'received_at': '2026-11-02T01:03:00Z'}])
        rows = self.rows(chat_tui_status.task_detail_text(record, '12:34:56'))
        self.assertIn('MERGED', rows['PR 狀態'])
        self.assertIn('GitHub', rows['PR 狀態'])

    def test_abandoned_task_is_never_shown_as_running(self):
        record = self.record(abandonment={'reason': '需求取消', 'pm_session_id': 'pm-test',
                                          'abandoned_at': '2026-11-02T01:05:00Z'})
        text = chat_tui_status.task_detail_text(record, '12:34:56')
        self.assertIn('Core 狀態：ABANDONED', text)
        self.assertIn('Stage: ABANDONED', text)
        self.assertIn('需求取消', text)
        self.assertNotIn('RUNNING', text)
        self.assertEqual(chat_tui_status.stage(record)[0], 'ABANDONED')
        self.assertEqual(chat_tui_status.background_items([record]), [])   # not in-flight work


def pane_rows(ui):
    """Conversation pane rows as rendered on the last screen (above the rule row)."""
    screen = ui.app.renderer.last_rendered_screen
    rows = []
    for y in range(ui.app.output.get_size().rows):
        cells = screen.data_buffer[y]
        text = ''.join(cells[x].char for x in range(ui.app.output.get_size().columns)).rstrip()
        if text and set(text) == {'─'}:   # the rule row above the input (W56a)
            break
        rows.append(text)
    return rows


def selected_cells(ui):
    """Count of rendered cells drawn with the selection style."""
    screen = ui.app.renderer.last_rendered_screen
    size = ui.app.output.get_size()
    return sum(1 for y in range(size.rows) for x in range(size.columns)
               if 'selected' in screen.data_buffer[y][x].style)


WHEEL_UP, WHEEL_DOWN = '\x1b[<64;10;5M', '\x1b[<65;10;5M'


def press(x, y):
    return '\x1b[<0;%d;%dM' % (x, y)


def drag(x, y):
    return '\x1b[<32;%d;%dM' % (x, y)


def release(x, y):
    return '\x1b[<0;%d;%dm' % (x, y)


CJK_LINES = '\n'.join('%03d 中文長行測試abcdefghij ' % i * 1 + '中文長行測試abcdefghij ' * 15 for i in range(120))
SHORT_LINES = '\n'.join('%03d short line' % i for i in range(120))


@unittest.skipUnless(HAVE_TOOLKIT, 'prompt_toolkit not installed')
class ConversationViewTests(unittest.TestCase):
    """The pane model alone: rows, reading position, follow state, selection, clipboard."""

    def make(self, text, width=20, height=5):
        copies = []
        view = chat_tui_view.ConversationView(get_cwidth, copier=lambda t: copies.append(t) or True)
        view.set_text(text)
        view.layout(width, height)
        return view, copies

    def test_wraps_by_display_width_and_never_splits_a_wide_char(self):
        view, _ = self.make('中文字測試中文字測試x\nab\n\n' + 'y' * 25 + '\n' + 'z' * 20, width=10, height=3)
        shown = [view.text[s:e] for _l, s, e in view.rows]
        self.assertEqual(shown, ['中文字測試', '中文字測試', 'x', 'ab', '', 'y' * 10, 'y' * 10, 'yyyyy',
                                 'z' * 10, 'z' * 10])   # an exactly full line adds no blank row
        for text in shown:
            self.assertLessEqual(get_cwidth(text), 10)
        self.assertEqual(view.offset_at(0, 3), 1)      # right half of the second wide char
        self.assertEqual(view.offset_at(0, 99), 5)     # past the row end: the row end
        self.assertEqual(view.row_of(view.text.index('ab')), 3)
        self.assertEqual(view.row_of(len(view.text)), 9)

    def test_following_is_explicit_and_resumes_only_at_the_bottom(self):
        view, _ = self.make('\n'.join(str(i) for i in range(50)), height=10)
        self.assertTrue(view.following)
        self.assertEqual(view.top, 40)
        view.scroll_rows(-1)
        self.assertFalse(view.following)
        self.assertEqual(view.top, 39)
        view.set_text(view.text + '\nnew')          # new output: the view stays put
        view.layout(20, 10)
        self.assertEqual(view.top, 39)
        self.assertTrue(view.unseen)
        self.assertIn('捲動', view.status())
        self.assertIn('⇣新輸出', view.status())
        view.scroll_rows(1)
        self.assertFalse(view.following)            # one row below: still not at the bottom
        view.scroll_rows(1)
        self.assertTrue(view.following)             # arrived: follows again
        self.assertEqual(view.top, 41)
        self.assertEqual(view.status(), '最新')
        view.page(-1)
        self.assertEqual(view.top, 41 - 9)
        view.to_bottom()
        self.assertTrue(view.following)
        view.to_top()
        self.assertEqual(view.top, 0)
        view.scroll_rows(-5)
        self.assertEqual(view.top, 0)               # clamped

    def test_resize_keeps_the_text_at_the_top_of_the_view(self):
        view, _ = self.make(CJK_LINES, width=80, height=20)
        view.scroll_rows(-30)
        anchor = view.text[view.top_offset:view.top_offset + 8]
        top = view.top
        view.layout(45, 24)                         # narrower and taller: rows re-wrap
        self.assertEqual(view.text[view.top_offset:view.top_offset + 8], anchor)
        self.assertFalse(view.following)
        # QA O3: back and forth never drifts, because the anchor offset is kept across re-wraps.
        for _ in range(3):
            view.layout(80, 20)
            self.assertEqual(view.top, top)
            view.layout(45, 24)
            self.assertEqual(view.text[view.top_offset:view.top_offset + 8], anchor)
        view.scroll_rows(-1)                        # scrolling moves the anchor again
        self.assertNotEqual(view.text[view.top_offset:view.top_offset + 8], anchor)
        view.to_bottom()
        view.layout(120, 10)
        self.assertEqual(view.top, view.max_top())

    def test_selection_offsets_give_exactly_the_selected_text(self):
        view, copies = self.make(CJK_LINES, width=80, height=20)
        view.scroll_rows(-100)
        start = view.offset_at(view.top, 4)
        view.begin_drag(start)
        view.extend_drag(view.offset_at(view.top + 45, 7))        # beyond the visible rows
        self.assertEqual(view.status(), '已選 %d 字' % (view.head - start))
        text = view.end_drag(view.head)
        self.assertEqual(text, view.text[start:view.head])
        self.assertTrue(view.wait_copy())
        self.assertEqual(copies, [text])
        self.assertIn('\n', text)
        self.assertIn('中文長行測試', text)
        self.assertEqual(view.status(), '已複製 %d 字' % len(text))
        # Streaming below keeps the selection; a shrunk text clamps it.
        view.set_text(view.text + '\nstreamed 中文 more')
        self.assertEqual(view.selected_text(), text)
        view.set_text(view.text[:start + 3])
        self.assertEqual(view.selected_text(), text[:3])
        view.set_text(view.text[:start])
        self.assertIsNone(view.selected_range())

    def test_click_clears_and_double_click_selects_a_word(self):
        view, copies = self.make('hello 世界 word\nnext', width=40, height=5)
        view.begin_drag(2)
        self.assertEqual(view.end_drag(2), '')
        self.assertIsNone(view.selected_range())
        view.begin_drag(2)
        self.assertEqual(view.end_drag(2), 'hello')
        view.wait_copy()
        self.assertEqual(copies, ['hello'])
        self.assertEqual(view.selected_range(), (0, 5))
        view.begin_drag(7)
        view.end_drag(7)
        view.begin_drag(7)
        self.assertEqual(view.end_drag(7), '世界')

    def test_copy_failure_is_reported_not_raised(self):
        view = chat_tui_view.ConversationView(get_cwidth, copier=Mock(side_effect=OSError('no pbcopy')))
        view.set_text('abc')
        view.layout(10, 2)
        view.begin_drag(0)
        self.assertEqual(view.end_drag(3), 'abc')
        self.assertTrue(view.wait_copy())
        self.assertTrue(view.copy_failed)
        self.assertIn('複製失敗', view.status())

    def test_copy_runs_off_the_ui_thread_and_refuses_oversized_selections(self):
        import threading
        started, release, seen = threading.Event(), threading.Event(), []

        def slow_copier(text):
            seen.append(threading.current_thread().name)
            started.set()
            release.wait(5)
            return True
        view = chat_tui_view.ConversationView(get_cwidth, copier=slow_copier)
        view.set_text('中文' * 10)
        view.layout(20, 2)
        view.begin_drag(0)
        self.assertEqual(view.end_drag(4), '中文中文')      # returns at once: the copy is pending
        self.assertTrue(started.wait(2))
        self.assertEqual(view.status(), '複製中…')
        self.assertTrue(view.copy_pending())
        release.set()
        self.assertTrue(view.wait_copy())
        self.assertEqual(seen, ['llmtool-clipboard'])
        self.assertEqual(view.status(), '已複製 4 字')
        with patch('llmtool.chat_tui_view.COPY_LIMIT_BYTES', 8):
            calls = []
            view = chat_tui_view.ConversationView(get_cwidth, copier=lambda t: calls.append(t) or True)
            view.set_text('中文' * 10)
            view.layout(20, 2)
            view.begin_drag(0)
            self.assertEqual(view.end_drag(4), '')           # 12 UTF-8 bytes > 8: refused
            self.assertEqual(calls, [])
            self.assertTrue(view.copy_failed)
            self.assertEqual(view.selected_range(), (0, 4))   # still highlighted
            self.assertEqual(view.status(), '選取超過複製上限 0 KB')
            view.begin_drag(0)
            self.assertEqual(view.end_drag(2), '中文')       # 6 bytes: copied
            self.assertTrue(view.wait_copy())
            self.assertEqual(calls, ['中文'])

    def test_double_click_word_goes_through_the_byte_limit_and_a_bounded_scan(self):
        # Codex review v3: select_word sliced the whole word and bypassed copy_range.
        calls = []
        view = chat_tui_view.ConversationView(get_cwidth, copier=lambda t: calls.append(t) or True)
        word = '中' * 400000                                   # 1.2 MB of UTF-8, no blanks
        view.set_text('a ' + word + ' b')
        view.layout(80, 10)
        with patch('llmtool.chat_tui_view.utf8_size', wraps=chat_tui_view.utf8_size) as sized:
            self.assertEqual(view.select_word(2 + 1000), '')   # refused
            sized.assert_called_once()
        self.assertEqual(calls, [])
        self.assertTrue(view.copy_failed)
        self.assertEqual(view.status(), '選取超過複製上限 1024 KB')
        self.assertEqual(view.selected_range(), (2, 2 + len(word)))   # selected, not copied
        with patch('llmtool.chat_tui_view.WORD_SCAN_LIMIT', 5):
            self.assertEqual(view.select_word(2 + 1000), '中' * 10)    # bounded scan: 5 each way
            self.assertTrue(view.wait_copy())
        self.assertEqual(calls, ['中' * 10])

    def test_rapid_copies_are_serialized_and_the_newest_wins(self):
        # Codex review v3: independent workers could run pbcopy concurrently and an older
        # one could overwrite the clipboard or report after a newer copy.
        import threading
        release = threading.Event()
        running, overlap, order = [0], [0], []

        def slow_copier(text):
            running[0] += 1
            overlap[0] = max(overlap[0], running[0])
            order.append(text)
            if text == 'first':
                release.wait(5)
            running[0] -= 1
            return text != 'first'                              # the stale one even fails
        view = chat_tui_view.ConversationView(get_cwidth, copier=slow_copier)
        view.set_text('first second third')
        view.layout(80, 2)
        self.assertTrue(view.copy('first'))
        for _ in range(100):
            if order:
                break
            time.sleep(0.01)
        self.assertTrue(view.copy('second'))                    # queued behind the running one
        self.assertTrue(view.copy('third'))                     # supersedes 'second' in the queue
        self.assertEqual(view.status(), '複製中…')
        release.set()
        self.assertTrue(view.wait_copy())
        self.assertEqual(order, ['first', 'third'])             # serialized, newest wins
        self.assertEqual(overlap[0], 1)                         # never two copier calls at once
        self.assertFalse(view.copy_failed)                      # the stale failure is ignored
        self.assertEqual(view.status(), '已複製 5 字')          # describes 'third'

    def test_streamed_chunks_rewrap_only_the_changed_tail(self):
        # QA D2: with > 4096 lines every chunk re-wrapped everything (476 ms per chunk at
        # 6000 lines). Only the changed line(s) are wrapped now; the result equals a full wrap.
        lines = ['L%05d 中文與 English mixed 長行內容 lorem ipsum dolor sit amet 再加中文確保超過八十欄寬度 abc' % i
                 for i in range(10000)]
        text = '\n'.join(lines)
        view = chat_tui_view.ConversationView(get_cwidth)
        view.set_text(text)
        view.layout(80, 21)
        self.assertEqual(len(view.rows), 20000)
        wrapper = view._wrapper
        started = time.monotonic()
        with patch.object(wrapper, 'line_rows', wraps=wrapper.line_rows) as wrapped:
            for i in range(100):
                text += '片段' if i % 10 else '\nnew line'
                view.set_text(text)
                view.layout(80, 21)
                self.assertLessEqual(wrapped.call_count, 2, 'chunk %d' % i)   # the tail only
                wrapped.reset_mock()
        elapsed = time.monotonic() - started
        self.assertLess(elapsed / 100, 0.05)                      # per chunk, generous for CI
        view.scroll_rows(-5)
        text = text[:-300] + '\nreplaced tail 中文'                   # a streaming block re-render
        view.set_text(text)
        view.layout(80, 21)
        fresh = chat_tui_view.ConversationView(get_cwidth)
        fresh.set_text(text)
        fresh.layout(80, 21)
        self.assertEqual(view.rows, fresh.rows)
        self.assertEqual(view._line_rows, fresh._line_rows)
        view.layout(45, 21)                                       # a width change: full re-wrap
        fresh.layout(45, 21)
        self.assertEqual(view.rows, fresh.rows)

    def test_copy_worker_exit_and_enqueue_are_atomic(self):
        # Codex review v4: a copy arriving after the worker saw an empty queue but before its
        # thread died found the old thread "alive", started no worker, and was never copied.
        import threading
        order = []
        view = chat_tui_view.ConversationView(get_cwidth, copier=lambda t: order.append(t) or True)
        view.set_text('x')
        view.layout(10, 2)

        class StillAlive:            # the old worker thread object, not yet finished
            def is_alive(self):
                return True

            def join(self, timeout=None):
                pass
        view._copy_thread = StillAlive()
        view._copy_next = None
        view._copy_worker()                                   # the worker's last pass
        self.assertIsNone(view._copy_thread)                  # cleared under the lock
        self.assertTrue(view.copy('late'))                    # so this starts a worker
        self.assertTrue(view.wait_copy())
        self.assertEqual(order, ['late'])
        # Stress: requests from two threads are never lost and the last one wins.
        done = []
        view = chat_tui_view.ConversationView(get_cwidth, copier=lambda t: done.append(t) or True)
        view.set_text('x')
        view.layout(10, 2)

        def burst(tag):
            for i in range(200):
                view.copy('%s%03d' % (tag, i))
        threads = [threading.Thread(target=burst, args=(tag,)) for tag in 'ab']
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        last = view._copy_next[1] if view._copy_next else None
        self.assertTrue(view.wait_copy())
        self.assertIsNone(view._copy_next)
        self.assertIsNone(view._copy_thread)
        self.assertTrue(done[-1] in ('a199', 'b199') or done[-1] == last)
        self.assertEqual(view.status(), '已複製 4 字')

    def test_oversized_selection_is_refused_without_measuring_or_building_it_whole(self):
        # Codex review v2: the limit check must not encode (or slice) the whole selection on
        # the UI thread. Measured chunks are bounded and the scan stops at the first excess.
        text = '中文abc' * 400000                              # 2 M chars, ~3.2 MB of UTF-8
        measured = []

        def measure(part):
            measured.append(len(part))
            return len(part.encode('utf-8'))
        size = chat_tui_view.utf8_size(text, 0, len(text), 1024 * 1024, measure=measure)
        self.assertGreater(size, 1024 * 1024)
        self.assertLessEqual(len(measured), 1024 * 1024 // chat_tui_view.COPY_SCAN_CHUNK + 1)
        self.assertTrue(all(n <= chat_tui_view.COPY_SCAN_CHUNK for n in measured))
        self.assertEqual(chat_tui_view.utf8_size('中文abc', 0, 5, 100), 9)   # exact under the limit
        self.assertEqual(chat_tui_view.utf8_size(text, 10, 10, 100), 0)
        calls = []
        view = chat_tui_view.ConversationView(get_cwidth, copier=lambda t: calls.append(t) or True)
        view.set_text(text)
        view.layout(80, 10)
        view.begin_drag(0)
        with patch('llmtool.chat_tui_view.utf8_size', wraps=chat_tui_view.utf8_size) as sized:
            self.assertEqual(view.end_drag(len(text)), '')
            sized.assert_called_once_with(text, 0, len(text), chat_tui_view.COPY_LIMIT_BYTES)
        self.assertEqual(calls, [])
        self.assertIsNone(view._copy_thread)
        self.assertEqual(view.status(), '選取超過複製上限 1024 KB')
        self.assertEqual(view.selected_range(), (0, len(text)))

    def test_wrapping_and_clicks_keep_grapheme_clusters_whole(self):
        family, flag, accented, heart = ('\U0001F468\u200d\U0001F469\u200d\U0001F467',
                                        '\U0001F1F9\U0001F1FC', 'e\u0301', '\u2764\ufe0f')
        line = 'ab' + family + 'cd' + flag + accented + 'f中' + heart + 'g'
        wrapper = chat_tui_view.Wrapper(get_cwidth)
        self.assertEqual([line[s:e] for s, e, _w in wrapper.clusters(line)],
                         ['a', 'b', family, 'c', 'd', flag, accented, 'f', '中', heart, 'g'])
        # Widths are what prompt_toolkit's renderer draws (per code point), so rows never overflow.
        self.assertEqual([w for _s, _e, w in wrapper.clusters(line)],
                         [1, 1, 6, 1, 1, 4, 1, 1, 2, 1, 1])
        for width in range(4, 25):
            rows = [line[s:e] for s, e in wrapper.line_rows(line, width)]
            self.assertEqual(''.join(rows), line)
            for row in rows:
                for cluster in (family, flag, accented, heart):
                    self.assertFalse(0 < sum(1 for c in cluster if c in row) < len(cluster),
                                     'width %d split %r in %r' % (width, cluster, row))
                self.assertFalse(row.endswith('\u200d') or row.startswith('\u0301'))
                self.assertLessEqual(sum(get_cwidth(c) for c in row), max(width, 6))
        view, copies = self.make(line, width=8, height=4)
        self.assertEqual(view.offset_at(0, 2), 2)       # family starts at column 2
        self.assertEqual(view.offset_at(0, 5), 2)       # inside the family: its start
        self.assertEqual(view.offset_at(1, 3), line.index(flag))   # inside the flag
        view.begin_drag(view.offset_at(0, 3))
        self.assertEqual(view.end_drag(view.offset_at(1, 3)), family + 'cd')
        view.wait_copy()
        self.assertEqual(copies, [family + 'cd'])

    def test_clipboard_tool_is_called_with_the_exact_text(self):
        with patch('llmtool.chat_tui_view.shutil.which', return_value='/usr/bin/pbcopy'), \
                patch('llmtool.chat_tui_view.subprocess.run') as run:
            run.return_value = Mock(returncode=0)
            self.assertTrue(chat_tui_view.copy_to_clipboard('中文 text\nline'))
            self.assertEqual(run.call_args.kwargs['input'], '中文 text\nline'.encode('utf-8'))
        with patch('llmtool.chat_tui_view.shutil.which', return_value=None):
            self.assertFalse(chat_tui_view.copy_to_clipboard('x'))

    def test_parse_mouse_sgr_and_x10(self):
        from prompt_toolkit.mouse_events import MouseButton, MouseEventType
        self.assertEqual(chat_tui_view.parse_mouse('\x1b[<64;10;5M'),
                         (MouseEventType.SCROLL_UP, MouseButton.NONE, 9, 4))
        self.assertEqual(chat_tui_view.parse_mouse('\x1b[<32;3;2M'),
                         (MouseEventType.MOUSE_MOVE, MouseButton.LEFT, 2, 1))
        self.assertEqual(chat_tui_view.parse_mouse('\x1b[<0;3;2m'),
                         (MouseEventType.MOUSE_UP, MouseButton.LEFT, 2, 1))
        self.assertEqual(chat_tui_view.parse_mouse('\x1b[M' + chr(32) + chr(33) + chr(34)),
                         (MouseEventType.MOUSE_DOWN, MouseButton.LEFT, 0, 1))
        self.assertIsNone(chat_tui_view.parse_mouse('\x1b[<999;1;1M'))


@unittest.skipUnless(HAVE_TOOLKIT, 'prompt_toolkit not installed')
class ScrollSelectTests(Base):
    """W42 Owner acceptance: wheel, reading position, drag selection and copy, on screen."""

    drive = WidgetTests.drive
    idle, until = staticmethod(WidgetTests.idle), staticmethod(WidgetTests.until)

    def setUp(self):
        super().setUp()
        self.copies = []
        patch('llmtool.chat_tui_view.copy_to_clipboard',
              side_effect=lambda text: self.copies.append(text) or True).start()

    async def settle(self, ui, seconds=0.08):
        ui.app.invalidate()
        await asyncio.sleep(seconds)

    def test_wheel_up_moves_wrapped_cjk_lines_one_row_per_event_and_back(self):
        # Owner simulation 1: 120 wrapped CJK/English lines, 12 wheel-ups left the view
        # stuck (vertical_scroll 121 throughout); short lines moved one line per event.
        seen = {}

        async def fill(ui):
            ui.append(CJK_LINES)
            await self.settle(ui)
            rows = pane_rows(ui)
            self.assertIn('119 中文長行測試', ''.join(rows))   # bottom shown
            seen['bottom'] = rows

        async def wheel_up(ui):
            previous = pane_rows(ui)
            for step in range(1, 13):
                ui.app.key_processor.feed(KeyPress(Keys.Vt100MouseEvent, WHEEL_UP))
                ui.app.key_processor.process_keys()
                await self.settle(ui)
                rows = pane_rows(ui)
                # Exactly one row of movement: the old top row is now the second row.
                self.assertEqual(rows[1], previous[0], 'wheel up #%d' % step)
                self.assertNotEqual(rows[0], previous[0], 'wheel up #%d' % step)
                previous = rows
            seen['up'] = previous
            self.assertNotIn('最新', ui.status_text())
            self.assertIn('捲動', ui.status_text())

        async def wheel_down(ui):
            previous = pane_rows(ui)
            for step in range(1, 13):
                ui.app.key_processor.feed(KeyPress(Keys.Vt100MouseEvent, WHEEL_DOWN))
                ui.app.key_processor.process_keys()
                await self.settle(ui)
                rows = pane_rows(ui)
                self.assertEqual(rows[0], previous[1], 'wheel down #%d' % step)
                previous = rows
            self.assertEqual(previous, seen['bottom'])   # back where it started: following
            self.assertEqual(ui.status_text().split(' | ')[-3], '最新')

        ui, _ = self.drive(['draft', fill, wheel_up, wheel_down,
                            lambda ui: self.assertEqual(ui.input.text, 'draft')],
                           size=[Size(rows=24, columns=80)])
        self.factory.assert_not_called()

    def test_short_lines_scroll_one_row_per_wheel_event_and_keep_position_on_append(self):
        async def fill(ui):
            ui.append(SHORT_LINES)
            await self.settle(ui)
            self.assertTrue(pane_rows(ui)[-1].startswith('119 short line'))

        async def scrolled(ui):
            await self.settle(ui)
            self.assertTrue(pane_rows(ui)[-1].startswith('107 short line'))   # 12 rows up
            ui.append('STREAM NEW OUTPUT')
            await self.settle(ui)
            self.assertTrue(pane_rows(ui)[-1].startswith('107 short line'))   # position kept
            self.assertIn('⇣新輸出', ui.status_text())

        async def back(ui):
            await self.settle(ui)
            self.assertTrue(pane_rows(ui)[-1].startswith('STREAM NEW OUTPUT'))
            self.assertIn('最新', ui.status_text())

        self.drive([fill, WHEEL_UP * 12, scrolled, WHEEL_DOWN * 14, back],
                   size=[Size(rows=24, columns=80)])

    def test_drag_to_the_top_edge_keeps_scrolling_and_extends_the_selection(self):
        # Owner simulation 2: ten drag events at the top edge and a 0.7 s hold never
        # scrolled. Now each edge event and each 50 ms tick scroll one row.
        tops = {}

        async def fill(ui):
            ui.append(SHORT_LINES)
            await self.settle(ui)

        async def dragged(ui):
            await self.settle(ui)
            tops['before'] = pane_rows(ui)[0]
            self.assertGreater(selected_cells(ui), 0)

        async def held(ui):
            await asyncio.sleep(0.4)      # held at the edge without any new mouse event
            await self.settle(ui)
            tops['held'] = pane_rows(ui)[0]
            self.assertLessEqual(int(tops['held'][:3]), int(tops['before'][:3]) - 3)

        def released(ui):
            self.assertTrue(ui.view.wait_copy())
            text = ui.view.selected_text()
            start, end = ui.view.selected_range()
            _line, row_start, _row_end = ui.view.rows[ui.view.row_of(start)]
            top_label = int(ui.view.text[row_start:row_start + 3])
            # Extended to column 4 of the top row reached while held (or further up).
            self.assertLessEqual(top_label, int(tops['held'][:3]))
            self.assertEqual(start - row_start, 4)
            self.assertTrue(text.startswith('short line'))
            _line, anchor_start, _row_end = ui.view.rows[ui.view.row_of(end)]
            anchor_label = int(ui.view.text[anchor_start:anchor_start + 3])   # the press row
            self.assertEqual(len(text.splitlines()), anchor_label - top_label + 1)
            self.assertEqual(self.copies[-1], text)   # copied while dragging, then on release
            self.assertIn('已複製 %d 字' % len(text), ui.status_text())

        self.drive([fill, press(5, 11), drag(5, 10), drag(5, 1), drag(5, 1), drag(5, 1), dragged,
                    held, release(5, 1), released, lambda ui: ui.view.clear_selection()],
                   size=[Size(rows=24, columns=80)])

    def test_drag_whose_release_never_arrives_is_already_on_the_clipboard(self):
        # W52 F3, Warp log 03:56:52Z: drag events, then no Released, then Warp's own Copy
        # (Cmd+C never reaches the app). The moving drag must have copied by itself.
        async def no_release(ui):
            await asyncio.sleep(chat_tui_view.DRAG_COPY_SECONDS + 0.15)
            self.assertTrue(ui.view.wait_copy())
            self.assertTrue(ui.view.dragging)               # still no mouse up
            self.assertEqual(self.copies[-1], ui.view.selected_text())
            self.assertIn('short line\n', self.copies[-1])   # two rows of the pane
            self.assertIn('已複製 %d 字' % len(self.copies[-1]), ui.status_text())

        async def clicked_again(ui):
            await self.settle(ui)
            self.assertIsNone(ui._drag_copy_handle)
            self.assertIsNone(ui.view.selected_range())     # a plain click ends the stale drag

        self.drive([lambda ui: ui.append(SHORT_LINES), press(7, 10), drag(9, 11), drag(12, 12),
                    no_release, press(4, 4), release(4, 4), clicked_again],
                   size=[Size(rows=24, columns=80)])

    def test_click_without_movement_never_copies(self):
        async def check(ui):
            await asyncio.sleep(chat_tui_view.DRAG_COPY_SECONDS + 0.15)
            self.assertEqual(self.copies, [])
            self.assertIsNone(ui._drag_copy_handle)

        self.drive([lambda ui: ui.append(SHORT_LINES), press(7, 10), release(7, 10), check],
                   size=[Size(rows=24, columns=80)])

    def test_drag_below_the_pane_scrolls_down_and_selection_survives_streaming(self):
        # Owner simulation 3: one _block_render() call dropped the selection.
        state = {}

        async def fill(ui):
            ui.append(CJK_LINES)
            await self.settle(ui)
            for _ in range(40):
                ui.app.key_processor.feed(KeyPress(Keys.Vt100MouseEvent, WHEEL_UP))
            ui.app.key_processor.process_keys()
            await self.settle(ui)

        async def selected(ui):
            await self.settle(ui)
            self.assertGreater(selected_cells(ui), 50)   # a first drag selects: no click to focus
            state['text'] = ui.view.selected_text()
            self.assertGreater(len(state['text']), 100)
            state['cells'] = selected_cells(ui)
            state['top'] = pane_rows(ui)[0]
            # A streaming re-render below the selection keeps it and the reading position.
            ui.stream_raw = ''
            ui.busy, ui.worker_kind = True, 'chat'
            ui._stream_attach('stream slice 中文')
            ui._block_render(ui.stream_start, [('stream slice 中文 more', 'text')])
            ui.busy = False
            await self.settle(ui)
            self.assertEqual(ui.view.selected_text(), state['text'])
            self.assertEqual(selected_cells(ui), state['cells'])
            self.assertEqual(pane_rows(ui)[0], state['top'])
            self.assertTrue(ui.conversation.text.endswith('stream slice 中文 more'))

        async def resized(ui):
            await self.settle(ui)
            self.assertEqual(ui.view.selected_text(), state['text'])   # offsets, not cells
            self.assertIn(state['top'][:8], ''.join(pane_rows(ui)[:2]))   # same text on top

        size = [Size(rows=24, columns=80)]

        def resize(ui):
            size[0] = Size(rows=24, columns=45)
            ui.app._on_resize()

        self.drive([fill, press(3, 2), drag(3, 5), drag(3, 20), drag(3, 20), release(3, 20),
                    selected, resize, resized, lambda ui: ui.view.clear_selection()], size=size)

    def test_click_on_the_first_or_last_row_never_scrolls_selects_or_copies(self):
        # QA D1: a plain click on the pane's first/last row scrolled one row, selected and
        # overwrote the clipboard; a double-click there copied the wrong fragment.
        seen = {}

        async def fill(ui):
            ui.append(SHORT_LINES)
            await self.settle(ui)
            for _ in range(20):
                ui.app.key_processor.feed(KeyPress(Keys.Vt100MouseEvent, WHEEL_UP))
            ui.app.key_processor.process_keys()
            await self.settle(ui)
            seen['rows'] = pane_rows(ui)
            self.assertTrue(seen['rows'][0].startswith('0'))

        async def unchanged(ui):
            await self.settle(ui)
            self.assertEqual(pane_rows(ui), seen['rows'])
            self.assertIsNone(ui.view.selected_range())
            self.assertFalse(ui.view.following)
            self.assertEqual(self.copies, [])

        async def word(ui):
            await self.settle(ui)
            self.assertEqual(pane_rows(ui), seen['rows'])            # still no scroll
            self.assertTrue(ui.view.wait_copy())
            self.assertEqual(self.copies, [seen['rows'][0][:3]])      # the label under the click
            ui.view.clear_selection()

        async def to_bottom(ui):
            ui.view.to_bottom()
            await self.settle(ui)
            self.assertTrue(ui.view.following)

        async def still_following(ui):
            await self.settle(ui)
            self.assertTrue(ui.view.following)
            self.assertIsNone(ui.view.selected_range())

        bottom = 21   # pane rows 1..21 at 24 rows (rule, input, status below)
        self.drive([fill, press(5, 1), release(5, 1), unchanged,
                    press(5, bottom), release(5, bottom), unchanged,
                    press(5, 1), drag(5, 1), release(5, 1), unchanged,       # a motion report in place
                    press(2, 1), release(2, 1), press(2, 1), release(2, 1), word,
                    to_bottom, press(5, 1), release(5, 1), still_following],
                   size=[Size(rows=24, columns=80)])

    def test_pageup_pagedown_arrows_and_home_end_scroll_rows(self):
        long_reply = '\n'.join('row %d' % i for i in range(200))
        self.client.complete.return_value = Reply(long_reply, {})

        async def paged_up(ui):
            await self.settle(ui)
            self.assertFalse(ui.view.following)
            page = ui.view.height - 1
            self.assertEqual(ui.view.top, ui.view.max_top() - 2 * page)
            ui.append('new while paged up')
            await self.settle(ui)
            self.assertEqual(ui.view.top, ui.view.max_top() - 2 * page - 2)   # same text on top
            self.assertTrue(ui.app.layout.has_focus(ui.input))

        async def focused_rows(ui):
            await self.settle(ui)
            self.assertTrue(ui.app.layout.has_focus(ui.conversation))
            top = ui.view.top
            ui.app.key_processor.feed(KeyPress(Keys.Up))
            ui.app.key_processor.process_keys()
            self.assertEqual(ui.view.top, top - 1)
            ui.app.key_processor.feed(KeyPress(Keys.Down))
            ui.app.key_processor.process_keys()
            self.assertEqual(ui.view.top, top)
            ui.app.key_processor.feed(KeyPress(Keys.Home))
            ui.app.key_processor.process_keys()
            self.assertEqual(ui.view.top, 0)
            ui.app.key_processor.feed(KeyPress(Keys.End))
            ui.app.key_processor.process_keys()
            self.assertTrue(ui.view.following)

        ui, _ = self.drive(['question\r', self.idle, '\x1b[5~\x1b[5~', paged_up,
                            '\t', focused_rows, '\t', '\x1b[5~', '\x1b[6~' * 60,
                            lambda ui: self.assertTrue(ui.view.following)])
        self.assertTrue(ui.app.layout.has_focus(ui.input))

    def test_f6_hands_the_mouse_to_the_terminal_and_back(self):
        async def mouse_off(ui):
            await self.settle(ui)
            self.assertFalse(ui.mouse_enabled)
            self.assertFalse(ui.app.mouse_support())
            self.assertIn('mouse off; drag to select and copy text with the terminal',
                          ui.conversation.text)
            self.assertIn('F6 滑鼠:關', ui.status_text())
            self.assertLessEqual(get_cwidth(ui.status_text()), 80)

        ui, _ = self.drive(['\x1b[17~', mouse_off, '\x1b[17~',
                            lambda ui: self.assertTrue(ui.mouse_enabled)],
                           size=[Size(rows=24, columns=80)])
        self.assertIn('mouse on; the wheel scrolls the conversation and a drag selects', ui.conversation.text)
        self.assertIn('Drag', chat_tui.KEY_HELP)
        self.assertIn('F6', chat_tui.KEY_HELP)
        # W52 F3: the working gestures per terminal are stated exactly.
        self.assertIn('Warp and Terminal.app: drag, then Cmd+V', chat_tui.KEY_HELP)
        self.assertIn('Cmd+C goes to the terminal, never to llmtool', chat_tui.KEY_HELP)
        self.assertIn('Shift-drag (Warp), Fn-drag (Terminal.app)', chat_tui.KEY_HELP)

    def test_mouse_modes_include_button_event_tracking(self):
        output = DummyOutput()
        output.write_raw = Mock()
        chat_tui._enable_mouse_drag_reporting(output)
        output.enable_mouse_support()
        output.disable_mouse_support()
        self.assertEqual([c.args[0] for c in output.write_raw.call_args_list],
                         ['\x1b[?1002h', '\x1b[?1002l'])

    def test_ctrl_c_with_a_selection_copies_and_clears_instead_of_exiting(self):
        async def selected(ui):
            await self.settle(ui)
            self.assertTrue(ui.view.selected_range())
            self.assertTrue(ui.view.wait_copy())
            self.assertEqual(len(self.copies), 1)

        async def after_ctrl_c(ui):
            await self.settle(ui)
            self.assertTrue(ui.view.wait_copy())
            self.assertIsNone(ui.view.selected_range())
            self.assertIn('已複製 ', ui.status_text())
            self.assertIn('再按 Ctrl-C 中斷', ui.status_text())            # QA O1 hint
            self.assertEqual(self.copies, [self.copies[0], self.copies[0]])
            self.assertFalse(ui.quit_pending)
            self.assertIsNone(ui.app.future.result() if ui.app.future.done() else None)

        ui, result = self.drive([lambda ui: ui.append(SHORT_LINES), press(3, 2), drag(3, 4),
                                 release(3, 4), selected, '\x03', after_ctrl_c])
        self.assertEqual(result, 0)   # the drive's final Ctrl-C (no selection) still exits

    def test_pane_switch_ends_drag_timer_and_selection_so_ctrl_c_interrupts_again(self):
        # Codex review v4: with F2/F3/F4 open, a hidden selection kept capturing Ctrl-C, and a
        # pending edge auto-scroll timer could keep scrolling and extending it.
        async def dragging_at_edge(ui):
            await self.settle(ui)
            self.assertTrue(ui.view.dragging)
            self.assertIsNotNone(ui._autoscroll_handle)
            self.assertTrue(ui.view.selected_range())

        async def events_pane(ui):
            await self.settle(ui)
            self.assertTrue(ui.show_events)
            self.assertFalse(ui.conversation_visible())
            self.assertIsNone(ui._autoscroll_handle)
            self.assertFalse(ui.view.dragging)
            self.assertIsNone(ui.view.selected_range())

        async def selected_then_list(ui):
            await self.settle(ui)
            self.assertTrue(ui.view.selected_range())
            self.assertEqual(self.copies[-1], ui.view.selected_text())
            copies['before'] = len(self.copies)
            ui.toggle_list(True)
            await self.settle(ui)
            self.assertIsNone(ui.view.selected_range())
            self.assertFalse(ui.conversation_visible())

        async def exited(ui):
            await self.settle(ui)
            self.assertFalse(ui.app.is_running)              # Ctrl-C in the list pane exited

        copies = {}
        ui, result = self.drive([lambda ui: ui.append(SHORT_LINES), press(5, 11), drag(5, 10),
                                 drag(5, 1), dragging_at_edge, '\x1b[14~', events_pane,
                                 '\x1b[14~', press(3, 2), drag(3, 4), release(3, 4),
                                 selected_then_list, '\x03', exited])
        self.assertEqual(result, 0)
        self.assertEqual(len(self.copies), copies['before'])  # Ctrl-C in the list copied nothing

    def test_clicks_on_the_pane_keep_input_focus_and_wheel_never_recalls_history(self):
        async def check(ui):
            await self.settle(ui)
            self.assertTrue(ui.app.layout.has_focus(ui.input))
            self.assertEqual(ui.input.text, 'draft')
            self.assertIsNone(ui.view.selected_range())

        self.drive(['first\r', self.idle, 'draft', press(3, 2), release(3, 2),
                    WHEEL_UP * 3, WHEEL_DOWN * 3, check])


class TaskGroupProjectionTests(unittest.TestCase):
    """Pure F3 grouping, filtering and compact PR projection."""

    NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)

    @staticmethod
    def record(task_id, updated, **changes):
        record = {'task_id': task_id * 32, 'request': task_id,
                  'created_at': updated, 'updated_at': updated,
                  'developer': {'executor_task_id': 'd6-task'}}
        record.update(changes)
        return record

    def test_original_and_three_repair_rounds_form_one_row(self):
        from llmtool import chat_tui_status
        records = [self.record(letter, '2026-11-04T%02d:00:00Z' % hour,
                               **({'review_repair_claims': [{'pr': 884}]}
                                  if letter == 'a' else {}))
                   for letter, hour in zip('dcba', (11, 10, 9, 8))]
        groups, hidden = chat_tui_status.group_rows(records, self.NOW, False)
        self.assertEqual(hidden, 0)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['record']['task_id'], 'd' * 32)
        self.assertEqual(groups[0]['rounds'], 3)
        self.assertEqual(groups[0]['pr'], '#884')

    def test_done_and_four_day_idle_groups_are_hidden_until_show_all(self):
        from llmtool import chat_tui_status
        done = self.record('d', '2026-11-04T11:00:00Z', completion={
            'state': 'PM_RETURNED', 'pm_recommendation': {'decision': 'ACCEPT'}})
        old = dict(self.record('o', '2026-10-31T11:00:00Z'),
                   developer={'executor_task_id': 'old-d6'})
        recent = dict(self.record('r', '2026-11-04T10:00:00Z'),
                      developer={'executor_task_id': 'recent-d6'})
        groups, hidden = chat_tui_status.group_rows([done, old, recent], self.NOW, False)
        self.assertEqual(hidden, 2)
        self.assertEqual([group['record']['task_id'] for group in groups], ['r' * 32])
        groups, hidden = chat_tui_status.group_rows([done, old, recent], self.NOW, True)
        self.assertEqual(hidden, 0)
        self.assertEqual(len(groups), 3)

    def test_published_pr_and_conflicting_sources(self):
        from llmtool import chat_tui_status
        published = self.record('p', '2026-11-04T11:00:00Z',
                                published_pr={'pr': 885})
        group, _hidden = chat_tui_status.group_rows([published], self.NOW, True)
        self.assertEqual(group[0]['pr'], '#885')
        conflict = self.record('c', '2026-11-04T11:00:00Z',
                               published_pr={'pr': 885},
                               review_dispatch_claim={'pr': 886})
        group, _hidden = chat_tui_status.group_rows([conflict], self.NOW, True)
        self.assertEqual(group[0]['pr'], 'CONFLICT')
        detail_pr, _url = chat_tui_status.pull_request(published)
        self.assertIn('#885', detail_pr)

    def test_placeholder_rows_without_task_records_remain_individual_rows(self):
        from llmtool import chat_tui_status
        placeholders = [None, {}, {'developer': None}]
        groups, hidden = chat_tui_status.group_rows(placeholders, self.NOW, True)
        self.assertEqual(hidden, 0)
        self.assertEqual([group['record'] for group in groups], placeholders)
        self.assertTrue(all(group['rounds'] == 0 and not group['pr']
                            for group in groups))
        self.assertTrue(all(chat_tui_status.row(record)['task_id'] == 'UNKNOWN'
                            for record in placeholders))

    def test_merged_event_is_terminal(self):
        from llmtool import chat_tui_status
        merged = self.record('m', '2026-11-04T11:00:00Z', completion_events=[{
            'source': 'GITHUB', 'marker_type': 'merge', 'marker_status': 'MERGED'}])
        groups, hidden = chat_tui_status.group_rows([merged], self.NOW, False)
        self.assertEqual(groups, [])
        self.assertEqual(hidden, 1)

    def test_newest_created_round_is_head_and_newest_update_controls_idle(self):
        from llmtool import chat_tui_status
        older_abandoned = self.record(
            'o', '2026-11-04T11:30:00Z', created_at='2026-11-01T08:00:00Z',
            abandonment={'abandoned_at': '2026-11-04T11:00:00Z'},
            developer={'executor_task_id': 'shared-d6', 'state': 'DEVELOPER_RESPONDED'})
        newest_in_progress = self.record(
            'n', '2026-10-31T11:00:00Z', created_at='2026-11-02T08:00:00Z',
            developer={'executor_task_id': 'shared-d6', 'state': 'DEVELOPER_PENDING'})
        groups, hidden = chat_tui_status.group_rows(
            [older_abandoned, newest_in_progress], self.NOW, False)
        self.assertEqual(hidden, 0)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['record']['task_id'], 'n' * 32)
        self.assertEqual(groups[0]['rounds'], 1)


@unittest.skipUnless(HAVE_TOOLKIT, 'prompt-toolkit optional extra not installed')
class TaskGroupListUiTests(Base):
    """F3 keeps grouped selection and actions attached to the newest Task ID."""

    drive = WidgetTests.drive

    @staticmethod
    async def listed(ui):
        for _ in range(200):
            if ui.list_loaded is not None and ui.list_reader is None:
                return
            await asyncio.sleep(0.01)
        raise AssertionError('list read did not finish')

    def test_toggle_refresh_copy_and_enter_keep_group_head_selected(self):
        from llmtool import chat_tui_status
        now = datetime.now(timezone.utc)
        older = {'task_id': 'a' * 32, 'request': 'older round',
                 'created_at': (now - timedelta(days=2)).isoformat(),
                 'updated_at': (now - timedelta(minutes=1)).isoformat(),
                 'developer': {'executor_task_id': 'shared', 'state': 'DEVELOPER_RESPONDED'}}
        newest = {'task_id': 'b' * 32, 'request': 'newest round',
                  'created_at': (now - timedelta(days=1)).isoformat(),
                  'updated_at': (now - timedelta(minutes=2)).isoformat(),
                  'developer': {'executor_task_id': 'shared', 'state': 'DEVELOPER_PENDING'}}
        done = {'task_id': 'c' * 32, 'request': 'done',
                'created_at': now.isoformat(), 'updated_at': now.isoformat(),
                'completion': {'state': 'PM_RETURNED'},
                'developer': {'executor_task_id': 'done-d6', 'state': 'DEVELOPER_RESPONDED'}}
        records = [older, newest, done]

        async def initial(ui):
            await self.listed(ui)
            self.assertEqual(ui.selected_task_id(), 'b' * 32)
            self.assertEqual(len(ui.list_records), 1)
            ui.list_view.copy = Mock()
            ui.start_task = Mock()

        def toggled_on(ui):
            self.assertTrue(ui.list_show_all)
            self.assertEqual(ui.selected_task_id(), 'b' * 32)
            self.assertEqual(len(ui.list_records), 2)

        def toggled_off(ui):
            self.assertFalse(ui.list_show_all)
            self.assertEqual(ui.selected_task_id(), 'b' * 32)

        def refreshed(ui):
            ui.finish_list((records, 0, None))
            self.assertEqual(ui.selected_task_id(), 'b' * 32)

        def copied(ui):
            ui.list_view.copy.assert_called_once_with('b' * 32)

        def opened(ui):
            ui.start_task.assert_called_once_with('show', 'b' * 32, None)

        with patch.object(chat_tui_status, 'load_records', return_value=(records, 0)):
            self.drive(['\x1bOR', initial, 'a', toggled_on, 'a', toggled_off,
                        refreshed, 'c', copied, '\r', opened])


if __name__ == '__main__':
    unittest.main()
