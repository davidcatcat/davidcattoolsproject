"""#2602 item (p): a stale-base EC2 FAIL updates the branch instead of repairing."""

import hashlib
import unittest
from unittest.mock import patch

from llmtool import task_advance
from llmtool.task_store import _json_bytes


HEAD = 'a' * 40
EXECUTOR = 'executor-1'
PR = {'number': 17, 'head': {'sha': HEAD}, 'state': 'open', 'merged': False}
CODE = task_advance.REMOTE_TEST_MODULES_ABSENT_AT_HEAD


def base_record(**changes):
    record = {
        'task_id': '1' * 32,
        'run_id': '2' * 32,
        'completion': {'executor_task_id': EXECUTOR},
        'developer': {'state': 'DEVELOPER_RESPONDED'},
        'review_dispatch_claim': [],
        'review_repair_claims': [],
        'completion_events': [],
        'remote_test_outcomes': [],
        'remote_test_receipts': [],
    }
    record.update(changes)
    return record


def d6():
    return {'root': '/repo/task', 'task_id': EXECUTOR, 'result_head': HEAD,
            'recorded_result_head': HEAD, 'dirty': False}


def receipt(**changes):
    value = {
        'task_id': '1' * 32,
        'run_id': '2' * 32,
        'executor_task': EXECUTOR,
        'source_sha': HEAD,
        'result': 'FAIL',
        'modules': ['test_absent'],
        'ran_total': 0,
        'failures': ['NO_FINAL'],
        'elapsed_s': 1.0,
    }
    value.update(changes)
    value['receipt_digest'] = hashlib.sha256(_json_bytes(value)).hexdigest()
    return value


def tree_get(paths, *, truncated=False, error=None, calls=None):
    def get(path):
        if calls is not None:
            calls.append(path)
        if not path.startswith('git/trees/'):
            return None
        if error is not None:
            raise error
        return {'sha': 'f' * 40, 'truncated': truncated,
                'tree': [{'path': path, 'type': 'blob'} for path in paths]}
    return get


class Store:
    def __init__(self, record):
        self.record = record
        self.writes = 0

    def get(self, _task_id):
        return self.record

    def task_ids(self):
        return [self.record['task_id']]

    def compare_and_update(self, *_args, **_kwargs):
        self.writes += 1

    def record_published_pr(self, *_args, **_kwargs):
        self.writes += 1


