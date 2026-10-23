import io
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from llmtool.cli import main, parser_for_cli
from llmtool.errors import LLMToolError, coded_error
from llmtool.providers.base import Reply
from llmtool.task_store import MAX_RECORD_BYTES, TaskStore
from llmtool.task_submission_cli import (REQUEST_LIMIT, RESPONSE_LIMIT,
                                         _provider_route_binding, run_task_delegate,
                                         run_task_opencode_delegate,
                                         run_task_record_pm, run_task_show,
                                         run_task_submit)
from llmtool.opencode_direct_developer import DirectOpenCodeError, DirectResult


def config(provider_type='codex-cli', model='gpt-6-luna'):
    return {'active_provider': 'selected', 'providers': {
        'selected': {'type': provider_type, 'model': model,
                     'timeout_seconds': 30, 'provider_options': {}}
    }, 'generation': {'temperature': 0, 'max_tokens': 128}}


class FakeClient:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def complete(self, messages):
        self.calls.append(messages)
        if self.error:
            raise self.error
        return self.reply


class TaskSubmissionCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve() / 'home'
        self.home.mkdir(mode=0o700)
        self.args = Namespace(provider='selected', request=['Implement', 'a', 'health-check'])
        self.executor_root = Path(self.temp.name).resolve() / 'executor'
        self.executor_root.mkdir(mode=0o700)
        subprocess.run(['/usr/bin/git', 'init', '-b', 'feat/test-executor'],
                       cwd=self.executor_root, check=True, capture_output=True)
        subprocess.run(['/usr/bin/git', 'config', 'user.name', 'Executor Fixture'],
                       cwd=self.executor_root, check=True)
        subprocess.run(['/usr/bin/git', 'config', 'user.email', 'executor@example.invalid'],
                       cwd=self.executor_root, check=True)
        (self.executor_root / 'sample.py').write_text('VALUE = 1\n')
        subprocess.run(['/usr/bin/git', 'add', 'sample.py'], cwd=self.executor_root, check=True)
        subprocess.run(['/usr/bin/git', 'commit', '-m', 'fixture source'],
                       cwd=self.executor_root, check=True, capture_output=True)
        self.executor_head = subprocess.check_output(
            ['/usr/bin/git', 'rev-parse', 'HEAD'], cwd=self.executor_root, text=True).strip()
        self.executor_common_dir = str(self.executor_root / '.git')
        registry_dir = Path(self.executor_common_dir) / 'llmtool-worktrees'
        registry_dir.mkdir(mode=0o700)
        root_stat = self.executor_root.stat()
        self.executor_registry = {
            'schema_version': 1, 'task_id': 'executor-task-1',
            'trusted_agent_id': 'executor-agent-1', 'session_ref': 'Executor Session 1',
            'root_realpath': str(self.executor_root),
            'physical_identity': {'device': root_stat.st_dev, 'inode': root_stat.st_ino,
                                  'uid': root_stat.st_uid},
            'git_common_dir': self.executor_common_dir, 'branch': 'feat/test-executor',
            'base_sha': self.executor_head, 'head_sha': self.executor_head,
            'state': 'ACTIVE', 'lock_state': 'LOCKED',
        }
        record_path = registry_dir / 'executor-task-1.json'
        record_path.write_text(json.dumps(self.executor_registry))
        record_path.chmod(0o600)
        original_cwd = os.getcwd()
        self.addCleanup(os.chdir, original_cwd)
        os.chdir(self.executor_root)
        self.bootstrap_patch = patch(
            'llmtool.task_submission_cli._bootstrap_registered_d6')
        self.bootstrap_patch.start()
        self.addCleanup(self.bootstrap_patch.stop)

    def invoke(self, *, resolver=None, client=None, key=None):
        out = io.StringIO()
        resolver = (lambda profile, require_configured: config()) if resolver is None else resolver
        client = FakeClient(Reply('PM received the request; next I will shape a bounded task.',
                                  {'prompt_tokens': 10, 'completion_tokens': 8,
                                   'total_tokens': 18})) if client is None else client
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.resolve_profile_config', side_effect=resolver) as resolve, \
                patch('llmtool.task_submission_cli.api_key', return_value=key), \
                patch('llmtool.task_submission_cli.create_client', return_value=client), \
                redirect_stdout(out):
            code = run_task_submit(self.args)
        return code, json.loads(out.getvalue()), client, resolve

    def test_direct_submit_persists_logical_binding_real_reply_and_usage(self):
        code, result, client, resolve = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(result['state'], 'PM_RESPONDED')
        self.assertTrue(result['task_id'])
        self.assertTrue(result['run_id'])
        self.assertTrue(result['pm_session_id'].startswith('pm-'))
        self.assertEqual(result['pm_provider_profile'], 'selected')
        self.assertEqual(result['pm_provider_type'], 'codex-cli')
        self.assertEqual(result['pm_route_binding'], {
            'type': 'codex-cli', 'model': 'gpt-6-luna', 'base_url': None})
        self.assertEqual(result['backend_session_semantics'], 'EPHEMERAL_SINGLE_CALL')
        self.assertIsNone(result['native_session_id'])
        self.assertEqual(result['response'], client.reply.content)
        self.assertEqual(result['usage'], {'prompt_tokens': 10,
                                           'completion_tokens': 8, 'total_tokens': 18})
        self.assertEqual(result['usage_status'], 'REPORTED')
        self.assertEqual(resolve.call_args.args, ('selected',))
        self.assertEqual(len(client.calls), 1)
        self.assertIn('does not dispatch a Developer', client.calls[0][0]['content'])
        self.assertIn('exactly one raw JSON object', client.calls[0][0]['content'])
        self.assertIn('no Markdown fences', client.calls[0][0]['content'])
        self.assertIn('no prose before or after it', client.calls[0][0]['content'])
        saved = TaskStore(Path(self.home) / '.local/state/llmtool/tasks').get(result['task_id'])
        self.assertEqual(saved['response'], client.reply.content)
        self.assertNotIn('api_key', json.dumps(saved).lower())

    def test_submit_and_show_can_write_to_explicit_streams(self):
        submit_output = io.StringIO()
        show_output = io.StringIO()
        process_stdout = io.StringIO()
        client = FakeClient(Reply('PM intake recorded.', {}))
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.resolve_profile_config',
                      return_value=config()), \
                patch('llmtool.task_submission_cli.api_key', return_value=None), \
                patch('llmtool.task_submission_cli.create_client', return_value=client), \
                redirect_stdout(process_stdout):
            code = run_task_submit(self.args, stdout=submit_output)
            submitted = json.loads(submit_output.getvalue())
            shown_code = run_task_show(Namespace(task_id=submitted['task_id']),
                                       stdout=show_output)
        self.assertEqual((code, shown_code), (0, 0))
        self.assertEqual(json.loads(show_output.getvalue())['task_id'], submitted['task_id'])
        self.assertEqual(process_stdout.getvalue(), '')

    def test_rejected_pm_response_uses_explicit_output_stream(self):
        task_output = io.StringIO()
        process_stdout = io.StringIO()
        client = FakeClient(Reply('Bearer abcdefghijklmnop', {}))
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.resolve_profile_config',
                      return_value=config()), \
                patch('llmtool.task_submission_cli.api_key', return_value=None), \
                patch('llmtool.task_submission_cli.create_client', return_value=client), \
                redirect_stdout(process_stdout):
            code = run_task_submit(self.args, stdout=task_output)
        result = json.loads(task_output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(result['state'], 'PM_RESPONSE_REJECTED')
        self.assertEqual(result['error_code'], 'PM_RESPONSE_REJECTED')
        self.assertIsNone(result['response'])
        self.assertEqual(process_stdout.getvalue(), '')

    def test_pm_route_binding_normalizes_endpoint_and_omits_credentials(self):
        route = _provider_route_binding({
            'type': 'OPENAI-COMPATIBLE', 'model': 'pm-model',
            'base_url': 'HTTPS://API.EXAMPLE:443/'})
        self.assertEqual(route, {'type': 'openai-compatible', 'model': 'pm-model',
                                 'base_url': 'https://api.example'})
        self.assertNotIn('api_key', json.dumps(route).lower())
        self.assertEqual(_provider_route_binding({
            'type': 'codex-cli', 'model': 'gpt-6-luna', 'base_url': None
        })['base_url'], None)
        with self.assertRaises(LLMToolError):
            _provider_route_binding({'type': 'openai-compatible', 'model': 'pm-model',
                                     'base_url': 'https://user:pass@api.example'})

    def test_usage_unknown_is_recorded_without_gating_success(self):
        client = FakeClient(Reply('PM response without reported usage.', {}))
        code, result, _, _ = self.invoke(client=client)
        self.assertEqual(code, 0)
        self.assertEqual(result['state'], 'PM_RESPONDED')
        self.assertIsNone(result['usage'])
        self.assertEqual(result['usage_status'], 'UNKNOWN')

    def test_large_unicode_request_and_reply_fit_the_bounded_record(self):
        large_text = '🙂' * 16000
        self.args.request = [large_text]
        client = FakeClient(Reply(large_text, {}))
        code, result, _, _ = self.invoke(client=client)
        self.assertEqual(code, 0)
        self.assertEqual(result['state'], 'PM_RESPONDED')
        path = Path(self.home) / '.local/state/llmtool/tasks' / (result['task_id'] + '.json')
        raw = path.read_bytes()
        self.assertLessEqual(len(raw), MAX_RECORD_BYTES)
        self.assertIn(large_text.encode('utf-8')[:4], raw)
        saved = TaskStore(Path(self.home) / '.local/state/llmtool/tasks').get(result['task_id'])
        self.assertEqual(saved['request'], large_text)
        self.assertEqual(saved['response'], large_text)

    def test_maximum_json_escaping_from_accepted_text_round_trips(self):
        escaped_unit = '"\\\t\n\r'
        text = escaped_unit * (REQUEST_LIMIT // len(escaped_unit))
        text += '"' * (REQUEST_LIMIT - len(text.encode('utf-8')))
        self.assertEqual(len(text.encode('utf-8')), REQUEST_LIMIT)
        self.assertEqual(REQUEST_LIMIT, RESPONSE_LIMIT)
        self.args.request = [text]
        client = FakeClient(Reply(text, {}))
        code, result, _, _ = self.invoke(client=client)
        self.assertEqual(code, 0)
        self.assertEqual(result['state'], 'PM_RESPONDED')
        path = Path(self.home) / '.local/state/llmtool/tasks' / (result['task_id'] + '.json')
        raw = path.read_bytes()
        self.assertLessEqual(len(raw), MAX_RECORD_BYTES)
        saved = TaskStore(Path(self.home) / '.local/state/llmtool/tasks').get(result['task_id'])
        self.assertEqual(saved['request'], text)
        self.assertEqual(saved['response'], text)

    def test_later_cli_process_retrieves_original_task_and_response(self):
        with patch.dict(os.environ, {'HOME': str(self.home)}):
            code, result, _, _ = self.invoke()
        self.assertEqual(code, 0)
        package_root = Path(__file__).resolve().parents[1]
        env = dict(os.environ)
        env['HOME'] = str(self.home)
        env['PYTHONPATH'] = str(package_root)
        process = subprocess.run(
            [sys.executable, '-B', '-m', 'llmtool', 'task', 'show', result['task_id']],
            cwd=package_root, env=env, text=True, capture_output=True, timeout=15,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        reopened = json.loads(process.stdout)
        self.assertEqual(reopened['task_id'], result['task_id'])
        self.assertEqual(reopened['run_id'], result['run_id'])
        self.assertEqual(reopened['pm_session_id'], result['pm_session_id'])
        self.assertEqual(reopened['response'], result['response'])

    def test_unconfigured_profile_is_persisted_as_pm_unavailable_without_fallback(self):
        def missing(profile, require_configured):
            raise LLMToolError('private config detail')
        code, result, client, resolve = self.invoke(resolver=missing)
        self.assertEqual(code, 1)
        self.assertEqual(result['state'], 'PM_UNAVAILABLE')
        self.assertEqual(result['error_code'], 'PM_PROVIDER_NOT_CONFIGURED')
        self.assertIsNone(result['response'])
        self.assertEqual(len(client.calls), 0)
        resolve.assert_called_once_with('selected', require_configured=True)
        stored = TaskStore(Path(self.home) / '.local/state/llmtool/tasks').get(result['task_id'])
        self.assertEqual(stored['state'], 'PM_UNAVAILABLE')
        self.assertNotIn('private config detail', json.dumps(stored))

    def test_authentication_failure_is_persisted_without_credential_or_message(self):
        auth_error = coded_error('secret auth body', 'AUTH_CREDENTIAL_MISSING', 'config.auth')
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.resolve_profile_config', return_value=config()), \
                patch('llmtool.task_submission_cli.api_key', side_effect=auth_error), \
                patch('llmtool.task_submission_cli.create_client') as create, \
                redirect_stdout(io.StringIO()) as out:
            code = run_task_submit(self.args)
        result = json.loads(out.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(result['state'], 'PM_UNAVAILABLE')
        self.assertEqual(result['error_code'], 'AUTH_CREDENTIAL_MISSING')
        self.assertNotIn('secret auth body', out.getvalue())
        create.assert_not_called()

    def test_timeout_persists_unavailable_state_and_error_code(self):
        client = FakeClient(error=coded_error('private timeout detail',
                                               'PROVIDER_TIMEOUT', 'provider.test'))
        code, result, _, _ = self.invoke(client=client)
        self.assertEqual(code, 1)
        self.assertEqual(result['state'], 'PM_UNAVAILABLE')
        self.assertEqual(result['error_code'], 'PROVIDER_TIMEOUT')
        self.assertNotIn('private timeout detail', json.dumps(result))

    def test_secret_request_is_rejected_without_provider_call_or_persistence(self):
        secret = 'api_key=sk-abcdefghijklmnopqrstuvwxyz'
        secret_digest = __import__('hashlib').sha256(secret.encode()).hexdigest()
        self.args.request = [secret]
        code, result, client, _ = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(result['state'], 'INPUT_REJECTED')
        self.assertIsNone(result['request'])
        self.assertIsNone(result['request_sha256'])
        self.assertNotIn('sk-abcdefghijklmnopqrstuvwxyz', json.dumps(result))
        self.assertNotIn(secret_digest, json.dumps(result))
        self.assertEqual(client.calls, [])
        path = Path(self.home) / '.local/state/llmtool/tasks' / (result['task_id'] + '.json')
        persisted = path.read_text()
        self.assertNotIn('sk-abcdefghijklmnopqrstuvwxyz', persisted)
        self.assertNotIn(secret_digest, persisted)

    def test_secret_provider_response_is_rejected_without_persistence(self):
        client = FakeClient(Reply('Bearer abcdefghijklmnop', {}))
        code, result, _, _ = self.invoke(client=client)
        self.assertEqual(code, 1)
        self.assertEqual(result['state'], 'PM_RESPONSE_REJECTED')
        self.assertEqual(result['error_code'], 'PM_RESPONSE_REJECTED')
        self.assertIsNone(result['response'])
        self.assertNotIn('abcdefghijklmnop', json.dumps(result))

    def test_show_rejects_malformed_and_unknown_task_ids(self):
        out = io.StringIO()
        with patch.dict(os.environ, {'HOME': str(self.home)}):
            for task_id in ('../outside', '0' * 32):
                with self.subTest(task_id=task_id), redirect_stdout(out), \
                        self.assertRaises(LLMToolError):
                    run_task_show(Namespace(task_id=task_id))

    def test_parser_requires_explicit_provider_and_has_lookup_command(self):
        parser = parser_for_cli()
        with self.assertRaises(SystemExit) as raised:
            parser.parse_args(['task', 'submit', 'build something'])
        self.assertEqual(raised.exception.code, 2)
        args = parser.parse_args(['task', 'submit', '--provider', 'company-gw', 'build something'])
        self.assertEqual((args.action, args.provider, args.request),
                         ('submit', 'company-gw', ['build something']))
        shown = parser.parse_args(['task', 'show', 'a' * 32])
        self.assertEqual((shown.action, shown.task_id), ('show', 'a' * 32))
        delegate = next(action for action in parser._actions
                        if action.dest == 'command').choices['task']._subparsers._group_actions[0]
        options = [option for action in delegate.choices['delegate']._actions
                   for option in action.option_strings]
        self.assertIn('--executor-task', options)
        self.assertNotIn('--source-sha', options)

    def test_record_pm_is_strict_offline_and_persists_truthful_provenance(self):
        request = 'Implement a small, isolated OpenCode adapter test.'
        response = json.dumps({
            'schema_version': 1, 'summary': 'A bounded low-risk test task.',
            'developer_assignment': {
                'route': 'opencode', 'risk': 'LOW',
                'goal': 'Add one focused offline regression test.',
                'acceptance': ['The focused test passes.'],
            },
        })
        envelope = json.dumps({'request': request, 'response': response})
        output = io.StringIO()
        store = TaskStore(self.home / '.local/state/llmtool/tasks')
        with patch('llmtool.task_submission_cli.resolve_profile_config',
                   side_effect=AssertionError('provider resolution must not run')), \
                patch('llmtool.task_submission_cli.api_key',
                      side_effect=AssertionError('credentials must not be read')), \
                patch('llmtool.task_submission_cli.create_client',
                      side_effect=AssertionError('provider client must not be created')), \
                patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('sys.stdin', io.StringIO(envelope)), \
                redirect_stdout(output):
            code = main(['task', 'record-pm', '--stdin-json'])
        result = json.loads(output.getvalue())
        saved = store.get(result['task_id'])
        self.assertEqual(code, 0)
        self.assertEqual(result, saved | {'usage_status': 'UNKNOWN'})
        self.assertEqual(saved['state'], 'PM_RESPONDED')
        self.assertNotEqual(saved['task_id'], saved['run_id'])
        self.assertEqual(saved['task_binding']['task_id'], saved['task_id'])
        self.assertEqual(saved['task_binding']['run_id'], saved['run_id'])
        self.assertEqual(saved['request'], request)
        self.assertEqual(saved['request_sha256'], hashlib.sha256(request.encode()).hexdigest())
        self.assertEqual(saved['response'], response)
        self.assertEqual(saved['pm_assignment']['route'], 'opencode')
        self.assertEqual(saved['pm_assignment']['risk'], 'LOW')
        self.assertEqual(saved['pm_provider_profile'], 'active-pm-runtime')
        self.assertEqual(saved['pm_provider_type'], 'codex-cli')
        self.assertIsNone(saved['pm_model'])
        self.assertEqual(saved['pm_route_binding'], {
            'type': 'codex-cli', 'model': None, 'base_url': None})
        self.assertEqual(saved['backend_session_semantics'],
                         'ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL')
        self.assertIsNone(saved['native_session_id'])
        self.assertIsNone(saved['usage'])
        self.assertEqual(saved['pm_provenance'], {
            'source': 'ACTIVE_PM_RUNTIME', 'provider_invoked': False,
            'native_session_id_status': 'UNAVAILABLE', 'usage_status': 'UNKNOWN',
            'session_restored': False, 'automatic_callback': False,
        })
        parsed = parser_for_cli().parse_args(['task', 'record-pm', '--stdin-json'])
        self.assertEqual(parsed.action, 'record-pm')
        output = io.StringIO()
        with patch.dict(os.environ, {'HOME': str(self.home)}), redirect_stdout(output):
            self.assertEqual(main(['task', 'show', saved['task_id']]), 0)
        self.assertEqual(json.loads(output.getvalue())['pm_provenance'], saved['pm_provenance'])

    def test_record_pm_rejects_malformed_or_sensitive_input_without_task(self):
        good_response = json.dumps({
            'schema_version': 1, 'summary': 'Bounded.',
            'developer_assignment': {
                'route': 'opencode', 'risk': 'LOW', 'goal': 'Do one task.',
                'acceptance': ['A test passes.'],
            },
        })
        cases = (
            json.dumps({'request': 'Build feature', 'response': '{bad json'}),
            ('{"request":"Build feature","response":' + json.dumps(good_response) +
             ',"request":"Duplicate"}'),
            json.dumps({'request': 'api_key=sk-abcdefghijklmnopqrstuvwxyz',
                        'response': good_response}),
            json.dumps({'request': 'Build feature',
                        'response': 'Bearer abcdefghijklmnop'}),
            json.dumps({'request': 'Build feature', 'response': json.dumps({
                'schema_version': 1, 'summary': 'Duplicate fields.',
                'developer_assignment': {'route': 'opencode', 'route': 'codex',
                    'risk': 'LOW', 'goal': 'Do one task.',
                    'acceptance': ['A test passes.']},
            })}),
        )
        for raw in cases:
            with self.subTest(raw=raw[:40]):
                store = TaskStore(Path(self.temp.name).resolve() /
                                  ('rejected-' + str(cases.index(raw))))
                with self.assertRaises(LLMToolError):
                    run_task_record_pm(Namespace(stdin_json=True), store=store,
                                       input_stream=io.StringIO(raw))
                self.assertFalse(store.root.exists())

    def _pm_task_with_assignment(self):
        acceptance = ['new test passes']
        response = json.dumps({
            'schema_version': 1, 'summary': 'Bounded test-only task.',
            'developer_assignment': {
                'route': 'company-gw', 'risk': 'LOW', 'goal': 'Add one focused test.',
                'acceptance': acceptance,
            },
        })
        code, task, _, _ = self.invoke(client=FakeClient(Reply(response, {
            'prompt_tokens': 12, 'completion_tokens': 18, 'total_tokens': 30,
        })))
        self.assertEqual(code, 0)
        return task

    def _pm_task_with_opencode_assignment(self):
        acceptance = ['focused change is present']
        response = json.dumps({
            'schema_version': 1, 'summary': 'Bounded low-risk task.',
            'developer_assignment': {
                'route': 'opencode', 'risk': 'LOW', 'goal': 'Update one fixture value.',
                'acceptance': acceptance,
            },
        })
        code, task, _, _ = self.invoke(client=FakeClient(Reply(response, {
            'prompt_tokens': 12, 'completion_tokens': 18, 'total_tokens': 30,
        })))
        self.assertEqual(code, 0)
        return task

    def _opencode_delegate_args(self, task, **changes):
        values = dict(task_id=task['task_id'], provider='opencode',
                      selection_source='PM', executor_task='executor-task-1',
                      read_path=['sample.py'], write_path=['sample.py'],
                      acceptance=['focused change is present'],
                      egress_basis='Owner approved non-sensitive source files')
        values.update(changes)
        return Namespace(**values)

    def test_opencode_direct_route_records_scoped_diff_and_cli_evidence(self):
        task = self._pm_task_with_opencode_assignment()
        output = io.StringIO()

        def execute(**kwargs):
            self.assertEqual(kwargs['root'], str(self.executor_root))
            self.assertEqual(kwargs['read_paths'], ['sample.py'])
            self.assertEqual(kwargs['write_paths'], ['sample.py'])
            self.assertIn(task['task_id'], kwargs['prompt'])
            (self.executor_root / 'sample.py').write_text('VALUE = 2\n')
            return DirectResult(0, 'ses_Abc123', 'Updated fixture.',
                {'prompt_tokens': 18, 'completion_tokens': 9, 'total_tokens': 27},
                'CLI_REPORTED', 'a' * 64, 1234, 'CONFIRMED', 'opencode v2.0.32',
                10, 1, 'b' * 64)

        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.opencode_direct_developer.run',
                      side_effect=execute) as invoke, \
                redirect_stdout(output):
            code = run_task_delegate(self._opencode_delegate_args(task))
        self.assertEqual(code, 1, output.getvalue())
        result = json.loads(output.getvalue())
        developer = result['developer']
        self.assertEqual(developer['provider_profile'], 'opencode')
        self.assertEqual(developer['provider_type'], 'opencode-cli')
        self.assertEqual(developer['model'], 'opencode/deepseek-v4.1-flash-free')
        self.assertEqual(developer['execution']['native_session_id'], 'ses_Abc123')
        self.assertEqual(developer['execution']['process_exit_code'], 0)
        self.assertEqual(developer['execution']['usage_source'], 'CLI_REPORTED')
        self.assertEqual(developer['execution']['source_bytes'], 10)
        self.assertEqual(developer['execution']['source_file_count'], 1)
        self.assertEqual(developer['execution']['source_manifest_sha256'], 'b' * 64)
        self.assertEqual(developer['usage']['total_tokens'], 27)
        self.assertIn('+VALUE = 2', developer['artifact'])
        self.assertEqual(result['completion']['state'], 'WAITING_EXECUTOR')
        self.assertEqual(developer['execution_result']['status'], 'UNVERIFIED')
        self.assertEqual(invoke.call_count, 1)
        self.assertEqual(developer['direct_result_callback']['state'], 'WAITING_EXECUTOR')
        self.assertIn('TEST_SCOPE_MISSING',
                      developer['direct_result_callback']['missing_gates'])
        stored = TaskStore(Path(self.home) / '.local/state/llmtool/tasks').get(task['task_id'])
        self.assertEqual(stored['developer']['artifact'], developer['artifact'])

    def test_opencode_d6_head_drift_blocks_dispatch_before_provider_process(self):
        task = self._pm_task_with_opencode_assignment()
        output = io.StringIO()
        provider_process_started = False

        def simulate_prelaunch(**kwargs):
            subprocess.run(['/usr/bin/git', 'commit', '--allow-empty', '-m', 'synthetic drift'],
                           cwd=self.executor_root, check=True, capture_output=True)
            try:
                kwargs['prelaunch_validate']()
            except Exception:
                raise DirectOpenCodeError('OPENCODE_D6_BINDING_CHANGED') from None
            nonlocal provider_process_started
            provider_process_started = True
            self.fail('Provider process must not start after D6/Git drift')

        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.opencode_direct_developer.run',
                      side_effect=simulate_prelaunch) as invoke, redirect_stdout(output):
            code = run_task_delegate(self._opencode_delegate_args(task))
        result = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(result['developer']['state'], 'DEVELOPER_UNAVAILABLE')
        self.assertEqual(result['developer']['error_code'], 'OPENCODE_D6_BINDING_CHANGED')
        self.assertFalse(provider_process_started)
        self.assertEqual(invoke.call_count, 1)

    def test_opencode_bootstraps_registered_d6_before_provider_launch(self):
        task = self._pm_task_with_opencode_assignment()
        events = []
        result = DirectResult(0, 'ses_Abc123', 'Updated fixture.', None, 'UNKNOWN',
                              'a' * 64, 32, 'CONFIRMED', 'opencode v2.0.32',
                              10, 1, 'b' * 64)

        def bootstrap(registration):
            self.assertEqual(registration.task_id, 'executor-task-1')
            events.append('bootstrap')

        def launch(**kwargs):
            kwargs['prelaunch_validate']()
            (self.executor_root / 'sample.py').write_text('VALUE = 2\n')
            events.append('provider')
            return result

        output = io.StringIO()
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli._bootstrap_registered_d6',
                      side_effect=bootstrap), \
                patch('llmtool.task_submission_cli.opencode_direct_developer.run',
                      side_effect=launch), \
                patch('llmtool.task_submission_cli._developer_output_rejection',
                      return_value=None), redirect_stdout(output):
            code = run_task_delegate(self._opencode_delegate_args(task))

        self.assertEqual(code, 1, output.getvalue())
        self.assertIn('"TEST_SCOPE_MISSING"', output.getvalue())
        self.assertEqual(events, ['bootstrap', 'provider'])

    def test_opencode_failed_call_is_unknown_and_never_replayed(self):
        task = self._pm_task_with_opencode_assignment()
        output = io.StringIO()
        from llmtool.opencode_direct_developer import DirectOpenCodeError
        error = DirectOpenCodeError('OPENCODE_TIMEOUT', attempted=True,
                                    cleanup_status='CONFIRMED', process_exit_code=None,
                                    stdout_sha256='a' * 64, elapsed_ms=1200,
                                    source_bytes=10, source_file_count=1,
                                    source_manifest_sha256='b' * 64)
        args = self._opencode_delegate_args(task)
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.opencode_direct_developer.run',
                      side_effect=error) as invoke, redirect_stdout(output):
            code = run_task_delegate(args)
        self.assertEqual(code, 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result['developer']['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(result['developer']['error_code'], 'OPENCODE_TIMEOUT')
        self.assertEqual(result['developer']['usage'], None)
        self.assertEqual(result['developer']['execution']['process_exit_code'], None)
        self.assertEqual(result['developer']['execution']['stdout_sha256'], 'a' * 64)
        self.assertEqual(result['developer']['execution']['elapsed_ms'], 1200)
        self.assertEqual(result['developer']['execution']['source_bytes'], 10)
        self.assertEqual(result['developer']['execution']['source_file_count'], 1)
        self.assertEqual(result['developer']['execution']['source_manifest_sha256'], 'b' * 64)
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.opencode_direct_developer.run') as second, \
                self.assertRaises(LLMToolError):
            run_task_delegate(args)
        second.assert_not_called()
        self.assertEqual(invoke.call_count, 1)

    def test_opencode_interrupt_persists_unknown_then_preserves_exit(self):
        from llmtool.providers.cli_lifecycle import CLIHostTermination
        for interruption, expected_code in (
                (KeyboardInterrupt(), 'OPENCODE_INTERRUPTED'),
                (CLIHostTermination(143), 'OPENCODE_HOST_TERMINATED')):
            for evidence in (None, {
                    'process_exit_code': None, 'stdout_sha256': 'd' * 64,
                    'elapsed_ms': 35, 'cleanup_status': 'CONFIRMED',
                    'source_bytes': 20, 'source_file_count': 1,
                    'source_manifest_sha256': 'e' * 64,
            }):
                with self.subTest(interruption=type(interruption).__name__,
                                  evidence=evidence is not None):
                    task = self._pm_task_with_opencode_assignment()
                    args = self._opencode_delegate_args(task)
                    if evidence is not None:
                        interruption._llmtool_direct_opencode_execution = evidence
                    output = io.StringIO()
                    with patch.dict(os.environ, {'HOME': str(self.home)}), \
                            patch('llmtool.task_submission_cli.opencode_direct_developer.run',
                                  side_effect=interruption), redirect_stdout(output), \
                            self.assertRaises(type(interruption)) as caught:
                        run_task_delegate(args)
                    if isinstance(interruption, CLIHostTermination):
                        self.assertEqual(caught.exception.code, 143)
                    result = json.loads(output.getvalue())
                    developer = result['developer']
                    self.assertEqual(developer['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
                    self.assertEqual(developer['error_code'], expected_code)
                    self.assertEqual(developer['execution']['status'], 'UNKNOWN')
                    self.assertEqual(developer['execution']['usage_source'], 'UNKNOWN')
                    self.assertEqual(developer['execution']['cleanup_status'],
                                     'CONFIRMED' if evidence else 'UNKNOWN')
                    self.assertEqual(developer['execution']['stdout_sha256'],
                                     'd' * 64 if evidence else None)
                    self.assertEqual(developer['execution']['elapsed_ms'],
                                     35 if evidence else None)
                    self.assertEqual(developer['execution']['source_manifest_sha256'],
                                     'e' * 64 if evidence else None)
                    stored = TaskStore(Path(self.home) / '.local/state/llmtool/tasks').get(
                        task['task_id'])
                    self.assertEqual(stored['developer']['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
                    self.assertEqual(stored['developer']['error_code'], expected_code)

    def test_opencode_final_store_interrupt_persists_unknown_without_replay(self):
        task = self._pm_task_with_opencode_assignment()
        result = DirectResult(0, 'ses_Abc123', 'bounded result', None, 'UNKNOWN',
                              'f' * 64, 45, 'CONFIRMED', 'opencode v2.0.32',
                              20, 1, 'e' * 64)
        backing = TaskStore(Path(self.home) / '.local/state/llmtool/tasks')

        class InterruptOnTerminalWrite:
            def __init__(self, store):
                self.store = store
                self.update_count = 0
                self.update_error = None

            def __getattr__(self, name):
                return getattr(self.store, name)

            def update(self, task_id, **kwargs):
                self.update_count += 1
                if self.update_count == 2:
                    raise KeyboardInterrupt()
                try:
                    return self.store.update(task_id, **kwargs)
                except BaseException as exc:
                    self.update_error = exc
                    raise

        wrapped = InterruptOnTerminalWrite(backing)
        output = io.StringIO()
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.opencode_direct_developer.run',
                      return_value=result) as invoke, \
                patch('llmtool.task_submission_cli._developer_output_rejection',
                      return_value=None), redirect_stdout(output), \
                self.assertRaises(KeyboardInterrupt):
            run_task_opencode_delegate(self._opencode_delegate_args(task), store=wrapped)
        self.assertEqual(invoke.call_count, 1)
        self.assertEqual(wrapped.update_count, 3)
        self.assertIsNone(wrapped.update_error)
        saved = backing.get(task['task_id'])
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(saved['developer']['error_code'], 'OPENCODE_INTERRUPTED')
        self.assertEqual(saved['developer']['execution']['cleanup_status'], 'CONFIRMED')

    def test_opencode_interrupt_survives_output_failure(self):
        task = self._pm_task_with_opencode_assignment()
        interruption = KeyboardInterrupt()
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.opencode_direct_developer.run',
                      side_effect=interruption), \
                patch('llmtool.task_submission_cli._print', side_effect=BrokenPipeError()), \
                self.assertRaises(KeyboardInterrupt) as caught:
            run_task_delegate(self._opencode_delegate_args(task))
        self.assertIs(caught.exception, interruption)
        saved = TaskStore(Path(self.home) / '.local/state/llmtool/tasks').get(task['task_id'])
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_OUTCOME_UNKNOWN')

    def test_opencode_interrupt_during_result_collection_persists_unknown(self):
        task = self._pm_task_with_opencode_assignment()
        result = DirectResult(0, 'ses_Abc123', 'bounded result', None, 'UNKNOWN',
                              'f' * 64, 45, 'CONFIRMED', 'opencode v2.0.32',
                              20, 1, 'e' * 64)
        output = io.StringIO()
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.opencode_direct_developer.run',
                      return_value=result), \
                patch('llmtool.task_submission_cli._developer_output_rejection',
                      side_effect=KeyboardInterrupt()), redirect_stdout(output), \
                self.assertRaises(KeyboardInterrupt):
            run_task_delegate(self._opencode_delegate_args(task))
        saved = json.loads(output.getvalue())
        developer = saved['developer']
        self.assertEqual(developer['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(developer['error_code'], 'OPENCODE_INTERRUPTED')
        self.assertIsNone(developer['usage'])
        self.assertEqual(developer['execution']['status'], 'UNKNOWN')
        self.assertEqual(developer['execution']['native_session_id'], 'ses_Abc123')
        self.assertEqual(developer['execution']['cleanup_status'], 'CONFIRMED')

    def _delegate_args(self, task, **changes):
        values = dict(task_id=task['task_id'], provider='company-gw',
                      selection_source='PM',
                      executor_task='executor-task-1',
                      read_path=['tools/llmtool/tests/test_task_submission_cli.py'],
                      write_path=['tools/llmtool/tests/test_task_submission_cli.py'],
                      acceptance=['new test passes'],
                      egress_basis='Owner approved non-sensitive source and test context',
                      context='class TaskSubmissionCLITests: pass')
        values.update(changes)
        return Namespace(**values)

    def test_company_gw_patch_is_persisted_with_separate_logical_session_and_usage(self):
        task = self._pm_task_with_assignment()
        target = 'tools/llmtool/tests/test_task_submission_cli.py'
        patch_text = (f'diff --git a/{target} b/{target}\n'
                      f'--- a/{target}\n+++ b/{target}\n@@ -1 +1 @@\n-old\n+new\n')
        client = FakeClient(Reply(patch_text, {
            'prompt_tokens': 20, 'completion_tokens': 15, 'total_tokens': 35,
        }))
        args = self._delegate_args(task)
        output = io.StringIO()
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.resolve_profile_config',
                      return_value=config('openai-compatible', 'gw')), \
                patch('llmtool.task_submission_cli.api_key', return_value='not-persisted'), \
                patch('llmtool.task_submission_cli.create_client', return_value=client), \
                redirect_stdout(output):
            code = run_task_delegate(args)
        self.assertEqual(code, 0, output.getvalue())
        result = json.loads(output.getvalue())
        developer = result['developer']
        self.assertEqual(developer['state'], 'DEVELOPER_RESPONDED')
        self.assertEqual(developer['provider_profile'], 'company-gw')
        self.assertEqual(developer['selection_source'], 'PM')
        self.assertNotEqual(developer['session_id'], result['pm_session_id'])
        self.assertEqual(developer['source_sha'], self.executor_head)
        self.assertEqual(developer['source_sha_binding'], 'D6_REGISTRY_OBSERVED')
        self.assertEqual(developer['artifact'], patch_text)
        self.assertEqual(developer['usage']['total_tokens'], 35)
        self.assertEqual(developer['max_tokens'], 128)
        self.assertEqual(developer['execution_result'], {
            'status': 'UNVERIFIED', 'code': 'EXECUTOR_EVIDENCE_NOT_ATTACHED',
        })
        self.assertGreater(developer['input_bytes'], 0)
        self.assertEqual(developer['egress_basis'],
                         'Owner approved non-sensitive source and test context')
        self.assertEqual(result['pm_session_id'], task['pm_session_id'])
        self.assertEqual(result['completion']['state'], 'WAITING_EXECUTOR')
        self.assertEqual(result['completion']['executor_task_id'], 'executor-task-1')
        self.assertEqual(result['completion']['evidence']['source_sha'], self.executor_head)
        self.assertNotIn('not-persisted', output.getvalue())
        system_prompt = client.calls[0][0]['content']
        self.assertIn('Do not use Markdown fences', system_prompt)
        self.assertIn('diff --git a/example.py b/example.py', system_prompt)
        self.assertIn('@@ -1 +1 @@\n-old\n+new', system_prompt)
        developer_packet = json.loads(client.calls[0][1]['content'])
        self.assertEqual(developer_packet['pm_selected_goal'], 'Add one focused test.')
        self.assertEqual(developer_packet['acceptance'], ['new test passes'])
        self.assertEqual(developer_packet['context'], 'class TaskSubmissionCLITests: pass')
        self.assertEqual(developer_packet['write_paths'], [target])
        self.assertNotIn('original_request', developer_packet)
        self.assertNotIn('pm_response', developer_packet)
        self.assertNotIn('Implement a health-check', client.calls[0][1]['content'])
        self.assertNotIn('Bounded test-only task.', client.calls[0][1]['content'])
        saved = TaskStore(Path(self.home) / '.local/state/llmtool/tasks').get(task['task_id'])
        self.assertEqual(saved['developer']['artifact'], patch_text)
        self.assertEqual(len(client.calls), 1)

    def test_pm_recommendation_not_text_or_wrong_risk_cannot_authorize_gw(self):
        task = self._pm_task_with_assignment()
        stored = TaskStore(Path(self.home) / '.local/state/llmtool/tasks').get(task['task_id'])
        stored['pm_assignment']['risk'] = 'OTHER'
        TaskStore(Path(self.home) / '.local/state/llmtool/tasks').update(
            task['task_id'], pm_assignment=stored['pm_assignment'])
        args = self._delegate_args(task)
        with patch.dict(os.environ, {'HOME': str(self.home)}):
            with self.assertRaises(LLMToolError):
                run_task_delegate(args)

    def test_plain_prose_or_malformed_pm_assignment_is_not_a_route_decision(self):
        self.assertIsNone(__import__('llmtool.task_submission_cli',
                                     fromlist=['_pm_assignment'])._pm_assignment(
                                         'Use company-gw for this.'))
        malformed = json.dumps({'schema_version': 1, 'summary': 'ok',
                                'developer_assignment': {'route': 'company-gw',
                                                         'risk': 'LOW', 'goal': 'x',
                                                         'acceptance': [], 'authorize': True}})
        self.assertIsNone(__import__('llmtool.task_submission_cli',
                                     fromlist=['_pm_assignment'])._pm_assignment(malformed))

    def test_fake_ack_and_sensitive_patch_are_not_persisted(self):
        target = 'tools/llmtool/tests/test_task_submission_cli.py'
        rejected = (
            ('Developer started and completed the work.',
             'DEVELOPER_OUTPUT_INVALID_ENVELOPE'),
            (f'diff --git a/{target} b/{target}\n'
             f'--- a/{target}\n+++ b/{target}\n@@ -1 +1 @@\n'
             '-old\n+Bearer abcdefghijklmnop\n', 'DEVELOPER_OUTPUT_SENSITIVE'),
        )
        for response, error_code in rejected:
            task = self._pm_task_with_assignment()
            args = self._delegate_args(task)
            client = FakeClient(Reply(response, {}))
            output = io.StringIO()
            with patch.dict(os.environ, {'HOME': str(self.home)}), \
                    patch('llmtool.task_submission_cli.resolve_profile_config',
                          return_value=config('openai-compatible', 'gw')), \
                    patch('llmtool.task_submission_cli.api_key', return_value=None), \
                    patch('llmtool.task_submission_cli.create_client', return_value=client), \
                    redirect_stdout(output):
                code = run_task_delegate(args)
            self.assertEqual(code, 1)
            result = json.loads(output.getvalue())
            self.assertEqual(result['developer']['state'], 'DEVELOPER_OUTPUT_REJECTED')
            self.assertEqual(result['developer']['error_code'], error_code)
            self.assertIsNone(result['developer']['artifact'])
            self.assertNotIn('abcdefghijklmnop', output.getvalue())
            saved = TaskStore(Path(self.home) / '.local/state/llmtool/tasks').get(task['task_id'])
            self.assertNotIn('abcdefghijklmnop', json.dumps(saved))

    def test_markdown_fenced_company_patch_is_rejected_without_persistence(self):
        target = 'tools/llmtool/tests/test_task_submission_cli.py'
        fenced_patch = (
            '```diff\n'
            f'diff --git a/{target} b/{target}\n'
            f'--- a/{target}\n+++ b/{target}\n@@ -1 +1 @@\n'
            '-old\n+FENCED_PROVIDER_PATCH_SENTINEL\n'
            '```\n'
        )
        task = self._pm_task_with_assignment()
        client = FakeClient(Reply(fenced_patch, {
            'prompt_tokens': 20, 'completion_tokens': 25, 'total_tokens': 45,
        }))
        output = io.StringIO()
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.resolve_profile_config',
                      return_value=config('openai-compatible', 'gw')), \
                patch('llmtool.task_submission_cli.api_key', return_value=None), \
                patch('llmtool.task_submission_cli.create_client', return_value=client), \
                redirect_stdout(output):
            code = run_task_delegate(self._delegate_args(task))
            with self.assertRaisesRegex(
                    LLMToolError,
                    'developer assignment already exists; duplicate dispatch refused'):
                run_task_delegate(self._delegate_args(task))

        self.assertEqual(code, 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result['developer']['state'], 'DEVELOPER_OUTPUT_REJECTED')
        self.assertEqual(result['developer']['error_code'], 'DEVELOPER_OUTPUT_INVALID_ENVELOPE')
        self.assertIsNone(result['developer']['artifact'])
        self.assertEqual(result['developer']['usage']['total_tokens'], 45)
        self.assertNotIn(fenced_patch, output.getvalue())
        self.assertNotIn('FENCED_PROVIDER_PATCH_SENTINEL', output.getvalue())
        self.assertEqual(len(client.calls), 1)
        saved = TaskStore(Path(self.home) / '.local/state/llmtool/tasks').get(task['task_id'])
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_OUTPUT_REJECTED')
        self.assertEqual(saved['developer']['error_code'], 'DEVELOPER_OUTPUT_INVALID_ENVELOPE')
        self.assertIsNone(saved['developer']['artifact'])
        self.assertEqual(saved['developer']['usage']['total_tokens'], 45)
        serialized = json.dumps(saved)
        self.assertNotIn(fenced_patch, serialized)
        self.assertNotIn('FENCED_PROVIDER_PATCH_SENTINEL', serialized)

    def test_rejection_categories_are_durable_without_provider_content(self):
        target = 'tools/llmtool/tests/test_task_submission_cli.py'
        cases = (
            ('DEVELOPER_OUTPUT_INVALID_DIFF',
             f'diff --git a/{target} b/{target}\n--- a/other.py\n'
             f'+++ b/{target}\n@@ -1 +1 @@\n-old\n+new\n', 'other.py'),
            ('DEVELOPER_OUTPUT_OUT_OF_SCOPE',
             'diff --git a/tools/llmtool/tests/outside.py b/tools/llmtool/tests/outside.py\n'
             '--- a/tools/llmtool/tests/outside.py\n'
             '+++ b/tools/llmtool/tests/outside.py\n@@ -1 +1 @@\n-old\n+new\n', 'outside.py'),
            ('DEVELOPER_OUTPUT_INVALID_TEXT',
             f'diff --git a/{target} b/{target}\n--- a/{target}\n+++ b/{target}\n'
             '@@ -1 +1 @@\n-old\n+new\x01marker\n', 'marker'),
            ('DEVELOPER_OUTPUT_TOO_LARGE',
             f'diff --git a/{target} b/{target}\n' + ('x' * (RESPONSE_LIMIT + 1)), 'x' * 64),
        )
        for error_code, response, marker in cases:
            with self.subTest(error_code=error_code):
                task = self._pm_task_with_assignment()
                client = FakeClient(Reply(response, {
                    'prompt_tokens': 20, 'completion_tokens': 25, 'total_tokens': 45,
                }))
                output = io.StringIO()
                with patch.dict(os.environ, {'HOME': str(self.home)}), \
                        patch('llmtool.task_submission_cli.resolve_profile_config',
                              return_value=config('openai-compatible', 'gw')), \
                        patch('llmtool.task_submission_cli.api_key', return_value=None), \
                        patch('llmtool.task_submission_cli.create_client', return_value=client), \
                        redirect_stdout(output):
                    code = run_task_delegate(self._delegate_args(task))
                self.assertEqual(code, 1)
                result = json.loads(output.getvalue())
                self.assertEqual(result['developer']['state'], 'DEVELOPER_OUTPUT_REJECTED')
                self.assertEqual(result['developer']['error_code'], error_code)
                self.assertEqual(result['developer']['usage']['total_tokens'], 45)
                self.assertIsNone(result['developer']['artifact'])
                self.assertNotIn(marker, output.getvalue())
                saved = TaskStore(Path(self.home) / '.local/state/llmtool/tasks').get(task['task_id'])
                self.assertNotIn(marker, json.dumps(saved))

    def test_provider_unavailable_is_durable_and_not_fallen_back(self):
        task = self._pm_task_with_assignment()
        args = self._delegate_args(task)
        output = io.StringIO()
        def unavailable(profile, require_configured):
            raise LLMToolError('private configuration detail')
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.resolve_profile_config', side_effect=unavailable), \
                patch('llmtool.task_submission_cli.create_client') as create, \
                redirect_stdout(output):
            code = run_task_delegate(args)
        self.assertEqual(code, 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result['developer']['state'], 'DEVELOPER_UNAVAILABLE')
        self.assertIsNone(result['developer']['artifact'])
        self.assertNotIn('private configuration detail', output.getvalue())
        create.assert_not_called()

    def test_scope_overflow_and_out_of_scope_patch_fail_closed(self):
        task = self._pm_task_with_assignment()
        args = self._delegate_args(task, write_path=['tools/llmtool/tests/other.py'])
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                self.assertRaises(LLMToolError):
            run_task_delegate(args)
        target = 'tools/llmtool/tests/outside.py'
        client = FakeClient(Reply(
            f'diff --git a/{target} b/{target}\n--- a/{target}\n+++ b/{target}\n'
            '@@ -1 +1 @@\n-old\n+new\n', {}))
        args = self._delegate_args(task)
        output = io.StringIO()
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.resolve_profile_config',
                      return_value=config('openai-compatible', 'gw')), \
                patch('llmtool.task_submission_cli.api_key', return_value=None), \
                patch('llmtool.task_submission_cli.create_client', return_value=client), \
                redirect_stdout(output):
            code = run_task_delegate(args)
        self.assertEqual(code, 1)
        rejected = json.loads(output.getvalue())['developer']
        self.assertEqual(rejected['state'], 'DEVELOPER_OUTPUT_REJECTED')
        self.assertEqual(rejected['error_code'], 'DEVELOPER_OUTPUT_OUT_OF_SCOPE')
        self.assertIsNone(rejected['artifact'])

    def test_ambiguous_provider_outcome_is_persisted_and_never_replayed(self):
        task = self._pm_task_with_assignment()
        client = FakeClient(error=coded_error('private timeout', 'PROVIDER_TIMEOUT', 'provider.test'))
        args = self._delegate_args(task)
        output = io.StringIO()
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.resolve_profile_config',
                      return_value=config('openai-compatible', 'gw')), \
                patch('llmtool.task_submission_cli.api_key', return_value=None), \
                patch('llmtool.task_submission_cli.create_client', return_value=client), \
                redirect_stdout(output):
            code = run_task_delegate(args)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())['developer']['state'],
                         'DEVELOPER_OUTCOME_UNKNOWN')
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                self.assertRaises(LLMToolError):
            run_task_delegate(args)
        self.assertEqual(len(client.calls), 1)

    def test_concurrent_stale_delegations_claim_once_before_provider_call(self):
        task = self._pm_task_with_assignment()
        backing = TaskStore(Path(self.home) / '.local/state/llmtool/tasks')
        read_barrier = threading.Barrier(2)

        class StaleReadStore:
            def get(self, task_id):
                result = backing.get(task_id)
                read_barrier.wait(timeout=5)
                return result

            def compare_and_update(self, task_id, **kwargs):
                return backing.compare_and_update(task_id, **kwargs)

            def update(self, task_id, **kwargs):
                return backing.update(task_id, **kwargs)

        client = FakeClient(Reply(
            'diff --git a/tools/llmtool/tests/test_task_submission_cli.py '
            'b/tools/llmtool/tests/test_task_submission_cli.py\n'
            '--- a/tools/llmtool/tests/test_task_submission_cli.py\n'
            '+++ b/tools/llmtool/tests/test_task_submission_cli.py\n'
            '@@ -1 +1 @@\n-old\n+new\n', {}))
        outcomes = []

        def delegate():
            try:
                outcomes.append(run_task_delegate(self._delegate_args(task), store=StaleReadStore()))
            except Exception as exc:
                outcomes.append(exc)

        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.resolve_profile_config',
                      return_value=config('openai-compatible', 'gw')), \
                patch('llmtool.task_submission_cli.api_key', return_value=None), \
                patch('llmtool.task_submission_cli.create_client', return_value=client), \
                patch('llmtool.task_submission_cli._print'):
            workers = [threading.Thread(target=delegate) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=10)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(sum(value == 0 for value in outcomes), 1, outcomes)
        self.assertEqual(sum(isinstance(value, LLMToolError) for value in outcomes), 1, outcomes)
        self.assertEqual(len(client.calls), 1)
        saved = backing.get(task['task_id'])
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_RESPONDED')

    def test_mismatched_diff_headers_and_fake_empty_diff_are_rejected(self):
        task_target = 'tools/llmtool/tests/test_task_submission_cli.py'
        bad_artifacts = (
            f'diff --git a/{task_target} b/{task_target}\n'
            '--- a/tools/llmtool/tests/outside.py\n'
            f'+++ b/{task_target}\n@@ -1 +1 @@\n-old\n+new\n',
            f'diff --git a/{task_target} b/{task_target}\n'
            f'--- a/{task_target}\n+++ b/{task_target}\n',
            f'diff --git a/{task_target} b/{task_target}\n'
            f'--- a/{task_target}\n+++ b/{task_target}\n@@ -1 +1 @@\n',
        )
        for artifact in bad_artifacts:
            with self.subTest(artifact=artifact):
                task = self._pm_task_with_assignment()
                client = FakeClient(Reply(artifact, {}))
                output = io.StringIO()
                with patch.dict(os.environ, {'HOME': str(self.home)}), \
                        patch('llmtool.task_submission_cli.resolve_profile_config',
                              return_value=config('openai-compatible', 'gw')), \
                        patch('llmtool.task_submission_cli.api_key', return_value=None), \
                        patch('llmtool.task_submission_cli.create_client', return_value=client), \
                        redirect_stdout(output):
                    code = run_task_delegate(self._delegate_args(task))
                self.assertEqual(code, 1)
                result = json.loads(output.getvalue())
                self.assertEqual(result['developer']['state'], 'DEVELOPER_OUTPUT_REJECTED')
                self.assertEqual(result['developer']['error_code'], 'DEVELOPER_OUTPUT_INVALID_DIFF')
                self.assertIsNone(result['developer']['artifact'])


if __name__ == '__main__':
    unittest.main()
