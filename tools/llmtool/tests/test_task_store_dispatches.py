"""Tests for TaskStore remote_test_dispatches (#1257 R2b2).

All values are synthetic; no credential-shaped strings are used.
"""
import hashlib
import tempfile
import unittest
from pathlib import Path

from llmtool.errors import LLMToolError
from llmtool import task_store as task_store_module
from llmtool.task_store import TaskStore, new_id
from llmtool.task_submission_cli import _record

import review_trust_fixture


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


INVALID = 'REMOTE_TEST_DISPATCH_INVALID'
CONFLICT = 'REMOTE_TEST_DISPATCH_CONFLICT'
LIMIT = 'REMOTE_TEST_DISPATCH_LIMIT'
NOT_FOUND = 'REMOTE_TEST_DISPATCH_NOT_FOUND'


def dispatch_entry(*, nonce=None, sha=None, executor_task=None, modules=None,
                   dispatched_at=None, run_id=None, state='DISPATCHED', **overrides):
    entry = {
        'nonce': nonce or '1' * 32,
        'sha': sha or 'a' * 40,
        'executor_task': executor_task or 'd6-task-1',
        'modules': modules or ['test_module_a'],
        'dispatched_at': dispatched_at or '2026-11-09T00:00:00Z',
        'run_id': run_id,
        'state': state,
    }
    entry.update(overrides)
    return entry


class TaskStoreDispatchesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TaskStore(Path(self.temp.name).resolve() / 'tasks')
        self.task = self._task()
        self.store.create(self.task)
        self.task_id = self.task['task_id']

    @staticmethod
    def _task():
        request = 'Bounded remote test dispatch task.'
        task_id, run_id = new_id(), new_id()
        request_sha = hashlib.sha256(request.encode()).hexdigest()
        task = _record(
            task_id, run_id, 'pm-' + new_id(), 'pm-profile', request,
            state='PM_RESPONDED', provider_type='openai-compatible', model='pm-model',
            backend='NO_NATIVE_SESSION_ID_EXPOSED', request_sha=request_sha)
        task.update(
            pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                              'base_url': None},
            response='PM returned a bounded task.', error_code=None,
            pm_assignment={'schema_version': 1, 'route': 'codex', 'risk': 'LOW',
                           'goal': 'Produce a proposal diff.',
                           'acceptance': ['the proposal is recorded']},
        )
        return task

    def _record_with(self, *dispatches):
        current = self.store.get(self.task_id)
        value = dict(current, remote_test_dispatches=list(dispatches))
        value['record_sha256'] = task_store_module._digest(value)
        return value

    def test_constants_and_shapes(self):
        self.assertEqual(task_store_module.MAX_REMOTE_TEST_DISPATCHES, 32)
        self.assertEqual(task_store_module._REMOTE_TEST_DISPATCH_RUN_ID_MAX, 2 ** 53)
        self.assertEqual(task_store_module._REMOTE_TEST_DISPATCH_FIELDS, frozenset((
            'nonce', 'sha', 'executor_task', 'modules', 'dispatched_at', 'run_id', 'state')))
        self.assertEqual(task_store_module._REMOTE_TEST_DISPATCH_STATES, frozenset((
            'DISPATCHED', 'RUN_FOUND', 'RECORDED', 'ABANDONED')))
        self.assertIn('remote_test_dispatches', task_store_module.FIELDS)

    def test_record_without_field_validates_normally(self):
        current = self.store.get(self.task_id)
        self.assertNotIn('remote_test_dispatches', current)
        task_store_module._validate(current)

    def test_record_with_empty_dispatches_validates(self):
        record = self._record_with()
        self.assertEqual(record['remote_test_dispatches'], [])
        task_store_module._validate(record)
        task_store_module._validate_remote_test_dispatches(record)

    def test_record_with_valid_dispatches_validates(self):
        entry1 = dispatch_entry(nonce='1' * 32, state='DISPATCHED', run_id=None)
        entry2 = dispatch_entry(nonce='2' * 32, state='RUN_FOUND', run_id=100)
        entry3 = dispatch_entry(nonce='3' * 32, state='RECORDED', run_id=100)
        entry4 = dispatch_entry(nonce='4' * 32, state='ABANDONED', run_id=None)
        entry5 = dispatch_entry(nonce='5' * 32, state='ABANDONED', run_id=100,
                                dispatched_at='2026-11-09T00:00:00.123456Z')
        record = self._record_with(entry1, entry2, entry3, entry4, entry5)
        task_store_module._validate(record)
        task_store_module._validate_remote_test_dispatches(record)

    def test_record_validation_rejects_invalid_list_container(self):
        current = self.store.get(self.task_id)
        for bad_container in ('invalid', 123, True, None, {'nonce': '1' * 32}):
            with self.subTest(bad=bad_container):
                record = dict(current, remote_test_dispatches=bad_container)
                with self.assertRaises(LLMToolError) as caught:
                    task_store_module._validate_remote_test_dispatches(record)
                self.assertEqual(caught.exception.task_store_error_code, INVALID)

    def test_record_validation_rejects_more_than_32_entries(self):
        entries = [
            dispatch_entry(nonce=f'{i:032x}', state='DISPATCHED', run_id=None)
            for i in range(33)
        ]
        record = self._record_with(*entries)
        with self.assertRaises(LLMToolError) as caught:
            task_store_module._validate_remote_test_dispatches(record)
        self.assertEqual(caught.exception.task_store_error_code, INVALID)
        with self.assertRaises(LLMToolError) as caught:
            task_store_module._validate(record)
        self.assertEqual(caught.exception.task_store_error_code, INVALID)

    def test_record_validation_rejects_duplicate_nonces(self):
        entry1 = dispatch_entry(nonce='1' * 32, sha='a' * 40)
        entry2 = dispatch_entry(nonce='1' * 32, sha='b' * 40)
        record = self._record_with(entry1, entry2)
        with self.assertRaises(LLMToolError) as caught:
            task_store_module._validate_remote_test_dispatches(record)
        self.assertEqual(caught.exception.task_store_error_code, INVALID)

    def test_record_validation_rejects_malformed_entries(self):
        valid = dispatch_entry()
        bad_cases = (
            # Missing fields
            {k: v for k, v in valid.items() if k != 'nonce'},
            {k: v for k, v in valid.items() if k != 'state'},
            {k: v for k, v in valid.items() if k != 'modules'},
            # Extra fields
            dict(valid, extra='unexpected'),
            # Nonce violations
            dict(valid, nonce='1' * 31),
            dict(valid, nonce='1' * 33),
            dict(valid, nonce='A' * 32),
            dict(valid, nonce='g' * 32),
            dict(valid, nonce=123),
            dict(valid, nonce=None),
            # Sha violations
            dict(valid, sha='a' * 39),
            dict(valid, sha='a' * 41),
            dict(valid, sha='A' * 40),
            dict(valid, sha='g' * 40),
            dict(valid, sha=None),
            # Executor task violations
            dict(valid, executor_task=''),
            dict(valid, executor_task='-bad-start'),
            dict(valid, executor_task='D6-TASK'),
            dict(valid, executor_task='x' * 97),
            dict(valid, executor_task=None),
            # Modules violations
            dict(valid, modules=[]),
            dict(valid, modules=['not_test']),
            dict(valid, modules=['test_UPPER']),
            dict(valid, modules=['test_b', 'test_a']),  # unsorted
            dict(valid, modules=['test_a', 'test_a']),  # duplicate
            dict(valid, modules=[f'test_mod_{i}' for i in range(9)]),  # > 8
            dict(valid, modules='test_module_a'),  # not a list
            # Dispatched_at violations
            dict(valid, dispatched_at='2026-11-09'),
            dict(valid, dispatched_at='invalid-date'),
            dict(valid, dispatched_at=None),
            # State violations
            dict(valid, state='UNKNOWN'),
            dict(valid, state='PENDING'),
            dict(valid, state=123),
            # Run_id violations
            dict(valid, state='RUN_FOUND', run_id=None),  # RUN_FOUND needs run_id
            dict(valid, state='RECORDED', run_id=None),   # RECORDED needs run_id
            dict(valid, run_id=0),
            dict(valid, run_id=-1),
            dict(valid, run_id=True),
            dict(valid, run_id=False),
            dict(valid, run_id=1.5),
            dict(valid, run_id='100'),
            dict(valid, run_id=2 ** 53 + 1),
        )
        for bad_entry in bad_cases:
            with self.subTest(bad=bad_entry):
                record = self._record_with(bad_entry)
                with self.assertRaises(LLMToolError) as caught:
                    task_store_module._validate_remote_test_dispatches(record)
                self.assertEqual(caught.exception.task_store_error_code, INVALID)

    def test_append_dispatched_entry_success(self):
        entry = dispatch_entry(nonce='1' * 32, state='DISPATCHED', run_id=None)
        saved = self.store.append_remote_test_dispatch(self.task_id, entry)
        self.assertEqual(saved, entry)
        current = self.store.get(self.task_id)
        self.assertEqual(current['remote_test_dispatches'], [entry])

        entry2 = dispatch_entry(nonce='2' * 32, state='DISPATCHED', run_id=None)
        saved2 = self.store.append_remote_test_dispatch(self.task_id, entry2)
        self.assertEqual(saved2, entry2)
        current = self.store.get(self.task_id)
        self.assertEqual(current['remote_test_dispatches'], [entry, entry2])

    def test_append_requires_dispatched_state_and_null_run_id(self):
        cases = (
            dispatch_entry(nonce='1' * 32, state='RUN_FOUND', run_id=100),
            dispatch_entry(nonce='2' * 32, state='RECORDED', run_id=100),
            dispatch_entry(nonce='3' * 32, state='ABANDONED', run_id=None),
            dispatch_entry(nonce='4' * 32, state='DISPATCHED', run_id=100),
            dispatch_entry(nonce='5' * 32, state='UNKNOWN', run_id=None),
            {'bad': 'entry'},
        )
        for bad in cases:
            with self.subTest(bad=bad):
                with self.assertRaises(LLMToolError) as caught:
                    self.store.append_remote_test_dispatch(self.task_id, bad)
                self.assertEqual(caught.exception.task_store_error_code, INVALID)

    def test_append_replay_is_idempotent(self):
        entry = dispatch_entry(nonce='1' * 32, state='DISPATCHED', run_id=None)
        saved1 = self.store.append_remote_test_dispatch(self.task_id, entry)
        self.assertEqual(saved1, entry)

        # Exact replay returns stored entry without creating a duplicate
        saved2 = self.store.append_remote_test_dispatch(self.task_id, entry)
        self.assertEqual(saved2, entry)
        current = self.store.get(self.task_id)
        self.assertEqual(current['remote_test_dispatches'], [entry])

    def test_append_conflict_on_same_nonce_different_fields(self):
        entry = dispatch_entry(nonce='1' * 32, sha='a' * 40)
        self.store.append_remote_test_dispatch(self.task_id, entry)

        different = dispatch_entry(nonce='1' * 32, sha='b' * 40)
        with self.assertRaises(LLMToolError) as caught:
            self.store.append_remote_test_dispatch(self.task_id, different)
        self.assertEqual(caught.exception.task_store_error_code, CONFLICT)

    def test_append_conflict_on_replay_after_update(self):
        entry = dispatch_entry(nonce='1' * 32, state='DISPATCHED', run_id=None)
        self.store.append_remote_test_dispatch(self.task_id, entry)
        self.store.update_remote_test_dispatch(self.task_id, entry['nonce'],
                                              state='RUN_FOUND', run_id=200)

        # Attempting to replay original DISPATCHED entry now conflicts with stored RUN_FOUND
        with self.assertRaises(LLMToolError) as caught:
            self.store.append_remote_test_dispatch(self.task_id, entry)
        self.assertEqual(caught.exception.task_store_error_code, CONFLICT)

    def test_append_limit_at_32_entries(self):
        for i in range(32):
            entry = dispatch_entry(nonce=f'{i:032x}', state='DISPATCHED', run_id=None)
            self.store.append_remote_test_dispatch(self.task_id, entry)

        current = self.store.get(self.task_id)
        self.assertEqual(len(current['remote_test_dispatches']), 32)

        # 33rd entry fails with LIMIT
        entry_33 = dispatch_entry(nonce=f'{32:032x}', state='DISPATCHED', run_id=None)
        with self.assertRaises(LLMToolError) as caught:
            self.store.append_remote_test_dispatch(self.task_id, entry_33)
        self.assertEqual(caught.exception.task_store_error_code, LIMIT)

        # Exact replay of an existing entry when full still succeeds
        existing = dispatch_entry(nonce=f'{0:032x}', state='DISPATCHED', run_id=None)
        replayed = self.store.append_remote_test_dispatch(self.task_id, existing)
        self.assertEqual(replayed, existing)

    def test_update_not_found_on_unknown_nonce(self):
        with self.assertRaises(LLMToolError) as caught:
            self.store.update_remote_test_dispatch(self.task_id, '1' * 32,
                                                  state='RUN_FOUND', run_id=100)
        self.assertEqual(caught.exception.task_store_error_code, NOT_FOUND)

        entry = dispatch_entry(nonce='1' * 32)
        self.store.append_remote_test_dispatch(self.task_id, entry)
        with self.assertRaises(LLMToolError) as caught:
            self.store.update_remote_test_dispatch(self.task_id, '9' * 32,
                                                  state='RUN_FOUND', run_id=100)
        self.assertEqual(caught.exception.task_store_error_code, NOT_FOUND)

    def test_update_dispatched_to_run_found_sets_run_id(self):
        entry = dispatch_entry(nonce='1' * 32)
        self.store.append_remote_test_dispatch(self.task_id, entry)

        # Moving to RUN_FOUND without run_id fails
        with self.assertRaises(LLMToolError) as caught:
            self.store.update_remote_test_dispatch(self.task_id, entry['nonce'],
                                                  state='RUN_FOUND')
        self.assertEqual(caught.exception.task_store_error_code, INVALID)

        # Moving to RUN_FOUND with valid run_id succeeds
        updated = self.store.update_remote_test_dispatch(self.task_id, entry['nonce'],
                                                        state='RUN_FOUND', run_id=5001)
        self.assertEqual(updated['state'], 'RUN_FOUND')
        self.assertEqual(updated['run_id'], 5001)
        self.assertEqual(self.store.get(self.task_id)['remote_test_dispatches'][0], updated)

    def test_update_run_found_to_recorded_keeps_same_run_id(self):
        entry = dispatch_entry(nonce='1' * 32)
        self.store.append_remote_test_dispatch(self.task_id, entry)
        self.store.update_remote_test_dispatch(self.task_id, entry['nonce'],
                                              state='RUN_FOUND', run_id=5001)

        # Different run_id fails
        with self.assertRaises(LLMToolError) as caught:
            self.store.update_remote_test_dispatch(self.task_id, entry['nonce'],
                                                  state='RECORDED', run_id=9999)
        self.assertEqual(caught.exception.task_store_error_code, INVALID)

        # Same run_id or default run_id=None succeeds
        updated = self.store.update_remote_test_dispatch(self.task_id, entry['nonce'],
                                                        state='RECORDED')
        self.assertEqual(updated['state'], 'RECORDED')
        self.assertEqual(updated['run_id'], 5001)

        # Supplying same run_id explicitly is also fine
        repeated = self.store.update_remote_test_dispatch(self.task_id, entry['nonce'],
                                                         state='RECORDED', run_id=5001)
        self.assertEqual(repeated, updated)

    def test_update_dispatched_to_abandoned(self):
        entry = dispatch_entry(nonce='1' * 32)
        self.store.append_remote_test_dispatch(self.task_id, entry)

        # Moving from DISPATCHED to ABANDONED with run_id fails
        with self.assertRaises(LLMToolError) as caught:
            self.store.update_remote_test_dispatch(self.task_id, entry['nonce'],
                                                  state='ABANDONED', run_id=123)
        self.assertEqual(caught.exception.task_store_error_code, INVALID)

        # Without run_id succeeds
        updated = self.store.update_remote_test_dispatch(self.task_id, entry['nonce'],
                                                        state='ABANDONED')
        self.assertEqual(updated['state'], 'ABANDONED')
        self.assertIsNone(updated['run_id'])

    def test_update_run_found_to_abandoned(self):
        entry = dispatch_entry(nonce='1' * 32)
        self.store.append_remote_test_dispatch(self.task_id, entry)
        self.store.update_remote_test_dispatch(self.task_id, entry['nonce'],
                                              state='RUN_FOUND', run_id=5001)

        # Different run_id fails
        with self.assertRaises(LLMToolError) as caught:
            self.store.update_remote_test_dispatch(self.task_id, entry['nonce'],
                                                  state='ABANDONED', run_id=9999)
        self.assertEqual(caught.exception.task_store_error_code, INVALID)

        # Keeping same run_id succeeds
        updated = self.store.update_remote_test_dispatch(self.task_id, entry['nonce'],
                                                        state='ABANDONED', run_id=5001)
        self.assertEqual(updated['state'], 'ABANDONED')
        self.assertEqual(updated['run_id'], 5001)

    def test_update_refuses_illegal_transitions(self):
        # 1. DISPATCHED cannot jump directly to RECORDED
        entry1 = dispatch_entry(nonce='1' * 32)
        self.store.append_remote_test_dispatch(self.task_id, entry1)
        with self.assertRaises(LLMToolError) as caught:
            self.store.update_remote_test_dispatch(self.task_id, entry1['nonce'],
                                                  state='RECORDED', run_id=100)
        self.assertEqual(caught.exception.task_store_error_code, INVALID)

        # 2. RUN_FOUND cannot go back to DISPATCHED
        entry2 = dispatch_entry(nonce='2' * 32)
        self.store.append_remote_test_dispatch(self.task_id, entry2)
        self.store.update_remote_test_dispatch(self.task_id, entry2['nonce'],
                                              state='RUN_FOUND', run_id=200)
        with self.assertRaises(LLMToolError) as caught:
            self.store.update_remote_test_dispatch(self.task_id, entry2['nonce'],
                                                  state='DISPATCHED')
        self.assertEqual(caught.exception.task_store_error_code, INVALID)

        # 3. RECORDED cannot transition to anything else
        entry3 = dispatch_entry(nonce='3' * 32)
        self.store.append_remote_test_dispatch(self.task_id, entry3)
        self.store.update_remote_test_dispatch(self.task_id, entry3['nonce'],
                                              state='RUN_FOUND', run_id=300)
        self.store.update_remote_test_dispatch(self.task_id, entry3['nonce'],
                                              state='RECORDED')
        for next_state in ('DISPATCHED', 'RUN_FOUND', 'ABANDONED'):
            with self.subTest(recorded_to=next_state):
                with self.assertRaises(LLMToolError) as caught:
                    self.store.update_remote_test_dispatch(self.task_id, entry3['nonce'],
                                                          state=next_state)
                self.assertEqual(caught.exception.task_store_error_code, INVALID)

        # 4. ABANDONED cannot transition to anything else
        entry4 = dispatch_entry(nonce='4' * 32)
        self.store.append_remote_test_dispatch(self.task_id, entry4)
        self.store.update_remote_test_dispatch(self.task_id, entry4['nonce'],
                                              state='ABANDONED')
        for next_state in ('DISPATCHED', 'RUN_FOUND', 'RECORDED'):
            with self.subTest(abandoned_to=next_state):
                with self.assertRaises(LLMToolError) as caught:
                    self.store.update_remote_test_dispatch(self.task_id, entry4['nonce'],
                                                          state=next_state)
                self.assertEqual(caught.exception.task_store_error_code, INVALID)

    def test_update_idempotent_repeats(self):
        entry = dispatch_entry(nonce='1' * 32)
        self.store.append_remote_test_dispatch(self.task_id, entry)

        # Repeat DISPATCHED
        r1 = self.store.update_remote_test_dispatch(self.task_id, entry['nonce'],
                                                   state='DISPATCHED')
        self.assertEqual(r1, entry)

        # Move to RUN_FOUND
        rf = self.store.update_remote_test_dispatch(self.task_id, entry['nonce'],
                                                   state='RUN_FOUND', run_id=777)
        # Repeat RUN_FOUND
        r2 = self.store.update_remote_test_dispatch(self.task_id, entry['nonce'],
                                                   state='RUN_FOUND', run_id=777)
        self.assertEqual(r2, rf)
        r3 = self.store.update_remote_test_dispatch(self.task_id, entry['nonce'],
                                                   state='RUN_FOUND')
        self.assertEqual(r3, rf)

        # Move to RECORDED
        rec = self.store.update_remote_test_dispatch(self.task_id, entry['nonce'],
                                                    state='RECORDED')
        # Repeat RECORDED
        r4 = self.store.update_remote_test_dispatch(self.task_id, entry['nonce'],
                                                   state='RECORDED')
        self.assertEqual(r4, rec)

        # Repeat with conflicting run_id fails
        with self.assertRaises(LLMToolError) as caught:
            self.store.update_remote_test_dispatch(self.task_id, entry['nonce'],
                                                  state='RECORDED', run_id=888)
        self.assertEqual(caught.exception.task_store_error_code, INVALID)

    def test_failure_messages_style_no_record_value_echo(self):
        private_marker = 'a' * 40
        entry = dispatch_entry(nonce='1' * 32, sha=private_marker)
        self.store.append_remote_test_dispatch(self.task_id, entry)

        # Conflict on same nonce with different sha
        conflict_entry = dispatch_entry(nonce='1' * 32, sha='b' * 40)
        with self.assertRaises(LLMToolError) as caught:
            self.store.append_remote_test_dispatch(self.task_id, conflict_entry)
        self.assertEqual(caught.exception.task_store_error_code, CONFLICT)
        self.assertNotIn(private_marker, str(caught.exception))
        self.assertNotIn('b' * 40, str(caught.exception))

        # Not found on unknown nonce
        unknown_marker = 'f' * 32
        with self.assertRaises(LLMToolError) as caught:
            self.store.update_remote_test_dispatch(self.task_id, unknown_marker,
                                                  state='RUN_FOUND', run_id=100)
        self.assertEqual(caught.exception.task_store_error_code, NOT_FOUND)
        self.assertNotIn(unknown_marker, str(caught.exception))


if __name__ == '__main__':
    unittest.main()
