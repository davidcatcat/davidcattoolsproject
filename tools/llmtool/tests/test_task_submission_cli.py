import io
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from llmtool.cli import main, parser_for_cli
from llmtool.errors import LLMToolError, coded_error
from llmtool import worker_dispatch
from llmtool import opencode_direct_developer
from llmtool.providers import get_provider_capabilities
from llmtool.providers.base import Reply
from llmtool.providers.codex_cli import CodexCLIProvider, OUTPUT_LIMIT
from llmtool.task_store import MAX_RECORD_BYTES, TaskStore, TaskStoreConflict, new_id
from llmtool.task_submission_cli import TEXT_DEVELOPER_CONTEXT_SEPARATOR
from llmtool import remote_test
from llmtool import active_pm_inbox
from llmtool import task_submission_cli as submission_cli
from llmtool.task_submission_cli import (REQUEST_LIMIT, RESPONSE_LIMIT, _record,
                                         _provider_route_binding, run_task_complete,
                                         run_task_delegate,
                                         run_task_opencode_delegate,
                                         run_task_record_pm, run_task_show,
                                         run_task_submit)
from llmtool.opencode_direct_developer import DirectOpenCodeError, DirectResult


def config(provider_type='codex-cli', model='gpt-6-luna'):
    return {'active_provider': 'selected', 'providers': {
        'selected': {'type': provider_type, 'model': model,
                     'timeout_seconds': 30, 'provider_options': {}}
    }, 'generation': {'temperature': 0, 'max_tokens': 128}}


