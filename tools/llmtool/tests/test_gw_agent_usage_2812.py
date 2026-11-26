"""#2812: a budget-exceeded gw-agent run keeps reported cumulative usage.

The gateway result carries a usage object for every status. A run that ends
BUDGET_EXCEEDED (including a turn-budget result) records the fully reported
token pair on its Developer record; when the gateway reports no usable usage,
the record keeps no usage and its execution source stays UNKNOWN.
"""

import copy
import tempfile
import unittest
from argparse import Namespace
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import (gw_agent_sessions, repair_policy, task_store,
                     task_submission_cli, worker_availability, worker_dispatch)
from llmtool.developer_routes import GwAgentRoute, RouteError
from llmtool.developer_routes.gw_agent import _normalize_gw_agent_usage

TASK_ID = 'a' * 32
RUN_ID = 'b' * 32
CONTRACT_DIGEST = 'c' * 64
SOURCE_SHA = 'd' * 40
POLICY_DIGEST = 'f' * 64


def _task():
    return {
        'schema_version': 2, 'task_id': TASK_ID, 'run_id': RUN_ID,
        'state': 'PM_RESPONDED', 'response': 'PM response',
        'request': 'Synthetic task.', 'task_binding': {'contract_digest': CONTRACT_DIGEST},
        'pm_assignment': {'route': 'gw-agent', 'risk': 'LOW', 'goal': 'goal',
                          'acceptance': ['record result']},
        'developer': None, 'completion': None,
    }


def _route_config():
    return {
        'allowed_data_classes': ['synthetic', 'public_repo'],
        'budgets': {'max_turns': 512, 'max_input_tokens': 262144,
                    'max_output_tokens': 65536, 'max_wall_seconds': 1800,
                    'max_tool_result_bytes': 262144, 'max_sent_bytes': 16777216},
        'profile': {'adapter': 'openai-chat-tools', 'model': 'qwen-authorized',
                    'context_cap_tokens': 32768, 'max_output_tokens': 8192,
                    'allowed_data_classes': ['synthetic', 'public_repo']},
        'default_model': 'qwen-authorized', 'allowed_models': ['qwen-authorized'],
        'default_effort': 'low',
    }


def _result(status, code, usage):
    return {
        'schema_version': 1, 'status': status, 'code': code,
        'run_id': 'e' * 24, 'session_id': None, 'task_id': TASK_ID,
        'changed_paths': [], 'summary': 'bounded', 'served_model': 'qwen-authorized',
        'usage': usage, 'turns': 4, 'budget_used': {}, 'audit_sha256': '1' * 64,
    }


def _route_for(result):
    """A real GwAgentRoute whose parsed gateway result is ``result``."""
    route = GwAgentRoute.__new__(GwAgentRoute)
    route.config = {}
    route.expected_served_model = None
    route._validated_request = None
    route._result = result
    route._tool_failure = None
    route._status = result['status']
    route._gateway_code = result['code']
    route.audit_copy_path = None
    return route


class _Store:
    def __init__(self, task):
        self._temp = tempfile.TemporaryDirectory(prefix='gw-agent-usage-2812-')
        self.root = Path(self._temp.name) / 'tasks'
        self.value = copy.deepcopy(task)

    def cleanup(self):
        self._temp.cleanup()

    def get(self, _task_id):
        return copy.deepcopy(self.value)

    def compare_and_update(self, _task_id, *, expected, **updates):
        for key, value in expected.items():
            if self.value.get(key) != value:
                raise AssertionError('unexpected expected value')
        self.value.update(copy.deepcopy(updates))
        return copy.deepcopy(self.value)

    def d6_bind_compare_and_update(self, task_id, *, executor_task_id, expected, **updates):
        return self.compare_and_update(task_id, expected=expected, **updates)

    def update(self, _task_id, **updates):
        self.value.update(copy.deepcopy(updates))
        return copy.deepcopy(self.value)


