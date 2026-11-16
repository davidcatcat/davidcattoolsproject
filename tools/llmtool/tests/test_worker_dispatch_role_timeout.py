"""Dispatch gate and TaskStore tests for the role timeout retry (#1019 A11 row 41 PR 1b).

docs/design/a11-row41-timeout-retry-1019.md section 6: audit_entry (test 5),
the dispatch gate (tests 13-23), the regressions for the four findings R1-R4
at limits 2 and 3, and TaskStore persistence S1-S3 through the real TaskStore
in a temporary directory. The store is never mocked; only the timeout cap
(worker_dispatch.timeout_continuation_max) is injected, and the Owner worker
policy is the shared all-ALLOWED test policy (#1692), set PAUSED or
OWNER_ASSIGNMENT_ONLY explicitly where a refusal is checked.

No provider calls. Fixed, made-up route names, codes and short-pattern hex
values, plus runtime hex ids; no credential-shaped strings (common-core.md
section 4).
"""

import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool import role_timeout
from llmtool import worker_dispatch
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, new_id
from llmtool import task_store as task_store_module
from llmtool.task_submission_cli import _record

import review_trust_fixture
import worker_policy_fixture

ROLE = 'ROLE_TIMEOUT_RETRY'
CODEX = 'TIMEOUT_CONTINUATION'
LIMIT = 'WORKER_ROLE_TIMEOUT_RETRY_LIMIT'
PRECONDITION = 'WORKER_ROLE_TIMEOUT_RETRY_PRECONDITION'
POLICY_INVALID = 'WORKER_ROLE_TIMEOUT_RETRY_POLICY_INVALID'
CODEX_LIMIT = 'WORKER_TIMEOUT_CONTINUATION_LIMIT'
NOT_APPEND_ONLY = 'TASK_STORE_AUDIT_NOT_APPEND_ONLY'
SOURCE_SHA = 'a' * 40
OTHER_SHA = 'b' * 40
EMPTY_DIFF_SHA256 = hashlib.sha256(b'').hexdigest()
BINDING_FIELDS = ('assignment_id', 'source_sha', 'contract_digest')
TIMEOUT_EXECUTION = {
    'status': 'TIMEOUT', 'native_session_id': None, 'process_exit_code': None,
    'stdout_sha256': None, 'elapsed_ms': 25, 'cleanup_status': 'CONFIRMED',
    'usage_source': 'UNKNOWN', 'source_bytes': None, 'source_file_count': None,
    'source_manifest_sha256': None, 'diff_base_sha': SOURCE_SHA,
    'diff_sha256': EMPTY_DIFF_SHA256,
}


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def limit(value):
    """Inject the timeout cap; every other gate check stays real."""
    return patch.object(worker_dispatch, 'timeout_continuation_max', return_value=value)


def dispatch_task(route='agy'):
    """A PM-responded Task record, built like test_task_store._codex_dispatch_task."""
    request = 'Bounded non-sensitive role timeout task.'
    task_id, run_id = new_id(), new_id()
    request_sha = hashlib.sha256(request.encode()).hexdigest()
    task = _record(
        task_id, run_id, 'pm-' + new_id(), 'pm-profile', request,
        state='PM_RESPONDED', provider_type='openai-compatible', model='pm-model',
        backend='NO_NATIVE_SESSION_ID_EXPOSED', request_sha=request_sha)
    task.update(
        pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model', 'base_url': None},
        response='PM returned a bounded task.', error_code=None,
        pm_assignment={'schema_version': 1, 'route': route, 'risk': 'LOW',
                       'goal': 'Produce a proposal diff.',
                       'acceptance': ['the proposal is recorded']},
    )
    return task


def timed_out_developer(**changes):
    """An agy Developer whose run hit its own time limit with no write recorded."""
    developer = {
        'assignment_id': new_id(), 'selection_source': 'PM',
        'state': 'DEVELOPER_OUTCOME_UNKNOWN', 'provider_profile': 'agy',
        'provider_type': 'agy-cli', 'model': None,
        'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
        'session_id': 'dev-' + new_id(), 'source_sha': SOURCE_SHA,
        'source_sha_binding': 'D6_REGISTRY_OBSERVED',
        'read_paths': ['src.py'], 'write_paths': ['src.py'],
        'acceptance': ['the proposal is recorded'], 'context_sha256': 'b' * 64,
        'egress_basis': 'Approved non-sensitive test context.', 'input_bytes': 10,
        'max_tokens': None, 'artifact_format': 'unified_diff', 'artifact': None,
        'artifact_sha256': None, 'usage': None, 'error_code': 'AGY_TIMEOUT',
        'created_at': '2026-10-23T00:00:00Z', 'owner_assignment_ref': None,
        'execution': dict(TIMEOUT_EXECUTION),
    }
    developer.update(changes)
    return developer


def dispatching(developer):
    """The same assignment dispatched again: only state, error and execution change."""
    return dict(developer, state='DEVELOPER_DISPATCHING', error_code=None, execution=None)


def timed_out_again(developer):
    return dict(developer, state='DEVELOPER_OUTCOME_UNKNOWN', error_code='AGY_TIMEOUT',
                execution=dict(TIMEOUT_EXECUTION))