def codex_developer_config():
    return {
        'active_provider': 'codex-developer',
        'providers': {'codex-developer': {
            'type': 'codex-cli', 'model': 'gpt-6-luna', 'timeout_seconds': 30,
            'provider_options': {},
        }},
        'generation': {'temperature': 0, 'max_tokens': 128},
    }


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
        policy = json.loads(Path(worker_dispatch._POLICY_PATH).read_text(encoding='utf-8'))
        policy['workers']['opencode-developer']['dispatch'] = 'ALLOWED'
        policy['workers']['opencode-developer']['reason'] = 'test fixture allows isolated route checks'
        policy['workers']['company-gw']['dispatch'] = 'ALLOWED'
        policy['workers']['company-gw']['reason'] = 'test fixture allows isolated route checks'
        self.policy_path = Path(self.temp.name).resolve() / 'worker-dispatch.json'
        self.policy_path.write_text(json.dumps(policy), encoding='utf-8')
        policy_patch = patch.object(worker_dispatch, '_POLICY_PATH', self.policy_path)
        policy_patch.start()
        self.addCleanup(policy_patch.stop)
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
        (self.executor_root / 'README.md').write_text('Executor fixture documentation.\n')
        target_fixture = (self.executor_root / 'tools' / 'llmtool' / 'tests' /
                          'test_task_submission_cli.py')
        target_fixture.parent.mkdir(parents=True, exist_ok=True)
        target_fixture.write_text('old\n')
        report_dir = self.executor_root / 'tools' / 'llmtool'
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / '.gitignore').write_text('/.d6-bootstrap-report.json\n')
        subprocess.run(['/usr/bin/git', 'add', 'sample.py', 'README.md',
                        'tools/llmtool/.gitignore',
                        'tools/llmtool/tests/test_task_submission_cli.py'],
                       cwd=self.executor_root, check=True)
        subprocess.run(['/usr/bin/git', 'commit', '-m', 'fixture source'],
                       cwd=self.executor_root, check=True, capture_output=True)
        self.executor_head = subprocess.check_output(
            ['/usr/bin/git', 'rev-parse', 'HEAD'], cwd=self.executor_root, text=True).strip()
        self.executor_common_dir = str(self.executor_root / '.git')
        registry_dir = Path(self.executor_common_dir) / 'llmtool-worktrees'
        registry_dir.mkdir(mode=0o700)
        lock_path = registry_dir / '.lifecycle.lock'
        lock_path.touch(mode=0o600)
        lock_path.chmod(0o600)
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
        report = {'schema_version': 1, 'result': 'PASS', 'status': 'env_reused',
            'task_id': 'executor-task-1', 'root_realpath': str(self.executor_root),
            'head_sha': self.executor_head,
            'tooling_sha': self.executor_head,
            'cache_fingerprint': 'cpython3.13-darwin-arm64-012345abcdef'}
        report_bytes = (json.dumps(report, sort_keys=True, separators=(',', ':')) + '\n').encode()
        report_path = report_dir / '.d6-bootstrap-report.json'
        report_path.write_bytes(report_bytes)
        report_path.chmod(0o600)
        self.executor_registry['env2_pass_marker'] = {
            'schema_version': 1, 'result': 'PASS', 'task_id': 'executor-task-1',
            'root_realpath': str(self.executor_root),
            'submitted_head_sha': self.executor_head, 'current_head_sha': self.executor_head,
            'tooling_sha': self.executor_head,
            'environment_fingerprint': report['cache_fingerprint'],
            'report_sha256': hashlib.sha256(report_bytes).hexdigest()}
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

    def test_completion_repair_chain_skips_failed_attempt_before_successful_retry(self):
        from types import SimpleNamespace

        source_sha, failed_head, retry_head, result_head = (
            'a' * 40, 'b' * 40, 'c' * 40, 'd' * 40)
        original_artifact = 'diff --git a/sample.py b/sample.py\n'
        repair_artifact = 'diff --git a/sample.py b/sample.py\n'
        repair_id = '4' * 32
        task = {
            'developer': {'source_sha': source_sha, 'write_paths': ['sample.py'],
                          'artifact': original_artifact,
                          'artifact_sha256': hashlib.sha256(
                              original_artifact.encode()).hexdigest()},
            'review_repair_claims': [
                {'attempt': 1, 'state': 'FAILED', 'head_sha': failed_head,
                 'repair_task_id': '3' * 32},
                {'attempt': 2, 'state': 'DISPATCHED', 'head_sha': retry_head,
                 'repair_task_id': repair_id},
            ],
        }
        repair = {'developer': {'state': 'DEVELOPER_RESPONDED',
                                'source_sha': retry_head, 'artifact': repair_artifact,
                                'artifact_sha256': hashlib.sha256(
                                    repair_artifact.encode()).hexdigest()}}
        class Store:
            def get(self, task_id):
                if task_id == repair_id:
                    return repair
                raise AssertionError('failed repair Task must not be loaded')

        registration = SimpleNamespace(root='/repo/task', branch='task-branch',
                                       source_sha=source_sha, result_sha=result_head)
        with patch.object(submission_cli.task_completion, 'read_d6_registration',
                          return_value=registration):
            artifacts = submission_cli._completion_repair_artifact_segments(
                task, Store(), executor_task='executor', common_dir='/repo/.git',
                root='/repo/task', branch='task-branch', source_sha=source_sha)
        self.assertEqual(artifacts, [
            (original_artifact, task['developer']['artifact_sha256'],
             source_sha, retry_head),
            (repair_artifact, repair['developer']['artifact_sha256'],
             retry_head, result_head),
        ])

    def test_trusted_bootstrap_helper_requires_clean_pinned_dispatcher_source(self):
        dispatcher_root = Path(self.temp.name).resolve() / 'dispatcher'
        helper = dispatcher_root / 'tools' / 'llmtool' / 'scripts' / 'd6_worktree_env.py'
        dispatcher = (dispatcher_root / 'tools' / 'llmtool' / 'llmtool' /
                      'task_submission_cli.py')
        helper.parent.mkdir(parents=True)
        dispatcher.parent.mkdir(parents=True)
        helper.write_bytes(b"raise SystemExit(0)\n")
        dispatcher.write_bytes(b"# pinned dispatcher fixture\n")
        subprocess.run(['/usr/bin/git', 'init', '-b', 'main'], cwd=dispatcher_root,
                       check=True, capture_output=True)
        subprocess.run(['/usr/bin/git', 'config', 'user.name', 'Dispatcher Fixture'],
                       cwd=dispatcher_root, check=True)
        subprocess.run(['/usr/bin/git', 'config', 'user.email', 'dispatcher@example.invalid'],
                       cwd=dispatcher_root, check=True)
        subprocess.run(['/usr/bin/git', 'remote', 'add', 'origin',
                        'git@github.com:davidcatcat/davidcattoolsproject.git'],
                       cwd=dispatcher_root, check=True)
        subprocess.run(['/usr/bin/git', 'add', 'tools'], cwd=dispatcher_root, check=True)
        subprocess.run(['/usr/bin/git', 'commit', '-m', 'trusted dispatcher fixture'],
                       cwd=dispatcher_root, check=True, capture_output=True)

        with patch.object(submission_cli, 'D6_INTEGRATOR_DISPATCHER_ROOT', dispatcher_root), \
                patch.object(submission_cli, '__file__', str(dispatcher)):
            root, source = submission_cli._trusted_d6_helper_source()
        self.assertEqual(root, dispatcher_root)
        self.assertEqual(source, b"raise SystemExit(0)\n")

        helper.write_bytes(b"raise SystemExit(23)\n")
        with patch.object(submission_cli, 'D6_INTEGRATOR_DISPATCHER_ROOT', dispatcher_root), \
                patch.object(submission_cli, '__file__', str(dispatcher)):
            with self.assertRaises(ValueError):
                submission_cli._trusted_d6_helper_source()

    def test_committed_task_tree_dispatcher_and_helper_cannot_be_trust_source(self):
        trusted_root = Path(self.temp.name).resolve() / 'trusted-dispatcher'
        helper = trusted_root / 'tools' / 'llmtool' / 'scripts' / 'd6_worktree_env.py'
        dispatcher = (trusted_root / 'tools' / 'llmtool' / 'llmtool' /
                      'task_submission_cli.py')
        helper.parent.mkdir(parents=True)
        dispatcher.parent.mkdir(parents=True)
        helper.write_text('# trusted pinned helper\n', encoding='utf-8')
        dispatcher.write_text('# trusted pinned dispatcher\n', encoding='utf-8')
        subprocess.run(['/usr/bin/git', 'init', '-b', 'main'], cwd=trusted_root,
                       check=True, capture_output=True)
        subprocess.run(['/usr/bin/git', 'config', 'user.name', 'Dispatcher Fixture'],
                       cwd=trusted_root, check=True)
        subprocess.run(['/usr/bin/git', 'config', 'user.email', 'dispatcher@example.invalid'],
                       cwd=trusted_root, check=True)
        subprocess.run(['/usr/bin/git', 'remote', 'add', 'origin',
                        'git@github.com:davidcatcat/davidcattoolsproject.git'],
                       cwd=trusted_root, check=True)
        subprocess.run(['/usr/bin/git', 'add', 'tools'], cwd=trusted_root, check=True)
        subprocess.run(['/usr/bin/git', 'commit', '-m', 'trusted dispatcher fixture'],
                       cwd=trusted_root, check=True, capture_output=True)

        task_root = Path(self.temp.name).resolve() / 'committed-task-tree'
        subprocess.run(['/usr/bin/git', 'clone', '-b', 'main', str(trusted_root),
                        str(task_root)], check=True, capture_output=True)
        subprocess.run(['/usr/bin/git', 'config', 'user.name', 'Task Fixture'],
                       cwd=task_root, check=True)
        subprocess.run(['/usr/bin/git', 'config', 'user.email', 'task@example.invalid'],
                       cwd=task_root, check=True)
        task_helper = task_root / 'tools' / 'llmtool' / 'scripts' / 'd6_worktree_env.py'
        task_dispatcher = (task_root / 'tools' / 'llmtool' / 'llmtool' /
                           'task_submission_cli.py')
        marker_path = Path(self.temp.name).resolve() / 'committed-task-helper-ran'
        task_helper.write_text(
            'from pathlib import Path\nPath(' + repr(str(marker_path)) +
            ").write_text('ran')\n", encoding='utf-8')
        task_dispatcher.write_text('# committed descendant dispatcher\n', encoding='utf-8')
        subprocess.run(['/usr/bin/git', 'add', 'tools'], cwd=task_root, check=True)
        subprocess.run(['/usr/bin/git', 'commit', '-m', 'committed task descendant'],
                       cwd=task_root, check=True, capture_output=True)

        registration = type('Registration', (), {
            'root': str(self.executor_root), 'task_id': 'executor-task-1'})()
        self.bootstrap_patch.stop()
        with patch.object(submission_cli, 'D6_INTEGRATOR_DISPATCHER_ROOT', trusted_root), \
                patch.object(submission_cli, '__file__', str(task_dispatcher)):
            with self.assertRaises(DirectOpenCodeError) as raised:
                submission_cli._bootstrap_registered_d6(registration)

        self.assertEqual(raised.exception.direct_opencode_code,
                         'ENV_BOOTSTRAP_SOURCE_UNTRUSTED')
        self.assertFalse(marker_path.exists())

    def test_integrator_approved_tooling_reads_exact_source_blobs_from_shared_store(self):
        trusted_root = Path(self.temp.name).resolve() / 'approved-dispatcher'
        paths = {
            'helper': trusted_root / 'tools' / 'llmtool' / 'scripts' /
                      'd6_worktree_env.py',
            'lifecycle': trusted_root / 'tools' / 'llmtool' / 'scripts' /
                         'd6_worktree_lifecycle.py',
            'dispatcher': trusted_root / 'tools' / 'llmtool' / 'llmtool' /
                          'task_submission_cli.py',
        }
        for key in ('helper', 'lifecycle', 'dispatcher'):
            paths[key].parent.mkdir(parents=True, exist_ok=True)
        paths['helper'].write_text('# base helper\n', encoding='utf-8')
        paths['lifecycle'].write_text('# base lifecycle\n', encoding='utf-8')
        paths['dispatcher'].write_text('# pinned dispatcher\n', encoding='utf-8')
        subprocess.run(['/usr/bin/git', 'init', '-b', 'main'], cwd=trusted_root,
                       check=True, capture_output=True)
        subprocess.run(['/usr/bin/git', 'config', 'user.name', 'Dispatcher Fixture'],
                       cwd=trusted_root, check=True)
        subprocess.run(['/usr/bin/git', 'config', 'user.email', 'dispatcher@example.invalid'],
                       cwd=trusted_root, check=True)
        subprocess.run(['/usr/bin/git', 'remote', 'add', 'origin',
                        'git@github.com:davidcatcat/davidcattoolsproject.git'],
                       cwd=trusted_root, check=True)
        subprocess.run(['/usr/bin/git', 'add', 'tools'], cwd=trusted_root, check=True)
        subprocess.run(['/usr/bin/git', 'commit', '-m', 'approved fixture base'],
                       cwd=trusted_root, check=True, capture_output=True)
        base_sha = subprocess.check_output(['/usr/bin/git', 'rev-parse', 'HEAD'],
                                           cwd=trusted_root, text=True).strip()

        task_root = Path(self.temp.name).resolve() / 'approved-task'
        branch = 'feat/approved-env2-source'
        subprocess.run(['/usr/bin/git', 'worktree', 'add', '-b', branch,
                        str(task_root), base_sha], cwd=trusted_root,
                       check=True, capture_output=True)
        marker_path = Path(self.temp.name).resolve() / 'approved-source-ran'
        helper_bytes = (
            'from pathlib import Path\nPath(' + repr(str(marker_path)) +
            ").write_text('ran')\n").encode()
        lifecycle_bytes = b'# exact reviewed lifecycle candidate\n'
        (task_root / 'tools' / 'llmtool' / 'scripts' /
         'd6_worktree_env.py').write_bytes(helper_bytes)
        (task_root / 'tools' / 'llmtool' / 'scripts' /
         'd6_worktree_lifecycle.py').write_bytes(lifecycle_bytes)
        subprocess.run(['/usr/bin/git', 'add', 'tools'], cwd=task_root, check=True)
        subprocess.run(['/usr/bin/git', 'commit', '-m', 'approved candidate sources'],
                       cwd=task_root, check=True, capture_output=True)
        approved_sha = subprocess.check_output(['/usr/bin/git', 'rev-parse', 'HEAD'],
                                              cwd=task_root, text=True).strip()
        registration = type('Registration', (), {
            'task_id': 'approved-task-1',
            'common_dir': str((trusted_root / '.git').resolve()),
            'root': str(task_root), 'branch': branch,
            'source_sha': base_sha, 'result_sha': approved_sha,
            'env2_approved_tooling_sha': approved_sha,
        })()
        with patch.object(submission_cli, 'D6_INTEGRATOR_DISPATCHER_ROOT', trusted_root), \
                patch.object(submission_cli, '__file__', str(paths['dispatcher'])), \
                patch.object(submission_cli.task_completion, 'read_d6_registration',
                             return_value=registration):
            _root, loaded_helper = submission_cli._trusted_d6_helper_source(registration)
            _root, loaded_lifecycle = submission_cli._trusted_d6_helper_source(
                registration, 'tools/llmtool/scripts/d6_worktree_lifecycle.py')

        self.assertEqual(loaded_helper, helper_bytes)
        self.assertEqual(loaded_lifecycle, lifecycle_bytes)
        self.assertFalse(marker_path.exists())

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
        recorded_pm = parser.parse_args([
            'task', 'record-pm', '--stdin-json', '--pm-runtime', 'codex',
            '--pm-session-id', '12345678-1234-4234-8234-123456789abc',
            '--pm-cwd', '/tmp/pm-task'])
        self.assertEqual((recorded_pm.action, recorded_pm.pm_runtime,
                          recorded_pm.pm_session_id, recorded_pm.pm_cwd),
                         ('record-pm', 'codex',
                          '12345678-1234-4234-8234-123456789abc', '/tmp/pm-task'))
        delegated = parser.parse_args([
            'task', 'delegate', 'a' * 32, '--provider', 'codex',
            '--selection-source', 'PM', '--executor-task', 'executor-task-1',
            '--read-path', 'sample.py', '--write-path', 'sample.py',
            '--acceptance', 'focused test passes', '--egress-basis', 'synthetic text',
            '--context-stdin',
        ])
        self.assertEqual((delegated.action, delegated.provider, delegated.context_stdin),
                         ('delegate', 'codex', True))
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
        self.assertEqual(saved['pm_provider_type'], 'active-pm-runtime')
        self.assertIsNone(saved['pm_model'])
        self.assertEqual(saved['pm_route_binding'], {
            'type': 'active-pm-runtime', 'model': None, 'base_url': None})
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

    def test_task_adopt_moves_open_tasks_and_second_run_is_noop(self):
        pm_cwd = Path(self.temp.name).resolve() / 'pm-cwd'
        pm_cwd.mkdir()
        claude_home = Path(self.temp.name).resolve() / 'claude-home'
        old_native, new_native = str(uuid.uuid4()), str(uuid.uuid4())
        env = {'HOME': str(self.home), 'CLAUDE_CONFIG_DIR': str(claude_home)}
        old_file = active_pm_inbox.claude_session_file(old_native, str(pm_cwd), env)
        old_file.parent.mkdir(parents=True)
        old_file.write_text('{}\n', encoding='utf-8')
        new_file = active_pm_inbox.claude_session_file(new_native, str(pm_cwd), env)
        new_file.parent.mkdir(parents=True, exist_ok=True)
        new_file.write_text('{}\n', encoding='utf-8')
        store = TaskStore(Path(self.temp.name).resolve() / 'adopt-store')
        response = json.dumps({'schema_version': 1, 'summary': 'A bounded plan.',
            'developer_assignment': {'route': 'codex', 'risk': 'LOW',
                'goal': 'Add a focused test.', 'acceptance': ['The test passes.']}})
        with patch.dict(os.environ, env):
            task = run_task_record_pm(
                Namespace(stdin_json=True, pm_session_id=old_native,
                          pm_runtime='claude-code', pm_cwd=str(pm_cwd)),
                store=store, input_stream=io.StringIO(json.dumps({
                    'request': 'Add a focused test.', 'response': response})),
                return_record=True)
        old_file.unlink()
        self.assertEqual(parser_for_cli().parse_args([
            'task', 'adopt', '--from-pm-session', old_native,
            '--to-pm-session', 'pm-' + new_native]).action, 'adopt')
        with patch.dict(os.environ, env), \
                patch('llmtool.task_submission_cli.os.getcwd', return_value=str(pm_cwd)), \
                patch('llmtool.task_submission_cli.TaskStore', return_value=store):
            first_out = io.StringIO()
            with redirect_stdout(first_out):
                self.assertEqual(main(['task', 'adopt', '--from-pm-session', old_native,
                                       '--to-pm-session', 'pm-' + new_native]), 0)
            first = json.loads(first_out.getvalue())
            self.assertEqual(first, {'moved': [task['task_id']], 'skipped': []})
            self.assertNotIn(str(pm_cwd), first_out.getvalue())
            self.assertNotIn(old_file.name, first_out.getvalue())

            snapshot = store.get(task['task_id'])
            second_out = io.StringIO()
            with redirect_stdout(second_out):
                self.assertEqual(main(['task', 'adopt', '--from-pm-session', 'pm-' + old_native,
                                       '--to-pm-session', new_native]), 0)
            self.assertEqual(json.loads(second_out.getvalue()),
                             {'moved': [], 'skipped': []})
            self.assertEqual(store.get(task['task_id']), snapshot)

    def test_task_adopt_refusals_are_sanitized_and_do_not_change_records(self):
        pm_cwd = Path(self.temp.name).resolve() / 'pm-refusal-cwd'
        pm_cwd.mkdir()
        claude_home = Path(self.temp.name).resolve() / 'claude-refusal-home'
        old_native, new_native = str(uuid.uuid4()), str(uuid.uuid4())
        env = {'HOME': str(self.home), 'CLAUDE_CONFIG_DIR': str(claude_home)}
        old_file = active_pm_inbox.claude_session_file(old_native, str(pm_cwd), env)
        old_file.parent.mkdir(parents=True)
        old_file.write_text('{}\n', encoding='utf-8')
        new_file = active_pm_inbox.claude_session_file(new_native, str(pm_cwd), env)
        new_file.parent.mkdir(parents=True, exist_ok=True)
        new_file.write_text('{}\n', encoding='utf-8')
        store = TaskStore(Path(self.temp.name).resolve() / 'adopt-refusal-store')
        response = json.dumps({'schema_version': 1, 'summary': 'A bounded plan.',
            'developer_assignment': {'route': 'codex', 'risk': 'LOW',
                'goal': 'Add a focused test.', 'acceptance': ['The test passes.']}})
        with patch.dict(os.environ, env):
            task = run_task_record_pm(
                Namespace(stdin_json=True, pm_session_id=old_native,
                          pm_runtime='claude-code', pm_cwd=str(pm_cwd)),
                store=store, input_stream=io.StringIO(json.dumps({
                    'request': 'Add a focused test.', 'response': response})),
                return_record=True)
        before = store.get(task['task_id'])
        attempts = (
            (old_native, new_native, 'PM_SESSION_ADOPTION_OLD_SESSION_PRESENT'),
            (old_native, old_native, 'PM_SESSION_ADOPTION_KEYS_EQUAL'),
            ('malformed-key', new_native, 'PM_SESSION_ADOPTION_KEY_INVALID'),
        )
        with patch.dict(os.environ, env), \
                patch('llmtool.task_submission_cli.os.getcwd', return_value=str(pm_cwd)), \
                patch('llmtool.task_submission_cli.TaskStore', return_value=store):
            for old_key, new_key, code in attempts:
                out = io.StringIO()
                with self.subTest(code=code):
                    with redirect_stdout(out):
                        self.assertEqual(main(['task', 'adopt', '--from-pm-session', old_key,
                                               '--to-pm-session', new_key]), 1)
                    self.assertEqual(json.loads(out.getvalue()), {
                        'code': code, 'moved': [], 'skipped': []})
                    self.assertNotIn(str(pm_cwd), out.getvalue())
                    self.assertNotIn(old_file.name, out.getvalue())
                    self.assertEqual(store.get(task['task_id']), before)

    def test_task_adopt_appears_in_help_and_other_task_parser_remains(self):
        parser = parser_for_cli()
        task_action = next(action for action in parser._actions
                           if action.dest == 'command').choices['task']
        help_text = task_action.format_help()
        self.assertIn('adopt', help_text)
        self.assertEqual(parser.parse_args(['task', 'show', 'a' * 32]).action, 'show')

    def test_record_pm_reports_safe_assignment_limit_diagnostics(self):
        cases = (
            ({'route': 'codex', 'risk': 'LOW', 'goal': 'g' * 515,
              'acceptance': ['A test passes.']},
             'PM_RESPONSE_GOAL_TOO_LONG: goal is 515 bytes (max 512)'),
            ({'route': 'codex', 'risk': 'LOW', 'goal': 'Do one task.',
              'acceptance': ['x' * 539]},
             'PM_RESPONSE_ACCEPTANCE_ITEM_TOO_LONG: acceptance[0] is 539 bytes (max 512)'),
            ({'route': 'codex', 'risk': 'LOW', 'goal': 'Do one task.',
              'acceptance': ['ok'] * 22},
             'PM_RESPONSE_ACCEPTANCE_COUNT: 22 items (allowed 1-16)'),
        )
        for assignment, expected in cases:
            with self.subTest(expected=expected):
                response = json.dumps({
                    'schema_version': 1, 'summary': 'Bounded.',
                    'developer_assignment': assignment,
                })
                envelope = json.dumps({'request': 'Build feature', 'response': response})
                store = TaskStore(Path(self.temp.name).resolve() / 'over-limit')
                with self.assertRaises(LLMToolError) as raised:
                    run_task_record_pm(Namespace(stdin_json=True), store=store,
                                       input_stream=io.StringIO(envelope))
                self.assertEqual(str(raised.exception), expected)
                self.assertFalse(store.root.exists())

    def test_task_delegate_reports_acceptance_item_size_without_echoing_text(self):
        secretish = 'x' * 513
        args = Namespace(provider='codex', acceptance=[secretish])
        with self.assertRaises(LLMToolError) as raised:
            run_task_delegate(args, store=object())
        self.assertEqual(str(raised.exception),
                         'PM_RESPONSE_ACCEPTANCE_ITEM_TOO_LONG: acceptance[0] is '
                         '513 bytes (max 512)')
        self.assertNotIn(secretish, str(raised.exception))

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
            json.dumps({'request': 'Build feature', 'response':
                '{"schema_version":1,"summary":"Duplicate fields.",'
                '"developer_assignment":{"route":"opencode","route":"codex",'
                '"risk":"LOW","goal":"Do one task.",'
                '"acceptance":["A test passes."]}}'}),
        )
        for raw in cases:
            with self.subTest(raw=raw[:40]):
                store = TaskStore(Path(self.temp.name).resolve() /
                                  ('rejected-' + str(cases.index(raw))))
                with self.assertRaises(LLMToolError):
                    run_task_record_pm(Namespace(stdin_json=True), store=store,
                                       input_stream=io.StringIO(raw))
                self.assertFalse(store.root.exists())

    def test_record_pm_accepts_codex_low_risk_and_refuses_other_routes_or_risk(self):
        for route, risk, expected in (
                ('codex', 'LOW', True), ('opencode', 'LOW', True),
                ('company-gw', 'LOW', True), ('none', 'LOW', False),
                ('codex', 'OTHER', False)):
            with self.subTest(route=route, risk=risk):
                response = json.dumps({
                    'schema_version': 1, 'summary': 'Bounded.',
                    'developer_assignment': {
                        'route': route, 'risk': risk, 'goal': 'Do one task.',
                        'acceptance': ['A test passes.'],
                    },
                })
                envelope = json.dumps({'request': 'Build feature', 'response': response})
                store = TaskStore(Path(self.temp.name).resolve() /
                                  ('route-' + route + '-' + risk))
                if expected:
                    with redirect_stdout(io.StringIO()):
                        self.assertEqual(run_task_record_pm(
                            Namespace(stdin_json=True), store=store,
                            input_stream=io.StringIO(envelope)), 0)
                    record_path = next(store.root.glob('*.json'))
                    self.assertEqual(store.get(record_path.stem)['pm_assignment']['route'],
                                     route)
                else:
                    with self.assertRaises(LLMToolError):
                        run_task_record_pm(Namespace(stdin_json=True), store=store,
                                           input_stream=io.StringIO(envelope))
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

    def _pm_task_with_codex_assignment(self):
        acceptance = ['new test passes']
        response = json.dumps({
            'schema_version': 1, 'summary': 'Bounded low-risk task.',
            'developer_assignment': {
                'route': 'codex', 'risk': 'LOW', 'goal': 'Add one focused test.',
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
        self.assertEqual(developer['model'], opencode_direct_developer.MODEL)
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

    def test_opencode_direct_callback_accepts_docs_only_and_blocks_mixed_unregistered(self):
        for mixed in (False, True):
            with self.subTest(mixed=mixed):
                task = self._pm_task_with_opencode_assignment()
                output = io.StringIO()
                paths = ['README.md', 'sample.py'] if mixed else ['README.md']

                def execute(**kwargs):
                    self.assertEqual(kwargs['write_paths'], paths)
                    (self.executor_root / 'README.md').write_text(
                        'Updated executor documentation.\n')
                    if mixed:
                        (self.executor_root / 'sample.py').write_text('VALUE = 2\n')
                    return DirectResult(0, 'ses_Abc123', 'Updated documentation.', None,
                        'UNKNOWN', 'a' * 64, 32, 'CONFIRMED', 'opencode v2.0.32',
                        10, len(paths), 'b' * 64)

                args = self._opencode_delegate_args(
                    task, read_path=paths, write_path=paths)
                with patch.dict(os.environ, {'HOME': str(self.home)}), \
                        patch('llmtool.task_submission_cli.opencode_direct_developer.run',
                              side_effect=execute), redirect_stdout(output):
                    code = run_task_delegate(args)

                self.assertEqual(code, 1, output.getvalue())
                developer = json.loads(output.getvalue())['developer']
                callback = developer['direct_result_callback']
                if mixed:
                    self.assertEqual(callback['state'], 'WAITING_EXECUTOR')
                    self.assertIn('TEST_SCOPE_MISSING', callback['missing_gates'])
                else:
                    self.assertEqual(callback['evidence']['verification_state'], 'READY')
                    self.assertEqual(callback['evidence']['tests'], [{
                        'status': 'NOT_APPLICABLE_DOCS_ONLY',
                        'changed_paths': ['README.md'], 'source_sha': self.executor_head,
                        'diff_sha256': callback['evidence']['result']['diff_sha256'],
                        'result_head': None, 'docs_only': True,
                    }])
                    complete = Namespace(task_id=task['task_id'],
                        executor_task='executor-task-1', reviewer_task=None, pr=None)
                    with patch('llmtool.task_submission_cli.run_task_direct_result_event',
                               return_value=0) as resume:
                        self.assertEqual(run_task_complete(
                            complete, store=TaskStore(
                                Path(self.home) / '.local/state/llmtool/tasks')), 0)
                    resume.assert_called_once_with(
                        task['task_id'], 'executor-task-1', store=unittest.mock.ANY,
                        reviewer_adapter=None)
                subprocess.run(['/usr/bin/git', 'checkout', '--', 'README.md', 'sample.py'],
                               cwd=self.executor_root, check=True, capture_output=True)

    def _write_opencode_developer_model(self, model, *, steps=None):
        config = self.home / '.config/llmtool/config.yaml'
        config.parent.mkdir(parents=True, exist_ok=True)
        text = 'providers:\n  opencode-developer:\n    model: ' + model + '\n'
        if steps is not None:
            text += '    provider_options:\n      steps: ' + str(steps) + '\n'
        config.write_text(text)

    def test_opencode_developer_profile_free_model_is_used_and_recorded(self):
        task = self._pm_task_with_opencode_assignment()
        self._write_opencode_developer_model('opencode/other-model-free', steps=24)
        output = io.StringIO()

        def execute(**kwargs):
            self.assertEqual(kwargs['model'], 'opencode/other-model-free')
            self.assertEqual(kwargs['steps'], 24)
            (self.executor_root / 'sample.py').write_text('VALUE = 2\n')
            return DirectResult(0, 'ses_Abc123', 'Updated fixture.', None,
                'UNKNOWN', 'a' * 64, 1234, 'CONFIRMED', 'opencode v2.1.4',
                10, 1, 'b' * 64)

        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.opencode_direct_developer.run',
                      side_effect=execute) as invoke, \
                redirect_stdout(output):
            run_task_delegate(self._opencode_delegate_args(task))
        self.assertEqual(invoke.call_count, 1)
        developer = json.loads(output.getvalue())['developer']
        self.assertEqual(developer['model'], 'opencode/other-model-free')

    def test_opencode_developer_profile_steps_default_configured_and_invalid(self):
        with patch.dict(os.environ, {'HOME': str(self.home)}):
            self._write_opencode_developer_model('opencode/other-model-free')
            self.assertEqual(submission_cli._opencode_developer_profile(),
                             ('opencode/other-model-free', 12))
            self._write_opencode_developer_model('opencode/other-model-free', steps=24)
            self.assertEqual(submission_cli._opencode_developer_profile(),
                             ('opencode/other-model-free', 24))
            self._write_opencode_developer_model('opencode/other-model-free', steps=51)
            with self.assertRaisesRegex(LLMToolError,
                                        'steps must be an integer from 3 to 50'):
                submission_cli._opencode_developer_profile()

    def test_opencode_developer_profile_non_free_model_is_refused_before_claim(self):
        task = self._pm_task_with_opencode_assignment()
        for model in ('opencode/paid-model', 'other/name-free', 'opencode/a/b-free'):
            with self.subTest(model=model):
                self._write_opencode_developer_model(model)
                with patch.dict(os.environ, {'HOME': str(self.home)}), \
                        patch('llmtool.task_submission_cli.opencode_direct_developer.run') as invoke, \
                        self.assertRaisesRegex(LLMToolError, 'opencode/<name>-free'):
                    run_task_delegate(self._opencode_delegate_args(task))
                invoke.assert_not_called()
                stored = TaskStore(Path(self.home) / '.local/state/llmtool/tasks').get(
                    task['task_id'])
                self.assertIsNone(stored['developer'])

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

    def test_opencode_accepts_atomic_env2_pass_marker_refresh_after_bootstrap(self):
        task = self._pm_task_with_opencode_assignment()
        events = []
        result = DirectResult(0, 'ses_Abc123', 'Updated fixture.', None, 'UNKNOWN',
                              'a' * 64, 32, 'CONFIRMED', 'opencode v2.0.32',
                              10, 1, 'b' * 64)

        def bootstrap(_registration):
            report_path = (self.executor_root / 'tools' / 'llmtool' /
                           '.d6-bootstrap-report.json')
            report = json.loads(report_path.read_text(encoding='utf-8'))
            fingerprint = 'cpython3.13-darwin-arm64-abcdef123456'
            report['cache_fingerprint'] = fingerprint
            report_bytes = (json.dumps(report, sort_keys=True, separators=(',', ':')) +
                            '\n').encode()
            report_tmp = report_path.with_suffix('.tmp')
            report_tmp.write_bytes(report_bytes)
            report_tmp.chmod(0o600)
            os.replace(report_tmp, report_path)

            self.executor_registry['env2_pass_marker'].update(
                environment_fingerprint=fingerprint,
                report_sha256=hashlib.sha256(report_bytes).hexdigest())
            record_path = (Path(self.executor_common_dir) / 'llmtool-worktrees' /
                           'executor-task-1.json')
            record_tmp = record_path.with_suffix('.tmp')
            record_tmp.write_text(json.dumps(self.executor_registry), encoding='utf-8')
            record_tmp.chmod(0o600)
            os.replace(record_tmp, record_path)
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
        self.assertEqual(events, ['bootstrap', 'provider'])
        self.assertIn('"TEST_SCOPE_MISSING"', output.getvalue())

    def test_opencode_bootstrap_head_change_fails_before_provider_launch(self):
        task = self._pm_task_with_opencode_assignment()
        output = io.StringIO()

        def bootstrap(_registration):
            subprocess.run(['/usr/bin/git', 'commit', '--allow-empty', '-m',
                            'synthetic bootstrap drift'], cwd=self.executor_root,
                           check=True, capture_output=True)

        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli._bootstrap_registered_d6',
                      side_effect=bootstrap), \
                patch('llmtool.task_submission_cli.opencode_direct_developer.run') as launch, \
                redirect_stdout(output):
            code = run_task_delegate(self._opencode_delegate_args(task))

        result = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(result['developer']['state'], 'DEVELOPER_UNAVAILABLE')
        self.assertEqual(result['developer']['error_code'], 'OPENCODE_D6_BINDING_CHANGED')
        launch.assert_not_called()

    def test_opencode_bootstrap_rejection_blocks_launch_and_binds_process(self):
        self.bootstrap_patch.stop()
        task = self._pm_task_with_opencode_assignment()
        target_script = (self.executor_root / 'tools' / 'llmtool' / 'scripts' /
                         'd6_worktree_env.py')
        target_script.parent.mkdir(parents=True, exist_ok=True)
        marker_path = Path(self.temp.name) / 'untrusted-helper-ran'
        target_script.write_text(
            "from pathlib import Path\nPath(" + repr(str(marker_path)) +
            ").write_text('ran')\n", encoding='utf-8')
        subprocess.run(['/usr/bin/git', 'add', 'tools/llmtool/scripts/d6_worktree_env.py'],
                       cwd=self.executor_root, check=True)
        subprocess.run(['/usr/bin/git', 'commit', '-m', 'untrusted descendant helper'],
                       cwd=self.executor_root, check=True, capture_output=True)
        self.executor_registry['head_sha'] = subprocess.check_output(
            ['/usr/bin/git', 'rev-parse', 'HEAD'], cwd=self.executor_root,
            text=True).strip()
        report_path = (self.executor_root / 'tools' / 'llmtool' /
                       '.d6-bootstrap-report.json')
        report = json.loads(report_path.read_text(encoding='utf-8'))
        report['head_sha'] = self.executor_registry['head_sha']
        report_bytes = (json.dumps(report, sort_keys=True, separators=(',', ':')) + '\n').encode()
        report_path.write_bytes(report_bytes)
        report_path.chmod(0o600)
        self.executor_registry['env2_pass_marker'].update(
            submitted_head_sha=self.executor_registry['head_sha'],
            current_head_sha=self.executor_registry['head_sha'],
            report_sha256=hashlib.sha256(report_bytes).hexdigest())
        record_path = (Path(self.executor_common_dir) / 'llmtool-worktrees' /
                       'executor-task-1.json')
        record_path.write_text(json.dumps(self.executor_registry))
        record_path.chmod(0o600)
        captured = []
        real_run = subprocess.run

        def reject_env_bootstrap(argv, *args, **kwargs):
            if '-c' in argv:
                captured.append((argv, kwargs))
                return subprocess.CompletedProcess(argv, 23)
            return real_run(argv, *args, **kwargs)

        output = io.StringIO()
        injected = {
            'HOME': str(self.home), 'MAKEFLAGS': '-f attacker.mk',
            'MAKEFILES': 'attacker.mk', 'PYTHONPATH': '/tmp/shadow',
            'PYTHONHOME': '/tmp/other-python', 'GNUMAKEFLAGS': '--eval=bad',
            'MFLAGS': '-f attacker.mk',
        }
        with patch.dict(os.environ, injected), \
                patch('llmtool.task_submission_cli._trusted_d6_helper_source',
                      return_value=(self.executor_root, b'raise SystemExit(0)')), \
                patch.object(submission_cli.subprocess, 'run',
                             side_effect=reject_env_bootstrap), \
                patch('llmtool.task_submission_cli.opencode_direct_developer.run') as launch, \
                redirect_stdout(output):
            code = run_task_delegate(self._opencode_delegate_args(task))

        result = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(result['developer']['state'], 'DEVELOPER_UNAVAILABLE')
        self.assertEqual(result['developer']['error_code'], 'ENV_BOOTSTRAP_REJECTED')
        launch.assert_not_called()
        self.assertEqual(len(captured), 1)
        argv, kwargs = captured[0]
        self.assertEqual(argv, [sys.executable, '-B', '-I', '-c',
                                'raise SystemExit(0)', '--task-id', 'executor-task-1',
                                '--worktree-root', str(self.executor_root)])
        self.assertNotIn(str(target_script), argv)
        self.assertFalse(marker_path.exists())
        self.assertEqual(Path(kwargs['cwd']), self.executor_root)
        self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
        self.assertEqual(kwargs['stdout'], subprocess.DEVNULL)
        self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
        self.assertFalse(kwargs['check'])
        self.assertEqual(set(kwargs['env']), {
            'PATH', 'HOME', 'LC_ALL', 'LANG', 'PIP_CONFIG_FILE',
            'GIT_CONFIG_NOSYSTEM', 'GIT_CONFIG_GLOBAL', 'GIT_ATTR_NOSYSTEM',
            'GIT_OPTIONAL_LOCKS', 'GIT_NO_LAZY_FETCH', 'GIT_TERMINAL_PROMPT'})
        for name in ('MAKEFLAGS', 'MAKEFILES', 'PYTHONPATH', 'PYTHONHOME',
                     'GNUMAKEFLAGS', 'MFLAGS'):
            self.assertNotIn(name, kwargs['env'])

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

    def test_direct_taskstore_assignment_rechecks_policy_and_denies_paused_worker(self):
        task = self._pm_task_with_opencode_assignment()
        policy = json.loads(self.policy_path.read_text(encoding='utf-8'))
        policy['workers']['opencode-developer']['dispatch'] = 'PAUSED'
        policy['workers']['opencode-developer']['reason'] = 'test pause'
        self.policy_path.write_text(json.dumps(policy), encoding='utf-8')
        args = self._opencode_delegate_args(task)
        out = io.StringIO()
        store = TaskStore(Path(self.home) / '.local/state/llmtool/tasks')
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.opencode_direct_developer.run') as invoke, \
                redirect_stdout(out):
            with self.assertRaises(worker_dispatch.WorkerDispatchDenied):
                run_task_delegate(args, store=store)
        invoke.assert_not_called()
        saved = store.get(task['task_id'])
        self.assertIsNone(saved['developer'])
        self.assertEqual(saved['dispatch_audit'][-1]['decision'], 'BLOCKED')
        self.assertEqual(saved['dispatch_audit'][-1]['reason_code'], 'OWNER_PAUSED')

        policy['workers']['opencode-developer']['dispatch'] = 'ALLOWED'
        policy['workers']['opencode-developer']['reason'] = 'test resume'
        self.policy_path.write_text(json.dumps(policy), encoding='utf-8')
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.opencode_direct_developer.run',
                      return_value=DirectResult(0, 'ses_Abc123', 'ok', None,
                          'CLI_REPORTED', 'a' * 64, 1, 'CONFIRMED',
                          'opencode v2.0.32', 1, 1, 'b' * 64)) as invoke, \
                redirect_stdout(io.StringIO()):
            # Assignment is authorized after the policy file changes; the policy is reread.
            result = run_task_delegate(args, store=store)
        self.assertEqual(result, 1)
        invoke.assert_called_once()
        saved = store.get(task['task_id'])
        self.assertEqual([entry['decision'] for entry in saved['dispatch_audit']],
                         ['BLOCKED', 'ALLOWED', 'ALLOWED'])

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

    def _codex_delegate_args(self, task, **changes):
        values = dict(task_id=task['task_id'], provider='codex',
                      selection_source='PM', executor_task='executor-task-1',
                      read_path=['sample.py'], write_path=['sample.py'],
                      acceptance=['new test passes'],
                      egress_basis='Owner-approved public synthetic source only',
                      context='VALUE = 1\n')
        values.update(changes)
        return Namespace(**values)

    def test_codex_delegate_persists_scoped_proposal_and_unknown_usage(self):
        task = self._pm_task_with_codex_assignment()
        patch_text = ('diff --git a/sample.py b/sample.py\n'
                      '--- a/sample.py\n+++ b/sample.py\n@@ -1 +1 @@\n'
                      '-VALUE = 1\n+VALUE = 2\n')
        codex_config = codex_developer_config()
        output = io.StringIO()
        args = self._codex_delegate_args(task)
        bind_executor_task = submission_cli.task_completion.bind_executor_task
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.resolve_profile_config',
                      return_value=codex_config) as resolve, \
                patch('llmtool.task_submission_cli.api_key',
                      side_effect=AssertionError('Codex CLI credentials must remain CLI-owned')), \
                patch.object(submission_cli.task_completion, 'bind_executor_task',
                             wraps=bind_executor_task) as bind_executor, \
                patch.object(CodexCLIProvider, 'complete', autospec=True,
                             return_value=Reply(patch_text, {'elapsed_seconds': 0.25,
                                                             'model': 'gpt-6-luna'})) as complete, \
                redirect_stdout(output):
            code = run_task_delegate(args)
        self.assertEqual(code, 0, output.getvalue())
        result = json.loads(output.getvalue())
        developer = result['developer']
        self.assertEqual(resolve.call_args.args, ('codex-developer',))
        self.assertEqual(bind_executor.call_count, 2)
        complete.assert_called_once()
        client = complete.call_args.args[0]
        messages = complete.call_args.args[1]
        self.assertIsInstance(client, CodexCLIProvider)
        self.assertEqual(client.provider, codex_config['providers']['codex-developer'])
        capabilities = get_provider_capabilities('codex-cli').to_dict()
        self.assertFalse(capabilities['code_edit'])
        self.assertFalse(capabilities['shell'])
        self.assertFalse(capabilities['git'])
        self.assertEqual(OUTPUT_LIMIT, 1024 * 1024)
        argv = client._argv('/usr/bin/codex', '/tmp/llmtool-isolated-test')
        settings = [argv[index + 1] for index, item in enumerate(argv) if item == '-c']
        self.assertIn('permissions.llmtool-text.extends=":read-only"', settings)
        self.assertIn('permissions.llmtool-text.network.enabled=false', settings)
        self.assertIn('features.shell_tool=false', settings)
        self.assertIn('features.unified_exec=false', settings)
        self.assertNotIn('--sandbox', argv)
        with client._temporary_directory() as isolated:
            isolated_path = Path(isolated).resolve()
            self.assertFalse(isolated_path.is_relative_to(self.executor_root))
            self.assertEqual(list(isolated_path.iterdir()), [])
        self.assertEqual(messages[1]['content'][:1], '{')
        self.assertEqual(developer['state'], 'DEVELOPER_RESPONDED')
        self.assertEqual(developer['provider_profile'], 'codex')
        self.assertEqual(developer['provider_type'], 'codex-cli')
        self.assertEqual(developer['model'], 'gpt-6-luna')
        self.assertIsNone(developer['max_tokens'])
        self.assertIsNone(developer['usage'])
        self.assertEqual(developer['usage_status'], 'UNKNOWN')
        self.assertEqual(developer['artifact'], patch_text)
        self.assertEqual(developer['artifact_sha256'], hashlib.sha256(
            patch_text.encode()).hexdigest())
        self.assertEqual(result['completion']['evidence']['source_sha'], self.executor_head)
        self.assertEqual(result['completion']['executor_task_id'], 'executor-task-1')
        packet_json, _context_section = messages[1]['content'].split(
            TEXT_DEVELOPER_CONTEXT_SEPARATOR, 1)
        packet = json.loads(packet_json)
        self.assertEqual(packet['task_contract_digest'], task['task_binding']['contract_digest'])
        self.assertEqual(packet['source_sha'], self.executor_head)
        self.assertEqual(packet['write_paths'], ['sample.py'])
        self.assertIn('codex-cli code Developer', messages[0]['content'])
        saved = TaskStore(Path(self.home) / '.local/state/llmtool/tasks').get(task['task_id'])
        self.assertEqual(saved['developer']['artifact'], patch_text)
        self.assertEqual(saved['dispatch_audit'][-1]['worker_key'], 'codex-developer')
        self.assertEqual(saved['dispatch_audit'][-1]['decision'], 'ALLOWED')
        self.assertEqual(saved['dispatch_audit'][-1]['reason_code'], 'OWNER_POLICY_ALLOWED')

    def test_captured_source_files_allow_an_ordinary_modify_proposal(self):
        source_files = submission_cli._text_developer_source_files(
            self.executor_root, self.executor_head, ['sample.py'])
        self.assertIsInstance(source_files, submission_cli._DeveloperSourceFiles)
        self.assertIn('sample.py', source_files.existing_paths)
        source = source_files['sample.py']
        old_line = source.splitlines()[0]
        new_line = old_line + '  # updated'
        proposal = ('diff --git a/sample.py b/sample.py\n'
                    '--- a/sample.py\n+++ b/sample.py\n'
                    f'@@ -1 +1 @@\n-{old_line}\n+{new_line}\n')
        artifact, _tokens, rejection = submission_cli._developer_normalized_output(
            proposal, ['sample.py'], source_files=source_files)
        self.assertIsNone(rejection)
        self.assertEqual(artifact, proposal)

    def test_text_developer_recounts_hunks_and_rejects_fake_context(self):
        source_files = submission_cli._text_developer_source_files(
            self.executor_root, self.executor_head, ['sample.py'])
        proposal = (
            'diff --git a/sample.py b/sample.py\n'
            '--- a/sample.py\n+++ b/sample.py\n'
            '@@ -1,9 +1,8 @@\n'
            ' VALUE = 1\n'
            '+# updated\n')
        artifact, _tokens, rejection = submission_cli._developer_normalized_output(
            proposal, ['sample.py'], source_files=source_files)
        self.assertIsNone(rejection)
        self.assertIn('@@ -1 +1,2 @@', artifact)
        self.assertTrue(submission_cli._text_developer_apply_check(
            self.executor_root, self.executor_head, artifact))

        fake_context = proposal.replace(' VALUE = 1', ' ...')
        artifact, _tokens, rejection = submission_cli._developer_normalized_output(
            fake_context, ['sample.py'], source_files=source_files)
        self.assertIsNone(artifact)
        self.assertEqual(rejection, 'DEVELOPER_OUTPUT_INVALID_DIFF')

    def test_captured_source_files_admit_allowed_addition_and_deletion(self):
        added = 'tools/llmtool/new_module.py'
        source_files = submission_cli._text_developer_source_files(
            self.executor_root, self.executor_head, ['sample.py', added])
        self.assertIsNotNone(source_files)
        self.assertEqual(source_files[added], '')
        self.assertNotIn(added, source_files.existing_paths)

        new_file = (f'diff --git a/{added} b/{added}\nnew file mode 100644\n'
                    f'--- /dev/null\n+++ b/{added}\n'
                    '@@ -0,0 +1 @@\n+VALUE = 2\n')
        artifact, _tokens, rejection = submission_cli._developer_normalized_output(
            new_file, ['sample.py', added], source_files=source_files)
        self.assertIsNone(rejection)
        self.assertEqual(artifact, new_file)
        self.assertTrue(submission_cli._text_developer_apply_check(
            self.executor_root, self.executor_head, artifact))

        existing = source_files['sample.py']
        deletion = ('diff --git a/sample.py b/sample.py\n'
                    'deleted file mode 100644\n--- a/sample.py\n+++ /dev/null\n'
                    f'@@ -1 +0,0 @@\n-{existing.rstrip()}\n')
        artifact, _tokens, rejection = submission_cli._developer_normalized_output(
            deletion, ['sample.py', added], source_files=source_files)
        self.assertIsNone(rejection)
        self.assertEqual(artifact, deletion)
        self.assertTrue(submission_cli._text_developer_apply_check(
            self.executor_root, self.executor_head, artifact))

    def test_codex_delegate_missing_developer_profile_fails_closed_without_pm_fallback(self):
        task = self._pm_task_with_codex_assignment()
        output = io.StringIO()
        snapshot = Namespace(root=str(self.executor_root),
                             common_dir=self.executor_common_dir,
                             branch='feat/test-executor', head=self.executor_head)
        registration = Namespace(
            task_id='executor-task-1', trusted_agent_id='executor-agent-1',
            session_ref='Executor Session 1', native_session_id=None,
            root=str(self.executor_root), common_dir=self.executor_common_dir,
            branch='feat/test-executor')
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch.object(submission_cli.task_completion, 'collect_git_snapshot',
                             return_value=snapshot), \
                patch.object(submission_cli.task_completion, 'bind_executor_task',
                             return_value=(registration, snapshot)), \
                patch('llmtool.task_submission_cli.resolve_profile_config',
                      side_effect=LLMToolError(
                          'requested provider profile is not defined')) as resolve, \
                patch('llmtool.task_submission_cli.create_client',
                      side_effect=AssertionError('unconfigured profile must not create client')), \
                redirect_stdout(output):
            code = run_task_delegate(self._codex_delegate_args(task))
        result = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        resolve.assert_called_once_with('codex-developer', require_configured=True)
        self.assertEqual(result['developer']['state'], 'DEVELOPER_UNAVAILABLE')
        self.assertEqual(result['developer']['error_code'], 'DEVELOPER_PROFILE_UNAVAILABLE')
        self.assertIsNone(result['developer']['model'])

    def test_codex_d6_revalidation_failure_blocks_provider_call(self):
        task = self._pm_task_with_codex_assignment()
        original_bind = submission_cli.task_completion.bind_executor_task
        bind_calls = []

        def drift_after_dispatch(*args, **kwargs):
            bind_calls.append((args, kwargs))
            if len(bind_calls) == 1:
                return original_bind(*args, **kwargs)
            raise LLMToolError('simulated D6 registry drift')

        output = io.StringIO()
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.resolve_profile_config',
                      return_value=codex_developer_config()), \
                patch('llmtool.task_submission_cli.api_key',
                      side_effect=AssertionError('Codex CLI credentials must remain CLI-owned')), \
                patch.object(submission_cli.task_completion, 'bind_executor_task',
                             side_effect=drift_after_dispatch), \
                patch.object(CodexCLIProvider, 'complete', autospec=True) as complete, \
                redirect_stdout(output):
            code = run_task_delegate(self._codex_delegate_args(task))
        self.assertEqual(code, 1)
        self.assertEqual(len(bind_calls), 2)
        complete.assert_not_called()
        result = json.loads(output.getvalue())
        self.assertEqual(result['developer']['state'], 'DEVELOPER_UNAVAILABLE')
        self.assertEqual(result['developer']['error_code'],
                         'DEVELOPER_CALL_BINDING_INVALID')

    def test_codex_proposal_is_host_size_and_scope_validated(self):
        cases = (
            ('DEVELOPER_OUTPUT_OUT_OF_SCOPE',
             'diff --git a/outside.py b/outside.py\n'
             '--- a/outside.py\n+++ b/outside.py\n@@ -1 +1 @@\n-old\n+new\n'),
            ('DEVELOPER_OUTPUT_TOO_LARGE',
             'diff --git a/sample.py b/sample.py\n' + ('x' * (RESPONSE_LIMIT + 1))),
        )
        for reason, proposal in cases:
            with self.subTest(reason=reason):
                task = self._pm_task_with_codex_assignment()
                client = FakeClient(Reply(proposal, {'elapsed_seconds': 0.1}))
                output = io.StringIO()
                with patch.dict(os.environ, {'HOME': str(self.home)}), \
                        patch('llmtool.task_submission_cli.resolve_profile_config',
                              return_value=codex_developer_config()), \
                        patch('llmtool.task_submission_cli.api_key',
                              side_effect=AssertionError('Codex CLI credentials must remain CLI-owned')), \
                        patch('llmtool.task_submission_cli.create_client',
                              return_value=client), redirect_stdout(output):
                    code = run_task_delegate(self._codex_delegate_args(task))
                self.assertEqual(code, 1)
                self.assertEqual(len(client.calls), 1)
                result = json.loads(output.getvalue())
                self.assertEqual(result['developer']['state'],
                                 'DEVELOPER_OUTPUT_REJECTED')
                self.assertEqual(result['developer']['error_code'], reason)
                self.assertIsNone(result['developer']['artifact'])
                self.assertNotIn(proposal[:64], output.getvalue())

    def test_codex_paused_or_invalid_policy_never_calls_provider(self):
        for policy_change, expected_reason in (
                ('paused', 'OWNER_PAUSED'), ('missing', 'WORKER_POLICY_INVALID')):
            with self.subTest(policy_change=policy_change):
                task = self._pm_task_with_codex_assignment()
                policy = json.loads(self.policy_path.read_text(encoding='utf-8'))
                if policy_change == 'paused':
                    policy['workers']['codex-developer']['dispatch'] = 'PAUSED'
                else:
                    policy['workers'].pop('codex-developer')
                self.policy_path.write_text(json.dumps(policy), encoding='utf-8')
                client = FakeClient(Reply('must not be returned', {}))
                args = self._codex_delegate_args(task)
                with patch.dict(os.environ, {'HOME': str(self.home)}), \
                        patch('llmtool.task_submission_cli.resolve_profile_config',
                              return_value=config('codex-cli')), \
                        patch('llmtool.task_submission_cli.create_client',
                              side_effect=AssertionError('denied route must not build provider')), \
                        redirect_stdout(io.StringIO()):
                    with self.assertRaises(worker_dispatch.WorkerDispatchDenied):
                        run_task_delegate(args)
                saved = TaskStore(Path(self.home) / '.local/state/llmtool/tasks').get(task['task_id'])
                self.assertIsNone(saved['developer'])
                self.assertEqual(saved['dispatch_audit'][-1]['decision'], 'BLOCKED')
                self.assertEqual(saved['dispatch_audit'][-1]['reason_code'], expected_reason)

    def test_codex_d6_mismatch_and_unknown_provider_result_are_not_replayed(self):
        task = self._pm_task_with_codex_assignment()
        registry = Path(self.executor_common_dir) / 'llmtool-worktrees' / 'executor-task-1.json'
        record = json.loads(registry.read_text(encoding='utf-8'))
        record['head_sha'] = '0' * 40
        registry.write_text(json.dumps(record))
        client = FakeClient(Reply('unused', {}))
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.resolve_profile_config',
                      return_value=config('codex-cli')), \
                patch('llmtool.task_submission_cli.create_client',
                      side_effect=AssertionError('invalid D6 binding must block client creation')):
            with self.assertRaises(LLMToolError):
                run_task_delegate(self._codex_delegate_args(task))
        registry.write_text(json.dumps(self.executor_registry))

        task = self._pm_task_with_codex_assignment()
        client = FakeClient(error=RuntimeError('simulated provider failure'))
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.resolve_profile_config',
                      return_value=config('codex-cli')), \
                patch('llmtool.task_submission_cli.create_client', return_value=client), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(run_task_delegate(self._codex_delegate_args(task)), 1)
            with self.assertRaises(LLMToolError):
                run_task_delegate(self._codex_delegate_args(task))
        self.assertEqual(len(client.calls), 1)
        saved = TaskStore(Path(self.home) / '.local/state/llmtool/tasks').get(task['task_id'])
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertIsNone(saved['developer']['usage'])

    def test_task_complete_passes_executor_changed_paths_to_github_collector(self):
        task = self._pm_task_with_assignment()
        target = 'sample.py'
        patch_text = (f'diff --git a/{target} b/{target}\n'
                      f'--- a/{target}\n+++ b/{target}\n'
                      '@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n')
        client = FakeClient(Reply(patch_text, {
            'prompt_tokens': 20, 'completion_tokens': 15, 'total_tokens': 35,
            'served_model': 'qwen3.8-27b',
        }))
        store = TaskStore(Path(self.home) / '.local/state/llmtool/tasks')
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.resolve_profile_config',
                      return_value=config('openai-compatible', 'gw')), \
                patch('llmtool.task_submission_cli.api_key', return_value='not-persisted'), \
                patch('llmtool.task_submission_cli.create_client', return_value=client), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(run_task_delegate(self._delegate_args(
                task, read_path=[target], write_path=[target], context='VALUE = 1\n'),
                store=store), 0)

        changed_paths = [target]
        result_sha = '4' * 40
        git_evidence = {
            'root': str(self.executor_root), 'common_dir': self.executor_common_dir,
            'branch': 'feat/test-executor', 'source_sha': self.executor_head,
            'result_head': result_sha, 'changed_paths': changed_paths,
            'diff_sha256': '5' * 64, 'executor_identity': 'executor-agent-1',
        }
        github_result = {
            'evidence': {'pr_number': 10, 'head_sha': result_sha,
                         'formal_review_state': 'UNVERIFIED', 'required_checks': [],
                         'successful_checks': [], 'ci_head_sha': result_sha},
            'missing_gates': ['CI_REQUIRED_CHECKS_MISSING'],
        }
        args = Namespace(task_id=task['task_id'], executor_task='executor-task-1',
                         reviewer_task='reviewer-task-1', pr=10)
        with patch('llmtool.task_submission_cli.task_completion.collect_executor_result',
                   return_value=git_evidence), \
                patch('llmtool.task_submission_cli.task_completion.run_bounded_tests',
                      return_value=[{'path': target, 'source_sha': result_sha,
                                     'exit_code': 0, 'output_sha256': '6' * 64}]), \
                patch('llmtool.task_submission_cli.task_completion.collect_github_evidence',
                      return_value=github_result) as github, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(run_task_complete(args, store=store), 1)

        self.assertEqual(github.call_args.kwargs['changed_paths'], changed_paths)

    def _remote_test_task(self):
        path = 'tools/llmtool/tests/test_task_submission_cli.py'
        return {
            'task_id': 'a' * 32, 'run_id': 'b' * 32,
            'task_binding': {'contract_digest': 'c' * 64},
            'task_contract': {'test_commands': [{
                'argv': ['python3', '-m', 'unittest', 'discover', '-s',
                         'tools/llmtool/tests', '-p',
                         'test_task_submission_cli.py'],
                'cwd': '.'}]},
            'developer': {'read_paths': [path], 'write_paths': [path]},
        }

    def _write_remote_test_config(self, value):
        config_path = self.home / '.config/llmtool/remote-test.json'
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            value if isinstance(value, str) else json.dumps(value), encoding='utf-8')
        config_path.chmod(0o600)
        return config_path

    def _remote_test_config_loader(self, config_path):
        """Load exactly this test-owned config; never consult the host HOME."""
        def load():
            if not config_path.exists():
                raise FileNotFoundError
            return remote_test._config(str(config_path))
        return load

    def _remote_local_evidence(self):
        return ({'test_policy': 'LOCAL', 'root': str(self.executor_root),
                 'source_sha': self.executor_head},
                {'diff_sha256': 'd' * 64,
                 'changed_paths': ['tools/llmtool/tests/test_task_submission_cli.py']})

    def test_invalid_remote_test_config_fails_closed_without_local_tests(self):
        task = self._remote_test_task()
        config_path = self._write_remote_test_config('{invalid json')
        config_loader = self._remote_test_config_loader(config_path)
        evidence, result = self._remote_local_evidence()
        with patch('llmtool.task_submission_cli.task_completion.'
                      'run_bounded_worktree_tests') as local:
            self.assertEqual(submission_cli._remote_test_config_state(config_loader),
                             'INVALID')
            with self.assertRaises(LLMToolError) as raised:
                submission_cli._remote_receipt_test_policy(
                    task, config_loader=config_loader)
            self.assertEqual(str(raised.exception), 'REMOTE_TEST_CONFIG_INVALID')
            self.assertEqual(raised.exception.completion_code,
                             'REMOTE_TEST_CONFIG_INVALID')
            with self.assertRaises(LLMToolError) as blocked:
                submission_cli._direct_result_tests(
                    task, evidence, result, task['developer'],
                    config_loader=config_loader)
        local.assert_not_called()
        self.assertEqual(str(blocked.exception), 'REMOTE_TEST_CONFIG_INVALID')
        self.assertEqual(blocked.exception.completion_code,
                         'REMOTE_TEST_CONFIG_INVALID')

    def test_unconfigured_remote_test_keeps_local_test_behaviour(self):
        task = self._remote_test_task()
        config_loader = self._remote_test_config_loader(
            self.home / '.config/llmtool/remote-test.json')
        evidence, result = self._remote_local_evidence()
        with patch('llmtool.task_submission_cli.task_completion.'
                      'run_bounded_worktree_tests', return_value=[]) as local:
            self.assertEqual(submission_cli._remote_test_config_state(config_loader),
                             'MISSING')
            self.assertEqual(submission_cli._remote_receipt_test_policy(
                task, config_loader=config_loader), 'LOCAL')
            tests = submission_cli._direct_result_tests(
                task, evidence, result, task['developer'],
                config_loader=config_loader)
        self.assertEqual(tests, [])
        local.assert_called_once()

    def test_valid_remote_test_config_keeps_remote_receipt_behaviour(self):
        task = self._remote_test_task()
        config_path = self._write_remote_test_config({
            'host': 'ec2.example', 'user': 'tester',
            'repo_path': '/srv/repo', 'python': '/usr/bin/python3'})
        config_loader = self._remote_test_config_loader(config_path)
        evidence, result = self._remote_local_evidence()
        evidence['test_policy'] = 'REMOTE_RECEIPT'
        with patch('llmtool.task_submission_cli.task_completion.'
                      'run_bounded_worktree_tests') as local:
            self.assertEqual(submission_cli._remote_test_config_state(config_loader),
                             'VALID')
            self.assertEqual(submission_cli._remote_receipt_test_policy(
                task, config_loader=config_loader),
                             'REMOTE_RECEIPT')
            tests = submission_cli._direct_result_tests(
                task, evidence, result, task['developer'],
                config_loader=config_loader)
        local.assert_not_called()
        self.assertEqual(tests[0]['status'], 'DEFERRED_REMOTE_RECEIPT')
        self.assertEqual(tests[0]['path'],
                         'tools/llmtool/tests/test_task_submission_cli.py')

    def test_company_gw_patch_is_persisted_with_separate_logical_session_and_usage(self):
        acceptance = ['new test passes']
        pm_response = json.dumps({
            'schema_version': 1, 'summary': 'Bounded low-risk task.',
            'developer_assignment': {
                'route': 'company-gw', 'risk': 'LOW',
                'goal': 'Add one focused test.', 'acceptance': acceptance,
            },
        })
        store = TaskStore(Path(self.home) / '.local/state/llmtool/tasks')
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(run_task_record_pm(
                Namespace(stdin_json=True), store=store,
                input_stream=io.StringIO(json.dumps({
                    'request': 'Implement one small test change.',
                    'response': pm_response,
                }))), 0)
        task = store.get(next(store.root.glob('*.json')).stem)
        target = 'sample.py'
        patch_text = (f'diff --git a/{target} b/{target}\n'
                      f'--- a/{target}\n+++ b/{target}\n'
                      '@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n')
        client = FakeClient(Reply(patch_text, {
            'prompt_tokens': 20, 'completion_tokens': 15, 'total_tokens': 35,
            'served_model': 'qwen3.8-27b',
        }))
        args = self._delegate_args(task, read_path=[target], write_path=[target],
                                   context='VALUE = 1\n')
        output = io.StringIO()
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.resolve_profile_config',
                      return_value=config('openai-compatible', 'gw')), \
                patch('llmtool.task_submission_cli.api_key', return_value='not-persisted'), \
                patch('llmtool.task_submission_cli.create_client', return_value=client), \
                redirect_stdout(output):
            code = run_task_delegate(args, store=store)
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
        self.assertEqual(developer['usage']['served_model'], 'qwen3.8-27b')
        stored = store.get(task['task_id'])
        self.assertEqual(stored['developer']['usage']['served_model'], 'qwen3.8-27b')
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
        self.assertIn('@@ -1,3 +1,3 @@\n unchanged line\n-old\n+new\n unchanged line', system_prompt)
        self.assertIn('New files use --- /dev/null and +++ b/path', system_prompt)
        self.assertIn('deletions use --- a/path and +++ /dev/null', system_prompt)
        self.assertIn('Renames are not supported', system_prompt)
        self.assertIn('The first line must be the diff --git header', system_prompt)
        self.assertIn('blank lines', system_prompt)
        self.assertIn('Copy context lines verbatim', system_prompt)
        packet_text = client.calls[0][1]['content']
        packet_json, context_section = packet_text.split(
            TEXT_DEVELOPER_CONTEXT_SEPARATOR, 1)
        developer_packet = json.loads(packet_json)
        self.assertEqual(developer_packet['pm_selected_goal'], 'Add one focused test.')
        self.assertEqual(developer_packet['acceptance'], ['new test passes'])
        self.assertNotIn('context', developer_packet)
        self.assertTrue(context_section.startswith('Context:\nVALUE = 1\n'))
        self.assertTrue(context_section.endswith(
            'Return only the unified diff, starting with "diff --git".'))
        self.assertIn('The model has no tools, cannot read files, and all needed file content is provided below.',
                      packet_text)
        self.assertEqual(developer_packet['write_paths'], [target])
        self.assertNotIn('original_request', developer_packet)
        self.assertNotIn('pm_response', developer_packet)
        self.assertNotIn('Implement a health-check', packet_text)
        self.assertNotIn('Bounded test-only task.', packet_text)
        saved = TaskStore(Path(self.home) / '.local/state/llmtool/tasks').get(task['task_id'])
        self.assertEqual(saved['developer']['artifact'], patch_text)
        self.assertEqual(len(client.calls), 1)

    def test_apply_check_failure_is_durably_rejected(self):
        task = self._pm_task_with_assignment()
        client = FakeClient(Reply(
            'diff --git a/sample.py b/sample.py\n--- a/sample.py\n+++ b/sample.py\n'
            '@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n', {}))
        output = io.StringIO()
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.resolve_profile_config',
                      return_value=config('openai-compatible', 'gw')), \
                patch('llmtool.task_submission_cli.api_key', return_value=None), \
                patch('llmtool.task_submission_cli.create_client', return_value=client), \
                patch('llmtool.task_submission_cli._text_developer_apply_check',
                      return_value=False), redirect_stdout(output):
            code = run_task_delegate(self._delegate_args(
                task, read_path=['sample.py'], write_path=['sample.py']))
        self.assertEqual(code, 1)
        developer = json.loads(output.getvalue())['developer']
        self.assertEqual(developer['state'], 'DEVELOPER_OUTPUT_REJECTED')
        self.assertEqual(developer['error_code'], 'DEVELOPER_OUTPUT_APPLY_FAILED')
        self.assertIsNone(developer['artifact'])
        saved = TaskStore(Path(self.home) / '.local/state/llmtool/tasks').get(task['task_id'])
        self.assertEqual(saved['developer']['error_code'], 'DEVELOPER_OUTPUT_APPLY_FAILED')

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

    def test_markdown_fenced_company_patch_is_normalized_before_persistence(self):
        target = 'sample.py'
        fenced_patch = (
            '```diff\n'
            f'diff --git a/{target} b/{target}\n'
            f'--- a/{target}\n+++ b/{target}\n@@ -1 +1 @@\n'
            '-VALUE = 1\n+FENCED_PROVIDER_PATCH_SENTINEL\n'
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
            args = self._delegate_args(task, read_path=[target], write_path=[target],
                                       context='VALUE = 1\n')
            code = run_task_delegate(args)
            with self.assertRaisesRegex(
                    LLMToolError,
                    'developer assignment already exists; duplicate dispatch refused'):
                run_task_delegate(args)

        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result['developer']['state'], 'DEVELOPER_RESPONDED')
        self.assertIsNone(result['developer']['error_code'])
        self.assertIn('OUTER_FENCE_STRIPPED', result['developer']['normalizations'])
        self.assertNotIn('```', result['developer']['artifact'])
        self.assertIn('FENCED_PROVIDER_PATCH_SENTINEL', result['developer']['artifact'])
        self.assertEqual(result['developer']['usage']['total_tokens'], 45)
        self.assertEqual(len(client.calls), 1)
        saved = TaskStore(Path(self.home) / '.local/state/llmtool/tasks').get(task['task_id'])
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_RESPONDED')
        self.assertIsNone(saved['developer']['error_code'])
        self.assertIn('OUTER_FENCE_STRIPPED', saved['developer']['normalizations'])
        self.assertNotIn('```', saved['developer']['artifact'])
        self.assertEqual(saved['developer']['usage']['total_tokens'], 45)
        serialized = json.dumps(saved)
        self.assertNotIn(fenced_patch, serialized)

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

    def test_mode_metadata_patch_is_rejected_without_persistence(self):
        # This text-proposal route never applies a patch, so the checks below
        # pin non-persistence. Real artifact collection is covered on the
        # opencode direct route, where the Developer edits a live worktree.
        target = 'sample.py'
        patch_text = (
            f'diff --git a/{target} b/{target}\n'
            'index 1111111..2222222 100644\n'
            'old mode 100644\n'
            'new mode 100755\n'
            f'--- a/{target}\n+++ b/{target}\n'
            '@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n'
        )
        source = self.executor_root / target
        source.chmod(0o644)
        original = source.read_bytes()
        task = self._pm_task_with_assignment()
        client = FakeClient(Reply(patch_text, {
            'prompt_tokens': 20, 'completion_tokens': 25, 'total_tokens': 45,
        }))
        output = io.StringIO()
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.resolve_profile_config',
                      return_value=config('openai-compatible', 'gw')), \
                patch('llmtool.task_submission_cli.api_key', return_value=None), \
                patch('llmtool.task_submission_cli.create_client', return_value=client), \
                redirect_stdout(output):
            code = run_task_delegate(self._delegate_args(
                task, read_path=[target], write_path=[target]))
        self.assertEqual(code, 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result['developer']['state'], 'DEVELOPER_OUTPUT_REJECTED')
        self.assertEqual(result['developer']['error_code'], 'DEVELOPER_OUTPUT_INVALID_DIFF')
        self.assertIsNone(result['developer']['artifact'])
        self.assertNotIn('new mode 100755', output.getvalue())
        # The rejected proposal was never applied to the tree.
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o644)
        saved = TaskStore(Path(self.home) / '.local/state/llmtool/tasks').get(task['task_id'])
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_OUTPUT_REJECTED')
        self.assertEqual(saved['developer']['error_code'], 'DEVELOPER_OUTPUT_INVALID_DIFF')
        self.assertIsNone(saved['developer']['artifact'])
        self.assertNotIn('new mode 100755', json.dumps(saved))

    def test_opencode_mode_change_artifact_is_rejected_and_never_recorded(self):
        # The real artifact on this route is the git diff of the live D6 root,
        # so a Developer that both edits a file and flips its executable bit
        # produces mode metadata plus a valid hunk: the accepted-before-fix shape.
        target = 'sample.py'
        task = self._pm_task_with_opencode_assignment()
        output = io.StringIO()

        def execute(**kwargs):
            (self.executor_root / target).write_text('VALUE = 2\n')
            (self.executor_root / target).chmod(0o755)
            return DirectResult(0, 'ses_Mode0001', 'Updated fixture and mode.',
                {'prompt_tokens': 18, 'completion_tokens': 9, 'total_tokens': 27},
                'CLI_REPORTED', 'a' * 64, 1234, 'CONFIRMED', 'opencode v2.0.32',
                10, 1, 'b' * 64)

        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.opencode_direct_developer.run',
                      side_effect=execute), \
                redirect_stdout(output):
            code = run_task_delegate(self._opencode_delegate_args(task))
        self.assertEqual(code, 1, output.getvalue())
        developer = json.loads(output.getvalue())['developer']
        self.assertEqual(developer['state'], 'DEVELOPER_OUTPUT_REJECTED')
        self.assertEqual(developer['error_code'], 'DEVELOPER_OUTPUT_INVALID_DIFF')
        self.assertIsNone(developer['artifact'])
        self.assertEqual(developer['usage']['total_tokens'], 27)
        # The route rejects the artifact but does not revert the worktree.
        self.assertEqual(stat.S_IMODE((self.executor_root / target).stat().st_mode), 0o755)
        self.assertNotIn('new mode 100755', output.getvalue())
        stored = TaskStore(Path(self.home) / '.local/state/llmtool/tasks').get(task['task_id'])
        self.assertEqual(stored['developer']['state'], 'DEVELOPER_OUTPUT_REJECTED')
        self.assertEqual(stored['developer']['error_code'], 'DEVELOPER_OUTPUT_INVALID_DIFF')
        self.assertIsNone(stored['developer']['artifact'])

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
        synchronized_reads = [0]
        synchronized_reads_lock = threading.Lock()

        class StaleReadStore:
            def get(self, task_id):
                result = backing.get(task_id)
                with synchronized_reads_lock:
                    synchronize = synchronized_reads[0] < 2
                    synchronized_reads[0] += 1
                if synchronize:
                    read_barrier.wait(timeout=5)
                return result

            def compare_and_update(self, task_id, **kwargs):
                return backing.compare_and_update(task_id, **kwargs)

            def update(self, task_id, **kwargs):
                return backing.update(task_id, **kwargs)

            def append_dispatch_audit(self, task_id, entry, *, expected=None):
                return backing.append_dispatch_audit(task_id, entry, expected=expected)

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

    def test_interrupted_after_durable_claim_is_not_completed_or_replayed(self):
        task = self._pm_task_with_assignment()
        backing = TaskStore(Path(self.home) / '.local/state/llmtool/tasks')

        class InterruptAfterClaimStore:
            def get(self, task_id):
                return backing.get(task_id)

            def compare_and_update(self, task_id, **kwargs):
                result = backing.compare_and_update(task_id, **kwargs)
                if kwargs.get('developer', {}).get('state') == 'DEVELOPER_PENDING':
                    # Model a process interruption at the exact durable-claim boundary,
                    # before config resolution, client creation, or provider execution.
                    raise SystemExit(77)
                return result

            def update(self, task_id, **kwargs):
                return backing.update(task_id, **kwargs)

            def append_dispatch_audit(self, task_id, entry, *, expected=None):
                return backing.append_dispatch_audit(task_id, entry, expected=expected)

        args = self._delegate_args(task)
        client = FakeClient(Reply('unused', {}))
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.task_submission_cli.resolve_profile_config',
                      return_value=config('openai-compatible', 'gw')), \
                patch('llmtool.task_submission_cli.api_key', return_value=None), \
                patch('llmtool.task_submission_cli.create_client', return_value=client) as factory:
            with self.assertRaises(SystemExit) as interrupted:
                run_task_delegate(args, store=InterruptAfterClaimStore())
            self.assertEqual(interrupted.exception.code, 77)
            pending = backing.get(task['task_id'])
            self.assertEqual(pending['developer']['state'], 'DEVELOPER_PENDING')
            self.assertEqual(pending['completion']['state'], 'WAITING_EXECUTOR')
            factory.assert_not_called()
            with self.assertRaisesRegex(LLMToolError, 'duplicate dispatch refused'):
                run_task_delegate(args, store=backing)
            factory.assert_not_called()
            # The ambiguous claim cannot be completed either: the completion
            # path refuses (it records only the missing gate), never calls the
            # PM, and the claim stays pending, so a later dispatch is still refused.
            complete = Namespace(task_id=task['task_id'], executor_task=args.executor_task,
                                 reviewer_task=None, pr=None)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(run_task_complete(complete, store=backing), 1)
            after = backing.get(task['task_id'])
            self.assertEqual(after['state'], 'PM_RESPONDED')
            self.assertEqual(after['developer']['state'], 'DEVELOPER_PENDING')
            self.assertEqual(after['completion']['state'], 'WAITING_EXECUTOR')
            self.assertEqual(after['completion']['pm_dispatch'], 'NOT_STARTED')
            self.assertIsNone(after['completion']['pm_response'])
            self.assertTrue(after['completion']['missing_gates'])
            with self.assertRaisesRegex(LLMToolError, 'duplicate dispatch refused'):
                run_task_delegate(args, store=backing)
            factory.assert_not_called()
        self.assertEqual(client.calls, [])

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

    def test_rejected_output_capture_is_private_bounded_redacted_and_not_printed(self):
        secret = 'sk-' + ('a' * 24)
        rejected = 'not a diff\napi_key=' + secret + '\n' + ('z' * (70 * 1024))
        output = io.StringIO()
        store = TaskStore(Path(self.home) / '.local/state/llmtool/tasks')
        task_id = 'a' * 32
        with redirect_stdout(output):
            capture = submission_cli._capture_rejected_output(store, task_id, rejected)
        self.assertNotIn(secret, output.getvalue())
        self.assertEqual(set(capture), {'ref', 'sha256'})
        path = store.root / capture['ref']
        parent_stat, file_stat = path.parent.stat(), path.stat()
        self.assertEqual(stat.S_IMODE(parent_stat.st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(file_stat.st_mode), 0o600)
        raw = path.read_bytes()
        self.assertLessEqual(len(raw), RESPONSE_LIMIT)
        self.assertNotIn(secret.encode(), raw)
        self.assertIn(b'[REDACTED]', raw)
        self.assertIn(b'[TRUNCATED]', raw)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), capture['sha256'])

    def test_capture_failure_still_persists_rejection_state(self):
        class Store:
            def update(self, task_id, developer):
                self.task_id = task_id
                self.developer = dict(developer)
                return {'developer': dict(developer)}

        store = Store()
        developer = {'state': 'DEVELOPER_DISPATCHING', 'usage': None}
        usage = {'prompt_tokens': 3, 'completion_tokens': 4, 'total_tokens': 7}
        with patch('llmtool.task_submission_cli._capture_rejected_output',
                   side_effect=OSError('disk unavailable')):
            saved = submission_cli._persist_developer_output_rejection(
                store, 'a' * 32, developer, 'DEVELOPER_OUTPUT_INVALID_DIFF', usage,
                'invalid proposal')
        self.assertEqual(store.task_id, 'a' * 32)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_OUTPUT_REJECTED')
        self.assertEqual(saved['developer']['error_code'], 'DEVELOPER_OUTPUT_INVALID_DIFF')
        self.assertEqual(saved['developer']['usage'], usage)
        self.assertNotIn('rejected_output_capture', saved['developer'])


