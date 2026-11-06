import copy
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
from unittest.mock import patch

from llmtool import review_dispatch, task_completion, task_submission_cli, worker_dispatch
from llmtool.developer_routes import GwAgentRoute, RouteError, load_route_config
from llmtool.developer_routes.gw_agent import cleanup_audit_copies
from llmtool.errors import LLMToolError
from llmtool import task_store


import review_trust_fixture


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


class GwAgentRouteWiringTests(unittest.TestCase):
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
        route_config = {'allowed_data_classes': ['synthetic'], 'profile': {
            'data_class_map': {'SYNTHETIC': 'synthetic'}, 'context_cap_tokens': 32768,
            'max_output_tokens': 8192}}
        with patch.object(task_completion, 'validate_task_binding'), \
             patch.object(task_submission_cli, '_delegate_d6_binding',
                          return_value=(registration, snapshot)), \
             patch.object(task_submission_cli, '_assignment_paths',
                          return_value=(['src.py'], ['src.py'])), \
             patch.object(task_submission_cli, 'load_route_config',
                          return_value={'routes': {'gw-agent': route_config}}), \
             patch.object(task_submission_cli, 'resolve_profile_config', return_value={
                 'generation': {'max_tokens': 4096}}), \
             patch.object(task_submission_cli, 'selected_provider', return_value={
                 'type': 'openai-compatible', 'model': 'authorized-model',
                 'base_url': 'https://company.example/v1'}):
            with self.assertRaisesRegex(
                    LLMToolError, 'authorized company-gw profile is unavailable or invalid'):
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
        self.assertEqual(config['allowed_data_classes'], ['synthetic'])
        self.assertEqual(config['profile']['adapter'], 'openai-chat-tools')
        self.assertNotIn('base_url', config['profile'])
        self.assertEqual(config['profile']['allowed_data_classes'], ['synthetic'])
        self.assertEqual(config['authorization_ref'],
            'https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6594101520')
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
                              'allowed_data_classes': ['synthetic'],
                              'profile': {'adapter': 'openai-chat-tools',
                                  'base_url': 'https://api.openai.com/v1',
                                  'model': 'gw-agent-default',
                                  'context_cap_tokens': 32768,
                                  'max_output_tokens': 8192,
                                  'allowed_data_classes': ['synthetic']},
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
                self.value = dict(task)

            def get(self, _task_id):
                return dict(self.value)

            def compare_and_update(self, _task_id, *, expected, **updates):
                self.value.update(updates)
                return dict(self.value)

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
                              'allowed_data_classes': ['synthetic'],
                              'profile': {'adapter': 'openai-chat-tools',
                                  'base_url': 'https://api.openai.com/v1',
                                  'model': 'gw-agent-default',
                                  'context_cap_tokens': 32768,
                                  'max_output_tokens': 8192,
                                  'allowed_data_classes': ['synthetic']},
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
                              'allowed_data_classes': ['synthetic'],
                              'profile': gateway_profile,
                              'profile_name': 'llmtool-direct',
                              'authorization_ref': 'pm-task-ref',
                              'default_model': 'gw-agent-default', 'default_effort': 'low'}}}), \
             patch.dict(os.environ, {'HOME': str(config_home)}), \
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
        self.assertEqual(seen['profile']['max_output_tokens'], 8192)
        self.assertEqual(seen['profile']['http_allow_hosts'], ['10.20.30.40'])
        self.assertEqual(developer['artifact'], 'diff')
        self.assertEqual(developer['usage']['served_model'], 'authorized-model')
        self.assertEqual(seen['task']['schema_version'], 1)
        self.assertEqual(seen['task']['goal'], 'goal')
        self.assertEqual(seen['task']['acceptance'], ['record result'])
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
        self.store = task_store.TaskStore(Path(temp.name).resolve() / 'tasks')

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
                  provider_model='gw', reject_responded=False):
        task_id = self._active_pm_task(target)
        if provider_model != 'gw':
            config = self.base / '.config' / 'llmtool' / 'config.yaml'
            config.write_text(
                'active_provider: company-gw\n'
                'generation:\n  max_tokens: 49152\n'
                'providers:\n  company-gw:\n    type: openai-compatible\n'
                f'    base_url: http://127.0.0.1:{self.server.port}\n'
                f'    model: {provider_model}\n',
                encoding='utf-8')
        self.server.script.extend((
            self.reply(self.tool_call('apply_edit', path=target, new='hello synthetic\n',
                                      create=True), model=served_model, usage=usage),
            self.reply(self.tool_call('finish', summary='done'), model=served_model,
                       usage=usage)))
        args = Namespace(task_id=task_id, mode='direct', context_stdin=False,
            data_class='SYNTHETIC', egress_basis='Synthetic test content only.',
            acceptance=[f'Only {target} is created.'], selection_source='PM',
            executor_task='executor-d6', read_path=[target], write_path=[target],
            cwd=str(self.root))
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
        with patch.object(task_submission_cli, 'load_route_config',
                          return_value=real_config), \
             patch.object(task_submission_cli, 'for_platform',
                          return_value=_NoSandboxBoundary()), \
             patch.object(worker_dispatch, 'authorize_dispatch'), \
             patch.object(worker_dispatch, 'evaluate', return_value=decision), \
             redirect_stdout(io.StringIO()):
            code = task_submission_cli.run_task_gw_agent_delegate(args, store=store)
        # A fresh store proves the record is on disk and still passes validation.
        return code, task_store.TaskStore(Path(self.base / 'tasks').resolve()).get(task_id)

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

    def test_docs_success_persists_and_reaches_the_active_pm(self):
        target = 'gw-agent-synthetic.md'
        code, task = self._delegate(target)
        self._assert_success_persisted(task, target)
        callback = task['developer']['direct_result_callback']
        self.assertEqual((code, callback['state'], callback['missing_gates']),
                         (0, 'PM_EVIDENCE_READY', []))
        self.assertEqual(callback['evidence']['verification_state'], 'READY')

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
        self.assertEqual(developer['execution']['status'], 'DONE')

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


if __name__ == '__main__':
    unittest.main()
