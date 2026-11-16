"""#2188 follow-up: a repair whose Claude Developer stopped with CLAUDE_STATUS_FAILED.

PR #2215 v91: a status failure with no native session id and no diff does not
prove Claude never ran, so derive_repair_state keeps it UNKNOWN. The
review-repair decision retries it only when the repair Task's executor D6,
read fresh with task_completion.collect_git_snapshot, is clean at the claim
head. A dirty D6, a moved HEAD, an unreadable D6 or a run with a native
session id stays REPAIR_OUTCOME_UNKNOWN.

Test data rule (Owner #763 A): no test here holds a password-, API-key-,
token- or other credential-shaped string. The D6 registry and Git reads are
injected; the decision logic under test is the real one.
"""

import unittest
from types import SimpleNamespace
from unittest import mock

from llmtool import task_advance, task_completion

import review_trust_fixture
import worker_policy_fixture


_SESSION = '12345678-1234-4234-8234-123456789abc'
_HEAD = 'a' * 40
_MOVED = 'b' * 40
_EXECUTOR = 'c' * 32
_REPAIR_TASK = 'd' * 32
_ROOT = '/registered/d6'
_COMMON = '/repo/.git'
_BRANCH = 'task-branch'


def setUpModule():
    review_trust_fixture.start_module()
    worker_policy_fixture.start_module()


def tearDownModule():
    worker_policy_fixture.stop_module()
    review_trust_fixture.stop_module()


def _execution(**fields):
    execution = {'status': 'BLOCKED', 'native_session_id': None,
                 'process_exit_code': 1, 'stdout_sha256': None, 'elapsed_ms': 7,
                 'cleanup_status': 'CONFIRMED', 'usage_source': 'CLI_REPORTED',
                 'source_bytes': None, 'source_file_count': None,
                 'source_manifest_sha256': None}
    execution.update(fields)
    return execution


def _record(error_code='CLAUDE_STATUS_FAILED', execution=None, *,
            state='BLOCKED', top_state='PM_RESPONDED'):
    if execution is None:
        execution = _execution()
    return {'state': top_state,
            'developer': {'state': state, 'error_code': error_code,
                          'provider_profile': 'claude', 'artifact': None,
                          'executor_task_id': _EXECUTOR,
                          'execution': execution}}


def _repair_claim(repair_record):
    """Build the repair claim the advance loop builds from a repair record."""
    state = task_advance.derive_repair_state(repair_record)
    claim = {'head_sha': _HEAD, 'state': 'DISPATCHED', 'repair_task_id': _REPAIR_TASK,
             'repair_state': state}
    executor = (task_advance._claude_status_failed_executor(repair_record)
                if state == 'UNKNOWN' else None)
    if executor is not None:
        claim['repair_start_failure_executor'] = executor
    return claim


def _decide(repair_record):
    record = {'developer': {'state': 'DEVELOPER_RESPONDED'},
              'review_dispatch_claim': [{'pr': 7, 'head_sha': _HEAD,
                                         'state': 'COMPLETED', 'version': 1}]}
    d6 = {'root': _ROOT, 'common_dir': _COMMON, 'branch': _BRANCH,
          'result_head': _HEAD, 'recorded_result_head': _HEAD,
          'dirty': False, 'state': 'CLEAN'}
    pr = {'number': 7, 'head': {'sha': _HEAD}}
    related = {'events': [{'source': 'REVIEWER', 'source_sha': _HEAD, 'version': 1,
                           'outcome': 'REVIEW_FAIL', 'findings_count': 1}],
               'repair_claims': [_repair_claim(repair_record)]}
    return task_advance.decide(record, d6, pr, related)


def _registration(task_id, common_dir, *, allowed_states):
    if task_id != _EXECUTOR or common_dir != _COMMON:
        raise AssertionError('unexpected D6 registry read')
    return SimpleNamespace(task_id=task_id, root=_ROOT, common_dir=_COMMON,
                           branch=_BRANCH, result_sha=_HEAD, state='REPAIR')


def _snapshot(head=_HEAD, clean=True, root=_ROOT, common_dir=_COMMON, branch=_BRANCH):
    return task_completion.GitSnapshot(root, common_dir, branch, head, clean)


class _D6Reads:
    """Inject the D6 registry read and the Git snapshot read."""

    def __init__(self, test, snapshot=None, *, snapshot_error=None, registry_error=None):
        self.roots = []

        def read_registration(task_id, common_dir, *, allowed_states):
            if registry_error is not None:
                raise registry_error
            return _registration(task_id, common_dir, allowed_states=allowed_states)

        def collect(root):
            self.roots.append(root)
            if snapshot_error is not None:
                raise snapshot_error
            return snapshot

        for name, value in (('read_d6_registration', read_registration),
                            ('collect_git_snapshot', collect)):
            patcher = mock.patch.object(task_advance.task_completion, name, value)
            patcher.start()
            test.addCleanup(patcher.stop)