class StaleBase2602Tests(unittest.TestCase):
    def advance(self, record, github_get, *, repair=None):
        store = Store(record)
        repair_calls = []
        if repair is None:
            def repair(**_kwargs):
                repair_calls.append(_kwargs)
                return {'state': 'DISPATCHED'}
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
                patch.object(task_advance, '_pr_snapshot', return_value=PR), \
                patch.object(task_advance, '_review_outcomes', return_value=[]), \
                patch.object(task_advance, '_test_family_used_up', return_value=False):
            result = task_advance.advance(
                task_id=record['task_id'], store=store, pr_number=17, cwd='/repo/task',
                github_get=github_get, repair_review_call=repair)
        return result, store, repair_calls

    def test_absent_modules_end_the_step_without_a_repair_or_a_count(self):
        record = base_record(remote_test_receipts=[
            receipt(modules=['test_gw_agent_historic_failure_hold'],
                    failures=['NO_FINAL'])])
        calls = []

        def no_repair(**_kwargs):
            self.fail('a stale base must not dispatch a repair')

        result, store, _ = self.advance(
            record, tree_get(['tools/llmtool/tests/test_present.py'], calls=calls),
            repair=no_repair)

        self.assertEqual(result['step'], CODE)
        self.assertEqual(result['next'], CODE)
        self.assertEqual(result['codes'], [CODE])
        self.assertEqual(result['result']['absent_modules'],
                         ['test_gw_agent_historic_failure_hold'])
        self.assertEqual(result['result']['absent_count'], 1)
        self.assertEqual(result['result']['head'], HEAD)
        self.assertIn('test_gw_agent_historic_failure_hold',
                      result['result']['message'])
        self.assertIn('update the PR branch from main, then rerun EC2',
                      result['result']['message'])
        self.assertEqual(
            calls, [f'git/trees/{HEAD}?recursive=1'])
        self.assertEqual(store.writes, 0)
        self.assertEqual(record['review_repair_claims'], [])

    def test_not_executed_receipt_also_ends_as_stale_base(self):
        record = base_record(remote_test_receipts=[
            receipt(modules=['test_absent'], failures=[])])

        def no_repair(**_kwargs):
            self.fail('a stale base must not dispatch a repair')

        result, store, _ = self.advance(
            record, tree_get([]), repair=no_repair)

        self.assertEqual(result['step'], CODE)
        self.assertEqual(result['codes'], [CODE])
        self.assertEqual(result['result']['absent_count'], 1)
        self.assertEqual(store.writes, 0)

    def test_mixed_absent_and_real_failure_keeps_the_repair_routing(self):
        record = base_record(remote_test_receipts=[
            receipt(modules=['test_absent', 'test_present'],
                    failures=['test_present'])])
        calls = []

        result, _store, repairs = self.advance(
            record, tree_get(['tools/llmtool/tests/test_present.py'], calls=calls))

        self.assertEqual(result['step'], 'REPAIR_REVIEW')
        self.assertNotIn(CODE, result['codes'])
        self.assertEqual(len(repairs), 1)
        # Presence is read once; the PR read is the existing docs-only check.
        self.assertEqual(calls, [f'git/trees/{HEAD}?recursive=1', 'pulls/17'])

    def test_every_requested_module_present_keeps_the_repair_routing(self):
        record = base_record(remote_test_receipts=[
            receipt(modules=['test_present'], failures=['NO_FINAL'])])

        result, _store, repairs = self.advance(
            record, tree_get(['tools/llmtool/tests/test_present.py']))

        self.assertEqual(result['step'], 'REPAIR_REVIEW')
        self.assertNotIn(CODE, result['codes'])
        self.assertEqual(len(repairs), 1)

    def test_a_run_that_executed_tests_keeps_the_repair_routing(self):
        record = base_record(remote_test_receipts=[
            receipt(modules=['test_absent'], ran_total=1, failures=['NO_FINAL'])])
        calls = []

        result, _store, repairs = self.advance(
            record, tree_get([], calls=calls))

        self.assertEqual(result['step'], 'REPAIR_REVIEW')
        self.assertNotIn(CODE, result['codes'])
        self.assertEqual(len(repairs), 1)
        # A positive ran_total proves a test ran, so module presence is not read;
        # only the existing docs-only PR read happens.
        self.assertEqual(calls, ['pulls/17'])

    def test_unreadable_or_incomplete_presence_keeps_the_repair_routing(self):
        cases = (
            ('tree read failed', tree_get([], error=RuntimeError('offline'))),
            ('tree truncated', tree_get([], truncated=True)),
            ('malformed tree', lambda _path: {'sha': 'f' * 40, 'tree': []}),
            ('malformed entry', lambda _path: {'sha': 'f' * 40, 'truncated': False,
                                               'tree': [{'path': 7, 'type': 'blob'}]}),
        )
        for name, github_get in cases:
            with self.subTest(name=name):
                record = base_record(remote_test_receipts=[
                    receipt(modules=['test_absent'], failures=['NO_FINAL'])])
                result, _store, repairs = self.advance(record, github_get)
                self.assertEqual(result['step'], 'REPAIR_REVIEW')
                self.assertNotIn(CODE, result['codes'])
                self.assertEqual(len(repairs), 1)

    def test_absent_module_list_is_bounded_in_the_message_and_result(self):
        modules = [f'test_absent_{index:02d}' for index in range(12)]
        record = base_record(remote_test_receipts=[
            receipt(modules=modules, failures=['NO_FINAL'])])

        def no_repair(**_kwargs):
            self.fail('a stale base must not dispatch a repair')

        result, _store, _ = self.advance(record, tree_get([]), repair=no_repair)

        self.assertEqual(result['step'], CODE)
        self.assertEqual(result['result']['absent_count'], 12)
        self.assertEqual(len(result['result']['absent_modules']), 10)
        self.assertIn('(+2 more)', result['result']['message'])
        self.assertNotIn('test_absent_11', result['result']['absent_modules'])


if __name__ == '__main__':
    unittest.main()
