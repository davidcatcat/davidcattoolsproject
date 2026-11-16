"""Advance step tests for Developer role timeouts (#1019 A11 row 41 PR 2).

Tests design tests 25-29 from docs/design/a11-row41-timeout-retry-1019.md:
25. Without the flag, each classifier outcome maps to the step and code in
    section 3.2, and only LIMIT changes Task state (to STOPPED).
26. LIMIT produces the D4 notice through the existing path, once.
27. With the flag, a quota FAIL or OWNER_PAUSED pre-check returns REFUSED with
    that code and writes no audit entry.
28. With the flag, the retry does not change repair, review FAIL, test failure
    or Debug counts.
29. An unknown transition or launch outcome returns
    ROLE_TIMEOUT_RETRY_OUTCOME_UNKNOWN, and a second call does not replay.

Built the same way tests/test_task_advance.py builds Tasks and stores.
No provider calls; unittest only.
Test data rule (Owner #763 A): ordinary tests never contain password-,
API-key-, token- or other credential-shaped strings.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llmtool import repair_policy
from llmtool import role_timeout
from llmtool import task_advance
from llmtool import worker_dispatch
from llmtool.task_store import TaskStore
from llmtool.task_submission_cli import _record as make_task_record

from review_trust_fixture import install_for_case as install_trust_fixture
from worker_policy_fixture import install as install_worker_policy, set_dispatch

HEAD = 'a' * 40
EMPTY_DIFF_SHA256 = hashlib.sha256(b'').hexdigest()

_code_tree_patch = None


def setUpModule():
    global _code_tree_patch
    _code_tree_patch = patch('llmtool.source_freshness.code_tree', return_value='d' * 64)
    _code_tree_patch.start()


def tearDownModule():
    global _code_tree_patch
    if _code_tree_patch is not None:
        _code_tree_patch.stop()


def d6(clean=True, head=HEAD, recorded=HEAD):
    return {
        'root': '/repo/task',
        'task_id': 'executor-1',
        'result_head': head,
        'recorded_result_head': recorded,
        'registered_result_head': recorded,
        'dirty': not clean,
        'state': 'CLEAN' if clean else 'DIRTY',
    }


def role_timeout_record(route='agy', error_code='AGY_TIMEOUT', **changes):
    record = {
        'task_id': '1' * 32,
        'run_id': '2' * 32,
        'developer': {
            'state': 'DEVELOPER_DISPATCHING',
            'provider_profile': route,
            'error_code': error_code,
            'source_sha': HEAD,
            'assignment_id': '3' * 32,
            'session_id': 'session-1',
            'executor_task_id': 'executor-1',
            'read_paths': ['src/module.py'],
            'write_paths': ['src/module.py'],
            'acceptance': ['test acceptance'],
            'execution': {
                'status': 'TIMEOUT',
                'cleanup_status': 'CONFIRMED',
                'diff_base_sha': HEAD,
                'diff_sha256': EMPTY_DIFF_SHA256,
            },
        },
        'dispatch_audit': [],
        'review_dispatch_claim': [],
        'review_repair_claims': [],
        'completion_events': [],
        'remote_test_outcomes': [],
        'remote_test_receipts': [],
    }
    record.update(changes)
    return record


class MockStore:
    def __init__(self, record):
        self.record = record
        self.updates = []

    def get(self, _task_id):
        return self.record

    def compare_and_update(self, _task_id, expected=None, **changes):
        self.updates.append((expected, changes))
        self.record.update(changes)
        return self.record

    def append_dispatch_audit(self, _task_id, entry, expected=None):
        self.record.setdefault('dispatch_audit', []).append(entry)
        return self.record


class TaskAdvanceRoleTimeoutTests(unittest.TestCase):
    def setUp(self):
        install_trust_fixture(self)
        self.policy_path = install_worker_policy(self)

    # -------------------------------------------------------------------------
    # Test 25: Without the flag, each classifier outcome maps to the step and
    # code in section 3.2, and only LIMIT changes Task state (to STOPPED).
    # -------------------------------------------------------------------------

    def test_25_no_flag_retry_ready_step_and_code(self):
        record = role_timeout_record('agy', 'AGY_TIMEOUT', state='PM_PENDING')
        store = MockStore(record)
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()):
            result = task_advance.advance(task_id=record['task_id'], store=store)

        self.assertEqual(result['step'], 'DEVELOPER_TIMEOUT')
        self.assertEqual(result['codes'], ['ROLE_TIMEOUT_RETRY_READY'])
        self.assertEqual(result['next'], 'DEVELOPER_TIMEOUT')
        self.assertEqual(record['state'], 'PM_PENDING')  # state unchanged

    def test_25_no_flag_limit_moves_task_to_stopped(self):
        # Limit 1, but one attempt already in audit (qualifying transition + adapter)
        attempt_id = '4' * 32
        transition = {
            'action': 'ROLE_TIMEOUT_RETRY', 'attempt_id': attempt_id,
            'assignment_id': '3' * 32, 'source_sha': HEAD,
            'contract_digest': 'c' * 64, 'worker_key': 'agy',
            'decision': 'ALLOWED', 'retry_phase': 'TRANSITION',
        }
        adapter = {
            'action': 'ROLE_TIMEOUT_RETRY', 'attempt_id': '5' * 32,
            'authorization_id': attempt_id, 'assignment_id': '3' * 32,
            'source_sha': HEAD, 'contract_digest': 'c' * 64,
            'worker_key': 'agy', 'decision': 'ALLOWED', 'retry_phase': 'ADAPTER',
        }
        record = role_timeout_record('agy', 'AGY_TIMEOUT', state='PM_PENDING',
                                     dispatch_audit=[transition, adapter])
        store = MockStore(record)

        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(repair_policy, 'owner_notice') as notice_mock:
            result = task_advance.advance(task_id=record['task_id'], store=store)

        self.assertEqual(result['step'], 'STOPPED')
        self.assertEqual(result['codes'], ['ROLE_TIMEOUT_LIMIT'])
        self.assertEqual(result['next'], 'STOPPED')
        self.assertEqual(record['state'], 'STOPPED')  # moved to STOPPED
        self.assertEqual(record['error_code'], 'ROLE_TIMEOUT_LIMIT')
        notice_mock.assert_called_once_with('STOPPED')

    def test_25_no_flag_limit_zero_moves_task_to_stopped(self):
        record = role_timeout_record('agy', 'AGY_TIMEOUT', state='PM_PENDING')
        store = MockStore(record)
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(worker_dispatch, 'timeout_continuation_max', return_value=0), \
             patch.object(repair_policy, 'owner_notice') as notice_mock:
            result = task_advance.advance(task_id=record['task_id'], store=store)

        self.assertEqual(result['step'], 'STOPPED')
        self.assertEqual(result['codes'], ['ROLE_TIMEOUT_LIMIT'])
        self.assertEqual(record['state'], 'STOPPED')
        notice_mock.assert_called_once_with('STOPPED')

    def test_25_no_flag_write_ambiguous_dirty_d6(self):
        record = role_timeout_record('agy', 'AGY_TIMEOUT', state='PM_PENDING')
        store = MockStore(record)
        with patch.object(task_advance, '_d6_snapshot', return_value=d6(clean=False)):
            result = task_advance.advance(task_id=record['task_id'], store=store)

        self.assertEqual(result['step'], 'DEVELOPER_ATTENTION')
        self.assertEqual(result['codes'], ['ROLE_TIMEOUT_WRITE_AMBIGUOUS'])
        self.assertEqual(record['state'], 'PM_PENDING')  # state unchanged

    def test_25_no_flag_write_ambiguous_non_empty_diff(self):
        record = role_timeout_record('agy', 'AGY_TIMEOUT', state='PM_PENDING')
        record['developer']['execution']['diff_sha256'] = 'f' * 64
        store = MockStore(record)
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()):
            result = task_advance.advance(task_id=record['task_id'], store=store)

        self.assertEqual(result['step'], 'DEVELOPER_ATTENTION')
        self.assertEqual(result['codes'], ['ROLE_TIMEOUT_WRITE_AMBIGUOUS'])
        self.assertEqual(record['state'], 'PM_PENDING')

    def test_25_no_flag_write_ambiguous_cleanup_not_confirmed(self):
        record = role_timeout_record('agy', 'AGY_TIMEOUT', state='PM_PENDING')
        record['developer']['execution']['cleanup_status'] = 'PENDING'
        store = MockStore(record)
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()):
            result = task_advance.advance(task_id=record['task_id'], store=store)

        self.assertEqual(result['step'], 'DEVELOPER_ATTENTION')
        self.assertEqual(result['codes'], ['ROLE_TIMEOUT_WRITE_AMBIGUOUS'])
        self.assertEqual(record['state'], 'PM_PENDING')

    def test_25_no_flag_write_ambiguous_diff_base_sha_mismatch(self):
        record = role_timeout_record('agy', 'AGY_TIMEOUT', state='PM_PENDING')
        record['developer']['execution']['diff_base_sha'] = 'b' * 40
        store = MockStore(record)
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()):
            result = task_advance.advance(task_id=record['task_id'], store=store)

        self.assertEqual(result['step'], 'DEVELOPER_ATTENTION')
        self.assertEqual(result['codes'], ['ROLE_TIMEOUT_WRITE_AMBIGUOUS'])
        self.assertEqual(record['state'], 'PM_PENDING')

    def test_25_no_flag_write_ambiguous_d6_head_moved(self):
        record = role_timeout_record('agy', 'AGY_TIMEOUT', state='PM_PENDING')
        store = MockStore(record)
        with patch.object(task_advance, '_d6_snapshot', return_value=d6(head='c' * 40)):
            result = task_advance.advance(task_id=record['task_id'], store=store)

        self.assertEqual(result['step'], 'DEVELOPER_ATTENTION')
        self.assertEqual(result['codes'], ['ROLE_TIMEOUT_WRITE_AMBIGUOUS'])
        self.assertEqual(record['state'], 'PM_PENDING')

    def test_25_no_flag_write_ambiguous_failed_d6_read(self):
        record = role_timeout_record('agy', 'AGY_TIMEOUT', state='PM_PENDING')
        store = MockStore(record)
        with patch.object(task_advance, '_d6_snapshot', return_value=None):
            result = task_advance.advance(task_id=record['task_id'], store=store)

        self.assertEqual(result['step'], 'DEVELOPER_ATTENTION')
        self.assertEqual(result['codes'], ['ROLE_TIMEOUT_WRITE_AMBIGUOUS'])
        self.assertEqual(record['state'], 'PM_PENDING')

    def test_25_no_flag_write_ambiguous_result_head_in_completion(self):
        record = role_timeout_record('agy', 'AGY_TIMEOUT', state='PM_PENDING',
                                     completion={'evidence': {'result_head': HEAD}})
        store = MockStore(record)
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()):
            result = task_advance.advance(task_id=record['task_id'], store=store)

        self.assertEqual(result['step'], 'DEVELOPER_ATTENTION')
        self.assertEqual(result['codes'], ['ROLE_TIMEOUT_WRITE_AMBIGUOUS'])
        self.assertEqual(record['state'], 'PM_PENDING')

    def test_25_no_flag_write_ambiguous_committed_result_carry(self):
        carry = {'action': 'COMMITTED_RESULT_CARRY', 'decision': 'ALLOWED',
                 'assignment_id': '3' * 32}
        record = role_timeout_record('agy', 'AGY_TIMEOUT', state='PM_PENDING',
                                     dispatch_audit=[carry])
        store = MockStore(record)
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()):
            result = task_advance.advance(task_id=record['task_id'], store=store)

        self.assertEqual(result['step'], 'DEVELOPER_ATTENTION')
        self.assertEqual(result['codes'], ['ROLE_TIMEOUT_WRITE_AMBIGUOUS'])
        self.assertEqual(record['state'], 'PM_PENDING')

    def test_25_no_flag_policy_invalid(self):
        record = role_timeout_record('agy', 'AGY_TIMEOUT', state='PM_PENDING')
        store = MockStore(record)
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(worker_dispatch, 'timeout_continuation_max', return_value=None):
            result = task_advance.advance(task_id=record['task_id'], store=store)

        self.assertEqual(result['step'], 'REFUSED')
        self.assertEqual(result['codes'], ['ROLE_TIMEOUT_POLICY_INVALID'])
        self.assertEqual(record['state'], 'PM_PENDING')

    def test_25_all_four_routes_recognized(self):
        routes = (
            ('claude', 'CLAUDE_TIMEOUT'),
            ('agy', 'AGY_TIMEOUT'),
            ('opencode', 'OPENCODE_TIMEOUT'),
            ('gw-agent', 'GW_AGENT_TIMEOUT'),
        )
        for route, code in routes:
            with self.subTest(route=route, code=code):
                record = role_timeout_record(route, code, state='PM_PENDING')
                store = MockStore(record)
                with patch.object(task_advance, '_d6_snapshot', return_value=d6()):
                    result = task_advance.advance(task_id=record['task_id'], store=store)
                self.assertEqual(result['step'], 'DEVELOPER_TIMEOUT')
                self.assertEqual(result['codes'], ['ROLE_TIMEOUT_RETRY_READY'])

    def test_25_non_timeout_codes_do_not_trigger_role_timeout(self):
        non_timeout_cases = (
            ('claude', 'CLAUDE_QUOTA_EXHAUSTED'),
            ('opencode', 'OPENCODE_STEPS_EXHAUSTED'),
            ('claude', 'AGY_TIMEOUT'),  # wrong route code
            ('codex', 'CODEX_TIMEOUT_WITH_CHANGES'),
            ('agy', 'ROUTE_ERROR'),
        )
        for route, code in non_timeout_cases:
            with self.subTest(route=route, code=code):
                record = role_timeout_record(route, code, state='DEVELOPER_OUTCOME_UNKNOWN')
                store = MockStore(record)
                with patch.object(task_advance, '_d6_snapshot', return_value=d6()):
                    result = task_advance.advance(task_id=record['task_id'], store=store)
                self.assertEqual(result['step'], 'DEVELOPER_ATTENTION')
                self.assertNotIn('ROLE_TIMEOUT_RETRY_READY', result['codes'])

    # -------------------------------------------------------------------------
    # Test 26: LIMIT produces the D4 notice through the existing path, once.
    # -------------------------------------------------------------------------

    def test_26_limit_produces_d4_owner_notice_once(self):
        record = role_timeout_record('agy', 'AGY_TIMEOUT', state='PM_PENDING')
        store = MockStore(record)
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(worker_dispatch, 'timeout_continuation_max', return_value=0), \
             patch.object(repair_policy, 'owner_notice') as notice_mock:
            first = task_advance.advance(task_id=record['task_id'], store=store)

        self.assertEqual(first['step'], 'STOPPED')
        self.assertEqual(first['codes'], ['ROLE_TIMEOUT_LIMIT'])
        self.assertEqual(record['state'], 'STOPPED')
        notice_mock.assert_called_once_with('STOPPED')

        # A second advance on the STOPPED task refuses before D4 notice
        notice_mock.reset_mock()
        second = task_advance.advance(task_id=record['task_id'], store=store)
        self.assertEqual(second['step'], 'REFUSED')
        notice_mock.assert_not_called()

    # -------------------------------------------------------------------------
    # Test 27: With the flag, a quota FAIL or OWNER_PAUSED pre-check returns
    # REFUSED with that code and writes no audit entry.
    # -------------------------------------------------------------------------

    def test_27_paused_worker_precheck_refuses_without_audit_write(self):
        # Pause agy worker
        policy_data = json.loads(Path(self.policy_path).read_text(encoding='utf-8'))
        set_dispatch(policy_data, 'agy', 'PAUSED')
        Path(self.policy_path).write_text(json.dumps(policy_data), encoding='utf-8')

        record = role_timeout_record('agy', 'AGY_TIMEOUT', state='PM_PENDING')
        store = MockStore(record)
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()):
            result = task_advance.advance(task_id=record['task_id'], store=store,
                                          timeout_retry=True)

        self.assertEqual(result['step'], 'REFUSED')
        self.assertEqual(result['codes'], ['OWNER_PAUSED'])
        self.assertEqual(record['dispatch_audit'], [])  # writes nothing
        self.assertEqual(store.updates, [])

    def test_27_quota_fail_precheck_refuses_without_audit_write(self):
        record = role_timeout_record('agy', 'AGY_TIMEOUT', state='PM_PENDING')
        store = MockStore(record)
        quota_gate = lambda worker_key, **_: 'QUOTA_FAIL'
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()):
            result = task_advance.advance(task_id=record['task_id'], store=store,
                                          timeout_retry=True, quota_gate=quota_gate)

        self.assertEqual(result['step'], 'REFUSED')
        self.assertEqual(result['codes'], ['QUOTA_FAIL'])
        self.assertEqual(record['dispatch_audit'], [])  # writes nothing
        self.assertEqual(store.updates, [])

    # -------------------------------------------------------------------------
    # Test 28: With the flag, the retry does not change repair, review FAIL,
    # test failure or Debug counts.
    # -------------------------------------------------------------------------

    def test_28_with_flag_retry_preserves_counts_and_lineage(self):
        claims = [{'head_sha': HEAD, 'state': 'DISPATCHED', 'repair_task_id': 'repair-1'}]
        events = [{'source': 'REVIEWER', 'outcome': 'REVIEW_FAIL', 'source_sha': HEAD, 'version': 1}]
        receipts = [{'result': 'FAIL', 'source_sha': HEAD}]
        lineage = {'state': 'OPEN', 'kind': 'child', 'parent': 'p' * 32}

        record = role_timeout_record(
            'agy', 'AGY_TIMEOUT', state='PM_PENDING',
            review_repair_claims=list(claims),
            completion_events=list(events),
            remote_test_receipts=list(receipts),
            lineage=dict(lineage),
            task_binding={'contract_digest': 'c' * 64},
        )
        store = MockStore(record)

        launched = []
        launch_call = lambda **kwargs: launched.append(kwargs)

        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(worker_dispatch, 'authorize_dispatch') as auth_dispatch:
            result = task_advance.advance(task_id=record['task_id'], store=store,
                                          timeout_retry=True,
                                          launch_developer_call=launch_call)

        self.assertEqual(result['step'], 'WAITING_EXECUTOR')
        self.assertEqual(result['codes'], [])
        self.assertEqual(record['developer']['state'], 'DEVELOPER_DISPATCHING')

        # Repair claims, review events, remote test receipts, and lineage are unchanged
        self.assertEqual(record['review_repair_claims'], claims)
        self.assertEqual(record['completion_events'], events)
        self.assertEqual(record['remote_test_receipts'], receipts)
        self.assertEqual(record['lineage'], lineage)

        auth_dispatch.assert_called_once_with(store, record['task_id'], 'agy',
                                              action='ROLE_TIMEOUT_RETRY')
        self.assertEqual(len(launched), 1)

    # -------------------------------------------------------------------------
    # Test 29: An unknown transition or launch outcome returns
    # ROLE_TIMEOUT_RETRY_OUTCOME_UNKNOWN, and a second call does not replay.
    # -------------------------------------------------------------------------

    def test_29_unknown_transition_outcome_does_not_replay(self):
        record = role_timeout_record('agy', 'AGY_TIMEOUT', state='PM_PENDING')

        class FailingStore(MockStore):
            def compare_and_update(self, task_id, expected=None, **changes):
                raise RuntimeError('transition outcome unknown')

        store = FailingStore(record)
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()):
            first = task_advance.advance(task_id=record['task_id'], store=store,
                                         timeout_retry=True)

        self.assertEqual(first['step'], 'ROLE_TIMEOUT_RETRY_OUTCOME_UNKNOWN')
        self.assertEqual(first['codes'], ['ROLE_TIMEOUT_RETRY_OUTCOME_UNKNOWN'])

        # Second call with flag does not replay
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()):
            second = task_advance.advance(task_id=record['task_id'], store=store,
                                          timeout_retry=True)
        self.assertNotEqual(second['step'], 'WAITING_EXECUTOR')
        self.assertEqual(second['step'], 'REFUSED')
        self.assertEqual(second['codes'], ['ROLE_TIMEOUT_NOT_TIMEOUT'])

    def test_29_unknown_launch_outcome_does_not_replay(self):
        record = role_timeout_record('agy', 'AGY_TIMEOUT', state='PM_PENDING')
        store = MockStore(record)

        def failing_launch(**_kwargs):
            raise RuntimeError('launch failed ambiguously')

        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(worker_dispatch, 'authorize_dispatch'):
            first = task_advance.advance(task_id=record['task_id'], store=store,
                                         timeout_retry=True,
                                         launch_developer_call=failing_launch)

        self.assertEqual(first['step'], 'ROLE_TIMEOUT_RETRY_OUTCOME_UNKNOWN')
        self.assertEqual(first['codes'], ['ROLE_TIMEOUT_RETRY_OUTCOME_UNKNOWN'])
        self.assertEqual(record['developer']['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(record['developer']['error_code'],
                         'ROLE_TIMEOUT_RETRY_OUTCOME_UNKNOWN')

        # Second call does not replay
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()):
            second = task_advance.advance(task_id=record['task_id'], store=store,
                                          timeout_retry=True)
        self.assertNotEqual(second['step'], 'WAITING_EXECUTOR')
        self.assertEqual(second['step'], 'REFUSED')
        self.assertEqual(second['codes'], ['ROLE_TIMEOUT_NOT_TIMEOUT'])


class TaskAdvanceRoleTimeoutRealStoreTests(unittest.TestCase):
    """End-to-end advance step with real TaskStore and real worker_dispatch gates."""

    def setUp(self):
        install_trust_fixture(self)
        self.policy_path = install_worker_policy(self)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = TaskStore(Path(temp.name).resolve() / 'tasks')

    def _create_timeout_task(self, route='agy', code='AGY_TIMEOUT'):
        from llmtool.task_store import new_id
        task_id = new_id()
        run_id = new_id()
        contract_digest = hashlib.sha256(b'contract').hexdigest()
        request = 'Test role timeout task.'
        request_sha = hashlib.sha256(request.encode()).hexdigest()
        task = make_task_record(
            task_id, run_id, 'pm-' + new_id(), 'pm-profile', request,
            state='PM_RESPONDED', provider_type='openai-compatible', model='test-model',
            backend='NO_NATIVE_SESSION_ID_EXPOSED',
            request_sha=request_sha,
        )
        task.update(
            pm_route_binding={'type': 'openai-compatible', 'model': 'test-model', 'base_url': None},
            response='PM returned a bounded task.',
            error_code=None,
            pm_assignment={'schema_version': 1, 'route': route, 'risk': 'LOW',
                           'goal': request, 'acceptance': ['test acceptance']},
            task_binding={'contract_digest': contract_digest},
        )
        self.store.create(task)

        developer = {
            'assignment_id': new_id(),
            'selection_source': 'PM',
            'state': 'DEVELOPER_PENDING',
            'provider_profile': route,
            'provider_type': 'agy-cli' if route == 'agy' else None,
            'model': None,
            'backend_session_semantics': 'CLI_REPORTED_SESSION_ID' if route == 'agy' else 'NO_NATIVE_SESSION_ID_EXPOSED',
            'session_id': 'dev-' + new_id(),
            'source_sha': HEAD,
            'source_sha_binding': 'D6_REGISTRY_OBSERVED',
            'read_paths': ['src/module.py'],
            'write_paths': ['src/module.py'],
            'acceptance': ['test acceptance'],
            'context_sha256': hashlib.sha256(b'context').hexdigest(),
            'egress_basis': 'Approved non-sensitive test context.',
            'input_bytes': 10,
            'max_tokens': None,
            'artifact_format': 'unified_diff',
            'artifact': None,
            'artifact_sha256': None,
            'usage': None,
            'error_code': code,
            'created_at': '2026-10-23T00:00:00Z',
            'executor_task_id': 'executor-1',
            'execution': {
                'status': 'TIMEOUT',
                'native_session_id': None,
                'process_exit_code': None,
                'stdout_sha256': None,
                'elapsed_ms': None,
                'cleanup_status': 'CONFIRMED',
                'usage_source': 'UNKNOWN',
                'source_bytes': None,
                'source_file_count': None,
                'source_manifest_sha256': None,
                'diff_base_sha': HEAD,
                'diff_sha256': EMPTY_DIFF_SHA256,
            },
        }
        if route in ('agy', 'claude'):
            developer['owner_assignment_ref'] = None

        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            self.store.compare_and_update(task_id, expected={'developer': None}, developer=developer)
        return task_id

    def test_real_store_timeout_retry_transition_and_audit(self):
        task_id = self._create_timeout_task('agy', 'AGY_TIMEOUT')

        # First call without flag reports RETRY_READY
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()):
            adv1 = task_advance.advance(task_id=task_id, store=self.store)
        self.assertEqual(adv1['step'], 'DEVELOPER_TIMEOUT')
        self.assertEqual(adv1['codes'], ['ROLE_TIMEOUT_RETRY_READY'])

        # Advance with timeout_retry=True executes retry through real gates
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()):
            retry_res = task_advance.advance(task_id=task_id, store=self.store,
                                             timeout_retry=True)
        self.assertEqual(retry_res['step'], 'WAITING_EXECUTOR')
        self.assertEqual(retry_res['codes'], [])

        stored = self.store.get(task_id)
        self.assertEqual(stored['developer']['state'], 'DEVELOPER_DISPATCHING')
        audit = stored.get('dispatch_audit', [])
        self.assertEqual(len(audit), 2)
        self.assertEqual(audit[0]['action'], 'ROLE_TIMEOUT_RETRY')
        self.assertEqual(audit[0]['retry_phase'], 'TRANSITION')
        self.assertEqual(audit[0]['decision'], 'ALLOWED')
        self.assertEqual(audit[1]['action'], 'ROLE_TIMEOUT_RETRY')
        self.assertEqual(audit[1]['retry_phase'], 'ADAPTER')
        self.assertEqual(audit[1]['decision'], 'ALLOWED')

        # Logical attempts count is 1
        attempts = role_timeout.count_timeout_attempts(audit)
        self.assertEqual(attempts, 1)

        # Now suppose it times out again; with default limit 1, it moves to STOPPED
        stored['developer']['state'] = 'DEVELOPER_DISPATCHING'
        stored['developer']['error_code'] = 'AGY_TIMEOUT'
        stored['developer']['execution'] = {
            'status': 'TIMEOUT',
            'cleanup_status': 'CONFIRMED',
            'diff_base_sha': HEAD,
            'diff_sha256': EMPTY_DIFF_SHA256,
        }
        self.store.compare_and_update(task_id, expected={'developer': stored['developer']},
                                      developer=stored['developer'])

        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(repair_policy, 'owner_notice') as notice_mock:
            adv2 = task_advance.advance(task_id=task_id, store=self.store)

        self.assertEqual(adv2['step'], 'STOPPED')
        self.assertEqual(adv2['codes'], ['ROLE_TIMEOUT_LIMIT'])
        notice_mock.assert_called_once_with('STOPPED')

        final_stored = self.store.get(task_id)
        self.assertEqual(final_stored['state'], 'STOPPED')
        self.assertEqual(final_stored['error_code'], 'ROLE_TIMEOUT_LIMIT')


if __name__ == '__main__':
    unittest.main()
