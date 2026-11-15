"""#1502 WP-D phase 2 R2: chain views for the delegate repair cap and continuation link."""
import copy
import threading
import time
import unittest
from unittest.mock import Mock, patch

from llmtool import lineage_counts, task_store as task_store_module
from llmtool.errors import LLMToolError
from llmtool.lineage_counts import MAX_CHAIN

BASE = 'a' * 40
OTHER = 'b' * 40
SOURCE = 'c' * 40
EXECUTOR = 'executor-d6'


def tid(number):
    return f'{number:032x}'


class MemoryStore:
    """In-memory Task store that counts its listing and reads."""

    def __init__(self, *records, ids=None, listing_error=False, failing=()):
        self.records = {record['task_id']: record for record in records}
        self.ids = ids
        self.listing_error = listing_error
        self.failing = set(failing)
        self.listed = 0
        self.reads = []

    def task_ids(self):
        self.listed += 1
        if self.listing_error:
            raise OSError('listing unavailable')
        return list(self.ids) if self.ids is not None else sorted(self.records)

    def get(self, task_id):
        self.reads.append(task_id)
        if task_id in self.failing or task_id not in self.records:
            raise KeyError(task_id)
        return copy.deepcopy(self.records[task_id])


def linked(count, start=1):
    """count Tasks tid(start).. ; each continues the one before it."""
    records = []
    for number in range(start, start + count):
        record = {'task_id': tid(number)}
        if number > start:
            record['continues'] = tid(number - 1)
        records.append(record)
    return records


def snapshot(*records):
    return {record['task_id']: copy.deepcopy(record) for record in records}


def forked():
    """B=tid(2) continues A=tid(1); C=tid(3) and D=tid(4) both continue B."""
    return [{'task_id': tid(1)}, {'task_id': tid(2), 'continues': tid(1)},
            {'task_id': tid(3), 'continues': tid(2)},
            {'task_id': tid(4), 'continues': tid(2)}]


class StoreRecordsTests(unittest.TestCase):
    def test_lists_once_and_reads_each_id_once(self):
        records = linked(3)
        store = MemoryStore(*records)
        self.assertEqual(lineage_counts.store_records(store), snapshot(*records))
        self.assertEqual(store.listed, 1)
        self.assertEqual(store.reads, [tid(1), tid(2), tid(3)])

    def test_a_scan_in_doubt_is_none(self):
        records = linked(2)
        cases = {
            'no store': None,
            'listing raises': MemoryStore(*records, listing_error=True),
            'get raises': MemoryStore(*records, failing=[tid(2)]),
            'listed id missing': MemoryStore(*records, ids=[tid(1), tid(9)]),
            'duplicate id': MemoryStore(*records, ids=[tid(1), tid(2), tid(1)]),
            'empty id': MemoryStore(*records, ids=[tid(1), '']),
            'non-str id': MemoryStore(*records, ids=[tid(1), 7]),
        }
        store = MemoryStore(*records)
        store.records[tid(2)] = ['not', 'a', 'record']
        cases['non-dict record'] = store
        for name, store in cases.items():
            with self.subTest(case=name):
                self.assertIsNone(lineage_counts.store_records(store))

    def test_the_scan_is_bounded(self):
        self.assertEqual(lineage_counts.STORE_SCAN_MAX, 20000)
        with patch.object(lineage_counts, 'STORE_SCAN_MAX', 2):
            store = MemoryStore(*linked(3))
            self.assertIsNone(lineage_counts.store_records(store))
            self.assertEqual(store.reads, [])   # refused before any read
            self.assertEqual(len(lineage_counts.store_records(MemoryStore(*linked(2)))), 2)