class _Site:
    is_repair = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def route_fields(self):
        return {}

    def check_scope(self, _task_input):
        return None

    def after_result(self, _session_id, _task_input):
        return True


class GwAgentUsage2812Tests(unittest.TestCase):
    def setUp(self):
        self.registration = SimpleNamespace(
            task_id='executor-d6', root='/repo/worktree', common_dir='/repo/.git',
            branch='task-branch', trusted_agent_id='codex',
            session_ref='session-ref', native_session_id=None)
        self.snapshot = SimpleNamespace(head=SOURCE_SHA)
        self.args = Namespace(
            task_id=TASK_ID, mode='direct', context_stdin=False, data_class='SYNTHETIC',
            egress_basis='Synthetic test context.', acceptance=['record result'],
            selection_source='PM', executor_task='executor-d6',
            read_path=['src.py'], write_path=['src.py'], timeout=None)

    def _run(self, result, *, requested_model='qwen-authorized'):
        store = _Store(_task())
        self.addCleanup(store.cleanup)
        route = _route_for(result)
        decision = worker_dispatch.PolicyDecision(
            'gw-agent', True, 'OWNER_POLICY_ALLOWED', POLICY_DIGEST)

        def budget_runner(route_arg, _request, **_kwargs):
            self.assertIs(route_arg, route)
            route_arg.validate_status({})
            raise AssertionError('a budget-exceeded result must be refused')

        patches = (
            patch.object(gw_agent_sessions, 'sweep_quietly'),
            patch.object(worker_availability, 'gw_agent_refusal', return_value=None),
            patch.object(worker_availability, 'record_gw_agent_failure'),
            patch.object(task_submission_cli, '_delegate_timeout', return_value=600),
            patch.object(task_submission_cli, '_delegate_chain_gate',
                         side_effect=lambda _args, _store, _binding, claim: claim()),
            patch.object(task_submission_cli.task_completion, 'validate_task_binding'),
            patch.object(task_submission_cli, '_delegate_d6_binding',
                         return_value=(self.registration, self.snapshot)),
            patch.object(task_submission_cli, '_assignment_paths',
                         return_value=(['src.py'], ['src.py'])),
            patch.object(task_submission_cli, '_remote_receipt_test_policy',
                         return_value='REMOTE_RECEIPT'),
            patch.object(task_submission_cli, '_direct_result_callback', return_value={}),
            patch.object(task_submission_cli, 'load_route_config',
                         return_value={'routes': {'gw-agent': _route_config()}}),
            patch.object(task_submission_cli, '_gw_agent_endpoint', return_value=({
                'type': 'openai-compatible', 'model': requested_model,
                'base_url': 'http://10.20.30.40:8080'}, {'max_tokens': 49152})),
            patch.object(task_submission_cli, '_gw_agent_developer_profile',
                         return_value=(requested_model, 'low')),
            patch.object(task_submission_cli, 'GwAgentRoute', return_value=route),
            patch.object(task_submission_cli, 'for_platform', return_value=object()),
            patch.object(task_submission_cli, 'installed_gateway_stale',
                         return_value=False),
            patch.object(repair_policy, 'gw_agent_max_turns', return_value=64),
            patch.object(worker_dispatch, 'authorize_dispatch'),
            patch.object(worker_dispatch, 'evaluate', return_value=decision),
            patch.object(task_submission_cli.gw_agent_callsite, 'SessionCallSite',
                         return_value=_Site()),
            patch.object(task_submission_cli, 'cleanup_audit_copies'),
            patch.object(task_submission_cli, '_print'),
            patch.object(task_submission_cli, '_task_view', return_value={}),
        )
        with ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            status = task_submission_cli.run_task_gw_agent_delegate(
                self.args, store=store, route_runner=budget_runner)
        return status, store.value['developer']

    def test_budget_exceeded_result_records_reported_usage(self):
        status, developer = self._run(_result(
            'BUDGET_EXCEEDED', 'TURN_BUDGET_EXCEEDED',
            {'status': 'REPORTED', 'input_tokens': 1200, 'output_tokens': 340}))
        self.assertEqual(status, 1)
        self.assertEqual(developer['state'], 'DEVELOPER_UNAVAILABLE')
        self.assertIn('DEVELOPER_GW_AGENT_BUDGET_EXCEEDED', developer['error_code'])
        self.assertEqual(developer['usage'], {
            'prompt_tokens': 1200, 'completion_tokens': 340, 'total_tokens': 1540,
            'served_model': 'qwen-authorized'})
        # The recorded usage is the real store's accepted shape.
        task_store._validate_usage(developer['usage'])
        self.assertEqual(developer['execution']['status'], 'UNKNOWN')
        self.assertEqual(developer['execution']['usage_source'], 'CLI_REPORTED')

    def test_budget_exceeded_usage_and_model_name_the_served_model(self):
        status, developer = self._run(
            _result('BUDGET_EXCEEDED', 'TURN_BUDGET_EXCEEDED',
                    {'status': 'REPORTED', 'input_tokens': 1200, 'output_tokens': 340}),
            requested_model='gw')
        self.assertEqual(status, 1)
        self.assertEqual(developer['model'], 'qwen-authorized')
        self.assertEqual(developer['model_provenance']['provider_model'],
                         'qwen-authorized')
        self.assertEqual(developer['usage']['served_model'], 'qwen-authorized')

    def test_budget_exceeded_without_usage_keeps_usage_unknown(self):
        status, developer = self._run(_result(
            'BUDGET_EXCEEDED', 'TURN_BUDGET_EXCEEDED',
            {'status': 'UNKNOWN', 'input_tokens': None, 'output_tokens': None}))
        self.assertEqual(status, 1)
        self.assertIn('DEVELOPER_GW_AGENT_BUDGET_EXCEEDED', developer['error_code'])
        self.assertIsNone(developer['usage'])
        self.assertEqual(developer['execution']['status'], 'UNKNOWN')
        self.assertEqual(developer['execution']['usage_source'], 'UNKNOWN')

    def test_other_gateway_failure_does_not_gain_usage(self):
        status, developer = self._run(_result(
            'MODEL_ERROR', 'HTTP_500',
            {'status': 'REPORTED', 'input_tokens': 1200, 'output_tokens': 340}))
        self.assertEqual(status, 1)
        self.assertIn('DEVELOPER_GW_AGENT_MODEL_ERROR', developer['error_code'])
        self.assertIsNone(developer['usage'])

    def test_route_exposes_normalized_budget_usage(self):
        route = _route_for(_result(
            'BUDGET_EXCEEDED', 'TURN_BUDGET_EXCEEDED',
            {'status': 'REPORTED', 'input_tokens': 12, 'output_tokens': 3}))
        self.assertEqual(route.reported_usage(), {
            'prompt_tokens': 12, 'completion_tokens': 3, 'total_tokens': 15})
        parsed = route.parse_output(b'', b'', '')
        with self.assertRaises(RouteError) as caught:
            route.validate_status(parsed)
        self.assertEqual(caught.exception.gateway_usage, {
            'prompt_tokens': 12, 'completion_tokens': 3, 'total_tokens': 15})
        self.assertEqual(_normalize_gw_agent_usage(caught.exception.gateway_usage), {
            'prompt_tokens': 12, 'completion_tokens': 3, 'total_tokens': 15})

    def test_route_exposes_no_usage_when_not_fully_reported(self):
        route = _route_for(_result(
            'BUDGET_EXCEEDED', 'TURN_BUDGET_EXCEEDED',
            {'status': 'UNKNOWN', 'input_tokens': 12, 'output_tokens': None}))
        self.assertIsNone(route.reported_usage())
        parsed = route.parse_output(b'', b'', '')
        with self.assertRaises(RouteError) as caught:
            route.validate_status(parsed)
        self.assertIsNone(caught.exception.gateway_usage)


if __name__ == '__main__':
    unittest.main()