def codex_timeout(developer):
    """A Codex Developer that timed out with changes (the TIMEOUT_CONTINUATION path)."""
    return dict(developer, provider_profile='codex', provider_type='codex-cli',
                backend_session_semantics='NO_NATIVE_SESSION_ID_EXPOSED',
                error_code='CODEX_TIMEOUT_WITH_CHANGES', executor_task_id='executor-codex',
                execution=dict(TIMEOUT_EXECUTION, diff_sha256='c' * 64))


def gate(record, action=ROLE, *, changes=None, expected=None):
    """Run the real gate for the next transition on a copy of record.

    Returns (refusal code or None, the audit entry the gate wrote)."""
    current = copy.deepcopy(record)
    old = current['developer']
    if action == CODEX:
        old = codex_timeout(old)
        current['developer'] = old
        changes = {'developer': dict(old, assignment_id=new_id(),
                                     state='DEVELOPER_DISPATCHING', error_code=None,
                                     execution=None),
                   'completion': {'executor_task_id': 'executor-codex',
                                  'evidence': {'result_head': 'd' * 40}}}
    elif changes is None:
        changes = {'developer': dispatching(old)}
    if expected is None:
        expected = {'developer': old}
    before = len(current.get('dispatch_audit', []))
    denial = worker_dispatch.authorize_task_store_change(current, changes, expected)
    audit = current['dispatch_audit']
    entry = audit[-1] if len(audit) > before else None
    return (None if denial is None else denial.dispatch_code), entry


def count(record):
    return role_timeout.count_timeout_attempts(record['dispatch_audit'])


def codex_pair(binding):
    """A Codex continuation transition and its adapter, as audit_entry writes them."""
    allowed = worker_dispatch.PolicyDecision('codex-developer', True, 'OWNER_POLICY_ALLOWED',
                                             None)
    first = worker_dispatch.audit_entry(allowed, CODEX, binding=binding)
    second = worker_dispatch.audit_entry(allowed, CODEX, binding=binding,
                                         authorization_id=first['attempt_id'])
    return [first, second]


def binding_of(entry):
    return {field: entry[field] for field in BINDING_FIELDS}