class ChainInTests(unittest.TestCase):
    def test_singleton_is_its_own_chain_and_key(self):
        records = snapshot({'task_id': tid(1)}, *linked(2, start=5))
        self.assertEqual(lineage_counts.chain_in(records, tid(1)), ([tid(1)], tid(1)))
        store = MemoryStore(*records.values())
        self.assertEqual(lineage_counts.chain_key(store, store.get(tid(1))), tid(1))

    def test_three_task_chain_from_each_member(self):
        chain = linked(3)
        store = MemoryStore(*chain, {'task_id': tid(9)})
        records = lineage_counts.store_records(store)
        expected = {tid(1): [tid(1), tid(2), tid(3)], tid(2): [tid(2), tid(1), tid(3)],
                    tid(3): [tid(3), tid(2), tid(1)]}
        for task_id, order in expected.items():
            with self.subTest(task_id=task_id):
                self.assertEqual(lineage_counts.chain_in(records, task_id), (order, tid(1)))
                self.assertEqual(lineage_counts.chain_key(store, store.get(task_id)), tid(1))

    def test_a_missing_or_malformed_link_is_none(self):
        upper = tid(0xAB).upper()
        cases = {'missing task': (snapshot(*linked(2)), tid(9)),
                 'missing predecessor': (snapshot({'task_id': tid(2), 'continues': tid(1)}),
                                         tid(2)),
                 'self link': (snapshot({'task_id': tid(2), 'continues': tid(2)}), tid(2))}
        for value in (None, 'x' * 32, upper, 7):
            records = snapshot({'task_id': tid(2), 'continues': value}, {'task_id': tid(1)},
                               {'task_id': upper})
            cases[f'malformed link {value!r}'] = (records, tid(2))
        cases['records not a dict'] = (list(linked(2)), tid(1))
        cases['non-str task id'] = (snapshot(*linked(2)), 7)
        for name, (records, task_id) in cases.items():
            with self.subTest(case=name):
                self.assertIsNone(lineage_counts.chain_in(records, task_id))

    def test_a_chain_record_with_a_wrong_id_is_none(self):
        for wrong in (1, 3):   # a predecessor and a successor of tid(2)
            records = snapshot(*linked(3))
            records[tid(wrong)]['task_id'] = tid(0x77)
            with self.subTest(wrong=wrong):
                self.assertIsNone(lineage_counts.chain_in(records, tid(2)))

    def test_a_cycle_is_none_from_each_member(self):
        records = snapshot({'task_id': tid(1), 'continues': tid(2)},
                           {'task_id': tid(2), 'continues': tid(1)})
        for task_id in (tid(1), tid(2)):
            with self.subTest(task_id=task_id):
                self.assertIsNone(lineage_counts.chain_in(records, task_id))

    def test_a_fork_at_a_predecessor_or_at_the_task_is_none(self):
        # From A the fork is at a successor, from B at the Task, from C or D at a predecessor.
        records = snapshot(*forked())
        for number in (1, 2, 3, 4):
            with self.subTest(start=number):
                self.assertIsNone(lineage_counts.chain_in(records, tid(number)))
        # A root fork: B and E both continue A; C continues B.
        records = snapshot(*linked(3), {'task_id': tid(5), 'continues': tid(1)})
        for number in (1, 2, 3, 5):
            with self.subTest(root_fork_start=number):
                self.assertIsNone(lineage_counts.chain_in(records, tid(number)))
        # Without the extra branch the chain is linear again.
        del records[tid(5)]
        self.assertEqual(lineage_counts.chain_in(records, tid(3))[1], tid(1))

    def test_more_than_max_chain_tasks_is_none(self):
        records = snapshot(*linked(MAX_CHAIN + 1))
        for number in (1, 9, MAX_CHAIN + 1):
            with self.subTest(start=number):
                self.assertIsNone(lineage_counts.chain_in(records, tid(number)))
        records = snapshot(*linked(MAX_CHAIN))
        for number in (1, 9, MAX_CHAIN):
            with self.subTest(full_start=number):
                chain_ids, key = lineage_counts.chain_in(records, tid(number))
                self.assertEqual((len(chain_ids), key), (MAX_CHAIN, tid(1)))

    def test_a_non_dict_record_is_none(self):
        records = snapshot(*linked(2))
        records[tid(5)] = 'not a record'
        self.assertIsNone(lineage_counts.chain_in(records, tid(2)))


def done(task_id, executor=EXECUTOR, **evidence):
    record = {'task_id': task_id, 'completion': {'executor_task_id': executor}}
    if evidence:
        record['completion']['evidence'] = evidence
    return record


