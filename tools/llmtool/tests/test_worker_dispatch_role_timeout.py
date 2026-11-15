"""Dispatch-gate tests for ROLE_TIMEOUT_RETRY (#1019 A11 row 41 PR 1b).

Design: docs/design/a11-row41-timeout-retry-1019.md section 6, tests 13-24.
Fixed, made-up values only: repeated-digit hex ids, route names and plain
words. No provider calls. The Owner policy decision (worker_dispatch.evaluate)
and the repair policy loader are injected; the gate code under test is real.
The adapter store keeps its record in a temporary JSON file.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from llmtool import repair_policy, role_timeout, worker_dispatch
from llmtool.errors import LLMToolError
from llmtool.worker_dispatch import PolicyDecision, WorkerDispatchDenied

TASK_ID = '1' * 32
RUN_ID = '0' * 32
ASSIGNMENT = '2' * 32
OTHER_ASSIGNMENT = '3' * 32
SOURCE_SHA = '4' * 40
OTHER_SHA = '9' * 40
CONTRACT = '5' * 64
NON_EMPTY_DIFF = '6' * 64
RESULT_HEAD = '7' * 40
EXECUTOR = 'a11r41-fixed-d6'
EMPTY_DIFF = hashlib.sha256(b'').hexdigest()
ACTION = 'ROLE_TIMEOUT_RETRY'
WORKER_KEYS = {
    'claude': 'claude-developer',
    'agy': 'agy',
    'opencode': 'opencode-developer',
    'gw-agent': 'gw-agent',
}
PROVIDER_TYPES = {
    'claude': 'claude-cli',
    'agy': 'agy-cli',
    'opencode': 'opencode-cli',
    'gw-agent': 'gw-agent-cli',
    'codex': 'codex-cli',
}
ALLOWED_REASONS = ('OWNER_POLICY_ALLOWED', 'OWNER_ASSIGNMENT_MATCHED')


def developer(route='claude', state='DEVELOPER_OUTCOME_UNKNOWN', error_code=None,
              assignment=ASSIGNMENT):
    """A Developer record that ended in its route's exact timeout, no write."""
    return {
        'assignment_id': assignment,
        'selection_source': 'OWNER_ASSIGNMENT',
        'provider_profile': route,
        'provider_type': PROVIDER_TYPES[route],
        'model': 'fixed-model',
        'backend_session_semantics': 'NONE',
        'session_id': 'session-one',
        'source_sha': SOURCE_SHA,
        'source_sha_binding': 'D6_HEAD',
        'owner_assignment_ref': None,
        'executor_task_id': EXECUTOR,
        'read_paths': ['docs/read-one.md'],
        'write_paths': ['docs/write-one.md'],
        'acceptance': ['fixed acceptance item'],
        'state': state,
        'error_code': (role_timeout.ROUTE_TIMEOUT_CODES[route]
                       if error_code is None else error_code),
        'execution': {
            'status': 'TIMEOUT',
            'cleanup_status': 'CONFIRMED',
            'diff_base_sha': SOURCE_SHA,
            'diff_sha256': EMPTY_DIFF,
        },
    }


def dispatching(old):
    new = copy.deepcopy(old)
    new['state'] = 'DEVELOPER_DISPATCHING'
    new['error_code'] = None
    new['execution'] = None
    return new


def record(dev, audit=None):
    return {
        'task_id': TASK_ID,
        'run_id': RUN_ID,
        'task_binding': {'contract_digest': CONTRACT},
        'developer': dev,
        'dispatch_audit': list(audit or []),
        'completion': None,
    }


def binding(assignment=ASSIGNMENT):
    return {'assignment_id': assignment, 'source_sha': SOURCE_SHA,
            'contract_digest': CONTRACT}


def allowed(worker_key):
    return PolicyDecision(worker_key, True, 'OWNER_POLICY_ALLOWED', None)


def blocked(worker_key, code):
    return PolicyDecision(worker_key, False, code, None)


def role_entry(worker_key='claude-developer', phase='TRANSITION', decision=None,
               assignment=ASSIGNMENT, authorization_id=None):
    return worker_dispatch.audit_entry(
        decision or allowed(worker_key), ACTION, binding=binding(assignment),
        authorization_id=authorization_id, retry_phase=phase)


def codex_entry(authorization_id=None, assignment=OTHER_ASSIGNMENT):
    return worker_dispatch.audit_entry(
        allowed('codex-developer'), 'TIMEOUT_CONTINUATION', binding=binding(assignment),
        authorization_id=authorization_id)


def adapter_for(transition, worker_key='claude-developer', bind=None, decision=None):
    """A ROLE_TIMEOUT_RETRY ADAPTER entry naming transition's attempt_id."""
    return worker_dispatch.audit_entry(
        decision or allowed(worker_key), ACTION, binding=bind or binding(),
        authorization_id=transition['attempt_id'], retry_phase='ADAPTER')