class RoleTimeoutStoreCase(unittest.TestCase):
    """Fixture chain of test_task_store.TaskStoreTests: real store, shared test policy."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'tasks'
        self.store = TaskStore(self.root)
        # #1692: the shared all-ALLOWED test policy, never the live file.
        self.policy_path = worker_policy_fixture.install(self)

    def timed_out_task(self, store=None, **changes):
        store = self.store if store is None else store
        task = dispatch_task()
        store.create(task)
        saved = store.compare_and_update(task['task_id'], expected={'developer': None},
                                         developer=timed_out_developer(**changes))
        self.assertIsNotNone(worker_dispatch._assignment_audit_binding(saved, saved['developer']))
        return task['task_id']

    def retry_transition(self, task_id, store=None):
        store = self.store if store is None else store
        old = store.get(task_id)['developer']
        return store.compare_and_update(task_id, expected={'developer': old},
                                        developer=dispatching(old))

    def adapter_launch(self, task_id, store=None):
        return worker_dispatch.authorize_dispatch(self.store if store is None else store,
                                                  task_id, 'agy', action=ROLE)

    def time_out_again(self, task_id):
        old = self.store.get(task_id)['developer']
        return self.store.compare_and_update(task_id, expected={'developer': old},
                                             developer=timed_out_again(old))

    def full_retry(self, task_id):
        self.retry_transition(task_id)
        self.assertTrue(self.adapter_launch(task_id).allowed)
        self.time_out_again(task_id)

    def genuine(self, retries):
        """A stored timed-out record after retries full retries, written by the real paths."""
        with limit(3):
            task_id = self.timed_out_task()
            for _ in range(retries):
                self.full_retry(task_id)
        record = self.store.get(task_id)
        self.assertEqual(len(record['dispatch_audit']), 1 + 2 * retries)
        self.assertEqual(count(record), retries)
        return record

    def assertDenied(self, code, call, *args, **kwargs):
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            call(*args, **kwargs)
        self.assertEqual(caught.exception.dispatch_code, code)

    def assertStoreRefused(self, code, call, *args, **kwargs):
        with self.assertRaises(LLMToolError) as caught:
            call(*args, **kwargs)
        self.assertEqual(getattr(caught.exception, 'task_store_error_code', None), code)


class AuditEntryTests(unittest.TestCase):
    """Design test 5: retry_phase is required for ROLE_TIMEOUT_RETRY and only there."""

    allowed = worker_dispatch.PolicyDecision('agy', True, 'OWNER_POLICY_ALLOWED', None)
    binding = {'assignment_id': '1' * 32, 'source_sha': SOURCE_SHA, 'contract_digest': 'c' * 64}

    def assertInvalid(self, action, **kwargs):
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            worker_dispatch.audit_entry(self.allowed, action, binding=self.binding, **kwargs)
        self.assertEqual(caught.exception.dispatch_code, 'WORKER_DISPATCH_ACTION_INVALID')

    def test_action_is_known(self):
        self.assertIn(ROLE, worker_dispatch.AUDIT_ACTIONS)
        self.assertEqual(worker_dispatch.RETRY_PHASES, frozenset(('TRANSITION', 'ADAPTER')))

    def test_role_retry_without_or_with_unknown_phase_is_refused(self):
        for phase in (None, 'transition', 'ADAPTER ', 'OTHER', '', 1, ['ADAPTER']):
            with self.subTest(phase=phase):
                self.assertInvalid(ROLE, retry_phase=phase)

    def test_transition_phase_never_carries_an_authorization_id(self):
        self.assertInvalid(ROLE, retry_phase='TRANSITION', authorization_id='2' * 32)

    def test_phase_on_any_other_action_is_refused(self):
        for action in sorted(worker_dispatch.AUDIT_ACTIONS - {ROLE}):
            for phase in ('TRANSITION', 'ADAPTER', 'OTHER'):
                with self.subTest(action=action, phase=phase):
                    self.assertInvalid(action, retry_phase=phase)

    def test_valid_phases_are_written(self):
        first = worker_dispatch.audit_entry(self.allowed, ROLE, binding=self.binding,
                                            retry_phase='TRANSITION')
        self.assertEqual(first['retry_phase'], 'TRANSITION')
        self.assertNotIn('authorization_id', first)
        second = worker_dispatch.audit_entry(self.allowed, ROLE, binding=self.binding,
                                             retry_phase='ADAPTER',
                                             authorization_id=first['attempt_id'])
        self.assertEqual((second['retry_phase'], second['authorization_id']),
                         ('ADAPTER', first['attempt_id']))
        lone = worker_dispatch.audit_entry(self.allowed, ROLE, retry_phase='ADAPTER')
        self.assertEqual(lone['retry_phase'], 'ADAPTER')
        self.assertEqual(role_timeout.count_timeout_attempts([first, second]), 1)

    def test_legacy_actions_have_no_phase_key(self):
        for action in sorted(worker_dispatch.AUDIT_ACTIONS - {ROLE}):
            with self.subTest(action=action):
                entry = worker_dispatch.audit_entry(self.allowed, action, binding=self.binding)
                self.assertNotIn('retry_phase', entry)


class DispatchGateTests(RoleTimeoutStoreCase):
    """Design tests 13-23."""

    def test_13_transition_is_role_timeout_retry_and_allowed_once_at_limit_one(self):
        task_id = self.timed_out_task()
        current = self.store.get(task_id)
        changes = {'developer': dispatching(current['developer'])}
        self.assertEqual(worker_dispatch.task_store_attempts(current, changes), [('agy', ROLE)])
        with limit(1):
            saved = self.retry_transition(task_id)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_DISPATCHING')
        entry = saved['dispatch_audit'][-1]
        self.assertEqual((entry['action'], entry['decision'], entry['retry_phase']),
                         (ROLE, 'ALLOWED', 'TRANSITION'))
        self.assertNotIn('authorization_id', entry)
        self.assertEqual(binding_of(entry),
                         worker_dispatch._assignment_audit_binding(saved, saved['developer']))

    def test_unavailable_role_timeout_to_dispatching_is_a_role_retry(self):
        record = self.genuine(0)
        record['developer'] = dict(record['developer'], state='DEVELOPER_UNAVAILABLE')
        changes = {'developer': dispatching(record['developer'])}
        self.assertEqual(worker_dispatch.task_store_attempts(record, changes), [('agy', ROLE)])

    def test_non_timeout_records_keep_their_legacy_action(self):
        record = self.genuine(0)
        for code in ('AGY_UNAVAILABLE', 'CLAUDE_TIMEOUT', 'AGY_TIMEOUT ', None):
            with self.subTest(code=code):
                old = dict(record['developer'], error_code=code)
                current = dict(record, developer=old)
                attempts = worker_dispatch.task_store_attempts(
                    current, {'developer': dispatching(old)})
                self.assertEqual(attempts, [('agy', 'RETRY')])

    def test_14_second_retry_at_limit_one_is_refused(self):
        task_id = self.timed_out_task()
        with limit(1):
            self.full_retry(task_id)
            self.assertDenied(LIMIT, self.retry_transition, task_id)
        record = self.store.get(task_id)
        self.assertEqual(record['developer']['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(record['dispatch_audit'][-1]['decision'], 'BLOCKED')
        self.assertEqual(record['dispatch_audit'][-1]['reason_code'], LIMIT)

    def test_14_first_retry_on_another_assignment_or_worker_counts(self):
        record = self.genuine(1)
        first, second = record['dispatch_audit'][1:3]
        for changes in ({'assignment_id': new_id()}, {'worker_key': 'claude-developer'},
                        {'worker_key': 'claude-developer', 'assignment_id': new_id(),
                         'source_sha': OTHER_SHA}):
            with self.subTest(changes=changes):
                moved = copy.deepcopy(self.genuine(0))
                moved['dispatch_audit'] += [dict(first, **changes), dict(second, **changes)]
                self.assertEqual(count(moved), 1)
                with limit(1):
                    self.assertEqual(gate(moved)[0], LIMIT)

    def test_14_blocked_first_transition_uses_the_budget(self):
        task_id = self.timed_out_task()
        worker_policy_fixture.update(self.policy_path, 'agy', 'PAUSED')
        with limit(1):
            self.assertDenied('OWNER_PAUSED', self.retry_transition, task_id)
        self.assertEqual(count(self.store.get(task_id)), 1)
        worker_policy_fixture.update(self.policy_path, 'agy', 'ALLOWED')
        with limit(1):
            self.assertDenied(LIMIT, self.retry_transition, task_id)

    def check_store_limit(self, cap):
        task_id = self.timed_out_task()
        with limit(cap):
            for _ in range(cap):
                self.full_retry(task_id)
            self.assertEqual(count(self.store.get(task_id)), cap)
            self.assertEqual(len(self.store.get(task_id)['dispatch_audit']), 1 + 2 * cap)
            self.assertDenied(LIMIT, self.retry_transition, task_id)
        return task_id

    def test_15_limit_two_allows_two_full_retries(self):
        self.check_store_limit(2)

    def test_16_limit_three_allows_three_full_retries(self):
        self.check_store_limit(3)

    def test_limit_zero_refuses_the_first_retry(self):
        task_id = self.timed_out_task()
        with limit(0):
            self.assertDenied(LIMIT, self.retry_transition, task_id)

    def test_17_codex_continuation_and_role_retry_share_the_budget(self):
        fresh = self.genuine(0)
        binding = worker_dispatch._assignment_audit_binding(fresh, fresh['developer'])
        with_codex = copy.deepcopy(fresh)
        with_codex['dispatch_audit'] += codex_pair(binding)
        with_role = self.genuine(1)
        with limit(1):
            self.assertEqual(gate(fresh)[0], None)
            self.assertEqual(gate(fresh, CODEX)[0], None)
            self.assertEqual(gate(with_codex)[0], LIMIT)
            self.assertEqual(gate(with_role, CODEX)[0], CODEX_LIMIT)
        both = copy.deepcopy(with_role)
        both['dispatch_audit'] += codex_pair(binding)
        with limit(2):
            self.assertEqual(gate(with_codex)[0], None)
            self.assertEqual(gate(with_role, CODEX)[0], None)
            self.assertEqual(count(both), 2)
            self.assertEqual(gate(both)[0], LIMIT)
            self.assertEqual(gate(both, CODEX)[0], CODEX_LIMIT)

    def test_codex_check_counts_a_pair_once(self):
        fresh = self.genuine(0)
        binding = worker_dispatch._assignment_audit_binding(fresh, fresh['developer'])
        record = copy.deepcopy(fresh)
        record['dispatch_audit'] += codex_pair(binding)
        with limit(2):
            self.assertEqual(count(record), 1)
            self.assertEqual(gate(record, CODEX)[0], None)

    def test_18_unreadable_or_invalid_policy_refuses(self):
        task_id = self.timed_out_task()
        with limit(None):
            self.assertDenied(POLICY_INVALID, self.retry_transition, task_id)
        self.assertEqual(self.store.get(task_id)['dispatch_audit'][-1]['reason_code'],
                         POLICY_INVALID)
        record = self.store.get(task_id)
        for value in (None, True, '1', 1.0, -1):
            with self.subTest(value=value):
                with limit(value):
                    self.assertEqual(gate(record)[0], POLICY_INVALID)
                    self.assertEqual(gate(record, CODEX)[0],
                                     'WORKER_TIMEOUT_CONTINUATION_POLICY_INVALID')

    def test_count_that_cannot_be_computed_refuses_as_limit(self):
        record = self.genuine(0)
        with limit(3), patch.object(role_timeout, 'count_timeout_attempts',
                                    side_effect=role_timeout.RoleTimeoutError(
                                        role_timeout.AUDIT_INVALID)):
            self.assertEqual(gate(record)[0], LIMIT)
            self.assertEqual(gate(record, CODEX)[0], CODEX_LIMIT)

    def test_19_changed_identity_scope_or_evidence_refuses_precondition(self):
        record = self.genuine(0)
        old = record['developer']
        new_changes = {
            'assignment': {'assignment_id': new_id()},
            'session': {'session_id': 'dev-' + new_id()},
            'source sha': {'source_sha': OTHER_SHA},
            'provider type': {'provider_type': None},
            'read paths': {'read_paths': ['other.py', 'src.py']},
            'write paths': {'write_paths': []},
            'acceptance': {'acceptance': ['another acceptance item']},
            'executor task': {'executor_task_id': 'executor-other'},
        }
        with limit(3):
            self.assertEqual(gate(record)[0], None)
            for label, change in new_changes.items():
                with self.subTest(new=label):
                    changes = {'developer': dict(dispatching(old), **change)}
                    self.assertEqual(gate(record, changes=changes)[0], PRECONDITION)
            for label, expected in (('other expected', {'developer': dispatching(old)}),
                                    ('no developer', {'record_sha256': record['record_sha256']})):
                with self.subTest(expected=label):
                    self.assertEqual(gate(record, expected=expected)[0], PRECONDITION)
            self.assertEqual(gate(record, expected={})[0], PRECONDITION)

    def test_19_old_record_evidence_must_show_a_clean_timeout(self):
        record = self.genuine(0)
        execution = record['developer']['execution']
        bad_executions = {
            'cleanup unknown': dict(execution, cleanup_status='UNKNOWN'),
            'status failed': dict(execution, status='FAILED'),
            'non-empty diff': dict(execution, diff_sha256='c' * 64),
            'other diff base': dict(execution, diff_base_sha=OTHER_SHA),
            'no diff fields': {key: value for key, value in execution.items()
                               if key not in ('diff_sha256', 'diff_base_sha')},
            'no execution': None,
        }
        with limit(3):
            for label, value in bad_executions.items():
                with self.subTest(execution=label):
                    changed = copy.deepcopy(record)
                    changed['developer']['execution'] = value
                    self.assertEqual(gate(changed)[0], PRECONDITION)
            for label, completion in (
                    ('result head', {'evidence': {'result_head': 'd' * 40}}),
                    ('carried result', {'evidence': {'carried_result': {'mode': 'X'}}}),
                    ('not a dict', ['x'])):
                with self.subTest(completion=label):
                    changed = dict(copy.deepcopy(record), completion=completion)
                    self.assertEqual(gate(changed)[0], PRECONDITION)
            carried = copy.deepcopy(record)
            carry = dict(carried['dispatch_audit'][0], action='COMMITTED_RESULT_CARRY',
                         attempt_id=new_id())
            carried['dispatch_audit'].append(carry)
            self.assertEqual(gate(carried)[0], PRECONDITION)

    def test_20_paused_worker_and_missing_owner_row_refuse(self):
        task_id = self.timed_out_task()
        worker_policy_fixture.update(self.policy_path, 'agy', 'PAUSED')
        with limit(3):
            self.assertDenied('OWNER_PAUSED', self.retry_transition, task_id)
        worker_policy_fixture.update(self.policy_path, 'agy', 'OWNER_ASSIGNMENT_ONLY')
        with limit(3):
            self.assertDenied('OWNER_ASSIGNMENT_REQUIRED', self.retry_transition, task_id)
        self.assertEqual(self.store.get(task_id)['developer']['state'],
                         'DEVELOPER_OUTCOME_UNKNOWN')

    def test_21_recovery_of_a_role_timeout_is_refused(self):
        record = self.genuine(0)
        for code, refusal in (('AGY_TIMEOUT', 'WORKER_ROLE_TIMEOUT_NOT_RECOVERY'),
                              ('CLAUDE_TIMEOUT', 'WORKER_ROLE_TIMEOUT_NOT_RECOVERY'),
                              ('AGY_UNAVAILABLE', None)):
            with self.subTest(code=code):
                old = dict(record['developer'], state='DEVELOPER_UNAVAILABLE', error_code=code)
                current = dict(copy.deepcopy(record), developer=old)
                pending = dict(old, state='DEVELOPER_PENDING', error_code=None)
                self.assertEqual(worker_dispatch.task_store_attempts(
                    current, {'developer': pending}), [('agy', 'RECOVERY')])
                with limit(3):
                    code_seen, entry = gate(current, changes={'developer': pending})
                self.assertEqual(code_seen, refusal)
                self.assertEqual(entry['action'], 'RECOVERY')
                self.assertNotIn('retry_phase', entry)

    def test_22_adapter_passes_once_then_consumed_adds_one(self):
        task_id = self.timed_out_task()
        with limit(3):
            transition = self.retry_transition(task_id)['dispatch_audit'][-1]
            self.assertTrue(self.adapter_launch(task_id).allowed)
            entry = self.store.get(task_id)['dispatch_audit'][-1]
            self.assertEqual((entry['action'], entry['decision'], entry['retry_phase'],
                              entry['authorization_id']),
                             (ROLE, 'ALLOWED', 'ADAPTER', transition['attempt_id']))
            self.assertEqual(count(self.store.get(task_id)), 1)
            self.assertDenied('WORKER_DISPATCH_AUTHORIZATION_CONSUMED',
                              self.adapter_launch, task_id)
        refused = self.store.get(task_id)['dispatch_audit'][-1]
        self.assertEqual((refused['decision'], refused['retry_phase']), ('BLOCKED', 'ADAPTER'))
        self.assertEqual(count(self.store.get(task_id)), 2)

    def test_adapter_without_transition_is_blocked_and_counts(self):
        task_id = self.timed_out_task()
        with limit(3):
            self.retry_transition(task_id)
        record = self.store.get(task_id)
        # A launch under another worker key finds no transition of that key, so
        # its blocked adapter entry has no authorization_id and counts 1.
        self.assertDenied('WORKER_ASSIGNMENT_NOT_DISPATCHING', worker_dispatch.authorize_dispatch,
                          self.store, task_id, 'claude-developer', action=ROLE)
        entry = self.store.get(task_id)['dispatch_audit'][-1]
        self.assertEqual((entry['decision'], entry['retry_phase']), ('BLOCKED', 'ADAPTER'))
        self.assertNotIn('authorization_id', entry)
        self.assertEqual(count(self.store.get(task_id)), count(record) + 1)

    def test_23_adapter_phase_entry_never_authorizes_a_launch(self):
        record = self.genuine(0)
        binding = worker_dispatch._assignment_audit_binding(record, record['developer'])
        allowed = worker_dispatch.PolicyDecision('agy', True, 'OWNER_POLICY_ALLOWED', None)
        hand_built = worker_dispatch.audit_entry(allowed, ROLE, binding=binding,
                                                 retry_phase='ADAPTER')
        no_phase = {key: value for key, value in hand_built.items() if key != 'retry_phase'}
        for entry in (hand_built, no_phase, dict(hand_built, retry_phase='OTHER')):
            with self.subTest(entry=entry.get('retry_phase')):
                current = dict(record, dispatch_audit=[entry])
                self.assertIsNone(worker_dispatch._latest_transition_authorization(
                    current, 'agy', ROLE, binding))
        good = dict(hand_built, retry_phase='TRANSITION')
        self.assertEqual(worker_dispatch._latest_transition_authorization(
            dict(record, dispatch_audit=[good]), 'agy', ROLE, binding), good['attempt_id'])


class FindingRegressionTests(RoleTimeoutStoreCase):
    """R1-R4 at limits 2 and 3; added and altered forms (design section 6)."""

    def setUp(self):
        super().setUp()
        self.records = {retries: self.genuine(retries) for retries in (1, 2, 3)}

    def allowed_base(self, cap):
        """L - 1 genuine retries: the positive control."""
        record = copy.deepcopy(self.records[cap - 1])
        with limit(cap):
            self.assertEqual(count(record), cap - 1)
            self.assertIsNone(gate(record)[0])
            self.assertIsNone(gate(record, CODEX)[0])
        return record

    def assertRefusedAt(self, cap, record):
        with limit(cap):
            self.assertGreaterEqual(count(record), cap)
            code, entry = gate(record)
            self.assertEqual(code, LIMIT)
            self.assertEqual((entry['decision'], entry['retry_phase']), ('BLOCKED', 'TRANSITION'))
            self.assertEqual(gate(record, CODEX)[0], CODEX_LIMIT)

    def check_added(self, additions):
        for cap in (2, 3):
            base = self.allowed_base(cap)
            first, second = base['dispatch_audit'][1:3]
            for label, build in additions.items():
                with self.subTest(limit=cap, added=label):
                    record = copy.deepcopy(base)
                    record['dispatch_audit'] += build(first, second)
                    self.assertRefusedAt(cap, record)

    def check_altered(self, alterations):
        for cap in (2, 3):
            for label, alter in alterations.items():
                with self.subTest(limit=cap, altered=label):
                    record = copy.deepcopy(self.records[cap])
                    alter(record['dispatch_audit'])
                    self.assertRefusedAt(cap, record)

    def test_r1_unvalidated_adapters(self):
        def no_id(first, second):
            entry = dict(second, attempt_id=new_id())
            del entry['authorization_id']
            return [entry]

        def blocked_transition(first, second):
            identity = new_id()
            return [dict(first, attempt_id=identity, decision='BLOCKED',
                         reason_code='OWNER_PAUSED'),
                    dict(second, attempt_id=new_id(), authorization_id=identity)]

        def later_transition(first, second):
            identity = new_id()
            return [dict(second, attempt_id=new_id(), authorization_id=identity),
                    dict(first, attempt_id=identity)]

        def other_action(first, second):
            entry = dict(second, attempt_id=new_id(), action=CODEX)
            del entry['retry_phase']
            return [entry]

        additions = {
            'adapter phase without id': no_id,
            'names no transition': lambda first, second: [
                dict(second, attempt_id=new_id(), authorization_id=new_id())],
            'names a blocked transition': blocked_transition,
            'names a later transition': later_transition,
            'other worker key': lambda first, second: [
                dict(second, attempt_id=new_id(), worker_key='claude-developer')],
            'other action': other_action,
        }
        for field, value in (('assignment_id', '3' * 32), ('source_sha', OTHER_SHA),
                             ('contract_digest', '4' * 64)):
            additions['other ' + field] = (
                lambda first, second, field=field, value=value: [
                    dict(second, attempt_id=new_id(), **{field: value})])
        self.check_added(additions)
        self.check_altered({'transition phase set to ADAPTER':
                            lambda audit: audit[1].update(retry_phase='ADAPTER')})

    def test_r2_missing_adapter_fields(self):
        fields = ('authorization_id', 'attempt_id', 'worker_key', 'decision',
                  'assignment_id', 'source_sha', 'contract_digest')

        def adapter_of(action, second):
            entry = dict(second, attempt_id=new_id())
            if action == CODEX:
                entry['action'] = CODEX
                del entry['retry_phase']
            return entry

        additions = {}
        for action in (ROLE, CODEX):
            for field in fields:
                def missing(first, second, action=action, field=field):
                    entry = adapter_of(action, second)
                    del entry[field]
                    return [entry]

                def malformed(first, second, action=action, field=field):
                    entry = adapter_of(action, second)
                    value = entry[field]
                    entry[field] = value.upper() + 'Z' if type(value) is str else None
                    return [entry]

                additions['%s missing %s' % (action, field)] = missing
                additions['%s malformed %s' % (action, field)] = malformed
        self.check_added(additions)
        alterations = {}
        for field in fields:
            alterations['adapter without ' + field] = (
                lambda audit, field=field: audit[2].pop(field))
        self.check_altered(alterations)

    def test_r3_malformed_action_strings(self):
        variants = {
            'lower case': 'role_timeout_retry', 'padded space': 'ROLE_TIMEOUT_RETRY ',
            'padded newline': 'ROLE_TIMEOUT_RETRY\n', 'prefixed': 'X_ROLE_TIMEOUT_RETRY',
            'suffixed': 'ROLE_TIMEOUT_RETRY_X', 'timeout-like': 'TIMEOUT_RETRY',
            'not a str': 7,
        }
        additions = {}
        alterations = {}
        for label, action in variants.items():
            additions[label] = (lambda first, second, action=action: [
                dict(first, attempt_id=new_id(), action=action)])
            alterations[label] = (lambda audit, action=action: audit[1].update(action=action))

        def missing(first, second):
            entry = dict(first, attempt_id=new_id())
            del entry['action']
            return [entry]

        additions['missing'] = missing
        alterations['missing'] = lambda audit: audit[1].pop('action')
        self.check_added(additions)
        self.check_altered(alterations)

    def test_r3_negative_control_exact_unrelated_actions_count_zero(self):
        for cap in (2, 3):
            base = self.allowed_base(cap)
            first = base['dispatch_audit'][1]
            for action in ('RETRY', 'REVIEWER_RETRY', 'DISPATCH'):
                with self.subTest(limit=cap, action=action):
                    record = copy.deepcopy(base)
                    entry = dict(first, attempt_id=new_id(), action=action)
                    del entry['retry_phase']
                    record['dispatch_audit'].append(entry)
                    with limit(cap):
                        self.assertEqual(count(record), cap - 1)
                        self.assertIsNone(gate(record)[0])

    def test_r4_reused_authorization_id(self):
        def own_id(first, second):
            identity = new_id()
            return [dict(second, attempt_id=identity, authorization_id=identity)]

        self.check_added({
            'second adapter allowed': lambda first, second: [
                dict(second, attempt_id=new_id())],
            'second adapter blocked': lambda first, second: [
                dict(second, attempt_id=new_id(), decision='BLOCKED',
                     reason_code='WORKER_DISPATCH_AUTHORIZATION_CONSUMED')],
            'authorization id equals own attempt': own_id,
        })
        # Altered: a role transition given the id of a completed pair, phase kept.
        self.check_altered({'role transition given an id': lambda audit: audit[1].update(
            authorization_id=audit[3]['attempt_id'])})
        # Altered: L genuine attempts whose last is a Codex continuation transition
        # written by audit_entry; it is then given the id of a completed pair.
        for cap in (2, 3):
            with self.subTest(limit=cap, altered='codex transition given an id'):
                record = copy.deepcopy(self.records[cap - 1])
                binding = worker_dispatch._assignment_audit_binding(record, record['developer'])
                record['dispatch_audit'].append(codex_pair(binding)[0])
                self.assertRefusedAt(cap, record)
                record['dispatch_audit'][-1]['authorization_id'] = (
                    record['dispatch_audit'][1]['attempt_id'])
                self.assertRefusedAt(cap, record)


class TaskStoreAuditTests(RoleTimeoutStoreCase):
    """S1-S3 and the audit entry schema, through the real TaskStore."""

    def test_s1_two_full_retries_persist_and_the_third_is_refused(self):
        task_id = self.timed_out_task()
        with limit(2):
            self.full_retry(task_id)
            self.full_retry(task_id)
        reopened = TaskStore(self.root)
        record = reopened.get(task_id)
        actions = [(entry['action'], entry.get('retry_phase')) for entry in record['dispatch_audit']]
        self.assertEqual(actions, [('DISPATCH', None)] + [(ROLE, 'TRANSITION'),
                                                          (ROLE, 'ADAPTER')] * 2)
        self.assertEqual(count(record), 2)
        with limit(2):
            self.assertDenied(LIMIT, self.retry_transition, task_id, reopened)
        record = TaskStore(self.root).get(task_id)
        self.assertEqual((record['dispatch_audit'][-1]['decision'],
                          record['dispatch_audit'][-1]['reason_code']), ('BLOCKED', LIMIT))
        self.assertEqual(count(record), 3)
        self.assertEqual(record['developer']['state'], 'DEVELOPER_OUTCOME_UNKNOWN')

    def test_s3_restart_gives_the_same_count_and_refusal(self):
        task_id = self.timed_out_task()
        with limit(1):
            self.full_retry(task_id)
        for attempt in range(2):
            store = TaskStore(self.root)
            with self.subTest(restart=attempt):
                before = count(store.get(task_id))
                self.assertEqual(before, 1 + attempt)
                with limit(1):
                    self.assertDenied(LIMIT, self.retry_transition, task_id, store)
                self.assertEqual(count(TaskStore(self.root).get(task_id)), before + 1)

    def stored_audit(self):
        task_id = self.timed_out_task()
        with limit(3):
            self.full_retry(task_id)
            self.full_retry(task_id)
            self.retry_transition(task_id)  # a lone transition: its adapter never wrote
        return task_id, self.store.get(task_id)

    def test_s2_audit_rewrites_are_refused_and_nothing_is_written(self):
        task_id, record = self.stored_audit()
        audit = record['dispatch_audit']
        first, lone = audit[1], audit[-1]
        rewrites = {
            'field changed': [audit[0], dict(first, reason_code='OTHER_REASON')] + audit[2:],
            'decision changed': [audit[0], dict(first, decision='BLOCKED')] + audit[2:],
            'lone transition into an adapter half': audit[:-1] + [
                dict(lone, retry_phase='ADAPTER', authorization_id=first['attempt_id'])],
            'timeout entry into DISPATCH': [audit[0], {
                key: value for key, value in dict(first, action='DISPATCH').items()
                if key != 'retry_phase'}] + audit[2:],
            'reordered': [audit[0], audit[2], audit[1]] + audit[3:],
            'entry removed': audit[:2] + audit[3:],
            'trimmed front': audit[1:],
            'trimmed end': audit[:-1],
            'emptied': [],
            'rewritten then appended': [audit[0], dict(first, reason_code='OTHER_REASON')] +
                                       audit[2:] + [dict(audit[0], attempt_id=new_id())],
        }
        for label, proposed in rewrites.items():
            with self.subTest(rewrite=label):
                self.assertStoreRefused(
                    NOT_APPEND_ONLY, self.store._update, task_id, expected=None,
                    changes={'dispatch_audit': copy.deepcopy(proposed)},
                    trusted_dispatch_audit=True)
                self.assertEqual(self.store.get(task_id), record)
        # The public update path refuses any audit write under its existing code.
        with self.assertRaises(LLMToolError):
            self.store.compare_and_update(
                task_id, expected={'record_sha256': record['record_sha256']},
                dispatch_audit=rewrites['field changed'])
        self.assertEqual(self.store.get(task_id), record)

    def test_s2_appending_keeps_the_stored_audit_as_prefix(self):
        task_id, record = self.stored_audit()
        audit = record['dispatch_audit']
        paused = worker_dispatch.PolicyDecision('agy', False, 'OWNER_PAUSED', None)
        binding = binding_of(audit[1])
        added = worker_dispatch.audit_entry(paused, 'DISPATCH', binding=binding)
        saved = self.store._update(task_id, expected=None,
                                   changes={'dispatch_audit': audit + [added]},
                                   trusted_dispatch_audit=True)
        self.assertEqual(saved['dispatch_audit'], audit + [added])
        unchanged = self.store._update(task_id, expected=None,
                                       changes={'dispatch_audit': audit + [added]},
                                       trusted_dispatch_audit=True)
        self.assertEqual(unchanged['dispatch_audit'], audit + [added])
        later = worker_dispatch.audit_entry(paused, ROLE, binding=binding, retry_phase='ADAPTER')
        saved = self.store.append_dispatch_audit(task_id, later)
        self.assertEqual(saved['dispatch_audit'], audit + [added, later])

    def test_audit_entry_schema_accepts_only_the_exact_phase_shapes(self):
        task_id = self.timed_out_task()
        record = self.store.get(task_id)
        binding = worker_dispatch._assignment_audit_binding(record, record['developer'])
        blocked = worker_dispatch.PolicyDecision('agy', False, 'OWNER_PAUSED', None)
        transition = worker_dispatch.audit_entry(blocked, ROLE, binding=binding,
                                                 retry_phase='TRANSITION')
        dispatch = worker_dispatch.audit_entry(blocked, 'DISPATCH', binding=binding)
        codex = worker_dispatch.audit_entry(blocked, CODEX, binding=binding)
        bad = {
            'role without phase': {key: value for key, value in transition.items()
                                   if key != 'retry_phase'},
            'role unknown phase': dict(transition, retry_phase='OTHER'),
            'role lower-case phase': dict(transition, retry_phase='transition'),
            'role phase not a str': dict(transition, retry_phase=1),
            'role transition with id': dict(transition, authorization_id=new_id()),
            'dispatch with phase': dict(dispatch, retry_phase='TRANSITION'),
            'codex with phase': dict(codex, retry_phase='ADAPTER'),
            'unknown action': dict(dispatch, action='ROLE_TIMEOUT'),
        }
        for label, entry in bad.items():
            with self.subTest(entry=label):
                with self.assertRaises(LLMToolError):
                    self.store.append_dispatch_audit(task_id, dict(entry, attempt_id=new_id()))
                self.assertEqual(self.store.get(task_id), record)
        for label, entry in (('role transition', transition), ('dispatch', dispatch),
                             ('codex', codex),
                             ('role adapter without binding', worker_dispatch.audit_entry(
                                 blocked, ROLE, retry_phase='ADAPTER'))):
            with self.subTest(accepted=label):
                saved = self.store.append_dispatch_audit(task_id, entry)
                self.assertEqual(saved['dispatch_audit'][-1], entry)
        task_store_module._validate(dict(self.store.get(task_id)))

    def test_diff_evidence_is_accepted_only_with_the_routes_own_timeout_code(self):
        for code in ('AGY_UNAVAILABLE', 'CLAUDE_TIMEOUT', 'GW_AGENT_TIMEOUT', 'AGY_TIMEOUT_X'):
            with self.subTest(code=code):
                task = dispatch_task()
                self.store.create(task)
                with self.assertRaises(LLMToolError):
                    self.store.compare_and_update(
                        task['task_id'], expected={'developer': None},
                        developer=timed_out_developer(error_code=code))
                self.assertIsNone(self.store.get(task['task_id'])['developer'])
        task = dispatch_task()
        self.store.create(task)
        with self.assertRaises(LLMToolError):
            self.store.compare_and_update(
                task['task_id'], expected={'developer': None},
                developer=timed_out_developer(execution=dict(TIMEOUT_EXECUTION,
                                                             diff_base_sha=OTHER_SHA)))


if __name__ == '__main__':
    unittest.main()
