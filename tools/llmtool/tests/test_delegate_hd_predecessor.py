"""Tests for accepting High Debug from a continuation predecessor (#2190).

When a repair Task continues an earlier Task on a fresh D6, the RCA_CONFIRMED
High Debug that ran on the predecessor's executor D6 must be accepted at
delegate time. High Debug from an unrelated Task or D6 is still refused with
DELEGATE_HIGH_DEBUG_TASK_INVALID, and same-D6 behaviour stays unchanged.
"""

import copy
from datetime import datetime
import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import lineage_counts
from llmtool import task_submission_cli
from llmtool.errors import LLMToolError

from test_task_delegate_repair_cap import (
    _Store, _entry, _diagnosis, _event, _args, _no_high_debug,
    _patch_trust_and_binding, _chain_store, _binding, _lock_patches,
    _assert_released, _use_policy, _record_id,
    _CURRENT_TASK, _HIGH_DEBUG_TASK, _EXECUTOR_TASK, _HIGH_DEBUG_REF, _HEAD,
    _POLICY, _REPAIR_TWO, _REVIEW_LIMIT,
)

_PRED_D6 = 'predecessor-d6'
_PRED_D6_EARLIER = 'earlier-predecessor-d6'
_FRESH_D6 = 'fresh-executor-d6'
_UNRELATED_D6 = 'unrelated-d6'


def _check_continuation_rca(args, store, newest_assignment=None):
    """Run root-cause validation for a continuation Task on a fresh D6 (#2190).

    _check_delegate_repair_cap enforces the per-D6 cap, which is 0 on a fresh
    D6. For continuation repairs across D6s, root-cause binding to predecessor
    D6s is evaluated by _check_repair_rca (as invoked by _check_chain_repair_count).
    """
    if newest_assignment is None:
        newest_stamp = None
        task_id = getattr(args, 'task_id', None)
        if task_id and store:
            try:
                rec = store.get(task_id)
            except Exception:
                rec = None
            if isinstance(rec, dict):
                chain = lineage_counts.chain_records(store, rec)
                if chain:
                    for pred in chain[1:]:
                        dev = pred.get('developer')
                        if isinstance(dev, dict):
                            created_at = dev.get('created_at')
                            if isinstance(created_at, str):
                                try:
                                    s = datetime.strptime(created_at, '%Y-%m-%dT%H:%M:%SZ')
                                    newest_stamp = max(newest_stamp, s) if newest_stamp else s
                                except ValueError:
                                    pass
        newest_assignment = newest_stamp or datetime(2026, 11, 3, 0, 0, 0)
    return task_submission_cli._check_repair_rca(args, store, None, newest_assignment)


class SameD6BehaviourUnchangedTests(unittest.TestCase):
    """Same-D6 High Debug behaviour remains identical to existing requirements."""

    def setUp(self):
        _patch_trust_and_binding(self)

    def test_same_d6_confirmed_high_debug_passes(self):
        store = _Store(count=_REPAIR_TWO, diagnosis=_diagnosis([_entry()]))
        task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.writes, [])

    def test_same_d6_missing_high_debug_refuses_task_required(self):
        store = _Store(count=_REPAIR_TWO, diagnosis=_diagnosis([_entry()]))
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_HIGH_DEBUG_TASK_REQUIRED$'):
            task_submission_cli._check_delegate_repair_cap(_no_high_debug(), store)

    def test_same_d6_unconfirmed_high_debug_refuses_task_invalid(self):
        store = _Store(count=_REPAIR_TWO,
                       diagnosis=_diagnosis([_entry(status='RCA_UNCONFIRMED')]))
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_HIGH_DEBUG_TASK_INVALID$'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)

    def test_same_d6_head_moved_refuses(self):
        store = _Store(count=_REPAIR_TWO,
                       diagnosis=_diagnosis([_entry(examined_sha='b' * 40)]))
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_HIGH_DEBUG_HEAD_MOVED'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)

    def test_require_bound_high_debug_run_same_d6_passes_and_mismatched_fails(self):
        diagnosis = _diagnosis([_entry()])
        entry = diagnosis['high_debug'][0]
        # Same executor matches
        task_submission_cli._require_bound_high_debug_run(
            diagnosis, _HIGH_DEBUG_TASK, entry, _EXECUTOR_TASK)

        # Mismatched executor without predecessors fails closed
        with self.assertRaises(ValueError):
            task_submission_cli._require_bound_high_debug_run(
                diagnosis, _HIGH_DEBUG_TASK, entry, _UNRELATED_D6)