def genuine_pair():
    """An ALLOWED transition and its bound ADAPTER entry: one logical attempt."""
    transition = role_entry()
    return [transition, adapter_for(transition)]


def codex_pair():
    """A Codex continuation transition and its bound adapter entry."""
    transition = codex_entry()
    return [transition, codex_entry(authorization_id=transition['attempt_id'])]


def codex_continuation(audit=None):
    """A valid Codex CODEX_TIMEOUT_WITH_CHANGES continuation (existing path)."""
    old = {
        'assignment_id': ASSIGNMENT,
        'selection_source': 'OWNER_ASSIGNMENT',
        'provider_profile': 'codex',
        'provider_type': 'codex-cli',
        'model': 'fixed-model',
        'backend_session_semantics': 'NONE',
        'session_id': 'session-one',
        'source_sha': SOURCE_SHA,
        'source_sha_binding': 'D6_HEAD',
        'owner_assignment_ref': None,
        'executor_task_id': EXECUTOR,
        'read_paths': ['docs/read-one.md'],
        'write_paths': ['docs/write-one.md'],
        'acceptance': ['fixed acceptance item'],
        'state': 'DEVELOPER_OUTCOME_UNKNOWN',
        'error_code': 'CODEX_TIMEOUT_WITH_CHANGES',
        'execution': {'status': 'TIMEOUT', 'diff_base_sha': SOURCE_SHA,
                      'diff_sha256': NON_EMPTY_DIFF},
    }
    new = copy.deepcopy(old)
    new.update({'assignment_id': OTHER_ASSIGNMENT, 'state': 'DEVELOPER_DISPATCHING',
                'error_code': None, 'execution': None})
    current = record(old, audit)
    changes = {'developer': new,
               'completion': {'executor_task_id': EXECUTOR,
                              'evidence': {'result_head': RESULT_HEAD}}}
    return current, changes, {'developer': copy.deepcopy(old)}


class TempFileStore:
    """Minimal adapter store: get and append_dispatch_audit on a temp JSON file."""

    def __init__(self, directory, current):
        self.path = Path(directory) / 'task-record.json'
        self.put(current)

    def put(self, current):
        self.path.write_text(json.dumps(current, sort_keys=True), encoding='utf-8')

    def get(self, task_id):
        current = json.loads(self.path.read_text(encoding='utf-8'))
        if current.get('task_id') != task_id:
            raise KeyError('unknown task')
        return current

    def append_dispatch_audit(self, task_id, entry, expected):
        current = self.get(task_id)
        if expected != {'developer': current.get('developer'),
                        'dispatch_audit': current.get('dispatch_audit')}:
            raise RuntimeError('stale expected record')
        current['dispatch_audit'] = list(current.get('dispatch_audit', [])) + [entry]
        self.put(current)
        return current