class RepairStateStartFailureTests(unittest.TestCase):

    def test_status_failure_stays_unknown(self):
        self.assertEqual(task_advance.derive_repair_state(_record()), 'UNKNOWN')
        execution = _execution()
        del execution['native_session_id']
        self.assertEqual(task_advance.derive_repair_state(_record(execution=execution)),
                         'UNKNOWN')
        # Only the developer field is updated, so the top level may read PM_PENDING.
        self.assertEqual(task_advance.derive_repair_state(_record(top_state='PM_PENDING')),
                         'UNKNOWN')
        for execution in (_execution(native_session_id=_SESSION),
                          _execution(diff_sha256='d' * 64), None):
            with self.subTest(execution=execution):
                self.assertEqual(
                    task_advance.derive_repair_state(_record(execution=execution)), 'UNKNOWN')

    def test_status_failure_executor_only_without_session_or_diff(self):
        self.assertEqual(task_advance._claude_status_failed_executor(_record()), _EXECUTOR)
        execution = _execution()
        del execution['native_session_id']
        self.assertEqual(
            task_advance._claude_status_failed_executor(_record(execution=execution)),
            _EXECUTOR)
        no_executor = _record()
        del no_executor['developer']['executor_task_id']
        cases = (
            _record(execution=_execution(native_session_id=_SESSION)),
            _record(execution=_execution(diff_sha256='d' * 64)),
            _record(error_code='CLAUDE_TIMEOUT'),
            _record(state='DEVELOPER_OUTCOME_UNKNOWN'),
            no_executor,
            None,
        )
        for record in cases:
            with self.subTest(record=record):
                self.assertIsNone(task_advance._claude_status_failed_executor(record))
        for execution in (None, 'BLOCKED', []):
            with self.subTest(execution=execution):
                record = _record()
                record['developer']['execution'] = execution
                self.assertIsNone(task_advance._claude_status_failed_executor(record))

    def test_other_states_and_codes_are_unchanged(self):
        cases = (
            (_record(state='DEVELOPER_DISPATCHING'), 'RUNNING'),
            (_record(error_code='CLAUDE_TIMEOUT'), 'UNKNOWN'),
            (_record(error_code='CLAUDE_TIMEOUT', top_state='PM_PENDING'), 'PENDING'),
            (_record(state='DEVELOPER_UNAVAILABLE'), 'UNAVAILABLE'),
            (_record(state='DEVELOPER_OUTCOME_UNKNOWN',
                     execution=_execution(status='UNKNOWN')), 'UNKNOWN'),
        )
        for record, expected in cases:
            with self.subTest(state=record['developer']['state'],
                              code=record['developer']['error_code']):
                self.assertEqual(task_advance.derive_repair_state(record), expected)

    def test_claude_no_changes_still_maps_to_blocked(self):
        for execution in (_execution(native_session_id=_SESSION, process_exit_code=0),
                          _execution(), None):
            with self.subTest(execution=execution is not None):
                record = _record(error_code='CLAUDE_NO_CHANGES')
                record['developer']['execution'] = execution
                self.assertEqual(task_advance.derive_repair_state(record), 'BLOCKED')


class ReviewRepairRetryTests(unittest.TestCase):

    def test_clean_d6_at_claim_head_retries(self):
        reads = _D6Reads(self, _snapshot())
        self.assertEqual(_decide(_record()), 'REPAIR_RETRY')
        self.assertEqual(reads.roots, [_ROOT])
        # The top level may still read PM_PENDING.
        self.assertEqual(_decide(_record(top_state='PM_PENDING')), 'REPAIR_RETRY')

    def test_dirty_d6_stays_unknown(self):
        reads = _D6Reads(self, _snapshot(clean=False))
        self.assertEqual(_decide(_record()), 'REPAIR_OUTCOME_UNKNOWN')
        self.assertEqual(reads.roots, [_ROOT])

    def test_moved_head_stays_unknown(self):
        _D6Reads(self, _snapshot(head=_MOVED))
        self.assertEqual(_decide(_record()), 'REPAIR_OUTCOME_UNKNOWN')

    def test_snapshot_binding_mismatch_stays_unknown(self):
        for snapshot in (_snapshot(root='/other/d6'), _snapshot(common_dir='/other/.git'),
                         _snapshot(branch='other-branch')):
            with self.subTest(snapshot=snapshot):
                _D6Reads(self, snapshot)
                self.assertEqual(_decide(_record()), 'REPAIR_OUTCOME_UNKNOWN')

    def test_unreadable_d6_stays_unknown(self):
        for kwargs in ({'snapshot_error': OSError('git read failed')},
                       {'registry_error': OSError('registry read failed')}):
            with self.subTest(kwargs=sorted(kwargs)):
                _D6Reads(self, None, **kwargs)
                self.assertEqual(_decide(_record()), 'REPAIR_OUTCOME_UNKNOWN')

    def test_native_session_or_diff_stays_unknown_without_d6_read(self):
        reads = _D6Reads(self, _snapshot())
        for execution in (_execution(native_session_id=_SESSION),
                          _execution(diff_sha256='d' * 64)):
            with self.subTest(execution=execution):
                self.assertEqual(_decide(_record(execution=execution)),
                                 'REPAIR_OUTCOME_UNKNOWN')
        self.assertEqual(reads.roots, [])

    def test_other_unknown_repairs_never_read_d6(self):
        reads = _D6Reads(self, _snapshot())
        record = _record(error_code='CLAUDE_TIMEOUT')
        self.assertEqual(_decide(record), 'REPAIR_OUTCOME_UNKNOWN')
        self.assertEqual(reads.roots, [])

    def test_d6_without_common_dir_stays_unknown(self):
        reads = _D6Reads(self, _snapshot())
        claim = _repair_claim(_record())
        self.assertFalse(task_advance._repair_d6_unchanged(claim, {'root': _ROOT}))
        self.assertFalse(task_advance._repair_d6_unchanged(dict(claim, head_sha='x'),
                                                           {'common_dir': _COMMON}))
        self.assertEqual(reads.roots, [])

    def test_remote_fail_path_is_unchanged(self):
        # The EC2 FAIL path does not read the D6, so it stays UNKNOWN.
        related = {'repair_claims': [_repair_claim(_record())]}
        self.assertEqual(task_advance._remote_fail_action(related, _HEAD),
                         'REPAIR_OUTCOME_UNKNOWN')


if __name__ == '__main__':
    unittest.main()
