"""#1502 WP-D L1: Task continuations (continues) and chain-wide repair counts."""
import contextlib
import hashlib
import io
import json
import tempfile
import threading
import time
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from llmtool import (lineage_counts, repair_policy, review_repair, task_advance,
                     task_store as task_store_module, task_submission_cli as cli)
from llmtool.errors import LLMToolError
from llmtool.repair_policy import UNKNOWN
from llmtool.task_store import TaskStore, new_id, record_lineage

import review_trust_fixture
import worker_policy_fixture

HEAD = 'a' * 40
OLD_HEAD = 'b' * 40
OTHER_HEAD = 'c' * 40
PM = 'pm-continues-test'
AT = '2026-11-14T00:00:00Z'
MAIN = 'e' * 40
RESPONSE = json.dumps({
    'schema_version': 1, 'summary': 'Bounded continuation test.',
    'developer_assignment': {'route': 'claude', 'risk': 'LOW',
                             'goal': 'Continue the earlier Task.',
                             'acceptance': ['Repair counts carry over the chain.']}})


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def tid(number):
    return f'{number:032x}'


def claim(family, head, *, pr=17, attempt=1, comment_id=9, state='FAILED', repair=None):
    return {'executor_task': 'executor-1', 'pr': pr, 'head_sha': head,
            'version': attempt, 'attempt': attempt, 'family': family, 'state': state,
            'repair_task_id': repair or tid(900 + attempt), 'comment_id': comment_id,
            'packet_sha256': 'f' * 64, 'claimed_at': AT}


def ec2_claim(family, head, **kwargs):
    return claim(family, head, comment_id=None, **kwargs)


class DictStore:
    """Read-only Task store fake keyed by Task ID."""

    def __init__(self, *records):
        self.records = {record['task_id']: record for record in records}

    def get(self, task_id):
        if task_id not in self.records:
            raise KeyError(task_id)
        return json.loads(json.dumps(self.records[task_id]))

    def task_ids(self):
        return sorted(self.records)


def linked(count, *, claims=None):
    """count Tasks tid(1)..tid(count); each continues the one before it."""
    records = []
    for number in range(1, count + 1):
        record = {'task_id': tid(number), 'review_repair_claims': list(claims or [])}
        if number > 1:
            record['continues'] = tid(number - 1)
        records.append(record)
    return records


def forked_chain():
    """{number: record}: B=tid(2) continues A=tid(1); C=tid(3) and D=tid(4) both continue B."""
    records = {1: {'task_id': tid(1), 'review_repair_claims': []}}
    for number, link in ((2, 1), (3, 2), (4, 2)):
        records[number] = {'task_id': tid(number), 'continues': tid(link),
                           'review_repair_claims': []}
    return records


class ChainWalkTests(unittest.TestCase):
    def test_task_without_continues_is_its_own_chain(self):
        own = {'task_id': tid(1), 'review_repair_claims': [claim('pr-17', OLD_HEAD)]}
        counts = lineage_counts.lineage_counts(None, own, base_family='pr-17')
        self.assertEqual(counts, {'chain': [tid(1)], 'review_repairs': 1,
                                  'review_heads': {OLD_HEAD: 'FAIL'}, 'test_fails': 0})

    def test_chain_is_walked_newest_first_up_to_sixteen_tasks(self):
        for count in (2, 3, 16):
            with self.subTest(count=count):
                records = linked(count)
                store = DictStore(*records)
                counts = lineage_counts.lineage_counts(store, records[-1],
                                                       base_family='pr-17')
                self.assertEqual(counts['chain'],
                                 [tid(number) for number in range(count, 0, -1)])
                self.assertEqual(counts['review_repairs'], 0)

    def test_a_link_in_doubt_makes_every_count_unknown(self):
        unknown = {'chain': UNKNOWN, 'review_repairs': UNKNOWN,
                   'review_heads': UNKNOWN, 'test_fails': UNKNOWN}
        records = linked(17)
        cases = {'over sixteen': (DictStore(*records), records[-1])}
        missing = {'task_id': tid(2), 'continues': tid(1), 'review_repair_claims': []}
        cases['missing predecessor'] = (DictStore(missing), missing)
        # An uppercase link: tid(0xab) has letters, so upper() really changes it.
        upper = tid(0xAB).upper()
        for value in (None, 'x' * 32, upper, 7):
            bad = {'task_id': tid(2), 'continues': value, 'review_repair_claims': []}
            # The store holds the uppercase ID too: only the link's shape refuses it.
            cases[f'malformed link {value!r}'] = (
                DictStore(bad, {'task_id': tid(1)}, {'task_id': upper}), bad)
        first = {'task_id': tid(1), 'continues': tid(2), 'review_repair_claims': []}
        second = {'task_id': tid(2), 'continues': tid(1), 'review_repair_claims': []}
        cases['cycle'] = (DictStore(first, second), second)
        child = {'task_id': tid(2), 'continues': tid(2), 'review_repair_claims': []}
        cases['self link'] = (DictStore(child), child)
        wrong = {'task_id': tid(2), 'continues': tid(1), 'review_repair_claims': []}
        store = DictStore(wrong)
        store.records[tid(1)] = {'task_id': tid(3), 'review_repair_claims': []}
        cases['wrong predecessor id'] = (store, wrong)
        for claims in ('not a list', [None], [{'family': 7, 'head_sha': OLD_HEAD}]):
            predecessor = {'task_id': tid(1), 'review_repair_claims': claims}
            cases[f'malformed predecessor claims {claims!r}'] = (
                DictStore(predecessor, missing), missing)
        cases['no store'] = (None, missing)
        for name, (store, task) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(lineage_counts.lineage_counts(
                    store, task, base_family='pr-17'), unknown)


