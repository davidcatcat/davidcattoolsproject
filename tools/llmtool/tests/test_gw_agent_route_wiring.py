import copy
import dataclasses
import hashlib
import inspect
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from llmtool import (
    gw_agent_sessions,
    review_dispatch,
    task_completion,
    task_status,
    task_submission_cli,
    worker_dispatch,
)
from llmtool.developer_routes import GwAgentRoute, RouteError, load_route_config
from llmtool.developer_routes.gw_agent import cleanup_audit_copies
from llmtool.errors import LLMToolError
from llmtool.providers.cli_lifecycle import CLIHostTermination, _Cancelled
from llmtool import task_store


import review_trust_fixture


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


class GwAgentRouteWiringTests(unittest.TestCase):
    def setUp(self):
        self._real_gw_agent_endpoint = task_submission_cli._gw_agent_endpoint
        endpoint = patch.object(task_submission_cli, '_gw_agent_endpoint', return_value=({
            'type': 'openai-compatible', 'model': 'authorized-model',
            'base_url': 'https://authorized.example'}, {'max_tokens': 49152}))
        endpoint.start()
        self.addCleanup(endpoint.stop)

    def test_gw_agent_endpoint_reads_the_gateway_profile_not_company_gw(self):
        profile = {'name': 'work', 'base_url': 'https://gateway.example', 'model': 'm1',
                   'api_key_env': None, 'context_cap_tokens': 9000, 'max_output_tokens': 700}
        with patch.object(task_submission_cli.gateway_profiles, 'load_gateway_profile',
                          return_value=profile), \
             patch.object(task_submission_cli, 'resolve_profile_config',
                          side_effect=AssertionError('company-gw read')):
            provider, generation = self._real_gw_agent_endpoint()
        self.assertEqual(provider, {'type': 'openai-compatible', 'model': 'm1',
                                     'base_url': 'https://gateway.example'})
        self.assertEqual(generation, {'max_tokens': 700})

    def test_delegate_sweeps_gw_agent_sessions_before_checks(self):
        args = Namespace(mode='proposal')
        store = object()
        with patch('llmtool.gw_agent_sessions.sweep_quietly') as sweep:
            with self.assertRaises(LLMToolError):
                task_submission_cli.run_task_gw_agent_delegate(args, store=store)
            sweep.assert_called_once_with(store)

    def test_company_gateway_origin_with_path_is_refused(self):
        task = {'schema_version': 2, 'task_id': 'a' * 32, 'run_id': 'b' * 32,
            'state': 'PM_RESPONDED', 'response': 'PM response', 'request': 'bounded task',
            'task_binding': {'contract_digest': 'c' * 64},
            'pm_assignment': {'route': 'gw-agent', 'risk': 'LOW', 'goal': 'goal',
                              'acceptance': ['record result']},
            'developer': None, 'completion': None}

        class Store:
            def get(self, _task_id):
                return copy.deepcopy(task)

        registration = SimpleNamespace(task_id='executor-d6', root='/repo/worktree',
            common_dir='/repo/.git', branch='task-branch', trusted_agent_id='codex',
            session_ref='session-ref', native_session_id=None)
        snapshot = SimpleNamespace(head='d' * 40)
        args = Namespace(task_id='a' * 32, mode='direct', context_stdin=False,
            data_class='SYNTHETIC', egress_basis='Synthetic test context.',
            acceptance=['record result'], selection_source='PM', executor_task='executor-d6',
            read_path=['src.py'], write_path=['src.py'])
        route_config = {'allowed_data_classes': ['synthetic', 'public_repo'], 'profile': {
            'data_class_map': {'SYNTHETIC': 'synthetic', 'PUBLIC_REPO': 'public_repo'}, 'context_cap_tokens': 32768,
            'max_output_tokens': 8192}}
        with patch.object(task_completion, 'validate_task_binding'), \
             patch.object(task_submission_cli, '_delegate_d6_binding',
                          return_value=(registration, snapshot)), \
             patch.object(task_submission_cli, '_assignment_paths',
                          return_value=(['src.py'], ['src.py'])), \
             patch.object(task_submission_cli, 'load_route_config',
                          return_value={'routes': {'gw-agent': route_config}}), \
             patch.object(task_submission_cli, '_gw_agent_endpoint', return_value=({
                 'type': 'openai-compatible', 'model': 'authorized-model',
                 'base_url': 'https://gateway.example/v1'}, {'max_tokens': 4096})):
            with self.assertRaisesRegex(
                    LLMToolError, 'gateway profile is unavailable or invalid'):
                task_submission_cli.run_task_gw_agent_delegate(args, store=Store())

    def test_pm_assignment_accepts_gw_agent_and_missing_data_class_is_pre_mutation(self):
        response = json.dumps({'schema_version': 1, 'summary': 'bounded',
            'developer_assignment': {'route': 'gw-agent', 'risk': 'LOW', 'goal': 'goal',
                                     'acceptance': ['record result']}})
        envelope = json.dumps({'request': 'bounded task', 'response': response})
        _request, _response, assignment = task_submission_cli._read_pm_record_input(
            io.StringIO(envelope))
        self.assertEqual(assignment['route'], 'gw-agent')

        class UnreadStore:
            def get(self, _task_id):
                raise AssertionError('missing data class must be refused before Task read')

        args = Namespace(task_id='a' * 32, mode='direct', context_stdin=False,
                          egress_basis='Approved non-sensitive context.', data_class=None)
        with self.assertRaises(LLMToolError):
            task_submission_cli.run_task_gw_agent_delegate(args, store=UnreadStore())

    def test_gw_agent_refuses_claude_reserved_d6_before_route_runner(self):
        task = {'schema_version': 2, 'task_id': 'a' * 32, 'run_id': 'b' * 32,
            'state': 'PM_RESPONDED', 'response': 'PM response', 'request': 'bounded task',
            'task_binding': {'contract_digest': 'c' * 64},
            'pm_assignment': {'route': 'gw-agent', 'risk': 'LOW', 'goal': 'goal',
                              'acceptance': ['record result']},
            'developer': None, 'completion': None}

        class Store:
            def __init__(self):
                self.value = copy.deepcopy(task)

            def get(self, _task_id):
                return copy.deepcopy(self.value)

        registration = SimpleNamespace(task_id='executor-d6', root='/repo/worktree',
            common_dir='/repo/.git', branch='task-branch',
            trusted_agent_id='claude-developer', session_ref='session-ref',
            native_session_id=None)
        snapshot = SimpleNamespace(head='d' * 40)
        args = Namespace(task_id=task['task_id'], mode='direct', context_stdin=False,
            data_class='SYNTHETIC', egress_basis='Synthetic test context.',
            acceptance=['record result'], selection_source='PM', executor_task='executor-d6',
            read_path=['src.py'], write_path=['src.py'])
        store = Store()
        with patch.object(task_completion, 'validate_task_binding'), \
             patch.object(task_submission_cli, '_delegate_d6_binding',
                          return_value=(registration, snapshot)), \
             patch.object(task_submission_cli, 'run_route') as runner:
            with self.assertRaisesRegex(LLMToolError, 'D6_CLAUDE_RESERVED'):
                task_submission_cli.run_task_gw_agent_delegate(
                    args, store=store, route_runner=runner)
        self.assertIsNone(store.value['developer'])
        runner.assert_not_called()

    def test_route_config_and_data_class_are_validated(self):
        config = load_route_config()['routes']['gw-agent']
        self.assertEqual(config['package_path'], '~/.local/lib/api-agent-gateway')
        self.assertEqual(config['allowed_data_classes'], ['synthetic', 'public_repo'])
        self.assertEqual(config['profile']['adapter'], 'openai-chat-tools')
        self.assertNotIn('base_url', config['profile'])
        self.assertEqual(config['profile']['allowed_data_classes'], ['synthetic', 'public_repo'])
        self.assertEqual(config['profile']['data_class_map'],
                         {'SYNTHETIC': 'synthetic', 'PUBLIC_REPO': 'public_repo'})
        self.assertEqual(config['authorization_ref'],
            'https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6760670167')
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / 'api_agent_gateway'
            package.mkdir()
            (package / '__init__.py').write_text('', encoding='utf-8')
            config['package_path'] = temp
            config['profile']['base_url'] = 'http://127.0.0.1:9/v1'
            route = GwAgentRoute(config)
        request = {'root': str(Path.cwd()), 'model': config['default_model'], 'effort': 'low',
            'expected_branch': 'task-branch', 'expected_head': 'a' * 40,
            'write_paths': ['src.py'], 'executable': '/usr/bin/python3', 'task': {
            'schema_version': 1, 'task_id': 'b' * 32, 'source_sha': 'a' * 40,
            'goal': 'Edit src.py.', 'acceptance': ['record the result'],
            'read_paths': ['src.py'],
            'write_paths': ['src.py'], 'budgets': {'max_turns': 4,
                'max_input_tokens': 10000, 'max_output_tokens': 1000,
                'max_wall_seconds': 60, 'max_tool_result_bytes': 4096,
                'max_sent_bytes': 100000},
            'egress_basis': 'Synthetic test content.', 'data_class': 'synthetic'}}
        route.validate_request(request)
        request['task']['data_class'] = 'public_repo'
        route.validate_request(request)
        request['task']['data_class'] = 'RESTRICTED'
        with self.assertRaises(LLMToolError):
            route.validate_request(request)

    def test_real_route_config_expands_package_path_from_home(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / 'home'
            package = home / '.local' / 'lib' / 'api-agent-gateway'
            (package / 'api_agent_gateway').mkdir(parents=True)
            with patch.dict(os.environ, {'HOME': str(home)}):
                config = load_route_config()['routes']['gw-agent']
                self.assertEqual(config['package_path'], '~/.local/lib/api-agent-gateway')
                config['profile']['base_url'] = 'https://authorized.example/v1'
                route = GwAgentRoute(config)
            self.assertEqual(route.package_path, str(package.resolve()))

    def test_non_synthetic_delegate_is_refused_before_task_mutation(self):
        task = {'schema_version': 2, 'task_id': 'a' * 32, 'run_id': 'b' * 32,
            'state': 'PM_RESPONDED', 'response': 'PM response', 'request': 'synthetic task',
            'task_binding': {'contract_digest': 'c' * 64},
            'pm_assignment': {'route': 'gw-agent', 'risk': 'LOW', 'goal': 'goal',
                              'acceptance': ['record result']},
            'developer': None}

        class Store:
            def __init__(self):
                self.mutations = 0

            def get(self, _task_id):
                return dict(task)

            def update(self, _task_id, **_updates):
                self.mutations += 1
                raise AssertionError('disallowed data class must not mutate Task')

        registration = SimpleNamespace(task_id='executor-d6', root='/repo/worktree',
            common_dir='/repo/.git', branch='task-branch', trusted_agent_id='codex',
            session_ref='session-ref', native_session_id=None)
        snapshot = SimpleNamespace(head='d' * 40)
        args = Namespace(task_id='a' * 32, mode='direct', context_stdin=False,
            data_class='INTERNAL', egress_basis='Synthetic test content only.',
            acceptance=['record result'], selection_source='PM', executor_task='executor-d6',
            read_path=['src.py'], write_path=['src.py'])
        store = Store()
        with patch.object(task_completion, 'validate_task_binding'), \
             patch.object(task_submission_cli, '_delegate_d6_binding',
                          return_value=(registration, snapshot)), \
             patch.object(task_submission_cli, '_assignment_paths',
                          return_value=(['src.py'], ['src.py'])):
            with self.assertRaises(LLMToolError):
                task_submission_cli.run_task_gw_agent_delegate(args, store=store)
        self.assertEqual(store.mutations, 0)

    def test_private_data_class_is_refused_with_profile_error(self):
        task = {'schema_version': 2, 'task_id': 'a' * 32, 'run_id': 'b' * 32,
            'state': 'PM_RESPONDED', 'response': 'PM response', 'request': 'private task',
            'task_binding': {'contract_digest': 'c' * 64},
            'pm_assignment': {'route': 'gw-agent', 'risk': 'LOW', 'goal': 'goal',
                              'acceptance': ['record result']},
            'developer': None}

        class Store:
            def __init__(self):
                self.mutations = 0

            def get(self, _task_id):
                return dict(task)

            def update(self, _task_id, **_updates):
                self.mutations += 1
                raise AssertionError('disallowed data class must not mutate Task')

        registration = SimpleNamespace(task_id='executor-d6', root='/repo/worktree',
            common_dir='/repo/.git', branch='task-branch', trusted_agent_id='codex',
            session_ref='session-ref', native_session_id=None)
        snapshot = SimpleNamespace(head='d' * 40)
        args = Namespace(task_id='a' * 32, mode='direct', context_stdin=False,
            data_class='PRIVATE', egress_basis='Approved non-sensitive context.',
            acceptance=['record result'], selection_source='PM', executor_task='executor-d6',
            read_path=['src.py'], write_path=['src.py'])
        store = Store()
        with patch.object(task_completion, 'validate_task_binding'), \
             patch.object(task_submission_cli, '_delegate_d6_binding',
                          return_value=(registration, snapshot)), \
             patch.object(task_submission_cli, '_assignment_paths',
                          return_value=(['src.py'], ['src.py'])):
            with self.assertRaisesRegex(
                    LLMToolError, 'gw-agent data class is not allowed by its profile'):
                task_submission_cli.run_task_gw_agent_delegate(args, store=store)
        self.assertEqual(store.mutations, 0)

    def test_delegate_without_egress_basis_is_refused(self):
        class UnreadStore:
            def get(self, _task_id):
                raise AssertionError('missing egress basis must be refused before Task read')

        for missing in (None, '', '   '):
            args = Namespace(task_id='a' * 32, mode='direct', context_stdin=False,
                             egress_basis=missing, data_class='PUBLIC_REPO')
            with self.assertRaises(LLMToolError):
                task_submission_cli.run_task_gw_agent_delegate(args, store=UnreadStore())

    def test_public_repo_data_class_passes_class_check_and_invokes_runner(self):
        task = {'schema_version': 2, 'task_id': 'a' * 32, 'run_id': 'b' * 32,
            'state': 'PM_RESPONDED', 'response': 'PM response', 'request': 'public repo task',
            'task_binding': {'contract_digest': 'c' * 64},
            'pm_assignment': {'route': 'gw-agent', 'risk': 'LOW', 'goal': 'goal',
                              'acceptance': ['record result']},
            'developer': None, 'completion': None}

        class Store:
            def __init__(self):
                self._temp = tempfile.TemporaryDirectory(prefix='gw-agent-store-')
                self.root = Path(self._temp.name) / 'tasks'
                self.value = dict(task)

            def get(self, _task_id):
                return dict(self.value)

            def compare_and_update(self, _task_id, *, expected, **updates):
                self.value.update(updates)
                return dict(self.value)

            def d6_bind_compare_and_update(self, task_id, *, executor_task_id,
                                           expected, **updates):
                return self.compare_and_update(task_id, expected=expected, **updates)

            def update(self, _task_id, **updates):
                self.value.update(updates)
                return dict(self.value)

        registration = SimpleNamespace(task_id='executor-d6', root='/repo/worktree',
            common_dir='/repo/.git', branch='task-branch', trusted_agent_id='codex',
            session_ref='session-ref', native_session_id=None)
        snapshot = SimpleNamespace(head='d' * 40)
        args = Namespace(task_id='a' * 32, mode='direct', context_stdin=False,
            data_class='PUBLIC_REPO', egress_basis='Public repository test context.',
            acceptance=['record result'], selection_source='PM', executor_task='executor-d6',
            read_path=['src.py'], write_path=['src.py'])
        store = Store()
        self.addCleanup(store._temp.cleanup)
        route = SimpleNamespace(validate_profile=lambda *_args: None)
        decision = worker_dispatch.PolicyDecision('company-gw-agent', True,
                                                  'OWNER_POLICY_ALLOWED', 'f' * 64)
        seen_requests = []
        def fake_runner(_route, request, *, gate, **_kwargs):
            self.assertIs(gate(), decision)
            seen_requests.append(copy.deepcopy(request))
            raise RouteError('GW_AGENT_TIMEOUT')

        with patch.object(task_completion, 'validate_task_binding'), \
             patch.object(task_completion, 'bind_executor_task',
                          return_value=(registration, snapshot)), \
             patch.object(task_submission_cli, '_delegate_d6_binding',
                          return_value=(registration, snapshot)), \
             patch.object(task_submission_cli, '_assignment_paths',
                          return_value=(['src.py'], ['src.py'])), \
             patch.object(task_submission_cli, '_remote_receipt_test_policy', return_value='REMOTE_RECEIPT'), \
             patch.object(task_submission_cli, '_direct_result_callback', return_value={}), \
             patch.object(task_submission_cli, 'resolve_profile_config',
                          return_value={'generation': {'max_tokens': 49152}}), \
             patch.object(task_submission_cli, 'selected_provider', return_value={
                 'type': 'openai-compatible', 'model': 'authorized-model',
                 'base_url': 'https://authorized.example'}), \
             patch.object(worker_dispatch, 'authorize_dispatch'), \
             patch.object(worker_dispatch, 'evaluate', return_value=decision), \
             patch.object(task_submission_cli, 'GwAgentRoute', return_value=route), \
             patch.object(task_submission_cli, 'for_platform', return_value=object()), \
             patch.object(task_submission_cli, '_task_view', return_value={}):
            status = task_submission_cli.run_task_gw_agent_delegate(
                args, store=store, route_runner=fake_runner)

        self.assertEqual(status, 1)
        self.assertEqual(len(seen_requests), 1)
        self.assertEqual(seen_requests[0]['task']['data_class'], 'public_repo')
        self.assertEqual(store.value['developer']['state'], 'DEVELOPER_UNAVAILABLE')
        self.assertEqual(store.value['developer']['error_code'], 'DEVELOPER_GW_AGENT_TIMEOUT')

    def test_gw_agent_uses_claude_formal_reviewer(self):
        task = {'task_id': 'a' * 32, 'run_id': 'b' * 32,
            'task_binding': {'contract_digest': 'c' * 64},
            'completion': {'executor_task_id': 'executor-d6',
                           'evidence': {'common_dir': '/repo/.git'}},
            'developer': {'provider_profile': 'gw-agent'}}
        executor = SimpleNamespace(root='/repo/worktree', common_dir='/repo/.git',
                                    branch='task-branch', result_sha='d' * 40)
        snapshot = SimpleNamespace(clean=True, head='d' * 40, root='/repo/worktree',
            common_dir='/repo/.git', branch='task-branch')
        pull = {'number': 7, 'head': {'sha': 'd' * 40}, 'base': {'sha': 'e' * 40}}
        with patch.object(task_completion, 'validate_task_binding'), \
             patch.object(task_completion, 'read_d6_registration', return_value=executor), \
             patch.object(task_completion, 'collect_git_snapshot', return_value=snapshot), \
             patch.object(task_completion, '_github_get', return_value=pull):
            result = review_dispatch._preflight(None, 'a' * 32, 'executor-d6', 7, 1, task=task)
        self.assertEqual(result[-1], 'claude-reviewer')

    def test_paused_dispatch_claims_once_without_running_provider(self):
        task = {'schema_version': 2, 'task_id': 'a' * 32, 'run_id': 'b' * 32,
            'state': 'PM_RESPONDED', 'response': 'PM response', 'request': 'bounded task',
            'task_binding': {'contract_digest': 'c' * 64},
            'pm_assignment': {'route': 'gw-agent', 'risk': 'LOW', 'goal': 'goal',
                              'acceptance': ['record result']},
            'developer': None, 'completion': None}

        class Store:
            def __init__(self):
                self.value = dict(task)
                self.claims = 0
                self.developer_states = []
                self.developer_records = []

            def get(self, _task_id):
                return copy.deepcopy(self.value)

            def compare_and_update(self, _task_id, *, expected, **updates):
                self.claims += 1
                for key, value in expected.items():
                    if self.value.get(key) != value:
                        raise task_store.TaskStoreConflict('expected value changed')
                denial = worker_dispatch.authorize_task_store_change(
                    self.value, updates, expected)
                if denial is not None:
                    raise denial
                self.value.update(copy.deepcopy(updates))
                if type(self.value.get('developer')) is dict:
                    self.developer_states.append(self.value['developer']['state'])
                    self.developer_records.append(copy.deepcopy(self.value['developer']))
                return copy.deepcopy(self.value)

            def d6_bind_compare_and_update(self, task_id, *, executor_task_id,
                                           expected, **updates):
                return self.compare_and_update(task_id, expected=expected, **updates)

            def update(self, _task_id, **updates):
                self.value.update(copy.deepcopy(updates))
                return copy.deepcopy(self.value)

        registration = SimpleNamespace(task_id='executor-d6', root='/repo/worktree',
            common_dir='/repo/.git', branch='task-branch', trusted_agent_id='codex',
            session_ref='session-ref', native_session_id=None)
        snapshot = SimpleNamespace(head='d' * 40)
        args = Namespace(task_id='a' * 32, mode='direct', context_stdin=False,
            data_class='SYNTHETIC', egress_basis='Synthetic test context.',
            acceptance=['record result'], selection_source='PM', executor_task='executor-d6',
            read_path=['src.py'], write_path=['src.py'])
        store = Store()
        with patch.object(task_completion, 'validate_task_binding'), \
             patch.object(task_completion, 'bind_executor_task',
                          return_value=(registration, snapshot)), \
             patch.object(task_submission_cli, '_delegate_d6_binding',
                          return_value=(registration, snapshot)), \
             patch.object(task_submission_cli, '_assignment_paths',
                          return_value=(['src.py'], ['src.py'])), \
             patch.object(task_submission_cli, '_remote_receipt_test_policy', return_value='REMOTE_RECEIPT'), \
             patch.object(task_submission_cli, '_direct_result_callback', return_value={}), \
             patch.object(task_submission_cli, 'load_route_config',
                          return_value={'routes': {'gw-agent': {
                              'allowed_data_classes': ['synthetic', 'public_repo'],
                              'profile': {'adapter': 'openai-chat-tools',
                                  'base_url': 'https://api.openai.com/v1',
                                  'model': 'gw-agent-default',
                                  'context_cap_tokens': 32768,
                                  'max_output_tokens': 8192,
                                  'allowed_data_classes': ['synthetic', 'public_repo']},
                              'default_model': 'gw-agent-default', 'default_effort': 'low'}}}), \
             patch.object(task_submission_cli, 'resolve_profile_config',
                          return_value={'generation': {'max_tokens': 49152}}), \
             patch.object(task_submission_cli, 'selected_provider', return_value={
                 'type': 'openai-compatible', 'model': 'authorized-model',
                 'base_url': 'https://authorized.example'}), \
             patch.object(worker_dispatch, 'evaluate', return_value=worker_dispatch.PolicyDecision(
                 'company-gw-agent', True, 'OWNER_POLICY_ALLOWED', 'f' * 64)), \
             patch.object(worker_dispatch, 'authorize_dispatch',
                          side_effect=worker_dispatch.WorkerDispatchDenied('OWNER_PAUSED')), \
             patch.object(task_submission_cli, 'run_route') as runner, \
             patch.object(task_submission_cli, '_task_view', return_value={}):
            status = task_submission_cli.run_task_gw_agent_delegate(args, store=store)
        self.assertEqual(status, 1)
        # The initial claim, DISPATCHING transition, and failure persistence each compare once.
        self.assertEqual(store.claims, 3)
        self.assertEqual(store.developer_states,
                         ['DEVELOPER_PENDING', 'DEVELOPER_DISPATCHING',
                          'DEVELOPER_UNAVAILABLE'])
        self.assertIsNone(store.developer_records[0]['provider_type'])
        self.assertIsNone(store.developer_records[0]['model'])
        self.assertEqual(store.developer_records[1]['provider_type'], 'gw-agent-cli')
        self.assertEqual(store.developer_records[1]['model'], 'authorized-model')
        self.assertEqual(store.value['developer']['state'], 'DEVELOPER_UNAVAILABLE')
        self.assertEqual(store.value['developer']['error_code'], 'DEVELOPER_OWNER_PAUSED')
        self.assertEqual(store.value['dispatch_audit'][-1]['action'], 'DISPATCH')
        self.assertEqual(store.value['dispatch_audit'][-1]['decision'], 'ALLOWED')
        runner.assert_not_called()

    def test_gw_agent_developer_shape_includes_execution_and_direct_callback(self):
        task_id, run_id = 'a' * 32, 'b' * 32
        task = {'task_id': task_id, 'run_id': run_id,
                'task_binding': {'contract_digest': 'c' * 64}}
        registration = SimpleNamespace(task_id='executor-d6', root='/repo/worktree',
            common_dir='/repo/.git', branch='task-branch', trusted_agent_id='codex',
            session_ref='session-ref', native_session_id=None)

        # Keep this record shape aligned with run_task_gw_agent_delegate.
        developer = {
            'assignment_id': 'd' * 32, 'selection_source': 'PM',
            'state': 'DEVELOPER_PENDING', 'provider_profile': 'gw-agent',
            'provider_type': None, 'model': None,
            'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
            'session_id': 'dev-' + 'e' * 32, 'source_sha': 'f' * 40,
            'source_sha_binding': 'D6_REGISTRY_OBSERVED',
            'read_paths': ['src.py'], 'write_paths': ['src.py'],
            'acceptance': ['record result'], 'context_sha256': '1' * 64,
            'egress_basis': 'Synthetic test context.', 'input_bytes': 128,
            'max_tokens': None, 'artifact_format': 'unified_diff',
            'artifact': None, 'artifact_sha256': None, 'usage': None,
            'error_code': None, 'execution': None, 'created_at': '2026-01-01T00:00:00Z',
            'direct_result_callback': task_submission_cli._direct_result_callback(
                task, registration, 'f' * 40, test_policy='REMOTE_RECEIPT'),
        }

        self.assertIn('execution', developer)
        self.assertIn('direct_result_callback', developer)
        self.assertIn(set(developer), task_store._DEVELOPER_SHAPES)

        # The callback profile allowlist is enforced in TaskStore._validate;
        # guard its gw-agent entry without creating a TaskStore root or record.
        validator = inspect.getsource(task_store._validate)
        self.assertIn("developer['provider_profile'] not in ('opencode', 'codex', 'agy', 'gw-agent', 'claude')",
                      validator)

    def test_pending_gw_agent_provider_binding_is_validated_at_dispatch(self):
        task_id, run_id = 'a' * 32, 'b' * 32
        task_contract = {
            'schema_version': 1, 'contract_version': 1, 'task_id': task_id,
            'goal': 'Run the bounded gw-agent task.', 'authorized_scope': ['pm-planning'],
            'forbidden_scope': [], 'criteria': [{'criterion_id': 'result',
                'description': 'Record the result.', 'kind': 'ARTIFACT'}],
            'previous_digest': None,
        }
        contract = task_store.parse_task_contract(json.dumps(task_contract))
        binding = vars(contract.binding(run_id))
        task = {'task_id': task_id, 'run_id': run_id, 'task_binding': binding}
        registration = SimpleNamespace(task_id='executor-d6', root='/repo/worktree',
            common_dir='/repo/.git', branch='task-branch', trusted_agent_id='codex',
            session_ref='session-ref', native_session_id=None)
        developer = {
            'assignment_id': 'd' * 32, 'selection_source': 'PM',
            'state': 'DEVELOPER_PENDING', 'provider_profile': 'gw-agent',
            'provider_type': None, 'model': None,
            'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
            'session_id': 'dev-' + 'e' * 32, 'source_sha': 'f' * 40,
            'source_sha_binding': 'D6_REGISTRY_OBSERVED',
            'read_paths': ['src.py'], 'write_paths': ['src.py'],
            'acceptance': ['Record the result.'], 'context_sha256': '1' * 64,
            'egress_basis': 'Synthetic test context.', 'input_bytes': 128,
            'max_tokens': None, 'artifact_format': 'unified_diff',
            'artifact': None, 'artifact_sha256': None, 'usage': None,
            'error_code': None, 'execution': None, 'created_at': '2026-01-01T00:00:00Z',
            'direct_result_callback': task_submission_cli._direct_result_callback(
                task, registration, 'f' * 40, test_policy='REMOTE_RECEIPT'),
        }
        record = {
            'schema_version': 2, 'task_id': task_id, 'run_id': run_id,
            'pm_session_id': 'pm-test', 'pm_provider_profile': 'test-provider',
            'pm_provider_type': 'test-provider', 'pm_model': 'test-model',
            'backend_session_semantics': 'EPHEMERAL_SINGLE_CALL',
            'native_session_id': None, 'state': 'PM_RESPONDED',
            'created_at': '2026-01-01T00:00:00Z', 'updated_at': '2026-01-01T00:00:00Z',
            'task_contract': task_contract, 'task_binding': binding,
            'request': 'bounded request',
            'request_sha256': hashlib.sha256(b'bounded request').hexdigest(),
            'response': 'PM response', 'usage': None, 'error_code': None,
            'developer': developer,
            'pm_assignment': {'schema_version': 1, 'route': 'gw-agent', 'risk': 'LOW',
                'goal': 'Run the bounded gw-agent task.',
                'acceptance': ['Record the result.']},
            'dispatch_audit': [],
        }
        record['record_sha256'] = task_store._digest(record)
        task_store._validate(record)

        dispatching = copy.deepcopy(developer)
        dispatching.update(state='DEVELOPER_DISPATCHING',
                           provider_type='gw-agent-cli', model='gw-agent-default')
        self.assertFalse(worker_dispatch._developer_identity_changed(developer, dispatching))
        self.assertTrue(worker_dispatch._initial_provider_binding(developer, dispatching))
        updated = copy.deepcopy(record)
        updated['developer'] = dispatching
        updated['record_sha256'] = task_store._digest(updated)
        task_store._validate(updated)

        decision = worker_dispatch.PolicyDecision(
            'company-gw-agent', True, 'OWNER_POLICY_ALLOWED', 'f' * 64)
        with patch.object(worker_dispatch, 'evaluate', return_value=decision):
            denial = worker_dispatch.authorize_task_store_change(
                record, {'developer': dispatching}, {'developer': developer})
        self.assertIsNone(denial)
        self.assertEqual(record['dispatch_audit'][-1]['action'], 'DISPATCH')

    def test_route_failures_map_to_persistable_developer_states(self):
        task = {'schema_version': 2, 'task_id': 'a' * 32, 'run_id': 'b' * 32,
            'state': 'PM_RESPONDED', 'response': 'PM response', 'request': 'bounded task',
            'task_binding': {'contract_digest': 'c' * 64},
            'pm_assignment': {'route': 'gw-agent', 'risk': 'LOW', 'goal': 'goal',
                              'acceptance': ['record result']},
            'developer': None, 'completion': None}

        class Store:
            def __init__(self):
                self._temp = tempfile.TemporaryDirectory(prefix='gw-agent-store-')
                self.root = Path(self._temp.name) / 'tasks'
                self.value = dict(task)

            def get(self, _task_id):
                return dict(self.value)

            def compare_and_update(self, _task_id, *, expected, **updates):
                self.value.update(updates)
                return dict(self.value)

            def d6_bind_compare_and_update(self, task_id, *, executor_task_id,
                                           expected, **updates):
                return self.compare_and_update(task_id, expected=expected, **updates)

            def update(self, _task_id, **updates):
                self.value.update(updates)
                return dict(self.value)

        registration = SimpleNamespace(task_id='executor-d6', root='/repo/worktree',
            common_dir='/repo/.git', branch='task-branch', trusted_agent_id='codex',
            session_ref='session-ref', native_session_id=None)
        snapshot = SimpleNamespace(head='d' * 40)
        args = Namespace(task_id='a' * 32, mode='direct', context_stdin=False,
            data_class='SYNTHETIC', egress_basis='Synthetic test context.',
            acceptance=['record result'], selection_source='PM', executor_task='executor-d6',
            read_path=['src.py'], write_path=['src.py'])
        store = Store()
        self.addCleanup(store._temp.cleanup)
        route = SimpleNamespace(validate_profile=lambda *_args: None)
        decision = worker_dispatch.PolicyDecision('company-gw-agent', True,
                                                  'OWNER_POLICY_ALLOWED', 'f' * 64)
        def fail_runner(_route, _request, *, gate, **_kwargs):
            self.assertIs(gate(), decision)
            raise RouteError('GW_AGENT_MODEL_ERROR_HTTP_429',
                             facts={'gateway_code': 'HTTP_429'})

        def unexpected_runner(_route, _request, **_kwargs):
            raise RuntimeError('unexpected route failure')
        unexpected_store = Store()
        self.addCleanup(unexpected_store._temp.cleanup)
        with patch.object(task_completion, 'validate_task_binding'), \
             patch.object(task_completion, 'bind_executor_task',
                          return_value=(registration, snapshot)), \
             patch.object(task_submission_cli, '_delegate_d6_binding',
                          return_value=(registration, snapshot)), \
             patch.object(task_submission_cli, '_assignment_paths',
                          return_value=(['src.py'], ['src.py'])), \
             patch.object(task_submission_cli, '_remote_receipt_test_policy', return_value='REMOTE_RECEIPT'), \
             patch.object(task_submission_cli, '_direct_result_callback', return_value={}), \
             patch.object(task_submission_cli, 'load_route_config',
                          return_value={'routes': {'gw-agent': {
                              'allowed_data_classes': ['synthetic', 'public_repo'],
                              'profile': {'adapter': 'openai-chat-tools',
                                  'base_url': 'https://api.openai.com/v1',
                                  'model': 'gw-agent-default',
                                  'context_cap_tokens': 32768,
                                  'max_output_tokens': 8192,
                                  'allowed_data_classes': ['synthetic', 'public_repo']},
                              'default_model': 'gw-agent-default', 'default_effort': 'low'}}}), \
             patch.object(task_submission_cli, 'resolve_profile_config',
                          return_value={'generation': {'max_tokens': 49152}}), \
             patch.object(task_submission_cli, 'selected_provider', return_value={
                 'type': 'openai-compatible', 'model': 'authorized-model',
                 'base_url': 'https://authorized.example'}), \
             patch.object(worker_dispatch, 'authorize_dispatch'), \
             patch.object(worker_dispatch, 'evaluate', return_value=decision), \
             patch.object(task_submission_cli, 'GwAgentRoute', return_value=route), \
             patch.object(task_submission_cli, 'for_platform', return_value=object()), \
             patch.object(task_submission_cli, 'run_task_direct_result_event') as event, \
             patch.object(task_submission_cli, '_task_view', return_value={}):
            status = task_submission_cli.run_task_gw_agent_delegate(
                args, store=store, route_runner=fail_runner)
            unexpected_status = task_submission_cli.run_task_gw_agent_delegate(
                args, store=unexpected_store, route_runner=unexpected_runner)
        self.assertEqual(status, 1)
        self.assertEqual(store.value['developer']['state'], 'DEVELOPER_UNAVAILABLE')
        self.assertEqual(store.value['developer']['error_code'],
                         'DEVELOPER_GW_AGENT_MODEL_ERROR_HTTP_429')
        event.assert_not_called()

        self.assertEqual(unexpected_status, 1)
        self.assertEqual(unexpected_store.value['developer']['state'],
                         'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(unexpected_store.value['developer']['error_code'],
                         'DEVELOPER_RESULT_UNKNOWN')

    def test_gw_agent_failure_states_persist_in_real_task_store(self):
        task_id, run_id = 'a' * 32, 'b' * 32
        task_contract_data = {
            'schema_version': 1, 'contract_version': 1, 'task_id': task_id,
            'goal': 'Run the bounded gw-agent task.', 'authorized_scope': ['pm-planning'],
            'forbidden_scope': [], 'criteria': [{'criterion_id': 'result',
                'description': 'Record the result.', 'kind': 'ARTIFACT'}],
            'previous_digest': None,
        }
        contract = task_store.parse_task_contract(json.dumps(task_contract_data))
        binding = vars(contract.binding(run_id))
        task = {'task_id': task_id, 'run_id': run_id, 'task_binding': binding}
        registration = SimpleNamespace(task_id='executor-d6', root='/repo/worktree',
            common_dir='/repo/.git', branch='task-branch', trusted_agent_id='codex',
            session_ref='session-ref', native_session_id=None)
        developer = {
            'assignment_id': 'd' * 32, 'selection_source': 'PM',
            'state': 'DEVELOPER_DISPATCHING', 'provider_profile': 'gw-agent',
            'provider_type': 'gw-agent-cli', 'model': 'gw-agent-default',
            'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
            'session_id': 'dev-' + 'e' * 32, 'source_sha': 'f' * 40,
            'source_sha_binding': 'D6_REGISTRY_OBSERVED',
            'read_paths': ['src.py'], 'write_paths': ['src.py'],
            'acceptance': ['Record the result.'], 'context_sha256': '1' * 64,
            'egress_basis': 'Synthetic test context.', 'input_bytes': 128,
            'max_tokens': None, 'artifact_format': 'unified_diff',
            'artifact': None, 'artifact_sha256': None, 'usage': None,
            'error_code': None, 'execution': None, 'created_at': '2026-01-01T00:00:00Z',
            'direct_result_callback': task_submission_cli._direct_result_callback(
                task, registration, 'f' * 40, test_policy='REMOTE_RECEIPT'),
        }

        def make_record():
            record = {
                'schema_version': 2, 'task_id': task_id, 'run_id': run_id,
                'pm_session_id': 'pm-test', 'pm_provider_profile': 'test-provider',
                'pm_provider_type': 'test-provider', 'pm_model': 'test-model',
                'backend_session_semantics': 'EPHEMERAL_SINGLE_CALL',
                'native_session_id': None, 'state': 'PM_RESPONDED',
                'created_at': '2026-01-01T00:00:00Z',
                'updated_at': '2026-01-01T00:00:00Z',
                'task_contract': task_contract_data, 'task_binding': binding,
                'request': 'bounded request',
                'request_sha256': hashlib.sha256(b'bounded request').hexdigest(),
                'response': 'PM response', 'usage': None, 'error_code': None,
                'developer': None,
                'pm_assignment': {'schema_version': 1, 'route': 'gw-agent', 'risk': 'LOW',
                    'goal': 'Run the bounded gw-agent task.',
                    'acceptance': ['Record the result.']},
            }
            record['record_sha256'] = task_store._digest(record)
            return record

        decision = worker_dispatch.PolicyDecision(
            'company-gw-agent', True, 'OWNER_POLICY_ALLOWED', 'f' * 64)

        def seed_dispatching_developer(store):
            current = store.get(task_id)
            dispatching = copy.deepcopy(developer)
            return store.compare_and_update(
                task_id, expected={'developer': current['developer']},
                developer=dispatching)

        for state, error_code in (
                ('DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_MODEL_ERROR'),
                ('DEVELOPER_OUTCOME_UNKNOWN', 'DEVELOPER_RESULT_UNKNOWN')):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temp:
                store = task_store.TaskStore(Path(temp).resolve() / 'tasks')
                store.create(make_record())
                with patch.object(worker_dispatch, 'evaluate', return_value=decision):
                    current = seed_dispatching_developer(store)
                    updated_developer = copy.deepcopy(current['developer'])
                    updated_developer.update(state=state, error_code=error_code)
                    saved = store.compare_and_update(
                        task_id, expected={'developer': current['developer']},
                        developer=updated_developer)
                self.assertEqual(saved['developer']['state'], state)
                self.assertEqual(saved['developer']['error_code'], error_code)

        with tempfile.TemporaryDirectory() as temp:
            store = task_store.TaskStore(Path(temp).resolve() / 'tasks')
            store.create(make_record())
            with patch.object(worker_dispatch, 'evaluate', return_value=decision):
                current = seed_dispatching_developer(store)
                blocked = copy.deepcopy(current['developer'])
                blocked.update(state='BLOCKED', error_code='DEVELOPER_GW_AGENT_MODEL_ERROR')
                # BLOCKED is reserved for AGY-specific conditions in TaskStore validation.
                with self.assertRaises(LLMToolError):
                    store.compare_and_update(task_id,
                        expected={'developer': current['developer']}, developer=blocked)

    def test_gw_agent_success_persists_with_verified_served_model(self):
        task = {'schema_version': 2, 'task_id': 'a' * 32, 'run_id': 'b' * 32,
            'state': 'PM_RESPONDED', 'response': 'PM response', 'request': 'bounded task',
            'task_binding': {'contract_digest': 'c' * 64},
            'pm_assignment': {'route': 'gw-agent', 'risk': 'LOW', 'goal': 'goal',
                              'acceptance': ['record result']},
            'developer': None, 'completion': None}

        class Store:
            def __init__(self):
                self._temp = tempfile.TemporaryDirectory(prefix='gw-agent-store-')
                self.root = Path(self._temp.name) / 'tasks'
                self.value = copy.deepcopy(task)

            def get(self, _task_id):
                return copy.deepcopy(self.value)

            def compare_and_update(self, _task_id, *, expected, **updates):
                for key, value in expected.items():
                    if self.value.get(key) != value:
                        raise task_store.TaskStoreConflict('expected value changed')
                denial = worker_dispatch.authorize_task_store_change(
                    self.value, updates, expected)
                if denial is not None:
                    raise denial
                self.value.update(copy.deepcopy(updates))
                return copy.deepcopy(self.value)

            def d6_bind_compare_and_update(self, task_id, *, executor_task_id,
                                           expected, **updates):
                return self.compare_and_update(task_id, expected=expected, **updates)

        registration = SimpleNamespace(task_id='executor-d6', root=str(Path.cwd()),
            common_dir='/repo/.git', branch='task-branch', trusted_agent_id='codex',
            session_ref='session-ref', native_session_id=None)
        snapshot = SimpleNamespace(head='d' * 40, root=str(Path.cwd()),
            common_dir='/repo/.git', branch='task-branch')
        args = Namespace(task_id='a' * 32, mode='direct', context_stdin=False,
            data_class='SYNTHETIC', egress_basis='Synthetic test context.',
            acceptance=['record result'], selection_source='PM', executor_task='executor-d6',
            read_path=['src.py'], write_path=['src.py'])
        store = Store()
        self.addCleanup(store._temp.cleanup)
        gateway_package = Path(tempfile.mkdtemp(prefix='gw-agent-gateway-'))
        (gateway_package / 'api_agent_gateway').mkdir()
        (gateway_package / 'api_agent_gateway' / '__init__.py').write_text('', encoding='utf-8')
        self.addCleanup(lambda: __import__('shutil').rmtree(gateway_package, ignore_errors=True))
        config_home = Path(tempfile.mkdtemp(prefix='gw-agent-config-'))
        self.addCleanup(lambda: __import__('shutil').rmtree(config_home, ignore_errors=True))
        config_path = config_home / '.config' / 'llmtool' / 'config.yaml'
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            'active_provider: company-gw\n'
            'generation:\n'
            '  max_tokens: 49152\n'
            'providers:\n'
            '  company-gw:\n'
            '    type: openai-compatible\n'
            '    base_url: http://10.20.30.40:8080\n'
            '    model: authorized-model\n',
            encoding='utf-8')
        result = SimpleNamespace(model='authorized-model', usage={
            'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 3},
            session_id='provider-session', exit_code=0, stdout_sha256='f' * 64,
            elapsed_ms=10, cleanup_status='CLEAN')
        decision = worker_dispatch.PolicyDecision('company-gw-agent', True,
                                                  'OWNER_POLICY_ALLOWED', 'f' * 64)
        gateway_profile = dict(load_route_config()['routes']['gw-agent']['profile'])
        gateway_profile.pop('data_class_map', None)

        seen = {}
        def make_route(config):
            seen['profile'] = copy.deepcopy(config['profile'])
            seen['route_model'] = config['default_model']
            config['package_path'] = str(gateway_package)
            seen['route'] = GwAgentRoute(config)
            return seen['route']

        def run_route(_route, request, *, gate, **_kwargs):
            seen['runner_calls'] = seen.get('runner_calls', 0) + 1
            self.assertIs(gate(), decision)
            _route.audit_copy_path = '/tmp/gw-agent-audit'
            _route._result = {'audit_sha256': 'e' * 64}
            seen['task'] = copy.deepcopy(request['task'])
            _route.validate_request(request)
            with tempfile.TemporaryDirectory() as temp:
                with patch.object(_route, '_load_credential', return_value=None):
                    _route._prepare_files(temp, request)
                prepared_task = json.loads((Path(temp) / 'task.json').read_text())
                prepared_profile = json.loads((Path(temp) / 'profiles.json').read_text())
                seen['prepared_task'] = prepared_task
                seen['prepared_profile'] = prepared_profile['profiles']['llmtool-direct']
            gateway_path = str(Path(__file__).resolve().parents[2] / 'api-agent-gateway')
            sys.path.insert(0, gateway_path)
            try:
                from api_agent_gateway.adapters import OpenAIChatTools
                from api_agent_gateway.contract import parse_profile, parse_task
                adapter = OpenAIChatTools(
                    SimpleNamespace(base_url=seen['prepared_profile']['base_url']), None)
                seen['post_path'] = adapter._path
                serialized = json.dumps(seen['prepared_task'], sort_keys=True,
                                        separators=(',', ':')).encode('utf-8')
                seen['parsed_task'] = parse_task(seen['prepared_task'], serialized)
                parse_profile({'schema_version': 1, 'profiles': {
                    'llmtool-direct': seen['prepared_profile']}},
                    'llmtool-direct')
            finally:
                sys.path.remove(gateway_path)
            return result

        with patch.object(task_completion, 'validate_task_binding'), \
             patch.object(task_completion, 'bind_executor_task',
                          return_value=(registration, snapshot)), \
             patch.object(task_submission_cli, '_delegate_d6_binding',
                          return_value=(registration, snapshot)), \
             patch.object(task_submission_cli, '_assignment_paths',
                          return_value=(['src.py'], ['src.py'])), \
             patch.object(task_submission_cli, '_remote_receipt_test_policy', return_value='REMOTE_RECEIPT'), \
             patch.object(task_submission_cli, '_direct_result_callback', return_value={}), \
             patch.object(task_submission_cli, 'load_route_config',
                          return_value={'routes': {'gw-agent': {
                              'allowed_data_classes': ['synthetic', 'public_repo'],
                              'profile': gateway_profile,
                              'profile_name': 'llmtool-direct',
                              'authorization_ref': 'pm-task-ref',
                              'default_model': 'gw-agent-default', 'default_effort': 'low'}}}), \
             patch.dict(os.environ, {'HOME': str(config_home)}), \
             patch.object(task_submission_cli, '_gw_agent_endpoint', return_value=({
                 'type': 'openai-compatible', 'model': 'authorized-model',
                 'base_url': 'http://10.20.30.40:8080'}, {'max_tokens': 49152})), \
             patch.object(worker_dispatch, 'authorize_dispatch'), \
             patch.object(worker_dispatch, 'evaluate', return_value=decision), \
             patch.object(task_submission_cli, 'GwAgentRoute', side_effect=make_route) as constructor, \
             patch.object(task_submission_cli, 'for_platform', return_value=object()), \
             patch.object(task_submission_cli, 'capture_snapshot'), \
             patch.object(task_submission_cli, 'cleanup_audit_copies'), \
             patch.object(task_completion, 'read_d6_registration', return_value=registration), \
             patch.object(task_completion, 'collect_git_snapshot', return_value=snapshot), \
             patch.object(task_completion, '_worktree_diff', return_value=(['src.py'], b'diff')), \
             patch.object(task_submission_cli, 'run_task_direct_result_event', return_value=0), \
             patch.object(task_submission_cli, '_task_view', return_value={}):
            status = task_submission_cli.run_task_gw_agent_delegate(
                args, store=store, route_runner=run_route)

        constructor.assert_called_once()
        self.assertEqual(seen['runner_calls'], 1)
        self.assertEqual(status, 0)
        developer = store.value['developer']
        self.assertEqual(developer['state'], 'DEVELOPER_RESPONDED')
        self.assertEqual(developer['model'], 'authorized-model')
        self.assertEqual(seen['route_model'], 'authorized-model')
        self.assertEqual(seen['profile']['model'], 'authorized-model')
        self.assertEqual(seen['profile']['base_url'], 'http://10.20.30.40:8080/v1')
        self.assertEqual(seen['profile']['max_output_tokens'], 16384)
        self.assertEqual(seen['profile']['http_allow_hosts'], ['10.20.30.40'])
        self.assertEqual(developer['artifact'], 'diff')
        self.assertEqual(developer['usage']['served_model'], 'authorized-model')
        self.assertEqual(seen['task']['schema_version'], 1)
        self.assertEqual(seen['task']['goal'], 'goal')
        self.assertEqual(seen['task']['acceptance'], ['record result', task_submission_cli.TEST_DATA_RULE])
        self.assertEqual(seen['task']['data_class'], 'synthetic')
        self.assertEqual(seen['prepared_task']['data_class'], 'synthetic')
        self.assertEqual(seen['prepared_profile']['base_url'],
                         'http://10.20.30.40:8080/v1')
        self.assertEqual(seen['post_path'], '/v1/chat/completions')
        self.assertEqual(seen['prepared_profile']['http_allow_hosts'], ['10.20.30.40'])
        self.assertEqual(set(seen['task']['budgets']), {
            'max_turns', 'max_input_tokens', 'max_output_tokens', 'max_wall_seconds',
            'max_tool_result_bytes', 'max_sent_bytes'})
        self.assertNotIn('stdout_bytes', seen['task']['budgets'])
        # Per-Task budgets scale with the route caps (#120): 8 x 32768 and 4 x 16384.
        self.assertEqual(seen['task']['budgets']['max_input_tokens'], 262144)
        self.assertEqual(seen['task']['budgets']['max_output_tokens'], 65536)
        self.assertEqual(seen['task']['budgets']['max_turns'], 16)
        self.assertEqual(developer['usage']['prompt_tokens'], 1)
        self.assertEqual(developer['usage']['completion_tokens'], 2)
        self.assertEqual(developer['execution']['usage_source'], 'CLI_REPORTED')

    def test_old_audit_copies_are_removed_and_recent_copies_retained(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / 'llmtool-gw-agent-audit-old'
            old.mkdir()
            (old / 'audit.jsonl').write_text('{}\n', encoding='utf-8')
            os.utime(old, (1, 1))
            recent = root / 'llmtool-gw-agent-audit-recent'
            recent.mkdir()
            (recent / 'audit.jsonl').write_text('{}\n', encoding='utf-8')
            removed = cleanup_audit_copies(now=40 * 24 * 60 * 60, temp_root=root)
            self.assertEqual(removed, [old.name])
            self.assertFalse(old.exists())
            self.assertTrue(recent.exists())



class _NoSandboxBoundary:
    """The OS sandbox is proven by the real-sandbox route test; this runs on any host."""

    def prepare(self, root, write_paths, state_dirs=()):
        return [], 'a' * 64

    def verify(self, root, write_paths, state_dirs=(), *, runner):
        return {'verified': True, 'policy_sha256': 'a' * 64, 'probes': []}


# #1072: sessions in the format TaskStore accepts for a DONE gw-agent run.
_BIND_SESSION = '0123456789abcdef-0123456789abcdef'
_EARLIER_SESSION = 'fedcba9876543210-fedcba9876543210'
_OTHER_SESSION = 'abcdefabcdefabcd-0000000000000000'
_REVIEWER_SESSION = '1111222233334444-5555666677778888'


class GwAgentRealStoreTests(unittest.TestCase):
    """Real TaskStore, D6 record, gateway process and result event; fake model only."""

    def setUp(self):
        review_trust_fixture.install_for_case(self)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        scratch = self.base / 'tmp'
        scratch.mkdir(mode=0o700)
        for patcher in (patch.dict(os.environ, {'HOME': str(self.base)}),
                        patch.object(tempfile, 'tempdir', str(scratch))):
            patcher.start()
            self.addCleanup(patcher.stop)
        gateway_tests = Path(__file__).resolve().parents[2] / 'api-agent-gateway' / 'tests'
        sys.path.insert(0, str(gateway_tests))
        try:
            from helpers import FakeServer, reply, tool_call
        finally:
            sys.path.remove(str(gateway_tests))
        self.reply, self.tool_call = reply, tool_call
        self.server = FakeServer()
        self.addCleanup(self.server.close)
        config = self.base / '.config' / 'llmtool' / 'config.yaml'
        config.parent.mkdir(parents=True)
        config.write_text(
            'active_provider: company-gw\n'
            'generation:\n  max_tokens: 49152\n'
            'providers:\n  company-gw:\n    type: openai-compatible\n'
            f'    base_url: http://127.0.0.1:{self.server.port}\n    model: gw\n',
            encoding='utf-8')
        gateway_file = config.parent / 'gateway-profiles.json'
        gateway_file.write_text(json.dumps({'schema_version': 1, 'default': 'home', 'profiles': {
            'home': {'base_url': f'http://127.0.0.1:{self.server.port}', 'model': 'gw',
                    'api_key_env': 'GW_AGENT_TEST_KEY_ENV', 'context_cap_tokens': 32768,
                    'max_output_tokens': 49152}}}), encoding='utf-8')
        gateway_file.chmod(0o600)
        key_env = patch.dict(os.environ, {'GW_AGENT_TEST_KEY_ENV': 'synthetic-value'})
        key_env.start()
        self.addCleanup(key_env.stop)
        self.root = self.base / 'd6'
        (self.root / 'tools' / 'llmtool').mkdir(parents=True)
        self._git('init', '-b', 'task-branch')
        self._git('config', 'user.name', 'Test')
        self._git('config', 'user.email', 'test@example.invalid')
        (self.root / 'tools' / 'llmtool' / '.gitignore').write_text(
            '/.d6-bootstrap-report.json\n', encoding='utf-8')
        (self.root / 'README.md').write_text('fixture\n', encoding='utf-8')
        self._git('add', 'README.md', 'tools/llmtool/.gitignore')
        self._git('commit', '-m', 'source')
        self.head = self._git('rev-parse', 'HEAD')
        self.common = str(self.root / '.git')
        self._register_d6()
        self.store_root = Path(temp.name).resolve() / 'tasks'
        self.store = task_store.TaskStore(self.store_root)
        self._store_serial = 0

    def _git(self, *args):
        return subprocess.check_output(['/usr/bin/git', '-C', str(self.root), *args],
                                       text=True, stderr=subprocess.DEVNULL).strip()

    def _register_d6(self):
        self.root.chmod(0o700)
        info = self.root.stat()
        report = (json.dumps({'schema_version': 1, 'result': 'PASS', 'status': 'env_reused',
            'task_id': 'executor-d6', 'root_realpath': str(self.root), 'head_sha': self.head,
            'tooling_sha': self.head,
            'cache_fingerprint': 'cpython3.13-darwin-arm64-012345abcdef'},
            sort_keys=True, separators=(',', ':')) + '\n').encode()
        report_path = self.root / 'tools' / 'llmtool' / '.d6-bootstrap-report.json'
        report_path.write_bytes(report)
        report_path.chmod(0o600)
        record = {'schema_version': 1, 'task_id': 'executor-d6',
            'trusted_agent_id': 'gw-agent-developer', 'session_ref': 'gw agent D6 session',
            'root_realpath': str(self.root),
            'physical_identity': {'device': info.st_dev, 'inode': info.st_ino,
                                  'uid': info.st_uid, 'mode': oct(stat.S_IMODE(info.st_mode))},
            'git_common_dir': self.common, 'branch': 'task-branch', 'base_sha': self.head,
            'head_sha': self.head, 'state': 'ACTIVE', 'lock_state': 'LOCKED',
            'env2_pass_marker': {'schema_version': 1, 'result': 'PASS',
                'task_id': 'executor-d6', 'root_realpath': str(self.root),
                'submitted_head_sha': self.head, 'current_head_sha': self.head,
                'tooling_sha': self.head,
                'environment_fingerprint': 'cpython3.13-darwin-arm64-012345abcdef',
                'report_sha256': hashlib.sha256(report).hexdigest()}}
        registry = Path(self.common) / 'llmtool-worktrees'
        registry.mkdir(mode=0o700)
        path = registry / 'executor-d6.json'
        path.write_text(json.dumps(record), encoding='utf-8')
        path.chmod(0o600)

    def _active_pm_task(self, target):
        task_id, run_id = task_store.new_id(), task_store.new_id()
        prompt = 'Create one synthetic file.'
        digest = hashlib.sha256(prompt.encode()).hexdigest()
        record = task_submission_cli._record(
            task_id, run_id, 'pm-' + task_store.new_id(), 'active-pm-runtime', prompt,
            state='PM_RESPONDED', provider_type='active-pm-runtime', model=None,
            backend='ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL', request_sha=digest)
        assignment = {'route': 'gw-agent', 'risk': 'LOW', 'goal': 'Create one synthetic file.',
                      'acceptance': [f'Only {target} is created.']}
        record.update(
            pm_route_binding={'type': 'active-pm-runtime', 'model': None, 'base_url': None},
            response=json.dumps({'schema_version': 1, 'summary': 'bounded',
                                 'developer_assignment': assignment}),
            usage=None, error_code=None,
            pm_assignment={'schema_version': 1, **assignment},
            pm_provenance={'source': 'ACTIVE_PM_RUNTIME', 'provider_invoked': False,
                           'native_session_id_status': 'UNAVAILABLE',
                           'usage_status': 'UNKNOWN', 'session_restored': False,
                           'automatic_callback': False})
        self.store.create(record)
        return task_id

    def _delegate(self, target, *, usage=(1026, 49), served_model='qwen3.8-27b',
                  provider_model='gw', reject_responded=False, route_runner=None,
                  wrap_store=None, gate=None, authorize=None, route_factory=None,
                  content='hello synthetic\n', bind=None, repair_packet=None,
                  data_class='SYNTHETIC', egress_basis='Synthetic test content only.'):
        # #1072: without ``bind`` the D6 session step keeps today's unbound
        # result; with it the real step runs against that fake lifecycle helper.
        task_id = self.task_id = self._active_pm_task(target)
        if provider_model != 'gw':
            config = self.base / '.config' / 'llmtool' / 'config.yaml'
            config.write_text(
                'active_provider: company-gw\n'
                'generation:\n  max_tokens: 49152\n'
                'providers:\n  company-gw:\n    type: openai-compatible\n'
                f'    base_url: http://127.0.0.1:{self.server.port}\n'
                f'    model: {provider_model}\n',
                encoding='utf-8')
            gateway_file = config.parent / 'gateway-profiles.json'
            profiles = json.loads(gateway_file.read_text(encoding='utf-8'))
            profiles['profiles']['home']['model'] = provider_model
            gateway_file.write_text(json.dumps(profiles), encoding='utf-8')
        self.server.script.extend((
            self.reply(self.tool_call('apply_edit', path=target, new=content,
                                      create=True), model=served_model, usage=usage),
            self.reply(self.tool_call('finish', summary='done'), model=served_model,
                       usage=usage)))
        args = Namespace(task_id=task_id, mode='direct', context_stdin=False,
            data_class=data_class, egress_basis=egress_basis,
            acceptance=[f'Only {target} is created.'], selection_source='PM',
            executor_task='executor-d6', read_path=[target], write_path=[target],
            cwd=str(self.root))
        if repair_packet is not None:
            args.repair_packet = repair_packet
        real_config = load_route_config()
        real_config['routes']['gw-agent']['package_path'] = str(
            Path(__file__).resolve().parents[2] / 'api-agent-gateway')
        decision = worker_dispatch.PolicyDecision('company-gw-agent', True,
                                                  'OWNER_POLICY_ALLOWED', 'f' * 64)
        store = self.store
        if reject_responded:
            base_store = self.store

            class _RejectRespondedStore:
                def __getattr__(self, name):
                    return getattr(base_store, name)

                def compare_and_update(self, task_id, *, expected, **updates):
                    developer_update = updates.get('developer')
                    if (type(developer_update) is dict and
                            developer_update.get('state') == 'DEVELOPER_RESPONDED'):
                        raise task_store.TaskStoreConflict(
                            'injected RESPONDED write rejection')
                    return base_store.compare_and_update(
                        task_id, expected=expected, **updates)

            store = _RejectRespondedStore()
        if wrap_store is not None:
            store = wrap_store(self.store)
        with patch.object(task_submission_cli, 'load_route_config',
                          return_value=real_config), \
             patch.object(task_submission_cli, 'for_platform',
                          return_value=_NoSandboxBoundary()), \
             patch.object(worker_dispatch, 'authorize_dispatch', authorize or Mock()), \
             patch.object(worker_dispatch, 'evaluate', return_value=gate or decision), \
             patch.object(task_submission_cli, 'GwAgentRoute',
                          route_factory or task_submission_cli.GwAgentRoute), \
             (patch.object(task_submission_cli, '_bind_direct_native_session',
                           return_value=False) if bind is None else
              patch.object(task_submission_cli, '_bind_codex_native_session',
                           side_effect=bind)), \
             redirect_stdout(io.StringIO()):
            code = task_submission_cli.run_task_gw_agent_delegate(
                args, store=store, route_runner=route_runner)
        # A fresh store proves the record is on disk and still passes validation.
        return code, self._reloaded()

    def _fresh_store(self):
        # An unconfirmed Task holds its D6; each subtest that leaves one behind
        # gets its own store so it cannot block the next subtest.
        self._store_serial += 1
        parent = self.base / f'store-{self._store_serial}'
        parent.mkdir(mode=0o700)
        self.store_root = parent / 'tasks'
        self.store = task_store.TaskStore(self.store_root)

    def _reloaded(self):
        return task_store.TaskStore(self.store_root).get(self.task_id)

    def _release_d6(self):
        """Abandon the last delegated Task, as the PM must before reusing its D6.

        A run that ends DEVELOPER_OUTCOME_UNKNOWN without confirmed cleanup
        stays the live holder of ``executor-d6``. Subtests that delegate again
        on the same D6 release it first instead of relying on a looser store.
        """
        if getattr(self, 'task_id', None) is None:
            return
        stranded = self._reloaded()
        task_store.TaskStore(self.store_root).abandon(
            self.task_id, pm_session_id=stranded['pm_session_id'],
            reason='Synthetic subtest finished with the D6.')

    def _assert_success_persisted(self, task, target):
        developer = task['developer']
        self.assertEqual(developer['state'], 'DEVELOPER_RESPONDED')
        self.assertIsNone(developer['error_code'])
        self.assertEqual(developer['changed_files'], [target])
        self.assertEqual(developer['model'], 'qwen3.8-27b')
        self.assertEqual(developer['usage'], {
            'prompt_tokens': 2052, 'completion_tokens': 98, 'total_tokens': 2150,
            'served_model': 'qwen3.8-27b'})
        self.assertEqual(developer['execution']['status'], 'DONE')
        self.assertEqual(developer['execution']['usage_source'], 'CLI_REPORTED')
        self.assertIn('+hello synthetic\n', developer['artifact'])
        self.assertEqual((self.root / target).read_text(), 'hello synthetic\n')
        self.assertEqual(self._git('status', '--porcelain'), f'?? {target}')

    def test_repair_run_resumes_bound_gateway_session(self):
        self._delegate("gw-agent-first.md")
        first_task = self.task_id
        root_record_before = self.store.get(first_task)
        home = Path(self.store_root).parent / 'gw-agent-sessions' / 'executor-d6'
        from llmtool.developer_routes import gw_agent
        binding = gw_agent.read_session_binding(home, executor_task_id='executor-d6')
        self.assertEqual(binding['root_task_id'], first_task)
        initial_scope_digest = binding['scope_digest']
        (self.root / 'gw-agent-first.md').unlink()

        root_record = self.store.get(first_task)
        self.assertNotIn(task_status.derive_state(root_record), ('ABANDONED', 'COMPLETED'))
        binding_before = gw_agent.read_session_binding(home, executor_task_id='executor-d6')
        self.assertEqual(binding_before['root_task_id'], first_task)

        seen = {}

        def runner(route, request, **kwargs):
            seen.update(request)
            raise RouteError(
                'GW_AGENT_MODEL_ERROR_HTTP_429',
                facts={'gateway_code': 'HTTP_429'})

        with patch('llmtool.gw_agent_sessions.sweep_quietly',
                   wraps=gw_agent_sessions.sweep_quietly) as sweep_spy, \
             patch.object(task_submission_cli, '_validate_repair_packet',
                          side_effect=lambda value: value):
            _code, repair_task_record = self._delegate(
                'gw-agent-first.md', route_runner=runner,
                repair_packet={'reviewer_text_excerpt_untrusted_data':
                               'synthetic finding'})

        sweep_spy.assert_called_with(self.store)
        self.assertTrue(home.exists())
        binding_after = gw_agent.read_session_binding(home, executor_task_id='executor-d6')
        self.assertEqual(binding_after['session_id'], binding['session_id'])
        self.assertEqual(binding_after['scope_digest'], initial_scope_digest)
        self.assertEqual(seen['session_id'], binding['session_id'])
        self.assertEqual(seen['task']['task_id'], first_task)
        self.assertEqual(seen['task']['findings'], ['synthetic finding'])

        repair_task = self.task_id
        self.assertNotEqual(repair_task, first_task)
        self.assertEqual(repair_task_record['task_id'], repair_task)
        self.assertEqual(self.store.get(first_task), root_record_before)
        self._release_d6()

    def test_abandoned_task_session_is_cleared_by_sweep(self):
        target = 'gw-agent-sweep-abandoned.md'
        self._delegate(target)
        first_task = self.task_id
        home = Path(self.store_root).parent / 'gw-agent-sessions' / 'executor-d6'
        self.assertTrue(home.exists())
        from llmtool.developer_routes import gw_agent
        binding = gw_agent.read_session_binding(home, executor_task_id='executor-d6')
        self.assertEqual(binding['root_task_id'], first_task)
        (self.root / target).unlink()
        self._release_d6()
        reloaded_root = self.store.get(first_task)
        self.assertEqual(task_status.derive_state(reloaded_root), 'ABANDONED')
        gw_agent_sessions.sweep_quietly(self.store)
        self.assertFalse(home.exists())

    def test_docs_success_persists_and_reaches_the_active_pm(self):
        target = 'gw-agent-synthetic.md'
        code, task = self._delegate(target)
        self._assert_success_persisted(task, target)
        callback = task['developer']['direct_result_callback']
        self.assertEqual((code, callback['state'], callback['missing_gates']),
                         (0, 'PM_EVIDENCE_READY', []))
        self.assertEqual(callback['evidence']['verification_state'], 'READY')

    def test_public_repo_data_class_succeeds_on_real_store(self):
        target = 'gw-agent-public-repo.md'
        code, task = self._delegate(
            target, data_class='PUBLIC_REPO',
            egress_basis='Public repository test context.')
        self.assertEqual(code, 0)
        self.assertEqual(task['developer']['state'], 'DEVELOPER_RESPONDED')

    def test_docs_success_with_unknown_usage_persists_served_model(self):
        target = 'gw-agent-unknown-usage.md'
        code, task = self._delegate(target, usage=(None, None))
        developer = task['developer']
        self.assertEqual(code, 0)
        self.assertEqual(developer['state'], 'DEVELOPER_RESPONDED')
        self.assertEqual(developer['model'], 'qwen3.8-27b')
        self.assertIsNone(developer['usage'])
        self.assertEqual(developer['execution']['usage_source'], 'UNKNOWN')
        persisted = task_store.TaskStore(Path(self.base / 'tasks').resolve()).get(task['task_id'])
        self.assertEqual(persisted['developer']['model'], 'qwen3.8-27b')

    def test_docs_success_with_partial_usage_persists_served_model(self):
        target = 'gw-agent-partial-usage.md'
        code, task = self._delegate(target, usage=(1026, None))
        developer = task['developer']
        self.assertEqual(code, 0)
        self.assertEqual(developer['state'], 'DEVELOPER_RESPONDED')
        self.assertEqual(developer['model'], 'qwen3.8-27b')
        self.assertIsNone(developer['usage'])
        self.assertEqual(developer['execution']['usage_source'], 'UNKNOWN')
        persisted = task_store.TaskStore(Path(self.base / 'tasks').resolve()).get(task['task_id'])
        self.assertEqual(persisted['developer']['model'], 'qwen3.8-27b')

    def test_rejected_responded_write_clears_reported_usage_and_artifact(self):
        target = 'gw-agent-rejected-responded.md'
        with patch.object(task_submission_cli, 'run_task_direct_result_event') as callback:
            code, task = self._delegate(target, reject_responded=True)

        callback.assert_not_called()
        developer = task['developer']
        self.assertEqual(code, 1)
        self.assertEqual(developer['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(developer['error_code'], 'DEVELOPER_RESULT_PERSISTENCE_REJECTED')
        self.assertIsNone(developer['usage'])
        self.assertIsNone(developer['artifact'])
        self.assertIsNone(developer['artifact_sha256'])
        self.assertEqual(developer['execution']['usage_source'], 'UNKNOWN')
        self.assertEqual(developer['execution']['status'], 'UNKNOWN')
        self.assertEqual(developer['execution']['cleanup_status'], 'CONFIRMED')
        self.assertEqual(developer['changed_files'], [target])
        self.assertEqual(developer['model'], 'gw')

    def test_unknown_usage_control_persists_without_usage(self):
        target = 'gw-agent-unknown-usage-control.md'
        code, task = self._delegate(target, usage=(None, None))

        developer = task['developer']
        self.assertEqual(code, 0)
        self.assertEqual(developer['state'], 'DEVELOPER_RESPONDED')
        self.assertIsNone(developer['usage'])
        self.assertEqual(developer['execution']['usage_source'], 'UNKNOWN')
        self.assertEqual(developer['model'], 'qwen3.8-27b')

    def test_matching_model_control_persists_served_model(self):
        target = 'gw-agent-matching-model-control.md'
        code, task = self._delegate(target, provider_model='qwen3.8-27b')

        developer = task['developer']
        self.assertEqual(code, 0)
        self.assertEqual(developer['state'], 'DEVELOPER_RESPONDED')
        self.assertEqual(developer['model'], 'qwen3.8-27b')
        self.assertEqual(developer['usage']['served_model'], 'qwen3.8-27b')
        self.assertEqual(developer['execution']['usage_source'], 'CLI_REPORTED')

    def test_untestable_file_persists_but_waits_with_a_named_gate(self):
        target = 'gw-agent-synthetic.txt'
        code, task = self._delegate(target)
        self._assert_success_persisted(task, target)
        callback = task['developer']['direct_result_callback']
        self.assertEqual((code, callback['state'], callback['missing_gates']),
                         (1, 'WAITING_EXECUTOR', ['TEST_SCOPE_MISSING']))

    def _assert_failed(self, task, state, error_code, *, provider_type='gw-agent-cli',
                       model='gw'):
        developer = task['developer']
        self.assertEqual((developer['state'], developer['error_code']), (state, error_code))
        self.assertEqual((developer['provider_type'], developer['model']),
                         (provider_type, model))
        self.assertIsNone(developer['artifact'])
        self.assertIsNone(developer['artifact_sha256'])
        self.assertIsNone(developer['usage'])

    @staticmethod
    def _route_result_with(**fault):
        def runner(route, request, **kwargs):
            return dataclasses.replace(
                task_submission_cli.run_route(route, request, **kwargs), **fault)
        return runner

    @staticmethod
    def _unused_runner(*_args, **_kwargs):
        raise AssertionError('the provider must not start')

    @staticmethod
    def _store_faulting(*, state, landed=False, error=LLMToolError):
        """Fail one developer state's write, before or after it reaches the disk."""
        def wrap(base):
            class FaultingStore:
                fired = False

                def __getattr__(self, name):
                    return getattr(base, name)

                def compare_and_update(self, task_id, *, expected, **updates):
                    if self.fired or (updates.get('developer') or {}).get('state') != state:
                        return base.compare_and_update(task_id, expected=expected, **updates)
                    self.fired = True
                    if landed:
                        base.compare_and_update(task_id, expected=expected, **updates)
                    raise error('injected write failure')
            return FaultingStore()
        return wrap

    def test_store_rejecting_a_sensitive_artifact_never_strands_the_task(self):
        target = 'gw-agent-sensitive.md'
        with patch.object(task_submission_cli, 'run_task_direct_result_event') as callback:
            code, task = self._delegate(target, content='password = synthetic-value-123456\n')
        callback.assert_not_called()
        self.assertEqual(code, 1)
        self._assert_failed(task, 'DEVELOPER_OUTCOME_UNKNOWN',
                            'DEVELOPER_RESULT_PERSISTENCE_REJECTED')
        self.assertEqual(task['developer']['execution']['usage_source'], 'UNKNOWN')

    def test_routing_alias_is_never_recorded_as_the_served_model(self):
        code, task = self._delegate('gw-agent-alias.md', served_model='gw')
        self.assertEqual(code, 1)
        self._assert_failed(task, 'DEVELOPER_UNAVAILABLE',
                            'DEVELOPER_GW_AGENT_MODEL_UNVERIFIED')

    def test_rejected_execution_block_never_strands_the_task(self):
        for name, fault, kept in (
                ('unconfirmed-cleanup', {'cleanup_status': 'UNKNOWN'},
                 {'cleanup_status': 'UNKNOWN'}),
                ('no-session', {'session_id': None}, {'native_session_id': None}),
                ('bad-session', {'session_id': 'not a session'}, None),
                ('bad-cleanup', {'cleanup_status': 'LEFT_RUNNING'}, None),
                ('bad-exit-code', {'exit_code': 999}, None),
                ('bad-stdout-digest', {'stdout_sha256': 'not-a-digest'}, None)):
            with self.subTest(fault=name), \
                 patch.object(task_submission_cli, 'run_task_direct_result_event') as callback:
                self._fresh_store()
                target = f'gw-agent-{name}.md'
                try:
                    code, task = self._delegate(
                        target, route_runner=self._route_result_with(**fault))
                    callback.assert_not_called()
                    self.assertEqual(code, 1)
                    self._assert_failed(task, 'DEVELOPER_OUTCOME_UNKNOWN',
                                        'DEVELOPER_RESULT_PERSISTENCE_REJECTED')
                    developer = task['developer']
                    if kept is not None:
                        self.assertEqual(developer['execution']['status'], 'UNKNOWN')
                        self.assertEqual(developer['execution']['usage_source'], 'UNKNOWN')
                        for field, value in kept.items():
                            self.assertEqual(developer['execution'][field], value)
                        self.assertEqual(developer['changed_files'], [target])
                    else:
                        self.assertIsNone(developer['execution'])
                        self.assertNotIn('changed_files', developer)
                    (self.root / target).unlink()
                finally:
                    # An unknown outcome without confirmed cleanup still holds
                    # executor-d6; the next fault runs as a new Task on it.
                    self._release_d6()

    def test_owner_pause_on_the_dispatching_write_is_persisted(self):
        paused = worker_dispatch.PolicyDecision('company-gw-agent', False, 'OWNER_PAUSED',
                                                'f' * 64)
        code, task = self._delegate('gw-agent-paused.md', gate=paused,
                                    route_runner=self._unused_runner)
        self.assertEqual(code, 1)
        self._assert_failed(task, 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_OWNER_PAUSED',
                            provider_type=None, model=None)
        self.assertIsNone(task['developer']['execution'])
        self.assertEqual([(entry['action'], entry['decision'], entry['reason_code'])
                          for entry in task['dispatch_audit']],
                         [('DISPATCH', 'BLOCKED', 'OWNER_PAUSED')])

    def test_store_refusing_the_dispatching_write_is_persisted(self):
        # The store's own validation: a model name longer than it accepts.
        code, task = self._delegate('gw-agent-long-model.md', provider_model='m' * 300,
                                    route_runner=self._unused_runner)
        self.assertEqual(code, 1)
        self._assert_failed(task, 'DEVELOPER_UNAVAILABLE',
                            'DEVELOPER_DISPATCH_PERSISTENCE_REJECTED',
                            provider_type=None, model=None)

    def test_dispatching_write_conflict_is_persisted(self):
        code, task = self._delegate(
            'gw-agent-dispatch-conflict.md', route_runner=self._unused_runner,
            wrap_store=self._store_faulting(state='DEVELOPER_DISPATCHING',
                                            error=task_store.TaskStoreConflict))
        self.assertEqual(code, 1)
        self._assert_failed(task, 'DEVELOPER_UNAVAILABLE',
                            'DEVELOPER_DISPATCH_PERSISTENCE_REJECTED',
                            provider_type=None, model=None)

    def test_dispatching_write_that_landed_continues_the_run(self):
        target = 'gw-agent-dispatch-landed.md'
        code, task = self._delegate(target, wrap_store=self._store_faulting(
            state='DEVELOPER_DISPATCHING', landed=True))
        self.assertEqual(code, 0)
        self._assert_success_persisted(task, target)

    def test_responded_write_that_landed_stays_a_success(self):
        target = 'gw-agent-responded-landed.md'
        code, task = self._delegate(target, wrap_store=self._store_faulting(
            state='DEVELOPER_RESPONDED', landed=True))
        self.assertEqual(code, 0)
        self._assert_success_persisted(task, target)
        self.assertEqual(task['developer']['direct_result_callback']['state'],
                         'PM_EVIDENCE_READY')

    def test_failure_write_that_landed_reports_the_failure(self):
        def runner(*_args, **_kwargs):
            raise RouteError('GW_AGENT_TIMEOUT')
        code, task = self._delegate(
            'gw-agent-failure-landed.md', route_runner=runner,
            wrap_store=self._store_faulting(state='DEVELOPER_UNAVAILABLE', landed=True))
        self.assertEqual(code, 1)
        self._assert_failed(task, 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_TIMEOUT')

    def test_failures_after_dispatching_persist_on_a_real_store(self):
        def route_error(*_args, **_kwargs):
            raise RouteError('GW_AGENT_TIMEOUT')

        def unexpected(*_args, **_kwargs):
            raise RuntimeError('synthetic')

        for name, options, state, error_code in (
                ('adapter-gate-denied', {'authorize': Mock(
                    side_effect=worker_dispatch.WorkerDispatchDenied('OWNER_PAUSED')),
                    'route_runner': self._unused_runner},
                 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_OWNER_PAUSED'),
                ('adapter-gate-error', {'authorize': Mock(
                    side_effect=RuntimeError('synthetic')),
                    'route_runner': self._unused_runner},
                 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_DISPATCH_FAILED'),
                ('route-config', {'route_factory': Mock(
                    side_effect=RouteError('GW_AGENT_CONFIG_INVALID')),
                    'route_runner': self._unused_runner},
                 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_CONFIG_INVALID'),
                ('route-unexpected', {'route_factory': Mock(
                    side_effect=TypeError('synthetic')),
                    'route_runner': self._unused_runner},
                 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_DISPATCH_FAILED'),
                ('run-route-error', {'route_runner': route_error},
                 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_TIMEOUT'),
                ('run-unexpected', {'route_runner': unexpected},
                 'DEVELOPER_OUTCOME_UNKNOWN', 'DEVELOPER_RESULT_UNKNOWN')):
            with self.subTest(path=name):
                code, task = self._delegate(f'gw-agent-{name}.md', **options)
                self.assertEqual(code, 1)
                self._assert_failed(task, state, error_code)
                self.assertIsNone(task['developer']['execution'])

    def test_interruption_during_the_run_is_persisted_and_still_raised(self):
        for error in (KeyboardInterrupt(), CLIHostTermination(143), _Cancelled()):
            def interrupted(*_args, _error=error, **_kwargs):
                raise _error
            with self.subTest(error=type(error).__name__):
                self._fresh_store()
                try:
                    with self.assertRaises(type(error)):
                        self._delegate('gw-agent-interrupted.md', route_runner=interrupted)
                    self._assert_failed(self._reloaded(), 'DEVELOPER_OUTCOME_UNKNOWN',
                                        'DEVELOPER_INTERRUPTED')
                finally:
                    # The interrupted run may still be live, so it holds
                    # executor-d6 until the PM abandons it.
                    self._release_d6()

    def test_interrupted_run_holds_the_d6_until_the_pm_abandons_it(self):
        def interrupted(*_args, **_kwargs):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self._delegate('gw-agent-held.md', route_runner=interrupted)
        holder_id = self.task_id
        self.server.script.clear()
        with self.assertRaises(LLMToolError) as caught:
            self._delegate('gw-agent-held-next.md', route_runner=self._unused_runner)
        self.assertEqual(getattr(caught.exception, 'task_store_error_code', None),
                         'TASK_D6_ALREADY_BOUND')
        self.task_id = holder_id
        self._assert_failed(self._reloaded(), 'DEVELOPER_OUTCOME_UNKNOWN',
                            'DEVELOPER_INTERRUPTED')
        self._release_d6()
        self.server.script.clear()
        target = 'gw-agent-held-after-abandon.md'
        code, task = self._delegate(target)
        self.assertEqual(code, 0)
        self._assert_success_persisted(task, target)

    def test_repair_round_after_a_failed_run_binds_the_same_d6(self):
        def route_error(*_args, **_kwargs):
            raise RouteError('GW_AGENT_TIMEOUT')
        code, failed = self._delegate('gw-agent-repair-failed.md', route_runner=route_error)
        self.assertEqual(code, 1)
        self._assert_failed(failed, 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_TIMEOUT')
        # No abandonment: a run that ended DEVELOPER_UNAVAILABLE holds no D6.
        self.server.script.clear()
        target = 'gw-agent-repair.md'
        code, task = self._delegate(target)
        self.assertEqual(code, 0)
        self._assert_success_persisted(task, target)
        self.assertIsNone(task_store.TaskStore(Path(self.base / 'tasks').resolve()).get(
            failed['task_id']).get('abandonment'))

    def test_interruption_before_the_run_is_persisted_and_still_raised(self):
        with self.assertRaises(KeyboardInterrupt):
            self._delegate('gw-agent-interrupted-gate.md', route_runner=self._unused_runner,
                           authorize=Mock(side_effect=KeyboardInterrupt()))
        self._assert_failed(self._reloaded(), 'DEVELOPER_OUTCOME_UNKNOWN',
                            'DEVELOPER_INTERRUPTED')

    def test_interruption_during_the_responded_write_is_persisted_and_still_raised(self):
        target = 'gw-agent-interrupted-write.md'
        with self.assertRaises(KeyboardInterrupt):
            self._delegate(target, wrap_store=self._store_faulting(
                state='DEVELOPER_RESPONDED', error=KeyboardInterrupt))
        task = self._reloaded()
        self._assert_failed(task, 'DEVELOPER_OUTCOME_UNKNOWN', 'DEVELOPER_INTERRUPTED')
        self.assertEqual(task['developer']['changed_files'], [target])

    def test_interruption_with_an_unwritable_store_still_reaches_the_caller(self):
        def interrupted(*_args, **_kwargs):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self._delegate('gw-agent-interrupted-no-store.md', route_runner=interrupted,
                           wrap_store=self._store_faulting(state='DEVELOPER_OUTCOME_UNKNOWN'))
        self.assertEqual(self._reloaded()['developer']['state'], 'DEVELOPER_DISPATCHING')

    def test_unwritable_store_before_dispatching_leaves_a_task_the_pm_can_abandon(self):
        refuse_dispatching = self._store_faulting(state='DEVELOPER_DISPATCHING')
        refuse_failure = self._store_faulting(state='DEVELOPER_UNAVAILABLE')
        with self.assertRaisesRegex(LLMToolError, 'gw-agent outcome could not be persisted'):
            self._delegate('gw-agent-no-store-pending.md', route_runner=self._unused_runner,
                           wrap_store=lambda base: refuse_dispatching(refuse_failure(base)))
        stranded = self._reloaded()
        self.assertEqual(stranded['developer']['state'], 'DEVELOPER_PENDING')
        reloaded = task_store.TaskStore(Path(self.base / 'tasks').resolve())
        saved = reloaded.abandon(self.task_id, pm_session_id=stranded['pm_session_id'],
                                 reason='Synthetic store outage.')
        self.assertEqual(saved['abandonment']['reason'], 'Synthetic store outage.')

    def test_unwritable_store_leaves_a_task_the_pm_can_abandon(self):
        def runner(*_args, **_kwargs):
            raise RouteError('GW_AGENT_TIMEOUT')
        with self.assertRaisesRegex(LLMToolError, 'gw-agent outcome could not be persisted'):
            self._delegate('gw-agent-no-store.md', route_runner=runner,
                           wrap_store=self._store_faulting(state='DEVELOPER_UNAVAILABLE'))
        stranded = self._reloaded()
        self.assertEqual(stranded['developer']['state'], 'DEVELOPER_DISPATCHING')

        reloaded = task_store.TaskStore(Path(self.base / 'tasks').resolve())
        saved = reloaded.abandon(self.task_id, pm_session_id=stranded['pm_session_id'],
                                 reason='Synthetic store outage.')
        self.assertEqual(saved['abandonment']['reason'], 'Synthetic store outage.')

        self.server.script.clear()
        target = 'gw-agent-after-abandon.md'
        code, task = self._delegate(target)
        self.assertEqual(code, 0)
        self._assert_success_persisted(task, target)

    # #1072: an accepted gw-agent run binds its session to the D6 before the
    # result is saved. The D6 lifecycle helper is faked; the model is the fake
    # server, so no gateway or provider call leaves the host.

    def _d6_record_path(self):
        return Path(self.common) / 'llmtool-worktrees' / 'executor-d6.json'

    def _set_d6_session(self, value):
        path = self._d6_record_path()
        record = json.loads(path.read_text(encoding='utf-8'))
        record.pop('native_session_id', None)
        if value is not None:
            record['native_session_id'] = value
        path.write_text(json.dumps(record), encoding='utf-8')
        path.chmod(0o600)

    def _d6_session(self):
        return json.loads(self._d6_record_path().read_text(
            encoding='utf-8')).get('native_session_id')

    def _bind_run(self, target, *, session=_BIND_SESSION, real_bind=True, answer='BOUND',
                  stored=None, held=None, fault=None, route_runner=None, mutate=None,
                  **options):
        """Delegate once on a fresh store with a fake D6 lifecycle helper.

        ``mutate`` edits the run's execution evidence just before the bind
        step, to model evidence the store refuses.
        """
        self._fresh_store()
        self.server.script.clear()
        self._set_d6_session(held)
        calls, writes = [], []

        def lifecycle(_registration, value):
            calls.append(value)
            if answer == 'BOUND':
                self._set_d6_session(value if stored is None else stored)
            return answer

        def recording(base):
            class RecordingStore:
                def __getattr__(self, name):
                    return getattr(base, name)

                def compare_and_update(self, task_id, *, expected, **updates):
                    writes.append(copy.deepcopy(updates))
                    return base.compare_and_update(task_id, expected=expected, **updates)
            return RecordingStore()

        if route_runner is None:
            route_runner = self._route_result_with(session_id=session, **(fault or {}))
        real_bind_step = task_submission_cli._bind_direct_native_session

        def mutating_bind(registration, developer, completion, session_format, **kwargs):
            mutate(developer['execution'])
            return real_bind_step(registration, developer, completion, session_format,
                                  **kwargs)

        run = {'calls': calls, 'code': None, 'error': None}
        try:
            with patch.object(task_submission_cli, 'run_task_direct_result_event',
                              wraps=task_submission_cli.run_task_direct_result_event) as event, \
                 patch.object(task_completion, 'collect_executor_worktree_result',
                              wraps=task_completion.collect_executor_worktree_result) as collect, \
                 patch.object(task_submission_cli, '_bind_direct_native_session',
                              real_bind_step if mutate is None else mutating_bind):
                try:
                    run['code'], _ = self._delegate(
                        target, route_runner=route_runner, wrap_store=recording,
                        bind=lifecycle if real_bind else None, **options)
                except KeyboardInterrupt as exc:
                    run['error'] = type(exc).__name__
            run.update(
                saved=self._reloaded(), d6=self._d6_session(),
                event_calls=event.call_count,
                collect_calls=[call.kwargs for call in collect.call_args_list],
                responded_writes=[write for write in writes
                                  if (write.get('developer') or {}).get('state') ==
                                  'DEVELOPER_RESPONDED'])
        finally:
            # Each run starts from the clean registered head.
            if (self.root / target).exists():
                (self.root / target).unlink()
        return run

    def _assert_gw_evidence_names(self, saved, session):
        for evidence in (saved['completion']['evidence'],
                         saved['developer']['direct_result_callback']['evidence']):
            self.assertEqual(evidence['executor_native_session_id'], session)
            self.assertEqual(evidence['executor_native_session'],
                             'VERIFIED' if session is not None else 'UNKNOWN')

    def test_gw_agent_accepted_run_binds_its_session_in_the_result_write(self):
        run = self._bind_run('gw-agent-bind.md')
        saved = run['saved']
        developer = saved['developer']
        self.assertEqual(run['code'], 0)
        self.assertEqual(run['calls'], [_BIND_SESSION])
        self.assertEqual(run['d6'], _BIND_SESSION)
        self.assertEqual(developer['state'], 'DEVELOPER_RESPONDED')
        self.assertEqual(developer['execution']['native_session_id'], _BIND_SESSION)
        self.assertNotIn('native_session_binding', developer['execution'])
        # Read back from a fresh TaskStore: both evidence blocks name the session.
        self._assert_gw_evidence_names(saved, _BIND_SESSION)
        # The existing developer-and-completion write carries the session.
        result_write = run['responded_writes'][0]
        self.assertIn('completion', result_write)
        self.assertEqual(result_write['completion']['evidence']['executor_native_session_id'],
                         _BIND_SESSION)
        self.assertEqual(result_write['developer']['direct_result_callback']['evidence']
                         ['executor_native_session'], 'VERIFIED')
        # The first evidence collection expects the session the D6 now holds.
        self.assertEqual(run['event_calls'], 1)
        self.assertEqual(run['collect_calls'][0]['expected_native_session_id'],
                         _BIND_SESSION)
        callback = developer['direct_result_callback']
        self.assertNotIn('D6_TASK_BINDING_MISMATCH', callback['missing_gates'])
        self.assertEqual((callback['state'], callback['missing_gates']),
                         ('PM_EVIDENCE_READY', []))

    def test_gw_agent_run_without_usable_session_binds_nothing(self):
        def outcome(run):
            saved = run['saved']
            developer = saved['developer']
            execution = developer['execution']
            if type(execution) is dict:
                # Timing and gateway output digest differ from run to run.
                execution = {key: value for key, value in execution.items()
                             if key not in ('elapsed_ms', 'stdout_sha256')}
            callback = developer['direct_result_callback']
            return (run['code'], run['error'], run['event_calls'], developer['state'],
                    developer['error_code'], execution, developer['artifact'] is None,
                    saved['completion']['evidence']['executor_native_session_id'],
                    saved['completion']['evidence']['executor_native_session'],
                    callback['evidence']['executor_native_session_id'],
                    callback['evidence']['executor_native_session'],
                    callback.get('state'), callback.get('missing_gates'))

        for session in (None, '', 'UNKNOWN', 'not a session'):
            with self.subTest(session=session):
                target = 'gw-agent-no-usable-session.md'
                today = self._bind_run(target, session=session, real_bind=False)
                run = self._bind_run(target, session=session)
                self.assertEqual(run['calls'], [])
                self.assertIsNone(run['d6'])
                self.assertEqual(outcome(run), outcome(today))
                if session in (None, 'not a session'):
                    # The existing no-session and bad-session results stand.
                    developer = run['saved']['developer']
                    self.assertEqual(run['code'], 1)
                    self.assertEqual((developer['state'], developer['error_code']),
                                     ('DEVELOPER_OUTCOME_UNKNOWN',
                                      'DEVELOPER_RESULT_PERSISTENCE_REJECTED'))

    def test_gw_agent_bind_waits_for_every_store_execution_check(self):
        def route_error(*_args, **_kwargs):
            raise RouteError('GW_AGENT_TIMEOUT')

        def interrupted(*_args, **_kwargs):
            raise KeyboardInterrupt()

        for name, options in (
                ('cleanup', {'fault': {'cleanup_status': 'UNKNOWN'}}),
                # The exit code the existing bad-exit-code case shows the store refuses.
                ('exit-code', {'fault': {'exit_code': 999}}),
                ('stdout-digest', {'fault': {'stdout_sha256': 'not-a-digest'}}),
                ('rejected-model', {'served_model': 'gw'}),
                ('failed', {'route_runner': route_error}),
                ('interrupted', {'route_runner': interrupted})):
            with self.subTest(run=name):
                run = self._bind_run(f'gw-agent-wait-{name}.md', **options)
                self.assertEqual(run['calls'], [])
                self.assertIsNone(run['d6'])
                self._assert_gw_evidence_names(run['saved'], None)
                self.assertNotEqual(run['saved']['developer']['state'],
                                    'DEVELOPER_RESPONDED')
                self.assertEqual(run['event_calls'], 0)
                if name == 'interrupted':
                    self.assertEqual(run['error'], 'KeyboardInterrupt')

    @staticmethod
    def _set_execution(**fields):
        def mutate(execution):
            execution.update(fields)
        return mutate

    def test_gw_agent_bind_refuses_execution_evidence_the_store_rejects(self):
        # A valid session with execution evidence TaskStore refuses: nothing
        # is bound, and the existing persistence-rejected result stands.
        def drop(field):
            return lambda execution: execution.pop(field)

        set_ = self._set_execution
        source = {'source_bytes': 10, 'source_file_count': 1,
                  'source_manifest_sha256': 'b' * 64}
        for name, mutate in (
                ('extra-binding-field', set_(native_session_binding='BOUND')),
                ('extra-launch-field', set_(child_pid=321)),
                ('missing-field', drop('elapsed_ms')),
                ('status-succeeded', set_(status='SUCCEEDED')),
                ('elapsed-negative', set_(elapsed_ms=-1)),
                ('elapsed-too-large', set_(elapsed_ms=86_400_001)),
                ('elapsed-bool', set_(elapsed_ms=True)),
                ('elapsed-str', set_(elapsed_ms='32')),
                ('elapsed-float', set_(elapsed_ms=32.0)),
                ('source-bytes-negative', set_(**{**source, 'source_bytes': -1})),
                ('source-bytes-too-large',
                 set_(**{**source, 'source_bytes': 2 * 1024 * 1024 + 1})),
                ('source-bytes-bool', set_(**{**source, 'source_bytes': True})),
                ('source-files-zero', set_(**{**source, 'source_file_count': 0})),
                ('source-files-too-many', set_(**{**source, 'source_file_count': 33})),
                ('source-files-bool', set_(**{**source, 'source_file_count': True})),
                ('manifest-uppercase',
                 set_(**{**source, 'source_manifest_sha256': 'B' * 64})),
                ('manifest-short', set_(**{**source, 'source_manifest_sha256': 'b' * 63})),
                ('manifest-not-str', set_(**{**source, 'source_manifest_sha256': 1})),
                ('usage-source', set_(usage_source='ESTIMATED')),
                ('source-partial', set_(source_bytes=10)),
                ('source-partial-two', set_(source_bytes=10, source_file_count=1))):
            with self.subTest(field=name):
                run = self._bind_run(f'gw-agent-refuse-{name}.md', mutate=mutate)
                developer = run['saved']['developer']
                self.assertEqual(run['calls'], [])
                self.assertIsNone(run['d6'])
                self._assert_gw_evidence_names(run['saved'], None)
                self.assertEqual(run['code'], 1)
                self.assertEqual((developer['state'], developer['error_code']),
                                 ('DEVELOPER_OUTCOME_UNKNOWN',
                                  'DEVELOPER_RESULT_PERSISTENCE_REJECTED'))
                self.assertEqual(run['event_calls'], 0)

    def test_gw_agent_bind_accepts_execution_at_the_store_bounds(self):
        set_ = self._set_execution
        for name, mutate in (('elapsed-zero', set_(elapsed_ms=0)),
                             ('elapsed-max', set_(elapsed_ms=86_400_000))):
            with self.subTest(field=name):
                run = self._bind_run(f'gw-agent-bounds-{name}.md', mutate=mutate)
                self.assertEqual(run['code'], 0)
                self.assertEqual(run['calls'], [_BIND_SESSION])
                self.assertEqual(run['d6'], _BIND_SESSION)
                self.assertEqual(run['saved']['developer']['state'], 'DEVELOPER_RESPONDED')
                self._assert_gw_evidence_names(run['saved'], _BIND_SESSION)

    def test_gw_agent_d6_already_holding_the_run_session_needs_no_helper(self):
        run = self._bind_run('gw-agent-bind-held.md', held=_BIND_SESSION)
        self.assertEqual(run['code'], 0)
        self.assertEqual(run['calls'], [])
        self.assertEqual(run['d6'], _BIND_SESSION)
        self.assertEqual(run['saved']['developer']['state'], 'DEVELOPER_RESPONDED')
        self._assert_gw_evidence_names(run['saved'], _BIND_SESSION)
        self.assertEqual(run['event_calls'], 1)
        self.assertEqual(run['collect_calls'][0]['expected_native_session_id'],
                         _BIND_SESSION)

    def test_gw_agent_session_the_d6_does_not_take_is_refused(self):
        for name, options, calls, d6 in (
                ('d6-holds-another', {'held': _EARLIER_SESSION}, [], _EARLIER_SESSION),
                ('helper-refused', {'answer': 'D6_BIND_NATIVE_SESSION_REFUSED'},
                 [_BIND_SESSION], None),
                ('helper-unavailable', {'answer': 'D6_BIND_NATIVE_SESSION_HELPER_UNAVAILABLE'},
                 [_BIND_SESSION], None),
                ('read-back-differs', {'stored': _OTHER_SESSION},
                 [_BIND_SESSION], _OTHER_SESSION)):
            with self.subTest(case=name):
                run = self._bind_run(f'gw-agent-refused-{name}.md', **options)
                developer = run['saved']['developer']
                self.assertEqual(run['calls'], calls)
                self.assertEqual(run['d6'], d6)
                self.assertEqual(run['code'], 1)
                self.assertEqual(developer['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
                self.assertEqual(developer['error_code'],
                                 'DEVELOPER_GW_AGENT_D6_SESSION_REFUSED')
                self.assertIsNone(developer['artifact'])
                self.assertEqual(run['event_calls'], 0)
                self.assertEqual(run['collect_calls'], [])
                # No evidence names the reported session; the D6 value stands.
                self._assert_gw_evidence_names(
                    run['saved'], _EARLIER_SESSION if name == 'd6-holds-another' else None)

    def _complete_with_reviewer_session(self, target, reviewer_native):
        result_sha = '4' * 40
        git_evidence = {
            'root': str(self.root), 'common_dir': self.common,
            'branch': 'task-branch', 'source_sha': self.head,
            'result_head': result_sha, 'changed_paths': [target],
            'diff_sha256': '5' * 64, 'executor_identity': 'gw-agent-developer',
        }

        def github(**_kwargs):
            return {'evidence': {'pr_number': 10, 'head_sha': result_sha,
                                 'formal_review_state': 'UNVERIFIED',
                                 'required_checks': [], 'successful_checks': [],
                                 'ci_head_sha': result_sha},
                    'missing_gates': ['CI_REQUIRED_CHECKS_MISSING']}

        real_read = task_completion.read_d6_registration

        def read_registration(task_id, *args, **kwargs):
            if task_id == 'reviewer-d6':
                return SimpleNamespace(task_id=task_id, native_session_id=reviewer_native)
            return real_read(task_id, *args, **kwargs)

        args = Namespace(task_id=self.task_id, executor_task='executor-d6',
                         reviewer_task='reviewer-d6', pr=10)
        with patch.object(task_completion, 'collect_executor_result',
                          return_value=git_evidence) as collect, \
             patch.object(task_completion, '_github_get', return_value={
                 'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': '9' * 40}}), \
             patch.object(task_completion, '_trusted_slug',
                          return_value='davidcatcat/davidcattoolsproject'), \
             patch.object(task_completion, 'run_bounded_tests',
                          return_value=[{'path': target, 'source_sha': result_sha,
                                         'exit_code': 0, 'output_sha256': '6' * 64}]), \
             patch.object(task_completion, 'collect_github_evidence', side_effect=github), \
             patch.object(task_completion, 'read_d6_registration',
                          side_effect=read_registration), \
             redirect_stdout(io.StringIO()):
            task_submission_cli.run_task_complete(args, store=self.store)
        return collect.call_args.kwargs, self._reloaded()['completion']

    def test_gw_agent_reviewer_session_gate_uses_the_bound_session(self):
        for name, reviewer, blocked in (
                ('other-reviewer', _REVIEWER_SESSION, False),
                ('same-reviewer', _BIND_SESSION, True)):
            with self.subTest(case=name):
                target = f'gw-agent-review-{name}.md'
                run = self._bind_run(target)
                self.assertEqual(run['saved']['developer']['state'], 'DEVELOPER_RESPONDED')
                self.assertEqual(run['d6'], _BIND_SESSION)
                collected, completion = self._complete_with_reviewer_session(target, reviewer)
                self.assertEqual(collected['expected_native_session_id'], _BIND_SESSION)
                # The GitHub step ran, so the Reviewer gate was evaluated.
                self.assertIn('CI_REQUIRED_CHECKS_MISSING', completion['missing_gates'])
                self.assertNotIn('D6_TASK_BINDING_MISMATCH', completion['missing_gates'])
                if blocked:
                    self.assertIn('REVIEWER_NATIVE_SESSION_NOT_INDEPENDENT',
                                  completion['missing_gates'])
                    self.assertEqual(
                        completion['evidence']['review_pr_ci']['formal_review_state'],
                        'UNVERIFIED')
                else:
                    self.assertNotIn('REVIEWER_NATIVE_SESSION_NOT_INDEPENDENT',
                                     completion['missing_gates'])


if __name__ == '__main__':
    unittest.main()
