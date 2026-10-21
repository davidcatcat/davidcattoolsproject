import io
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
                                         run_task_delegate, run_task_show, run_task_submit)


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
        self.assertEqual(result['backend_session_semantics'], 'EPHEMERAL_SINGLE_CALL')
        self.assertIsNone(result['native_session_id'])
        self.assertEqual(result['response'], client.reply.content)
        self.assertEqual(result['usage'], {'prompt_tokens': 10,
                                           'completion_tokens': 8, 'total_tokens': 18})
        self.assertEqual(result['usage_status'], 'REPORTED')
        self.assertEqual(resolve.call_args.args, ('selected',))
        self.assertEqual(len(client.calls), 1)
        self.assertIn('does not dispatch a Developer', client.calls[0][0]['content'])
        saved = TaskStore(Path(self.home) / '.local/state/llmtool/tasks').get(result['task_id'])
        self.assertEqual(saved['response'], client.reply.content)
        self.assertNotIn('api_key', json.dumps(saved).lower())

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

    def _delegate_args(self, task, **changes):
        values = dict(task_id=task['task_id'], provider='company-gw',
                      selection_source='PM', source_sha='a' * 40,
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
        self.assertEqual(developer['source_sha'], 'a' * 40)
        self.assertEqual(developer['source_sha_binding'], 'UNVERIFIED_CALLER_INPUT')
        self.assertEqual(developer['artifact'], patch_text)
        self.assertEqual(developer['usage']['total_tokens'], 35)
        self.assertEqual(developer['max_tokens'], 128)
        self.assertGreater(developer['input_bytes'], 0)
        self.assertEqual(developer['egress_basis'],
                         'Owner approved non-sensitive source and test context')
        self.assertEqual(result['pm_session_id'], task['pm_session_id'])
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