class ChainCountTests(unittest.TestCase):
    def test_counts_sum_over_two_and_three_tasks_any_pr(self):
        first = {'task_id': tid(1), 'review_repair_claims': [
            claim('pr-5', OLD_HEAD, pr=5),
            ec2_claim('pr-5-ec2', OLD_HEAD, pr=5, attempt=2)]}
        second = {'task_id': tid(2), 'continues': tid(1), 'review_repair_claims': [
            claim('pr-6', OTHER_HEAD, pr=6),
            ec2_claim('pr-6', OTHER_HEAD, pr=6, attempt=2)]}   # legacy EC2: both counts
        current = {'task_id': tid(3), 'continues': tid(2), 'review_repair_claims': [
            ec2_claim('pr-17-ec2', HEAD)]}
        store = DictStore(first, second, current)
        two = lineage_counts.lineage_counts(store, second, base_family='pr-6')
        self.assertEqual((two['review_repairs'], two['test_fails']), (3, 2))
        three = lineage_counts.lineage_counts(store, current, base_family='pr-17')
        self.assertEqual(three['chain'], [tid(3), tid(2), tid(1)])
        self.assertEqual(three['review_repairs'], 3)
        self.assertEqual(three['review_heads'], {OLD_HEAD: 'FAIL', OTHER_HEAD: 'FAIL'})
        self.assertEqual(three['test_fails'], 3)

    def test_own_claims_count_as_before_only_in_the_base_family(self):
        own = {'task_id': tid(2), 'review_repair_claims': [
            claim('pr-17', OLD_HEAD), claim('pr-18', OTHER_HEAD, pr=18),
            ec2_claim('pr-17', HEAD, attempt=2), ec2_claim('pr-18-ec2', HEAD, pr=18)]}
        counts = lineage_counts.lineage_counts(None, own, base_family='pr-17')
        self.assertEqual((counts['review_repairs'], counts['test_fails']), (2, 1))
        # task_advance's legacy count is unchanged for a Task alone in its chain.
        self.assertEqual(task_advance._test_fail_count(own, 17, DictStore(own)), 1)
        # Without a store the successors cannot be read: never 0 by doubt.
        self.assertEqual(task_advance._test_fail_count(own, 17), UNKNOWN)

    def test_a_continuation_counts_its_own_claims_on_every_pr(self):
        # F1: a Task with continues keeps its own claims on another PR in the counts.
        own = [claim('pr-5', OLD_HEAD, pr=5),
               ec2_claim('pr-5-ec2', HEAD, pr=5, attempt=2),
               ec2_claim('pr-6', OTHER_HEAD, pr=6, attempt=3)]   # legacy EC2: both counts
        predecessor = {'task_id': tid(1), 'review_repair_claims': []}
        current = {'task_id': tid(2), 'continues': tid(1), 'review_repair_claims': own}
        store = DictStore(predecessor, current)
        counts = lineage_counts.lineage_counts(store, current, base_family='pr-17')
        self.assertEqual((counts['review_repairs'], counts['test_fails']), (2, 2))
        self.assertEqual(counts['review_heads'], {OLD_HEAD: 'FAIL', OTHER_HEAD: 'FAIL'})
        self.assertEqual(task_advance._test_fail_count(current, 17, store), 2)
        # The same claims on a Task without continues stay family-local (unchanged).
        alone = {'task_id': tid(3), 'review_repair_claims': own}
        plain = lineage_counts.lineage_counts(None, alone, base_family='pr-17')
        self.assertEqual((plain['review_repairs'], plain['test_fails'],
                          plain['review_heads']), (0, 0, {}))
        self.assertEqual(task_advance._test_fail_count(alone, 17, DictStore(alone)), 0)

    def test_advance_test_fail_count_includes_successors(self):
        # Review F1 (v1): A <- B <- C; only C holds claims (two EC2 repairs).
        # Counting B must see C, as the locked review_repair gate does.
        first = {'task_id': tid(1), 'review_repair_claims': []}
        middle = {'task_id': tid(2), 'continues': tid(1), 'review_repair_claims': []}
        last = {'task_id': tid(3), 'continues': tid(2), 'review_repair_claims': [
            ec2_claim('pr-17-ec2', OLD_HEAD), ec2_claim('pr-17-ec2', HEAD, attempt=2)]}
        store = DictStore(first, middle, last)
        for record in (first, middle, last):
            with self.subTest(task_id=record['task_id']):
                self.assertEqual(task_advance._test_fail_count(record, 17, store), 2)
                locked = lineage_counts.lineage_counts(
                    store, record, base_family='pr-17', include_successors=True)
                self.assertEqual(locked['test_fails'], 2)
        # A Task without continues that has a successor is in a chain too.
        self.assertEqual(task_advance._test_fail_count(first, 18, store), 2)
        # Two successors of one Task are a chain in doubt: never 0.
        store.records[tid(4)] = {'task_id': tid(4), 'continues': tid(2),
                                 'review_repair_claims': []}
        self.assertEqual(task_advance._test_fail_count(middle, 17, store), UNKNOWN)

    def test_an_ancestor_fork_is_in_doubt_from_every_member(self):
        # Review F1 (v2): B continues A; C and D both continue B. Counted from
        # any of the four, the chain is in doubt: every count UNKNOWN, never a
        # known count that leaves out the other branch's spent repairs.
        unknown = {'chain': UNKNOWN, 'review_repairs': UNKNOWN,
                   'review_heads': UNKNOWN, 'test_fails': UNKNOWN}
        spent = [ec2_claim('pr-17-ec2', OLD_HEAD), ec2_claim('pr-17-ec2', HEAD, attempt=2),
                 claim('pr-17', OTHER_HEAD, attempt=3), claim('pr-17', HEAD, attempt=4)]
        for start, opposite in ((1, 3), (1, 4), (2, 3), (2, 4), (3, 4), (4, 3)):
            records = forked_chain()
            records[opposite]['review_repair_claims'] = list(spent)
            store = DictStore(*records.values())
            with self.subTest(start=start, opposite=opposite):
                record = store.get(tid(start))
                self.assertEqual(lineage_counts.lineage_counts(
                    store, record, base_family='pr-17', include_successors=True), unknown)
                self.assertEqual(task_advance._test_fail_count(record, 17, store), UNKNOWN)
        # Without D the same chain is linear: root, middle and leaf all count C's claims.
        records = forked_chain()
        del records[4]
        records[3]['review_repair_claims'] = list(spent)
        store = DictStore(*records.values())
        for start in (1, 2, 3):
            with self.subTest(linear_start=start):
                counts = lineage_counts.lineage_counts(
                    store, store.get(tid(start)), base_family='pr-17',
                    include_successors=True)
                self.assertEqual(counts['chain'], {
                    1: [tid(1), tid(2), tid(3)], 2: [tid(2), tid(1), tid(3)],
                    3: [tid(3), tid(2), tid(1)]}[start])
                self.assertEqual((counts['review_repairs'], counts['test_fails']), (2, 2))

    def test_one_head_failed_in_two_tasks_counts_once(self):
        first = {'task_id': tid(1), 'review_repair_claims': [claim('pr-5', OLD_HEAD, pr=5)]}
        second = {'task_id': tid(2), 'continues': tid(1),
                  'review_repair_claims': [claim('pr-17', OLD_HEAD)]}
        counts = lineage_counts.lineage_counts(DictStore(first, second), second,
                                               base_family='pr-17')
        self.assertEqual(counts['review_repairs'], 2)
        self.assertEqual(repair_policy.review_fail_count(counts['review_heads']), 1)


_NO_LINK = object()


class StoreCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = TaskStore(Path(temp.name).resolve() / 'tasks')

    @staticmethod
    def raw(continues=_NO_LINK, task_id=None):
        request = 'Bounded continuation store test Task.'
        task = cli._record(task_id or new_id(), new_id(), PM, 'pm-profile', request,
                           state='PM_RESPONDED', provider_type='openai-compatible',
                           model='pm-model', backend='NO_NATIVE_SESSION_ID_EXPOSED',
                           request_sha=hashlib.sha256(request.encode()).hexdigest())
        task.update(pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                                      'base_url': None}, response='PM reply.')
        if continues is not _NO_LINK:
            task['continues'] = continues
        return task

    def task(self, continues=_NO_LINK, task_id=None):
        task = self.raw(continues, task_id)
        self.store.create(task)
        return task['task_id']

    def code(self, call):
        with self.assertRaises(LLMToolError) as caught:
            call()
        return getattr(caught.exception, 'task_store_error_code', None)


