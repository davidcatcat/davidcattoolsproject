"""#2812: a budget-exceeded gw-agent run keeps reported cumulative usage.

The gateway result carries a usage object for every status. A run that ends
BUDGET_EXCEEDED (including a turn-budget result) records the fully reported
token pair on its Developer record; when the gateway reports no usable usage,
the record keeps no usage and its execution source stays UNKNOWN.

The run below goes through the real TaskStore: the store accepts usage on an
unavailable Developer record only for this gw-agent budget shape, and keeps a
failure without usage when a usage-carrying write is refused.
"""

import copy
import hashlib
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
from llmtool.errors import LLMToolError

TASK_ID = 'a' * 32
RUN_ID = 'b' * 32
SOURCE_SHA = 'd' * 40
POLICY_DIGEST = 'f' * 64


def _task():
    """A fully valid PM_RESPONDED Task for the real TaskStore."""
    request = 'Synthetic task.'
    task = task_submission_cli._record(
        TASK_ID, RUN_ID, 'pm-' + 'a' * 32, 'pm-profile', request,
        state='PM_RESPONDED', provider_type='openai-compatible', model='pm-model',
        backend='NO_NATIVE_SESSION_ID_EXPOSED',
        request_sha=hashlib.sha256(request.encode()).hexdigest())
    task.update(
        pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                          'base_url': None},
        response='PM returned a bounded task.', error_code=None,
        pm_assignment={'schema_version': 1, 'route': 'gw-agent', 'risk': 'LOW',
                       'goal': 'record result', 'acceptance': ['record result']},
        developer=None, completion=None)
    return task


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


