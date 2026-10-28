"""Local contract tests for the isolated PM runtime driver."""

import io
import json
import os
import signal
import sys
import tempfile
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


class FakeProcess:
    def __init__(self, lines=()):
        self.stdin = io.BytesIO()
        self.stdout = iter(lines)
        self.terminated = False
        self.killed = False
        self.wait_calls = 0

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.stuck and self.wait_calls < 3:
            raise TimeoutError()
        return 0

    stuck = False


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
        self.observe = patch.object(pm_runtime.active_pm_inbox,
                                    'observe_claude_session', return_value=self.cwd)
        self.observe.start()
        self.addCleanup(self.observe.stop)
        pm_runtime._write_json_atomic(self.root, pm_runtime._binding_path(self.root, SESSION), {
            'runtime': 'claude-code', 'native_session_id': SESSION, 'cwd': self.cwd,
            'dedicated': True, 'created_at': pm_runtime._timestamp(lambda: 1)})

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
        runtime = pm_runtime.PMRuntime(SESSION, cwd=self.cwd, runner=runner,
                                       state_root=self.root, environ={'HOME': self.temp.name},
                                       **kwargs)
        self.runtimes.add(runtime)
        self.addCleanup(runtime.close)
        return runtime

    def test_argv_cwd_stream_events_and_history(self):
        process = FakeProcess(encoded(
            {'type': 'stream_event', 'event': {'type': 'content_block_delta',
             'delta': {'type': 'text_delta', 'text': 'Hi'}}},
            {'type': 'assistant', 'message': {'content': [
                {'type': 'text', 'text': 'Hello'},
                {'type': 'tool_use', 'id': 'tool-1', 'name': 'Read', 'input': {'path': 'hidden'}}]}},
            {'type': 'user', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': 'tool-1', 'content': 'hidden output'}]}},
            {'type': 'result', 'result': 'Done', 'usage': {'input_tokens': 1}}))
        runtime = self.runtime(process)
        runtime.open()
        events = list(runtime.send('Owner request'))
        argv, cwd, _ = self.calls[0]
        self.assertEqual(argv, ['claude', '-p', '--input-format', 'stream-json',
                                '--output-format', 'stream-json', '--verbose',
                                '--include-partial-messages', '--resume', SESSION,
                                '--permission-prompts', 'none'])
        self.assertEqual(cwd, self.cwd)
        self.assertEqual([event['type'] for event in events],
                         ['TEXT_DELTA', 'ASSISTANT_TEXT', 'TOOL_USE', 'TOOL_RESULT', 'TURN_END'])
        self.assertNotIn('hidden', repr(events))
        sent = json.loads(process.stdin.getvalue().decode().splitlines()[0])
        self.assertEqual(sent['type'], 'user')
        runtime.close()
        page = pm_runtime.read_history(SESSION, state_root=self.root)
        self.assertEqual(page['turns'][0]['status'], 'COMPLETED')
        self.assertEqual(page['turns'][0]['owner_text'], 'Owner request')

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
        for lines, code in (([b'not-json\n'], 'PM_STREAM_INVALID'), ([], 'PM_PROCESS_EXITED')):
            runtime = self.runtime(FakeProcess(lines))
            runtime.open()
            events = list(runtime.send('request'))
            self.assertEqual(events[0]['type'], 'RUNTIME_ERROR')
            self.assertEqual(events[0]['code'], code)
            self.assertTrue(events[0]['turn_id'])
            self.assertEqual(events[0]['seq'], 0)
            self.assertEqual(events[-1]['status'], 'ERROR')
            runtime.close()

    def test_close_escalates_to_kill(self):
        process = FakeProcess()
        process.stuck = True
        runtime = self.runtime(process)
        runtime.open()
        runtime.close(timeout=0)
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)

    def test_history_paging_and_unsupported_adapter(self):
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
        with self.assertRaises(LLMToolError) as raised:
            self.runtime(runtime='codex').open()
        self.assertEqual(raised.exception.pm_runtime_code, 'PM_RUNTIME_UNSUPPORTED')

    def test_create_and_read_dedicated_binding(self):
        calls = []

        def starter(argv, cwd, env):
            calls.append((argv, cwd, env))
            return FakeProcess()

        binding = pm_runtime.create_dedicated_pm(self.cwd, state_root=self.root,
                                                 environ={'HOME': self.temp.name},
                                                 clock=lambda: 1000, runner=starter)
        self.assertEqual(calls[0][0][:1], ['claude'])
        self.assertEqual(calls[0][0][1], '--session-id')
        self.assertEqual(calls[0][0][2], binding['native_session_id'])
        self.assertEqual(pm_runtime.current_binding(state_root=self.root), binding)

    def test_open_refuses_non_dedicated_session(self):
        (self.root / (SESSION + '.binding.json')).unlink()
        with self.assertRaises(LLMToolError) as raised:
            self.runtime().open()
        self.assertEqual(raised.exception.pm_runtime_code, 'PM_SESSION_NOT_DEDICATED')
        self.assertIn('create', str(raised.exception))

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


if __name__ == '__main__':
    unittest.main()