class StoreShapeTests(StoreCase):
    def test_old_records_stay_valid_and_the_field_is_stored(self):
        old = self.task()
        self.assertNotIn('continues', self.store.get(old))
        new = self.task(continues=old)
        self.assertEqual(self.store.get(new)['continues'], old)

    def test_malformed_or_self_continues_is_refused_at_create(self):
        for value in ('x' * 32, 'A' * 32, tid(1)[:-1], None, 5):
            with self.subTest(value=value):
                task = self.raw(value)
                self.assertEqual(self.code(lambda: self.store.create(task)),
                                 'TASK_CONTINUES_INVALID')
        task = self.raw()
        task['continues'] = task['task_id']
        self.assertEqual(self.code(lambda: self.store.create(task)), 'TASK_CONTINUES_INVALID')
        self.assertEqual(self.store.task_ids(), [])

    def test_continues_is_immutable_after_creation(self):
        first, second = self.task(), self.task()
        linked_task = self.task(continues=first)
        for task_id, value in ((linked_task, second), (linked_task, first),
                               (second, first), (linked_task, None)):
            with self.subTest(task_id=task_id, value=value):
                record = self.store.get(task_id)
                self.assertEqual(self.code(lambda: self.store.update(task_id, continues=value)),
                                 'TASK_CONTINUES_IMMUTABLE')
                self.assertEqual(self.code(lambda: self.store.compare_and_update(
                    task_id, expected={'record_sha256': record['record_sha256']},
                    continues=value)), 'TASK_CONTINUES_IMMUTABLE')
                self.assertEqual(self.store.get(task_id), record)


class RecordPmCase(StoreCase):
    def record_pm(self, continues=None, task_id=None):
        args = Namespace(stdin_json=True, pm_session_id=None, pm_runtime=None,
                         pm_cwd=None, continues=continues)
        return cli.run_task_record_pm(
            args, store=self.store, return_record=True, task_id=task_id,
            input_stream=io.StringIO(json.dumps({
                'request': 'Continue the earlier bounded Task.', 'response': RESPONSE})))

    def refused(self, code, continues, task_id=None):
        before = self.store.task_ids()
        with self.assertRaises(LLMToolError) as caught:
            self.record_pm(continues, task_id=task_id)
        self.assertEqual(getattr(caught.exception, 'continues_code', None), code)
        self.assertEqual(str(caught.exception), code)
        self.assertEqual(self.store.task_ids(), before)