class PredecessorD6HDAcceptedTests(unittest.TestCase):
    """High Debug from verified continuation predecessors is accepted."""

    def setUp(self):
        _patch_trust_and_binding(self)

    def test_predecessor_d6_hd_accepted_for_continues_repair(self):
        # Chain: predecessor (count 1) on _PRED_D6, current Task on _FRESH_D6.
        # Under policy repair_max=1 (or store count at repair #2), this is repair #2.
        store = _chain_store(_PRED_D6)
        # Store another counted run on _PRED_D6 so chain total is 2 (repair #2).
        extra_task = _record_id(0x150)
        store.records[extra_task] = {
            'task_id': extra_task,
            'completion': {'executor_task_id': _PRED_D6},
            'developer': {'state': 'DEVELOPER_RESPONDED',
                          'created_at': '2026-11-04T00:00:00Z'},
            'continues': _record_id(0x100),
        }
        # HD ran on _PRED_D6
        hd_entry = _entry(executor_task=_PRED_D6)
        store.records[_HIGH_DEBUG_TASK] = _diagnosis([hd_entry], executor_task=_PRED_D6)

        args = _args(task_id=_CURRENT_TASK, executor_task=_FRESH_D6)
        _check_continuation_rca(args, store)
        self.assertEqual(store.writes, [])

    def test_multi_step_predecessors_both_accepted(self):
        # Chain: Task 0x100 on _PRED_D6_EARLIER, Task 0x101 on _PRED_D6,
        # current Task _CURRENT_TASK on _FRESH_D6.
        # 2 prior runs in chain: delegates as repair #2.
        for pred_executor in (_PRED_D6_EARLIER, _PRED_D6):
            with self.subTest(pred_executor=pred_executor):
                store = _chain_store(_PRED_D6_EARLIER, _PRED_D6)
                hd_entry = _entry(executor_task=pred_executor)
                store.records[_HIGH_DEBUG_TASK] = _diagnosis(
                    [hd_entry], executor_task=pred_executor)
                args = _args(task_id=_CURRENT_TASK, executor_task=_FRESH_D6)
                _check_continuation_rca(args, store)
                self.assertEqual(store.writes, [])

    def test_predecessor_hd_passes_through_chain_gate(self):
        _lock_patches(self)
        _use_policy(self, repair_max=2)
        store = _chain_store(_PRED_D6_EARLIER, _PRED_D6)
        hd_entry = _entry(executor_task=_PRED_D6)
        store.records[_HIGH_DEBUG_TASK] = _diagnosis([hd_entry], executor_task=_PRED_D6)

        claims = []
        args = _args(task_id=_CURRENT_TASK, executor_task=_FRESH_D6)
        binding = (SimpleNamespace(task_id=_FRESH_D6, root='/d6', source_sha=_HEAD,
                                  trusted_agent_id='codex-developer'),
                   SimpleNamespace(head=_HEAD))

        result = task_submission_cli._delegate_chain_gate(
            args, store, binding, lambda: claims.append(_CURRENT_TASK) or 'CLAIMED')
        self.assertEqual(result, 'CLAIMED')
        self.assertEqual(claims, [_CURRENT_TASK])
        _assert_released(self, store)

    def test_covering_predecessor_hd_admits_repair_three_through_chain_gate(self):
        # #2367 A2/A3: three runs in the chain (on two D6s), so the fresh D6's
        # run is repair #3 under the repository policy. The High Debug taken
        # before repair #2, on an earlier HEAD of a predecessor D6, covers it.
        _lock_patches(self)
        self.assertEqual(_POLICY['repair_max'], 5)
        store = _chain_store(_PRED_D6_EARLIER, _PRED_D6, _PRED_D6)
        store.records[_record_id(0x102)]['developer']['created_at'] = '2026-11-04T00:00:02Z'
        hd_entry = _entry(executor_task=_PRED_D6, examined_sha='b' * 40)
        store.records[_HIGH_DEBUG_TASK] = _diagnosis([hd_entry], executor_task=_PRED_D6)
        claims = []
        args = _args(task_id=_CURRENT_TASK, executor_task=_FRESH_D6)
        binding = (SimpleNamespace(task_id=_FRESH_D6, root='/d6', source_sha=_HEAD,
                                  trusted_agent_id='codex-developer'),
                   SimpleNamespace(head=_HEAD))
        result = task_submission_cli._delegate_chain_gate(
            args, store, binding, lambda: claims.append(_CURRENT_TASK) or 'CLAIMED')
        self.assertEqual(result, 'CLAIMED')
        self.assertEqual(claims, [_CURRENT_TASK])
        _assert_released(self, store)
        # Repair #2 (two runs) still needs the High Debug on the current head.
        store = _chain_store(_PRED_D6_EARLIER, _PRED_D6)
        store.records[_HIGH_DEBUG_TASK] = _diagnosis([hd_entry], executor_task=_PRED_D6)
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_HIGH_DEBUG_HEAD_MOVED'):
            task_submission_cli._delegate_chain_gate(
                args, store, binding, lambda: claims.append('unexpected') or 'CLAIMED')
        self.assertEqual(claims, [_CURRENT_TASK])
        _assert_released(self, store)

    def test_require_bound_high_debug_run_with_predecessors(self):
        diagnosis = _diagnosis([_entry(executor_task=_PRED_D6)], executor_task=_PRED_D6)
        entry = diagnosis['high_debug'][0]

        # Accepted when predecessor_executors contains the predecessor D6
        task_submission_cli._require_bound_high_debug_run(
            diagnosis, _HIGH_DEBUG_TASK, entry, _FRESH_D6,
            predecessor_executors=[_PRED_D6])

        # Accepted when executor_task is passed as an iterable including predecessor D6
        task_submission_cli._require_bound_high_debug_run(
            diagnosis, _HIGH_DEBUG_TASK, entry, [_FRESH_D6, _PRED_D6])


