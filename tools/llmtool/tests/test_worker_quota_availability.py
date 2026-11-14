"""#1816 Developer quota availability gate (worker_availability).

Test data rule (Owner #763 A): no credential-shaped strings. Evidence digests
are sha256 of plain labels. The Owner policy is injected (worker_dispatch.evaluate
is patched) and its file and audit writers are guarded, never weakened.
Standard library unittest only; the remote runner has no pytest.
"""

from argparse import Namespace
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from llmtool import pm_caller, review_repair, task_submission_cli, worker_dispatch
from llmtool import worker_availability
from llmtool.errors import LLMToolError
from llmtool.task_store import (WORKER_AVAILABILITY_NAME,
                                WORKER_AVAILABILITY_SCHEMA_VERSION, TaskStore,
                                _digest, _json_bytes)

WORKER = 'claude-developer'
TASK = '1a' * 16
EXECUTOR = '2b' * 16
HEAD = '3c' * 20
DIGEST = hashlib.sha256(b'quota availability fixture').hexdigest()


def _stamp(value):
    return value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


class _MemoryTaskStore(TaskStore):
    """Real worker-observation sidecar; Task records in memory; Task writes refused."""

    def __init__(self, root):
        super().__init__(root)
        self.tasks = {}
        self.task_writes = []

    def get(self, task_id):
        return copy.deepcopy(self.tasks[task_id])

    def update(self, task_id, **changes):
        self.task_writes.append(task_id)
        raise AssertionError('unexpected Task write')

    def compare_and_update(self, task_id, *, expected, **changes):
        self.task_writes.append(task_id)
        raise AssertionError('unexpected Task write')

    def create(self, record):
        self.task_writes.append(record.get('task_id'))
        raise AssertionError('unexpected Task write')


