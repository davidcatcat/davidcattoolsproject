"""#1502 WP-D L1 phase 2: delegate repair cap, AI Debug admission and the
continuation link, each over the whole continuation chain.

The store is a TaskStore subclass on a real temporary TaskStore root: its
chain locks are the real flock sidecars; only the Task records are held in
memory (with an atomic compare-and-update), because the run records the cap
counts are shaped for the count, not for full Task validation.
"""
import json
import tempfile
import threading
import time
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import (high_debug, lineage_counts, repair_policy,
                     task_store as task_store_module, task_submission_cli as cli)
from llmtool.errors import LLMToolError
from llmtool.repair_policy import UNKNOWN
from llmtool.task_store import TaskStore, TaskStoreConflict

import review_trust_fixture

AT = '2026-11-14T00:00:00Z'
MAIN = 'e' * 40          # a D6 base on main: nobody's result
A_HEAD = 'a' * 40        # the recorded result HEAD of Task A
OTHER_HEAD = 'c' * 40
D6_X, D6_Y, D6_Z = 'chain-d6-x', 'chain-d6-y', 'chain-d6-z'


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def tid(number):
    return f'{number:032x}'


A, B, C, F, R = tid(0xA1), tid(0xB2), tid(0xC3), tid(0xF4), tid(0xE5)


def task(task_id, *, continues=None, d6=None, result_head=None, source=MAIN,
         high_debug_entries=None):
    """A Task record; with d6 it holds one counted Developer run on that D6."""
    record = {'task_id': task_id, 'developer': None, 'completion': None}
    if continues is not None:
        record['continues'] = continues
    if d6 is not None:
        record['developer'] = {'provider_profile': 'claude', 'state': 'DEVELOPER_RESPONDED',
                               'error_code': None, 'created_at': AT}
        record['completion'] = {'executor_task_id': d6, 'evidence': {
            'source_sha': source, 'result_head': None,
            'result': {'result_head': result_head}}}
    if high_debug_entries is not None:
        record['high_debug'] = list(high_debug_entries)
    return record


class MemoryTaskStore(TaskStore):
    """A real TaskStore root (real chain lock files) with in-memory records."""

    def __init__(self, root, *records, hold=0.0):
        super().__init__(root)
        self.records = {record['task_id']: record for record in records}
        self.guard = threading.Lock()
        self.hold = hold

    def add(self, record):
        with self.guard:
            self.records[record['task_id']] = record

    def get(self, task_id):
        with self.guard:
            if task_id not in self.records:
                raise KeyError(task_id)
            return json.loads(json.dumps(self.records[task_id]))

    def task_ids(self):
        with self.guard:
            return sorted(self.records)

    def completion_d6_task_ids(self, executor_task_id):
        with self.guard:
            return sorted(task_id for task_id, record in self.records.items()
                          if type(record.get('completion')) is dict and
                          record['completion'].get('executor_task_id') == executor_task_id)

    def d6_bind_compare_and_update(self, task_id, *, executor_task_id, expected, **changes):
        time.sleep(self.hold)   # widen the gap between the count and the claim write
        with self.guard:
            record = self.records[task_id]
            if any(record.get(key) != value for key, value in expected.items()):
                raise TaskStoreConflict('conflict')
            record.update(json.loads(json.dumps(changes)))
            return json.loads(json.dumps(record))


def args_for(task_id, d6):
    return Namespace(task_id=task_id, executor_task=d6, repair_packet=None,
                     high_debug_ref=None, high_debug_task=None, high_debug_family=None)


def binding(d6, base=MAIN):
    return (SimpleNamespace(task_id=d6, source_sha=base, root='/nonexistent-d6-root'),
            SimpleNamespace(head=base))


def claim_changes(d6):
    return {'executor_task_id': d6,
            'expected': {'developer': None, 'completion': None},
            'developer': {'provider_profile': 'claude', 'state': 'DEVELOPER_PENDING',
                          'error_code': None, 'created_at': AT},
            'completion': {'executor_task_id': d6,
                           'evidence': {'source_sha': MAIN, 'result_head': None}}}


