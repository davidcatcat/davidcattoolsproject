import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool import codex_direct_developer as direct
from llmtool import task_submission_cli
from llmtool.errors import LLMToolError
from llmtool.worker_dispatch import WorkerDispatchDenied
from llmtool.providers.codex_cli import CodexCLIProvider
from llmtool.cli import parser_for_cli


class CodexDirectDeveloperTests(unittest.TestCase):
    THREAD = '12345678-1234-4234-8234-123456789abc'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='llmtool-codex-direct-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.fake = self.bin / 'codex'
        self.invoked = self.root / 'invoked'
        self.final = {'status': 'DONE', 'summary': 'updated', 'tests': ['passed'],
                      'self_review': 'diff checked', 'blocked_reason': None}
        self.write_fake(self.events(self.final))
        self.path_patch = patch.dict(os.environ, {'PATH': str(self.bin) + os.pathsep + os.environ['PATH']})
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def events(self, final, *, thread_id=None):
        return '\n'.join((
            json.dumps({'type': 'thread.started', 'thread_id': thread_id or self.THREAD}),
            json.dumps({'type': 'item.completed', 'item': {
                'type': 'agent_message', 'text': json.dumps(final)}}),
            json.dumps({'type': 'turn.completed', 'usage': {
                'input_tokens': 13, 'cached_input_tokens': 2, 'output_tokens': 8}}),
            '',
        ))

    def write_fake(self, body, *, delay=0, delay_after_output=0, descendant_marker=None):
        script = "#!/usr/bin/env python3\nimport json,os,sys,time\n"
        script += f"open({str(self.invoked)!r},'w').write('yes')\n"
        script += (
            'expected_cache = "shell_environment_policy.set.PYTHONPYCACHEPREFIX=" + '
            'json.dumps(os.environ["TMPDIR"])\n'
            'assert expected_cache in sys.argv\n')
        if descendant_marker:
            script += (
                'pid=os.fork()\n'
                'if pid == 0:\n'
                ' time.sleep(1)\n'
                f" open({str(descendant_marker)!r},'w').write('survived')\n"
                ' os._exit(0)\n')
        if delay:
            script += f'time.sleep({delay!r})\n'
        script += f"sys.stdout.write({body!r});sys.stdout.flush()\n"
        if delay_after_output:
            script += f'time.sleep({delay_after_output!r})\n'
        self.fake.write_text(script, encoding='utf-8')
        self.fake.chmod(0o700)

    def run_fake(self, **kwargs):
        timeout = kwargs.pop('timeout', 3)
        kwargs.setdefault('prelaunch_validate', lambda: True)
        with patch('llmtool.codex_direct_developer.worker_dispatch.authorize_dispatch'):
            return direct.run(root=self.root, prompt='bounded prompt', model='gpt-6-luna',
                reasoning_effort='high', dispatch_store=object(), dispatch_task_id='a' * 32,
                timeout=timeout, **kwargs)

    def test_done_captures_thread_usage_and_hardening_arguments(self):
        result = self.run_fake()
        self.assertEqual((result.status, result.thread_id), ('DONE', self.THREAD))
        self.assertEqual(result.usage, {'prompt_tokens': 13, 'completion_tokens': 8,
                                        'total_tokens': 21})
        self.assertTrue(self.invoked.exists())
        argv = direct._argv('/fake/codex', self.root, 'gpt-6-luna', 'high',
                            pycache_prefix='/tmp/codex-direct-cache')
        self.assertIn('--sandbox', argv)
        self.assertIn('workspace-write', argv)
        self.assertIn('model_reasoning_effort=high', argv)
        self.assertIn('features.remote_plugin=false', argv)
        self.assertIn('shell_environment_policy.inherit="none"', argv)
        self.assertIn('shell_environment_policy.set.PYTHONDONTWRITEBYTECODE="1"', argv)
        self.assertIn('shell_environment_policy.set.PYTHONPYCACHEPREFIX="/tmp/codex-direct-cache"',
                      argv)

    def test_shell_commands_get_an_explicit_path_with_the_worktree_venv_first(self):
        # Regression (#352 G5): with inherit="none" and no PATH, every Codex
        # shell command failed with "command not found" (exit 127).
        argv = direct._argv('/fake/codex', self.root, 'gpt-6-luna', 'high',
                            pycache_prefix='/tmp/codex-direct-cache')
        self.assertIn('shell_environment_policy.set.PATH="/opt/homebrew/bin:/usr/local/bin:'
                      '/usr/bin:/bin"', argv)
        venv_bin = self.root / 'tools' / 'llmtool' / '.venv' / 'bin'
        venv_bin.mkdir(parents=True)
        argv = direct._argv('/fake/codex', self.root, 'gpt-6-luna', 'high',
                            pycache_prefix='/tmp/codex-direct-cache')
        self.assertIn('shell_environment_policy.set.PATH=' + json.dumps(
            str(venv_bin) + ':/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin'), argv)
        for bad in ('', 'relative/bin', '/bin:\nx'):
            with self.subTest(path=bad), self.assertRaises(direct.DirectCodexError):
                direct._argv('/fake/codex', self.root, 'gpt-6-luna', 'high',
                             pycache_prefix='/tmp/c', command_path=bad)

    def test_prelaunch_exception_is_typed_and_codex_is_not_started(self):
        self.invoked.unlink(missing_ok=True)
        def stale_binding():
            raise LLMToolError('D6 or Git identity changed before Codex launch')
        with self.assertRaises(direct.DirectCodexError) as caught:
            self.run_fake(prelaunch_validate=stale_binding)
        self.assertEqual(caught.exception.direct_codex_code, 'CODEX_D6_BINDING_CHANGED')
        self.assertIs(caught.exception.attempted, False)
        self.assertFalse(self.invoked.exists())

    def test_unexpected_exception_after_launch_keeps_class_code_and_facts(self):
        with patch.object(direct, '_result_from_events', side_effect=KeyError('secret')):
            with self.assertRaises(direct.DirectCodexError) as caught:
                self.run_fake()
        error = caught.exception
        self.assertEqual(error.direct_codex_code, 'CODEX_ADAPTER_KEY_ERROR')
        self.assertIs(error.attempted, True)
        self.assertEqual(error.process_exit_code, 0)
        self.assertEqual(error.native_session_id, self.THREAD)
        self.assertEqual(error.cleanup_status, 'CONFIRMED')
        self.assertNotIn('secret', str(error))

    def test_adapter_error_codes_are_stable_and_content_free(self):
        coded = LLMToolError('details omitted')
        coded.completion_code = 'D6_SOURCE_BINDING_MISMATCH'
        self.assertEqual(direct.adapter_error_code(coded),
                         'CODEX_ADAPTER_D6_SOURCE_BINDING_MISMATCH')
        self.assertEqual(direct.adapter_error_code(LLMToolError('/some/path')),
                         'CODEX_ADAPTER_LLMTOOL_ERROR')
        self.assertEqual(direct.adapter_error_code(OSError()), 'CODEX_ADAPTER_OSERROR')
        long_name = type('X' * 80 + 'Error', (Exception,), {})()
        self.assertRegex(direct.adapter_error_code(long_name), r'^[A-Z0-9_]{1,64}$')

    def test_blocked_final_is_recorded_as_blocked(self):
        final = dict(self.final, status='BLOCKED', blocked_reason='cannot safely edit')
        self.write_fake(self.events(final))
        self.assertEqual(self.run_fake().status, 'BLOCKED')

    def test_malformed_final_json_is_typed_failure(self):
        raw = json.dumps({'type': 'thread.started', 'thread_id': self.THREAD}) + '\n'
        raw += json.dumps({'type': 'item.completed', 'item': {
            'type': 'agent_message', 'text': '{broken'}}) + '\n'
        self.write_fake(raw)
        with self.assertRaises(direct.DirectCodexError) as caught:
            self.run_fake()
        self.assertEqual(caught.exception.direct_codex_code, 'CODEX_FINAL_JSON_INVALID')
        self.assertEqual(caught.exception.native_session_id, self.THREAD)

    def test_timeout_kills_owned_process_group(self):
        marker = self.root / 'descendant-survived'
        self.write_fake('', delay=10, descendant_marker=marker)
        began = time.monotonic()
        with self.assertRaises(direct.DirectCodexError) as caught:
            self.run_fake(timeout=0.2)
        self.assertEqual(caught.exception.direct_codex_code, 'CODEX_TIMEOUT')
        self.assertEqual(caught.exception.cleanup_status, 'CONFIRMED')
        self.assertLess(time.monotonic() - began, 3)
        time.sleep(1.1)
        self.assertFalse(marker.exists())

    def test_wait_timeout_keeps_observed_thread_usage_and_digest(self):
        self.write_fake(self.events(self.final), delay_after_output=2)
        with self.assertRaises(direct.DirectCodexError) as caught:
            self.run_fake(timeout=0.8)
        self.assertEqual(caught.exception.direct_codex_code, 'CODEX_TIMEOUT')
        self.assertEqual(caught.exception.native_session_id, self.THREAD)
        self.assertEqual(caught.exception.usage, {
            'prompt_tokens': 13, 'completion_tokens': 8, 'total_tokens': 21})
        self.assertRegex(caught.exception.stdout_sha256, r'^[0-9a-f]{64}$')

    def test_paused_dispatch_is_refused_before_codex_launch(self):
        self.invoked.unlink(missing_ok=True)
        def paused(*args, **kwargs):
            raise WorkerDispatchDenied('OWNER_PAUSED')
        with patch('llmtool.codex_direct_developer.worker_dispatch.authorize_dispatch', paused):
            with self.assertRaises(direct.DirectCodexError) as caught:
                direct.run(root=self.root, prompt='bounded', model='gpt-6-luna',
                    reasoning_effort='high', dispatch_store=object(),
                    dispatch_task_id='a' * 32, prelaunch_validate=lambda: True, timeout=3)
        self.assertEqual(caught.exception.direct_codex_code, 'OWNER_PAUSED')
        self.assertFalse(self.invoked.exists())

    def test_profile_missing_is_refused(self):
        with patch.object(task_submission_cli, 'resolve_profile_config',
                          side_effect=LLMToolError('missing')):
            with self.assertRaisesRegex(LLMToolError, 'codex-developer profile is missing'):
                task_submission_cli._codex_developer_profile()

    def test_out_of_allowlist_diff_gets_typed_rejection(self):
        diff = ('diff --git a/private.py b/private.py\n'
                '--- a/private.py\n+++ b/private.py\n@@ -1 +1 @@\n-x\n+y\n')
        self.assertEqual(task_submission_cli._developer_output_rejection(
            diff, ['allowed.py']), 'DEVELOPER_OUTPUT_OUT_OF_SCOPE')

    def test_invalid_thread_event_is_not_accepted(self):
        raw = self.events(self.final, thread_id='not-a-native-thread')
        self.write_fake(raw)
        with self.assertRaises(direct.DirectCodexError) as caught:
            self.run_fake()
        self.assertEqual(caught.exception.direct_codex_code, 'CODEX_THREAD_ID_INVALID')

    def test_thread_started_must_be_first_jsonl_event(self):
        body = json.dumps({'type': 'turn.started'}) + '\n' + self.events(self.final)
        self.write_fake(body)
        with self.assertRaises(direct.DirectCodexError) as caught:
            self.run_fake()
        self.assertEqual(caught.exception.direct_codex_code, 'CODEX_THREAD_ID_MISSING')

    def test_profile_reasoning_effort_is_validated_and_direct_cli_mode_parses(self):
        CodexCLIProvider.validate_options({'reasoning_effort': 'high'})
        with self.assertRaises(LLMToolError):
            CodexCLIProvider.validate_options({'reasoning_effort': 'unbounded'})
        args = parser_for_cli().parse_args([
            'task', 'delegate', 'a' * 32, '--provider', 'codex', '--mode', 'direct',
            '--selection-source', 'PM', '--executor-task', 'executor-1',
            '--read-path', 'src.py', '--write-path', 'src.py', '--acceptance', 'works',
            '--egress-basis', 'approved'])
        self.assertEqual(args.mode, 'direct')

    def test_prompt_contains_contract_bindings_paths_acceptance_tests_and_budget(self):
        prompt = task_submission_cli._codex_direct_prompt({
            'task_id': 'a' * 32, 'run_id': 'b' * 32,
            'task_binding': {'contract_digest': 'c' * 64},
            'task_contract': {'goal': 'bounded goal', 'criteria': [],
                              'test_commands': [['python3', '-m', 'unittest']]},
        }, source_sha='d' * 40, read_paths=['src.py'], write_paths=['src.py'],
            acceptance=['expected behavior'], model='gpt-6-luna',
            reasoning_effort='high')
        self.assertIn('"task_contract":{"criteria":[],"goal":"bounded goal"', prompt)
        self.assertIn('"task_id":"' + 'a' * 32 + '"', prompt)
        self.assertIn('"contract_digest":"' + 'c' * 64 + '"', prompt)
        self.assertIn('"allowed_write_paths":["src.py"]', prompt)
        self.assertIn('"acceptance":["expected behavior"]', prompt)
        self.assertIn('"test_commands":[["python3","-m","unittest"]]', prompt)
        self.assertIn('"wall_clock_seconds":600', prompt)


if __name__ == '__main__':
    unittest.main()
