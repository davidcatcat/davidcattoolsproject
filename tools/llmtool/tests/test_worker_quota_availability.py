"""#1816 Developer quota availability gate (worker_availability).

Test data rule (Owner #763 A): no credential-shaped strings. Evidence digests
are sha256 of plain labels. The Owner policy is injected (worker_dispatch.evaluate
is monkeypatched) and its file and audit writers are guarded, never weakened.
"""

from argparse import Namespace
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import os

import pytest

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
        self.count_reads = 0

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


@pytest.fixture
def store(tmp_path):
    value = _MemoryTaskStore(tmp_path / 'store')
    value.tasks[TASK] = {'task_id': TASK, 'developer': None}
    return value


@pytest.fixture
def policy(monkeypatch, tmp_path):
    """Inject the Owner switch; guard the policy file and every audit writer."""
    state = {'dispatch': 'ALLOWED'}

    def evaluate(worker_key, owner_assignment_ref=None, task_id=None):
        if state['dispatch'] == 'ALLOWED':
            return worker_dispatch.PolicyDecision(
                worker_key, True, 'OWNER_POLICY_ALLOWED', None)
        return worker_dispatch.PolicyDecision(worker_key, False, 'OWNER_PAUSED', None)

    def refuse_write(*args, **kwargs):
        raise AssertionError('worker dispatch policy or audit written')

    policy_file = tmp_path / 'worker-dispatch.json'
    policy_file.write_bytes(b'{"policy": "fixture"}')
    before = (policy_file.read_bytes(), policy_file.stat().st_mtime_ns)
    monkeypatch.setattr(worker_dispatch, '_POLICY_PATH', policy_file)
    monkeypatch.setattr(worker_dispatch, 'evaluate', evaluate)
    monkeypatch.setattr(worker_dispatch, 'authorize_dispatch', refuse_write)
    monkeypatch.setattr(worker_dispatch, 'authorize_task_store_change', refuse_write)
    yield state
    assert (policy_file.read_bytes(), policy_file.stat().st_mtime_ns) == before


@pytest.fixture
def delegate(monkeypatch, store):
    """Stand-in for run_task_delegate: policy refusal first, then one provider result."""
    monkeypatch.setattr(pm_caller, 'check_caller', lambda *args, **kwargs: None)
    calls = []
    outcome = {'developer': {'assignment_id': '4d' * 16, 'state': 'DEVELOPER_RESPONDED',
                             'error_code': None}}

    def run(args, *, store=None):
        calls.append(args.task_id)
        decision = worker_dispatch.evaluate(WORKER)
        if not decision.allowed:
            raise worker_dispatch.WorkerDispatchDenied(decision.reason_code)
        store.tasks[args.task_id]['developer'] = dict(outcome['developer'])
        return 0

    monkeypatch.setattr(task_submission_cli, 'run_task_delegate', run)
    return Namespace(calls=calls, outcome=outcome)


def _args():
    return Namespace(task_id=TASK, provider='claude', pm_session=None)


def _quota(store, worker=WORKER):
    return store.get_worker_observations()[worker]['quota']


def _write_fail(store, *, observed, fresh_until, condition='QUOTA_EXHAUSTED', **extra):
    store.record_worker_observation(
        worker_key=WORKER, layer='quota', status='FAIL',
        observed_at=_stamp(observed), fresh_until=_stamp(fresh_until),
        source='TEST_FIXTURE', evidence_sha256=DIGEST, condition=condition, **extra)


def _write_fresh_fail(store):
    observed = _now() - timedelta(minutes=5)
    _write_fail(store, observed=observed, fresh_until=observed + timedelta(hours=6))


# Owner case 1: ALLOWED + available -> dispatch.
def test_allowed_and_available_dispatches_and_records_pass(store, policy, delegate):
    assert _quota(store)['status'] == 'UNKNOWN'
    assert task_submission_cli.run_task_delegate_checked(_args(), store=store) == 0
    assert delegate.calls == [TASK]
    assert _quota(store)['status'] == 'PASS'