def lock_busy(store, key):
    """True when another thread cannot take the chain lock within the patched wait."""
    outcome = []

    def probe():
        try:
            with store.chain_lock(key):
                outcome.append(False)
        except Exception:   # the store's bounded-wait busy refusal
            outcome.append(True)
    thread = threading.Thread(target=probe, daemon=True)
    thread.start()
    thread.join(timeout=10)
    return outcome == [True]


class Case(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / 'tasks'
        self.policy = repair_policy.load_policy()
        self.claims = []

    def store(self, *records, hold=0.0):
        return MemoryTaskStore(self.root, *records, hold=hold)

    def claim(self):
        self.claims.append(True)
        return 'claimed'

    def cap(self, store, task_id, d6, base=MAIN, claim=None):
        return cli._check_delegate_repair_cap(args_for(task_id, d6), store,
                                              binding(d6, base), claim=claim)

    def refused(self, call):
        with self.assertRaises(LLMToolError) as caught:
            call()
        return caught.exception

    def second_run_code(self):
        """The refusal at two counted review runs: a root cause, or the cap itself."""
        if 2 >= 1 + self.policy['repair_max']:
            return 'DELEGATE_REPAIR_LIMIT_REACHED'
        return 'DELEGATE_HIGH_DEBUG_TASK_REQUIRED'


class DelegateRepairCapTests(Case):
    def test_runs_on_every_chain_d6_count(self):
        # A ran on D6 X; B continues A and is delegated on its own D6 Y.
        store = self.store(task(A, d6=D6_X), task(B, continues=A))
        self.assertEqual(self.cap(store, B, D6_Y, claim=self.claim), 'claimed')
        # A second run on X: B's delegate on Y now counts two runs.
        store.add(task(R, d6=D6_X))
        store.records[B] = task(B, continues=A)
        error = self.refused(lambda: self.cap(store, B, D6_Y))
        self.assertEqual(str(error), self.second_run_code())
        # The same runs do not count for a Task outside the chain.
        store.add(task(C))
        self.assertIsNone(self.cap(store, C, D6_Y))

    def test_the_cap_is_reached_over_two_d6s(self):
        limit = 1 + self.policy['repair_max']
        # A ran on X, B (continues A) ran on Y; follow-ups on X fill the cap.
        runs = [task(A, d6=D6_X), task(B, continues=A, d6=D6_Y)]
        runs += [task(tid(0x100 + n), d6=D6_X) for n in range(limit - 2)]
        store = self.store(*runs, task(R))
        # R is a repair Task delegated on B's D6 Y: it joins Y's chain.
        error = self.refused(lambda: self.cap(store, R, D6_Y, claim=self.claim))
        self.assertEqual(str(error), 'DELEGATE_REPAIR_LIMIT_REACHED')
        self.assertEqual(self.claims, [])
        self.assertIsNone(store.get(R)['developer'])
        # Y alone holds one run: under the old per-D6 count R would pass.
        self.assertEqual(store.completion_d6_task_ids(D6_Y), [B])

    def test_a_pm_follow_up_run_on_a_chain_d6_is_counted(self):
        # F is a PM follow-up Task with no continues, delegated on A's D6 X.
        store = self.store(task(A, d6=D6_X), task(F, d6=D6_X), task(B, continues=A))
        view = cli._delegate_chain_view(store, B, D6_Y, with_runs=True)
        self.assertEqual(sorted(view['runs']), sorted([A, F]))
        self.assertEqual(view['keys'], [A])
        error = self.refused(lambda: self.cap(store, B, D6_Y, claim=self.claim))
        self.assertEqual(str(error), self.second_run_code())
        self.assertEqual(self.claims, [])

    def test_a_count_in_doubt_refuses_as_at_the_cap(self):
        cases = {
            'missing predecessor': self.store(task(B, continues=A)),
            'fork': self.store(task(A, d6=D6_X), task(B, continues=A),
                               task(C, continues=A)),
            'chain member completion malformed': self.store(
                dict(task(A), completion={'evidence': {}}), task(B, continues=A)),
        }
        for name, store in cases.items():
            with self.subTest(case=name):
                error = self.refused(lambda: self.cap(store, B, D6_Y, claim=self.claim))
                self.assertEqual(str(error), 'DELEGATE_REPAIR_LIMIT_REACHED')
        store = self.store(task(A, d6=D6_X), task(B, continues=A))
        with patch.object(store, 'task_ids', side_effect=OSError('store unreadable')):
            error = self.refused(lambda: self.cap(store, B, D6_Y, claim=self.claim))
        self.assertEqual(str(error), 'DELEGATE_REPAIR_LIMIT_REACHED')
        self.assertEqual(self.claims, [])
        self.assertIsNone(store.get(B)['developer'])

    def test_an_unlinked_task_counts_exactly_as_before(self):
        # C and F have no continues and no successor: only the runs on the
        # delegated D6 Y count, as before phase 2. C's own earlier run on Z,
        # R's run on Z and the unrelated chain A <- B on X are not counted.
        store = self.store(task(A, d6=D6_X), task(B, continues=A, d6=D6_X),
                           task(F, d6=D6_Y), task(C, d6=D6_Z), task(R, d6=D6_Z))
        view = cli._delegate_chain_view(store, C, D6_Y, with_runs=True)
        self.assertEqual(sorted(view['runs']), [F])
        self.assertEqual(sorted(view['members']), sorted([C, F]))
        self.assertEqual(view['keys'], sorted([C, F]))
        self.assertIsNone(self.cap(store, C, D6_Y))

    def test_a_stored_record_that_cannot_be_read_refuses_as_at_the_cap(self):
        # Any stored Task may continue C: a failed read is a real doubt.
        store = self.store(task(A, d6=D6_X), task(C))
        real_get = store.get

        def get(task_id):
            if task_id == A:
                raise OSError('unreadable')
            return real_get(task_id)
        with patch.object(store, 'get', side_effect=get):
            error = self.refused(lambda: self.cap(store, C, D6_Y, claim=self.claim))
        self.assertEqual(str(error), 'DELEGATE_REPAIR_LIMIT_REACHED')
        self.assertEqual(self.claims, [])
        self.assertIsNone(store.get(C)['developer'])

    def test_the_lock_is_held_around_the_count_and_the_claim(self):
        store = self.store(task(A, d6=D6_X), task(B, continues=A))
        seen = []
        real_count = cli._check_delegate_run_count

        def count(*call_args):
            seen.append(('count', lock_busy(store, A)))
            return real_count(*call_args)

        def claim():
            seen.append(('claim', lock_busy(store, A)))
            return 'claimed'
        with patch.object(task_store_module, 'LINEAGE_LOCK_TIMEOUT_SECONDS', 0.2), \
             patch.object(cli, '_check_delegate_run_count', side_effect=count):
            self.assertEqual(self.cap(store, B, D6_Y, claim=claim), 'claimed')
            self.assertFalse(lock_busy(store, A))   # released after the claim
        self.assertEqual(seen, [('count', True), ('claim', True)])

    def test_a_lock_that_cannot_be_taken_refuses_before_any_count_or_claim(self):
        store = self.store(task(A, d6=D6_X), task(B, continues=A))
        with patch.object(task_store_module, 'LINEAGE_LOCK_TIMEOUT_SECONDS', 0.2), \
             patch.object(cli, '_check_delegate_run_count',
                          side_effect=AssertionError('counted without the lock')):
            with store.chain_lock(A):
                error = self.refused(lambda: self.cap(store, B, D6_Y, claim=self.claim))
        self.assertEqual(str(error), 'DELEGATE_CHAIN_LOCK_UNAVAILABLE')
        self.assertEqual(self.claims, [])

    def test_two_concurrent_claims_in_one_chain_admit_exactly_one(self):
        # A ran on X. B (continues A) on Y and C (continues B) on Z claim at
        # once; the second sees the first's run and needs a root cause.
        store = self.store(task(A, d6=D6_X), task(B, continues=A), task(C, continues=B),
                           hold=0.3)
        barrier = threading.Barrier(2)
        results = {}

        def delegate(task_id, d6):
            try:
                barrier.wait(timeout=5)
                results[task_id] = ('ok', cli._claim_d6_under_chain_lock(
                    args_for(task_id, d6), store, binding(d6), task_id,
                    **claim_changes(d6)))
            except LLMToolError as error:
                results[task_id] = ('refused', str(error))
        threads = [threading.Thread(target=delegate, args=pair, daemon=True)
                   for pair in ((B, D6_Y), (C, D6_Z))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(sorted(kind for kind, _value in results.values()),
                         ['ok', 'refused'], results)
        refusal = next(value for kind, value in results.values() if kind == 'refused')
        self.assertEqual(refusal, self.second_run_code())
        claimed = [task_id for task_id in (B, C) if store.get(task_id)['developer']]
        self.assertEqual(len(claimed), 1)

    def test_chain_locks_are_taken_in_sorted_order_and_released_on_failure(self):
        store = self.store()
        first, second = tid(1), tid(2)
        taken, released = [], []
        real = lineage_counts.acquire_chain_lock

        def acquire(lock_store, key):
            if key == second:
                raise LLMToolError('busy')
            release = real(lock_store, key)
            taken.append(key)

            def done():
                released.append(key)
                release()
            return done
        with patch.object(lineage_counts, 'acquire_chain_lock', side_effect=acquire):
            with self.assertRaises(LLMToolError):
                lineage_counts.acquire_chain_locks(store, [second, first])
        self.assertEqual((taken, released), ([first], [first]))
        with patch.object(task_store_module, 'LINEAGE_LOCK_TIMEOUT_SECONDS', 0.2):
            release = lineage_counts.acquire_chain_locks(store, [second, first])
            self.assertTrue(lock_busy(store, first) and lock_busy(store, second))
            release()
            release()   # safe twice
            self.assertFalse(lock_busy(store, first) or lock_busy(store, second))
        with self.assertRaises(LLMToolError):
            lineage_counts.acquire_chain_locks(store, [])


class ChainDebugAdmissionTests(Case):
    def used(self):
        return [{'status': 'RCA_CONFIRMED'}] * self.policy['parent_debug_max']

    def admit(self, store, task_id):
        record = store.get(task_id) if store is not None else task(task_id)
        return high_debug._admit_debug(record, record.get('high_debug', []), store)

    def refused_code(self, store, task_id):
        with self.assertRaises(LLMToolError) as caught:
            self.admit(store, task_id)
        return caught.exception.high_debug_code

    def test_a_second_parent_debug_anywhere_in_the_chain_is_refused(self):
        store = self.store(task(A, high_debug_entries=self.used()),
                           task(B, continues=A), task(C, continues=B))
        for task_id in (B, C):
            with self.subTest(task_id=task_id):
                self.assertEqual(self.refused_code(store, task_id),
                                 'HIGH_DEBUG_DEBUG_PARENT_USED')
        # A Debug used by a successor refuses its predecessor too.
        store = self.store(task(A), task(B, continues=A, high_debug_entries=self.used()))
        self.assertEqual(self.refused_code(store, A), 'HIGH_DEBUG_DEBUG_PARENT_USED')

    def test_an_unused_chain_or_a_run_failed_entry_admits(self):
        store = self.store(task(A, high_debug_entries=[{'status': 'RUN_FAILED'}]),
                           task(B, continues=A), task(C))
        self.assertIsNone(self.admit(store, B))
        self.assertIsNone(self.admit(store, C))
        self.assertEqual(lineage_counts.chain_debug_used(
            store, store.get(B), [], high_debug._DEBUG_USED_STATUSES), 0)

    def test_an_unlinked_task_counts_its_own_entries_as_before(self):
        statuses = high_debug._DEBUG_USED_STATUSES
        store = self.store(task(A, high_debug_entries=self.used()), task(C))
        self.assertIsNone(self.admit(store, C))
        self.assertEqual(self.refused_code(store, A), 'HIGH_DEBUG_DEBUG_PARENT_USED')
        # The Task's own fresh entries are counted, not its stored ones.
        self.assertEqual(lineage_counts.chain_debug_used(
            store, store.get(C), self.used(), statuses), self.policy['parent_debug_max'])
        self.assertEqual(lineage_counts.chain_debug_used(
            store, store.get(A), [], statuses), 0)

    def test_admission_takes_the_store_from_every_caller(self):
        import inspect
        parameter = inspect.signature(high_debug._admit_debug).parameters['store']
        self.assertIs(parameter.default, inspect.Parameter.empty)

    def test_a_chain_in_doubt_refuses(self):
        store = self.store(task(A), task(C))
        with patch.object(store, 'get', side_effect=OSError('unreadable')):
            self.assertEqual(lineage_counts.chain_debug_used(
                store, task(C), [], high_debug._DEBUG_USED_STATUSES), UNKNOWN)
        cases = {
            'missing predecessor': (self.store(task(B, continues=A)), B),
            'malformed predecessor entries': (
                self.store(dict(task(A), high_debug='not a list'), task(B, continues=A)), B),
            'fork': (self.store(task(A), task(B, continues=A), task(C, continues=A)), B),
            'no store': (None, B),
        }
        for name, (store, task_id) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(self.refused_code(store, task_id),
                                 'HIGH_DEBUG_DEBUG_PARENT_USED')
                record = store.get(task_id) if store is not None else task(task_id)
                self.assertEqual(lineage_counts.chain_debug_used(
                    store, record, [], high_debug._DEBUG_USED_STATUSES), UNKNOWN)


class ContinuationLinkTests(Case):
    def test_a_d6_built_on_another_chains_result_refuses_before_any_write(self):
        store = self.store(task(A, d6=D6_X, result_head=A_HEAD), task(C))
        error = self.refused(lambda: self.cap(store, C, D6_Y, base=A_HEAD, claim=self.claim))
        self.assertEqual(str(error), 'CONTINUATION_LINK_REQUIRED')
        self.assertEqual(error.continuation_link_reason, 'RESULT_HEAD_OUTSIDE_CHAIN')
        self.assertEqual(self.claims, [])
        self.assertIsNone(store.get(C)['developer'])

    def test_a_task_that_continues_it_passes(self):
        store = self.store(task(A, d6=D6_X, result_head=A_HEAD), task(B, continues=A))
        self.assertEqual(self.cap(store, B, D6_Y, base=A_HEAD, claim=self.claim), 'claimed')
        self.assertEqual(self.claims, [True])

    def test_a_base_that_is_no_result_passes(self):
        # MAIN is A's source, not its result; a result equal to its own source
        # (a pending claim) is no result either.
        store = self.store(task(A, d6=D6_X, result_head=A_HEAD),
                           task(F, d6=D6_Z, result_head=OTHER_HEAD, source=OTHER_HEAD),
                           task(C))
        self.assertIsNone(self.cap(store, C, D6_Y, base=MAIN))
        self.assertIsNone(self.cap(store, C, D6_Y, base=OTHER_HEAD))

    def test_a_scan_in_doubt_refuses(self):
        def doubt(store, base=MAIN):
            error = self.refused(lambda: self.cap(store, C, D6_Y, base=base, claim=self.claim))
            self.assertEqual(str(error), 'CONTINUATION_LINK_REQUIRED')
            self.assertEqual(error.continuation_link_reason, 'SCAN_IN_DOUBT')
        with self.subTest(case='malformed record'):
            doubt(self.store(dict(task(F), completion='not a completion'), task(C)))
        with self.subTest(case='malformed result'):
            bad = task(F, d6=D6_X)
            bad['completion']['evidence']['result'] = ['not a result']
            doubt(self.store(bad, task(C)))
        with self.subTest(case='over the scan bound'):
            with patch.object(lineage_counts, 'LINK_SCAN_MAX', 1):
                doubt(self.store(task(A), task(C)))
        with self.subTest(case='malformed base'):
            doubt(self.store(task(C)), base=None)
        with self.subTest(case='unreadable record'):
            store = self.store(task(A), task(C))
            with patch.object(store, 'get', side_effect=OSError('unreadable')):
                with self.assertRaises(LLMToolError) as caught:
                    lineage_counts.check_continuation_link(store, {C}, MAIN)
            self.assertEqual(caught.exception.continuation_link_reason, 'SCAN_IN_DOUBT')
        self.assertEqual(self.claims, [])


if __name__ == '__main__':
    unittest.main()