class RoleTimeoutGateBase(unittest.TestCase):
    def setUp(self):
        self.reason = 'OWNER_POLICY_ALLOWED'
        self.policy = {}
        self.policy_unreadable = False

        def fake_evaluate(worker_key, owner_assignment_ref=None, task_id=None):
            return PolicyDecision(worker_key, self.reason in ALLOWED_REASONS,
                                  self.reason, None)

        def fake_load_policy():
            if self.policy_unreadable:
                raise LLMToolError('synthetic unreadable policy')
            return dict(self.policy)

        for patcher in (mock.patch.object(worker_dispatch, 'evaluate', fake_evaluate),
                        mock.patch.object(repair_policy, 'load_policy', fake_load_policy)):
            patcher.start()
            self.addCleanup(patcher.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmpdir = tmp.name

    def set_limit(self, limit):
        self.policy = {'timeout_continuation_max': limit}

    def transition(self, current, new=None, expected='default', changes_extra=None):
        old = current['developer']
        new = dispatching(old) if new is None else new
        changes = {'developer': new}
        changes.update(changes_extra or {})
        if expected == 'default':
            expected = {'developer': copy.deepcopy(old)}
        result = worker_dispatch.authorize_task_store_change(current, changes, expected)
        return result, new

    def assert_refused(self, result, code):
        self.assertIsInstance(result, WorkerDispatchDenied)
        self.assertEqual(result.dispatch_code, code)

    def full_retry(self, store, route='claude'):
        """One allowed transition plus its allowed adapter launch, then a new timeout."""
        current = store.get(TASK_ID)
        result, new = self.transition(current)
        self.assertIsNone(result)
        current['developer'] = new
        store.put(current)
        decision = worker_dispatch.authorize_dispatch(
            store, TASK_ID, WORKER_KEYS[route], action=ACTION)
        self.assertTrue(decision.allowed)
        current = store.get(TASK_ID)
        current['developer'] = developer(route)
        store.put(current)

    def next_transition(self, store):
        current = store.get(TASK_ID)
        result, _ = self.transition(current)
        return result, current


class ClassificationTests(RoleTimeoutGateBase):
    """Test 13: classified ROLE_TIMEOUT_RETRY, not RETRY, for each route."""

    def test_each_route_timeout_is_role_timeout_retry(self):
        for route, worker_key in WORKER_KEYS.items():
            for state in ('DEVELOPER_OUTCOME_UNKNOWN', 'DEVELOPER_UNAVAILABLE'):
                with self.subTest(route=route, state=state):
                    old = developer(route, state=state)
                    attempts = worker_dispatch.task_store_attempts(
                        record(old), {'developer': dispatching(old)})
                    self.assertEqual(attempts, [(worker_key, ACTION)])

    def test_first_retry_allowed_at_limit_one_with_transition_entry(self):
        self.set_limit(1)
        current = record(developer())
        result, _ = self.transition(current)
        self.assertIsNone(result)
        self.assertEqual(len(current['dispatch_audit']), 1)
        entry = current['dispatch_audit'][0]
        self.assertEqual(entry['action'], ACTION)
        self.assertEqual(entry['decision'], 'ALLOWED')
        self.assertEqual(entry['retry_phase'], 'TRANSITION')
        self.assertNotIn('authorization_id', entry)
        self.assertEqual(entry['worker_key'], 'claude-developer')
        self.assertEqual(entry['assignment_id'], ASSIGNMENT)

    def test_default_limit_is_one_when_key_absent(self):
        self.policy = {}
        current = record(developer())
        self.assertIsNone(self.transition(current)[0])
        current['developer'] = developer()
        self.assert_refused(self.transition(current)[0], 'WORKER_ROLE_TIMEOUT_RETRY_LIMIT')

    def test_non_timeout_codes_stay_retry_and_are_refused(self):
        cases = (
            ('claude', 'AGY_TIMEOUT'),
            ('agy', 'CLAUDE_TIMEOUT'),
            ('claude', 'CLAUDE_QUOTA_EXHAUSTED'),
            ('opencode', 'OPENCODE_STEPS_EXHAUSTED'),
            ('claude', 'claude_timeout'),
            ('claude', 'CLAUDE_TIMEOUT_EXTRA'),
            ('gw-agent', 'ROUTE_ERROR'),
        )
        for route, code in cases:
            with self.subTest(route=route, code=code):
                current = record(developer(route, error_code=code))
                attempts = worker_dispatch.task_store_attempts(
                    current, {'developer': dispatching(current['developer'])})
                self.assertEqual(attempts, [(WORKER_KEYS[route], 'RETRY')])
                result, _ = self.transition(current)
                self.assert_refused(result, 'WORKER_REPLAY_FORBIDDEN')

    def test_codex_timeout_is_never_role_timeout_retry(self):
        current, changes, _ = codex_continuation()
        attempts = worker_dispatch.task_store_attempts(current, changes)
        self.assertEqual(attempts, [('codex-developer', 'TIMEOUT_CONTINUATION')])

    def test_pending_record_with_timeout_code_stays_dispatch(self):
        old = developer(state='DEVELOPER_PENDING')
        attempts = worker_dispatch.task_store_attempts(
            record(old), {'developer': dispatching(old)})
        self.assertEqual(attempts, [('claude-developer', 'DISPATCH')])


class LimitTests(RoleTimeoutGateBase):
    """Tests 14-16: the cap counts logical attempts per Task record."""

    def test_limit_zero_refuses_first(self):
        self.set_limit(0)
        result, _ = self.transition(record(developer()))
        self.assert_refused(result, 'WORKER_ROLE_TIMEOUT_RETRY_LIMIT')

    def test_limit_one_second_retry_refused(self):
        self.set_limit(1)
        store = TempFileStore(self.tmpdir, record(developer()))
        self.full_retry(store)
        result, current = self.next_transition(store)
        self.assert_refused(result, 'WORKER_ROLE_TIMEOUT_RETRY_LIMIT')
        last = current['dispatch_audit'][-1]
        self.assertEqual((last['decision'], last['retry_phase']), ('BLOCKED', 'TRANSITION'))

    def test_limit_one_counts_other_assignment_worker_and_blocked(self):
        self.set_limit(1)
        prior_cases = {
            'other assignment': role_entry(assignment=OTHER_ASSIGNMENT),
            'other worker key': role_entry(worker_key='agy'),
            'blocked transition': role_entry(
                decision=blocked('claude-developer', 'OWNER_PAUSED')),
        }
        for name, prior in prior_cases.items():
            with self.subTest(prior=name):
                result, _ = self.transition(record(developer(), [prior]))
                self.assert_refused(result, 'WORKER_ROLE_TIMEOUT_RETRY_LIMIT')

    def test_blocked_first_attempt_uses_budget(self):
        self.set_limit(1)
        current = record(developer())
        self.reason = 'OWNER_PAUSED'
        self.assert_refused(self.transition(current)[0], 'OWNER_PAUSED')
        self.reason = 'OWNER_POLICY_ALLOWED'
        self.assert_refused(self.transition(current)[0], 'WORKER_ROLE_TIMEOUT_RETRY_LIMIT')

    def _run_limit(self, limit):
        self.set_limit(limit)
        store = TempFileStore(self.tmpdir, record(developer()))
        for _ in range(limit):
            self.full_retry(store)
        audit = store.get(TASK_ID)['dispatch_audit']
        self.assertEqual(len(audit), 2 * limit)
        self.assertTrue(all(entry['decision'] == 'ALLOWED' for entry in audit))
        self.assertEqual([entry['retry_phase'] for entry in audit],
                         ['TRANSITION', 'ADAPTER'] * limit)
        self.assertEqual(role_timeout.count_timeout_attempts(audit), limit)
        result, _ = self.next_transition(store)
        self.assert_refused(result, 'WORKER_ROLE_TIMEOUT_RETRY_LIMIT')

    def test_limit_two_allows_two_full_retries(self):
        self._run_limit(2)

    def test_limit_three_allows_three_full_retries(self):
        self._run_limit(3)

    def test_other_routes_full_retry_then_limit(self):
        for route in ('agy', 'opencode', 'gw-agent'):
            with self.subTest(route=route):
                self.set_limit(1)
                store = TempFileStore(self.tmpdir, record(developer(route)))
                self.full_retry(store, route)
                result, _ = self.next_transition(store)
                self.assert_refused(result, 'WORKER_ROLE_TIMEOUT_RETRY_LIMIT')


class SharedCodexBudgetTests(RoleTimeoutGateBase):
    """Test 17 and 24: Codex continuation and role retry share one budget."""

    def test_codex_continuation_alone_still_allowed(self):
        self.set_limit(1)
        current, changes, expected = codex_continuation()
        self.assertIsNone(worker_dispatch.authorize_task_store_change(
            current, changes, expected))
        entry = current['dispatch_audit'][-1]
        self.assertEqual(entry['action'], 'TIMEOUT_CONTINUATION')
        self.assertNotIn('retry_phase', entry)

    def test_codex_attempt_then_role_retry(self):
        self.set_limit(1)
        result, _ = self.transition(record(developer(), [codex_entry()]))
        self.assert_refused(result, 'WORKER_ROLE_TIMEOUT_RETRY_LIMIT')
        self.set_limit(2)
        result, _ = self.transition(record(developer(), [codex_entry()]))
        self.assertIsNone(result)

    def test_role_retry_then_codex_attempt(self):
        self.set_limit(1)
        current, changes, expected = codex_continuation([role_entry()])
        self.assert_refused(
            worker_dispatch.authorize_task_store_change(current, changes, expected),
            'WORKER_TIMEOUT_CONTINUATION_LIMIT')
        self.set_limit(2)
        current, changes, expected = codex_continuation([role_entry()])
        self.assertIsNone(
            worker_dispatch.authorize_task_store_change(current, changes, expected))

    def test_codex_adapter_entry_is_not_a_second_attempt(self):
        # The adapter entry is bound to its transition (PR #2180 repair):
        # an unbound authorization_id is covered by AdapterValidationCountTests.
        self.set_limit(2)
        self.assertIsNone(self.transition(record(developer(), codex_pair()))[0])

    def test_codex_limit_and_policy_codes_unchanged(self):
        self.set_limit(1)
        current, changes, expected = codex_continuation([codex_entry()])
        self.assert_refused(
            worker_dispatch.authorize_task_store_change(current, changes, expected),
            'WORKER_TIMEOUT_CONTINUATION_LIMIT')
        self.policy_unreadable = True
        current, changes, expected = codex_continuation()
        self.assert_refused(
            worker_dispatch.authorize_task_store_change(current, changes, expected),
            'WORKER_TIMEOUT_CONTINUATION_POLICY_INVALID')


class PolicyAndPreconditionTests(RoleTimeoutGateBase):
    """Tests 18-21."""

    def test_unreadable_policy_fails_closed(self):
        self.policy_unreadable = True
        current = record(developer())
        result, _ = self.transition(current)
        self.assert_refused(result, 'WORKER_ROLE_TIMEOUT_RETRY_POLICY_INVALID')
        entry = current['dispatch_audit'][-1]
        self.assertEqual((entry['decision'], entry['retry_phase']), ('BLOCKED', 'TRANSITION'))

    def test_non_int_limit_fails_closed(self):
        for value in ('one', True, -1, 1.0):
            with self.subTest(value=value):
                self.policy = {'timeout_continuation_max': value}
                result, _ = self.transition(record(developer()))
                self.assert_refused(result, 'WORKER_ROLE_TIMEOUT_RETRY_POLICY_INVALID')

    def test_changed_identity_or_scope_field(self):
        changes = {
            'assignment_id': OTHER_ASSIGNMENT,
            'session_id': 'session-two',
            'source_sha': OTHER_SHA,
            'model': 'other-model',
            'executor_task_id': 'a11r41-other-d6',
            'read_paths': ['docs/read-two.md'],
            'write_paths': ['docs/write-two.md'],
            'acceptance': ['other acceptance item'],
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                current = record(developer())
                new = dispatching(current['developer'])
                new[field] = value
                self.assertEqual(
                    worker_dispatch.task_store_attempts(current, {'developer': new}),
                    [('claude-developer', ACTION)])
                result, _ = self.transition(current, new=new)
                self.assert_refused(result, 'WORKER_ROLE_TIMEOUT_RETRY_PRECONDITION')

    def test_unclean_timeout_evidence(self):
        def edit_execution(**values):
            def apply(dev):
                dev['execution'].update(values)
            return apply

        def drop_execution_field(name):
            def apply(dev):
                del dev['execution'][name]
            return apply

        cases = {
            'status not TIMEOUT': edit_execution(status='DONE'),
            'cleanup not confirmed': edit_execution(cleanup_status='PENDING'),
            'non-empty diff': edit_execution(diff_sha256=NON_EMPTY_DIFF),
            'diff base mismatch': edit_execution(diff_base_sha=OTHER_SHA),
            'diff digest missing': drop_execution_field('diff_sha256'),
            'diff base missing': drop_execution_field('diff_base_sha'),
            'cleanup missing': drop_execution_field('cleanup_status'),
            'execution missing': lambda dev: dev.update(execution=None),
            'carried result': lambda dev: dev.update(direct_result_callback={
                'evidence': {'carried_result': {'mode': 'NO_RUN_CHANGES'}}}),
        }
        for name, apply in cases.items():
            with self.subTest(case=name):
                old = developer()
                apply(old)
                result, _ = self.transition(record(old))
                self.assert_refused(result, 'WORKER_ROLE_TIMEOUT_RETRY_PRECONDITION')

    def test_result_head_or_committed_carry(self):
        current = record(developer())
        current['completion'] = {'executor_task_id': EXECUTOR,
                                 'evidence': {'result_head': RESULT_HEAD}}
        self.assert_refused(self.transition(current)[0],
                            'WORKER_ROLE_TIMEOUT_RETRY_PRECONDITION')
        current = record(developer())
        result, _ = self.transition(current, changes_extra={'completion': {
            'executor_task_id': EXECUTOR, 'evidence': {'result_head': RESULT_HEAD}}})
        self.assert_refused(result, 'WORKER_ROLE_TIMEOUT_RETRY_PRECONDITION')
        carry = worker_dispatch.audit_entry(
            allowed('claude-developer'), 'COMMITTED_RESULT_CARRY', binding=binding())
        result, _ = self.transition(record(developer(), [carry]))
        self.assert_refused(result, 'WORKER_ROLE_TIMEOUT_RETRY_PRECONDITION')

    def test_expected_record_must_match(self):
        for expected in (None, {}, {'developer': developer(error_code='ROUTE_ERROR')}):
            with self.subTest(expected=expected):
                result, _ = self.transition(record(developer()), expected=expected)
                self.assert_refused(result, 'WORKER_ROLE_TIMEOUT_RETRY_PRECONDITION')

    def test_owner_gate_codes_pass_through(self):
        for code in ('OWNER_PAUSED', 'OWNER_ASSIGNMENT_REQUIRED'):
            with self.subTest(code=code):
                self.reason = code
                current = record(developer())
                self.assert_refused(self.transition(current)[0], code)
                entry = current['dispatch_audit'][-1]
                self.assertEqual(entry['retry_phase'], 'TRANSITION')

    def test_recovery_of_role_timeout_is_refused(self):
        old = developer(state='DEVELOPER_UNAVAILABLE')
        new = copy.deepcopy(old)
        new.update({'state': 'DEVELOPER_PENDING', 'error_code': None, 'execution': None})
        current = record(old)
        self.assertEqual(worker_dispatch.task_store_attempts(current, {'developer': new}),
                         [('claude-developer', 'RECOVERY')])
        result, _ = self.transition(current, new=new)
        self.assert_refused(result, 'WORKER_ROLE_TIMEOUT_NOT_RECOVERY')
        self.assertNotIn('retry_phase', current['dispatch_audit'][-1])

    def test_recovery_of_other_unavailable_record_unchanged(self):
        old = developer(state='DEVELOPER_UNAVAILABLE', error_code='ROUTE_ERROR')
        new = copy.deepcopy(old)
        new.update({'state': 'DEVELOPER_PENDING', 'error_code': None, 'execution': None})
        current = record(old)
        self.assertIsNone(self.transition(current, new=new)[0])
        self.assertEqual(current['dispatch_audit'][-1]['action'], 'RECOVERY')


class AdapterGateTests(RoleTimeoutGateBase):
    """Tests 22-23 and the adapter half of the count rule."""

    def test_adapter_passes_once_then_consumed(self):
        store = TempFileStore(self.tmpdir, record(developer()))
        current = store.get(TASK_ID)
        result, new = self.transition(current)
        self.assertIsNone(result)
        transition_id = current['dispatch_audit'][-1]['attempt_id']
        current['developer'] = new
        store.put(current)

        decision = worker_dispatch.authorize_dispatch(
            store, TASK_ID, 'claude-developer', action=ACTION)
        self.assertTrue(decision.allowed)
        entry = store.get(TASK_ID)['dispatch_audit'][-1]
        self.assertEqual(entry['retry_phase'], 'ADAPTER')
        self.assertEqual(entry['authorization_id'], transition_id)
        self.assertEqual(entry['decision'], 'ALLOWED')

        with self.assertRaises(WorkerDispatchDenied) as raised:
            worker_dispatch.authorize_dispatch(store, TASK_ID, 'claude-developer',
                                               action=ACTION)
        self.assertEqual(raised.exception.dispatch_code,
                         'WORKER_DISPATCH_AUTHORIZATION_CONSUMED')
        audit = store.get(TASK_ID)['dispatch_audit']
        self.assertEqual((audit[-1]['decision'], audit[-1]['retry_phase']),
                         ('BLOCKED', 'ADAPTER'))
        self.assertEqual(role_timeout.count_timeout_attempts(audit), 1)

    def test_adapter_without_transition_is_refused_and_counts_zero(self):
        store = TempFileStore(self.tmpdir, record(dispatching(developer())))
        with self.assertRaises(WorkerDispatchDenied) as raised:
            worker_dispatch.authorize_dispatch(store, TASK_ID, 'claude-developer',
                                               action=ACTION)
        self.assertEqual(raised.exception.dispatch_code,
                         'WORKER_DISPATCH_TRANSITION_NOT_FOUND')
        audit = store.get(TASK_ID)['dispatch_audit']
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]['retry_phase'], 'ADAPTER')
        self.assertNotIn('authorization_id', audit[0])
        self.assertEqual(role_timeout.count_timeout_attempts(audit), 0)

    def test_paused_adapter_is_blocked_with_adapter_phase(self):
        store = TempFileStore(self.tmpdir, record(developer()))
        current = store.get(TASK_ID)
        result, new = self.transition(current)
        self.assertIsNone(result)
        current['developer'] = new
        store.put(current)
        self.reason = 'OWNER_PAUSED'
        with self.assertRaises(WorkerDispatchDenied) as raised:
            worker_dispatch.authorize_dispatch(store, TASK_ID, 'claude-developer',
                                               action=ACTION)
        self.assertEqual(raised.exception.dispatch_code, 'OWNER_PAUSED')
        audit = store.get(TASK_ID)['dispatch_audit']
        self.assertEqual(audit[-1]['retry_phase'], 'ADAPTER')
        self.assertEqual(role_timeout.count_timeout_attempts(audit), 1)

    def test_adapter_or_phaseless_entry_is_never_a_transition(self):
        adapter = role_entry(phase='ADAPTER')
        phaseless = dict(adapter)
        del phaseless['retry_phase']
        for name, entry in (('adapter', adapter), ('no phase', phaseless)):
            with self.subTest(entry=name):
                current = record(dispatching(developer()), [entry])
                self.assertIsNone(worker_dispatch._latest_transition_authorization(
                    current, 'claude-developer', ACTION, binding()))
                store = TempFileStore(self.tmpdir, current)
                with self.assertRaises(WorkerDispatchDenied) as raised:
                    worker_dispatch.authorize_dispatch(store, TASK_ID, 'claude-developer',
                                                       action=ACTION)
                self.assertEqual(raised.exception.dispatch_code,
                                 'WORKER_DISPATCH_TRANSITION_NOT_FOUND')
        transition = role_entry()
        current = record(dispatching(developer()), [transition])
        self.assertEqual(worker_dispatch._latest_transition_authorization(
            current, 'claude-developer', ACTION, binding()), transition['attempt_id'])


