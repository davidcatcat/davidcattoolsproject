"""Local contract tests for the isolated PM runtime driver."""

import io
import json
import os
import queue
import signal
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmtool import pm_runtime
from llmtool.errors import LLMToolError


SESSION = '01234567-89ab-cdef-0123-456789abcdef'


def encoded(*values):
    return [json.dumps(value).encode('utf-8') + b'\n' for value in values]


def _runtime_parts(*pieces):
    return ''.join(pieces)


def _runtime_delta(index, piece):
    return {
        _runtime_parts('type'): _runtime_parts('stream', '_', 'event'),
        _runtime_parts('event'): {
            _runtime_parts('type'): _runtime_parts('content', '_', 'block', '_', 'delta'),
            _runtime_parts('index'): index,
            _runtime_parts('delta'): {
                _runtime_parts('type'): _runtime_parts('text', '_', 'delta'),
                _runtime_parts('text'): piece,
            },
        },
    }


class _Stdin(io.BytesIO):
    """Records what the runtime writes and hands each user message to the fake CLI."""

    def __init__(self, users):
        super().__init__()
        self.users = users

    def write(self, data):
        size = super().write(data)
        line = json.loads(bytes(data))
        if line.get('type') == 'user':
            self.users.put(line)
        return size


def _cli_stdout(lines, users):
    """Claude Code 2.1.312 order: for each user message `queued`, `started`, the turn's
    lines up to its result, then `completed` (lifecycle frames only when it has a uuid)."""
    def frame(message, state):
        return encoded({'type': 'command_lifecycle', 'command_uuid': message['uuid'],
                        'state': state, 'uuid': 'frame-' + state})[0]
    remaining = list(lines)
    while remaining:
        message = users.get()
        if message is None:
            return
        if message.get('uuid'):
            yield frame(message, 'queued')
            yield frame(message, 'started')
        while remaining:
            raw = remaining.pop(0)
            yield raw
            try:
                done = json.loads(bytes(raw)).get('type') == 'result'
            except (TypeError, ValueError, AttributeError):
                done = False
            if done:
                break
        if message.get('uuid'):
            yield frame(message, 'completed')


class FakeProcess:
    def __init__(self, lines=()):
        self.user_queue = queue.Queue()
        self.stdin = _Stdin(self.user_queue)
        self.stdout = _cli_stdout(lines, self.user_queue)
        self.terminated = False
        self.killed = False
        self.wait_calls = 0

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True
        self.user_queue.put(None)

    def kill(self):
        self.killed = True
        self.user_queue.put(None)

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.stuck and self.wait_calls < 3:
            raise TimeoutError()
        return 0

    stuck = False


class LiveProcess(FakeProcess):
    """A child whose stdout lines arrive when the test feeds them, like a real pipe.

    Like Claude Code 2.1.312 it reports `command_lifecycle` frames for a user message
    sent with a uuid; with auto_accept (an idle CLI) it starts the message at once and
    reports `completed` right after that turn's result, unless withhold_completed."""

    def __init__(self, auto_accept=True, withhold_completed=False):
        super().__init__()
        self.feed = queue.Queue()
        self.stdout = iter(self.feed.get, None)
        self.auto_accept = auto_accept
        self.withhold_completed = withhold_completed
        self.running = None   # uuid of the auto-accepted message whose turn is running
        self.stdin = self
        self.lines = []

    def write(self, data):
        line = json.loads(data)
        self.lines.append(line)
        if line.get('type') == 'user' and self.auto_accept:
            self.lifecycle(line['uuid'], 'queued', 'started')
            self.running = line['uuid']
        return len(data)

    def flush(self):
        pass

    def close(self):
        self.feed.put(None)

    def lifecycle(self, message_uuid, *states):
        self.emit(*({'type': 'command_lifecycle', 'command_uuid': message_uuid, 'state': state,
                     'uuid': 'frame-' + state} for state in states))

    def emit(self, *values):
        for value, line in zip(values, encoded(*values)):
            self.feed.put(line)
            if value.get('type') == 'result' and self.running is not None:
                running, self.running = self.running, None
                if not self.withhold_completed:
                    self.lifecycle(running, 'completed')

    def terminate(self):
        super().terminate()
        self.feed.put(None)

    def users(self):
        return [line for line in self.lines if line.get('type') == 'user']


