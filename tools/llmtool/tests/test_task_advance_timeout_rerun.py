import hashlib
import unittest
from unittest import mock

from llmtool import task_advance
from llmtool.task_store import _json_bytes


HEAD = 'a' * 40
EXECUTOR = 'executor-1'
BRANCH = 'task-branch'


def base_record(**changes):
    record = {
        'task_id': '1' * 32,
        'run_id': '2' * 32,
        'developer': {'state': 'DEVELOPER_RESPONDED'},
        'completion': {'executor_task_id': EXECUTOR},
        'remote_test_receipts': [],
        'remote_test_outcomes': [],
    }
    record.update(changes)
    return record


def d6():
    return {'result_head': HEAD, 'recorded_result_head': HEAD}


def receipt(record, result):
    value = {
        'task_id': record['task_id'],
        'run_id': record['run_id'],
        'executor_task': EXECUTOR,
        'source_sha': HEAD,
        'result': result,
        'modules': ['test_task_advance'],
    }
    value['receipt_digest'] = hashlib.sha256(_json_bytes(value)).hexdigest()
    return value


def with_results(record, *results):
    record['remote_test_receipts'] = [receipt(record, value) for value in results]
    return record


class FakeStore:
    def __init__(self, records):
        self._records = records
        self._index = 0

    def get(self, task_id):
        index = min(self._index, len(self._records) - 1)
        self._index += 1
        return self._records[index]


class PreReviewTimeoutRerunTest(unittest.TestCase):
    def test_one_timeout_after_pr_runs_remote_test(self):
        record = with_results(base_record(), 'TIMEOUT')
        pr = {'number': 7, 'head': {'sha': HEAD}}
        self.assertEqual(
            task_advance.decide(record, d6(), pr, {}), 'RUN_REMOTE_TEST')

    def test_two_timeouts_after_pr_need_attention(self):
        record = with_results(base_record(), 'TIMEOUT', 'TIMEOUT')
        pr = {'number': 7, 'head': {'sha': HEAD}}
        self.assertEqual(
            task_advance.decide(record, d6(), pr, {}), 'NEEDS_ATTENTION')

    def test_sha_mismatch_after_pr_needs_attention(self):
        record = with_results(base_record(), 'SHA_MISMATCH')
        pr = {'number': 7, 'head': {'sha': HEAD}}
        self.assertEqual(
            task_advance.decide(record, d6(), pr, {}), 'NEEDS_ATTENTION')

    def test_one_timeout_before_pr_publishes(self):
        record = with_results(base_record(), 'TIMEOUT')
        self.assertEqual(
            task_advance.decide(record, d6(), None, {}), 'PUBLISH')

    def test_two_timeouts_before_pr_need_attention(self):
        record = with_results(base_record(), 'TIMEOUT', 'TIMEOUT')
        self.assertEqual(
            task_advance.decide(record, d6(), None, {}), 'NEEDS_ATTENTION')


class PrePrGateTimeoutRerunTest(unittest.TestCase):
    def test_rerun_once_after_one_timeout_then_pass(self):
        first = with_results(base_record(), 'TIMEOUT')
        second = with_results(base_record(), 'PASS')
        store = FakeStore([first, second])
        with mock.patch.object(task_advance.remote_test, '_task_path_modules',
                               return_value=['test_task_advance']), \
                mock.patch.object(task_advance, '_run_remote_test',
                                  return_value={'result': 'PASS'}) as run:
            status, hold = task_advance._pre_pr_remote_gate(
                first, store, EXECUTOR, HEAD, BRANCH)
        self.assertEqual(status, 'PASS')
        self.assertIsNone(hold)
        run.assert_called_once_with(
            store, first['task_id'], EXECUTOR, HEAD, f'refs/heads/{BRANCH}', None)

    def test_rerun_once_after_one_timeout_then_holds(self):
        first = with_results(base_record(), 'TIMEOUT')
        second = with_results(base_record(), 'TIMEOUT', 'TIMEOUT')
        store = FakeStore([first, second])
        with mock.patch.object(task_advance.remote_test, '_task_path_modules',
                               return_value=['test_task_advance']), \
                mock.patch.object(task_advance, '_run_remote_test',
                                  return_value={'result': 'TIMEOUT'}) as run:
            status, hold = task_advance._pre_pr_remote_gate(
                first, store, EXECUTOR, HEAD, BRANCH)
        self.assertEqual(status, 'TIMEOUT')
        self.assertEqual(hold['step'], 'NEEDS_ATTENTION')
        self.assertIn('REMOTE_TEST_TIMEOUT', hold['codes'])
        self.assertEqual(run.call_count, 1)

    def test_two_timeouts_hold_without_rerun(self):
        record = with_results(base_record(), 'TIMEOUT', 'TIMEOUT')
        store = FakeStore([record])
        with mock.patch.object(task_advance.remote_test, '_task_path_modules',
                               return_value=['test_task_advance']), \
                mock.patch.object(task_advance, '_run_remote_test') as run:
            status, hold = task_advance._pre_pr_remote_gate(
                record, store, EXECUTOR, HEAD, BRANCH)
        self.assertEqual(status, 'TIMEOUT')
        self.assertEqual(hold['step'], 'NEEDS_ATTENTION')
        self.assertIn('REMOTE_TEST_TIMEOUT', hold['codes'])
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
