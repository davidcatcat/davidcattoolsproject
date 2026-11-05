"""Tests for the High Debug evidence required beyond the delegate repair cap."""

import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import task_submission_cli
from llmtool.errors import LLMToolError
from llmtool.run_budget import MAX_REPAIR_ATTEMPTS


_CURRENT_TASK = 'f' * 32
_HIGH_DEBUG_TASK = 'e' * 32
_EXECUTOR_TASK = 'executor-d6'
_HIGH_DEBUG_REF = 'https://github.com/example/project/issues/12/comments/34'
_HEAD = 'a' * 40


class _Store:
    def __init__(self, *, count, diagnosis=None, unreadable=False):
        self.records = {
            f'{number:032x}': {
                'completion': {'executor_task_id': _EXECUTOR_TASK},
                'developer': {'state': 'DEVELOPER_RESPONDED',
                              'created_at': '2026-11-04T00:00:00Z'},
            }
            for number in range(count)
        }
        if diagnosis is not None:
            self.records[_HIGH_DEBUG_TASK] = diagnosis
        elif unreadable:
            # Keep the unreadable Task addressable without persisting a None
            # record; get() raises for it below.
            self.records[_HIGH_DEBUG_TASK] = {}
        self.unreadable = unreadable
        self.recorded = []

    def task_ids(self):
        return list(self.records)

    def get(self, task_id):
        if task_id == _HIGH_DEBUG_TASK and self.unreadable:
            raise OSError('unreadable')
        return self.records[task_id]

    def record_repair_cap_override(self, task_id, *, ref, rounds):
        self.recorded.append((task_id, ref, rounds))


def _entry(*, executor_task=_EXECUTOR_TASK, status='RCA_CONFIRMED',
           examined_sha=_HEAD, family='pr-938', recorded_at='2026-11-04T00:00:01Z'):
    from llmtool.high_debug import _d6_name

    return {
        'status': status,
        'family': family,
        'failed_corrections': 2,
        'early_reason': None,
        'd6': _d6_name(executor_task, 1, examined_sha),
        'examined_sha': examined_sha,
        'model_requested': 'gpt-6.1-sol',
        'model_observed': 'gpt-6.1-sol',
        'effort_observed': 'high',
        'report': 'Confirmed root cause.',
        'report_sha256': hashlib.sha256(
            b'Confirmed root cause.').hexdigest(),
        'usage': 'UNKNOWN',
        'recorded_at': recorded_at,
    }


def _diagnosis(entries):
    return {'task_id': _HIGH_DEBUG_TASK, 'high_debug': entries}