def run_concurrently(*calls):
    """Start every call at one barrier in its own thread; return (kind, value) each."""
    barrier = threading.Barrier(len(calls))
    results = [None] * len(calls)

    def worker(index, call):
        try:
            barrier.wait(timeout=5)
            results[index] = ('ok', call())
        except LLMToolError as error:
            results[index] = ('refused', error)
        except Exception as error:   # surfaced by the caller's assertions
            results[index] = ('error', error)
    threads = [threading.Thread(target=worker, args=(index, call), daemon=True)
               for index, call in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    if any(thread.is_alive() for thread in threads):
        raise AssertionError('a concurrent call did not finish')
    return results


class RecordPmContinuesTests(RecordPmCase):
    def test_record_pm_sets_the_link_and_without_it_records_as_before(self):
        plain = self.record_pm()
        self.assertNotIn('continues', plain)
        linked_task = self.record_pm(continues=plain['task_id'])
        self.assertEqual(linked_task['continues'], plain['task_id'])
        self.assertEqual(self.store.get(linked_task['task_id'])['continues'],
                         plain['task_id'])

    def test_parser_reads_continues(self):
        from llmtool import cli as main_cli
        parser = main_cli.parser_for_cli()
        task_id = tid(5)
        args = parser.parse_args(['task', 'record-pm', '--stdin-json', '--continues', task_id])
        self.assertEqual(args.continues, task_id)
        self.assertIsNone(parser.parse_args(['task', 'record-pm', '--stdin-json']).continues)

    def test_malformed_unknown_and_self_are_refused(self):
        for value in ('x' * 32, 'A' * 32, tid(1)[:-1]):
            with self.subTest(value=value):
                self.refused('CONTINUES_TASK_INVALID', value)
        self.refused('CONTINUES_TASK_UNKNOWN', new_id())
        existing = self.task()
        self.refused('CONTINUES_SELF', existing, task_id=existing)

    def test_cycle_is_refused(self):
        # A link to a Task whose own chain loops: P -> A -> B -> A.
        first, second = tid(0xA1), tid(0xB2)
        self.task(continues=second, task_id=first)
        self.task(continues=first, task_id=second)
        head = self.task(continues=first)
        self.refused('CONTINUES_CYCLE', head)

    def test_chain_over_sixteen_is_refused(self):
        previous = self.task()
        for _ in range(14):
            previous = self.task(continues=previous)
        # 15 Tasks exist; the new Task makes 16, which is allowed.
        sixteenth = self.record_pm(continues=previous)['task_id']
        self.refused('CONTINUES_CHAIN_TOO_LONG', sixteenth)

    def test_predecessor_with_a_successor_is_refused(self):
        first = self.task()
        self.record_pm(continues=first)
        self.refused('CONTINUES_PREDECESSOR_HAS_SUCCESSOR', first)

    def test_split_child_or_replan_predecessor_is_refused(self):
        root = self.task()
        for kind, replaces in (('child', ()), ('replan', (tid(77),))):
            with self.subTest(kind=kind):
                member = self.task()
                record_lineage(self.store, member, {
                    'lineage_id': root, 'kind': kind, 'parent': root,
                    'replaces': list(replaces), 'state': 'OPEN', 'reason': None,
                    'base_sha': MAIN, 'recorded_at': AT})
                self.refused('CONTINUES_LINEAGE_CONFLICT', member)
        # A ROOT may be continued.
        self.assertEqual(self.record_pm(continues=root)['continues'], root)


class ContinuesLockTests(RecordPmCase):
    """Phase 2: check_continues and the create run under one store-wide lock."""

    def slow_check(self, calls):
        real = lineage_counts.check_continues

        def check(store, task_id, predecessor):
            calls.append(task_id)
            real(store, task_id, predecessor)
            time.sleep(0.3)   # widen the gap between the check and the create
        return patch.object(lineage_counts, 'check_continues', side_effect=check)

    def successors(self, predecessor):
        return [task_id for task_id in self.store.task_ids()
                if self.store.get(task_id).get('continues') == predecessor]

    def test_two_concurrent_record_pm_give_exactly_one_successor(self):
        first = self.task()
        calls = []
        with self.slow_check(calls):
            results = run_concurrently(lambda: self.record_pm(continues=first),
                                       lambda: self.record_pm(continues=first))
        kinds = sorted(kind for kind, _value in results)
        self.assertEqual(kinds, ['ok', 'refused'], results)
        refusal = next(value for kind, value in results if kind == 'refused')
        self.assertEqual(getattr(refusal, 'continues_code', None),
                         'CONTINUES_PREDECESSOR_HAS_SUCCESSOR')
        created = next(value for kind, value in results if kind == 'ok')
        self.assertEqual(self.successors(first), [created['task_id']])
        self.assertEqual(len(calls), 2)   # both ran the check; neither skipped it

    def test_a_lock_that_cannot_be_taken_refuses_and_never_skips_the_check(self):
        first = self.task()
        calls = []
        with patch.object(task_store_module, 'LINEAGE_LOCK_TIMEOUT_SECONDS', 0.2), \
             self.slow_check(calls):
            with self.store.continues_lock():   # another holder past the bounded wait
                self.refused('CONTINUES_LOCK_UNAVAILABLE', first)
            with patch.object(TaskStore, 'continues_lock',
                              side_effect=LLMToolError('lock unavailable')):
                self.refused('CONTINUES_LOCK_UNAVAILABLE', first)
        self.assertEqual(calls, [])
        self.assertEqual(self.successors(first), [])

    def test_the_lock_is_released_on_every_path(self):
        first = self.task()
        with patch.object(task_store_module, 'LINEAGE_LOCK_TIMEOUT_SECONDS', 0.2):
            self.refused('CONTINUES_TASK_UNKNOWN', new_id())
            with patch.object(self.store, 'create', side_effect=OSError('disk')):
                with self.assertRaises(LLMToolError):
                    self.record_pm(continues=first)
            self.assertEqual(self.record_pm(continues=first)['continues'], first)
            self.refused('CONTINUES_PREDECESSOR_HAS_SUCCESSOR', first)
            with self.store.continues_lock():
                pass

    def test_a_task_without_continues_takes_no_lock(self):
        with patch.object(task_store_module, 'LINEAGE_LOCK_TIMEOUT_SECONDS', 0.2), \
             self.store.continues_lock():
            plain = self.record_pm()
        self.assertNotIn('continues', plain)


class ContractDigestTests(RecordPmCase):
    """F3: continues is part of the Task contract digest."""

    @staticmethod
    def digest(contract, run_id):
        return contract.binding(run_id).to_dict()['contract_digest']

    def test_different_predecessors_give_different_contract_digests(self):
        task_id, run_id, request = tid(0x51), tid(0x52), 'f' * 64
        first, second = tid(0x53), tid(0x54)
        digests = {self.digest(cli._contract(task_id, request, link), run_id)
                   for link in (None, first, second)}
        self.assertEqual(len(digests), 3)
        # record-pm binds the stored contract to the link it records.
        predecessor = self.task()
        recorded = self.record_pm(continues=predecessor)
        expected = cli._contract(recorded['task_id'], recorded['request_sha256'], predecessor)
        self.assertEqual(recorded['task_contract'], expected.to_dict())
        self.assertEqual(recorded['task_binding']['contract_digest'],
                         self.digest(expected, recorded['run_id']))
        self.assertNotEqual(recorded['task_binding']['contract_digest'], self.digest(
            cli._contract(recorded['task_id'], recorded['request_sha256']),
            recorded['run_id']))

    def test_no_continues_keeps_the_contract_unchanged(self):
        task_id, request = tid(0x55), 'f' * 64
        self.assertEqual(cli._contract(task_id, request).to_dict(),
                         cli._contract(task_id, request, None).to_dict())
        plain = self.record_pm()
        self.assertEqual(plain['task_contract'],
                         cli._contract(plain['task_id'], plain['request_sha256']).to_dict())


# review_repair through a continuation chain (I1-I3, I6).

TASK, RUN, PRED = 'a' * 32, 'b' * 32, 'd' * 32
EXECUTOR, PR = 'executor-d6', 42


def _comment(head=HEAD, version=1):
    body = ('<!-- activity: type=review_verdict actor=Claude target=PM status=FIX -->\n'
            f'Verdict: FAIL | Version: v{version} | HEAD: {head}\n'
            'Finding: address the reported issue\n')
    return {'id': 9, 'body': body, 'created_at': '2026-10-27T01:00:00Z',
            'updated_at': '2026-10-27T01:00:00Z'}


def _current(**changes):
    review_claim = {'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': 1,
                    'state': 'DISPATCHED', 'reviewer_task': 'reviewer-d6',
                    'native_session_id': 'review-session',
                    'account_mode': 'DEFAULT_SECOND_ACCOUNT',
                    'claimed_at': '2026-10-27T00:00:00Z', 'updated_at': '2026-10-27T00:00:00Z'}
    event_id = 'e' * 32
    record = {
        'task_id': TASK, 'run_id': RUN, 'continues': PRED,
        'developer': {'assignment_id': 'f' * 32, 'provider_profile': 'agy',
                      'model': 'gemini-test', 'owner_assignment_ref': None,
                      'selection_source': 'PM', 'read_paths': ['tools/llmtool/llmtool'],
                      'write_paths': ['tools/llmtool/llmtool/review_repair.py'],
                      'acceptance': ['Repair the reported issue.'],
                      'egress_basis': 'Task authorized source files only.',
                      'state': 'DEVELOPER_RESPONDED'},
        'completion': {'executor_task_id': EXECUTOR, 'evidence': {'common_dir': '/tmp/git'}},
        'pm_assignment': {'route': 'agy', 'risk': 'LOW', 'goal': 'Repair the issue.',
                          'acceptance': ['Repair the reported issue.']},
        'pm_provenance': {'pm_native_session_id': '12345678-1234-1234-1234-123456789abc',
                          'pm_cwd': '/repo'},
        'review_dispatch_claim': [review_claim],
        'completion_events': [{'event_id': event_id, 'source': 'REVIEWER', 'state': 'PENDING',
                               'source_sha': HEAD, 'session_id': 'review-' + event_id,
                               'received_at': '2026-10-27T01:01:00Z'}],
        'record_sha256': '0' * 64,
    }
    record.update(changes)
    return record


def _receipt(head=HEAD, task_id=TASK):
    from llmtool import task_store
    receipt = {'task_id': task_id, 'run_id': RUN, 'executor_task': EXECUTOR,
               'source_sha': head, 'result': 'FAIL', 'modules': ['test_remote'],
               'failure_excerpt': 'FAIL: test_remote\nAssertionError'}
    receipt['receipt_digest'] = hashlib.sha256(task_store._json_bytes(receipt)).hexdigest()
    return receipt


class MemoryStore(DictStore):
    def compare_and_update(self, task_id, *, expected, **changes):
        record = self.records[task_id]
        if any(record.get(key) != value for key, value in expected.items()):
            raise RuntimeError('conflict')
        record.update(json.loads(json.dumps(changes)))
        record['record_sha256'] = hashlib.sha256(json.dumps(
            {key: value for key, value in record.items() if key != 'record_sha256'},
            sort_keys=True).encode()).hexdigest()
        return record


class ReviewRepairChainTests(unittest.TestCase):
    def setUp(self):
        worker_policy_fixture.install(self)
        self.calls = []
        self.policy = repair_policy.load_policy()

    def store(self, predecessor_claims, **changes):
        records = [_current(**changes)]
        if predecessor_claims is not None:
            records.append({'task_id': PRED, 'review_repair_claims': predecessor_claims})
        return MemoryStore(*records)

    def dispatch(self, store, **kwargs):
        def adapter(**request):
            self.calls.append(request)
            return {'status': 'DISPATCHED'}

        def recorder(**request):
            return {'task_id': request['repair_task_id'],
                    'pm_assignment': {'acceptance': ['Repair the reported issue.']}}
        allowed = type('Decision', (), {'allowed': True})()
        with patch.object(review_repair.worker_dispatch, 'evaluate', return_value=allowed), \
             patch.object(review_repair.shutil, 'which', return_value='/usr/bin/agy'):
            return review_repair.repair_review(
                store=store, task_id=TASK, executor_task=EXECUTOR, pr_number=PR,
                github_get=lambda _path: [_comment()], dispatch_adapter=adapter,
                current_head=lambda _task, _executor: HEAD, repair_task_recorder=recorder,
                **kwargs)

    def refused(self, store, code, **kwargs):
        before = store.get(TASK).get('review_repair_claims')
        with self.assertRaises(LLMToolError) as caught:
            self.dispatch(store, **kwargs)
        self.assertEqual(caught.exception.review_repair_code, code)
        self.assertEqual(store.get(TASK).get('review_repair_claims'), before)
        self.assertEqual(self.calls, [])

    @staticmethod
    def rca(family=f'pr-{PR}', head=HEAD, status='RCA_CONFIRMED'):
        report = 'Root cause evidence.'
        return [{'family': family, 'status': status, 'examined_sha': head,
                 'recorded_at': '2026-10-27T03:00:00Z', 'report': report,
                 'report_sha256': hashlib.sha256(report.encode()).hexdigest()}]

    def test_first_repair_of_a_fresh_chain_is_admitted(self):
        result = self.dispatch(self.store([]))
        self.assertEqual((result['claim']['family'], result['claim']['attempt']),
                         (f'pr-{PR}', 1))
        self.assertNotIn('high_debug_report_sha256', self.calls[0]['packet'])

    def test_repair_used_by_a_predecessor_needs_debug_on_the_failed_head(self):
        used = [claim('pr-5', OLD_HEAD, pr=5)]
        self.refused(self.store(used), 'REPAIR_DEBUG_REQUIRED')
        # A Debug of another family or head does not count; RCA_CONFIRMED on HEAD does.
        self.refused(self.store(used, high_debug=self.rca(family='pr-5')),
                     'REPAIR_DEBUG_REQUIRED')
        self.refused(self.store(used, high_debug=self.rca(head=OLD_HEAD)),
                     'REPAIR_DEBUG_REQUIRED')
        store = self.store(used, high_debug=self.rca())
        result = self.dispatch(store)
        self.assertEqual(result['claim']['attempt'], 1)   # numbered within this Task
        self.assertEqual(self.calls[0]['packet']['high_debug_report_sha256'],
                         self.rca()[0]['report_sha256'])

    def test_repairs_used_up_across_the_chain_escalate(self):
        used = [claim('pr-5', OLD_HEAD, pr=5), claim('pr-6', OTHER_HEAD, pr=6, attempt=2)]
        self.refused(self.store(used, high_debug=self.rca()), 'REPAIR_SPLIT_REQUIRED')

    def test_unknown_chain_counts_as_the_limit(self):
        # The predecessor record is missing: never 0, so no repair starts.
        self.refused(self.store(None, high_debug=self.rca()), 'REPAIR_SPLIT_REQUIRED')
        self.refused(self.store(None), 'REPAIR_LIMIT_REACHED',
                     remote_test_receipt=_receipt())

    def test_same_head_replay_on_the_current_task_is_unchanged(self):
        live = claim(f'pr-{PR}', HEAD, pr=PR, state='DISPATCHED', repair=tid(321))
        live['executor_task'] = EXECUTOR
        store = self.store(None, review_repair_claims=[live])
        result = self.dispatch(store)
        self.assertTrue(result['replayed'])
        self.assertEqual(result['repair_task_id'], tid(321))
        self.assertEqual(self.calls, [])

    def test_a_continuations_own_repair_on_another_pr_counts(self):
        # F1: this Task's own review repair on PR 5 uses the chain's first repair.
        own = [claim('pr-5', OLD_HEAD, pr=5)]
        self.refused(self.store([], review_repair_claims=own), 'REPAIR_DEBUG_REQUIRED')
        own_ec2 = [ec2_claim('pr-5-ec2', OLD_HEAD, pr=5)]
        store = self.store([], review_repair_claims=own_ec2)
        result = self.dispatch(store, remote_test_receipt=_receipt())
        self.assertEqual(result['claim']['family'], f'pr-{PR}-ec2')
        store.records[TASK]['review_repair_claims'][-1]['state'] = 'FAILED'
        self.calls.clear()
        self.refused(store, 'REPAIR_LIMIT_REACHED', remote_test_receipt=_receipt())

    def test_test_failures_are_counted_across_the_chain(self):
        store = self.store([ec2_claim('pr-5-ec2', OLD_HEAD, pr=5)])
        result = self.dispatch(store, remote_test_receipt=_receipt())
        self.assertEqual(result['claim']['family'], f'pr-{PR}-ec2')
        store.records[TASK]['review_repair_claims'][-1]['state'] = 'FAILED'
        self.calls.clear()
        self.refused(store, 'REPAIR_LIMIT_REACHED', remote_test_receipt=_receipt())

    def test_gate_reads_chain_counts(self):
        packet = {'reviewer_text_excerpt_untrusted_data': _comment()['body'],
                  'failed_head': HEAD, 'pr': PR}
        task = {'task_id': TASK}
        family = f'pr-{PR}'
        self.assertIsNone(review_repair._review_gate(
            task, [], packet, family, self.policy,
            counts={'review_repairs': 0, 'review_heads': {}}))
        for counts, code in (
                ({'review_repairs': 1, 'review_heads': {OLD_HEAD: 'FAIL'}},
                 'REPAIR_DEBUG_REQUIRED'),
                ({'review_repairs': UNKNOWN, 'review_heads': UNKNOWN},
                 'REPAIR_SPLIT_REQUIRED')):
            with self.subTest(counts=counts):
                with self.assertRaises(LLMToolError) as caught:
                    review_repair._review_gate(task, [], packet, family, self.policy,
                                               counts=counts)
                self.assertEqual(caught.exception.review_repair_code, code)
        child = {'task_id': TASK, 'lineage': {'kind': 'child'}}
        with self.assertRaises(LLMToolError) as caught:
            review_repair._review_gate(child, [], packet, family, self.policy,
                                       counts={'review_repairs': UNKNOWN,
                                               'review_heads': UNKNOWN})
        self.assertEqual(caught.exception.review_repair_code, 'REPAIR_PM_DECIDE_REQUIRED')
        entry = review_repair._review_gate(
            dict(task, high_debug=self.rca()), [], packet, family, self.policy,
            counts={'review_repairs': 1, 'review_heads': {OLD_HEAD: 'FAIL'}})
        self.assertEqual(entry['status'], 'RCA_CONFIRMED')


class LockedMemoryStore(TaskStore):
    """A real TaskStore root (its chain lock files are real flocks in a temp dir)
    whose Task records are held in memory with an atomic compare-and-update."""

    def __init__(self, root, *records):
        super().__init__(root)
        self.memory = MemoryStore(*records)
        self.guard = threading.Lock()

    def get(self, task_id):
        with self.guard:
            return self.memory.get(task_id)

    def task_ids(self):
        with self.guard:
            return self.memory.task_ids()

    def compare_and_update(self, task_id, *, expected, **changes):
        with self.guard:
            return json.loads(json.dumps(self.memory.compare_and_update(
                task_id, expected=expected, **changes)))


OLDEST, MIDDLE, NEWEST = tid(0x71), tid(0x72), tid(0x73)


class ChainLockTests(unittest.TestCase):
    """Phase 2: chain counts and the claim write run under the chain lock."""

    def setUp(self):
        worker_policy_fixture.install(self)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / 'tasks'
        self.policy = repair_policy.load_policy()
        self.calls = []
        self.recorded = []

    def chain_store(self, oldest_claims):
        """OLDEST (claims only) <- MIDDLE <- NEWEST; both later Tasks can repair."""
        middle = _current(task_id=MIDDLE, continues=OLDEST, high_debug=self.rca())
        newest = _current(task_id=NEWEST, continues=MIDDLE, high_debug=self.rca())
        return LockedMemoryStore(self.root, {'task_id': OLDEST,
                                             'review_repair_claims': oldest_claims},
                                 middle, newest)

    rca = staticmethod(ReviewRepairChainTests.rca)

    def dispatch(self, store, task_id, *, test_failure, hold=0.0):
        def adapter(**request):
            self.calls.append(request)
            return {'status': 'DISPATCHED'}

        def recorder(**request):
            self.recorded.append(request['repair_task_id'])
            time.sleep(hold)   # widen the gap between the counts and the claim write
            return {'task_id': request['repair_task_id'],
                    'pm_assignment': {'acceptance': ['Repair the reported issue.']}}
        extra = {'remote_test_receipt': _receipt(task_id=task_id)} if test_failure else {}
        return review_repair.repair_review(
            store=store, task_id=task_id, executor_task=EXECUTOR, pr_number=PR,
            github_get=lambda _path: [_comment()], dispatch_adapter=adapter,
            current_head=lambda _task, _executor: HEAD, repair_task_recorder=recorder,
            **extra)

    def patched(self):
        allowed = type('Decision', (), {'allowed': True})()
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(review_repair.worker_dispatch, 'evaluate',
                                         return_value=allowed))
        stack.enter_context(patch.object(review_repair.shutil, 'which',
                                         return_value='/usr/bin/agy'))
        return stack

    @staticmethod
    def new_claims(store):
        return [item for task_id in (MIDDLE, NEWEST)
                for item in store.get(task_id).get('review_repair_claims', [])]

    def one_left(self, test_failure):
        if test_failure:
            limit = self.policy['test_fail_max']
            return [ec2_claim('pr-5-ec2', f'{n:040x}', pr=5, attempt=n)
                    for n in range(1, limit)]
        limit = self.policy['repair_max']
        return [claim('pr-5', f'{n:040x}', pr=5, attempt=n) for n in range(1, limit)]

    def refusal_codes(self, test_failure):
        return (('REPAIR_LIMIT_REACHED',) if test_failure else
                ('REPAIR_LIMIT_REACHED', 'REPAIR_SPLIT_REQUIRED'))

    def test_chain_key_is_the_oldest_task_or_the_task_itself(self):
        store = self.chain_store([])
        for task_id in (OLDEST, MIDDLE, NEWEST):
            with self.subTest(task_id=task_id):
                self.assertEqual(lineage_counts.chain_key(store, store.get(task_id)), OLDEST)
        plain = {'task_id': tid(0x99), 'review_repair_claims': []}
        self.assertEqual(lineage_counts.chain_key(store, plain), tid(0x99))
        broken = {'task_id': tid(0x98), 'continues': tid(0x97)}
        self.assertEqual(lineage_counts.chain_key(store, broken), tid(0x98))

    def test_counts_with_successors_see_claims_of_later_tasks(self):
        later = _current(task_id=NEWEST, continues=MIDDLE,
                         review_repair_claims=[ec2_claim('pr-9-ec2', OLD_HEAD, pr=9),
                                               claim('pr-9', OTHER_HEAD, pr=9)])
        store = LockedMemoryStore(self.root, {'task_id': OLDEST, 'review_repair_claims': []},
                                  _current(task_id=MIDDLE, continues=OLDEST), later)
        middle = store.get(MIDDLE)
        before = lineage_counts.lineage_counts(store, middle, base_family=f'pr-{PR}')
        self.assertEqual((before['review_repairs'], before['test_fails']), (0, 0))
        after = lineage_counts.lineage_counts(store, middle, base_family=f'pr-{PR}',
                                              include_successors=True)
        self.assertEqual(after['chain'], [MIDDLE, OLDEST, NEWEST])
        self.assertEqual((after['review_repairs'], after['test_fails']), (1, 1))
        # Two Tasks continuing one predecessor are a chain in doubt.
        store.memory.records[tid(0x74)] = {'task_id': tid(0x74), 'continues': MIDDLE}
        doubt = lineage_counts.lineage_counts(store, middle, base_family=f'pr-{PR}',
                                              include_successors=True)
        self.assertEqual(doubt['test_fails'], UNKNOWN)

    def test_two_concurrent_repairs_in_one_chain_admit_exactly_one(self):
        for test_failure in (True, False):
            with self.subTest(test_failure=test_failure):
                self.calls.clear()
                store = self.chain_store(self.one_left(test_failure))
                with self.patched():
                    results = run_concurrently(
                        lambda: self.dispatch(store, MIDDLE, test_failure=test_failure,
                                              hold=0.3),
                        lambda: self.dispatch(store, NEWEST, test_failure=test_failure,
                                              hold=0.3))
                kinds = sorted(kind for kind, _value in results)
                self.assertEqual(kinds, ['ok', 'refused'], results)
                refusal = next(value for kind, value in results if kind == 'refused')
                self.assertIn(getattr(refusal, 'review_repair_code', None),
                              self.refusal_codes(test_failure))
                self.assertEqual(len(self.new_claims(store)), 1)
                self.assertEqual(len(self.calls), 1)

    def test_a_repair_on_an_earlier_task_sees_a_later_tasks_claim(self):
        for test_failure in (True, False):
            with self.subTest(test_failure=test_failure):
                store = self.chain_store(self.one_left(test_failure))
                with self.patched():
                    self.dispatch(store, NEWEST, test_failure=test_failure)
                    with self.assertRaises(LLMToolError) as caught:
                        self.dispatch(store, MIDDLE, test_failure=test_failure)
                self.assertIn(caught.exception.review_repair_code,
                              self.refusal_codes(test_failure))
                self.assertEqual(store.get(MIDDLE).get('review_repair_claims', []), [])

    def test_an_ancestor_fork_refuses_before_any_claim_or_dispatch(self):
        # Review F1 (v2): OLDEST <- MIDDLE <- NEWEST and FORK <- MIDDLE. The other
        # branch holds every spent repair; this branch holds none. A repair from
        # NEWEST, FORK or MIDDLE refuses: the chain is in doubt (UNKNOWN).
        fork = tid(0x74)
        for test_failure in (True, False):
            if test_failure:
                spent = [ec2_claim('pr-5-ec2', f'{n:040x}', pr=5, attempt=n)
                         for n in range(1, self.policy['test_fail_max'] + 1)]
            else:
                spent = [claim('pr-5', f'{n:040x}', pr=5, attempt=n)
                         for n in range(1, self.policy['repair_max'] + 1)]
            for start, opposite in ((NEWEST, fork), (fork, NEWEST), (MIDDLE, fork)):
                with self.subTest(test_failure=test_failure, start=start):
                    self.calls.clear()
                    self.recorded.clear()
                    branches = {task_id: _current(task_id=task_id, continues=MIDDLE,
                                                  high_debug=self.rca())
                                for task_id in (NEWEST, fork)}
                    branches[opposite]['review_repair_claims'] = list(spent)
                    store = LockedMemoryStore(
                        self.root, {'task_id': OLDEST, 'review_repair_claims': []},
                        _current(task_id=MIDDLE, continues=OLDEST, high_debug=self.rca()),
                        *branches.values())
                    before = store.get(start).get('review_repair_claims', [])
                    with self.patched():
                        with self.assertRaises(LLMToolError) as caught:
                            self.dispatch(store, start, test_failure=test_failure)
                    self.assertIn(caught.exception.review_repair_code,
                                  self.refusal_codes(test_failure))
                    self.assertEqual(store.get(start).get('review_repair_claims', []), before)
                    self.assertEqual((self.recorded, self.calls), ([], []))

    def test_a_chain_lock_that_cannot_be_taken_refuses_before_any_count(self):
        store = self.chain_store([])
        self.recorded.clear()
        with patch.object(task_store_module, 'LINEAGE_LOCK_TIMEOUT_SECONDS', 0.2), \
             self.patched(), \
             patch.object(lineage_counts, 'lineage_counts',
                          side_effect=AssertionError('counted without the lock')):
            for test_failure in (True, False):
                with self.subTest(test_failure=test_failure, holder='busy'):
                    with store.chain_lock(OLDEST):
                        with self.assertRaises(LLMToolError) as caught:
                            self.dispatch(store, NEWEST, test_failure=test_failure)
                    self.assertEqual(caught.exception.review_repair_code,
                                     'REPAIR_CHAIN_LOCK_UNAVAILABLE')
                with self.subTest(test_failure=test_failure, holder='error'):
                    with patch.object(TaskStore, 'chain_lock',
                                      side_effect=LLMToolError('lock unavailable')):
                        with self.assertRaises(LLMToolError) as caught:
                            self.dispatch(store, NEWEST, test_failure=test_failure)
                    self.assertEqual(caught.exception.review_repair_code,
                                     'REPAIR_CHAIN_LOCK_UNAVAILABLE')
        self.assertEqual(self.new_claims(store), [])
        self.assertEqual((self.recorded, self.calls), ([], []))

    def test_the_chain_lock_is_released_on_every_path(self):
        store = self.chain_store(self.one_left(True))
        keys = []
        real = lineage_counts.acquire_chain_lock

        def acquire(lock_store, key):
            keys.append(key)
            return real(lock_store, key)
        with patch.object(task_store_module, 'LINEAGE_LOCK_TIMEOUT_SECONDS', 0.2), \
             self.patched(), \
             patch.object(lineage_counts, 'acquire_chain_lock', side_effect=acquire):
            self.dispatch(store, NEWEST, test_failure=True)            # admitted
            with self.assertRaises(LLMToolError):                     # refused under lock
                self.dispatch(store, MIDDLE, test_failure=True)

            def failing_recorder(**_request):
                raise RuntimeError('record failed')
            with self.assertRaises(LLMToolError) as caught:           # recorder failure
                review_repair.repair_review(
                    store=store, task_id=MIDDLE, executor_task=EXECUTOR, pr_number=PR,
                    github_get=lambda _path: [_comment()], dispatch_adapter=lambda **_r: {},
                    current_head=lambda _task, _executor: HEAD,
                    repair_task_recorder=failing_recorder)
            # The predecessors hold only test repairs, so the review gate admits.
            self.assertEqual(caught.exception.review_repair_code,
                             'REPAIR_TASK_RECORD_UNAVAILABLE')
            with store.chain_lock(OLDEST):
                pass
        self.assertEqual(keys, [OLDEST, OLDEST, OLDEST])

    def test_a_task_without_continues_locks_its_own_id(self):
        plain = _current()
        del plain['continues']
        store = LockedMemoryStore(self.root, plain)
        keys = []
        real = lineage_counts.acquire_chain_lock

        def acquire(lock_store, key):
            keys.append(key)
            return real(lock_store, key)
        with self.patched(), \
             patch.object(lineage_counts, 'acquire_chain_lock', side_effect=acquire):
            result = self.dispatch(store, TASK, test_failure=False)
        self.assertEqual(keys, [TASK])
        self.assertEqual((result['claim']['family'], result['claim']['attempt']),
                         (f'pr-{PR}', 1))