class UnrelatedHDRefusedTests(unittest.TestCase):
    """High Debug from an unrelated Task or D6 is strictly refused."""

    def setUp(self):
        _patch_trust_and_binding(self)

    def test_unrelated_d6_hd_refused_with_task_invalid(self):
        # Chain has predecessor _PRED_D6, but HD is from _UNRELATED_D6.
        store = _chain_store(_PRED_D6_EARLIER, _PRED_D6)
        hd_entry = _entry(executor_task=_UNRELATED_D6)
        store.records[_HIGH_DEBUG_TASK] = _diagnosis(
            [hd_entry], executor_task=_UNRELATED_D6)

        args = _args(task_id=_CURRENT_TASK, executor_task=_FRESH_D6)
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_HIGH_DEBUG_TASK_INVALID$'):
            _check_continuation_rca(args, store)

    def test_d6_name_and_completion_executor_mismatch_refused(self):
        # Entry D6 derived from _PRED_D6, but completion executor is _UNRELATED_D6.
        store = _chain_store(_PRED_D6_EARLIER, _PRED_D6)
        hd_entry = _entry(executor_task=_PRED_D6)
        diagnosis = _diagnosis([hd_entry], executor_task=_UNRELATED_D6)
        store.records[_HIGH_DEBUG_TASK] = diagnosis

        args = _args(task_id=_CURRENT_TASK, executor_task=_FRESH_D6)
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_HIGH_DEBUG_TASK_INVALID$'):
            _check_continuation_rca(args, store)

    def test_broken_continuation_chain_does_not_accept_unverified_predecessor(self):
        store = _chain_store(_PRED_D6)
        extra_task = _record_id(0x150)
        store.records[extra_task] = {
            'task_id': extra_task,
            'completion': {'executor_task_id': _PRED_D6},
            'developer': {'state': 'DEVELOPER_RESPONDED',
                          'created_at': '2026-11-04T00:00:00Z'},
        }
        # Break the link so chain_records returns None
        store.records[_record_id(0x100)]['continues'] = 'unreadable-missing-id'

        hd_entry = _entry(executor_task=_PRED_D6)
        store.records[_HIGH_DEBUG_TASK] = _diagnosis([hd_entry], executor_task=_PRED_D6)

        args = _args(task_id=_CURRENT_TASK, executor_task=_FRESH_D6)
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_HIGH_DEBUG_TASK_INVALID$'):
            task_submission_cli._check_repair_rca(args, store, None, '2026-11-03T00:00:00Z')

    def test_require_bound_high_debug_run_unrelated_executor_raises_value_error(self):
        diagnosis = _diagnosis([_entry(executor_task=_UNRELATED_D6)], executor_task=_UNRELATED_D6)
        entry = diagnosis['high_debug'][0]

        with self.assertRaises(ValueError):
            task_submission_cli._require_bound_high_debug_run(
                diagnosis, _HIGH_DEBUG_TASK, entry, _FRESH_D6,
                predecessor_executors=[_PRED_D6])