def _bg_turn(text, tokens):
    return ({'type': 'stream_event', 'event': {'type': 'message_start', 'message': {}}},
            _runtime_delta(0, text),
            {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': text}]}},
            {'type': 'result', 'result': text, 'usage': {'output_tokens': tokens}})


class ToolLabelPathTests(unittest.TestCase):
    def test_descriptions_with_paths_are_preserved(self):
        from llmtool import chat_tui_pm
        descriptions = [
            'scp host:/Users/x/k.pem .', 'fetch https://example.com/a/b',
            'export PATH=/a:/b', 'cat ~/notes/x.md',
        ]
        for description in descriptions:
            with self.subTest(description=description):
                self.assertEqual(pm_runtime._tool_description(
                    'Bash', {'description': description}), description)
                self.assertEqual(chat_tui_pm.tool_label({
                    'name': 'Bash', 'description': description}), description)

    def test_structured_path_display(self):
        cases = [
            ('/repo/src/app.py', 'Read src/app.py'),
            ('/Users/alice/private/keys/client.pem', 'Read ~/private/keys/client.pem'),
            ('/var/x.pem', 'Read /var/x.pem'),
            ('~/notes/x.md', 'Read ~/notes/x.md'),
            ('docs/a.md', 'Read docs/a.md'),
            ('/repo', 'Read .'), ('/', 'Read [root]'),
        ]
        with patch.object(pm_runtime.Path, 'home', return_value=Path('/Users/alice')):
            for path, expected in cases:
                with self.subTest(path=path):
                    self.assertEqual(pm_runtime._tool_description('Read', {'path': path}, '/repo'),
                                     expected)

    def test_plain_descriptions_survive(self):
        self.assertEqual(pm_runtime._tool_description('Bash', {'description': 'List  files\n'}),
                         'List files')
        self.assertEqual(pm_runtime._tool_description('Bash', {'description': '列出 PR 狀態'}),
                         '列出 PR 狀態')

    def test_control_characters_are_stripped_and_cannot_crash_parser(self):
        class Runtime:
            cwd = '/repo'
            pm_session_id = None

        record = {'turn_id': 't', 'tool_names': [], 'result_text': ''}
        parser = pm_runtime._TurnParser(Runtime(), record, lambda: False)
        for description, shown in (('\x00URL7\x00', 'URL7'),
                                    ('https://example.com/a/b \x00URL7\x00',
                                     'https://example.com/a/b URL7')):
            line = {'type': 'assistant', 'message': {'content': [
                {'type': 'tool_use', 'id': 'x', 'name': 'Bash',
                 'input': {'description': description}}]}}
            events, done = parser.feed(json.dumps(line).encode())
            self.assertFalse(done)
            self.assertEqual(events[0]['type'], 'TOOL_USE')
            self.assertEqual(events[0]['description'], shown)
        line = {'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'id': 'y', 'name': 'Read', 'input': {'path': 'docs/a.md'}}]}}
        events, done = parser.feed(json.dumps(line).encode())
        self.assertFalse(done)
        self.assertEqual(events[0]['description'], 'Read docs/a.md')

    def test_commands_and_sensitive_text_stay_hidden(self):
        self.assertIsNone(pm_runtime._tool_description('Bash', {
            'command': 'rm -rf x'}))
        secret = 'ghp_' + 'A1b2C3d4E5f6G7h8J9k0' * 2
        label = pm_runtime._tool_description('Bash', {'description': 'push with ' + secret})
        self.assertNotIn(secret, label or '')
        self.assertIn('[REDACTED]', label)


class PMRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.original_signal_handlers = {
            signum: signal.getsignal(signum)
            for signum in (signal.SIGTERM, signal.SIGHUP)
        }
        self.runtimes = set()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'state'
        pm_runtime._ensure_state_dir(self.root)
        self.cwd = self.temp.name
        self.observe_patcher = patch.object(pm_runtime.active_pm_inbox,
                                            'observe_claude_session', return_value=self.cwd)
        self.observe = self.observe_patcher.start()
        self.addCleanup(self.observe_patcher.stop)
        pm_runtime._write_json_atomic(self.root, pm_runtime._binding_path(self.root, SESSION), {
            'runtime': 'claude-code', 'native_session_id': SESSION, 'cwd': self.cwd,
            'dedicated': True, 'created_at': pm_runtime._timestamp(lambda: 1),
            'session_started': False})

    def tearDown(self):
        for runtime in tuple(self.runtimes):
            runtime.close(timeout=0)
        self.assertFalse(pm_runtime._active_runtimes)
        for signum, handler in self.original_signal_handlers.items():
            self.assertIs(signal.getsignal(signum), handler)

    def runtime(self, process=None, **kwargs):
        self.process = process or FakeProcess()
        self.calls = []

        def runner(argv, cwd, env):
            self.calls.append((argv, cwd, env))
            return self.process

        kwargs.setdefault('clock', lambda: 1000)
        kwargs.setdefault('state_root', self.root)
        state_root = kwargs.pop('state_root')
        runtime = pm_runtime.PMRuntime(SESSION, cwd=self.cwd, runner=runner,
                                       state_root=state_root,
                                       environ={'HOME': self.temp.name}, **kwargs)
        self.runtimes.add(runtime)
        self.addCleanup(runtime.close)
        return runtime

    def test_thinking_blocks_are_status_only_and_never_answer_text(self):
        # Owner #304: a reasoning block must not render as the PM answer; the UI gets
        # a THINKING status event and only text blocks become TEXT_DELTA/ASSISTANT_TEXT.
        process = FakeProcess(encoded(
            {'type': 'stream_event', 'event': {'type': 'message_start', 'message': {}}},
            {'type': 'stream_event', 'event': {'type': 'content_block_start', 'index': 0,
             'content_block': {'type': 'thinking', 'thinking': ''}}},
            {'type': 'stream_event', 'event': {'type': 'content_block_delta', 'index': 0,
             'delta': {'type': 'thinking_delta', 'thinking': 'PRIVATE_REASONING no tools needed'}}},
            {'type': 'stream_event', 'event': {'type': 'content_block_stop', 'index': 0}},
            {'type': 'stream_event', 'event': {'type': 'content_block_start', 'index': 1,
             'content_block': {'type': 'text', 'text': ''}}},
            {'type': 'stream_event', 'event': {'type': 'content_block_delta', 'index': 1,
             'delta': {'type': 'text_delta', 'text': '台北101'}}},
            {'type': 'stream_event', 'event': {'type': 'content_block_start', 'index': 2,
             'content_block': {'type': 'redacted_thinking', 'data': 'OPAQUE'}}},
            {'type': 'assistant', 'message': {'content': [
                {'type': 'thinking', 'thinking': 'PRIVATE_REASONING no tools needed', 'signature': 'x'},
                {'type': 'text', 'text': '台北101'}]}},
            {'type': 'result', 'result': '台北101', 'usage': {'input_tokens': 1}}))
        runtime = self.runtime(process)
        runtime.open()
        events = list(runtime.send('說出台北觀光地點'))
        self.assertEqual([event['type'] for event in events],
                         ['THINKING', 'TEXT_DELTA', 'THINKING', 'ASSISTANT_TEXT', 'TURN_END'])
        self.assertEqual([event['text'] for event in events if 'text' in event], ['台北101', '台北101'])
        self.assertNotIn('PRIVATE_REASONING', json.dumps(events, ensure_ascii=False))
        self.assertNotIn('OPAQUE', json.dumps(events, ensure_ascii=False))
        self.assertEqual(events[-1]['result_text'], '台北101')
        history = pm_runtime.read_history(SESSION, state_root=self.root)
        self.assertNotIn('PRIVATE_REASONING', json.dumps(history, ensure_ascii=False))

    def test_argv_cwd_stream_events_and_history(self):
        process = FakeProcess(encoded(
            {'type': 'stream_event', 'event': {'type': 'content_block_delta',
             'delta': {'type': 'text_delta', 'text': 'Hi'}}},
            {'type': 'assistant', 'message': {'content': [
                {'type': 'text', 'text': 'Hello'},
                {'type': 'tool_use', 'id': 'tool-1', 'name': 'Read',
                 'input': {'path': 'docs/a.md', 'content': 'hidden'}}]}},
            {'type': 'user', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': 'tool-1', 'content': 'hidden output'}]}},
            {'type': 'result', 'result': 'Done', 'usage': {'input_tokens': 1}}))
        runtime = self.runtime(process)
        runtime.open()
        self.observe.assert_not_called()
        events = list(runtime.send('Owner request'))
        argv, cwd, _ = self.calls[0]
        self.assertEqual(argv, ['claude', '-p', '--input-format', 'stream-json',
                                '--output-format', 'stream-json', '--verbose',
                                '--include-partial-messages', '--session-id', SESSION,
                                '--permission-prompts', 'none', '--permission-mode', 'auto',
                                '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
                                '--append-system-prompt', pm_runtime.PM_SESSION_INSTRUCTION])
        self.assertEqual(cwd, self.cwd)
        self.assertEqual([event['type'] for event in events],
                         ['TEXT_DELTA', 'ASSISTANT_TEXT', 'TOOL_USE', 'TOOL_RESULT', 'TURN_END'])
        self.assertNotIn('hidden', repr(events))
        self.assertEqual(events[2]['description'], 'Read docs/a.md')   # W57: what it does
        sent = json.loads(process.stdin.getvalue().decode().splitlines()[0])
        self.assertEqual(sent['type'], 'user')
        runtime.close()
        page = pm_runtime.read_history(SESSION, state_root=self.root)
        self.assertEqual(page['turns'][0]['status'], 'COMPLETED')
        self.assertEqual(page['turns'][0]['owner_text'], 'Owner request')

    def test_tool_description_is_the_description_or_a_target_never_the_command(self):
        describe = pm_runtime._tool_description
        self.assertEqual(describe('Bash', {'command': 'rm -rf x', 'description': 'List  files\n'}),
                         'List files')
        self.assertIsNone(describe('Bash', {'command': 'cat ~/.netrc'}))   # no description
        self.assertEqual(describe('Grep', {'pattern': 'def main', 'path': 'src'}), 'Grep def main')
        self.assertEqual(describe('Edit', {'file_path': '/r/a.py', 'old_string': 'x'}),
                         'Edit /r/a.py')
        self.assertIsNone(describe('Read', 'not a dict'))
        self.assertIsNone(describe('Read', {'file_path': '  '}))
        self.assertEqual(len(describe('Bash', {'description': 'x' * 999})),
                         pm_runtime.TOOL_DESCRIPTION_CHARS)
        key = 'gh' + 'p_' + 'A1b2C3d4E5f6G7h8J9k0' * 2
        self.assertNotIn(key, describe('Bash', {'description': 'push with ' + key}) or '')

    def test_tool_descriptions_display_paths_by_location(self):
        describe = pm_runtime._tool_description
        home = str(Path.home())
        self.assertEqual(describe('Read', {'file_path': self.cwd + '/src/app.py'}, self.cwd),
                         'Read src/app.py')
        self.assertEqual(describe('Read', {'file_path': home + '/private/keys/client.pem'},
                                  self.cwd), 'Read ~/private/keys/client.pem')
        self.assertEqual(describe('Read', {'file_path': '/var/private/client.pem'}, self.cwd),
                         'Read /var/private/client.pem')
        self.assertEqual(describe('Bash', {'description':
                                           'Read /var/private/client.pem'}, self.cwd),
                         'Read /var/private/client.pem')

    def test_argv_fixes_tool_set_and_adds_pm_instruction_on_every_launch(self):
        # Owner W40/W41 (TUI latency): claude.ai connectors joined mid-session and invalidated
        # the prompt cache, so the PM process runs with a fixed, empty MCP set (claude CLI
        # 2.1.312 --strict-mcp-config + --mcp-config); #304: the PM answers without a
        # preamble when it uses no tool (--append-system-prompt). Permission prompts stay off.
        runtime = self.runtime(FakeProcess())
        runtime.open()
        runtime.close()
        argv = self.calls[-1][0]
        self.assertIn('--strict-mcp-config', argv)
        self.assertEqual(json.loads(argv[argv.index('--mcp-config') + 1]), {'mcpServers': {}})
        self.assertEqual(argv[argv.index('--mcp-config') + 1], pm_runtime.PM_MCP_CONFIG)
        instruction = argv[argv.index('--append-system-prompt') + 1]
        self.assertEqual(instruction, pm_runtime.PM_SESSION_INSTRUCTION)
        self.assertIn('preamble', instruction)
        self.assertIn('without calling any tool', instruction)
        self.assertEqual(argv[argv.index('--permission-prompts') + 1], 'none')
        self.assertNotIn('--dangerously-skip-permissions', argv)
        # Owner #304 c6494914495 (A): the TUI PM runs in Claude Code auto permission mode.
        self.assertEqual(argv.count('--permission-mode'), 1)
        self.assertEqual(argv[argv.index('--permission-mode') + 1], 'auto')
        self.assertEqual(argv.index('--permission-mode'), argv.index('--permission-prompts') + 2)
        self.assertEqual(argv.count('--strict-mcp-config'), 1)
        self.assertEqual(argv.count('--append-system-prompt'), 1)
        self.assertEqual(argv.index('--permission-prompts'), argv.index(SESSION) + 1)

    def write_user_config(self, text):
        path = Path(self.temp.name) / '.config/llmtool/config.yaml'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def test_configured_claude_account_is_used_for_child_and_transcript(self):
        # Owner 10-27 "claude2 only": the PM runs on the configured account, even
        # when the inherited environment names another one.
        account = os.path.join(self.temp.name, 'claude-account2')
        os.mkdir(account, 0o700)
        self.write_user_config('pm_runtime:\n  claude_config_dir: ' + account + '\n')
        runtime = self.runtime()
        runtime.environ['CLAUDE_CONFIG_DIR'] = os.path.join(self.temp.name, 'default-claude')
        with patch.object(pm_runtime.active_pm_inbox, 'claude_session_present',
                          return_value=False) as present:
            runtime.open()
        self.assertEqual(present.call_args.args[2]['CLAUDE_CONFIG_DIR'], account)
        argv, _, env = self.calls[0]
        self.assertEqual(env['CLAUDE_CONFIG_DIR'], account)
        self.assertEqual(env['HOME'], self.temp.name)
        self.assertEqual(runtime.claude_config_dir, account)
        self.assertEqual(argv[argv.index('--permission-prompts') + 1], 'none')

    def test_pm_role_config_selects_account_for_child(self):
        account = os.path.join(self.temp.name, 'pm-role-account')
        runtime = self.runtime()
        with patch.object(pm_runtime.role_config, 'resolve_role', return_value={
                'config_dir': account, 'source': 'roles'}) as resolve:
            runtime.open()
        resolve.assert_called_once_with('pm', environ=runtime.environ,
                                       home=self.temp.name)
        self.assertEqual(self.calls[0][2]['CLAUDE_CONFIG_DIR'], account)
        self.assertEqual(runtime.claude_config_dir, account)

    def test_pm_role_model_is_passed_and_codex_account_is_refused_before_start(self):
        runtime = self.runtime()
        with patch.object(pm_runtime.role_config, 'resolve_role', return_value={
                'provider': 'claude', 'config_dir': None, 'model': 'claude-model',
                'source': 'roles'}):
            runtime.open()
        self.assertEqual(self.calls[0][0][-2:], ['--model', 'claude-model'])
        runtime.close()

        self.calls.clear()
        runtime = self.runtime()
        with patch.object(pm_runtime.role_config, 'resolve_role', return_value={
                'provider': 'codex', 'config_dir': '/private/codex', 'model': None,
                'source': 'roles'}):
            with self.assertRaises(LLMToolError) as raised:
                runtime.open()
        self.assertEqual(raised.exception.pm_runtime_code, 'PM_CONFIG_INVALID')
        self.assertEqual(self.calls, [])

    def test_unset_claude_account_keeps_inherited_environment(self):
        runtime = self.runtime()
        runtime.open()
        _, _, env = self.calls[0]
        expected_env = {
            'HOME': self.temp.name,
            'GH_CONFIG_DIR': os.path.join(self.temp.name, '.config', 'gh-ai'),
            'GIT_CONFIG_COUNT': '3',
            'GIT_CONFIG_KEY_0': 'credential.helper',
            'GIT_CONFIG_VALUE_0': '',
            'GIT_CONFIG_KEY_1': 'credential.helper',
            'GIT_CONFIG_VALUE_1': '!gh auth git-credential',
            'GIT_CONFIG_KEY_2': 'url.https://github.com/.insteadOf',
            'GIT_CONFIG_VALUE_2': 'git@github.com:',
        }
        self.assertEqual(env, expected_env)
        self.assertEqual(runtime.claude_config_dir, os.path.join(self.temp.name, '.claude'))

    def test_invalid_claude_account_config_starts_nothing(self):
        for text in ('pm_runtime:\n  claude_config_dir: relative/claude\n',
                     'pm_runtime:\n  claude_config_dir: 5\n',
                     'pm_runtime:\n  other: x\n'):
            with self.subTest(text=text):
                self.write_user_config(text)
                runtime = self.runtime()
                with self.assertRaises(LLMToolError) as raised:
                    runtime.open()
                self.assertEqual(raised.exception.pm_runtime_code, 'PM_CONFIG_INVALID')
                self.assertEqual(self.calls, [])

    def test_busy_lock_does_not_start_second_process(self):
        first, second = self.runtime(), self.runtime()
        first.open()
        with self.assertRaises(LLMToolError) as raised:
            second.open()
        self.assertEqual(raised.exception.pm_runtime_code, 'PM_SESSION_BUSY')
        self.assertEqual(len(self.calls), 1)
        first.close()

    def test_fork_guard_releases_driver(self):
        process = FakeProcess(encoded({'type': 'system', 'subtype': 'init',
                                       'session_id': 'fedcba98-7654-3210-fedc-ba9876543210'}))
        runtime = self.runtime(process)
        runtime.open()
        with self.assertRaises(LLMToolError) as raised:
            list(runtime.send('hello'))
        self.assertEqual(raised.exception.pm_runtime_code, 'PM_SESSION_FORKED')
        self.assertTrue(process.terminated)
        self.assertGreater(process.wait_calls, 0)
        self.assertIsNone(runtime._child)
        self.assertIsNone(runtime._lock_fd)
        self.assertNotIn(runtime, pm_runtime._active_runtimes)
        for signum, handler in self.original_signal_handlers.items():
            self.assertIs(signal.getsignal(signum), handler)
        other = self.runtime()
        other.open()
        other.close()

    def test_invalid_or_sensitive_input_is_never_written(self):
        process = FakeProcess()
        runtime = self.runtime(process)
        runtime.open()
        for message in ('', 'bad\x00text', 'bad\u200dtext'):
            with self.assertRaises(LLMToolError) as raised:
                runtime.send(message)
            self.assertEqual(raised.exception.pm_runtime_code, 'PM_MESSAGE_INVALID')
        with patch.object(pm_runtime, '_emitted_sensitive', return_value=True):
            with self.assertRaises(LLMToolError) as raised:
                runtime.send('flagged text')
        self.assertEqual(raised.exception.pm_runtime_code, 'PM_MESSAGE_INVALID')
        self.assertEqual(process.stdin.getvalue(), b'')
        runtime.close()

    def test_interrupt_writes_control_and_marks_turn_interrupted(self):
        process = FakeProcess(encoded({'type': 'result', 'subtype': 'error', 'result': ''}))
        runtime = self.runtime(process)
        runtime.open()
        events = runtime.send('please stop')
        runtime.interrupt()
        result = list(events)
        control = json.loads(process.stdin.getvalue().decode().splitlines()[1])
        self.assertEqual(control['type'], 'control_request')
        self.assertEqual(control['request']['subtype'], 'interrupt')
        self.assertEqual(len(control['request_id']), 32)
        self.assertEqual(result[-1]['status'], 'INTERRUPTED')
        runtime.close()

    def test_invalid_line_and_early_exit_emit_runtime_error(self):
        # hd665: after the write, an invalid line or the child's exit leaves the message's
        # fate unconfirmed: the error is still surfaced, and the turn ends UNKNOWN (held).
        for lines, code in (([b'not-json\n'], 'PM_STREAM_INVALID'), ([], 'PM_PROCESS_EXITED')):
            runtime = self.runtime(FakeProcess(lines))
            runtime.open()
            events = list(runtime.send('request'))
            self.assertEqual(events[0]['type'], 'RUNTIME_ERROR')
            self.assertEqual(events[0]['code'], code)
            self.assertTrue(events[0]['turn_id'])
            self.assertEqual((events[-1]['status'], events[-1]['delivery']),
                             ('UNKNOWN', 'UNKNOWN'))
            runtime.close()

    def test_close_escalates_to_kill(self):
        process = FakeProcess()
        process.stuck = True
        runtime = self.runtime(process)
        runtime.open()
        runtime.close(timeout=0)
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)

    def test_history_paging_and_codex_adapter(self):
        runtime = self.runtime()
        runtime.open()
        list(runtime.send('one'))
        runtime.close()
        runtime = self.runtime()
        runtime.open()
        list(runtime.send('two'))
        runtime.close()
        first = pm_runtime.read_history(SESSION, after=0, limit=1, state_root=self.root)
        second = pm_runtime.read_history(SESSION, after=first['next_after'],
                                         state_root=self.root)
        self.assertEqual([turn['seq'] for turn in first['turns'] + second['turns']], [1, 2])
        latest = pm_runtime.read_history(SESSION, latest=1, state_root=self.root)
        self.assertEqual([turn['seq'] for turn in latest['turns']], [2])
        codex_account = Path(self.temp.name) / 'codex-account'
        codex_account.mkdir(mode=0o700)
        self.write_user_config('accounts:\n  codex-pm:\n    kind: codex\n    config_dir: ' +
                               str(codex_account) + '\n')
        bin_dir = Path(self.temp.name) / 'codex-bin'
        bin_dir.mkdir()
        fake = bin_dir / 'codex'
        fake.write_text(
            '#!' + sys.executable + '\n'
            'import json\n'
            "print(json.dumps({'type':'thread.started',"
            "'thread_id':'abcdefab-cdef-abcd-efab-cdefabcdefab'}), flush=True)\n"
            "print(json.dumps({'type':'item.completed','item':{"
            "'type':'agent_message','text':'Codex answer'}}), flush=True)\n"
            "print(json.dumps({'type':'turn.completed','usage':{}}), flush=True)\n",
            encoding='utf-8')
        fake.chmod(0o700)
        binding = pm_runtime.create_dedicated_pm(
            self.cwd, runtime='codex', account='codex-pm', state_root=self.root,
            environ={'HOME': self.temp.name}, home=self.temp.name)
        runtime = self.runtime(state_root=self.root)
        runtime.pm_session_id = binding['native_session_id']
        with patch.dict(os.environ, {'PATH': str(bin_dir)}):
            runtime.open()
            events = list(runtime.send('request'))
        self.assertEqual([event['type'] for event in events],
                         ['TEXT_DELTA', 'ASSISTANT_TEXT', 'TURN_END'])
        self.assertEqual(events[-1]['status'], 'COMPLETED')
        self.assertIsNotNone(runtime._codex_driver)

    def test_create_dedicated_binding_starts_no_process(self):
        binding = pm_runtime.create_dedicated_pm(self.cwd, state_root=self.root,
                                                 clock=lambda: 1000)
        self.assertFalse(binding['session_started'])
        self.assertEqual(pm_runtime.current_binding(state_root=self.root), binding)

    def test_binding_listing_selection_and_codex_runtime(self):
        binding_root = Path(self.temp.name) / 'listing-state'
        first = pm_runtime.create_dedicated_pm(self.cwd, state_root=binding_root,
                                               clock=lambda: 1000)
        codex_account = Path(self.temp.name) / 'codex-account'
        codex_account.mkdir(mode=0o700)
        self.write_user_config('accounts:\n  codex-pm:\n    kind: codex\n    config_dir: ' +
                               str(codex_account) + '\n')
        second = pm_runtime.create_dedicated_pm(self.cwd, runtime='codex', account='codex-pm',
                                                state_root=binding_root, clock=lambda: 1001,
                                                environ={'HOME': self.temp.name},
                                                home=self.temp.name)
        self.assertEqual([item['native_session_id'] for item in pm_runtime.list_bindings(
            state_root=binding_root)], [second['native_session_id'], first['native_session_id']])
        selected = pm_runtime.select_binding(first['native_session_id'], state_root=binding_root)
        self.assertEqual(selected['native_session_id'], first['native_session_id'])
        self.assertEqual(pm_runtime.current_binding(state_root=binding_root)['native_session_id'],
                         first['native_session_id'])
        bin_dir = Path(self.temp.name) / 'codex-bin'
        bin_dir.mkdir()
        fake = bin_dir / 'codex'
        fake.write_text(
            '#!' + sys.executable + '\n'
            'import json\n'
            "print(json.dumps({'type':'thread.started',"
            "'thread_id':'abcdefab-cdef-abcd-efab-cdefabcdefab'}), flush=True)\n"
            "print(json.dumps({'type':'item.completed','item':{"
            "'type':'agent_message','text':'Codex answer'}}), flush=True)\n"
            "print(json.dumps({'type':'turn.completed','usage':{}}), flush=True)\n",
            encoding='utf-8')
        fake.chmod(0o700)
        runtime = self.runtime(runtime='claude-code', state_root=binding_root)
        runtime.pm_session_id = second['native_session_id']
        with patch.dict(os.environ, {'PATH': str(bin_dir)}):
            runtime.open()
            events = list(runtime.send('request'))
        self.assertEqual([event['type'] for event in events],
                         ['TEXT_DELTA', 'ASSISTANT_TEXT', 'TURN_END'])
        self.assertEqual(events[-1]['status'], 'COMPLETED')
        self.assertIsNotNone(runtime._codex_driver)

    def test_binding_listing_and_selection_refuse_symlinks_and_malformed_files(self):
        binding = pm_runtime.create_dedicated_pm(self.cwd, state_root=self.root)
        symlink_id = '11234567-89ab-cdef-0123-456789abcdef'
        (self.root / (symlink_id + '.binding.json')).symlink_to(
            self.root / (binding['native_session_id'] + '.binding.json'))
        with self.assertRaises(LLMToolError):
            pm_runtime.list_bindings(state_root=self.root)
        with self.assertRaises(LLMToolError):
            pm_runtime.select_binding(symlink_id, state_root=self.root)
        (self.root / (symlink_id + '.binding.json')).unlink()
        malformed_id = '21234567-89ab-cdef-0123-456789abcdef'
        (self.root / (malformed_id + '.binding.json')).write_text('{', encoding='utf-8')
        with self.assertRaises(LLMToolError):
            pm_runtime.list_bindings(state_root=self.root)

    def test_binding_account_mismatch_and_one_shot_override(self):
        account_a = Path(self.temp.name) / 'account-a'
        account_b = Path(self.temp.name) / 'account-b'
        account_a.mkdir(mode=0o700)
        account_b.mkdir(mode=0o700)
        config = ('accounts:\n  A:\n    kind: claude\n    config_dir: ' + str(account_a) +
                  '\n  B:\n    kind: claude\n    config_dir: ' + str(account_b) +
                  '\nroles:\n  pm:\n    account: B\n')
        self.write_user_config(config)
        binding = pm_runtime.current_binding(SESSION, state_root=self.root)
        binding['account'] = 'A'
        pm_runtime._write_json_atomic(self.root, pm_runtime._binding_path(self.root, SESSION),
                                      binding)
        config_path = Path(self.temp.name) / '.config/llmtool/config.yaml'
        original_config = config_path.read_bytes()
        runtime = self.runtime()
        with self.assertRaises(LLMToolError) as raised:
            runtime.open()
        self.assertEqual(raised.exception.pm_runtime_code, 'PM_ACCOUNT_MISMATCH')
        self.assertEqual(self.calls, [])
        runtime = self.runtime(account='A')
        with patch.object(pm_runtime.active_pm_inbox, 'observe_claude_session',
                          return_value=self.cwd):
            runtime.open()
        self.assertEqual(self.calls[0][2]['CLAUDE_CONFIG_DIR'], str(account_a))
        self.assertEqual(config_path.read_bytes(), original_config)

    def test_completed_first_turn_marks_binding_for_resume_on_next_open(self):
        process = FakeProcess(encoded({'type': 'result', 'result': 'Done'}))
        runtime = self.runtime(process, clock=time.time)
        runtime.open()
        list(runtime.send('first request'))
        runtime.close()
        binding = pm_runtime.current_binding(SESSION, state_root=self.root)
        self.assertTrue(binding['session_started'])
        transcript = pm_runtime.active_pm_inbox.claude_session_file(
            SESSION, self.cwd, {'HOME': self.temp.name})
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text('{}', encoding='utf-8')
        self.observe_patcher.stop()

        later = self.runtime(FakeProcess(), clock=time.time)
        later.open()
        argv = self.calls[-1][0]
        self.assertIn('--resume', argv)
        self.assertNotIn('--session-id', argv)
        later.close()

    def test_completed_turn_does_not_persist_legacy_account_override(self):
        account = os.path.join(self.temp.name, 'legacy-account')
        os.mkdir(account, 0o700)
        self.write_user_config('accounts:\n  A:\n    kind: claude\n    config_dir: ' +
                               account + '\n')
        binding_path = pm_runtime._binding_path(self.root, SESSION)
        binding = pm_runtime.current_binding(SESSION, state_root=self.root,
                                             resolve_legacy_account=False)
        binding.pop('account', None)
        pm_runtime._write_json_atomic(self.root, binding_path, binding)
        process = FakeProcess(encoded({'type': 'result', 'result': 'Done'}))
        runtime = self.runtime(process, clock=time.time)
        runtime.environ['LLMTOOL_ROLE_PM_ACCOUNT'] = 'A'
        runtime.open()
        list(runtime.send('first request'))
        runtime.close()
        persisted = pm_runtime.current_binding(SESSION, state_root=self.root,
                                               resolve_legacy_account=False)
        self.assertNotIn('account', persisted)
        self.assertTrue(persisted['session_started'])

    def test_create_then_connect_does_not_treat_creator_as_live_session(self):
        binding = pm_runtime.create_dedicated_pm(self.cwd, state_root=self.root)
        process = FakeProcess()
        calls = []

        def runner(argv, cwd, env):
            calls.append(argv)
            return process

        with patch.object(pm_runtime.subprocess, 'check_output', return_value=b''):
            runtime = pm_runtime.PMRuntime(
                binding['native_session_id'], cwd=self.cwd, runner=runner,
                state_root=self.root, environ={'HOME': self.temp.name}, clock=time.time)
            self.runtimes.add(runtime)
            runtime.open()
        self.assertFalse(binding['session_started'])
        self.assertEqual(len(calls), 1)
        self.assertIn('--session-id', calls[0])
        self.assertNotIn('--resume', calls[0])
        runtime.close()

    def test_completed_turn_binding_precedes_history_persist_for_crash_recovery(self):
        process = FakeProcess(encoded({'type': 'result', 'result': 'Done'}))
        runtime = self.runtime(process)
        runtime.open()
        with patch.object(runtime, '_persist_turn', side_effect=OSError('simulated crash')):
            with self.assertRaises(OSError):
                list(runtime.send('first request'))
        runtime.close()
        binding = pm_runtime.current_binding(SESSION, state_root=self.root)
        self.assertTrue(binding['session_started'])

        later = self.runtime(FakeProcess())
        later.open()
        argv = self.calls[-1][0]
        self.assertIn('--resume', argv)
        self.assertNotIn('--session-id', argv)
        later.close()

    def test_create_binding_write_failure_starts_no_child(self):
        write_json_atomic = pm_runtime._write_json_atomic
        state_root = self.root / 'write-failure'
        pm_runtime._ensure_state_dir(state_root)
        def fail_write(root, path, value):
            if path.name.endswith('.binding.json'):
                raise pm_runtime._error(
                    'PM_BINDING_WRITE_FAILED', 'synthetic binding write failure')
            return write_json_atomic(root, path, value)

        with patch.object(pm_runtime, '_write_json_atomic', side_effect=fail_write):
            with self.assertRaises(LLMToolError) as raised:
                pm_runtime.create_dedicated_pm(self.cwd, state_root=state_root)
        self.assertEqual(raised.exception.pm_runtime_code, 'PM_BINDING_WRITE_FAILED')

    def test_open_refuses_non_dedicated_session(self):
        (self.root / (SESSION + '.binding.json')).unlink()
        self.observe_patcher.stop()
        with self.assertRaises(LLMToolError) as raised:
            self.runtime().open()
        self.assertEqual(raised.exception.pm_runtime_code, 'PM_SESSION_INVALID')

    def test_open_failure_after_lifecycle_registration_unregisters(self):
        register = pm_runtime._register_lifecycle

        def register_then_fail(runtime):
            register(runtime)
            raise LLMToolError('synthetic lifecycle registration failure')

        process = FakeProcess()
        runtime = self.runtime(process)
        with patch.object(pm_runtime, '_register_lifecycle', side_effect=register_then_fail):
            with self.assertRaises(LLMToolError):
                runtime.open()
        self.assertFalse(pm_runtime._active_runtimes)
        for signum, handler in self.original_signal_handlers.items():
            self.assertIs(signal.getsignal(signum), handler)
        self.assertTrue(process.terminated)

    def test_open_refuses_external_live_session_process(self):
        with patch.object(pm_runtime.subprocess, 'check_output',
                          return_value=f'123 claude --resume {SESSION}'.encode()):
            with self.assertRaises(LLMToolError) as raised:
                self.runtime().open()
        self.assertEqual(raised.exception.pm_runtime_code, 'PM_SESSION_LIVE_ELSEWHERE')

    def test_ps_match_requires_claude_executable_and_session_argument(self):
        for listing in (f'123 python worker-claude {SESSION}'.encode(),
                        f'123 /tmp/not-claude {SESSION}'.encode(),
                        f'123 claude --resume other-{SESSION}'.encode()):
            with self.subTest(listing=listing), patch.object(
                    pm_runtime.subprocess, 'check_output', return_value=listing):
                runtime = self.runtime()
                runtime.open()
                runtime.close()

    def test_close_then_reopen_ignores_own_recent_transcript(self):
        runtime = self.runtime(clock=time.time)
        runtime.open()
        runtime.close()
        transcript = pm_runtime.active_pm_inbox.claude_session_file(
            SESSION, self.cwd, {'HOME': self.temp.name})
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text('{}', encoding='utf-8')
        now = time.time()
        os.utime(transcript, (now, now))
        with patch.object(pm_runtime.subprocess, 'check_output', return_value=b''):
            runtime.open()
        runtime.close()

    def test_clean_close_allows_new_driver_within_live_window(self):
        runtime = self.runtime(clock=time.time)
        runtime.open()
        runtime.close()
        transcript = pm_runtime.active_pm_inbox.claude_session_file(
            SESSION, self.cwd, {'HOME': self.temp.name})
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text('{}', encoding='utf-8')
        now = time.time()
        os.utime(transcript, (now, now))
        with patch.object(pm_runtime.subprocess, 'check_output', return_value=b''):
            new_runtime = self.runtime(clock=time.time)
            new_runtime.open()
        new_runtime.close()

    def test_crash_left_active_turn_is_interrupted_before_idempotency_replay(self):
        runtime = self.runtime()
        runtime.open()
        list(runtime.send('request', idempotency_key='crash-key'))
        runtime.close()
        history_path = pm_runtime._history_path(runtime._root, runtime._key)
        history = json.loads(history_path.read_text(encoding='utf-8'))
        history['turns'][0]['status'] = 'ACTIVE'
        history['turns'][0]['ended_at'] = None
        history_path.write_text(json.dumps(history), encoding='utf-8')

        recovered = self.runtime()
        recovered.open()
        page = pm_runtime.read_history(SESSION, state_root=self.root)
        self.assertEqual(page['turns'][0]['status'], 'INTERRUPTED')
        replay = list(recovered.send('request', idempotency_key='crash-key'))
        self.assertEqual(replay[0]['type'], 'TURN_REPLAY')
        self.assertEqual(replay[0]['status'], 'INTERRUPTED')
        recovered.close()

    def test_signal_handler_none_restores_default_and_all_runtimes_close(self):
        signum = signal.SIGTERM
        failing = type('StubRuntime', (), {})()
        succeeding = type('StubRuntime', (), {})()
        pm_runtime._active_runtimes.update((failing, succeeding))
        close_calls = []

        def close_failing():
            close_calls.append('failing')
            raise RuntimeError('first close failed')

        def close_succeeding():
            close_calls.append('succeeding')

        failing.close = close_failing
        succeeding.close = close_succeeding
        pm_runtime._previous_signal_handlers[signum] = None
        pm_runtime._previous_signal_handlers[signal.SIGHUP] = None
        try:
            with patch.object(pm_runtime.signal, 'getsignal', return_value=pm_runtime._terminate_all), \
                    patch.object(pm_runtime.signal, 'signal') as set_signal:
                pm_runtime._restore_signal_handlers()
                self.assertCountEqual(set_signal.call_args_list, [
                    call(signal.SIGTERM, signal.SIG_DFL),
                    call(signal.SIGHUP, signal.SIG_DFL)])
            pm_runtime._previous_signal_handlers[signum] = None
            with patch.object(pm_runtime.os, 'kill') as kill:
                with self.assertRaisesRegex(RuntimeError, 'first close failed'):
                    pm_runtime._terminate_all(signum, None)
            kill.assert_called_once_with(pm_runtime.os.getpid(), signum)
            self.assertCountEqual(close_calls, ['failing', 'succeeding'])
        finally:
            pm_runtime._active_runtimes.discard(failing)
            pm_runtime._active_runtimes.discard(succeeding)
            pm_runtime._previous_signal_handlers.pop(signum, None)
            pm_runtime._previous_signal_handlers.pop(signal.SIGHUP, None)
            pm_runtime._signal_handlers_installed = False

    def test_runtime_signal_handler_chains_after_all_close_attempts(self):
        signum = signal.SIGHUP
        failing = type('StubRuntime', (), {})()
        succeeding = type('StubRuntime', (), {})()
        pm_runtime._active_runtimes.update((failing, succeeding))
        close_calls = []
        received = []

        def close_failing():
            close_calls.append('failing')
            raise RuntimeError('first close failed')

        def close_succeeding():
            close_calls.append('succeeding')

        failing.close = close_failing
        succeeding.close = close_succeeding
        pm_runtime._previous_signal_handlers[signum] = lambda received_signum, frame: received.append(received_signum)
        try:
            with self.assertRaisesRegex(RuntimeError, 'first close failed'):
                pm_runtime._terminate_all(signum, None)
            self.assertCountEqual(close_calls, ['failing', 'succeeding'])
            self.assertEqual(received, [signum])
        finally:
            pm_runtime._active_runtimes.discard(failing)
            pm_runtime._active_runtimes.discard(succeeding)
            pm_runtime._previous_signal_handlers.pop(signum, None)

    def test_runtime_signal_handlers_restore_and_chain_previous_handler(self):
        signum = signal.SIGTERM
        previous = signal.getsignal(signum)
        received = []

        def handler(received_signum, frame):
            received.append(received_signum)

        try:
            signal.signal(signum, handler)
            self.assertIs(signal.getsignal(signum), handler)
            runtime = self.runtime()
            runtime.open()
            self.assertIs(signal.getsignal(signum), pm_runtime._terminate_all)
            runtime.close()
            self.assertIs(signal.getsignal(signum), handler)
            os.kill(os.getpid(), signum)
            self.assertEqual(received, [signum])
        finally:
            signal.signal(signum, previous)

    def test_open_refuses_fresh_transcript_without_runtime_lock(self):
        transcript = pm_runtime.active_pm_inbox.claude_session_file(
            SESSION, self.cwd, {'HOME': self.temp.name})
        transcript.parent.mkdir(parents=True)
        transcript.write_text('{}', encoding='utf-8')
        now = time.time()
        os.utime(transcript, (now, now))
        with patch.object(pm_runtime.subprocess, 'check_output', return_value=b''):
            runtime = self.runtime(clock=lambda: now)
            with self.assertRaises(LLMToolError) as raised:
                runtime.open()
        self.assertEqual(raised.exception.pm_runtime_code, 'PM_SESSION_LIVE_ELSEWHERE')

    def _fresh_transcript_path(self):
        self.observe_patcher.stop()
        return pm_runtime.active_pm_inbox.claude_session_file(
            SESSION, self.cwd, {'HOME': self.temp.name})

    def test_first_open_refuses_symlinked_transcript_parent_before_launch(self):
        transcript = self._fresh_transcript_path()
        elsewhere = Path(self.temp.name) / 'elsewhere'
        elsewhere.mkdir()
        transcript.parent.parent.mkdir(parents=True)
        transcript.parent.symlink_to(elsewhere, target_is_directory=True)
        self.assertFalse(transcript.exists())
        runtime = self.runtime()
        with self.assertRaises(LLMToolError) as raised:
            runtime.open()
        self.assertEqual(raised.exception.pm_runtime_code, 'PM_SESSION_INVALID')
        self.assertEqual(self.calls, [])

    def test_first_open_refuses_dangling_transcript_symlink_before_launch(self):
        transcript = self._fresh_transcript_path()
        transcript.parent.mkdir(parents=True)
        transcript.symlink_to(Path(self.temp.name) / 'missing.jsonl')
        self.assertFalse(transcript.exists())
        runtime = self.runtime()
        with self.assertRaises(LLMToolError) as raised:
            runtime.open()
        self.assertEqual(raised.exception.pm_runtime_code, 'PM_SESSION_INVALID')
        self.assertEqual(self.calls, [])

    def test_first_open_without_transcript_in_real_directory_uses_session_id(self):
        transcript = self._fresh_transcript_path()
        transcript.parent.mkdir(parents=True)
        self.assertFalse(transcript.exists())
        runtime = self.runtime()
        runtime.open()
        argv = self.calls[0][0]
        self.assertIn('--session-id', argv)
        self.assertNotIn('--resume', argv)
        runtime.close()

    def test_stream_metadata_and_text_rebuild_without_duplication(self):
        process = FakeProcess(encoded(
            {'type': 'stream_event', 'event': {'type': 'content_block_delta', 'index': 3,
             'delta': {'type': 'text_delta', 'text': 'Hel'}}},
            {'type': 'stream_event', 'event': {'type': 'content_block_delta', 'index': 3,
             'delta': {'type': 'text_delta', 'text': 'lo'}}},
            {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'Hello'}]}},
            {'type': 'result', 'result': 'Hello'}))
        runtime = self.runtime(process)
        runtime.open()
        events = list(runtime.send('request'))
        self.assertTrue(all(e['turn_id'] and type(e['seq']) is int for e in events))
        self.assertEqual([e['seq'] for e in events], list(range(len(events))))
        deltas = ''.join(e['text'] for e in events if e['type'] == 'TEXT_DELTA')
        final = next(e for e in events if e['type'] == 'ASSISTANT_TEXT')
        rebuilt = final['text'] if final.get('final') else deltas + final['text']
        self.assertEqual(rebuilt, 'Hello')
        self.assertEqual([e['block_index'] for e in events if e['type'] == 'TEXT_DELTA'], [3, 3])
        runtime.close()

    def test_withheld_block_stays_suppressed_after_later_deltas(self):
        field = _runtime_parts('type')
        value = _runtime_parts('stream', '_', 'event')
        event_field = _runtime_parts('event')
        event_kind = _runtime_parts('type')
        delta_kind = _runtime_parts('content', '_', 'block', '_', 'delta')
        block_field = _runtime_parts('index')
        delta_field = _runtime_parts('delta')
        text_kind = _runtime_parts('text', '_', 'delta')
        assistant_text_kind = _runtime_parts('text')
        text_field = _runtime_parts('text')
        message_field = _runtime_parts('message')
        content_field = _runtime_parts('content')
        block_type = _runtime_parts('type')
        final_kind = _runtime_parts('assistant')
        result_kind = _runtime_parts('result')
        phrase = _runtime_parts('pass', 'word')
        marker = _runtime_parts(':', ' ')
        value_piece = _runtime_parts('private', '-', 'fragment')
        withheld_events = [
            {field: value, event_field: {event_kind: delta_kind, block_field: 6,
             delta_field: {event_kind: text_kind,
                           text_field: _runtime_parts('Notice ', phrase, ':')}}},
            _runtime_delta(6, marker + value_piece),
            {field: final_kind, message_field: {content_field: [{block_type: assistant_text_kind,
             block_field: 6, text_field: _runtime_parts('Notice ', phrase, marker,
                                                        value_piece)}]}},
            {field: result_kind, _runtime_parts('result'): _runtime_parts('complete')},
        ]
        runtime = self.runtime(FakeProcess(encoded(*withheld_events)))
        runtime.open()
        events = list(runtime.send(_runtime_parts('request')))
        model_text = [event['text'] for event in events
                      if event['type'] in ('TEXT_DELTA', 'ASSISTANT_TEXT')]
        self.assertNotIn(value_piece, repr(model_text))
        self.assertNotIn(_runtime_parts('[', 'withheld', ']'), repr(model_text))
        self.assertEqual(model_text, [])
        runtime.close()

    def test_stream_url_screening_checks_long_tail_quickly(self):
        adversarial = (_runtime_parts('https://') + _runtime_parts('a', ':', 'b@')) * 2048
        started = time.perf_counter()
        safe_end = pm_runtime._stream_safe_end(adversarial)
        elapsed = time.perf_counter() - started
        self.assertEqual(safe_end, 0)
        self.assertLess(elapsed, 0.5)

    def test_message_start_resets_stream_block_offsets(self):
        message_start = {
            _runtime_parts('type'): _runtime_parts('stream', '_', 'event'),
            _runtime_parts('event'): {_runtime_parts('type'):
                                      _runtime_parts('message', '_', 'start')},
        }
        process = FakeProcess(encoded(
            message_start,
            _runtime_delta(2, _runtime_parts('first')),
            message_start,
            _runtime_delta(2, _runtime_parts('second')),
            {_runtime_parts('type'): _runtime_parts('result'),
             _runtime_parts('result'): _runtime_parts('finished')}))
        runtime = self.runtime(process)
        runtime.open()
        events = list(runtime.send(_runtime_parts('request')))
        deltas = [event['text'] for event in events if event['type'] == 'TEXT_DELTA']
        self.assertEqual(deltas, [_runtime_parts('first'), _runtime_parts('second')])
        runtime.close()

    def test_assistant_text_replaces_the_stream_buffer_length(self):
        assistant = {
            _runtime_parts('type'): _runtime_parts('assistant'),
            _runtime_parts('message'): {
                _runtime_parts('content'): [{
                    _runtime_parts('type'): _runtime_parts('text'),
                    _runtime_parts('index'): 3,
                    _runtime_parts('text'): _runtime_parts('OK'),
                }],
            },
        }
        process = FakeProcess(encoded(
            _runtime_delta(3, _runtime_parts('Long stream text')),
            assistant,
            {_runtime_parts('type'): _runtime_parts('result'),
             _runtime_parts('result'): _runtime_parts('finished')}))
        runtime = self.runtime(process)
        runtime.open()
        events = list(runtime.send(_runtime_parts('request')))
        deltas = [event['text'] for event in events if event['type'] == 'TEXT_DELTA']
        self.assertEqual(deltas, [_runtime_parts('Long stream text')])
        self.assertEqual([event['text'] for event in events
                          if event['type'] == 'ASSISTANT_TEXT'], [_runtime_parts('OK')])
        runtime.close()

    def test_block_stop_flushes_only_the_pending_secret_candidate(self):
        process = FakeProcess(encoded(
            {'type': 'stream_event', 'event': {'type': 'content_block_delta', 'index': 4,
             'delta': {'type': 'text_delta', 'text': 'Hi sk-'}}},
            {'type': 'stream_event', 'event': {'type': 'content_block_stop', 'index': 4}},
            {'type': 'assistant', 'message': {'content': [
                {'type': 'text', 'index': 4, 'text': 'Hi sk-'}]}},
            {'type': 'result', 'result': 'Hi sk-'}))
        runtime = self.runtime(process)
        runtime.open()
        events = list(runtime.send('request'))
        deltas = [event for event in events if event['type'] == 'TEXT_DELTA']
        assistant_index = next(i for i, event in enumerate(events)
                               if event['type'] == 'ASSISTANT_TEXT')
        self.assertEqual([event['text'] for event in deltas], ['Hi ', 'sk-'])
        self.assertEqual(deltas[-1]['block_index'], 4)
        self.assertEqual(events[assistant_index - 1]['type'], 'TEXT_DELTA')
        runtime.close()

    def test_split_credential_is_screened_before_text_events(self):
        prefix = ''.join(('s', 'k', '-'))
        credential = prefix + ('x' * 24)
        process = FakeProcess(encoded(
            {'type': 'stream_event', 'event': {'type': 'content_block_delta', 'index': 2,
             'delta': {'type': 'text_delta', 'text': 'Reply ' + credential[:8]}}},
            {'type': 'stream_event', 'event': {'type': 'content_block_delta', 'index': 2,
             'delta': {'type': 'text_delta', 'text': credential[8:]}}},
            {'type': 'assistant', 'message': {'content': [
                {'type': 'text', 'index': 2, 'text': 'Reply ' + credential}] }},
            {'type': 'result', 'result': 'Reply ' + credential}))
        runtime = self.runtime(process)
        runtime.open()
        events = list(runtime.send('request'))
        self.assertNotIn(credential, repr(events))
        for event in events:
            if event['type'] in ('TEXT_DELTA', 'ASSISTANT_TEXT'):
                self.assertFalse(pm_runtime._emitted_sensitive(event['text']))
        runtime.close()

    def test_interrupt_persists_screened_streamed_text(self):
        process = FakeProcess(encoded(
            {'type': 'stream_event', 'event': {'type': 'content_block_delta', 'index': 0,
             'delta': {'type': 'text_delta', 'text': 'Partial reply so far.'}}},
            {'type': 'result', 'subtype': 'error', 'result': ''}))
        runtime = self.runtime(process)
        runtime.open()
        events = runtime.send('request')
        runtime.interrupt()
        result = list(events)
        self.assertEqual(result[-1]['status'], 'INTERRUPTED')
        page = pm_runtime.read_history(SESSION, state_root=self.root)
        self.assertEqual(page['turns'][0]['result_text'], 'Partial reply so far.')
        runtime.close()

    def test_tool_result_reason_is_fixed_and_does_not_expose_payload(self):
        process = FakeProcess(encoded(
            {'type': 'user', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': 'a', 'content': 'secret output'}]}},
            {'type': 'user', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': 'b', 'is_error': True,
                 'permission_denial': 'private reason', 'content': 'private'}]}},
            {'type': 'result', 'result': 'done'}))
        runtime = self.runtime(process)
        runtime.open()
        results = [event for event in runtime.send('request') if event['type'] == 'TOOL_RESULT']
        self.assertEqual([event['reason'] for event in results], ['OK', 'PERMISSION_DENIED'])
        self.assertNotIn('private', repr(results))
        runtime.close()

    def test_stderr_is_bounded_screened_and_process_alive_is_exposed(self):
        class StderrProcess(FakeProcess):
            stderr = io.BytesIO(b'quota exceeded')

        process = StderrProcess()
        runtime = self.runtime(process)
        runtime.open()
        self.assertTrue(runtime.is_alive())
        events = list(runtime.send('request'))
        error = next(event for event in events if event['type'] == 'RUNTIME_ERROR')
        self.assertEqual(error['code'], 'PM_QUOTA_EXHAUSTED')
        self.assertEqual(error['stderr'], 'quota exceeded')
        runtime.close()
        self.assertFalse(runtime.is_alive())

    def test_repeated_idempotency_key_reuses_persisted_turn(self):
        first_process = FakeProcess(encoded({'type': 'result', 'result': 'done'}))
        runtime = self.runtime(first_process)
        runtime.open()
        first = list(runtime.send('request', idempotency_key='request-1'))
        captured_input = first_process.stdin.getvalue()
        runtime.close()
        second_process = FakeProcess()
        runtime = self.runtime(second_process)
        runtime.open()
        replay = list(runtime.send('request', idempotency_key='request-1'))
        self.assertEqual(len(captured_input.splitlines()), 1)
        self.assertEqual(second_process.stdin.getvalue(), b'')
        self.assertEqual(replay[0]['type'], 'TURN_REPLAY')
        self.assertEqual(replay[0]['turn_id'], first[-1]['turn_id'])
        runtime.close()

    def test_close_terminates_child(self):
        process = FakeProcess()
        runtime = self.runtime(process)
        runtime.open()
        runtime.close()
        self.assertTrue(process.terminated)

    def test_close_persistence_failure_still_cleans_and_can_be_repeated(self):
        process = FakeProcess()
        runtime = self.runtime(process)
        runtime.open()
        runtime.send('pending request')
        persist = runtime._persist_turn
        failure = LLMToolError('synthetic persistence failure')
        with patch.object(runtime, '_persist_turn', side_effect=[failure, persist]):
            with self.assertRaisesRegex(LLMToolError, 'synthetic persistence failure'):
                runtime.close(timeout=0)
            self.assertTrue(process.terminated)
            self.assertGreater(process.wait_calls, 0)
            self.assertIsNone(runtime._child)
            self.assertIsNone(runtime._lock_fd)
            self.assertNotIn(runtime, pm_runtime._active_runtimes)
            for signum, handler in self.original_signal_handlers.items():
                self.assertIs(signal.getsignal(signum), handler)
            runtime.close(timeout=0)
            runtime.close(timeout=0)
        self.assertIsNone(runtime._turn)

    def test_unsolicited_turn_between_sends_is_a_background_turn_with_its_own_usage(self):
        # P0-3 (Owner TUI run 2026-10-29, PM 0d2524b7): Claude Code started turns by itself
        # for task notifications between Owner sends. stdout was read only inside send(),
        # so the next send ended on the stale background `result` (COMPLETED in 0.5s).
        process = LiveProcess()
        runtime = self.runtime(process)
        runtime.open()
        first = runtime.send('派工')
        process.emit(*_bg_turn('第一輪', 1))
        first = list(first)
        self.assertNotIn('TURN_ACCEPTED', [event['type'] for event in first])
        self.assertEqual(first[-1]['usage'], {'output_tokens': 1})
        self.assertIsNone(runtime.background_turn(timeout=0.05))   # idle: nothing to claim
        process.emit({'type': 'system', 'subtype': 'status'},   # not a turn: dropped
                     {'type': 'control_response', 'response': {}},
                     *_bg_turn('Codex 已回報', 2))
        background = list(runtime.background_turn(timeout=5))
        self.assertEqual([event['type'] for event in background],
                         ['TEXT_DELTA', 'ASSISTANT_TEXT', 'TURN_END'])
        self.assertEqual(background[-1]['result_text'], 'Codex 已回報')
        self.assertEqual(background[-1]['usage'], {'output_tokens': 2})
        second = runtime.send('回報進度')
        process.emit(*_bg_turn('進度如下', 3))
        second = list(second)
        self.assertEqual(second[-1]['result_text'], '進度如下')   # not the background reply
        self.assertEqual(second[-1]['usage'], {'output_tokens': 3})
        turns = pm_runtime.read_history(SESSION, state_root=self.root)['turns']
        self.assertEqual([(t['seq'], t['owner_text'], t.get('origin'), t['result_text'],
                           t['usage'], t['status']) for t in turns],
                         [(1, '派工', None, '第一輪', {'output_tokens': 1}, 'COMPLETED'),
                          (2, '', 'background', 'Codex 已回報', {'output_tokens': 2}, 'COMPLETED'),
                          (3, '回報進度', None, '進度如下', {'output_tokens': 3}, 'COMPLETED')])
        self.assertEqual([u['message']['content'][0]['text'] for u in process.users()],
                         ['派工', '回報進度'])

    def test_owner_send_during_a_background_turn_is_held_not_written(self):
        # The Owner message must not reach Claude Code mid-turn: it absorbs it into the
        # running background turn (queue-operation remove, absorbed_mid_turn) and no turn
        # of its own ever answers it.
        process = LiveProcess()
        runtime = self.runtime(process)
        runtime.open()
        process.emit({'type': 'stream_event', 'event': {'type': 'message_start', 'message': {}}},
                     _runtime_delta(0, '背景'))
        events = runtime.background_turn(timeout=5)
        self.assertEqual(next(events)['type'], 'TEXT_DELTA')
        with self.assertRaises(LLMToolError) as raised:
            runtime.send('繼續推進，回報進度')
        self.assertEqual(raised.exception.pm_runtime_code, 'PM_TURN_ACTIVE')
        self.assertEqual(process.users(), [])
        process.emit({'type': 'result', 'result': '背景', 'usage': {}})
        self.assertEqual(list(events)[-1]['type'], 'TURN_END')
        owner = runtime.send('繼續推進，回報進度')
        process.emit(*_bg_turn('收到', 1))
        self.assertEqual(list(owner)[-1]['result_text'], '收到')
        self.assertEqual([u['message']['content'][0]['text'] for u in process.users()],
                         ['繼續推進，回報進度'])

    def test_background_turn_waits_for_an_owner_turn_and_returns_none_after_close(self):
        process = LiveProcess()
        runtime = self.runtime(process)
        runtime.open()
        owner = runtime.send('請問')
        process.emit(_runtime_delta(0, '答'))
        self.assertEqual(next(owner)['text'], '答')
        # Owner turn lines are never claimed as a background turn.
        self.assertIsNone(runtime.background_turn(timeout=0.05))
        process.emit({'type': 'result', 'result': '答', 'usage': {}})
        self.assertEqual(list(owner)[-1]['status'], 'COMPLETED')
        process.feed.put(None)   # the child's stdout ends between turns
        with self.assertRaises(LLMToolError) as raised:
            runtime.background_turn(timeout=5)
        self.assertEqual(raised.exception.pm_runtime_code, 'PM_PROCESS_EXITED')
        runtime.close(timeout=0)
        self.assertIsNone(runtime.background_turn(timeout=5))


    def test_turn_started_upstream_before_the_message_is_read_is_a_separate_background_turn(self):
        # Codex v1 P1: a notification turn can start in Claude Code before it reads the
        # Owner line, with its output still in flight. The Owner turn begins only at the
        # message's own `started` frame; the turn before it is a background turn.
        process = LiveProcess(auto_accept=False)
        runtime = self.runtime(process)
        runtime.open()
        owner = runtime.send('回報進度')
        sent = process.users()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]['priority'], 'later')
        message = sent[0]['uuid']
        start, delta, assistant, result = _bg_turn('Codex 已回報', 2)
        process.emit(start, delta)                         # in the pipe before `queued`
        process.lifecycle(message, 'queued')
        process.emit({'type': 'system', 'subtype': 'task_notification'}, assistant, result)
        process.lifecycle(message, 'started')
        process.emit(*_bg_turn('進度如下', 3))
        process.lifecycle(message, 'completed')
        events = list(owner)
        self.assertEqual([event['type'] for event in events],
                         ['BACKGROUND_TURN_START', 'TEXT_DELTA', 'ASSISTANT_TEXT', 'TURN_END',
                          'TURN_ACCEPTED', 'TEXT_DELTA', 'ASSISTANT_TEXT', 'TURN_END'])
        background, mine = events[3], events[-1]
        self.assertEqual((background['origin'], background['result_text'], background['usage']),
                         ('background', 'Codex 已回報', {'output_tokens': 2}))
        self.assertNotIn('origin', mine)
        self.assertEqual((mine['result_text'], mine['usage']), ('進度如下', {'output_tokens': 3}))
        self.assertEqual(events[0]['turn_id'], background['turn_id'])
        self.assertEqual(events[4]['turn_id'], mine['turn_id'])
        turns = pm_runtime.read_history(SESSION, state_root=self.root)['turns']
        self.assertEqual([(t['owner_text'], t.get('origin'), t['result_text'], t['status'])
                          for t in sorted(turns, key=lambda t: t['seq'])],
                         [('回報進度', None, '進度如下', 'COMPLETED'),
                          ('', 'background', 'Codex 已回報', 'COMPLETED')])
        self.assertEqual(len(process.users()), 1)            # written once, never again
        self.assertIsNone(runtime.background_turn(timeout=0.05))   # nothing left over

    def test_message_withdrawn_by_claude_code_ends_not_sent_and_is_not_written_again(self):
        process = LiveProcess(auto_accept=False)
        runtime = self.runtime(process)
        runtime.open()
        owner = runtime.send('回報進度')
        message = process.users()[0]['uuid']
        process.lifecycle(message, 'queued')
        start, delta, assistant, result = _bg_turn('背景', 1)
        process.emit(start, delta)
        runtime.interrupt()   # Esc while the message waits behind that turn
        controls = [line['request'] for line in process.lines if line['type'] == 'control_request']
        self.assertEqual(controls, [{'subtype': 'interrupt'},
                                    {'subtype': 'cancel_async_message', 'message_uuid': message}])
        process.emit(assistant, result)
        process.lifecycle(message, 'cancelled')
        events = list(owner)
        self.assertEqual([event['type'] for event in events],
                         ['BACKGROUND_TURN_START', 'TEXT_DELTA', 'ASSISTANT_TEXT', 'TURN_END',
                          'TURN_END'])
        self.assertEqual(events[3]['status'], 'INTERRUPTED')   # the background turn
        self.assertEqual(events[-1]['status'], 'NOT_SENT')     # the Owner message, unprocessed
        self.assertEqual(len(process.users()), 1)
        self.assertFalse(runtime.turn_active())
        statuses = {t['owner_text']: t['status'] for t in
                    pm_runtime.read_history(SESSION, state_root=self.root)['turns']}
        self.assertEqual(statuses, {'回報進度': 'NOT_SENT', '': 'INTERRUPTED'})

    def test_message_taken_in_by_a_running_turn_ends_absorbed_without_a_second_write(self):
        # Not seen with priority "later" on Claude Code 2.1.312; if it happens the message
        # was delivered once, so the turn ends instead of waiting for a turn that never comes.
        process = LiveProcess(auto_accept=False)
        runtime = self.runtime(process)
        runtime.open()
        owner = runtime.send('回報進度')
        message = process.users()[0]['uuid']
        start, delta, assistant, result = _bg_turn('背景含回覆', 4)
        process.lifecycle(message, 'queued')
        process.emit(start, delta)
        process.lifecycle(message, 'started')
        process.emit(assistant, result)
        process.lifecycle(message, 'completed')
        events = list(owner)
        self.assertEqual([event.get('status') for event in events if event['type'] == 'TURN_END'],
                         ['COMPLETED', 'ABSORBED'])
        self.assertEqual(len(process.users()), 1)
        self.assertFalse(runtime.turn_active())


    def _statuses(self):
        return {t['owner_text']: (t['status'], t['result_text']) for t in
                pm_runtime.read_history(SESSION, state_root=self.root)['turns']}

    def test_missing_started_frame_ends_unknown_after_the_bound_and_is_not_resent(self):
        # Codex v2 P1: `queued` but never `started` must not leave the turn ACTIVE forever.
        process = LiveProcess(auto_accept=False)
        runtime = self.runtime(process)
        runtime.open()
        with patch.object(pm_runtime, 'PM_STARTED_GRACE', 0.3):
            owner = runtime.send('回報進度')
            process.lifecycle(process.users()[0]['uuid'], 'queued')
            began = time.monotonic()
            events = list(owner)
        self.assertLess(time.monotonic() - began, 3)
        self.assertEqual([(e['type'], e['status']) for e in events], [('TURN_END', 'UNKNOWN')])
        self.assertEqual(self._statuses(), {'回報進度': ('UNKNOWN', '')})
        self.assertEqual(len(process.users()), 1)
        self.assertFalse(runtime.turn_active())

    def test_missing_queued_frame_ends_unknown_after_the_bound(self):
        process = LiveProcess(auto_accept=False)
        runtime = self.runtime(process)
        runtime.open()
        with patch.object(pm_runtime, 'PM_QUEUED_TIMEOUT', 0.3):
            events = list(runtime.send('回報進度'))
        self.assertEqual([(e['type'], e['status']) for e in events], [('TURN_END', 'UNKNOWN')])
        self.assertEqual(len(process.users()), 1)

    def test_late_queued_after_a_background_result_is_not_misattributed(self):
        # Codex v2 P1: a result seen before `queued` used to be taken as the Owner reply.
        process = LiveProcess(auto_accept=False)
        runtime = self.runtime(process)
        runtime.open()
        owner = runtime.send('回報進度')
        message = process.users()[0]['uuid']
        process.emit(*_bg_turn('背景回覆', 2))        # a whole turn before `queued`
        process.lifecycle(message, 'queued', 'started')
        process.emit(*_bg_turn('進度如下', 3))
        process.lifecycle(message, 'completed')
        events = list(owner)
        self.assertEqual([e['type'] for e in events],
                         ['BACKGROUND_TURN_START', 'TEXT_DELTA', 'ASSISTANT_TEXT', 'TURN_END',
                          'TURN_ACCEPTED', 'TEXT_DELTA', 'ASSISTANT_TEXT', 'TURN_END'])
        self.assertEqual((events[3]['origin'], events[3]['result_text']), ('background', '背景回覆'))
        self.assertEqual((events[-1]['result_text'], events[-1]['usage']),
                         ('進度如下', {'output_tokens': 3}))
        self.assertEqual(self._statuses(), {'回報進度': ('COMPLETED', '進度如下'),
                                            '': ('COMPLETED', '背景回覆')})

    def test_background_result_without_any_lifecycle_frame_ends_unknown_not_answered(self):
        process = LiveProcess(auto_accept=False)
        runtime = self.runtime(process)
        runtime.open()
        with patch.object(pm_runtime, 'PM_QUEUED_TIMEOUT', 0.3):
            owner = runtime.send('回報進度')
            process.emit(*_bg_turn('背景回覆', 2))
            events = list(owner)
        self.assertEqual([(e['type'], e.get('status'), e.get('origin')) for e in events
                          if e['type'] == 'TURN_END'],
                         [('TURN_END', 'COMPLETED', 'background'), ('TURN_END', 'UNKNOWN', None)])
        self.assertEqual(self._statuses(), {'回報進度': ('UNKNOWN', ''),
                                            '': ('COMPLETED', '背景回覆')})

    def test_bound_passing_inside_a_background_turn_is_recorded_at_once(self):
        process = LiveProcess(auto_accept=False)
        runtime = self.runtime(process)
        runtime.open()
        start, delta, assistant, result = _bg_turn('長背景', 2)
        with patch.object(pm_runtime, 'PM_QUEUED_TIMEOUT', 0.3):
            owner = runtime.send('回報進度')
            process.emit(start, delta)
            self.assertEqual(next(owner)['type'], 'BACKGROUND_TURN_START')
            self.assertEqual(next(owner)['type'], 'TEXT_DELTA')
            rest = []
            reader = threading.Thread(target=lambda: rest.extend(owner), daemon=True)
            reader.start()   # like the TUI worker: blocked in the iterator meanwhile
            deadline = time.monotonic() + 3
            while self._statuses()['回報進度'][0] == 'ACTIVE' and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(self._statuses()['回報進度'][0], 'UNKNOWN')   # not ACTIVE
            process.emit(assistant, result)
            reader.join(5)
        # hd665: UNKNOWN is published at the deadline, before that background turn ends.
        self.assertEqual([e.get('status') for e in rest if e['type'] == 'TURN_END'],
                         ['UNKNOWN', 'COMPLETED'])
        self.assertFalse(runtime.turn_active())

    def test_completed_before_started_is_ambiguous_and_ends_unknown(self):
        process = LiveProcess(auto_accept=False)
        runtime = self.runtime(process)
        runtime.open()
        owner = runtime.send('回報進度')
        process.lifecycle(process.users()[0]['uuid'], 'queued', 'completed')
        self.assertEqual([e['status'] for e in owner], ['UNKNOWN'])
        self.assertEqual(len(process.users()), 1)

    def test_normal_path_events_are_unchanged(self):
        process = LiveProcess()
        runtime = self.runtime(process)
        runtime.open()
        owner = runtime.send('請問')
        process.emit(*_bg_turn('答', 1))
        self.assertEqual([e['type'] for e in owner], ['TEXT_DELTA', 'ASSISTANT_TEXT', 'TURN_END'])

    # -- hd665 (High Debug, RCA_CONFIRMED): Owner-message lifecycle as one ordered machine --
    OTHER = '0f0f0f0f-0000-4000-8000-000000000000'
    SHORT = {'PM_QUEUED_TIMEOUT': 0.3, 'PM_STARTED_GRACE': 0.3, 'PM_TERMINAL_GRACE': 0.3,
             'PM_CANCEL_GRACE': 0.3}
    # Independently written from hd665's tables (not derived from pm_runtime._TRANSITIONS):
    # the phase each live phase moves to on each normalized event.
    EXPECTED_NEXT = {
        'W': dict(queued='Q', started='X', completed='X', cancelled='X', discarded='X',
                  refused='X', output='W', result='W', other_started='W', notice='W',
                  timeout='X', esc='W', exit='X', invalid='X'),
        'Q': dict(queued='X', started='O', completed='X', cancelled='N', discarded='N',
                  refused='N', output='Q', result='Q', other_started='Q', notice='Q',
                  timeout='X', esc='Q', exit='X', invalid='X'),
        'O': dict(queued='X', started='X', completed='X', cancelled='X', discarded='X',
                  refused='X', output='O', result='RO', other_started='X', notice='O',
                  timeout='O', esc='O', exit='X', invalid='X'),
        'A': dict(queued='X', started='X', completed='X', cancelled='X', discarded='X',
                  refused='X', output='A', result='RA', other_started='X', notice='A',
                  timeout='A', esc='A', exit='X', invalid='X'),
        'RO': dict(queued='X', started='X', completed='C', cancelled='X', discarded='X',
                   refused='X', output='X', result='X', other_started='X', notice='RO',
                   timeout='X', esc='RO', exit='X', invalid='X'),
        'RA': dict(queued='X', started='X', completed='B', cancelled='X', discarded='X',
                   refused='X', output='X', result='X', other_started='X', notice='RA',
                   timeout='X', esc='RA', exit='X', invalid='X'),
    }
    PREFIX = {'W': [], 'Q': ['queued'], 'O': ['queued', 'started'],
              'A': ['queued', 'output', 'started'],
              'RO': ['queued', 'started', 'output', 'result'],
              'RA': ['queued', 'output', 'started', 'result']}
    # A legal way on from each phase; the final delivery it must reach.
    SUFFIX = {'W': ['queued', 'started', 'output', 'result', 'completed'],
              'Q': ['started', 'output', 'result', 'completed'],
              'O': ['output', 'result', 'completed'], 'A': ['result', 'completed'],
              'RO': ['completed'], 'RA': ['completed']}
    REACHES = {'W': 'COMPLETED', 'Q': 'COMPLETED', 'O': 'COMPLETED', 'RO': 'COMPLETED',
               'A': 'ABSORBED', 'RA': 'ABSORBED', 'C': 'COMPLETED', 'B': 'ABSORBED',
               'N': 'NOT_SENT', 'X': 'UNKNOWN'}

    def _attempt(self, bounds=None, process=None):
        """An Owner send consumed by a thread, as the TUI worker does."""
        if bounds:
            patcher = patch.multiple(pm_runtime, **bounds)
            patcher.start()
            self.addCleanup(patcher.stop)
        process = process or LiveProcess(auto_accept=False)
        runtime = self.runtime(process)
        runtime.open()
        run = {'runtime': runtime, 'process': process, 'events': [], 'error': None}
        stream = runtime.send('回報進度')
        run['attempt'] = runtime._attempt
        run['uuid'] = process.users()[0]['uuid'] if process.users() else None

        def consume():
            try:
                for event in stream:
                    run['events'].append(event)
            except LLMToolError as exc:
                run['error'] = exc

        run['reader'] = threading.Thread(target=consume, daemon=True)
        run['reader'].start()
        return run

    def _token(self, run, token):
        process, message = run['process'], run['uuid']
        if token in pm_runtime._LIFECYCLE_STATES:
            process.lifecycle(message, token)
        elif token == 'output':
            process.emit(_runtime_delta(0, 'TEXT'))
        elif token == 'result':
            process.emit({'type': 'result', 'result': 'R', 'usage': {'output_tokens': 7}})
        elif token == 'other_started':
            process.lifecycle(self.OTHER, 'started')
        elif token == 'other_completed':
            process.lifecycle(self.OTHER, 'completed')
        elif token == 'notice':
            process.emit({'type': 'system', 'subtype': 'status'})
        elif token == 'timeout':
            time.sleep(0.7)
        elif token == 'esc':
            run['runtime'].interrupt()
        elif token == 'exit':
            process.feed.put(None)
        elif token == 'close':
            run['runtime'].close(timeout=0)
        elif token == 'invalid':
            process.emit({'type': 'command_lifecycle', 'command_uuid': message, 'state': 'future'})
        else:
            raise AssertionError(token)

    def _phase(self, run, expected):
        deadline = time.monotonic() + 3
        while run['attempt']['phase'] != expected and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(run['attempt']['phase'], expected)

    def _finish(self, run):
        run['reader'].join(5)
        self.assertFalse(run['reader'].is_alive(), 'the Owner iterator never ended')
        owner = [e for e in run['events'] if e['type'] == 'TURN_END' and e.get('origin') is None]
        self.assertEqual(len(owner), 1, run['events'])   # exactly one receipt
        self.assertEqual(len(run['process'].users()), 1)   # written once, never again
        self.assertFalse(run['runtime'].turn_active())
        run['runtime'].close(timeout=0)   # the next case opens its own driver
        return owner[0]

    def _trace(self, tokens, bounds=None, process=None):
        run = self._attempt(bounds, process)
        for token in tokens:
            if isinstance(token, tuple):   # ('phase', P): wait until the reducer is there
                self._phase(run, token[1])
            else:
                self._token(run, token)
        return run

    def _owner_end(self, tokens, bounds=None, close_with_result=True):
        run = self._trace(tokens, bounds)
        if close_with_result:
            self._token(run, 'result')   # lets a turn still in flight end as background
        return self._finish(run), run

    def _assert_unknown(self, end):
        self.assertEqual((end['status'], end['delivery']), ('UNKNOWN', 'UNKNOWN'))
        self.assertEqual((end['result_text'], end['usage']), ('', {}))   # nothing attributed

    def test_owner_lifecycle_transition_matrix(self):
        events = pm_runtime._OWNER_EVENTS
        self.assertEqual(set(self.EXPECTED_NEXT), set(pm_runtime._LIVE))
        for phase, row in self.EXPECTED_NEXT.items():
            self.assertEqual(set(row), set(events))
            for event in events:
                with self.subTest(phase=phase, event=event):
                    bounds = self.SHORT if event == 'timeout' else None
                    run = self._trace([*self.PREFIX[phase], ('phase', phase), event], bounds)
                    expected = row[event]
                    if expected in pm_runtime._LIVE:
                        self._phase(run, expected)
                        suffix = list(self.SUFFIX[expected])
                        if expected in ('W', 'Q') and event in ('output', 'other_started'):
                            suffix.insert(0, 'result')   # that background turn ends first
                        for token in suffix:
                            self._token(run, token)
                    else:
                        self._phase(run, expected)
                        self._token(run, 'result')
                    end = self._finish(run)
                    self.assertEqual(end['delivery'], self.REACHES[expected])
                    if expected in ('X', 'N'):
                        self.assertEqual((end['result_text'], end['usage']), ('', {}))

    def test_started_without_queued_is_unknown(self):
        end, _ = self._owner_end(['started', 'output', 'result', 'completed'])
        self._assert_unknown(end)

    def test_started_before_queued_is_unknown(self):
        # The hd665 reproducer: started -> queued -> result used to end COMPLETED.
        end, _ = self._owner_end(['started', 'queued', 'result'])
        self._assert_unknown(end)

    def test_duplicate_and_regressive_lifecycle_frames_are_unknown(self):
        for trace in (['queued', 'queued'], ['queued', 'started', 'queued'],
                      ['queued', 'started', 'started']):
            with self.subTest(trace=trace):
                end, _ = self._owner_end(trace + ['output', 'result', 'completed'])
                self._assert_unknown(end)

    def test_completed_requires_started_and_result_in_order(self):
        for phase in ('W', 'Q', 'O', 'A'):
            with self.subTest(phase=phase):
                end, _ = self._owner_end([*self.PREFIX[phase], ('phase', phase), 'completed'])
                self._assert_unknown(end)

    def test_result_waits_for_matching_completed(self):
        run = self._trace(['queued', 'started', 'output', 'result', ('phase', 'RO')])
        time.sleep(0.3)
        self.assertFalse([e for e in run['events'] if e['type'] == 'TURN_END'])   # not yet
        self._token(run, 'completed')
        end = self._finish(run)
        self.assertEqual((end['status'], end['delivery'], end['usage']),
                         ('COMPLETED', 'COMPLETED', {'output_tokens': 7}))
        end, _ = self._owner_end(['queued', 'started', 'output', 'result', ('phase', 'RO'),
                                  'timeout'], self.SHORT, close_with_result=False)
        self._assert_unknown(end)   # no `completed` within the terminal grace

    def test_negative_frames_are_phase_sensitive(self):
        for state in pm_runtime._NOT_ACCEPTED:
            for phase in pm_runtime._LIVE:
                with self.subTest(state=state, phase=phase):
                    end, _ = self._owner_end([*self.PREFIX[phase], ('phase', phase), state])
                    if phase == 'Q':
                        self.assertEqual((end['status'], end['reason']), ('NOT_SENT', state))
                    else:
                        self._assert_unknown(end)

    def test_invalid_transition_inside_background_is_unknown(self):
        for trace in (['output', 'started'], ['output', 'completed'],
                      ['queued', 'output', 'queued'], ['queued', 'output', 'completed'],
                      ['queued', 'output', 'started', 'queued'],
                      ['queued', 'output', 'started', 'started'],
                      ['queued', 'output', 'started', 'cancelled']):
            with self.subTest(trace=trace):
                end, run = self._owner_end(trace)
                self._assert_unknown(end)
                self.assertNotIn('ABSORBED', [e.get('status') for e in run['events']])

    def test_unknown_cannot_become_absorbed_or_completed(self):
        run = self._trace(['queued', ('phase', 'Q'), 'timeout', ('phase', 'X')], self.SHORT)
        for token in ('started', 'output', 'result', 'completed'):
            self._token(run, token)
        run['reader'].join(5)
        late = list(run['runtime'].background_turn(timeout=2))   # the late run: background
        end = self._finish(run)
        self._assert_unknown(end)
        self.assertTrue(late)
        self.assertTrue(all(e.get('origin') == 'background' for e in late))
        statuses = {t['owner_text']: t['status'] for t in
                    pm_runtime.read_history(SESSION, state_root=self.root)['turns']}
        self.assertEqual(statuses['回報進度'], 'UNKNOWN')
        self.assertFalse(run['runtime'].automatic_sends_blocked())   # plausible after X

    def test_other_uuid_started_tracks_background_before_owner_start(self):
        run = self._trace(['queued', ('phase', 'Q'), 'other_started'], self.SHORT)
        time.sleep(0.7)   # past PM_STARTED_GRACE: suspended while that command runs
        self.assertEqual(run['attempt']['phase'], 'Q')
        for token in ('output', 'result', 'started', 'output', 'result', 'completed'):
            self._token(run, token)
        end = self._finish(run)
        self.assertEqual((end['delivery'], end['usage']), ('COMPLETED', {'output_tokens': 7}))
        self.assertIn('BACKGROUND_TURN_START', [e['type'] for e in run['events']])
        # and the foreign command's own end lifts the suspension again
        run = self._trace(['queued', ('phase', 'Q'), 'other_started', 'other_completed'],
                          self.SHORT)
        self._phase(run, 'X')
        self._assert_unknown(self._finish(run))

    def test_other_uuid_started_after_owner_start_is_unknown(self):
        end, run = self._owner_end(['queued', 'started', 'output', 'other_started', 'output'])
        self._assert_unknown(end)
        background = [e for e in run['events'] if e['type'] == 'TURN_END'
                      and e.get('origin') == 'background']
        self.assertEqual([e['usage'] for e in background], [{'output_tokens': 7}])

    def _stamped(self, run, value, arrived):
        line = pm_runtime._Line(encoded(value)[0])
        line.arrived = arrived
        with run['runtime']._lines_cond:
            run['runtime']._lines.append(line)
            run['runtime']._lines_cond.notify_all()

    def test_deadlines_use_write_and_arrival_times(self):
        # Delayed consumption: frames that arrived in time count, however late they are read.
        process = LiveProcess(auto_accept=False)
        runtime = self.runtime(process)
        runtime.open()
        with patch.multiple(pm_runtime, **self.SHORT):
            stream = runtime.send('回報進度')
            message = process.users()[0]['uuid']
            process.lifecycle(message, 'queued', 'started')
            process.emit(_runtime_delta(0, 'TEXT'), {'type': 'result', 'result': 'R', 'usage': {}})
            process.lifecycle(message, 'completed')
            time.sleep(0.8)   # well past every bound before the first read
            events = list(stream)
        self.assertEqual(events[-1]['delivery'], 'COMPLETED')
        runtime.close(timeout=0)
        # Boundary on `queued`: just before the deadline counts; at or after it does not.
        for offset, delivery in ((-0.01, 'COMPLETED'), (0.0, 'UNKNOWN'), (0.01, 'UNKNOWN')):
            with self.subTest(offset=offset):
                run = self._attempt({'PM_QUEUED_TIMEOUT': 5.0})
                arrived = run['attempt']['written_at'] + 5.0 + offset
                frame = {'type': 'command_lifecycle', 'command_uuid': run['uuid']}
                for state in ('queued', 'started'):
                    self._stamped(run, dict(frame, state=state), arrived)
                self._stamped(run, {'type': 'result', 'result': 'R', 'usage': {}}, arrived)
                self._stamped(run, dict(frame, state='completed'), arrived)
                self.assertEqual(self._finish(run)['delivery'], delivery)

    def test_deadlines_expire_under_continuous_output(self):
        run = self._attempt({'PM_QUEUED_TIMEOUT': 0.4})
        stop = threading.Event()

        def noise():
            while not stop.is_set():
                self._token(run, 'notice')
                run['process'].lifecycle(self.OTHER, 'queued')
                self._token(run, 'output')   # a background turn streaming meanwhile
                time.sleep(0.02)

        began = time.monotonic()
        threading.Thread(target=noise, daemon=True).start()
        self._phase(run, 'X')
        took = time.monotonic() - began
        stop.set()
        self._token(run, 'result')
        self._assert_unknown(self._finish(run))
        self.assertLess(took, 1.5)

    def test_queued_owner_waits_through_background_turn(self):
        run = self._trace(['queued', ('phase', 'Q'), 'output'], self.SHORT)
        time.sleep(0.8)   # a long background turn: only the idle start grace is suspended
        self.assertEqual(run['attempt']['phase'], 'Q')
        for token in ('result', 'started', 'output', 'result', 'completed'):
            self._token(run, token)
        self.assertEqual(self._finish(run)['delivery'], 'COMPLETED')

    def test_owner_exit_and_close_preserve_unknown_draft(self):
        for how in ('exit', 'close'):
            for phase in pm_runtime._LIVE:
                with self.subTest(how=how, phase=phase):
                    run = self._trace([*self.PREFIX[phase], ('phase', phase), how])
                    end = self._finish(run)
                    self._assert_unknown(end)
                    record = [t for t in pm_runtime.read_history(SESSION, state_root=self.root)
                              ['turns'] if t['turn_id'] == end['turn_id']][0]
                    self.assertEqual((record['status'], record['delivery']), ('UNKNOWN', 'UNKNOWN'))
                    codes = [e['code'] for e in run['events'] if e['type'] == 'RUNTIME_ERROR'
                             and e.get('origin') is None]
                    self.assertEqual(codes, ['PM_PROCESS_EXITED'] if how == 'exit' else [])

    def test_write_failure_distinguishes_unsent_from_uncertain(self):
        class Closed(io.BytesIO):
            closed = True

        class Broken(io.BytesIO):
            def write(self, data):
                raise BrokenPipeError()

        for stdin, status in ((Closed(), 'NOT_SENT'), (Broken(), 'UNKNOWN')):
            with self.subTest(status=status):
                process = FakeProcess()
                runtime = self.runtime(process)
                runtime.open()
                process.stdin = stdin
                events = list(runtime.send('回報進度'))
                self.assertEqual([e['type'] for e in events], ['RUNTIME_ERROR', 'TURN_END'])
                self.assertEqual((events[-1]['status'], events[-1]['delivery']), (status, status))
                self.assertEqual(events[-1]['reason'],
                                 'not_written' if status == 'NOT_SENT' else 'uncertain')
                self.assertFalse(runtime.turn_active())
                runtime.close()

    def test_lifecycle_envelope_validation(self):
        base = {'type': 'command_lifecycle', 'state': 'started'}
        for name in ('oversize', 'malformed_uuid', 'malformed_state', 'unknown_state'):
            with self.subTest(name=name):
                run = self._trace(['queued', ('phase', 'Q')])
                frame = dict(base, command_uuid=run['uuid'])
                if name == 'oversize':
                    frame['pad'] = 'x' * pm_runtime.MAX_STREAM_LINE_BYTES
                elif name == 'wrong_session':
                    frame['session_id'] = 'ffffffff-89ab-cdef-0123-456789abcdef'
                elif name == 'malformed_uuid':
                    frame['command_uuid'] = run['uuid'].upper() + 'x'
                elif name == 'malformed_state':
                    frame['state'] = 5
                else:
                    frame['state'] = 'future'
                run['process'].emit(frame)
                for token in ('output', 'result', 'completed'):
                    self._token(run, token)
                end = self._finish(run)
                self._assert_unknown(end)
                self.assertIn('PM_STREAM_INVALID', [e.get('code') for e in run['events']])
        # A matching frame from another session: the session check still closes the driver
        # (consumed on the main thread, which may restore signal handlers on close).
        process = LiveProcess(auto_accept=False)
        runtime = self.runtime(process)
        runtime.open()
        stream = runtime.send('回報進度')
        message = process.users()[0]['uuid']
        process.lifecycle(message, 'queued')
        process.emit(dict(base, command_uuid=message,
                          session_id='ffffffff-89ab-cdef-0123-456789abcdef'))
        events = []
        with self.assertRaises(LLMToolError) as raised:
            for event in stream:
                events.append(event)
        self.assertEqual(raised.exception.pm_runtime_code, 'PM_SESSION_FORKED')
        self._assert_unknown(events[-1])
        self.assertFalse(runtime.is_alive())

    def test_terminal_attempt_frames_cannot_acknowledge_next_attempt(self):
        process = LiveProcess(auto_accept=False)
        runtime = self.runtime(process)
        runtime.open()
        first = runtime.send('第一則')
        old = process.users()[0]['uuid']
        process.lifecycle(old, 'queued', 'started')
        process.emit(*_bg_turn('一', 1))
        process.lifecycle(old, 'completed')
        self.assertEqual(list(first)[-1]['delivery'], 'COMPLETED')
        self.assertFalse(runtime.automatic_sends_blocked())
        events = []
        second = runtime.send('第二則')
        attempt = runtime._attempt
        reader = threading.Thread(target=lambda: events.extend(second), daemon=True)
        reader.start()
        process.lifecycle(old, 'queued')            # late frames of the finished attempt
        process.lifecycle(old, 'completed')
        time.sleep(0.2)
        self.assertEqual(attempt['phase'], 'W')    # never acknowledged by them
        self.assertTrue(runtime.automatic_sends_blocked())   # latched C contradicted
        new = process.users()[1]['uuid']
        process.lifecycle(new, 'queued', 'started')
        process.emit(*_bg_turn('二', 2))
        process.lifecycle(new, 'completed')
        reader.join(5)
        self.assertEqual((events[-1]['delivery'], events[-1]['result_text']), ('COMPLETED', '二'))
        self.assertEqual(len(process.users()), 2)

    def test_esc_resolution_in_every_live_phase(self):
        cases = (
            ('W', ['esc', 'queued', 'cancelled'], 'NOT_SENT', True),      # confirmed pre-start
            ('Q', ['esc', 'cancelled'], 'NOT_SENT', True),
            ('Q', ['esc', 'timeout'], 'UNKNOWN', True),                   # cancellation missing
            ('O', ['esc', 'output', 'result', 'cancelled'], 'UNKNOWN', False),   # after start
            ('O', ['esc', 'output', 'result', 'completed'], 'COMPLETED', False),  # race
            ('A', ['esc', 'result', 'completed'], 'ABSORBED', False),
            ('RO', ['esc', 'completed'], 'COMPLETED', False),
            ('RA', ['esc', 'completed'], 'ABSORBED', False),
        )
        for phase, tokens, delivery, cancels in cases:
            with self.subTest(phase=phase, tokens=tokens):
                run = self._trace([*self.PREFIX[phase], ('phase', phase), *tokens],
                                  self.SHORT if 'timeout' in tokens else None)
                self._token(run, 'result')
                end = self._finish(run)
                self.assertEqual(end['delivery'], delivery)
                controls = [line['request']['subtype'] for line in run['process'].lines
                            if line['type'] == 'control_request']
                self.assertEqual(controls, ['interrupt', 'cancel_async_message'] if cancels
                                 else ['interrupt'])
                if delivery == 'COMPLETED' and 'result' in tokens:
                    self.assertEqual(end['status'], 'INTERRUPTED')   # the execution fact stays

    def test_absorbed_requires_ordered_terminal_receipt(self):
        end, run = self._owner_end(['queued', 'output', 'started', 'result', 'completed'],
                                   close_with_result=False)
        self.assertEqual((end['status'], end['delivery']), ('ABSORBED', 'ABSORBED'))
        self.assertEqual(sum(e.get('status') == 'ABSORBED' for e in run['events']), 1)
        for trace, bounds in ((['queued', 'output', 'started', 'result', ('phase', 'RA'),
                                'timeout'], self.SHORT),
                              (['queued', 'output', 'started', 'completed', 'result'], None)):
            with self.subTest(trace=trace):
                end, _ = self._owner_end(trace, bounds)
                self._assert_unknown(end)

if __name__ == '__main__':
    unittest.main()