class _Base(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._stores = 0
        self.store = self._new_store()

    def _new_store(self):
        self._stores += 1
        value = _MemoryTaskStore(self.tmp / f'store{self._stores}')
        value.tasks[TASK] = {'task_id': TASK, 'developer': None}
        return value

    def _patch(self, target, name, value):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _use_policy(self):
        """Inject the Owner switch; guard the policy file and every audit writer."""
        state = {'dispatch': 'ALLOWED'}

        def evaluate(worker_key, owner_assignment_ref=None, task_id=None):
            if state['dispatch'] == 'ALLOWED':
                return worker_dispatch.PolicyDecision(
                    worker_key, True, 'OWNER_POLICY_ALLOWED', None)
            return worker_dispatch.PolicyDecision(worker_key, False, 'OWNER_PAUSED', None)

        def refuse_write(*args, **kwargs):
            raise AssertionError('worker dispatch policy or audit written')

        policy_file = self.tmp / 'worker-dispatch.json'
        policy_file.write_bytes(b'{"policy": "fixture"}')
        before = (policy_file.read_bytes(), policy_file.stat().st_mtime_ns)
        self._patch(worker_dispatch, '_POLICY_PATH', policy_file)
        self._patch(worker_dispatch, 'evaluate', evaluate)
        self._patch(worker_dispatch, 'authorize_dispatch', refuse_write)
        self._patch(worker_dispatch, 'authorize_task_store_change', refuse_write)

        def check_unchanged():
            self.assertEqual(
                (policy_file.read_bytes(), policy_file.stat().st_mtime_ns), before)

        self.addCleanup(check_unchanged)
        return state

    def _use_delegate(self):
        """Stand-in for run_task_delegate: policy refusal first, then one provider result."""
        self._patch(pm_caller, 'check_caller', lambda *args, **kwargs: None)
        calls = []
        outcome = {'developer': {'assignment_id': '4d' * 16,
                                 'state': 'DEVELOPER_RESPONDED', 'error_code': None}}

        def run(args, *, store=None):
            calls.append(args.task_id)
            decision = worker_dispatch.evaluate(WORKER)
            if not decision.allowed:
                raise worker_dispatch.WorkerDispatchDenied(decision.reason_code)
            store.tasks[args.task_id]['developer'] = dict(outcome['developer'])
            return 0

        self._patch(task_submission_cli, 'run_task_delegate', run)
        return Namespace(calls=calls, outcome=outcome)

    def _checked(self):
        return task_submission_cli.run_task_delegate_checked(
            Namespace(task_id=TASK, provider='claude', pm_session=None), store=self.store)

    @staticmethod
    def _quota(store, worker=WORKER):
        return store.get_worker_observations()[worker]['quota']

    @staticmethod
    def _write_fail(store, *, observed, fresh_until, condition='QUOTA_EXHAUSTED'):
        store.record_worker_observation(
            worker_key=WORKER, layer='quota', status='FAIL',
            observed_at=_stamp(observed), fresh_until=_stamp(fresh_until),
            source='TEST_FIXTURE', evidence_sha256=DIGEST, condition=condition)

    def _write_fresh_fail(self, store):
        observed = _now() - timedelta(minutes=5)
        self._write_fail(store, observed=observed,
                         fresh_until=observed + timedelta(hours=6))


class OwnerCaseTests(_Base):

    def setUp(self):
        super().setUp()
        self.policy = self._use_policy()
        self.delegate = self._use_delegate()

    # Owner case 1: ALLOWED + available -> dispatch.
    def test_allowed_and_available_dispatches_and_records_pass(self):
        self.assertEqual(self._quota(self.store)['status'], 'UNKNOWN')
        self.assertEqual(self._checked(), 0)
        self.assertEqual(self.delegate.calls, [TASK])
        self.assertEqual(self._quota(self.store)['status'], 'PASS')

    # Owner case 2: ALLOWED + fresh quota FAIL -> refuse before any Task/D6 write.
    def test_allowed_and_fresh_fail_refuses_before_any_write(self):
        self._write_fresh_fail(self.store)
        with self.assertRaises(worker_availability.WorkerQuotaUnavailable) as caught:
            self._checked()
        self.assertEqual(caught.exception.code, 'WORKER_QUOTA_UNAVAILABLE')
        self.assertIsNone(caught.exception.reset_at)
        self.assertEqual(self.delegate.calls, [])
        self.assertEqual(self.store.task_writes, [])
        # No developer record, so no repair or review count is used.
        self.assertIsNone(self.store.tasks[TASK]['developer'])
        # The Owner decision is unchanged and still ALLOWED.
        decision = worker_dispatch.evaluate(WORKER)
        self.assertIs(decision.allowed, True)
        self.assertEqual(decision.reason_code, 'OWNER_POLICY_ALLOWED')
        self.assertEqual(self._quota(self.store)['status'], 'FAIL')

    # Owner case 3: PAUSED + available -> still refused by policy.
    def test_paused_and_available_is_refused_by_policy(self):
        self.policy['dispatch'] = 'PAUSED'
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            self._checked()
        self.assertEqual(caught.exception.dispatch_code, 'OWNER_PAUSED')
        self.assertIsNone(self.store.tasks[TASK]['developer'])
        # A policy refusal is not a quota outcome.
        self.assertEqual(self._quota(self.store)['status'], 'UNKNOWN')

    def test_paused_with_fresh_fail_is_refused_by_policy_first(self):
        self.policy['dispatch'] = 'PAUSED'
        self._write_fresh_fail(self.store)
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            self._checked()
        self.assertEqual(caught.exception.dispatch_code, 'OWNER_PAUSED')
        self.assertEqual(self.delegate.calls, [])
        self.assertEqual(self.store.task_writes, [])

    # Owner case 4: recovered + ALLOWED -> dispatch.
    def test_recovered_and_allowed_dispatches(self):
        self._write_fresh_fail(self.store)
        with self.assertRaises(worker_availability.WorkerQuotaUnavailable):
            self._checked()
        other = dict(self.store.tasks[TASK], task_id='5e' * 16,
                     developer={'assignment_id': '6f' * 16,
                                'state': 'DEVELOPER_RESPONDED', 'error_code': None})
        self.assertEqual(
            worker_availability.record_outcome(self.store, WORKER, other), 'PASS')
        self.assertEqual(self._checked(), 0)
        self.assertEqual(self.delegate.calls, [TASK])
        self.assertEqual(self._quota(self.store)['status'], 'PASS')

    def test_expired_fail_reads_unknown_never_pass_and_does_not_block(self):
        observed = _now() - timedelta(hours=7)
        self._write_fail(self.store, observed=observed,
                         fresh_until=observed + timedelta(hours=6))
        view = self._quota(self.store)
        self.assertEqual(view['status'], 'UNKNOWN')
        self.assertNotIn('condition', view)
        self.assertNotIn('reset_at', view)
        self.assertEqual(
            worker_availability.quota_gate(self.store, WORKER)['status'], 'UNKNOWN')
        self.assertEqual(self._checked(), 0)

    def test_delegate_timeout_through_checked_writes_nothing(self):
        self.delegate.outcome['developer'] = {
            'assignment_id': '4d' * 16, 'state': 'BLOCKED', 'error_code': 'CLAUDE_TIMEOUT'}
        self._checked()
        self.assertEqual(self._quota(self.store)['status'], 'UNKNOWN')

    def test_quota_fail_through_checked_then_refuses_next_dispatch(self):
        self.delegate.outcome['developer'] = {
            'assignment_id': '4d' * 16, 'state': 'BLOCKED',
            'error_code': 'CLAUDE_QUOTA_EXHAUSTED'}
        self._checked()
        self.assertEqual(self._quota(self.store)['status'], 'FAIL')
        with self.assertRaises(worker_availability.WorkerQuotaUnavailable):
            self._checked()
        self.assertEqual(self.delegate.calls, [TASK])

    def test_trusted_reset_sets_fresh_until_and_is_shown(self):
        observed = _now() - timedelta(minutes=1)
        reset = _stamp(observed + timedelta(hours=2))
        task = {'task_id': TASK, 'developer': {'state': 'BLOCKED',
                                               'error_code': 'CLAUDE_QUOTA_EXHAUSTED'}}
        worker_availability.record_outcome(
            self.store, WORKER, task, reset_at=reset, now=observed)
        view = self._quota(self.store)
        self.assertEqual(view['fresh_until'], reset)
        self.assertEqual(view['reset_at'], reset)
        self.assertEqual(view['reset_source'], 'PROVIDER_REPORTED')
        with self.assertRaises(worker_availability.WorkerQuotaUnavailable) as caught:
            worker_availability.quota_gate(self.store, WORKER)
        self.assertEqual(caught.exception.reset_at, reset)


class OutcomeRecordTests(_Base):

    def test_expired_pass_reads_unknown(self):
        observed = _now() - timedelta(hours=7)
        task = {'task_id': TASK, 'developer': {'state': 'DEVELOPER_RESPONDED',
                                               'error_code': None}}
        self.assertEqual(worker_availability.record_outcome(
            self.store, WORKER, task, now=observed), 'PASS')
        self.assertEqual(self._quota(self.store)['status'], 'UNKNOWN')

    def test_absent_record_reads_unknown_and_does_not_block(self):
        self.assertEqual(
            worker_availability.quota_gate(self.store, WORKER)['status'], 'UNKNOWN')

    def test_non_quota_failure_never_writes_fail(self):
        cases = [
            ('BLOCKED', 'CLAUDE_TIMEOUT'),
            ('BLOCKED', 'CLAUDE_EXIT_NONZERO'),
            ('BLOCKED', 'CLAUDE_WRITE_SCOPE_VIOLATION'),
            ('DEVELOPER_UNAVAILABLE', 'DEVELOPER_PROVIDER_UNAVAILABLE'),
            ('DEVELOPER_UNAVAILABLE', 'REMOTE_TEST_FAILED'),
            ('DEVELOPER_UNAVAILABLE', 'D6_SOURCE_BINDING_MISMATCH'),
            ('DEVELOPER_OUTCOME_UNKNOWN', 'DEVELOPER_PROVIDER_UNAVAILABLE'),
            ('BLOCKED', 'OWNER_PAUSED'),
            ('DEVELOPER_UNAVAILABLE', None),
        ]
        for state, code in cases:
            with self.subTest(state=state, code=code):
                store = self._new_store()
                task = {'task_id': TASK, 'developer': {'state': state, 'error_code': code}}
                self.assertIsNone(worker_availability.record_outcome(store, WORKER, task))
                self.assertEqual(self._quota(store)['status'], 'UNKNOWN')

    def test_non_quota_failure_after_pass_leaves_pass(self):
        passed = {'task_id': TASK, 'developer': {'state': 'DEVELOPER_RESPONDED',
                                                 'error_code': None}}
        worker_availability.record_outcome(self.store, WORKER, passed)
        failed = {'task_id': TASK, 'developer': {'state': 'BLOCKED',
                                                 'error_code': 'CLAUDE_TIMEOUT'}}
        self.assertIsNone(worker_availability.record_outcome(self.store, WORKER, failed))
        self.assertEqual(self._quota(self.store)['status'], 'PASS')

    def test_quota_code_writes_fail_fresh_for_six_hours(self):
        cases = [
            ('CLAUDE_QUOTA_EXHAUSTED', 'QUOTA_EXHAUSTED'),
            ('AGY_QUOTA_EXHAUSTED', 'QUOTA_EXHAUSTED'),
            ('GW_AGENT_RATE_LIMITED', 'RATE_LIMITED'),
        ]
        for code, condition in cases:
            with self.subTest(code=code):
                store = self._new_store()
                observed = _now() - timedelta(minutes=1)
                task = {'task_id': TASK, 'developer': {'state': 'BLOCKED',
                                                       'error_code': code}}
                self.assertEqual(worker_availability.record_outcome(
                    store, WORKER, task, now=observed), 'FAIL')
                view = self._quota(store)
                self.assertEqual(view['status'], 'FAIL')
                self.assertEqual(view['condition'], condition)
                self.assertEqual(view['fresh_until'],
                                 _stamp(observed + timedelta(hours=6)))
                self.assertNotIn('reset_at', view)
                self.assertNotIn('reset_source', view)

    def test_untrusted_reset_is_not_shown(self):
        for reset in ('soon', '2026-11-14 10:00:00', 'past'):
            with self.subTest(reset=reset):
                store = self._new_store()
                observed = _now() - timedelta(minutes=1)
                value = (_stamp(observed - timedelta(minutes=1))
                         if reset == 'past' else reset)
                task = {'task_id': TASK, 'developer': {
                    'state': 'BLOCKED', 'error_code': 'CLAUDE_QUOTA_EXHAUSTED'}}
                worker_availability.record_outcome(
                    store, WORKER, task, reset_at=value, now=observed)
                view = self._quota(store)
                self.assertEqual(view['fresh_until'],
                                 _stamp(observed + timedelta(hours=6)))
                self.assertNotIn('reset_at', view)
                self.assertNotIn('reset_source', view)

    def test_provider_result_writes_pass(self):
        cases = [('DEVELOPER_RESPONDED', None),
                 ('DEVELOPER_OUTPUT_REJECTED', 'DEVELOPER_OUTPUT_INVALID_DIFF')]
        for state, code in cases:
            with self.subTest(state=state):
                store = self._new_store()
                self._write_fresh_fail(store)
                task = {'task_id': TASK, 'developer': {'state': state, 'error_code': code}}
                self.assertEqual(
                    worker_availability.record_outcome(store, WORKER, task), 'PASS')
                view = self._quota(store)
                self.assertEqual(view['status'], 'PASS')
                self.assertNotIn('condition', view)

    def test_unchanged_developer_record_is_not_recorded_again(self):
        self.store.tasks[TASK]['developer'] = {'state': 'BLOCKED',
                                               'error_code': 'CLAUDE_QUOTA_EXHAUSTED'}
        before = worker_availability.developer_snapshot(self.store, TASK)
        self.assertIsNone(worker_availability.record_delegate_outcome(
            self.store, WORKER, TASK, before))
        self.assertEqual(self._quota(self.store)['status'], 'UNKNOWN')

    def test_uncovered_routes_are_not_gated(self):
        self.assertIsNone(worker_availability.delegate_worker_key('company-gw'))
        self.assertIsNone(worker_availability.delegate_worker_key('company-gw-agent'))
        self.assertIsNone(worker_availability.delegate_worker_key(None))
        self.assertEqual(worker_availability.delegate_worker_key('claude'), WORKER)


class QuotaLayerStoreTests(_Base):
    """TaskStore: strict quota-layer fields; old sidecars stay valid."""

    def test_quota_fields_validate_strictly(self):
        cases = [
            ('quota', 'FAIL', {}),                                     # FAIL needs a condition
            ('quota', 'FAIL', {'condition': 'OTHER'}),
            ('quota', 'PASS', {'condition': 'QUOTA_EXHAUSTED'}),       # only FAIL has one
            ('runtime', 'FAIL', {'condition': 'QUOTA_EXHAUSTED'}),     # quota layer only
            ('quota', 'FAIL', {'condition': 'QUOTA_EXHAUSTED', 'reset_at': 'R'}),
            ('quota', 'FAIL', {'condition': 'QUOTA_EXHAUSTED', 'reset_at': 'F',
                               'reset_source': 'LOCAL_GUESS'}),
            ('quota', 'FAIL', {'condition': 'QUOTA_EXHAUSTED', 'reset_at': 'R',
                               'reset_source': 'PROVIDER_REPORTED'}),  # must equal fresh_until
        ]
        for index, (layer, status, extra) in enumerate(cases):
            with self.subTest(case=index):
                store = self._new_store()
                observed = _now() - timedelta(minutes=1)
                fresh = observed + timedelta(hours=6)
                other = observed + timedelta(hours=2)
                values = {key: (_stamp(other) if value == 'R' else
                                _stamp(fresh) if value == 'F' else value)
                          for key, value in extra.items()}
                with self.assertRaises(LLMToolError):
                    store.record_worker_observation(
                        worker_key=WORKER, layer=layer, status=status,
                        observed_at=_stamp(observed), fresh_until=_stamp(fresh),
                        source='TEST_FIXTURE', evidence_sha256=DIGEST, **values)
                self.assertEqual(self._quota(store)['status'], 'UNKNOWN')

    def test_old_sidecar_stays_valid(self):
        self.store.get_worker_observations()   # creates the owner-only store root
        observed = _now() - timedelta(minutes=1)
        old = {
            'schema_version': WORKER_AVAILABILITY_SCHEMA_VERSION,
            'observations': [{
                'worker_key': WORKER, 'layer': 'runtime', 'status': 'PASS',
                'observed_at': _stamp(observed),
                'fresh_until': _stamp(observed + timedelta(hours=1)),
                'source': 'TEST_FIXTURE', 'evidence_sha256': DIGEST,
            }],
        }
        old['record_sha256'] = _digest(old)
        fd = os.open(self.store.root / WORKER_AVAILABILITY_NAME,
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(_json_bytes(old))
        view = self.store.get_worker_observations()[WORKER]
        self.assertEqual(view['runtime']['status'], 'PASS')
        self.assertEqual(set(view['runtime']), {'status', 'observed_at', 'fresh_until',
                                                'source', 'evidence_sha256'})
        self.assertEqual(view['quota']['status'], 'UNKNOWN')


class _ReachedCount(Exception):
    pass


class ReviewRepairGateTests(_Base):
    """Review-repair delegate is gated before any repair Task, claim or count."""

    def setUp(self):
        super().setUp()
        self.policy = self._use_policy()
        self.store.tasks[TASK] = {
            'task_id': TASK, 'review_repair_claims': [],
            'developer': {'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'opencode',
                          'owner_assignment_ref': None},
            'completion': {'executor_task_id': EXECUTOR},
        }
        self._patch(review_repair, '_failed_event',
                    lambda task, **kwargs: ({'failed_head': HEAD}, None))
        self._patch(review_repair, '_current_d6_head', lambda *args: HEAD)
        self._patch(review_repair, 'dispute_open_on', lambda *args: False)
        self.recorded = []
        self.dispatched = []

        def task_ids():
            raise _ReachedCount()

        self.store.task_ids = task_ids

    def _run(self):
        return review_repair.repair_review(
            store=self.store, task_id=TASK, executor_task=EXECUTOR, pr_number=7,
            current_head=HEAD,
            repair_task_recorder=lambda **kwargs: self.recorded.append(kwargs),
            dispatch_adapter=lambda **kwargs: self.dispatched.append(kwargs))

    def test_review_repair_refused_on_fresh_fail(self):
        self.store.record_worker_observation(
            worker_key='opencode-developer', layer='quota', status='FAIL',
            observed_at=_stamp(_now() - timedelta(minutes=1)),
            fresh_until=_stamp(_now() + timedelta(hours=5)),
            source='TEST_FIXTURE', evidence_sha256=DIGEST, condition='RATE_LIMITED')
        with self.assertRaises(LLMToolError) as caught:
            self._run()
        self.assertEqual(caught.exception.review_repair_code, 'WORKER_QUOTA_UNAVAILABLE')
        self.assertEqual(self.recorded, [])
        self.assertEqual(self.dispatched, [])
        self.assertEqual(self.store.task_writes, [])
        self.assertEqual(self.store.tasks[TASK]['review_repair_claims'], [])

    def test_review_repair_passes_gate_when_available(self):
        # Past the gate the repair reads its counts; the stub stops it there.
        with self.assertRaises(_ReachedCount):
            self._run()
        self.assertEqual(self.store.task_writes, [])

    def test_review_repair_paused_refused_by_policy(self):
        self.policy['dispatch'] = 'PAUSED'
        with self.assertRaises(LLMToolError) as caught:
            self._run()
        self.assertEqual(caught.exception.review_repair_code, 'OWNER_PAUSED')


if __name__ == '__main__':
    unittest.main()