# task advance / drive through a continuation chain (I2, I4).

ADV_TASK, ADV_PRED = '1' * 32, '9' * 32
PR_SNAPSHOT = {'number': 17, 'head': {'sha': HEAD}, 'merged': False}


def _advance_record(**changes):
    record = {
        'task_id': ADV_TASK, 'run_id': '2' * 32,
        'developer': {'state': 'DEVELOPER_RESPONDED'},
        'completion': {'executor_task_id': 'executor-1', 'state': 'WAITING_REVIEW',
                       'evidence': {'result': {'result_head': HEAD}}},
        'review_dispatch_claim': [], 'review_repair_claims': [], 'completion_events': [],
        'remote_test_outcomes': [{'result': 'ADMISSION_DENIED',
                                  'code': 'MODULE_NOT_ADMITTED', 'executor_task': 'executor-1',
                                  'sha': HEAD, 'modules': []}],
    }
    record.update(changes)
    return record


def _d6():
    return {'root': '/repo/task', 'task_id': 'executor-1', 'result_head': HEAD,
            'recorded_result_head': HEAD, 'registered_result_head': HEAD, 'dirty': False}


def _linux_failure_get(calls):
    run = {'id': 1, 'head_sha': HEAD, 'name': 'check (Linux 3.14)', 'status': 'completed',
           'conclusion': 'failure'}

    def github_get(path):
        calls.append(path)
        if path.startswith('commits/'):
            return {'total_count': 1, 'check_runs': [run]}
        if path.startswith('check-runs/'):
            return dict(run, output={'title': 'Tests failed', 'summary': 'FAIL: test_example'})
        if path.endswith('/files?per_page=100'):
            return [{'filename': 'src/app.py'}]
        return PR_SNAPSHOT
    return github_get