class AdapterValidationCountTests(RoleTimeoutGateBase):
    """PR #2180 repair (CWE-345): an adapter entry counts 0 only when it is bound
    to an earlier ALLOWED transition with the same authorization_id and binding;
    a forged, mis-bound or malformed one is a spent attempt."""

    LIMITS = (2, 3)

    def count(self, audit):
        return worker_dispatch._logical_timeout_attempts(audit)

    def pairs(self, number):
        audit = []
        for _ in range(number):
            audit.extend(genuine_pair())
        return audit

    def assert_cap(self, limit, audit, refused):
        self.set_limit(limit)
        current = record(developer(), audit)
        result, _ = self.transition(current)
        if refused:
            self.assert_refused(result, 'WORKER_ROLE_TIMEOUT_RETRY_LIMIT')
        else:
            self.assertIsNone(result)
        self.assertEqual(current['dispatch_audit'][-1]['retry_phase'], 'TRANSITION')

    def assert_spent(self, fragment, spent):
        """fragment counts spent; with genuine pairs filling the rest it hits the cap."""
        self.assertEqual(self.count(fragment), spent)
        for limit in self.LIMITS:
            with self.subTest(limit=limit):
                audit = self.pairs(limit - spent) + fragment
                self.assertEqual(self.count(audit), limit)
                self.assert_cap(limit, audit, refused=True)

    def test_genuine_pair_counts_once(self):
        self.assertEqual(self.count(genuine_pair()), 1)
        self.assertEqual(self.count(codex_pair()), 1)
        transition = role_entry()
        blocked_adapter = adapter_for(
            transition, decision=blocked('claude-developer', 'OWNER_PAUSED'))
        self.assertEqual(self.count([transition, blocked_adapter]), 1)
        consumed = adapter_for(transition, decision=blocked(
            'claude-developer', 'WORKER_DISPATCH_AUTHORIZATION_CONSUMED'))
        self.assertEqual(self.count([transition, adapter_for(transition), consumed]), 1)
        self.assertEqual(self.count(genuine_pair() + codex_pair()), 2)
        for limit in self.LIMITS:
            with self.subTest(limit=limit):
                self.assertEqual(self.count(self.pairs(limit - 1)), limit - 1)
                self.assert_cap(limit, self.pairs(limit - 1), refused=False)
                self.assert_cap(limit, self.pairs(limit), refused=True)

    def test_forged_adapter_without_transition_counts_spent(self):
        cases = {
            'role adapter, unknown id': role_entry(phase='ADAPTER', authorization_id='8' * 32),
            'role adapter, no id': role_entry(phase='ADAPTER'),
            'codex adapter, unknown id': codex_entry(authorization_id='8' * 32),
        }
        for name, forged in cases.items():
            with self.subTest(case=name):
                # role_timeout alone counts it 0; the dispatch gate does not trust it.
                self.assertEqual(role_timeout.count_timeout_attempts([forged]), 0)
                self.assert_spent([forged], 1)

    def test_forged_adapter_also_spends_codex_budget(self):
        self.set_limit(2)
        forged = role_entry(phase='ADAPTER', authorization_id='8' * 32)
        current, changes, expected = codex_continuation(genuine_pair() + [forged])
        self.assert_refused(
            worker_dispatch.authorize_task_store_change(current, changes, expected),
            'WORKER_TIMEOUT_CONTINUATION_LIMIT')
        current, changes, expected = codex_continuation(genuine_pair())
        self.assertIsNone(
            worker_dispatch.authorize_task_store_change(current, changes, expected))

    def test_mismatched_binding_counts_spent(self):
        other_source = {'assignment_id': ASSIGNMENT, 'source_sha': OTHER_SHA,
                        'contract_digest': CONTRACT}
        other_contract = {'assignment_id': ASSIGNMENT, 'source_sha': SOURCE_SHA,
                          'contract_digest': 'c' * 64}

        def bad_pair(**adapter_options):
            transition = role_entry()
            return [transition, adapter_for(transition, **adapter_options)]

        blocked_transition = role_entry(decision=blocked('claude-developer', 'OWNER_PAUSED'))
        reversed_transition = role_entry()
        codex_transition = codex_entry()
        cases = {
            'other assignment_id': bad_pair(bind=binding(OTHER_ASSIGNMENT)),
            'other source_sha': bad_pair(bind=other_source),
            'other contract_digest': bad_pair(bind=other_contract),
            'other worker key': bad_pair(worker_key='agy'),
            'transition not allowed': [blocked_transition, adapter_for(blocked_transition)],
            'adapter before its transition': [adapter_for(reversed_transition),
                                              reversed_transition],
            'codex other assignment_id': [codex_transition, codex_entry(
                authorization_id=codex_transition['attempt_id'], assignment=ASSIGNMENT)],
        }
        for name, fragment in cases.items():
            with self.subTest(case=name):
                self.assert_spent(fragment, 2)

    def test_malformed_adapter_counts_spent(self):
        def bad_pair(edit, make=genuine_pair):
            fragment = make()
            edit(*fragment)
            return fragment

        def fixed_ids(transition, adapter):
            transition['attempt_id'] = 'a' * 32
            adapter['authorization_id'] = 'A' * 32

        def drop_binding(transition, adapter):
            for item in (transition, adapter):
                for field in ('assignment_id', 'source_sha', 'contract_digest'):
                    del item[field]

        def non_hex_source(transition, adapter):
            for item in (transition, adapter):
                item['source_sha'] = 'x' * 40

        cases = {
            'authorization_id not a string': (
                bad_pair(lambda t, a: a.update(authorization_id=7)), 2),
            'authorization_id not lower-case hex': (bad_pair(fixed_ids), 2),
            'adapter binding field missing': (
                bad_pair(lambda t, a: a.pop('contract_digest')), 2),
            'binding missing on both entries': (bad_pair(drop_binding), 2),
            'binding not hex on both entries': (bad_pair(non_hex_source), 2),
            'adapter worker key missing': (bad_pair(lambda t, a: a.pop('worker_key')), 2),
            'codex adapter binding missing': (
                bad_pair(lambda t, a: a.pop('assignment_id'), make=codex_pair), 2),
            'entry not a mapping': (['ROLE_TIMEOUT_RETRY'], 1),
        }
        for name, (fragment, spent) in cases.items():
            with self.subTest(case=name):
                self.assert_spent(fragment, spent)

    def test_unreadable_audit_is_unknown(self):
        self.assertIsNone(self.count({'action': ACTION}))
        self.assertIsNone(self.count(None))