def _args(**overrides):
    values = {
        'task_id': _CURRENT_TASK,
        'executor_task': _EXECUTOR_TASK,
        'high_debug_ref': _HIGH_DEBUG_REF,
        'high_debug_task': _HIGH_DEBUG_TASK,
        'high_debug_family': 'pr-938',
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class DelegateRepairCapTests(unittest.TestCase):
    def setUp(self):
        self.trust_patch = patch.object(
            task_submission_cli.trust, 'load_trust',
            return_value=SimpleNamespace(repository={'slug': 'example/project'}))
        self.trust_patch.start()
        self.addCleanup(self.trust_patch.stop)
        self.binding_patch = patch.object(
            task_submission_cli, '_delegate_d6_binding',
            return_value=(None, SimpleNamespace(head=_HEAD)))
        self.binding_patch.start()
        self.addCleanup(self.binding_patch.stop)

    def _at_cap(self, **kwargs):
        return _Store(count=1 + MAX_REPAIR_ATTEMPTS, **kwargs)

    @staticmethod
    def _assignments(store):
        return [record['developer'] for record in store.records.values()
                if type(record.get('developer')) is dict]

    def test_three_no_work_records_do_not_reach_cap(self):
        store = _Store(count=3)
        assignments = self._assignments(store)
        assignments[0].update(state='DEVELOPER_PENDING')
        assignments[1].update(state='DEVELOPER_UNAVAILABLE')
        assignments[2].update(state='DEVELOPER_OUTCOME_UNKNOWN',
            error_code='CODEX_BLOCKED', execution={'changed_files': False})

        task_submission_cli._check_delegate_repair_cap(_args(), store)

        self.assertEqual(store.recorded, [])

    def test_two_real_rounds_and_no_work_stay_below_cap(self):
        store = _Store(count=3)
        self._assignments(store)[2].update(state='DEVELOPER_PENDING')

        task_submission_cli._check_delegate_repair_cap(_args(), store)

        self.assertEqual(store.recorded, [])

    def test_three_real_rounds_reach_cap_and_require_high_debug(self):
        store = self._at_cap()

        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_REPAIR_LIMIT_REACHED'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.recorded, [])

    def test_newer_no_work_assignment_still_dates_high_debug(self):
        store = self._at_cap(diagnosis=_diagnosis([_entry()]))
        store.records['00000000000000000000000000000003'] = {
            'completion': {'executor_task_id': _EXECUTOR_TASK},
            'developer': {'state': 'DEVELOPER_PENDING',
                          'created_at': '2026-11-04T00:00:02Z'},
        }

        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_INVALID'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.recorded, [])

    def test_below_cap_ignores_high_debug_task_option(self):
        store = _Store(count=MAX_REPAIR_ATTEMPTS)
        args = _args(high_debug_task='invalid')

        task_submission_cli._check_delegate_repair_cap(args, store)

        self.assertEqual(store.recorded, [])

    def test_below_cap_rejects_unreadable_record(self):
        store = _Store(count=MAX_REPAIR_ATTEMPTS, unreadable=True)
        with self.assertRaisesRegex(
                LLMToolError, 'DELEGATE_REPAIR_COUNT_UNAVAILABLE'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.recorded, [])

    def test_unreadable_diagnosis_that_may_be_counted_fails_closed(self):
        store = _Store(count=MAX_REPAIR_ATTEMPTS, unreadable=True)
        store.records.pop(_HIGH_DEBUG_TASK, None)
        store.records[_HIGH_DEBUG_TASK] = {
            'completion': {'executor_task_id': _EXECUTOR_TASK},
            'developer': {'created_at': '2026-11-04T00:00:00Z'},
        }

        with self.assertRaisesRegex(
                LLMToolError, 'DELEGATE_REPAIR_COUNT_UNAVAILABLE'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.recorded, [])

    def test_missing_high_debug_task_is_refused(self):
        store = self._at_cap(diagnosis=_diagnosis([_entry()]))

        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_REQUIRED'):
            task_submission_cli._check_delegate_repair_cap(
                _args(high_debug_task=None), store)
        self.assertEqual(store.recorded, [])

    def test_unreadable_high_debug_task_is_refused(self):
        store = self._at_cap(diagnosis=None, unreadable=True)

        with self.assertRaisesRegex(
                LLMToolError, 'DELEGATE_REPAIR_COUNT_UNAVAILABLE'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.recorded, [])

    def test_task_without_high_debug_entry_is_refused(self):
        store = self._at_cap(diagnosis=_diagnosis([]))

        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_INVALID'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)

    def test_last_high_debug_entry_must_be_confirmed(self):
        store = self._at_cap(diagnosis=_diagnosis([
            _entry(), _entry(status='RUN_FAILED')]))

        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_INVALID'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)

    def test_high_debug_entry_must_derive_from_repair_executor(self):
        store = self._at_cap(diagnosis=_diagnosis([_entry(executor_task='other-d6')]))

        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_INVALID'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)

    def test_confirmed_high_debug_entry_allows_recording_existing_override_shape(self):
        store = self._at_cap(diagnosis=_diagnosis([_entry()]))

        task_submission_cli._check_delegate_repair_cap(_args(), store)

        self.assertEqual(store.recorded, [(
            _CURRENT_TASK, _HIGH_DEBUG_REF,
            max(3, (1 + MAX_REPAIR_ATTEMPTS) + 1))])

    def test_refusal_order_checks_reference_before_task(self):
        store = self._at_cap(diagnosis=_diagnosis([_entry()]))
        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_REPAIR_LIMIT_REACHED'):
            task_submission_cli._check_delegate_repair_cap(
                _args(high_debug_ref=None, high_debug_task=None), store)
        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_REF_INVALID'):
            task_submission_cli._check_delegate_repair_cap(
                _args(high_debug_ref='bad', high_debug_task=None), store)
        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_REQUIRED'):
            task_submission_cli._check_delegate_repair_cap(
                _args(high_debug_task=None), store)

    def test_head_family_timestamp_and_reuse_are_rejected_without_override(self):
        cases = (
            (_entry(examined_sha='b' * 40), 'DELEGATE_HIGH_DEBUG_TASK_INVALID', 'pr-938'),
            (_entry(family='other'), 'DELEGATE_HIGH_DEBUG_TASK_INVALID', 'pr-938'),
            (_entry(recorded_at='2026-11-04T00:00:00Z'), 'DELEGATE_HIGH_DEBUG_TASK_INVALID', 'pr-938'),
            (_entry(recorded_at='bad'), 'DELEGATE_HIGH_DEBUG_TASK_INVALID', 'pr-938'),
        )
        for entry, code, family in cases:
            store = self._at_cap(diagnosis=_diagnosis([entry]))
            with self.subTest(entry=entry), self.assertRaisesRegex(LLMToolError, code):
                task_submission_cli._check_delegate_repair_cap(
                    _args(high_debug_family=family), store)
            self.assertEqual(store.recorded, [])

    def test_family_syntax_and_reuse_after_new_assignment_are_rejected(self):
        invalid_family = _entry(family='Bad Family')
        store = self._at_cap(diagnosis=_diagnosis([invalid_family]))
        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_INVALID'):
            task_submission_cli._check_delegate_repair_cap(
                _args(high_debug_family='Bad Family'), store)
        self.assertEqual(store.recorded, [])

        store = self._at_cap(diagnosis=_diagnosis([_entry()]))
        store.records['00000000000000000000000000000000']['developer']['created_at'] = (
            '2026-11-04T00:00:02Z')
        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_INVALID'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.recorded, [])

    def test_malformed_assignment_time_is_count_unavailable(self):
        store = self._at_cap(diagnosis=_diagnosis([_entry()]))
        store.records['00000000000000000000000000000000']['developer']['created_at'] = 'bad'
        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_REPAIR_COUNT_UNAVAILABLE'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.recorded, [])

    def test_d6_binding_failure_has_distinct_error(self):
        store = self._at_cap(diagnosis=_diagnosis([_entry()]))
        with patch.object(task_submission_cli, '_delegate_d6_binding',
                          side_effect=OSError('unavailable')):
            with self.assertRaisesRegex(
                    LLMToolError, 'D6_SOURCE_BINDING_MISMATCH'):
                task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.recorded, [])


if __name__ == '__main__':
    unittest.main()