# Owner case 2: ALLOWED + fresh quota FAIL -> refuse before any Task/D6 write.
def test_allowed_and_fresh_fail_refuses_before_any_write(store, policy, delegate):
    _write_fresh_fail(store)
    with pytest.raises(worker_availability.WorkerQuotaUnavailable) as caught:
        task_submission_cli.run_task_delegate_checked(_args(), store=store)
    assert caught.value.code == 'WORKER_QUOTA_UNAVAILABLE'
    assert caught.value.reset_at is None
    assert delegate.calls == []
    assert store.task_writes == []
    # No developer record, so no repair or review count is used.
    assert store.tasks[TASK]['developer'] is None
    # The Owner decision is unchanged and still ALLOWED.
    assert worker_dispatch.evaluate(WORKER).allowed is True
    assert worker_dispatch.evaluate(WORKER).reason_code == 'OWNER_POLICY_ALLOWED'
    assert _quota(store)['status'] == 'FAIL'


# Owner case 3: PAUSED + available -> still refused by policy.
def test_paused_and_available_is_refused_by_policy(store, policy, delegate):
    policy['dispatch'] = 'PAUSED'
    with pytest.raises(worker_dispatch.WorkerDispatchDenied) as caught:
        task_submission_cli.run_task_delegate_checked(_args(), store=store)
    assert caught.value.dispatch_code == 'OWNER_PAUSED'
    assert store.tasks[TASK]['developer'] is None
    # A policy refusal is not a quota outcome.
    assert _quota(store)['status'] == 'UNKNOWN'


def test_paused_with_fresh_fail_is_refused_by_policy_first(store, policy, delegate):
    policy['dispatch'] = 'PAUSED'
    _write_fresh_fail(store)
    with pytest.raises(worker_dispatch.WorkerDispatchDenied) as caught:
        task_submission_cli.run_task_delegate_checked(_args(), store=store)
    assert caught.value.dispatch_code == 'OWNER_PAUSED'
    assert delegate.calls == []
    assert store.task_writes == []


# Owner case 4: recovered + ALLOWED -> dispatch.
def test_recovered_and_allowed_dispatches(store, policy, delegate):
    _write_fresh_fail(store)
    with pytest.raises(worker_availability.WorkerQuotaUnavailable):
        task_submission_cli.run_task_delegate_checked(_args(), store=store)
    other = dict(store.tasks[TASK], task_id='5e' * 16,
                 developer={'assignment_id': '6f' * 16,
                            'state': 'DEVELOPER_RESPONDED', 'error_code': None})
    assert worker_availability.record_outcome(store, WORKER, other) == 'PASS'
    assert task_submission_cli.run_task_delegate_checked(_args(), store=store) == 0
    assert delegate.calls == [TASK]
    assert _quota(store)['status'] == 'PASS'


def test_expired_fail_reads_unknown_never_pass_and_does_not_block(store, policy, delegate):
    observed = _now() - timedelta(hours=7)
    _write_fail(store, observed=observed, fresh_until=observed + timedelta(hours=6))
    view = _quota(store)
    assert view['status'] == 'UNKNOWN'
    assert 'condition' not in view and 'reset_at' not in view
    assert worker_availability.quota_gate(store, WORKER)['status'] == 'UNKNOWN'
    assert task_submission_cli.run_task_delegate_checked(_args(), store=store) == 0


def test_expired_pass_reads_unknown(store):
    observed = _now() - timedelta(hours=7)
    task = {'task_id': TASK, 'developer': {'state': 'DEVELOPER_RESPONDED',
                                           'error_code': None}}
    assert worker_availability.record_outcome(store, WORKER, task, now=observed) == 'PASS'
    assert _quota(store)['status'] == 'UNKNOWN'


