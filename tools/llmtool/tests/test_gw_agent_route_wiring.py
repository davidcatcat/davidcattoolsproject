import hashlib
import inspect
import io
import json
import os
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import review_dispatch, task_completion, task_submission_cli, worker_dispatch
from llmtool.developer_routes import GwAgentRoute, RouteError, load_route_config
from llmtool.developer_routes.gw_agent import cleanup_audit_copies
from llmtool.errors import LLMToolError
from llmtool import task_store


class GwAgentRouteWiringTests(unittest.TestCase):
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

    def test_route_config_and_data_class_are_validated(self):
        config = load_route_config()['routes']['gw-agent']
        self.assertEqual(config['package_path'], '~/.local/lib/api-agent-gateway')
        self.assertEqual(config['allowed_data_classes'], ['SYNTHETIC'])
        self.assertEqual(config['authorization_ref'],
            'https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-6594101520')
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / 'api_agent_gateway'
            package.mkdir()
            (package / '__init__.py').write_text('', encoding='utf-8')
            config['package_path'] = temp
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
            'egress_basis': 'Synthetic test content.', 'data_class': 'SYNTHETIC'}}
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

            def get(self, _task_id):
                return dict(self.value)

            def compare_and_update(self, _task_id, *, expected, **updates):
                self.claims += 1
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
                              'allowed_data_classes': ['SYNTHETIC'],
                              'default_model': 'gw-agent-default', 'default_effort': 'low'}}}), \
             patch.object(worker_dispatch, 'authorize_dispatch',
                          side_effect=worker_dispatch.WorkerDispatchDenied('OWNER_PAUSED')), \
             patch.object(task_submission_cli, 'run_route') as runner, \
             patch.object(task_submission_cli, '_task_view', return_value={}):
            status = task_submission_cli.run_task_gw_agent_delegate(args, store=store)
        self.assertEqual(status, 1)
        self.assertEqual(store.claims, 1)
        self.assertEqual(store.value['developer']['state'], 'DEVELOPER_UNAVAILABLE')
        self.assertEqual(store.value['developer']['error_code'], 'DEVELOPER_OWNER_PAUSED')
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
        self.assertIn("developer['provider_profile'] not in ('opencode', 'codex', 'agy', 'gw-agent')",
                      validator)

    def test_allowed_route_error_maps_to_safe_developer_code(self):
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
            raise RouteError('GW_AGENT_MODEL_ERROR')
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
                              'allowed_data_classes': ['SYNTHETIC'],
                              'default_model': 'gw-agent-default', 'default_effort': 'low'}}}), \
             patch.object(worker_dispatch, 'authorize_dispatch'), \
             patch.object(worker_dispatch, 'evaluate', return_value=decision), \
             patch.object(task_submission_cli, 'GwAgentRoute', return_value=route), \
             patch.object(task_submission_cli, 'for_platform', return_value=object()), \
             patch.object(task_submission_cli, 'run_task_direct_result_event') as event, \
             patch.object(task_submission_cli, '_task_view', return_value={}):
            status = task_submission_cli.run_task_gw_agent_delegate(
                args, store=store, route_runner=fail_runner)
        self.assertEqual(status, 1)
        self.assertEqual(store.value['developer']['state'], 'BLOCKED')
        self.assertEqual(store.value['developer']['error_code'],
                         'DEVELOPER_GW_AGENT_MODEL_ERROR')
        event.assert_not_called()

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


if __name__ == '__main__':
    unittest.main()
