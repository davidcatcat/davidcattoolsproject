"""#2310: pre-provider refusal same-Task delegate restart and duplicate refusal tests.

Tests cover:
1. A Task whose delegate was refused before any provider call can be delegated
   again on the same Task and D6, without counting as a model result or a repair,
   and the restart is recorded in start_failures and dispatch_audit.
2. A Task whose run reached the provider (any model call) is still refused as
   a duplicate; repair and run counts, review gates and worker-dispatch gating
   are unchanged.

Tests run against a real temporary TaskStore and the real run_task_delegate entry.
TEST DATA RULE (Owner #763 A): no credential-shaped strings.
"""

from __future__ import annotations

import copy
import hashlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from llmtool.developer_routes import RouteError, RouteResult
from llmtool.errors import LLMToolError
from llmtool import task_completion
from llmtool.task_store import TaskStore, new_id
from llmtool import task_submission_cli as submission_cli
from llmtool import worker_dispatch

import review_trust_fixture
import worker_policy_fixture


def setUpModule():
    review_trust_fixture.start_module()
    worker_policy_fixture.start_module()


def tearDownModule():
    worker_policy_fixture.stop_module()
    review_trust_fixture.stop_module()


class _NoSandboxBoundary:
    def prepare(self, _root, _write_paths, _state_dirs=()):
        return ((), 'a' * 64)

    def verify(self, _root, _write_paths, _state_dirs=(), *, runner=None):
        return {'verified': True, 'policy_sha256': 'a' * 64}


class PreModelRestartTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.store_dir = self.base / 'tasks'
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.store = TaskStore(self.store_dir)
        self.d6_root = self.base / 'd6_worktree'
        self.d6_root.mkdir(parents=True, exist_ok=True)
        (self.d6_root / 'file.txt').write_text('initial content\n', encoding='utf-8')

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_task(self, route='agy', executor_task_id='executor-d6-1'):
        task_id = new_id()
        run_id = new_id()
        contract_digest = 'a' * 64
        record = {
            'schema_version': 2,
            'task_id': task_id,
            'run_id': run_id,
            'pm_session_id': 'pm-session-1',
            'state': 'PM_RESPONDED',
            'response': 'Approved PM response',
            'request': 'Bounded task request',
            'task_binding': {'contract_digest': contract_digest},
            'pm_assignment': {
                'route': route,
                'risk': 'LOW',
                'goal': 'Complete the bounded task.',
                'acceptance': ['First item', 'Second item'],
            },
            'developer': None,
            'completion': None,
        }
        self.store.create(record)
        return task_id

    def _make_args(self, task_id, route='agy', executor_task_id='executor-d6-1'):
        extra = {}
        if route == 'gw-agent':
            extra['data_class'] = 'SYNTHETIC'
        return SimpleNamespace(
            task_id=task_id,
            provider=route,
            mode='direct',
            background=False,
            context_stdin=False,
            selection_source='PM',
            executor_task=executor_task_id,
            cwd=str(self.d6_root),
            read_path=['file.txt'],
            write_path=['file.txt'],
            acceptance=['First item', 'Second item'],
            egress_basis='Synthetic verified non-sensitive test basis.',
            owner_ref=None,
            repair_packet=None,
            timeout=None,
            pm_session=None,
            **extra,
        )

    def _d6_binding_mock(self, executor_task_id='executor-d6-1', trusted_agent_id='agy-developer'):
        registration = SimpleNamespace(
            task_id=executor_task_id,
            root=str(self.d6_root),
            common_dir=str(self.d6_root / '.git'),
            branch='task-branch',
            trusted_agent_id=trusted_agent_id,
            session_ref='sess-1',
            native_session_id=None,
            source_sha='b' * 40,
            result_sha='b' * 40,
        )
        snapshot = SimpleNamespace(head='b' * 40)
        return registration, snapshot

    def test_agy_pre_model_refusal_can_be_delegated_again_on_same_task_and_d6(self):
        task_id = self._create_task(route='agy')
        args = self._make_args(task_id, route='agy')
        reg, snap = self._d6_binding_mock(trusted_agent_id='agy-developer')

        def failing_runner(*_args, **_kwargs):
            raise RouteError('AGY_CONFIG_INVALID')

        def succeeding_runner(*_args, **_kwargs):
            (self.d6_root / 'file.txt').write_text('modified content\n', encoding='utf-8')
            return RouteResult(
                route='agy', status='DONE', summary='Modified file.txt',
                session_id='agy-sess-1', model='gemini-2.5-pro', effort='medium',
                usage={'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150},
                changed_files=('file.txt',), exit_code=0, stdout_sha256='c' * 64)

        with patch.object(submission_cli, '_delegate_d6_binding', return_value=(reg, snap)), \
             patch.object(submission_cli, 'for_platform', return_value=_NoSandboxBoundary()), \
             patch.object(submission_cli, '_check_developer_screen_disposition'), \
             patch.object(submission_cli, 'run_task_direct_result_event', return_value=0), \
             patch.object(submission_cli, '_d6_unchanged_probe', return_value=lambda _sha, write_paths=None: True), \
             patch.object(task_completion, '_worktree_diff', return_value=(['file.txt'], b'diff')):

            # First delegate: fails before provider process starts (pre-model refusal).
            code1 = submission_cli.run_task_delegate(args, store=self.store, route_runner=failing_runner)
            self.assertEqual(code1, 1)

            task_after_first = self.store.get(task_id)
            dev1 = task_after_first['developer']
            self.assertEqual(dev1['state'], 'BLOCKED')
            self.assertEqual(dev1['error_code'], 'AGY_CONFIG_INVALID')
            self.assertIsNone(dev1['execution'])
            self.assertNotIn('start_failures', task_after_first)

            # Repair count check: verify that this prelaunch failure does not count as a repair
            stamp = submission_cli._repair_run_stamp(task_id, dev1, lambda _sha, write_paths=None: True)
            self.assertIsNone(stamp)

            # Second delegate: delegated again on the same Task and same D6!
            code2 = submission_cli.run_task_delegate(args, store=self.store, route_runner=succeeding_runner)
            self.assertEqual(code2, 0)

            task_after_second = self.store.get(task_id)
            dev2 = task_after_second['developer']
            self.assertEqual(dev2['state'], 'DEVELOPER_RESPONDED')
            self.assertNotEqual(dev2['assignment_id'], dev1['assignment_id'])

            # Verify that start_failures records the restart
            start_failures = task_after_second.get('start_failures')
            self.assertIsInstance(start_failures, list)
            self.assertEqual(len(start_failures), 1)
            self.assertEqual(start_failures[0]['failed_developer']['assignment_id'], dev1['assignment_id'])
            self.assertEqual(start_failures[0]['retry_assignment_id'], dev2['assignment_id'])

            # Verify that dispatch_audit contains START_RETRY with decision ALLOWED
            audit = task_after_second.get('dispatch_audit', [])
            retry_audits = [item for item in audit if item.get('action') == 'START_RETRY']
            self.assertEqual(len(retry_audits), 1)
            self.assertEqual(retry_audits[0]['decision'], 'ALLOWED')

    def test_claude_pre_model_refusal_can_be_delegated_again_on_same_task_and_d6(self):
        task_id = self._create_task(route='claude')
        args = self._make_args(task_id, route='claude')
        reg, snap = self._d6_binding_mock(trusted_agent_id='claude-developer')

        def failing_runner(*_args, **_kwargs):
            raise RouteError('CLAUDE_CONFIG_INVALID')

        def succeeding_runner(*_args, **_kwargs):
            (self.d6_root / 'file.txt').write_text('claude modified\n', encoding='utf-8')
            return RouteResult(
                route='claude', status='DONE', summary='Modified file.txt',
                session_id='claude-sess-1', model='claude-3-7-sonnet', effort='low',
                usage={'prompt_tokens': 120, 'completion_tokens': 60, 'total_tokens': 180},
                changed_files=('file.txt',), exit_code=0, stdout_sha256='d' * 64)

        with patch.object(submission_cli, '_delegate_d6_binding', return_value=(reg, snap)), \
             patch.object(submission_cli, 'for_platform', return_value=_NoSandboxBoundary()), \
             patch.object(submission_cli, '_check_developer_screen_disposition'), \
             patch.object(submission_cli, 'run_task_direct_result_event', return_value=0), \
             patch.object(submission_cli, '_d6_unchanged_probe', return_value=lambda _sha, write_paths=None: True), \
             patch.object(task_completion, '_worktree_diff', return_value=(['file.txt'], b'diff')):

            # First delegate: pre-model refusal
            code1 = submission_cli.run_task_delegate(args, store=self.store, route_runner=failing_runner)
            self.assertEqual(code1, 1)

            task_after_first = self.store.get(task_id)
            dev1 = task_after_first['developer']
            self.assertEqual(dev1['state'], 'BLOCKED')
            self.assertEqual(dev1['error_code'], 'CLAUDE_CONFIG_INVALID')

            # Second delegate: runs again on same Task and D6
            code2 = submission_cli.run_task_delegate(args, store=self.store, route_runner=succeeding_runner)
            self.assertEqual(code2, 0)

            task_after_second = self.store.get(task_id)
            dev2 = task_after_second['developer']
            self.assertEqual(dev2['state'], 'DEVELOPER_RESPONDED')
            self.assertNotEqual(dev2['assignment_id'], dev1['assignment_id'])

            start_failures = task_after_second.get('start_failures')
            self.assertEqual(len(start_failures), 1)
            self.assertEqual(start_failures[0]['failed_developer']['assignment_id'], dev1['assignment_id'])
            self.assertEqual(start_failures[0]['retry_assignment_id'], dev2['assignment_id'])

            audit = task_after_second.get('dispatch_audit', [])
            retry_audits = [item for item in audit if item.get('action') == 'START_RETRY']
            self.assertEqual(len(retry_audits), 1)
            self.assertEqual(retry_audits[0]['decision'], 'ALLOWED')

    def test_gw_agent_pre_model_refusal_can_be_delegated_again_on_same_task_and_d6(self):
        task_id = self._create_task(route='gw-agent')
        args = self._make_args(task_id, route='gw-agent')
        reg, snap = self._d6_binding_mock(trusted_agent_id='test-runner')

        gw_config = {
            'package_path': str(self.base),
            'allowed_data_classes': ['synthetic'],
            'profile': {
                'adapter': 'openai-chat-tools',
                'allowed_data_classes': ['synthetic'],
                'data_class_map': {'SYNTHETIC': 'synthetic'},
                'timeout_seconds': 1800,
                'model': 'test-model',
                'base_url': 'http://127.0.0.1:9099/v1',
                'max_output_tokens': 4096,
                'context_cap_tokens': 32768,
            },
            'default_model': 'test-model',
            'budgets': {
                'max_turns': 10, 'max_input_tokens': 10000,
                'max_output_tokens': 2000, 'max_wall_seconds': 60,
                'max_tool_result_bytes': 4096, 'max_sent_bytes': 10000,
            },
        }

        def failing_runner(*_args, **_kwargs):
            raise RouteError('GW_AGENT_CONFIG_INVALID')

        def succeeding_runner(*_args, **_kwargs):
            (self.d6_root / 'file.txt').write_text('gw modified\n', encoding='utf-8')
            return RouteResult(
                route='gw-agent', status='DONE', summary='Modified file.txt',
                session_id='gw-sess-1', model='test-model', effort='low',
                usage={'prompt_tokens': 80, 'completion_tokens': 40, 'total_tokens': 120},
                changed_files=('file.txt',), exit_code=0, stdout_sha256='g' * 64)

        with patch.object(submission_cli, '_delegate_d6_binding', return_value=(reg, snap)), \
             patch.object(submission_cli, 'load_route_config', return_value={'routes': {'gw-agent': gw_config}}), \
             patch.object(submission_cli, '_gw_agent_endpoint', return_value=({'model': 'test-model', 'base_url': 'http://127.0.0.1:9099'}, {'max_tokens': 2048})), \
             patch.object(submission_cli, '_gw_agent_developer_profile', return_value=('test-model', 'low')), \
             patch.object(submission_cli, 'for_platform', return_value=_NoSandboxBoundary()), \
             patch.object(submission_cli, '_check_developer_screen_disposition'), \
             patch.object(submission_cli, 'run_task_direct_result_event', return_value=0), \
             patch.object(submission_cli, '_d6_unchanged_probe', return_value=lambda _sha, write_paths=None: True), \
             patch.object(task_completion, '_worktree_diff', return_value=(['file.txt'], b'diff')):

            # First delegate: pre-model refusal
            code1 = submission_cli.run_task_delegate(args, store=self.store, route_runner=failing_runner)
            self.assertEqual(code1, 1)

            task_after_first = self.store.get(task_id)
            dev1 = task_after_first['developer']
            self.assertEqual(dev1['state'], 'DEVELOPER_UNAVAILABLE')
            self.assertEqual(dev1['error_code'], 'DEVELOPER_GW_AGENT_CONFIG_INVALID')

            # Second delegate: runs again on same Task and D6
            code2 = submission_cli.run_task_delegate(args, store=self.store, route_runner=succeeding_runner)
            self.assertEqual(code2, 0)

            task_after_second = self.store.get(task_id)
            dev2 = task_after_second['developer']
            self.assertEqual(dev2['state'], 'DEVELOPER_RESPONDED')
            self.assertNotEqual(dev2['assignment_id'], dev1['assignment_id'])

            start_failures = task_after_second.get('start_failures')
            self.assertEqual(len(start_failures), 1)
            self.assertEqual(start_failures[0]['failed_developer']['assignment_id'], dev1['assignment_id'])
            self.assertEqual(start_failures[0]['retry_assignment_id'], dev2['assignment_id'])

            audit = task_after_second.get('dispatch_audit', [])
            retry_audits = [item for item in audit if item.get('action') == 'START_RETRY']
            self.assertEqual(len(retry_audits), 1)
            self.assertEqual(retry_audits[0]['decision'], 'ALLOWED')

    def test_task_whose_run_reached_provider_is_refused_as_duplicate(self):
        task_id = self._create_task(route='agy')
        args = self._make_args(task_id, route='agy')
        reg, snap = self._d6_binding_mock(trusted_agent_id='agy-developer')

        def provider_runner(*_args, **_kwargs):
            (self.d6_root / 'file.txt').write_text('provider executed\n', encoding='utf-8')
            return RouteResult(
                route='agy', status='DONE', summary='Modified file.txt',
                session_id='agy-sess-1', model='gemini-2.5-pro', effort='medium',
                usage={'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150},
                changed_files=('file.txt',), exit_code=0, stdout_sha256='e' * 64)

        with patch.object(submission_cli, '_delegate_d6_binding', return_value=(reg, snap)), \
             patch.object(submission_cli, 'for_platform', return_value=_NoSandboxBoundary()), \
             patch.object(submission_cli, '_check_developer_screen_disposition'), \
             patch.object(submission_cli, 'run_task_direct_result_event', return_value=0), \
             patch.object(submission_cli, '_d6_unchanged_probe', return_value=lambda _sha, write_paths=None: True), \
             patch.object(task_completion, '_worktree_diff', return_value=(['file.txt'], b'diff')):

            # Initial delegate reaches provider and completes
            code1 = submission_cli.run_task_delegate(args, store=self.store, route_runner=provider_runner)
            self.assertEqual(code1, 0)

            task1 = self.store.get(task_id)
            self.assertEqual(task1['developer']['state'], 'DEVELOPER_RESPONDED')

            # Attempting to delegate again on the same Task must be refused as duplicate
            with self.assertRaises(LLMToolError) as ctx:
                submission_cli.run_task_delegate(args, store=self.store, route_runner=provider_runner)
            self.assertIn('duplicate dispatch refused', str(ctx.exception))

            # The stored record is unchanged; no start_failures recorded
            task2 = self.store.get(task_id)
            self.assertEqual(task2['developer'], task1['developer'])
            self.assertNotIn('start_failures', task2)

    def test_provider_no_changes_run_is_refused_as_duplicate(self):
        task_id = self._create_task(route='claude')
        args = self._make_args(task_id, route='claude')
        reg, snap = self._d6_binding_mock(trusted_agent_id='claude-developer')

        def no_changes_runner(*_args, **_kwargs):
            return RouteResult(
                route='claude', status='BLOCKED', summary='No changes made',
                session_id='claude-sess-2', model='claude-3-7-sonnet', effort='low',
                usage={'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120},
                changed_files=(), exit_code=0, stdout_sha256='f' * 64)

        with patch.object(submission_cli, '_delegate_d6_binding', return_value=(reg, snap)), \
             patch.object(submission_cli, 'for_platform', return_value=_NoSandboxBoundary()), \
             patch.object(submission_cli, '_check_developer_screen_disposition'), \
             patch.object(submission_cli, '_d6_unchanged_probe', return_value=lambda _sha, write_paths=None: True), \
             patch.object(task_completion, '_worktree_diff', return_value=([], b'')):

            # Delegate reaches provider but makes no changes -> CLAUDE_NO_CHANGES with execution facts
            code1 = submission_cli.run_task_delegate(args, store=self.store, route_runner=no_changes_runner)
            self.assertEqual(code1, 1)

            task1 = self.store.get(task_id)
            dev1 = task1['developer']
            self.assertEqual(dev1['state'], 'BLOCKED')
            self.assertEqual(dev1['error_code'], 'CLAUDE_NO_CHANGES')
            self.assertIsNotNone(dev1['execution'])

            # Second delegate must be refused as duplicate (not a start failure because provider was reached)
            with self.assertRaises(LLMToolError) as ctx:
                submission_cli.run_task_delegate(args, store=self.store, route_runner=no_changes_runner)
            self.assertIn('duplicate dispatch refused', str(ctx.exception))

            task2 = self.store.get(task_id)
            self.assertEqual(task2['developer'], dev1)
            self.assertNotIn('start_failures', task2)


if __name__ == '__main__':
    unittest.main()