class DeveloperOutputShapeMatrixTests(unittest.TestCase):
    """#290 / #315 P2-2: the local verdict for common model output shapes,
    pinned offline (no provider call). Any later relaxation of the strict
    validator must change this table deliberately."""

    def test_common_output_shapes(self):
        def rejection(text, paths):
            return submission_cli._developer_output_rejection(
                text, paths, normalize=True)
        target = 'tools/llmtool/example.py'
        header = f'diff --git a/{target} b/{target}\n--- a/{target}\n+++ b/{target}\n'
        good = header + '@@ -1,3 +1,3 @@\n keep\n-old\n+new\n keep\n'
        shapes = {
            'git diff': (good, None),
            'leading blank line': ('\n' + good, 'DEVELOPER_OUTPUT_INVALID_ENVELOPE'),
            'markdown fence': ('```diff\n' + good + '```\n', None),
            'no diff --git header': (good[len(header.split('\n')[0]) + 1:],
                                     None),
            'blank context without its space': (
                header + '@@ -1,4 +1,4 @@\n keep\n\n-old\n+new\n keep\n', None),
            'prose after the diff': (good + 'Done.\n', 'DEVELOPER_OUTPUT_INVALID_DIFF'),
            'closing fence after the diff': (good + '```\n', 'DEVELOPER_OUTPUT_INVALID_DIFF'),
            'deleted file': (f'diff --git a/{target} b/{target}\ndeleted file mode 100644\n'
                             f'--- a/{target}\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n',
                             None),
            'rename': (f'diff --git a/{target} b/tools/llmtool/renamed.py\nrename from {target}\n'
                       'rename to tools/llmtool/renamed.py\n', 'DEVELOPER_OUTPUT_INVALID_DIFF'),
            'path outside write_paths': (good.replace(target, 'tools/llmtool/other.py'),
                                         'DEVELOPER_OUTPUT_OUT_OF_SCOPE'),
            # A mode change is rejected even when it rides along with a real
            # hunk: text Developer routes never change executable bits (#290).
            'mode change with a hunk': (header.replace('--- a/', 'old mode 100644\nnew mode 100755\n--- a/', 1)
                                        + '@@ -1,3 +1,3 @@\n keep\n-old\n+new\n keep\n',
                                        'DEVELOPER_OUTPUT_INVALID_DIFF'),
            'hunk count off by one': (
                header + '@@ -1,4 +1,4 @@\n keep\n-old\n+new\n keep\n', None),
            'new file': (f'diff --git a/{target} b/{target}\nnew file mode 100644\n'
                         f'--- /dev/null\n+++ b/{target}\n@@ -0,0 +1 @@\n+new\n',
                         None),
            'new executable file': (f'diff --git a/{target} b/{target}\nnew file mode 100755\n'
                                    f'--- /dev/null\n+++ b/{target}\n@@ -0,0 +1 @@\n+new\n',
                                    'DEVELOPER_OUTPUT_NEW_FILE_UNSUPPORTED'),
            'new symlink': (f'diff --git a/{target} b/{target}\nnew file mode 120000\n'
                            f'--- /dev/null\n+++ b/{target}\n@@ -0,0 +1 @@\n+target\n',
                            'DEVELOPER_OUTPUT_NEW_FILE_UNSUPPORTED'),
            'deleted executable file': (
                f'diff --git a/{target} b/{target}\ndeleted file mode 100755\n'
                f'--- a/{target}\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n',
                'DEVELOPER_OUTPUT_DELETE_UNSUPPORTED'),
        }
        for label, (text, expected) in shapes.items():
            with self.subTest(label):
                self.assertEqual(rejection(text, [target]), expected)

    def test_file_operation_scope_and_source_existence(self):
        target = 'tools/llmtool/new.py'
        outside = 'tools/llmtool/outside.py'
        new_file = (f'diff --git a/{target} b/{target}\nnew file mode 100644\n'
                    f'--- /dev/null\n+++ b/{target}\n@@ -0,0 +1 @@\n+new\n')
        deletion = (f'diff --git a/{target} b/{target}\ndeleted file mode 100644\n'
                    f'--- a/{target}\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n')
        outside_new = new_file.replace(target, outside)
        self.assertEqual(submission_cli._developer_output_rejection(
            outside_new, [target]), 'DEVELOPER_OUTPUT_OUT_OF_SCOPE')
        self.assertEqual(submission_cli._developer_normalized_output(
            new_file, [target], source_files={target: ''})[2], None)
        self.assertEqual(submission_cli._developer_normalized_output(
            new_file, [target], source_files={target: 'already exists\n'})[2],
            'DEVELOPER_OUTPUT_NEW_FILE_UNSUPPORTED')
        self.assertEqual(submission_cli._developer_normalized_output(
            deletion, [target], source_files={target: 'old\n'})[2], None)

        existing_empty = submission_cli._DeveloperSourceFiles()
        existing_empty[target] = ''
        existing_empty.existing_paths.add(target)
        existing_empty.regular_paths.add(target)
        self.assertEqual(submission_cli._developer_normalized_output(
            new_file, [target], source_files=existing_empty)[2],
            'DEVELOPER_OUTPUT_NEW_FILE_UNSUPPORTED')

    def test_empty_file_git_metadata_shape_is_admitted_safely(self):
        target = 'tools/llmtool/new.py'
        outside = 'tools/llmtool/outside.py'
        empty_add = (f'diff --git a/{target} b/{target}\n'
                     'new file mode 100644\n'
                     'index 0000000..e69de29\n')
        empty_delete = (f'diff --git a/{target} b/{target}\n'
                        'deleted file mode 100644\n'
                        'index e69de29..0000000\n')
        empty_add_files = submission_cli._DeveloperSourceFiles()
        empty_add_files[target] = ''
        artifact, _normalizations, rejection = submission_cli._developer_normalized_output(
            empty_add, [target], source_files=empty_add_files)
        self.assertIsNone(rejection)
        self.assertEqual(artifact, empty_add)
        self.assertIsNone(submission_cli._developer_output_rejection(
            empty_add, [target]))
        self.assertEqual(submission_cli._developer_output_rejection(
            empty_add.replace(target, outside), [target]),
            'DEVELOPER_OUTPUT_OUT_OF_SCOPE')

        empty_delete_files = submission_cli._DeveloperSourceFiles()
        empty_delete_files[target] = ''
        empty_delete_files.existing_paths.add(target)
        empty_delete_files.regular_paths.add(target)
        artifact, _normalizations, rejection = submission_cli._developer_normalized_output(
            empty_delete, [target], source_files=empty_delete_files)
        self.assertIsNone(rejection)
        self.assertEqual(artifact, empty_delete)
        self.assertIsNone(submission_cli._developer_output_rejection(
            empty_delete, [target]))

        nonempty_delete_files = submission_cli._DeveloperSourceFiles()
        nonempty_delete_files[target] = 'content\n'
        nonempty_delete_files.existing_paths.add(target)
        nonempty_delete_files.regular_paths.add(target)
        self.assertEqual(submission_cli._developer_normalized_output(
            empty_delete, [target], source_files=nonempty_delete_files)[2],
            'DEVELOPER_OUTPUT_DELETE_UNSUPPORTED')

        wrong_add_mode = empty_add.replace('new file mode 100644',
                                           'new file mode 100755')
        wrong_delete_mode = empty_delete.replace('deleted file mode 100644',
                                                 'deleted file mode 100755')
        self.assertEqual(submission_cli._developer_output_rejection(
            wrong_add_mode, [target]), 'DEVELOPER_OUTPUT_NEW_FILE_UNSUPPORTED')
        self.assertEqual(submission_cli._developer_output_rejection(
            wrong_delete_mode, [target]), 'DEVELOPER_OUTPUT_DELETE_UNSUPPORTED')

    def test_empty_file_entry_before_another_diff_is_admitted(self):
        empty = 'tools/llmtool/__init__.py'
        modified = 'tools/llmtool/example.py'
        mixed = (
            f'diff --git a/{empty} b/{empty}\n'
            'new file mode 100644\n'
            'index 0000000..e69de29\n'
            f'diff --git a/{modified} b/{modified}\n'
            f'--- a/{modified}\n+++ b/{modified}\n'
            '@@ -1 +1 @@\n-old\n+new\n')
        source_files = submission_cli._DeveloperSourceFiles()
        source_files[empty] = ''
        source_files[modified] = 'old\n'
        source_files.existing_paths.add(modified)
        source_files.regular_paths.add(modified)

        artifact, _normalizations, rejection = submission_cli._developer_normalized_output(
            mixed, [empty, modified], source_files=source_files)

        self.assertIsNone(rejection)
        self.assertEqual(artifact, mixed)
        self.assertIsNone(submission_cli._developer_output_rejection(
            mixed, [empty, modified]))

    def test_proposal_can_mix_new_files_with_existing_file_modifications(self):
        target = 'tools/llmtool/existing.py'
        added = 'tools/llmtool/new.py'
        mixed = (
            f'diff --git a/{target} b/{target}\n'
            f'--- a/{target}\n+++ b/{target}\n'
            '@@ -1 +1 @@\n-old\n+updated\n'
            f'diff --git a/{added} b/{added}\nnew file mode 100644\n'
            f'--- /dev/null\n+++ b/{added}\n'
            '@@ -0,0 +1 @@\n+new file\n')
        source_files = submission_cli._DeveloperSourceFiles()
        source_files[target] = 'old\n'
        source_files.existing_paths.add(target)
        source_files.regular_paths.add(target)
        source_files[added] = ''
        artifact, _tokens, rejection = submission_cli._developer_normalized_output(
            mixed, [target, added], source_files=source_files)
        self.assertIsNone(rejection)
        self.assertEqual(artifact, mixed)

    def test_unsafe_shapes_remain_rejected(self):
        target = 'tools/llmtool/example.py'
        good = (f'diff --git a/{target} b/{target}\n--- a/{target}\n+++ b/{target}\n'
                '@@ -1 +1 @@\n-old\n+new\n')
        cases = {
            'two fences': ('```diff\n' + good + '```\n```\n' + good + '```',
                           'DEVELOPER_OUTPUT_INVALID_ENVELOPE'),
            'prose mixed with diff': ('intro\n' + good,
                                      'DEVELOPER_OUTPUT_INVALID_ENVELOPE'),
            'mode metadata': (good.replace('--- a/', 'old mode 100644\nnew mode 100755\n--- a/', 1),
                              'DEVELOPER_OUTPUT_INVALID_DIFF'),
            'out of scope': (good.replace(target, 'tools/llmtool/outside.py'),
                             'DEVELOPER_OUTPUT_OUT_OF_SCOPE'),
            'rename': (f'diff --git a/{target} b/tools/llmtool/renamed.py\n'
                       f'rename from {target}\nrename to tools/llmtool/renamed.py\n',
                       'DEVELOPER_OUTPUT_INVALID_DIFF'),
            'binary': (f'diff --git a/{target} b/{target}\nGIT binary patch\nliteral 2\n',
                       'DEVELOPER_OUTPUT_INVALID_DIFF'),
        }
        for label, (text, expected) in cases.items():
            with self.subTest(label=label):
                self.assertEqual(submission_cli._developer_output_rejection(
                                     text, [target], normalize=True),
                                 expected)

    def test_prompt_example_itself_passes_the_validator(self):
        task = {'task_id': 'a' * 32, 'run_id': 'b' * 32, 'pm_session_id': 'pm-x',
                'task_binding': {'contract_digest': 'c' * 64}}
        system = submission_cli._developer_prompt(
            task, source_sha='d' * 40, read_paths=['example.py'], write_paths=['example.py'],
            acceptance=['ok'], context='', pm_goal='g')[0]['content']
        user = submission_cli._developer_prompt(
            task, source_sha='d' * 40, read_paths=['example.py'], write_paths=['example.py'],
            acceptance=['ok'], context='provided source text', pm_goal='g')[1]['content']
        example = system.split('Exact format example:\n', 1)[1]
        example = example[:example.index('Every hunk header')]
        self.assertIsNone(submission_cli._developer_output_rejection(example, ['example.py']))
        packet, context = user.split(TEXT_DEVELOPER_CONTEXT_SEPARATOR, 1)
        self.assertNotIn('provided source text', packet)
        self.assertIn('no tools, cannot read files, and all needed file content is provided below.',
                      user)
        self.assertIn('Never abbreviate or write "..."', system)
        self.assertIn('Copy context lines verbatim', system)
        self.assertIn('New files use --- /dev/null and +++ b/path', system)
        self.assertIn('deletions use --- a/path and +++ /dev/null', system)
        self.assertNotIn('creating, deleting or renaming files is not supported', system)
        self.assertTrue(context.endswith('Return only the unified diff, starting with "diff --git".'))