class DelegateChainTests(unittest.TestCase):
    def test_d6_task_ids_are_sorted_matches(self):
        records = snapshot(done(tid(3)), {'task_id': tid(2)},
                           {'task_id': tid(6), 'completion': None}, done(tid(1)),
                           done(tid(4), executor='executor-other'),
                           {'task_id': tid(5), 'completion': {}})
        self.assertEqual(lineage_counts.d6_task_ids(records, EXECUTOR), [tid(1), tid(3)])
        self.assertEqual(lineage_counts.d6_task_ids(records, 'executor-none'), [])

    def test_d6_task_ids_in_doubt_is_none(self):
        for completion in ('done', [], 7):
            records = snapshot(done(tid(1)), {'task_id': tid(2), 'completion': completion})
            with self.subTest(completion=completion):
                self.assertIsNone(lineage_counts.d6_task_ids(records, EXECUTOR))
        records = snapshot(done(tid(1)))
        records[tid(2)] = 'not a record'
        self.assertIsNone(lineage_counts.d6_task_ids(records, EXECUTOR))
        self.assertIsNone(lineage_counts.d6_task_ids([done(tid(1))], EXECUTOR))
        for executor in (None, ''):
            with self.subTest(executor=executor):
                self.assertIsNone(lineage_counts.d6_task_ids(snapshot(done(tid(1))), executor))

    def test_two_seeds_in_one_chain_count_it_once(self):
        records = snapshot(*linked(3))
        result = lineage_counts.delegate_chain(records, [tid(3), tid(1)])
        self.assertEqual(result, {'members': records, 'keys': [tid(1)],
                                  'linked': [tid(1), tid(2), tid(3)]})

    def test_two_chains_give_both_keys_sorted(self):
        records = snapshot(*linked(2, start=5), {'task_id': tid(2)}, {'task_id': tid(8)})
        result = lineage_counts.delegate_chain(records, [tid(6), tid(2)])
        self.assertEqual(sorted(result['members']), [tid(2), tid(5), tid(6)])
        self.assertEqual(result['members'][tid(6)], records[tid(6)])
        self.assertEqual(result['keys'], [tid(2), tid(5)])
        self.assertEqual(result['linked'], [tid(5), tid(6)])

    def test_a_shared_task_is_none(self):
        records = snapshot(*linked(3), {'task_id': tid(7)})
        views = {tid(3): ([tid(3), tid(2)], tid(2)), tid(7): ([tid(7), tid(2)], tid(7))}
        with patch.object(lineage_counts, 'chain_in',
                          side_effect=lambda _records, seed: views[seed]):
            self.assertIsNone(lineage_counts.delegate_chain(records, [tid(3), tid(7)]))
        # One key with two different member sets is in doubt too.
        views[tid(7)] = ([tid(7), tid(3)], tid(2))
        with patch.object(lineage_counts, 'chain_in',
                          side_effect=lambda _records, seed: views[seed]):
            self.assertIsNone(lineage_counts.delegate_chain(records, [tid(3), tid(7)]))

    def test_the_member_bound(self):
        self.assertEqual(lineage_counts.MAX_DELEGATE_MEMBERS, 2 * MAX_CHAIN)
        full = snapshot(*linked(MAX_CHAIN, start=0x100), *linked(MAX_CHAIN, start=0x200))
        result = lineage_counts.delegate_chain(full, [tid(0x100), tid(0x200)])
        self.assertEqual(len(result['members']), 2 * MAX_CHAIN)
        over = snapshot(*linked(11, start=0x100), *linked(11, start=0x200),
                        *linked(11, start=0x300))
        self.assertIsNone(lineage_counts.delegate_chain(
            over, [tid(0x100), tid(0x200), tid(0x300)]))
        with patch.object(lineage_counts, 'MAX_DELEGATE_MEMBERS', 2):
            self.assertIsNone(lineage_counts.delegate_chain(snapshot(*linked(3)), [tid(1)]))

    def test_seeds_in_doubt_are_none(self):
        records = snapshot(*forked(), {'task_id': tid(9)})
        for seeds in ([], [tid(9), tid(3)], [tid(9), tid(0x55)], (tid(9),), None):
            with self.subTest(seeds=seeds):
                self.assertIsNone(lineage_counts.delegate_chain(records, seeds))


def result_record(task_id, *, top=None, nested=None, source=SOURCE):
    evidence = {'source_sha': source, 'result_head': top}
    if nested is not None:
        evidence['result'] = {'result_head': nested}
    return {'task_id': task_id, 'completion': {'executor_task_id': EXECUTOR,
                                               'evidence': evidence}}


