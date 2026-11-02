"""Contract tests for the per-turn Codex PM runtime adapter."""

import fcntl
import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmtool import pm_runtime
from llmtool.errors import LLMToolError


SESSION = '01234567-89ab-cdef-0123-456789abcdef'
THREAD = 'abcdefab-cdef-abcd-efab-cdefabcdefab'


class CodexRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cwd = self.root / 'repo'
        self.cwd.mkdir()
        self.state = self.root / 'state'
        pm_runtime._ensure_state_dir(self.state)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.log = self.root / 'calls.jsonl'
        self.write_fake()
        self.environ = {'HOME': str(self.root), 'PATH': str(self.bin),
                        'FAKE_VALUE': 'FAKE_VALUE'}

    def write_fake(self, mode='ok'):
        fake = self.bin / 'codex'
        script = textwrap.dedent('''\
            #!__INTERPRETER__
            import json, os, pathlib, sys, time
            args = sys.argv[1:]
            with open(__LOG__, 'a', encoding='utf-8') as stream:
                stream.write(json.dumps({'args': args,
                    'sentinel_present': 'FAKE_VALUE' in os.environ}) + '\\n')
            home = pathlib.Path(os.environ.get('CODEX_HOME') or os.environ['HOME'] + '/.codex')
            sessions = home / 'sessions'
            sessions.mkdir(parents=True, exist_ok=True)
            session = sessions / ('0000-' + __THREAD__ + '.jsonl')
            session.write_text(json.dumps({'type':'session_meta','payload':
                {'id':__THREAD__,'cwd':os.getcwd()}}) + '\\n', encoding='utf-8')
            if __MODE__ == 'malformed':
                print('{', flush=True)
            elif __MODE__ == 'exit':
                print(json.dumps({'type':'thread.started','thread_id':__THREAD__}), flush=True)
                sys.exit(3)
            elif __MODE__ == 'interrupt':
                print(json.dumps({'type':'thread.started','thread_id':__THREAD__}), flush=True)
                time.sleep(10)
            else:
                print(json.dumps({'type':'thread.started','thread_id':__THREAD__}), flush=True)
                print(json.dumps({'type':'item.started','item':{'id':'tool-1',
                    'type':'command_execution','command':'FAKE_VALUE'}}), flush=True)
                print(json.dumps({'type':'item.completed','item':{'id':'tool-1',
                    'type':'command_execution'}}), flush=True)
                print(json.dumps({'type':'item.completed','item':{'type':'agent_message',
                    'text':'Codex answer'}}), flush=True)
                print(json.dumps({'type':'turn.completed','usage':{'input_tokens':2,
                    'output_tokens':3}}), flush=True)
        ''')
        script = (script.replace('__INTERPRETER__', sys.executable)
                  .replace('__LOG__', repr(str(self.log)))
                  .replace('__THREAD__', repr(THREAD)).replace('__MODE__', repr(mode)))
        fake.write_text(script, encoding='utf-8')
        fake.chmod(0o700)

    def make_runtime(self, *, create=True, session=SESSION):
        if create:
            binding = pm_runtime.create_dedicated_pm(
                str(self.cwd), runtime='codex', state_root=self.state, environ=self.environ)
            session = binding['native_session_id']
        runtime = pm_runtime.PMRuntime(session, cwd=str(self.cwd), state_root=self.state,
                                       environ=self.environ)
        runtime.open()
        self.addCleanup(runtime.close)
        return runtime

    def test_first_and_resumed_send_share_codex_thread_and_event_shapes(self):
        runtime = self.make_runtime()
        first = list(runtime.send('first'))
        second = list(runtime.send('second'))
        self.assertEqual([event['type'] for event in first],
                         ['TOOL_USE', 'TOOL_RESULT', 'TEXT_DELTA', 'ASSISTANT_TEXT', 'TURN_END'])
        self.assertEqual(first[-1]['usage'], {'input_tokens': 2, 'output_tokens': 3})
        self.assertEqual(second[-1]['status'], 'COMPLETED')
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertNotIn('resume', calls[0]['args'])
        self.assertIn('resume', calls[1]['args'])
        self.assertIn(THREAD, calls[1]['args'])
        self.assertFalse(any(call['sentinel_present'] for call in calls))
        self.assertTrue(all('--sandbox' in call['args'] and
                            'workspace-write' in call['args'] and
                            '--ask-for-approval' in call['args'] and
                            'never' in call['args'] for call in calls))

    def test_malformed_output_and_process_exit_return_runtime_error_then_turn_end(self):
        for mode, code in (('malformed', 'PM_STREAM_INVALID'), ('exit', 'PM_PROCESS_EXITED')):
            with self.subTest(mode=mode):
                self.write_fake(mode)
                runtime = self.make_runtime()
                events = list(runtime.send('request'))
                self.assertEqual(events[-2]['type'], 'RUNTIME_ERROR')
                self.assertEqual(events[-2]['code'], code)
                self.assertEqual(events[-1]['type'], 'TURN_END')
                runtime.close()
                self.log.unlink(missing_ok=True)

    def test_interrupt_and_close_are_safe(self):
        self.write_fake('interrupt')
        runtime = self.make_runtime()
        events = runtime.send('request')
        runtime.interrupt()
        result = list(events)
        self.assertEqual(result[-1]['type'], 'TURN_END')
        self.assertEqual(result[-1]['status'], 'INTERRUPTED')

    def test_unbound_and_tui_originated_session_are_refused_before_cli(self):
        with self.assertRaises(LLMToolError):
            self.make_runtime(create=False)
        binding = pm_runtime.create_dedicated_pm(str(self.cwd), runtime='codex',
                                                 state_root=self.state, environ=self.environ)
        binding = pm_runtime.current_binding(state_root=self.state)
        binding['session_started'] = True
        pm_runtime._write_json_atomic(self.state,
            pm_runtime._binding_path(self.state, binding['native_session_id']), binding)
        runtime = pm_runtime.PMRuntime(binding['native_session_id'], cwd=str(self.cwd), state_root=self.state,
                                       environ=self.environ)
        with self.assertRaises(LLMToolError):
            runtime.open()
        self.assertFalse(self.log.exists())

    def test_busy_binding_is_refused_before_cli(self):
        binding = pm_runtime.create_dedicated_pm(str(self.cwd), runtime='codex',
                                                 state_root=self.state, environ=self.environ)
        key = 'pm-' + binding['native_session_id']
        lock = pm_runtime._check_state_file(self.state / (key + '.lock'), create=True)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            runtime = pm_runtime.PMRuntime(binding['native_session_id'], cwd=str(self.cwd), state_root=self.state,
                                           environ=self.environ)
            with self.assertRaises(LLMToolError):
                runtime.open()
            self.assertFalse(self.log.exists())
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)


if __name__ == '__main__':
    unittest.main()