def test_absent_record_reads_unknown_and_does_not_block(store):
    assert worker_availability.quota_gate(store, WORKER)['status'] == 'UNKNOWN'


@pytest.mark.parametrize('state, code', [
    ('BLOCKED', 'CLAUDE_TIMEOUT'),
    ('BLOCKED', 'CLAUDE_EXIT_NONZERO'),
    ('BLOCKED', 'CLAUDE_WRITE_SCOPE_VIOLATION'),
    ('DEVELOPER_UNAVAILABLE', 'DEVELOPER_PROVIDER_UNAVAILABLE'),
    ('DEVELOPER_UNAVAILABLE', 'REMOTE_TEST_FAILED'),
    ('DEVELOPER_UNAVAILABLE', 'D6_SOURCE_BINDING_MISMATCH'),
    ('DEVELOPER_OUTCOME_UNKNOWN', 'DEVELOPER_PROVIDER_UNAVAILABLE'),
    ('BLOCKED', 'OWNER_PAUSED'),
    ('DEVELOPER_UNAVAILABLE', None),
])
def test_non_quota_failure_never_writes_fail(store, state, code):
    task = {'task_id': TASK, 'developer': {'state': state, 'error_code': code}}
    assert worker_availability.record_outcome(store, WORKER, task) is None
    assert _quota(store)['status'] == 'UNKNOWN'


def test_non_quota_failure_after_pass_leaves_pass(store):
    passed = {'task_id': TASK, 'developer': {'state': 'DEVELOPER_RESPONDED',
                                             'error_code': None}}
    worker_availability.record_outcome(store, WORKER, passed)
    failed = {'task_id': TASK, 'developer': {'state': 'BLOCKED',
                                             'error_code': 'CLAUDE_TIMEOUT'}}
    assert worker_availability.record_outcome(store, WORKER, failed) is None
    assert _quota(store)['status'] == 'PASS'


def test_delegate_timeout_through_checked_writes_nothing(store, policy, delegate):
    delegate.outcome['developer'] = {'assignment_id': '4d' * 16, 'state': 'BLOCKED',
                                     'error_code': 'CLAUDE_TIMEOUT'}
    task_submission_cli.run_task_delegate_checked(_args(), store=store)
    assert _quota(store)['status'] == 'UNKNOWN'


@pytest.mark.parametrize('code, condition', [
    ('CLAUDE_QUOTA_EXHAUSTED', 'QUOTA_EXHAUSTED'),
    ('AGY_QUOTA_EXHAUSTED', 'QUOTA_EXHAUSTED'),
    ('GW_AGENT_RATE_LIMITED', 'RATE_LIMITED'),
])
def test_quota_code_writes_fail_fresh_for_six_hours(store, code, condition):
    observed = _now() - timedelta(minutes=1)
    task = {'task_id': TASK, 'developer': {'state': 'BLOCKED', 'error_code': code}}
    assert worker_availability.record_outcome(store, WORKER, task, now=observed) == 'FAIL'
    view = _quota(store)
    assert view['status'] == 'FAIL'
    assert view['condition'] == condition
    assert view['fresh_until'] == _stamp(observed + timedelta(hours=6))
    assert 'reset_at' not in view and 'reset_source' not in view


def test_quota_fail_through_checked_then_refuses_next_dispatch(store, policy, delegate):
    delegate.outcome['developer'] = {'assignment_id': '4d' * 16, 'state': 'BLOCKED',
                                     'error_code': 'CLAUDE_QUOTA_EXHAUSTED'}
    task_submission_cli.run_task_delegate_checked(_args(), store=store)
    assert _quota(store)['status'] == 'FAIL'
    with pytest.raises(worker_availability.WorkerQuotaUnavailable):
        task_submission_cli.run_task_delegate_checked(_args(), store=store)
    assert delegate.calls == [TASK]


