"""Regression tests for #2446 and #2449 automatic direct-route reassignment."""

from argparse import Namespace
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import task_submission_cli as submission_cli
from llmtool.errors import LLMToolError
from llmtool.opencode_direct_developer import DirectResult
from llmtool.task_store import TaskStore, new_id
from llmtool.task_submission_cli import (
    _record,
    run_task_delegate,
    run_task_delegate_checked,
)

import _chain_gate_fixture
import review_trust_fixture
import worker_policy_fixture


def setUpModule():
    review_trust_fixture.start_module()
    worker_policy_fixture.start_module()


def tearDownModule():
    worker_policy_fixture.stop_module()
    review_trust_fixture.stop_module()


class AutoReassign2446And2449Tests(unittest.TestCase):
    def setUp(self):
        cap_patch = patch.object(
            submission_cli, '_check_delegate_repair_cap',
            lambda args, store, d6_binding=None: None)
        cap_patch.start()
        self.addCleanup(cap_patch.stop)
        _chain_gate_fixture.pass_through_chain_gate(self)

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve() / 'home'
        self.home.mkdir(mode=0o700)
        self.policy_path = worker_policy_fixture.install(self)

        self.executor_root = Path(self.temp.name).resolve() / 'executor'
        self.executor_root.mkdir(mode=0o700)
        subprocess.run(['/usr/bin/git', 'init', '-b', 'feat/test-auto-reassign'],
                       cwd=self.executor_root, check=True, capture_output=True)
        subprocess.run(['/usr/bin/git', 'config', 'user.name', 'Executor Fixture'],
                       cwd=self.executor_root, check=True)
        subprocess.run(['/usr/bin/git', 'config', 'user.email', 'executor@example.invalid'],
                       cwd=self.executor_root, check=True)
        (self.executor_root / 'sample.py').write_text('VALUE = 1\n', encoding='utf-8')
        report_dir = self.executor_root / 'tools' / 'llmtool'
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / '.gitignore').write_text('/.d6-bootstrap-report.json\n', encoding='utf-8')
        subprocess.run(['/usr/bin/git', 'add', 'sample.py', 'tools/llmtool/.gitignore'],
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
            'trusted_agent_id': 'agy-developer', 'session_ref': 'Executor Session 1',
            'root_realpath': str(self.executor_root),
            'physical_identity': {'device': root_stat.st_dev, 'inode': root_stat.st_ino,
                                  'uid': root_stat.st_uid},
            'git_common_dir': self.executor_common_dir, 'branch': 'feat/test-auto-reassign',
            'base_sha': self.executor_head, 'head_sha': self.executor_head,
            'state': 'ACTIVE', 'lock_state': 'LOCKED',
        }
        report = {
            'schema_version': 1, 'result': 'PASS', 'status': 'env_reused',
            'task_id': 'executor-task-1', 'root_realpath': str(self.executor_root),
            'head_sha': self.executor_head, 'tooling_sha': self.executor_head,
            'cache_fingerprint': 'cpython3.13-darwin-arm64-012345abcdef',
        }
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
            'report_sha256': hashlib.sha256(report_bytes).hexdigest(),
        }
        self._registry_path().write_text(json.dumps(self.executor_registry), encoding='utf-8')
        self._registry_path().chmod(0o600)

        original_cwd = os.getcwd()
        self.addCleanup(os.chdir, original_cwd)
        os.chdir(self.executor_root)

        prefix = '\nREQUIRED_SKILL_BEGIN path=card\nFixture rules.\nREQUIRED_SKILL_END path=card'
        for patcher in (
            patch.object(submission_cli, '_bootstrap_registered_d6'),
            patch.object(submission_cli, '_bind_direct_native_session', return_value=False),
            patch.object(submission_cli, '_developer_card_prefix', return_value=(
                prefix, hashlib.sha256(prefix.encode('utf-8')).hexdigest())),
            patch.object(submission_cli.skill_ledger, 'record_card_delivery',
                         return_value='receipt'),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        self.store = TaskStore(self.home / '.local/state/llmtool/tasks')

    def _registry_path(self):
        return Path(self.executor_common_dir) / 'llmtool-worktrees' / 'executor-task-1.json'

    def _write_opencode_model(self, model):
        config = self.home / '.config/llmtool/config.yaml'
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            'providers:\n  opencode-developer:\n    model: ' + json.dumps(model) + '\n',
            encoding='utf-8')

    def _create_pm_task(self, route):
        task_id, run_id = new_id(), new_id()
        request = 'Update sample fixture value.'
        request_sha = hashlib.sha256(request.encode()).hexdigest()
        assignment = {
            'schema_version': 1, 'route': route, 'risk': 'LOW',
            'goal': 'Update sample.py value.', 'acceptance': ['focused change is present'],
        }
        task = _record(
            task_id, run_id, 'pm-' + new_id(), 'pm-profile', request,
            state='PM_RESPONDED', provider_type='openai-compatible', model='pm-model',
            backend='NO_NATIVE_SESSION_ID_EXPOSED', request_sha=request_sha)
        task.update(
            pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model', 'base_url': None},
            response=json.dumps({'schema_version': 1, 'summary': 'Bounded plan.',
                                 'developer_assignment': assignment}),
            pm_assignment=assignment,
        )
        self.store.create(task)
        return self.store.get(task_id)

    def _delegate_args(self, task, provider, **changes):
        values = dict(
            task_id=task['task_id'],
            provider=provider,
            mode='direct' if provider == 'agy' else 'proposal',
            selection_source='PM',
            executor_task='executor-task-1',
            read_path=['sample.py'],
            write_path=['sample.py'],
            acceptance=['focused change is present'],
            egress_basis='Owner approved non-sensitive source files',
            cwd=str(self.executor_root),
        )
        values.update(changes)
        return Namespace(**values)

    def _prepare_interrupted_assignment(self, task, original):
        agents = submission_cli.direct_reassignment.AGENTS
        record = dict(self.executor_registry, trusted_agent_id=agents[original])
        self._registry_path().write_text(json.dumps(record), encoding='utf-8')

        args = self._delegate_args(task, original)
        old = {
            'assignment_id': new_id(),
            'selection_source': 'PM',
            'state': 'DEVELOPER_PENDING',
            'provider_profile': original,
            'provider_type': None,
            'model': None,
            'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
            'session_id': 'dev-' + new_id(),
            'source_sha': self.executor_head,
            'source_sha_binding': 'D6_REGISTRY_OBSERVED',
            'read_paths': args.read_path,
            'write_paths': args.write_path,
            'acceptance': args.acceptance,
            'context_sha256': 'b' * 64,
            'egress_basis': args.egress_basis,
            'input_bytes': 128,
            'max_tokens': None,
            'artifact_format': 'unified_diff',
            'artifact': None,
            'artifact_sha256': None,
            'usage': None,
            'error_code': None,
            'execution': None,
            'created_at': '2026-11-23T00:00:00Z',
        }
        if original == 'agy':
            old['owner_assignment_ref'] = None
        completion = {
            'state': 'WAITING_EXECUTOR',
            'executor_task_id': args.executor_task,
            'reviewer_task_id': None,
            'evidence': {
                'task_id': task['task_id'],
                'run_id': task['run_id'],
                'contract_digest': task['task_binding']['contract_digest'],
                'executor_identity': agents[original],
                'executor_session_ref': record['session_ref'],
                'executor_native_session_id': None,
                'executor_native_session': 'UNKNOWN',
                'execution_mode': 'DIRECT_D6',
                'root': str(self.executor_root),
                'common_dir': self.executor_common_dir,
                'branch': record['branch'],
                'source_sha': self.executor_head,
                'result_head': None,
            },
            'missing_gates': [],
            'pm_dispatch': 'NOT_STARTED',
            'pm_response': None,
            'pm_recommendation': None,
            'pm_usage': None,
            'pm_error_code': None,
        }
        self.store.d6_bind_compare_and_update(
            task['task_id'], executor_task_id=args.executor_task,
            expected={'developer': None}, developer=old, completion=completion)
        self.store.prepare_direct_dispatch(
            task['task_id'], expected_developer=old,
            developer=dict(
                old,
                state='DEVELOPER_DISPATCHING',
                provider_type=original + '-cli',
                model='gemini-test' if original == 'agy' else 'opencode/test-free',
                max_tokens=None if original == 'agy' else 4096,
            ),
        )
        return old, args

    def _launch_opencode_stub(self, expected_model=None):
        def launch(**kwargs):
            if expected_model is not None:
                self.assertEqual(kwargs['model'], expected_model)
            kwargs['spawn_authorization']()
            kwargs['launch_callback'](321, '123456', 321)
            (self.executor_root / 'sample.py').write_text('VALUE = 2\n', encoding='utf-8')
            return DirectResult(
                0, 'ses_Abc123', 'Updated fixture.', None, 'UNKNOWN',
                'a' * 64, 12, 'CONFIRMED', 'opencode v2.1.4', 10, 1, 'b' * 64)
        return launch

    def _launch_agy_stub(self):
        def launch(_route, request, *, gate, on_launch, **_kwargs):
            self.assertTrue(gate().allowed)
            on_launch()
            (self.executor_root / 'sample.py').write_text('VALUE = 2\n', encoding='utf-8')
            return SimpleNamespace(
                session_id='conversation_12345678', model=request['model'],
                exit_code=0, stdout_sha256='a' * 64, elapsed_ms=12,
                cleanup_status='CONFIRMED', usage=None)
        return launch

    def test_original_command_with_data_class_reassigns_both_directions(self):
        for original, target in (('opencode', 'agy'), ('agy', 'opencode')):
            with self.subTest(original=original, target=target):
                self.store = TaskStore(self.home / '.local/state/llmtool' / ('tasks-' + new_id()))
                (self.executor_root / 'sample.py').write_text('VALUE = 1\n', encoding='utf-8')
                self._write_opencode_model('opencode/deepseek-v4.1-flash-free#high')
                task = self._create_pm_task(original)
                old, _ = self._prepare_interrupted_assignment(task, original)
                args = self._delegate_args(task, original, data_class='SYNTHETIC')

                with patch.dict(os.environ, {'HOME': str(self.home)}), \
                        patch('llmtool.pm_caller.check_caller', return_value=None), \
                        patch.object(submission_cli.opencode_direct_developer, 'run',
                                     side_effect=self._launch_opencode_stub()) as oc_run, \
                        patch.object(submission_cli, 'run_route',
                                     side_effect=self._launch_agy_stub()) as agy_run, \
                        patch.object(submission_cli, '_agy_developer_profile',
                                     return_value=('gemini-test', 'high')), \
                        patch.object(submission_cli.shutil, 'which',
                                     return_value='/usr/bin/fake-cli'), \
                        patch.object(submission_cli, 'run_task_direct_result_event',
                                     return_value=0), \
                        redirect_stdout(io.StringIO()):
                    self.assertEqual(run_task_delegate_checked(args, store=self.store), 0)

                (oc_run if target == 'opencode' else agy_run).assert_called_once()
                (agy_run if target == 'opencode' else oc_run).assert_not_called()
                saved = self.store.get(task['task_id'])['developer']
                self.assertEqual(saved['provider_profile'], target)
                self.assertEqual(saved['state'], 'DEVELOPER_RESPONDED')
                self.assertNotIn('data_class', saved)
                self.assertEqual(saved['direct_handoffs'][-1]['old_assignment'], old['assignment_id'])

    def test_manual_reassignment_with_data_class_still_refuses(self):
        task = self._create_pm_task('opencode')
        old, _ = self._prepare_interrupted_assignment(task, 'opencode')
        manual_args = self._delegate_args(
            task, 'agy', reassign_from=old['assignment_id'], data_class='SYNTHETIC')
        for runner in (run_task_delegate, run_task_delegate_checked):
            with self.subTest(runner=runner.__name__):
                with patch.dict(os.environ, {'HOME': str(self.home)}), \
                        patch('llmtool.pm_caller.check_caller', return_value=None):
                    with self.assertRaisesRegex(LLMToolError, 'DIRECT_REASSIGN_ROUTE_REFUSED'):
                        runner(manual_args, store=self.store)

    def test_manual_reassignment_scope_mismatch_refuses(self):
        cases = (
            {'egress_basis': 'Changed egress basis'},
            {'read_path': ['other.py']},
            {'write_path': ['other.py']},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                self.store = TaskStore(self.home / '.local/state/llmtool' / ('tasks-' + new_id()))
                task = self._create_pm_task('opencode')
                old, _ = self._prepare_interrupted_assignment(task, 'opencode')
                args = self._delegate_args(
                    task, 'agy', reassign_from=old['assignment_id'], **changes)
                with patch.dict(os.environ, {'HOME': str(self.home)}), \
                        patch('llmtool.pm_caller.check_caller', return_value=None), \
                        patch.object(submission_cli, '_agy_developer_profile',
                                     return_value=('gemini-test', 'high')), \
                        patch.object(submission_cli.shutil, 'which',
                                     return_value='/usr/bin/fake-cli'):
                    with self.assertRaisesRegex(LLMToolError, 'DIRECT_REASSIGN_SCOPE_MISMATCH'):
                        run_task_delegate_checked(args, store=self.store)
                self.assertEqual(
                    self.store.get(task['task_id'])['developer']['assignment_id'],
                    old['assignment_id'])

    def test_agy_to_opencode_reassignment_uses_configured_free_and_paid_models(self):
        for model in ('opencode/deepseek-v4.1-flash-free#high',
                      'opencode-go/deepseek-v4.1-flash#high',
                      'opencode/paid-model'):
            with self.subTest(model=model):
                self.store = TaskStore(self.home / '.local/state/llmtool' / ('tasks-' + new_id()))
                (self.executor_root / 'sample.py').write_text('VALUE = 1\n', encoding='utf-8')
                self._write_opencode_model(model)
                task = self._create_pm_task('agy')
                _, args = self._prepare_interrupted_assignment(task, 'agy')

                with patch.dict(os.environ, {'HOME': str(self.home)}), \
                        patch('llmtool.pm_caller.check_caller', return_value=None), \
                        patch.object(submission_cli.opencode_direct_developer, 'run',
                                     side_effect=self._launch_opencode_stub(
                                         expected_model=model)) as oc_run, \
                        patch.object(submission_cli.shutil, 'which',
                                     return_value='/usr/bin/fake-cli'), \
                        patch.object(submission_cli, 'run_task_direct_result_event',
                                     return_value=0), \
                        redirect_stdout(io.StringIO()):
                    self.assertEqual(run_task_delegate_checked(args, store=self.store), 0)

                oc_run.assert_called_once()
                saved = self.store.get(task['task_id'])['developer']
                self.assertEqual(saved['provider_profile'], 'opencode')
                self.assertEqual(saved['model'], model)
                self.assertEqual(saved['state'], 'DEVELOPER_RESPONDED')

    def test_malformed_model_paused_worker_and_quota_fail_refuse(self):
        # Malformed OpenCode model refuses with OPENCODE_MODEL_INVALID.
        task = self._create_pm_task('agy')
        old, args = self._prepare_interrupted_assignment(task, 'agy')
        self._write_opencode_model('opencode/a/b-free')
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.pm_caller.check_caller', return_value=None), \
                patch.object(submission_cli.opencode_direct_developer, 'run') as oc_run:
            with self.assertRaises(LLMToolError) as caught:
                run_task_delegate_checked(args, store=self.store)
        self.assertEqual(getattr(caught.exception, 'direct_opencode_code', None),
                         'OPENCODE_MODEL_INVALID')
        oc_run.assert_not_called()
        self.assertEqual(self.store.get(task['task_id'])['developer']['assignment_id'],
                         old['assignment_id'])

        # PAUSED alternate worker refuses before launch.
        self._write_opencode_model('opencode-go/deepseek-v4.1-flash#high')
        worker_policy_fixture.update(self.policy_path, 'opencode-developer', 'PAUSED')
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.pm_caller.check_caller', return_value=None), \
                patch.object(submission_cli.opencode_direct_developer, 'run') as oc_run:
            with self.assertRaisesRegex(LLMToolError, 'DIRECT_REASSIGN_NO_AUTHORIZED_ALTERNATE'):
                run_task_delegate_checked(args, store=self.store)
        oc_run.assert_not_called()

        # Quota FAIL on alternate worker refuses before launch.
        worker_policy_fixture.update(self.policy_path, 'opencode-developer', 'ALLOWED')
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch('llmtool.pm_caller.check_caller', return_value=None), \
                patch.object(submission_cli.worker_availability, 'quota_refusal',
                             side_effect=lambda _s, w, **_k: (
                                 'WORKER_QUOTA_UNAVAILABLE' if w == 'opencode-developer' else None)), \
                patch.object(submission_cli.opencode_direct_developer, 'run') as oc_run:
            with self.assertRaisesRegex(LLMToolError, 'DIRECT_REASSIGN_ALTERNATE_UNAVAILABLE'):
                run_task_delegate_checked(args, store=self.store)
        oc_run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