class AdvanceChainTests(unittest.TestCase):
    def store(self, own_claims=(), predecessor_claims=None, continues=True, **changes):
        record = _advance_record(review_repair_claims=list(own_claims), **changes)
        records = [record]
        if continues:
            record['continues'] = ADV_PRED
            if predecessor_claims is not None:
                records.append({'task_id': ADV_PRED,
                                'review_repair_claims': list(predecessor_claims)})
        return DictStore(*records)

    def test_fail_count_reaches_stopped_across_a_continuation(self):
        own = [ec2_claim('pr-17-ec2', OLD_HEAD)]
        cases = (('predecessor used one', [ec2_claim('pr-9-ec2', OTHER_HEAD, pr=9)], True,
                  ('STOPPED', ['TEST_FAIL_LIMIT_REACHED'])),
                 ('broken link', None, True, ('STOPPED', ['TEST_FAIL_LIMIT_REACHED'])),
                 ('fresh predecessor', [], True, ('CI_FAILED', [])),
                 ('legacy task', None, False, ('CI_FAILED', [])))
        for name, predecessor, continues, expected in cases:
            with self.subTest(case=name):
                store = self.store(own, predecessor, continues=continues)
                record = store.get(ADV_TASK)
                calls, repaired = [], []
                with patch.object(task_advance, '_d6_snapshot', return_value=_d6()), \
                     patch.object(task_advance, '_review_outcomes', return_value=[]):
                    result = task_advance.advance(
                        task_id=ADV_TASK, store=store, pr_number=17,
                        github_get=_linux_failure_get(calls),
                        dispatch_review_call=lambda **_kw: self.fail('no review'),
                        repair_review_call=lambda **kw: repaired.append(kw) or {'r': 1})
                self.assertEqual((result['step'], result['codes']), expected)
                self.assertEqual(len(repaired), 0 if expected[0] == 'STOPPED' else 1)
                count = task_advance._test_fail_count(record, 17, store)
                self.assertEqual(count, {'predecessor used one': 2, 'broken link': UNKNOWN,
                                         'fresh predecessor': 1, 'legacy task': 1}[name])

    def test_an_ancestor_fork_stops_the_test_repair_from_every_member(self):
        # Review F1 (v2): B continues A; C and D both continue B. The other
        # branch holds the spent EC2 repairs; from any member advance stops.
        spent = [ec2_claim('pr-17-ec2', OLD_HEAD), ec2_claim('pr-17-ec2', OTHER_HEAD,
                                                             attempt=2)]
        for start, opposite in ((1, 4), (2, 3), (3, 4), (4, 3)):
            with self.subTest(start=start, opposite=opposite):
                records = forked_chain()
                record = _advance_record(task_id=tid(start))
                if 'continues' in records[start]:
                    record['continues'] = records[start]['continues']
                records[start] = record
                records[opposite]['review_repair_claims'] = list(spent)
                store = DictStore(*records.values())
                calls, repaired = [], []
                with patch.object(task_advance, '_d6_snapshot', return_value=_d6()), \
                     patch.object(task_advance, '_review_outcomes', return_value=[]):
                    result = task_advance.advance(
                        task_id=tid(start), store=store, pr_number=17,
                        github_get=_linux_failure_get(calls),
                        dispatch_review_call=lambda **_kw: self.fail('no review'),
                        repair_review_call=lambda **kw: repaired.append(kw) or {'r': 1})
                self.assertEqual((result['step'], result['codes']),
                                 ('STOPPED', ['TEST_FAIL_LIMIT_REACHED']))
                self.assertEqual(repaired, [])
                self.assertEqual(task_advance._test_fail_count(
                    store.get(tid(start)), 17, store), UNKNOWN)

    def review_fail(self, store):
        repaired = []
        review_claim = [{'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}]
        store.records[ADV_TASK]['review_dispatch_claim'] = review_claim

        def advance(**_kwargs):
            with patch.object(task_advance, '_d6_snapshot', return_value=_d6()), \
                 patch.object(task_advance, '_pr_snapshot', return_value=PR_SNAPSHOT), \
                 patch.object(task_advance, '_review_outcomes', return_value=[{
                     'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                     'outcome': 'REVIEW_FAIL'}]):
                return task_advance.advance(
                    task_id=ADV_TASK, store=store, pr_number=17, cwd='/repo/task',
                    github_get=lambda _path: [],
                    repair_review_call=lambda **kw: repaired.append(kw) or {'r': 1})
        return advance, repaired

    def test_review_repair_is_left_to_review_repair_as_on_main(self):
        # Child 1 adds no advance-side review gate: whatever the chain's review
        # count, advance hands the review FAIL to review_repair (which counts the
        # chain under the chain lock) and passes no replay flag.
        cases = (('fresh chain', self.store((), [])),
                 ('legacy task, none used', self.store((), continues=False)),
                 ('predecessor used one', self.store((), [claim('pr-9', OLD_HEAD, pr=9)])),
                 ('legacy task used one', self.store([claim('pr-17', OLD_HEAD)],
                                                     continues=False)),
                 ('own claim on another PR', self.store([claim('pr-9', OLD_HEAD, pr=9)], [])),
                 ('broken link', self.store((), None)))
        for name, store in cases:
            with self.subTest(case=name):
                before = store.get(ADV_TASK)['review_repair_claims']
                advance, repaired = self.review_fail(store)
                result = advance()
                self.assertEqual((result['step'], result['codes']), ('REPAIR_REVIEW', []))
                self.assertEqual(len(repaired), 1)
                self.assertNotIn('replay_only', repaired[0])
                self.assertIsNone(repaired[0]['retry_claim_id'])
                self.assertEqual(store.get(ADV_TASK)['review_repair_claims'], before)
        self.assertFalse(hasattr(task_advance, '_review_repairs_used'))


if __name__ == '__main__':
    unittest.main()