def test_trusted_reset_sets_fresh_until_and_is_shown(store, policy):
    observed = _now() - timedelta(minutes=1)
    reset = _stamp(observed + timedelta(hours=2))
    task = {'task_id': TASK, 'developer': {'state': 'BLOCKED',
                                           'error_code': 'CLAUDE_QUOTA_EXHAUSTED'}}
    worker_availability.record_outcome(store, WORKER, task, reset_at=reset, now=observed)
    view = _quota(store)
    assert view['fresh_until'] == reset
    assert view['reset_at'] == reset
    assert view['reset_source'] == 'PROVIDER_REPORTED'
    with pytest.raises(worker_availability.WorkerQuotaUnavailable) as caught:
        worker_availability.quota_gate(store, WORKER)
    assert caught.value.reset_at == reset


@pytest.mark.parametrize('reset', ['soon', '2026-11-14 10:00:00', 'past'])
def test_untrusted_reset_is_not_shown(store, reset):
    observed = _now() - timedelta(minutes=1)
    if reset == 'past':
        reset = _stamp(observed - timedelta(minutes=1))
    task = {'task_id': TASK, 'developer': {'state': 'BLOCKED',
                                           'error_code': 'CLAUDE_QUOTA_EXHAUSTED'}}
    worker_availability.record_outcome(store, WORKER, task, reset_at=reset, now=observed)
    view = _quota(store)
    assert view['fresh_until'] == _stamp(observed + timedelta(hours=6))
    assert 'reset_at' not in view and 'reset_source' not in view


@pytest.mark.parametrize('state', ['DEVELOPER_RESPONDED', 'DEVELOPER_OUTPUT_REJECTED'])
def test_provider_result_writes_pass(store, state):
    _write_fresh_fail(store)
    task = {'task_id': TASK, 'developer': {'state': state,
                                           'error_code': 'DEVELOPER_OUTPUT_INVALID_DIFF'
                                           if state != 'DEVELOPER_RESPONDED' else None}}
    assert worker_availability.record_outcome(store, WORKER, task) == 'PASS'
    view = _quota(store)
    assert view['status'] == 'PASS'
    assert 'condition' not in view


def test_unchanged_developer_record_is_not_recorded_again(store):
    store.tasks[TASK]['developer'] = {'state': 'BLOCKED',
                                      'error_code': 'CLAUDE_QUOTA_EXHAUSTED'}
    before = worker_availability.developer_snapshot(store, TASK)
    assert worker_availability.record_delegate_outcome(store, WORKER, TASK, before) is None
    assert _quota(store)['status'] == 'UNKNOWN'


def test_uncovered_routes_are_not_gated():
    assert worker_availability.delegate_worker_key('company-gw') is None
    assert worker_availability.delegate_worker_key('company-gw-agent') is None
    assert worker_availability.delegate_worker_key(None) is None
    assert worker_availability.delegate_worker_key('claude') == WORKER


# TaskStore: strict quota-layer fields; old sidecars stay valid.
@pytest.mark.parametrize('layer, status, extra', [
    ('quota', 'FAIL', {}),                                     # FAIL needs a condition
    ('quota', 'FAIL', {'condition': 'OTHER'}),
    ('quota', 'PASS', {'condition': 'QUOTA_EXHAUSTED'}),       # only FAIL has one
    ('runtime', 'FAIL', {'condition': 'QUOTA_EXHAUSTED'}),     # quota layer only
    ('quota', 'FAIL', {'condition': 'QUOTA_EXHAUSTED', 'reset_at': 'R'}),
    ('quota', 'FAIL', {'condition': 'QUOTA_EXHAUSTED', 'reset_at': 'F',
                       'reset_source': 'LOCAL_GUESS'}),
    ('quota', 'FAIL', {'condition': 'QUOTA_EXHAUSTED', 'reset_at': 'R',
                       'reset_source': 'PROVIDER_REPORTED'}),  # must equal fresh_until
])
def test_quota_fields_validate_strictly(store, layer, status, extra):
    observed = _now() - timedelta(minutes=1)
    fresh = observed + timedelta(hours=6)
    other = observed + timedelta(hours=2)
    values = {key: (_stamp(other) if value == 'R' else
                    _stamp(fresh) if value == 'F' else value)
              for key, value in extra.items()}
    with pytest.raises(LLMToolError):
        store.record_worker_observation(
            worker_key=WORKER, layer=layer, status=status,
            observed_at=_stamp(observed), fresh_until=_stamp(fresh),
            source='TEST_FIXTURE', evidence_sha256=DIGEST, **values)
    assert _quota(store)['status'] == 'UNKNOWN'


