"""#2448: an AGY dispatch reaped before its prompt records a verified STOPPED.

The launcher, not the provider, supplies the evidence: a spawned running child,
confirmed cleanup, no provider session, no output and a non-zero exit. Every
other AGY end keeps its existing state, so the #2440 reassignment refuses it.
"""

import hashlib
import io
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import direct_reassignment
from llmtool import task_store as task_store_module
from llmtool import task_submission_cli as submission_cli
from llmtool.developer_routes import RouteError
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, new_id


import _chain_gate_fixture
import worker_policy_fixture


def setUpModule():
    worker_policy_fixture.start_module()


def tearDownModule():
    worker_policy_fixture.stop_module()


def _stop_facts(*, exit_code=1, stdout_sha256=None, cleanup_status='CONFIRMED',
                elapsed_ms=10):
    return {'process_exit_code': exit_code, 'stdout_sha256': stdout_sha256,
            'elapsed_ms': elapsed_ms, 'cleanup_status': cleanup_status}


def _stopped_error(facts=None, *, native_session_id=None):
    """A RouteError carrying the launcher's pre-prompt stop signal."""
    class _LauncherStopped(RouteError):
        stopped_before_prompt = True
        execution_facts = facts
        session_id = native_session_id

        def __init__(self):
            super().__init__('AGY_EXIT_NONZERO')
            # Set the launcher's own evidence after the base initializer, so
            # the stop signal is present whatever defaults RouteError applies.
            self.stopped_before_prompt = True
            self.execution_facts = facts
            self.native_session_id = native_session_id
            self.session_id = native_session_id

    return _LauncherStopped()


class AgyDirectStopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.policy_path = worker_policy_fixture.install(self)
        # These tests exercise the launcher's stop evidence, not the Owner
        # dispatch policy, so pin an ALLOWED decision (never the live policy).
        # The dispatch gate itself is covered by the route-wiring tests.
        _chain_gate_fixture.pass_through_chain_gate(self)
        # Store writes reach the same authorization seam directly, without the
        # delegate's chain gate; keep it deterministic for these STOPPED-record
        # tests (the gate's decisions are covered by the route-wiring tests).
        authorization = patch.object(
            submission_cli.worker_dispatch, 'authorize_task_store_change',
            return_value=None)
        authorization.start()
        self.addCleanup(authorization.stop)
        self.serial = 0

    def _store(self):
        self.serial += 1
        return TaskStore(Path(self.temp.name).resolve() / f'store-{self.serial}')

    def _make_task(self, store, route='agy'):
        request = 'Bounded direct stop fixture.'
        task = submission_cli._record(
            new_id(), new_id(), 'pm-' + new_id(), 'pm-profile', request,
            state='PM_RESPONDED', provider_type='openai-compatible',
            model='pm-model', backend='NO_NATIVE_SESSION_ID_EXPOSED',
            request_sha=hashlib.sha256(request.encode('utf-8')).hexdigest())
        acceptance = ['the result is recorded']
        task.update(
            response='PM returned a bounded task.',
            pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                              'base_url': None},
            pm_assignment={'schema_version': 1, 'route': route, 'risk': 'LOW',
                           'goal': 'Record the direct stop.', 'acceptance': acceptance})
        return store.create(task)

    def _delegate_args(self, task):
        return Namespace(
            task_id=task['task_id'], provider='agy', mode='direct', owner_ref=None,
            context_stdin=False, selection_source='PM',
            acceptance=['the result is recorded'],
            egress_basis='Approved synthetic test context.',
            executor_task='executor-d6', read_path=['src.py'], write_path=['src.py'],
            repair_packet=None, reassign_from=None, timeout=None)

    def _run_agy_stop(self, error):
        store = self._store()
        task = self._make_task(store)
        snapshot = SimpleNamespace(root='/registered/root', common_dir='/repo/.git',
                                   branch='task-branch', head='d' * 40, clean=True)
        registration = SimpleNamespace(
            task_id='executor-d6', trusted_agent_id='agy-developer',
            session_ref='session-ref', native_session_id=None,
            root=snapshot.root, common_dir=snapshot.common_dir,
            branch=snapshot.branch)
        prefix = '\nREQUIRED_SKILL_BEGIN path=card\nFixture rules.\nREQUIRED_SKILL_END path=card'
        route_config = {
            'executable': 'agy', 'default_model': 'gemini-test',
            'default_effort': 'high', 'denied_dirs': ['.gemini'],
            'state_dirs': {'policy': 'private-temp-only'}, 'max_diff_bytes': 4096}
        boundary = SimpleNamespace(
            prepare=lambda *_args, **_kwargs: (['/verified-boundary'], 'a' * 64),
            verify=lambda *_args, **_kwargs: {'verified': True,
                                             'policy_sha256': 'a' * 64})

        def runner(_route, _request, *, on_launch, on_child, **_kwargs):
            on_launch()
            on_child(321)
            raise error

        with patch.dict('os.environ', {'HOME': str(self.temp.name)}), \
                patch.object(submission_cli.task_completion, 'validate_task_binding'), \
                patch.object(submission_cli, '_delegate_d6_binding',
                             return_value=(registration, snapshot)), \
                patch.object(submission_cli.task_completion, 'bind_executor_task',
                             return_value=(registration, snapshot)), \
                patch.object(submission_cli.task_completion, 'collect_git_snapshot',
                             return_value=snapshot), \
                patch.object(submission_cli, '_reject_claude_reserved_d6'), \
                patch.object(submission_cli.worker_dispatch, 'evaluate',
                             return_value=SimpleNamespace(
                                 worker_key='agy', allowed=True,
                                 reason_code='OWNER_POLICY_ALLOWED',
                                 policy_sha256='a' * 64)), \
                patch.object(submission_cli, '_agy_developer_profile',
                             return_value=('gemini-test', 'high')), \
                patch.object(submission_cli, '_developer_card_prefix',
                             return_value=(prefix, hashlib.sha256(
                                 prefix.encode('utf-8')).hexdigest())), \
                patch.object(submission_cli, 'load_route_config',
                             return_value={'routes': {'agy': route_config}}), \
                patch.object(submission_cli.shutil, 'which', return_value='/usr/bin/agy'), \
                patch.object(submission_cli, 'for_platform', return_value=boundary), \
                patch.object(submission_cli.codex_direct_developer,
                             'process_start_time', return_value='123456'), \
                patch.object(submission_cli.os, 'getpgid', return_value=321), \
                redirect_stdout(io.StringIO()):
            status = submission_cli.run_task_agy_delegate(
                self._delegate_args(task), store=store, route_runner=runner)
        return status, task, store.get(task['task_id']), store

    def _inspect_reassignment(self, store, task, developer):
        args = Namespace(
            task_id=task['task_id'], reassign_from=developer['assignment_id'],
            executor_task='executor-d6', acceptance=developer['acceptance'],
            egress_basis=developer['egress_basis'],
            selection_source=developer['selection_source'],
            data_class=developer.get('data_class'))
        registration = SimpleNamespace(
            task_id='executor-d6', trusted_agent_id='agy-developer',
            session_ref='session-ref', native_session_id=None,
            root='/registered/root', common_dir='/repo/.git', branch='task-branch')
        snapshot = SimpleNamespace(head=developer['source_sha'], clean=True)
        return direct_reassignment.inspect(
            args, store, task, registration, snapshot, 'opencode',
            developer['read_paths'], developer['write_paths'])

    def test_pre_prompt_reap_records_stopped_and_reassignment_admits_it(self):
        status, _task, saved, store = self._run_agy_stop(
            _stopped_error(_stop_facts()))
        self.assertEqual(status, 1)
        developer = saved['developer']
        self.assertEqual(developer['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(developer['dispatch_boundary'], 'STOPPED')
        self.assertEqual(developer['provider_profile'], 'agy')
        self.assertEqual(developer['execution']['cleanup_status'], 'CONFIRMED')
        self.assertIsNone(developer['execution']['native_session_id'])
        self.assertIsNone(developer['execution']['stdout_sha256'])
        self.assertNotEqual(developer['execution']['process_exit_code'], 0)
        self.assertTrue(direct_reassignment.replacement_candidate(developer))
        plan = self._inspect_reassignment(store, saved, developer)
        self.assertEqual(plan.successor.trusted_agent_id, 'opencode-developer')

    def test_child_that_stopped_after_output_is_not_stopped(self):
        status, _task, saved, _store = self._run_agy_stop(
            _stopped_error(_stop_facts(stdout_sha256='a' * 64)))
        self.assertEqual(status, 1)
        developer = saved['developer']
        self.assertNotEqual(developer.get('dispatch_boundary'), 'STOPPED')
        self.assertFalse(direct_reassignment.replacement_candidate(developer))

    def test_unconfirmed_cleanup_is_not_stopped(self):
        status, _task, saved, _store = self._run_agy_stop(
            _stopped_error(_stop_facts(cleanup_status='UNKNOWN')))
        self.assertEqual(status, 1)
        developer = saved['developer']
        self.assertNotEqual(developer.get('dispatch_boundary'), 'STOPPED')
        self.assertFalse(direct_reassignment.replacement_candidate(developer))

    def test_recorded_provider_session_is_not_stopped(self):
        status, _task, saved, _store = self._run_agy_stop(
            _stopped_error(_stop_facts(), native_session_id='conversation_12345678'))
        self.assertEqual(status, 1)
        developer = saved['developer']
        self.assertNotEqual(developer.get('dispatch_boundary'), 'STOPPED')
        self.assertFalse(direct_reassignment.replacement_candidate(developer))

    def test_no_launcher_evidence_is_not_stopped(self):
        status, _task, saved, _store = self._run_agy_stop(
            RouteError('AGY_EXIT_NONZERO'))
        self.assertEqual(status, 1)
        developer = saved['developer']
        self.assertNotEqual(developer.get('dispatch_boundary'), 'STOPPED')
        self.assertFalse(direct_reassignment.replacement_candidate(developer))

    def test_opencode_stopped_record_is_unchanged(self):
        store = self._store()
        task = self._make_task(store, route='opencode')
        registration = SimpleNamespace(
            task_id='executor-d6', trusted_agent_id='opencode-developer',
            session_ref='session-ref', native_session_id=None,
            root='/registered/root', common_dir='/repo/.git',
            branch='task-branch')
        callback = submission_cli._direct_result_callback(
            task, registration, 'a' * 40, test_policy='LOCAL')
        developer = {
            'assignment_id': new_id(), 'selection_source': 'PM',
            'state': 'DEVELOPER_PENDING', 'provider_profile': 'opencode',
            'provider_type': None, 'model': None,
            'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
            'session_id': 'dev-' + new_id(), 'source_sha': 'a' * 40,
            'source_sha_binding': 'D6_REGISTRY_OBSERVED',
            'read_paths': ['src.py'], 'write_paths': ['src.py'],
            'acceptance': ['the result is recorded'], 'context_sha256': 'b' * 64,
            'egress_basis': 'Approved synthetic test context.', 'input_bytes': 10,
            'max_tokens': None, 'artifact_format': 'unified_diff', 'artifact': None,
            'artifact_sha256': None, 'usage': None, 'error_code': None,
            'execution': None, 'created_at': '2026-11-20T00:00:00Z',
            'timeout_seconds': 600, 'direct_result_callback': callback,
        }
        completion = {
            'state': 'WAITING_EXECUTOR', 'executor_task_id': 'executor-d6',
            'reviewer_task_id': None,
            'evidence': {
                'task_id': task['task_id'], 'run_id': task['run_id'],
                'contract_digest': task['task_binding']['contract_digest'],
                'executor_identity': 'opencode-developer',
                'executor_session_ref': 'session-ref',
                'executor_native_session_id': None,
                'executor_native_session': 'UNKNOWN',
                'execution_mode': 'DIRECT_D6', 'root': '/registered/root',
                'common_dir': '/repo/.git', 'branch': 'task-branch',
                'source_sha': 'a' * 40, 'result_head': None},
            'missing_gates': [], 'pm_dispatch': 'NOT_STARTED',
            'pm_response': None, 'pm_recommendation': None,
            'pm_usage': None, 'pm_error_code': None,
        }
        # Use the store's D6-bound dispatch path, as the OpenCode delegate
        # does, so the worker-dispatch gate sees the real assignment.
        bound = store.d6_bind_compare_and_update(
            task['task_id'], executor_task_id='executor-d6',
            expected={'developer': None, 'completion': None},
            developer=developer, completion=completion)
        dispatching = dict(developer, state='DEVELOPER_DISPATCHING',
                           provider_type='opencode-cli',
                           model='opencode/test-free', max_tokens=4096)
        prepared = store.prepare_direct_dispatch(
            task['task_id'], expected_developer=bound['developer'],
            developer=dispatching)
        starting = store.authorize_direct_spawn(
            task['task_id'], expected_developer=prepared['developer'])
        launched = store.record_opencode_launch(
            task['task_id'], assignment_id=developer['assignment_id'], pid=321,
            start_time='123456', pgid=321)
        running = launched['developer']
        execution = {key: value for key, value in running['execution'].items()
                     if not key.startswith('child_')}
        execution.update(status='UNKNOWN', cleanup_status='CONFIRMED',
                         process_exit_code=1)
        stopped = dict(
            running, state='DEVELOPER_OUTCOME_UNKNOWN',
            error_code='OPENCODE_STOPPED_BEFORE_PROMPT', usage=None,
            execution=execution)
        saved = store.record_direct_stop(
            task['task_id'], expected_developer=running, developer=stopped)
        self.assertEqual(saved['developer']['dispatch_boundary'], 'STOPPED')
        self.assertTrue(direct_reassignment.replacement_candidate(
            saved['developer']))
        self.assertEqual(starting['developer']['dispatch_boundary'], 'STARTING')

    def test_claude_and_gw_agent_cannot_record_stopped(self):
        _status, task, saved, _store = self._run_agy_stop(
            _stopped_error(_stop_facts()))
        developer = saved['developer']
        for profile, provider_type in (
                ('claude', 'claude-cli'), ('gw-agent', 'gw-agent-cli')):
            with self.subTest(provider=profile):
                record = dict(task)
                record['developer'] = dict(
                    developer, provider_profile=profile,
                    provider_type=provider_type,
                    backend_session_semantics='CLI_REPORTED_SESSION_ID',
                    max_tokens=None)
                record['record_sha256'] = task_store_module._digest(record)
                with self.assertRaises(LLMToolError) as caught:
                    task_store_module._validate(record)
                self.assertEqual(
                    caught.exception.task_store_error_code,
                    'DIRECT_DISPATCH_BOUNDARY_INVALID')


if __name__ == '__main__':
    unittest.main()
