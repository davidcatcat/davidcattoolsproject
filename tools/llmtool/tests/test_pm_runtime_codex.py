"""Contract tests for the per-turn Codex PM runtime adapter."""

import fcntl
import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmtool import pm_runtime
from llmtool.pm_runtime_codex import CodexRuntime
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
        self.account_dir = self.root / 'codex-account'
        self.account_dir.mkdir(mode=0o700)
        config = self.root / '.config/llmtool/config.yaml'
        config.parent.mkdir(parents=True)
        config.write_text('accounts:\n  codex-pm:\n    kind: codex\n    config_dir: ' +
                          str(self.account_dir) + '\n', encoding='utf-8')

    def write_fake(self, mode='ok', usages=None):
        if usages is None:
            usages = [{'input_tokens': 2, 'output_tokens': 3}]
        fake = self.bin / 'codex'
        script = textwrap.dedent('''\
            #!__INTERPRETER__
            import json, os, pathlib, sys, time
            args = sys.argv[1:]
            if 'exec' in args:
                exec_index = args.index('exec')
                if any(flag in args[exec_index:] for flag in ('--cd', '--sandbox')):
                    sys.exit(2)
                if (not (pathlib.Path.cwd() / '.git').exists() and
                        '--skip-git-repo-check' not in args[exec_index:]):
                    sys.exit(2)
            with open(__LOG__, 'a', encoding='utf-8') as stream:
                stream.write(json.dumps({'args': args,
                    'sentinel_present': 'FAKE_VALUE' in os.environ}) + '\\n')
            home = pathlib.Path(os.environ.get('CODEX_HOME') or os.environ['HOME'] + '/.codex')
            sessions = home / 'sessions'
            sessions.mkdir(parents=True, exist_ok=True)
            session = sessions / ('0000-' + __THREAD__ + '.jsonl')
            session.write_text(json.dumps({'type':'session_meta','payload':
                {'id':__THREAD__,'cwd':os.getcwd()}}) + '\\n', encoding='utf-8')
            usage_values = __USAGES__
            counter_path = pathlib.Path(__COUNTER__)
            try:
                usage_index = int(counter_path.read_text(encoding='utf-8'))
            except (FileNotFoundError, ValueError):
                usage_index = 0
            counter_path.write_text(str(usage_index + 1), encoding='utf-8')
            usage = usage_values[min(usage_index, len(usage_values) - 1)]
            if __MODE__ == 'malformed':
                print('{', flush=True)
                sys.exit(3)
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
                print(json.dumps({'type':'turn.completed','usage':usage}), flush=True)
        ''')
        script = (script.replace('__INTERPRETER__', sys.executable)
                  .replace('__LOG__', repr(str(self.log)))
                  .replace('__THREAD__', repr(THREAD)).replace('__MODE__', repr(mode))
                  .replace('__USAGES__', repr(usages))
                  .replace('__COUNTER__', repr(str(self.root / 'usage-counter'))))
        fake.write_text(script, encoding='utf-8')
        fake.chmod(0o700)

    def make_runtime(self, *, create=True, session=SESSION):
        if create:
            binding = pm_runtime.create_dedicated_pm(
                str(self.cwd), runtime='codex', account='codex-pm', state_root=self.state,
                environ=self.environ, home=str(self.root))
            session = binding['native_session_id']
        runtime = pm_runtime.PMRuntime(session, cwd=str(self.cwd), state_root=self.state,
                                       environ=self.environ)
        runtime.open()
        self.addCleanup(runtime.close)
        return runtime

    def test_first_and_resumed_send_share_codex_thread_and_event_shapes(self):
        self.assertFalse((self.cwd / '.git').exists())
        runtime = self.make_runtime()
        first = list(runtime.send('first'))
        second = list(runtime.send('second'))
        self.assertEqual([event['type'] for event in first],
                         ['TOOL_USE', 'TOOL_RESULT', 'TEXT_DELTA', 'ASSISTANT_TEXT', 'TURN_END'])
        self.assertEqual(first[-1]['status'], 'COMPLETED')
        self.assertEqual(first[-1]['usage'], {'input_tokens': 2, 'output_tokens': 3})
        self.assertEqual(second[-1]['status'], 'COMPLETED')
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertNotIn('resume', calls[0]['args'])
        self.assertIn('resume', calls[1]['args'])
        self.assertIn(THREAD, calls[1]['args'])
        self.assertIn('--skip-git-repo-check', calls[0]['args'])
        self.assertIn('--skip-git-repo-check', calls[1]['args'])
        expected_common = ['--cd', str(self.cwd), '--sandbox', 'workspace-write',
                           '--no-daemon', '--ask-for-approval', 'never', 'exec']
        self.assertEqual(calls[0]['args'], expected_common + [
            '--skip-git-repo-check', '--json', '-c',
            'sandbox_workspace_write.network_access=true', '-'])
        self.assertEqual(calls[1]['args'], expected_common + [
            'resume', THREAD, '--skip-git-repo-check', '--json', '-c',
            'sandbox_workspace_write.network_access=true', '-'])
        self.assertFalse(any(call['sentinel_present'] for call in calls))
        allowed_env = {'PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'LC_CTYPE',
                       'SSL_CERT_FILE', 'SSL_CERT_DIR', 'CODEX_HOME',
                       'GH_CONFIG_DIR', 'GIT_CONFIG_COUNT',
                       'GIT_CONFIG_KEY_0', 'GIT_CONFIG_VALUE_0',
                       'GIT_CONFIG_KEY_1', 'GIT_CONFIG_VALUE_1',
                       'GIT_CONFIG_KEY_2', 'GIT_CONFIG_VALUE_2'}
        env = runtime._codex_driver._environment()
        self.assertLessEqual(set(env), allowed_env)
        self.assertNotIn('FAKE_VALUE', env)
        self.assertIn('CODEX_HOME', env)
        self.assertTrue(all('--sandbox' in call['args'] and
                            'workspace-write' in call['args'] and
                            '--ask-for-approval' in call['args'] and
                            'never' in call['args'] for call in calls))

    def test_fresh_and_resumed_turns_store_only_their_own_usage(self):
        self.write_fake(usages=[
            {'input_tokens': 10, 'cached_input_tokens': 4, 'output_tokens': 3},
            {'input_tokens': 25, 'cached_input_tokens': 9, 'output_tokens': 8},
            {'input_tokens': 40, 'cached_input_tokens': 15, 'output_tokens': 12},
        ])
        runtime = self.make_runtime()
        first = list(runtime.send('first'))[-1]
        second = list(runtime.send('second'))[-1]
        third = list(runtime.send('third'))[-1]
        self.assertEqual(first['usage'],
                         {'input_tokens': 10, 'cached_input_tokens': 4, 'output_tokens': 3})
        self.assertEqual(second['usage'],
                         {'input_tokens': 15, 'cached_input_tokens': 5, 'output_tokens': 5})
        self.assertEqual(third['usage'],
                         {'input_tokens': 15, 'cached_input_tokens': 6, 'output_tokens': 4})
        turns = pm_runtime.read_history(runtime.pm_session_id,
                                        state_root=self.state)['turns']
        self.assertEqual(turns[-1]['usage'], third['usage'])

    def test_resumed_turn_usage_survives_runtime_restart(self):
        self.write_fake(usages=[
            {'input_tokens': 10, 'output_tokens': 3},
            {'input_tokens': 22, 'output_tokens': 9},
            {'input_tokens': 30, 'output_tokens': 14},
        ])
        runtime = self.make_runtime()
        first = list(runtime.send('first'))[-1]
        second = list(runtime.send('second'))[-1]
        self.assertEqual(first['usage'], {'input_tokens': 10, 'output_tokens': 3})
        self.assertEqual(second['usage'], {'input_tokens': 12, 'output_tokens': 6})
        runtime.close()
        restarted = pm_runtime.PMRuntime(runtime.pm_session_id, cwd=str(self.cwd),
                                         state_root=self.state, environ=self.environ)
        restarted.open()
        self.addCleanup(restarted.close)
        third = list(restarted.send('third'))[-1]
        self.assertEqual(third['usage'], {'input_tokens': 8, 'output_tokens': 5})

    def test_resumed_turn_without_earlier_total_records_no_usage(self):
        self.write_fake(usages=[
            {'input_tokens': 10, 'output_tokens': 3},
            {'input_tokens': 22, 'output_tokens': 9},
            {'input_tokens': 30, 'output_tokens': 14},
        ])
        runtime = self.make_runtime()
        list(runtime.send('first'))
        state_path = pm_runtime._runtime_state_path(runtime._root, runtime._key)
        state = json.loads(state_path.read_text(encoding='utf-8'))
        state.pop('codex_usage_total', None)
        state.pop('codex_usage_thread_id', None)
        pm_runtime._write_json_atomic(runtime._root, state_path, state)
        runtime.close()
        restarted = pm_runtime.PMRuntime(runtime.pm_session_id, cwd=str(self.cwd),
                                         state_root=self.state, environ=self.environ)
        restarted.open()
        self.addCleanup(restarted.close)
        second = list(restarted.send('second'))[-1]
        third = list(restarted.send('third'))[-1]
        self.assertEqual(second['usage'], {})
        self.assertEqual(third['usage'], {'input_tokens': 8, 'output_tokens': 5})

    def test_unusable_resumed_usage_invalidates_baseline_across_restart(self):
        self.write_fake(usages=[
            {'input_tokens': 10, 'output_tokens': 3},
            None,
            {'input_tokens': 30, 'output_tokens': 14},
            {'input_tokens': 45, 'output_tokens': 20},
        ])
        runtime = self.make_runtime()
        first = list(runtime.send('first'))[-1]
        second = list(runtime.send('second'))[-1]
        self.assertEqual(first['usage'], {'input_tokens': 10, 'output_tokens': 3})
        self.assertEqual(second['usage'], {})
        runtime.close()
        restarted = pm_runtime.PMRuntime(runtime.pm_session_id, cwd=str(self.cwd),
                                         state_root=self.state, environ=self.environ)
        restarted.open()
        self.addCleanup(restarted.close)
        third = list(restarted.send('third'))[-1]
        fourth = list(restarted.send('fourth'))[-1]
        # The turn after the unusable total must stay unknown, not become
        # 30 - 10 (which would attribute the missing turn's tokens here).
        self.assertEqual(third['usage'], {})
        self.assertEqual(fourth['usage'], {'input_tokens': 15, 'output_tokens': 6})
        turns = pm_runtime.read_history(restarted.pm_session_id,
                                        state_root=self.state)['turns']
        self.assertEqual(turns[1]['usage'], {})
        self.assertEqual(turns[2]['usage'], {})
        self.assertEqual(turns[3]['usage'], fourth['usage'])

    def test_malformed_output_and_process_exit_return_runtime_error_then_turn_end(self):
        expected_codes = {
            'malformed': ['PM_STREAM_INVALID'],
            'exit': ['PM_PROCESS_EXITED'],
        }
        for mode, expected in expected_codes.items():
            with self.subTest(mode=mode):
                self.write_fake(mode)
                runtime = self.make_runtime()
                events = list(runtime.send('request'))
                errors = [event['code'] for event in events
                          if event['type'] == 'RUNTIME_ERROR']
                self.assertEqual(errors, expected)
                self.assertEqual(events[-2]['type'], 'RUNTIME_ERROR')
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
                                                 account='codex-pm', state_root=self.state,
                                                 environ=self.environ, home=str(self.root))
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
                                                 account='codex-pm', state_root=self.state,
                                                 environ=self.environ, home=str(self.root))
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

    def test_interactive_resume_session_is_live_elsewhere(self):
        with patch('llmtool.pm_runtime_codex.subprocess.check_output',
                   return_value=b'123 codex resume ' + THREAD.encode() + b'\n'):
            self.assertTrue(CodexRuntime._live_elsewhere(THREAD))


if __name__ == '__main__':
    unittest.main()