def test_old_sidecar_stays_valid(store):
    store.get_worker_observations()   # creates the owner-only store root
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
    fd = os.open(store.root / WORKER_AVAILABILITY_NAME,
                 os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(_json_bytes(old))
    view = store.get_worker_observations()[WORKER]
    assert view['runtime']['status'] == 'PASS'
    assert set(view['runtime']) == {'status', 'observed_at', 'fresh_until', 'source',
                                    'evidence_sha256'}
    assert view['quota']['status'] == 'UNKNOWN'


# Review-repair delegate is gated before any repair Task, claim or count.
@pytest.fixture
def repair_setup(monkeypatch, store):
    store.tasks[TASK] = {
        'task_id': TASK, 'review_repair_claims': [],
        'developer': {'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'opencode',
                      'owner_assignment_ref': None},
        'completion': {'executor_task_id': EXECUTOR},
    }
    monkeypatch.setattr(review_repair, '_failed_event',
                        lambda task, **kwargs: ({'failed_head': HEAD}, None))
    monkeypatch.setattr(review_repair, '_current_d6_head', lambda *args: HEAD)
    monkeypatch.setattr(review_repair, 'dispute_open_on', lambda *args: False)
    spies = Namespace(recorded=[], dispatched=[])

    class _ReachedCount(Exception):
        pass

    def task_ids():
        raise _ReachedCount()

    monkeypatch.setattr(store, 'task_ids', task_ids)

    def run():
        return review_repair.repair_review(
            store=store, task_id=TASK, executor_task=EXECUTOR, pr_number=7,
            current_head=HEAD,
            repair_task_recorder=lambda **kwargs: spies.recorded.append(kwargs),
            dispatch_adapter=lambda **kwargs: spies.dispatched.append(kwargs))

    return Namespace(run=run, spies=spies, reached=_ReachedCount)


def test_review_repair_refused_on_fresh_fail(store, policy, repair_setup):
    store.record_worker_observation(
        worker_key='opencode-developer', layer='quota', status='FAIL',
        observed_at=_stamp(_now() - timedelta(minutes=1)),
        fresh_until=_stamp(_now() + timedelta(hours=5)),
        source='TEST_FIXTURE', evidence_sha256=DIGEST, condition='RATE_LIMITED')
    with pytest.raises(LLMToolError) as caught:
        repair_setup.run()
    assert caught.value.review_repair_code == 'WORKER_QUOTA_UNAVAILABLE'
    assert repair_setup.spies.recorded == []
    assert repair_setup.spies.dispatched == []
    assert store.task_writes == []
    assert store.tasks[TASK]['review_repair_claims'] == []


def test_review_repair_passes_gate_when_available(store, policy, repair_setup):
    # Past the gate the repair reads its counts; the stub stops it there.
    with pytest.raises(repair_setup.reached):
        repair_setup.run()
    assert store.task_writes == []


def test_review_repair_paused_refused_by_policy(store, policy, repair_setup):
    policy['dispatch'] = 'PAUSED'
    with pytest.raises(LLMToolError) as caught:
        repair_setup.run()
    assert caught.value.review_repair_code == 'OWNER_PAUSED'