class _UsageRefusingStore:
    """A real store whose writes of a usage-carrying Developer are refused."""

    def __init__(self, base):
        self._base = base
        self.refused = 0

    def __getattr__(self, name):
        return getattr(self._base, name)

    def compare_and_update(self, task_id, *, expected, **updates):
        developer = updates.get('developer')
        if type(developer) is dict and developer.get('usage') is not None:
            self.refused += 1
            raise task_store.TaskStoreConflict('injected usage write rejection')
        return self._base.compare_and_update(task_id, expected=expected, **updates)


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

    def _run(self, result, *, requested_model='qwen-authorized', store_wrapper=None):
        temp = tempfile.TemporaryDirectory(prefix='gw-agent-usage-2812-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / 'tasks'
        store = task_store.TaskStore(root)
        store.create(_task())
        used_store = store if store_wrapper is None else store_wrapper(store)
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
            patch.object(worker_dispatch, 'authorize_task_store_change',
                         return_value=None),
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
                self.args, store=used_store, route_runner=budget_runner)
        return status, store.get(TASK_ID)['developer'], store

    def _assert_refused(self, store, developer):
        record = store.get(TASK_ID)
        record['developer'] = copy.deepcopy(developer)
        record['record_sha256'] = task_store._digest(record)
        with self.assertRaises(LLMToolError) as caught:
            task_store._validate(record, strict=True)
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_INVALID')

    def test_budget_exceeded_result_records_reported_usage(self):
        status, developer, store = self._run(_result(
            'BUDGET_EXCEEDED', 'TURN_BUDGET_EXCEEDED',
            {'status': 'REPORTED', 'input_tokens': 1200, 'output_tokens': 340}))
        self.assertEqual(status, 1)
        self.assertEqual(developer['state'], 'DEVELOPER_UNAVAILABLE')
        self.assertIn('DEVELOPER_GW_AGENT_BUDGET_EXCEEDED', developer['error_code'])
        self.assertEqual(developer['usage'], {
            'prompt_tokens': 1200, 'completion_tokens': 340, 'total_tokens': 1540,
            'served_model': 'qwen-authorized'})
        # The real store wrote and revalidated the budget record.
        task_store._validate_usage(developer['usage'])
        reloaded = task_store.TaskStore(store.root).get(TASK_ID)
        self.assertEqual(reloaded['developer'], developer)
        self.assertEqual(developer['execution']['status'], 'UNKNOWN')
        self.assertEqual(developer['execution']['usage_source'], 'CLI_REPORTED')

    def test_budget_exceeded_usage_and_model_name_the_served_model(self):
        status, developer, _store = self._run(
            _result('BUDGET_EXCEEDED', 'TURN_BUDGET_EXCEEDED',
                    {'status': 'REPORTED', 'input_tokens': 1200, 'output_tokens': 340}),
            requested_model='gw')
        self.assertEqual(status, 1)
        self.assertEqual(developer['model'], 'qwen-authorized')
        self.assertEqual(developer['model_provenance']['provider_model'],
                         'qwen-authorized')
        self.assertEqual(developer['usage']['served_model'], 'qwen-authorized')

    def test_budget_exceeded_without_usage_keeps_usage_unknown(self):
        status, developer, _store = self._run(_result(
            'BUDGET_EXCEEDED', 'TURN_BUDGET_EXCEEDED',
            {'status': 'UNKNOWN', 'input_tokens': None, 'output_tokens': None}))
        self.assertEqual(status, 1)
        self.assertIn('DEVELOPER_GW_AGENT_BUDGET_EXCEEDED', developer['error_code'])
        self.assertIsNone(developer['usage'])
        self.assertEqual(developer['execution']['status'], 'UNKNOWN')
        self.assertEqual(developer['execution']['usage_source'], 'UNKNOWN')

    def test_other_gateway_failure_does_not_gain_usage(self):
        status, developer, _store = self._run(_result(
            'MODEL_ERROR', 'HTTP_500',
            {'status': 'REPORTED', 'input_tokens': 1200, 'output_tokens': 340}))
        self.assertEqual(status, 1)
        self.assertIn('DEVELOPER_GW_AGENT_MODEL_ERROR', developer['error_code'])
        self.assertIsNone(developer['usage'])

    def test_usage_is_refused_on_another_unavailable_route(self):
        _status, developer, store = self._run(_result(
            'BUDGET_EXCEEDED', 'TURN_BUDGET_EXCEEDED',
            {'status': 'REPORTED', 'input_tokens': 1200, 'output_tokens': 340}))
        other_route = copy.deepcopy(developer)
        other_route.update(provider_profile='codex', provider_type='codex-cli',
                           backend_session_semantics='NO_NATIVE_SESSION_ID_EXPOSED')
        other_route.pop('data_class', None)
        other_route.pop('model_provenance', None)
        self._assert_refused(store, other_route)

    def test_usage_is_refused_for_another_gw_agent_error_code(self):
        _status, developer, store = self._run(_result(
            'BUDGET_EXCEEDED', 'TURN_BUDGET_EXCEEDED',
            {'status': 'REPORTED', 'input_tokens': 1200, 'output_tokens': 340}))
        other_code = copy.deepcopy(developer)
        other_code['error_code'] = 'DEVELOPER_GW_AGENT_TIMEOUT'
        self._assert_refused(store, other_code)

    def test_usage_is_refused_without_a_provider_usage_source(self):
        _status, developer, store = self._run(_result(
            'BUDGET_EXCEEDED', 'TURN_BUDGET_EXCEEDED',
            {'status': 'REPORTED', 'input_tokens': 1200, 'output_tokens': 340}))
        no_source = copy.deepcopy(developer)
        no_source['execution']['usage_source'] = 'UNKNOWN'
        self._assert_refused(store, no_source)

    def test_usage_is_refused_without_a_budget_execution(self):
        _status, developer, store = self._run(_result(
            'BUDGET_EXCEEDED', 'TURN_BUDGET_EXCEEDED',
            {'status': 'REPORTED', 'input_tokens': 1200, 'output_tokens': 340}))
        no_execution = copy.deepcopy(developer)
        no_execution.pop('execution')
        self._assert_refused(store, no_execution)
        done = copy.deepcopy(developer)
        done['execution'].update(
            status='DONE', native_session_id='12345678-1234-4234-8234-123456789abc',
            process_exit_code=0, stdout_sha256='c' * 64, elapsed_ms=1,
            cleanup_status='CONFIRMED')
        self._assert_refused(store, done)

    def test_refused_usage_write_still_saves_the_failure_without_usage(self):
        wrappers = []

        def wrapper(base):
            made = _UsageRefusingStore(base)
            wrappers.append(made)
            return made

        status, developer, store = self._run(_result(
            'BUDGET_EXCEEDED', 'TURN_BUDGET_EXCEEDED',
            {'status': 'REPORTED', 'input_tokens': 1200, 'output_tokens': 340}),
            requested_model='gw', store_wrapper=wrapper)
        self.assertEqual(status, 1)
        self.assertGreater(wrappers[0].refused, 0)
        self.assertIn('DEVELOPER_GW_AGENT_BUDGET_EXCEEDED', developer['error_code'])
        self.assertEqual(developer['state'], 'DEVELOPER_UNAVAILABLE')
        self.assertIsNone(developer['usage'])
        self.assertEqual(developer['execution']['status'], 'UNKNOWN')
        self.assertEqual(developer['execution']['usage_source'], 'UNKNOWN')
        # The refused usage write also drops the served-model fields it added;
        # the fallback names the model the assignment was dispatched with.
        self.assertEqual(developer['model'], 'gw')
        self.assertIsNone(developer['model_provenance']['provider_model'])
        self.assertEqual(store.get(TASK_ID)['developer'], developer)

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
