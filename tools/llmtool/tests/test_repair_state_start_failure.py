"""#2188 follow-up: a repair whose Claude Developer failed before running is BLOCKED.

A repair Task whose Claude Developer stopped with CLAUDE_STATUS_FAILED, with no
native session id and no diff, never ran, so the advance loop may retry it.
A run with a native session or a diff stays UNKNOWN.

Test data rule (Owner #763 A): no test here holds a password-, API-key-,
token- or other credential-shaped string.
"""

import unittest

from llmtool import task_advance

import review_trust_fixture
import worker_policy_fixture


_SESSION = '12345678-1234-4234-8234-123456789abc'
_HEAD = 'a' * 40


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
                          'execution': execution}}


class RepairStateStartFailureTests(unittest.TestCase):

    def test_start_failure_maps_to_blocked(self):
        self.assertEqual(task_advance.derive_repair_state(_record()), 'BLOCKED')
        # A record without the optional keys is read the same way.
        execution = _execution()
        del execution['native_session_id']
        self.assertEqual(task_advance.derive_repair_state(_record(execution=execution)),
                         'BLOCKED')
        # Only the developer field is updated, so the top level may read PM_PENDING.
        self.assertEqual(task_advance.derive_repair_state(_record(top_state='PM_PENDING')),
                         'BLOCKED')

    def test_native_session_id_stays_unknown(self):
        record = _record(execution=_execution(native_session_id=_SESSION))
        self.assertEqual(task_advance.derive_repair_state(record), 'UNKNOWN')

    def test_diff_stays_unknown(self):
        record = _record(execution=_execution(diff_sha256='d' * 64))
        self.assertEqual(task_advance.derive_repair_state(record), 'UNKNOWN')
        record = _record(execution=_execution(native_session_id=_SESSION,
                                              diff_sha256='d' * 64))
        self.assertEqual(task_advance.derive_repair_state(record), 'UNKNOWN')

    def test_missing_execution_record_is_unchanged(self):
        # Without an execution record nothing shows Claude never ran.
        for execution in (None, 'BLOCKED', []):
            with self.subTest(execution=execution):
                record = _record()
                record['developer']['execution'] = execution
                self.assertEqual(task_advance.derive_repair_state(record), 'UNKNOWN')

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

    def test_start_failure_gives_repair_retry(self):
        def action(record):
            related = {'repair_claims': [{
                'head_sha': _HEAD, 'state': 'DISPATCHED',
                'repair_state': task_advance.derive_repair_state(record)}]}
            return task_advance._remote_fail_action(related, _HEAD)

        self.assertEqual(action(_record()), 'REPAIR_RETRY')
        self.assertEqual(action(_record(execution=_execution(native_session_id=_SESSION))),
                         'REPAIR_OUTCOME_UNKNOWN')


if __name__ == '__main__':
    unittest.main()