class ContinuationLinkTests(unittest.TestCase):
    def reason(self, records, members, base):
        with self.assertRaises(LLMToolError) as caught:
            lineage_counts.check_continuation_link(records, members, base)
        self.assertEqual(str(caught.exception), 'CONTINUATION_LINK_REQUIRED')
        return caught.exception.continuation_link_reason

    def test_recorded_result_heads(self):
        heads = lineage_counts.recorded_result_heads
        self.assertEqual(heads(result_record(tid(1), top=BASE, nested=OTHER)), {BASE, OTHER})
        self.assertEqual(heads(result_record(tid(1), top=BASE, nested=BASE)), {BASE})
        self.assertEqual(heads(result_record(tid(1), top=SOURCE, nested=BASE)), {BASE})
        self.assertEqual(heads(result_record(tid(1), top='not-a-head')), set())
        self.assertEqual(heads({'task_id': tid(1)}), set())
        self.assertEqual(heads({'task_id': tid(1), 'completion': {}}), set())
        for record in ({'completion': 'done'}, {'completion': {'evidence': []}},
                       {'completion': {'evidence': {'result': 'x'}}},
                       result_record(tid(1), top=7), result_record(tid(1), nested=['x']),
                       'not a record'):
            with self.subTest(record=record):
                self.assertIsNone(heads(record))

    def test_an_outside_result_head_refuses(self):
        for outside in (result_record(tid(2), top=BASE), result_record(tid(2), nested=BASE)):
            records = snapshot(result_record(tid(1), top=OTHER), outside)
            with self.subTest(outside=outside):
                self.assertEqual(self.reason(records, {tid(1)}, BASE),
                                 'RESULT_HEAD_OUTSIDE_CHAIN')

    def test_the_same_head_inside_the_members_passes(self):
        records = snapshot(result_record(tid(1), top=OTHER), result_record(tid(2), top=BASE))
        for members in ({tid(1), tid(2)}, [tid(2), tid(1)], {tid(1): {}, tid(2): {}}):
            with self.subTest(members=members):
                self.assertIsNone(lineage_counts.check_continuation_link(
                    records, members, BASE))

    def test_a_head_equal_to_its_own_source_is_ignored(self):
        records = snapshot(result_record(tid(2), top=BASE, nested=BASE, source=BASE))
        self.assertIsNone(lineage_counts.check_continuation_link(records, {tid(1)}, BASE))

    def test_a_malformed_base_or_record_refuses_in_doubt(self):
        records = snapshot(result_record(tid(1), top=OTHER))
        for base in ('A' * 40, BASE[:-1], 'g' * 40, None, 7):
            with self.subTest(base=base):
                self.assertEqual(self.reason(records, {tid(1)}, base), 'SCAN_IN_DOUBT')
        self.assertEqual(self.reason(list(records.values()), {tid(1)}, BASE),
                         'SCAN_IN_DOUBT')
        for members in (None, tid(1), [[tid(1)]]):
            with self.subTest(members=members):
                self.assertEqual(self.reason(records, members, BASE), 'SCAN_IN_DOUBT')
        for bad in ({'task_id': tid(2), 'completion': 'done'},
                    result_record(tid(2), top=7), 'not a record'):
            records = snapshot(result_record(tid(1), top=OTHER))
            records[tid(2)] = bad
            with self.subTest(bad=bad):
                self.assertEqual(self.reason(records, {tid(1)}, BASE), 'SCAN_IN_DOUBT')
                # A malformed member record is not scanned.
                self.assertIsNone(lineage_counts.check_continuation_link(
                    records, {tid(1), tid(2)}, BASE))

    def test_no_results_passes(self):
        records = snapshot({'task_id': tid(1)}, {'task_id': tid(2), 'completion': None},
                           done(tid(3)))
        self.assertIsNone(lineage_counts.check_continuation_link(records, {tid(1)}, BASE))
        self.assertIsNone(lineage_counts.check_continuation_link({}, set(), BASE))


def in_thread(call):
    """Run call in another thread; return ('ok', value) or ('error', exception)."""
    box = []

    def worker():
        try:
            box.append(('ok', call()))
        except Exception as error:
            box.append(('error', error))
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=10)
    if thread.is_alive():
        raise AssertionError('the call did not finish')
    return box[0]