class ModeMetadataRejectionTests(unittest.TestCase):
    """#290: permission/mode metadata is never accepted on a text Developer route.

    A text Developer patch may not change executable bits, so every documented
    mode-metadata shape is rejected by the strict parser before screening,
    persistence or application. Ordinary textual diffs keep their verdict."""

    TARGET = 'tools/llmtool/example.py'
    SECOND = 'tools/llmtool/second.py'

    def _section(self, path=None, *, metadata=(), hunk=True):
        path = self.TARGET if path is None else path
        parts = [f'diff --git a/{path} b/{path}\n', *metadata,
                 f'--- a/{path}\n+++ b/{path}\n']
        if hunk:
            parts.append('@@ -1,3 +1,3 @@\n keep\n-old\n+new\n keep\n')
        return ''.join(parts)

    def test_mode_metadata_shapes_are_rejected(self):
        clean = self._section()
        cases = {
            'executable bit added with a hunk': self._section(
                metadata=('index 1111111..2222222 100644\n', 'old mode 100644\n',
                          'new mode 100755\n')),
            'executable bit removed with a hunk': self._section(
                metadata=('old mode 100755\n', 'new mode 100644\n')),
            'mode change before the index line': self._section(
                metadata=('old mode 100644\n', 'new mode 100755\n',
                          'index 1111111..2222222\n')),
            'mode change without a hunk': self._section(
                metadata=('old mode 100644\n', 'new mode 100755\n'), hunk=False),
            'solo old mode': self._section(metadata=('old mode 100644\n',)),
            'solo new mode': self._section(metadata=('new mode 100755\n',)),
            'mode change in a later section': clean + self._section(
                path=self.SECOND, metadata=('old mode 100644\n', 'new mode 100755\n')),
            'mode change in an earlier section': self._section(
                metadata=('old mode 100644\n', 'new mode 100755\n')) + self._section(
                path=self.SECOND),
            'mode metadata with CRLF line endings': self._section(
                metadata=('old mode 100644\n', 'new mode 100755\n')).replace(
                    '\n', '\r\n'),
            'mode metadata after the file headers': (
                f'diff --git a/{self.TARGET} b/{self.TARGET}\n'
                f'--- a/{self.TARGET}\n+++ b/{self.TARGET}\n'
                'old mode 100644\nnew mode 100755\n'
                '@@ -1,3 +1,3 @@\n keep\n-old\n+new\n keep\n'),
        }
        for label, text in cases.items():
            with self.subTest(label):
                self.assertEqual(
                    submission_cli._developer_output_rejection(
                        text, [self.TARGET, self.SECOND]),
                    'DEVELOPER_OUTPUT_INVALID_DIFF')

    def test_rejection_is_caused_by_the_mode_metadata_alone(self):
        rejection = submission_cli._developer_output_rejection
        indexed = self._section(metadata=('index 1111111..2222222 100644\n',))
        mode_change = self._section(
            metadata=('index 1111111..2222222 100644\n', 'old mode 100644\n',
                      'new mode 100755\n'))
        self.assertEqual(mode_change.replace('old mode 100644\nnew mode 100755\n', ''),
                         indexed)
        self.assertIsNone(rejection(indexed, [self.TARGET]))
        self.assertEqual(rejection(mode_change, [self.TARGET]),
                         'DEVELOPER_OUTPUT_INVALID_DIFF')

    def test_ordinary_text_diffs_and_scope_checks_are_unchanged(self):
        rejection = submission_cli._developer_output_rejection
        plain = self._section()
        indexed = self._section(metadata=('index 1111111..2222222 100644\n',))
        two_files = self._section() + self._section(path=self.SECOND)
        self.assertIsNone(rejection(plain, [self.TARGET]))
        self.assertIsNone(rejection(indexed, [self.TARGET]))
        self.assertIsNone(rejection(two_files, [self.TARGET, self.SECOND]))
        self.assertEqual(rejection(two_files, [self.TARGET]),
                         'DEVELOPER_OUTPUT_OUT_OF_SCOPE')
        # Parser rejection precedes the scope verdict, so a mode change fails
        # closed even when its path is outside the allowlist.
        self.assertEqual(
            rejection(self._section(metadata=('old mode 100644\n',
                                              'new mode 100755\n')),
                      ['tools/llmtool/other.py']),
            'DEVELOPER_OUTPUT_INVALID_DIFF')