class AuditEntryAndLegacyTests(RoleTimeoutGateBase):
    """Test 5 (audit_entry) and legacy entries without retry_phase."""

    def test_audit_entry_refuses_bad_phase(self):
        decision = allowed('claude-developer')
        for phase in (None, 'transition', 'OTHER', '', 1):
            with self.subTest(phase=phase):
                with self.assertRaises(WorkerDispatchDenied) as raised:
                    worker_dispatch.audit_entry(decision, ACTION, binding=binding(),
                                                retry_phase=phase)
                self.assertEqual(raised.exception.dispatch_code,
                                 'WORKER_DISPATCH_ACTION_INVALID')
        for action in ('DISPATCH', 'TIMEOUT_CONTINUATION', 'RECOVERY'):
            for phase in ('TRANSITION', 'ADAPTER'):
                with self.subTest(action=action, phase=phase):
                    with self.assertRaises(WorkerDispatchDenied) as raised:
                        worker_dispatch.audit_entry(decision, action, binding=binding(),
                                                    retry_phase=phase)
                    self.assertEqual(raised.exception.dispatch_code,
                                     'WORKER_DISPATCH_ACTION_INVALID')

    def test_legacy_entry_shape_unchanged(self):
        entry = worker_dispatch.audit_entry(allowed('claude-developer'), 'DISPATCH',
                                            binding=binding())
        self.assertEqual(set(entry), {
            'attempt_id', 'worker_key', 'action', 'decision', 'reason_code',
            'policy_sha256', 'at', 'assignment_id', 'source_sha', 'contract_digest'})

    def test_legacy_dispatch_flow_has_no_phase(self):
        pending = developer(state='DEVELOPER_PENDING')
        pending.update({'error_code': None, 'execution': None})
        store = TempFileStore(self.tmpdir, record(pending))
        current = store.get(TASK_ID)
        result, new = self.transition(current)
        self.assertIsNone(result)
        self.assertEqual(current['dispatch_audit'][-1]['action'], 'DISPATCH')
        current['developer'] = new
        store.put(current)
        decision = worker_dispatch.authorize_dispatch(store, TASK_ID, 'claude-developer')
        self.assertTrue(decision.allowed)
        audit = store.get(TASK_ID)['dispatch_audit']
        self.assertTrue(all('retry_phase' not in entry for entry in audit))
        self.assertEqual(audit[-1]['authorization_id'], audit[0]['attempt_id'])
        self.assertEqual(role_timeout.count_timeout_attempts(audit), 0)

    def test_legacy_timeout_continuation_entry_counts_once(self):
        legacy = {
            'attempt_id': 'b' * 32,
            'worker_key': 'codex-developer',
            'action': 'TIMEOUT_CONTINUATION',
            'decision': 'ALLOWED',
            'reason_code': 'OWNER_POLICY_ALLOWED',
            'policy_sha256': None,
            'at': '2026-11-01T00:00:00Z',
        }
        self.set_limit(1)
        result, _ = self.transition(record(developer(), [legacy]))
        self.assert_refused(result, 'WORKER_ROLE_TIMEOUT_RETRY_LIMIT')
        self.set_limit(2)
        self.assertIsNone(self.transition(record(developer(), [legacy]))[0])

    def test_other_legacy_actions_do_not_use_budget(self):
        self.set_limit(1)
        legacy = [worker_dispatch.audit_entry(allowed('claude-developer'), action,
                                              binding=binding())
                  for action in ('DISPATCH', 'REPAIR', 'REASSIGN', 'RETRY')]
        self.assertIsNone(self.transition(record(developer(), legacy))[0])


if __name__ == '__main__':
    unittest.main()
