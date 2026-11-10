import hashlib
import unittest

from llmtool import task_advance
from llmtool.task_store import _json_bytes


HEAD = 'a' * 40
OTHER_HEAD = 'b' * 40
EXECUTOR = 'executor-1'


def base_record(**changes):
    record = {
        'task_id': '1' * 32,
        'run_id': '2' * 32,
        'completion': {'executor_task_id': EXECUTOR},
        'remote_test_receipts': [],
        'remote_test_outcomes': [],
    }
    record.update(changes)
    return record


def d6(head=HEAD):
    return {'result_head': head}


def receipt(record, **changes):
    value = {
        'task_id': record['task_id'],
        'run_id': record['run_id'],
        'executor_task': EXECUTOR,
        'source_sha': HEAD,
        'result': 'TIMEOUT',
        'modules': ['test_task_advance'],
    }
    value.update(changes)
    value['receipt_digest'] = hashlib.sha256(_json_bytes(value)).hexdigest()
    return value


def outcome(**changes):
    value = {'executor_task': EXECUTOR, 'sha': HEAD, 'result': 'TIMEOUT'}
    value.update(changes)
    return value


class RemoteTestTimeoutCountTest(unittest.TestCase):
    def test_no_results_counts_zero(self):
        self.assertEqual(
            task_advance._remote_test_timeout_count(base_record(), d6()), 0)

    def test_one_timeout_receipt_counts_one(self):
        record = base_record()
        record['remote_test_receipts'] = [receipt(record)]
        self.assertEqual(
            task_advance._remote_test_timeout_count(record, d6()), 1)

    def test_receipt_and_outcome_count_two(self):
        record = base_record()
        record['remote_test_receipts'] = [receipt(record)]
        record['remote_test_outcomes'] = [outcome()]
        self.assertEqual(
            task_advance._remote_test_timeout_count(record, d6()), 2)

    def test_wrong_digest_counts_zero(self):
        record = base_record()
        item = receipt(record)
        item['receipt_digest'] = '0' * 64
        record['remote_test_receipts'] = [item]
        self.assertEqual(
            task_advance._remote_test_timeout_count(record, d6()), 0)

    def test_other_head_counts_zero(self):
        record = base_record()
        record['remote_test_receipts'] = [receipt(record, source_sha=OTHER_HEAD)]
        record['remote_test_outcomes'] = [outcome(sha=OTHER_HEAD)]
        self.assertEqual(
            task_advance._remote_test_timeout_count(record, d6()), 0)

    def test_other_executor_counts_zero(self):
        record = base_record()
        record['remote_test_receipts'] = [
            receipt(record, executor_task='executor-2')]
        record['remote_test_outcomes'] = [outcome(executor_task='executor-2')]
        self.assertEqual(
            task_advance._remote_test_timeout_count(record, d6()), 0)

    def test_pass_and_fail_receipts_count_zero(self):
        record = base_record()
        record['remote_test_receipts'] = [
            receipt(record, result='PASS'), receipt(record, result='FAIL')]
        self.assertEqual(
            task_advance._remote_test_timeout_count(record, d6()), 0)

    def test_non_list_containers_count_zero(self):
        record = base_record(remote_test_receipts={'a': 1},
                             remote_test_outcomes='not-a-list')
        self.assertEqual(
            task_advance._remote_test_timeout_count(record, d6()), 0)

    def test_malformed_items_count_zero(self):
        record = base_record(remote_test_receipts=['bad', None],
                             remote_test_outcomes=['bad', None])
        self.assertEqual(
            task_advance._remote_test_timeout_count(record, d6()), 0)

    def test_missing_head_counts_zero(self):
        record = base_record()
        record['remote_test_receipts'] = [receipt(record)]
        record['remote_test_outcomes'] = [outcome()]
        self.assertEqual(
            task_advance._remote_test_timeout_count(record, {}), 0)

    def test_missing_executor_counts_zero(self):
        record = base_record(completion={})
        record['remote_test_receipts'] = [receipt(record)]
        record['remote_test_outcomes'] = [outcome()]
        self.assertEqual(
            task_advance._remote_test_timeout_count(record, d6()), 0)


if __name__ == '__main__':
    unittest.main()