class PredecessorHDBindingChecksUnchangedTests(unittest.TestCase):
    """All existing binding checks still apply to predecessor High Debug runs."""

    def setUp(self):
        _patch_trust_and_binding(self)

    def _setup_store(self, hd_entry, executor_task=_PRED_D6):
        store = _chain_store(_PRED_D6_EARLIER, _PRED_D6)
        store.records[_HIGH_DEBUG_TASK] = _diagnosis([hd_entry], executor_task=executor_task)
        return store

    def test_predecessor_hd_unconfirmed_refused(self):
        hd_entry = _entry(executor_task=_PRED_D6, status='RCA_UNCONFIRMED')
        store = self._setup_store(hd_entry)
        args = _args(task_id=_CURRENT_TASK, executor_task=_FRESH_D6)
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_HIGH_DEBUG_TASK_INVALID$'):
            _check_continuation_rca(args, store)

    def test_predecessor_hd_family_mismatch_refused(self):
        hd_entry = _entry(executor_task=_PRED_D6, family='pr-other')
        store = self._setup_store(hd_entry)
        args = _args(task_id=_CURRENT_TASK, executor_task=_FRESH_D6, high_debug_family='pr-938')
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_HIGH_DEBUG_TASK_INVALID$'):
            _check_continuation_rca(args, store)

    def test_predecessor_hd_head_moved_refused(self):
        hd_entry = _entry(executor_task=_PRED_D6, examined_sha='b' * 40)
        store = self._setup_store(hd_entry)
        args = _args(task_id=_CURRENT_TASK, executor_task=_FRESH_D6)
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_HIGH_DEBUG_HEAD_MOVED'):
            _check_continuation_rca(args, store)

    def test_predecessor_hd_stale_timestamp_refused(self):
        store = _chain_store(_PRED_D6_EARLIER, _PRED_D6)
        # Assign newer developer stamp in chain
        store.records[_record_id(0x101)]['developer']['created_at'] = '2026-11-04T00:00:02Z'
        hd_entry = _entry(executor_task=_PRED_D6, recorded_at='2026-11-04T00:00:01Z')
        store.records[_HIGH_DEBUG_TASK] = _diagnosis([hd_entry], executor_task=_PRED_D6)
        args = _args(task_id=_CURRENT_TASK, executor_task=_FRESH_D6)
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_HIGH_DEBUG_TASK_INVALID$'):
            _check_continuation_rca(args, store)


if __name__ == '__main__':
    unittest.main()