class ChainLocksTests(unittest.TestCase):
    def recording(self, events, failing=(), failing_release=()):
        def acquire(_store, key):
            if key in failing:
                raise LLMToolError('LINEAGE_LOCK_BUSY')
            events.append(('acquire', key))

            def release():
                events.append(('release', key))
                if key in failing_release:
                    raise RuntimeError('release failed')
            return release
        return patch.object(lineage_counts, 'acquire_chain_lock', side_effect=acquire)

    def test_sorted_order_and_idempotent_release(self):
        events, store = [], MemoryStore()
        with self.recording(events):
            release = lineage_counts.acquire_chain_locks(
                store, [tid(3), tid(1), tid(2), tid(1)])
            self.assertEqual(events, [('acquire', tid(1)), ('acquire', tid(2)),
                                      ('acquire', tid(3))])
            release()
            release()
        self.assertEqual(events[3:], [('release', tid(3)), ('release', tid(2)),
                                      ('release', tid(1))])

    def test_a_partial_failure_releases_the_taken_locks(self):
        events = []
        with self.recording(events, failing={tid(3)}):
            with self.assertRaises(LLMToolError) as caught:
                lineage_counts.acquire_chain_locks(MemoryStore(), [tid(3), tid(1), tid(2)])
        self.assertEqual(str(caught.exception), 'LINEAGE_LOCK_BUSY')
        self.assertEqual(events, [('acquire', tid(1)), ('acquire', tid(2)),
                                  ('release', tid(2)), ('release', tid(1))])
        # A release that fails still releases the rest; the acquire failure wins.
        events.clear()
        with self.recording(events, failing={tid(3)}, failing_release={tid(2)}):
            with self.assertRaises(LLMToolError) as caught:
                lineage_counts.acquire_chain_locks(MemoryStore(), [tid(1), tid(2), tid(3)])
        self.assertEqual(str(caught.exception), 'LINEAGE_LOCK_BUSY')
        self.assertEqual(events[2:], [('release', tid(2)), ('release', tid(1))])

    def test_invalid_keys_raise_before_any_lock(self):
        for keys in (tid(1), None, [], [''], [None], [tid(1), 7], (tid(1),)):
            acquire = Mock()
            with self.subTest(keys=keys), \
                 patch.object(lineage_counts, 'acquire_chain_lock', acquire):
                with self.assertRaises(LLMToolError) as caught:
                    lineage_counts.acquire_chain_locks(MemoryStore(), keys)
                self.assertEqual(str(caught.exception), 'LINEAGE_LOCK_INVALID')
                acquire.assert_not_called()

    def test_locks_are_held_until_release_on_an_in_memory_store(self):
        store, keys = MemoryStore(), [tid(0xD6101), tid(0xD6102)]
        with patch.object(task_store_module, 'LINEAGE_LOCK_TIMEOUT_SECONDS', 0.2):
            release = lineage_counts.acquire_chain_locks(store, list(keys))
            kind, error = in_thread(lambda: lineage_counts.acquire_chain_lock(store, keys[1]))
            self.assertEqual((kind, str(error)), ('error', 'LINEAGE_LOCK_BUSY'))
            release()
            release()
            kind, again = in_thread(lambda: lineage_counts.acquire_chain_locks(store, keys))
            self.assertEqual(kind, 'ok')
            again()

    def test_two_threads_with_overlapping_keys_both_finish(self):
        store = MemoryStore()
        first, second, third = tid(0xD6201), tid(0xD6202), tid(0xD6203)
        key_sets = ([third, first, second], [second, first])   # opposite request orders
        barrier = threading.Barrier(2)
        results = [None, None]

        def worker(index):
            try:
                barrier.wait(timeout=5)
                for _ in range(5):
                    release = lineage_counts.acquire_chain_locks(store, list(key_sets[index]))
                    time.sleep(0.02)
                    release()
                results[index] = 'ok'
            except Exception as error:   # surfaced by the assertion below
                results[index] = error
        with patch.object(task_store_module, 'LINEAGE_LOCK_TIMEOUT_SECONDS', 5):
            threads = [threading.Thread(target=worker, args=(index,), daemon=True)
                       for index in (0, 1)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(results, ['ok', 'ok'])


if __name__ == '__main__':
    unittest.main()