class CodexChildLivenessTests(unittest.TestCase):
    def _delegate(self, developer, *, kill_result=None, start='123456', pgid=321):
        task = {'task_id': 'a' * 32, 'state': 'PM_RESPONDED', 'response': 'response',
                'schema_version': 2, 'developer': developer}

        class Store:
            def __init__(self):
                self.task = task
                self.updates = 0

            def get(self, _task_id):
                return self.task

            def update(self, _task_id, **changes):
                self.updates += 1
                self.task.update(changes)
                return self.task

            def compare_and_update(self, _task_id, *, expected, **changes):
                if any(self.task.get(key) != value for key, value in expected.items()):
                    raise TaskStoreConflict()
                self.updates += 1
                self.task.update(changes)
                return self.task

        store = Store()
        # The delegate resolves the durable Task before checking child
        # liveness, so provide the same task binding fields as other delegate
        # tests even though this path returns before reading the rest.
        args = Namespace(task_id='a' * 32, executor_task='executor-one',
            selection_source='USER', context_stdin=False,
            acceptance=['the outcome is recorded'], read_path=['src.py'],
            write_path=['src.py'], egress_basis='Approved non-sensitive test context.')
        snapshot = type('Snapshot', (), {'common_dir': '/repo/.git', 'root': '/repo'})()
        registration = type('Registration', (), {
            'task_id': 'executor-one', 'trusted_agent_id': 'codex-developer'})()
        with patch.object(submission_cli.task_completion, 'collect_git_snapshot',
                          return_value=snapshot), \
             patch.object(submission_cli.task_completion, 'bind_executor_task',
                          return_value=(registration, snapshot)), \
             patch.object(submission_cli.os, 'kill', side_effect=kill_result), \
             patch.object(submission_cli.codex_direct_developer, 'process_start_time',
                          return_value=start), \
             patch.object(submission_cli.os, 'getpgid', return_value=pgid):
            with self.assertRaises(LLMToolError) as caught:
                submission_cli.run_task_codex_delegate(args, store=store)
        return caught.exception, store

    def _real_store(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = TaskStore(Path(tmp.name).resolve() / 'tasks')
        response = json.dumps({
            'schema_version': 1, 'summary': 'A bounded liveness test task.',
            'developer_assignment': {
                'route': 'codex', 'risk': 'LOW',
                'goal': 'Record the liveness outcome.',
                'acceptance': ['the outcome is recorded'],
            },
        })
        task = run_task_record_pm(
            Namespace(stdin_json=True), store=store,
            input_stream=io.StringIO(json.dumps({
                'request': 'Bounded non-sensitive liveness test.',
                'response': response,
            })), return_record=True)
        task_id = task['task_id']
        developer = {
            'assignment_id': new_id(), 'selection_source': 'PM',
            'executor_task_id': 'executor-one', 'state': 'DEVELOPER_PENDING',
            'provider_profile': 'codex', 'provider_type': 'codex-cli',
            'model': 'gpt-6-luna', 'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
            'session_id': 'dev-' + new_id(), 'source_sha': 'a' * 40,
            'source_sha_binding': 'D6_REGISTRY_OBSERVED', 'read_paths': ['src.py'],
            'write_paths': ['src.py'], 'acceptance': ['the outcome is recorded'],
            'context_sha256': 'b' * 64, 'egress_basis': 'Approved non-sensitive test context.',
            'input_bytes': 10, 'max_tokens': None, 'artifact_format': 'unified_diff',
            'artifact': None, 'artifact_sha256': None, 'usage': None,
            'error_code': None, 'execution': None, 'created_at': '2026-10-23T00:00:00Z',
        }
        with patch.object(submission_cli.worker_dispatch,
                          'authorize_task_store_change', return_value=None):
            store.compare_and_update(task_id,
                expected={'state': 'PM_RESPONDED', 'developer': None,
                          'completion': None},
                developer=developer,
                completion={
                    'state': 'WAITING_EXECUTOR', 'executor_task_id': 'executor-one',
                    'reviewer_task_id': None, 'evidence': {
                        'task_id': task_id, 'run_id': task['run_id'],
                        'contract_digest': task['task_binding']['contract_digest'],
                        'executor_identity': 'codex-developer',
                        'executor_session_ref': None, 'executor_native_session_id': None,
                        'executor_native_session': 'UNKNOWN', 'execution_mode': 'DIRECT_D6',
                        'root': '/repo', 'common_dir': '/repo/.git', 'branch': 'task-branch',
                        'source_sha': 'a' * 40, 'result_head': None,
                    },
                    'missing_gates': [], 'pm_dispatch': 'NOT_STARTED',
                    'pm_response': None, 'pm_recommendation': None,
                    'pm_usage': None, 'pm_error_code': None,
                })
            # This is the same durable launch transition used by the #776
            # liveness tests; it binds the child identity to the assignment.
            store.record_codex_launch(task_id,
                assignment_id=developer['assignment_id'], pid=321,
                start_time='123456', pgid=321)
        return store, task_id

    @staticmethod
    def _running_developer(execution=None):
        developer = {'state': 'DEVELOPER_DISPATCHING', 'provider_profile': 'codex',
                     'executor_task_id': 'executor-one', 'assignment_id': 'b' * 32}
        if execution is not None:
            developer['execution'] = execution
        return developer

    def test_dead_child_is_marked_unknown_once_and_not_replayed(self):
        execution = {'child_pid': 321, 'child_start_time': '123456', 'child_pgid': 321}
        error, store = self._delegate(self._running_developer(execution),
                                      kill_result=ProcessLookupError())
        self.assertEqual(str(error), 'CODEX_CHILD_OUTCOME_UNKNOWN')
        self.assertEqual(store.updates, 1)
        self.assertEqual(store.task['developer']['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(store.task['developer']['error_code'], 'CODEX_CHILD_NOT_RUNNING')

    def test_dead_child_transition_is_validated_by_real_task_store(self):
        store, task_id = self._real_store()
        args = Namespace(task_id=task_id, executor_task='executor-one',
            selection_source='USER', context_stdin=False,
            acceptance=['the outcome is recorded'], read_path=['src.py'],
            write_path=['src.py'], egress_basis='Approved non-sensitive test context.')
        with patch.object(submission_cli.task_completion, 'collect_git_snapshot',
                          return_value=type('Snapshot', (), {
                              'common_dir': '/repo/.git', 'root': '/repo'})()), \
             patch.object(submission_cli.task_completion, 'bind_executor_task',
                          return_value=(type('Registration', (), {
                              'task_id': 'executor-one',
                              'trusted_agent_id': 'codex-developer'})(), None)), \
             patch.object(submission_cli.os, 'kill', side_effect=ProcessLookupError()):
            with self.assertRaisesRegex(LLMToolError, 'CODEX_CHILD_OUTCOME_UNKNOWN'):
                submission_cli.run_task_codex_delegate(args, store=store)
        saved = store.get(task_id)['developer']
        self.assertEqual(saved['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(saved['error_code'], 'CODEX_CHILD_NOT_RUNNING')
        with patch.object(submission_cli.task_completion, 'collect_git_snapshot',
                          return_value=type('Snapshot', (), {
                              'common_dir': '/repo/.git', 'root': '/repo',
                              'branch': 'task-branch', 'head': 'a' * 40})()), \
             patch.object(submission_cli.task_completion, 'bind_executor_task',
                          return_value=(type('Registration', (), {
                              'task_id': 'executor-one',
                              'trusted_agent_id': 'codex-developer',
                              'source_sha': 'a' * 40, 'result_sha': 'a' * 40})(),
                              type('Snapshot', (), {
                                  'common_dir': '/repo/.git', 'root': '/repo',
                                  'branch': 'task-branch', 'head': 'a' * 40})())), \
             patch.object(submission_cli, '_assignment_paths',
                          return_value=(['src.py'], ['src.py'])), \
             patch.object(submission_cli, '_codex_developer_profile',
                          return_value=({'model': 'gpt-6-luna'}, 'high')), \
             patch.object(submission_cli, 'codex_direct_timeout', return_value=10), \
             patch.object(submission_cli, '_codex_timeout_recovery', return_value=False), \
             patch.object(submission_cli.codex_direct_developer, 'run') as run:
            with self.assertRaisesRegex(LLMToolError, 'developer assignment already exists'):
                submission_cli.run_task_codex_delegate(args, store=store)
        run.assert_not_called()
        self.assertEqual(store.get(task_id)['developer'], saved)

    def test_dead_child_compare_conflict_preserves_newer_responded_record(self):
        store, task_id = self._real_store()
        args = Namespace(task_id=task_id, executor_task='executor-one',
            selection_source='USER', context_stdin=False,
            acceptance=['the outcome is recorded'], read_path=['src.py'],
            write_path=['src.py'], egress_basis='Approved non-sensitive test context.')
        original_compare_and_update = store.compare_and_update
        newer = dict(store.get(task_id)['developer'])
        artifact = ('diff --git a/src.py b/src.py\n--- a/src.py\n+++ b/src.py\n'
                    '@@ -1 +1 @@\n-x=1\n+x=2\n')
        newer.update(state='DEVELOPER_RESPONDED', artifact=artifact,
            artifact_sha256=hashlib.sha256(artifact.encode()).hexdigest(),
            execution={'status': 'DONE',
                'native_session_id': '12345678-1234-4234-8234-123456789abc',
                'process_exit_code': 0, 'stdout_sha256': 'c' * 64,
                'elapsed_ms': 12, 'cleanup_status': 'CONFIRMED',
                'usage_source': 'UNKNOWN', 'source_bytes': None,
                'source_file_count': None, 'source_manifest_sha256': None,
                'native_session_binding': 'BOUND'})
        raced = False

        def advance_then_compare(_task_id, *, expected, **changes):
            nonlocal raced
            if not raced:
                raced = True
                current = store.get(task_id)['developer']
                with patch.object(submission_cli.worker_dispatch,
                                  'authorize_task_store_change', return_value=None):
                    original_compare_and_update(task_id,
                        expected={'developer': current}, developer=newer)
            return original_compare_and_update(_task_id, expected=expected, **changes)

        with patch.object(submission_cli.task_completion, 'collect_git_snapshot',
                          return_value=type('Snapshot', (), {
                              'common_dir': '/repo/.git', 'root': '/repo'})()), \
             patch.object(submission_cli.task_completion, 'bind_executor_task',
                          return_value=(type('Registration', (), {
                              'task_id': 'executor-one',
                              'trusted_agent_id': 'codex-developer'})(), None)), \
             patch.object(submission_cli.os, 'kill', side_effect=ProcessLookupError()), \
             patch.object(store, 'compare_and_update', side_effect=advance_then_compare):
            with self.assertRaisesRegex(LLMToolError, 'CODEX_LIVENESS_UNKNOWN'):
                submission_cli.run_task_codex_delegate(args, store=store)
        self.assertEqual(store.get(task_id)['developer'], newer)

    def test_matching_live_child_refuses_second_writer(self):
        execution = {'child_pid': 321, 'child_start_time': '123456', 'child_pgid': 321}
        error, store = self._delegate(self._running_developer(execution), kill_result=None)
        self.assertEqual(str(error), 'CODEX_CHILD_STILL_RUNNING')
        self.assertEqual(store.updates, 0)

    def test_reused_pid_is_dead_and_legacy_execution_remains_unknown(self):
        execution = {'child_pid': 321, 'child_start_time': 'old-start', 'child_pgid': 321}
        error, store = self._delegate(self._running_developer(execution),
                                      kill_result=None, start='new-start')
        self.assertEqual(str(error), 'CODEX_CHILD_OUTCOME_UNKNOWN')
        self.assertEqual(store.task['developer']['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        error, store = self._delegate(self._running_developer(), kill_result=None)
        self.assertEqual(str(error), 'CODEX_LIVENESS_UNKNOWN')
        self.assertEqual(store.updates, 0)
        self.assertEqual(store.task['developer']['state'], 'DEVELOPER_DISPATCHING')


class DirectRouteChildLivenessTests(unittest.TestCase):
    routes = (('opencode', 'opencode-developer'), ('agy', 'agy-developer'))

    def _store(self, profile):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = TaskStore(Path(tmp.name).resolve() / 'tasks')
        response = json.dumps({
            'schema_version': 1, 'summary': 'A bounded liveness test task.',
            'developer_assignment': {
                'route': profile, 'risk': 'LOW',
                'goal': 'Record the liveness outcome.',
                'acceptance': ['the outcome is recorded'],
            },
        })
        task = run_task_record_pm(
            Namespace(stdin_json=True), store=store,
            input_stream=io.StringIO(json.dumps({
                'request': 'Bounded non-sensitive liveness test.',
                'response': response,
            })), return_record=True)
        task_id = task['task_id']
        task['developer'] = {
            'assignment_id': new_id(), 'selection_source': 'PM',
            'executor_task_id': 'executor-one', 'state': 'DEVELOPER_PENDING',
            'provider_profile': profile,
            'provider_type': 'opencode-cli' if profile == 'opencode' else 'agy-cli',
            'model': 'test-model', 'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
            'session_id': 'dev-' + new_id(), 'source_sha': 'a' * 40,
            'source_sha_binding': 'D6_REGISTRY_OBSERVED', 'read_paths': ['src.py'],
            'write_paths': ['src.py'], 'acceptance': ['the outcome is recorded'],
            'context_sha256': 'b' * 64, 'egress_basis': 'Approved non-sensitive test context.',
            'input_bytes': 10, 'max_tokens': None, 'artifact_format': 'unified_diff',
            'artifact': None, 'artifact_sha256': None, 'usage': None,
            'error_code': None, 'execution': None,
            'created_at': '2026-10-23T00:00:00Z',
        }
        developer = task['developer']
        with patch.object(submission_cli.worker_dispatch,
                          'authorize_task_store_change', return_value=None):
            store.compare_and_update(task_id,
                expected={'state': 'PM_RESPONDED', 'developer': None,
                          'completion': None},
                developer=developer,
                completion={
                    'state': 'WAITING_EXECUTOR', 'executor_task_id': 'executor-one',
                    'reviewer_task_id': None, 'evidence': {
                        'task_id': task_id, 'run_id': task['run_id'],
                        'contract_digest': task['task_binding']['contract_digest'],
                        'executor_identity': ('opencode-developer' if profile == 'opencode'
                                              else 'agy-developer'),
                        'executor_session_ref': None, 'executor_native_session_id': None,
                        'executor_native_session': 'UNKNOWN', 'execution_mode': 'DIRECT_D6',
                        'root': '/repo', 'common_dir': '/repo/.git', 'branch': 'task-branch',
                        'source_sha': 'a' * 40, 'result_head': None,
                    },
                    'missing_gates': [], 'pm_dispatch': 'NOT_STARTED',
                    'pm_response': None, 'pm_recommendation': None,
                    'pm_usage': None, 'pm_error_code': None,
                })
            developer.update(state='DEVELOPER_DISPATCHING',
                             provider_type='agy-cli' if profile == 'agy' else 'opencode-cli',
                             model='test-model')
            store.update(task_id, developer=developer)
            submission_cli._record_direct_child_launch(
                store, task_id, developer, 321, '123456', 321)
        return store, task_id

    def _recover(self, store, task_id, *, agent, start='123456'):
        args = Namespace(task_id=task_id, executor_task='executor-one')
        snapshot = type('Snapshot', (), {'common_dir': '/repo/.git', 'root': '/repo'})()
        registration = type('Registration', (), {
            'task_id': 'executor-one', 'trusted_agent_id': agent})()
        with patch.object(submission_cli.task_completion, 'collect_git_snapshot',
                          return_value=snapshot), \
             patch.object(submission_cli.task_completion, 'bind_executor_task',
                          return_value=(registration, snapshot)), \
             patch.object(submission_cli.os, 'kill', side_effect=ProcessLookupError()), \
             patch.object(submission_cli.codex_direct_developer, 'process_start_time',
                          return_value=start), \
             patch.object(submission_cli.os, 'getpgid', return_value=321):
            with self.assertRaises(LLMToolError) as caught:
                submission_cli._recover_direct_child_dispatch(
                    args, store.get(task_id), store.get(task_id)['developer'], store,
                    expected_agent=agent)
        return caught.exception

    def test_dead_and_reused_children_transition_once_for_both_routes(self):
        for profile, agent in self.routes:
            with self.subTest(profile=profile):
                store, task_id = self._store(profile)
                error = self._recover(store, task_id, agent=agent)
                self.assertEqual(str(error), 'DIRECT_CHILD_OUTCOME_UNKNOWN')
                saved = store.get(task_id)['developer']
                self.assertEqual(saved['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
                self.assertEqual(saved['execution']['child_pid'], 321)
                self.assertEqual(saved['error_code'], 'DIRECT_CHILD_NOT_RUNNING')
                self.assertEqual(store.get(task_id)['developer'], saved)

                reused, reused_id = self._store(profile)
                with patch.object(submission_cli.os, 'kill', return_value=None), \
                     patch.object(submission_cli.codex_direct_developer, 'process_start_time',
                                  return_value='987654'), \
                     patch.object(submission_cli.os, 'getpgid', return_value=321), \
                     patch.object(submission_cli.task_completion, 'collect_git_snapshot',
                                  return_value=type('Snapshot', (), {
                                      'common_dir': '/repo/.git', 'root': '/repo'})()), \
                     patch.object(submission_cli.task_completion, 'bind_executor_task',
                                  return_value=(type('Registration', (), {
                                      'task_id': 'executor-one', 'trusted_agent_id': agent})(), None)):
                    with self.assertRaisesRegex(LLMToolError,
                                                'DIRECT_CHILD_OUTCOME_UNKNOWN'):
                        submission_cli._recover_direct_child_dispatch(
                            Namespace(task_id=reused_id, executor_task='executor-one'),
                            reused.get(reused_id), reused.get(reused_id)['developer'], reused,
                            expected_agent=agent)

    def test_alive_child_refuses_a_second_writer_for_both_routes(self):
        for profile, agent in self.routes:
            with self.subTest(profile=profile):
                store, task_id = self._store(profile)
                args = Namespace(task_id=task_id, executor_task='executor-one')
                snapshot = type('Snapshot', (), {'common_dir': '/repo/.git', 'root': '/repo'})()
                registration = type('Registration', (), {
                    'task_id': 'executor-one', 'trusted_agent_id': agent})()
                with patch.object(submission_cli.task_completion, 'collect_git_snapshot',
                                  return_value=snapshot), \
                     patch.object(submission_cli.task_completion, 'bind_executor_task',
                                  return_value=(registration, snapshot)), \
                     patch.object(submission_cli.os, 'kill', return_value=None), \
                     patch.object(submission_cli.codex_direct_developer, 'process_start_time',
                                  return_value='123456'), \
                     patch.object(submission_cli.os, 'getpgid', return_value=321):
                    with self.assertRaisesRegex(LLMToolError,
                                                'DIRECT_CHILD_STILL_RUNNING'):
                        submission_cli._recover_direct_child_dispatch(
                            args, store.get(task_id), store.get(task_id)['developer'], store,
                            expected_agent=agent)
                self.assertEqual(store.get(task_id)['developer']['state'],
                                 'DEVELOPER_DISPATCHING')

    def test_conflict_does_not_overwrite_newer_record_for_both_routes(self):
        for profile, agent in self.routes:
            with self.subTest(profile=profile):
                store, task_id = self._store(profile)
                original = store.compare_and_update
                newer = dict(store.get(task_id)['developer'], error_code='NEWER')
                raced = False
                def compare(_task_id, *, expected, **changes):
                    nonlocal raced
                    if not raced:
                        raced = True
                        original(task_id, expected={'developer': store.get(task_id)['developer']},
                                 developer=newer)
                    return original(_task_id, expected=expected, **changes)
                snapshot = type('Snapshot', (), {'common_dir': '/repo/.git', 'root': '/repo'})()
                registration = type('Registration', (), {
                    'task_id': 'executor-one', 'trusted_agent_id': agent})()
                with patch.object(submission_cli.task_completion, 'collect_git_snapshot',
                                  return_value=snapshot), \
                     patch.object(submission_cli.task_completion, 'bind_executor_task',
                                  return_value=(registration, snapshot)), \
                     patch.object(submission_cli.os, 'kill', side_effect=ProcessLookupError()), \
                     patch.object(store, 'compare_and_update', side_effect=compare):
                    with self.assertRaisesRegex(LLMToolError,
                                                'DIRECT_CHILD_LIVENESS_UNKNOWN'):
                        submission_cli._recover_direct_child_dispatch(
                            Namespace(task_id=task_id, executor_task='executor-one'),
                            store.get(task_id), store.get(task_id)['developer'], store,
                            expected_agent=agent)
                self.assertEqual(store.get(task_id)['developer'], newer)


class DispatcherGitTimeoutTests(unittest.TestCase):
    def test_dispatcher_git_preflight_timeout_is_a_preflight_failure(self):
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired('git', kwargs.get('timeout'))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(os.path.realpath(tmp))
            trusted = root / 'tools' / 'llmtool' / 'llmtool' / 'task_submission_cli.py'
            with patch.object(submission_cli, 'D6_INTEGRATOR_DISPATCHER_ROOT', root), \
                    patch.object(submission_cli, '__file__', str(trusted)), \
                    patch.object(submission_cli.subprocess, 'run', side_effect=timeout) as run:
                with self.assertRaisesRegex(ValueError, 'dispatcher Git preflight failed'):
                    submission_cli._trusted_d6_helper_source()
        self.assertEqual(run.call_args.kwargs['timeout'], 120)


if __name__ == '__main__':
    unittest.main()
