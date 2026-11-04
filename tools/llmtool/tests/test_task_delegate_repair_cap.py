"""Tests for the High Debug evidence required beyond the delegate repair cap."""

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
                'developer': {'state': 'DEVELOPER_RESPONDED'},
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


def _entry(*, executor_task=_EXECUTOR_TASK, status='RCA_CONFIRMED'):
    from llmtool.high_debug import _d6_name

    return {
        'status': status,
        'd6': _d6_name(executor_task, 1, _HEAD),
        'examined_sha': _HEAD,
    }


def _diagnosis(entries):
    return {'task_id': _HIGH_DEBUG_TASK, 'high_debug': entries}


def _args(**overrides):
    values = {
        'task_id': _CURRENT_TASK,
        'executor_task': _EXECUTOR_TASK,
        'high_debug_ref': _HIGH_DEBUG_REF,
        'high_debug_task': _HIGH_DEBUG_TASK,
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

    def _at_cap(self, **kwargs):
        return _Store(count=1 + MAX_REPAIR_ATTEMPTS, **kwargs)

    def test_below_cap_ignores_high_debug_task_option(self):
        store = _Store(count=MAX_REPAIR_ATTEMPTS)
        args = _args(high_debug_task='invalid')

        task_submission_cli._check_delegate_repair_cap(args, store)

        self.assertEqual(store.recorded, [])

    def test_missing_high_debug_task_is_refused(self):
        store = self._at_cap(diagnosis=_diagnosis([_entry()]))

        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_REQUIRED'):
            task_submission_cli._check_delegate_repair_cap(
                _args(high_debug_task=None), store)
        self.assertEqual(store.recorded, [])

    def test_unreadable_high_debug_task_is_refused(self):
        store = self._at_cap(diagnosis=None, unreadable=True)

        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_INVALID'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)

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


if __name__ == '__main__':
    unittest.main()
