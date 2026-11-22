"""#2602 item k: repeated EC2 timeouts and unreachable runners wait for the Owner."""

import hashlib
import unittest

from llmtool import task_advance
from llmtool.task_store import _json_bytes


HEAD = 'a' * 40
OTHER_HEAD = 'b' * 40
EXECUTOR = 'executor-1'
OTHER_EXECUTOR = 'executor-2'
PR = {'number': 17, 'head': {'sha': HEAD}, 'merged': False, 'state': 'open'}


def base_record(**changes):
    record = {
        'task_id': '1' * 32,
        'run_id': '2' * 32,
        'completion': {'executor_task_id': EXECUTOR},
        'remote_test_receipts': [],
        'remote_test_outcomes': [],
        'developer': {
            'state': 'DEVELOPER_RESPONDED',
            'write_paths': ['tools/llmtool/tests/test_task_advance.py'],
        },
    }
    record.update(changes)
    return record


def d6(head=HEAD):
    return {'result_head': head, 'recorded_result_head': head, 'root': '/repo/task',
            'dirty': False}


def receipt(record, **changes):
    value = {
        'task_id': record['task_id'],
        'run_id': record['run_id'],
        'executor_task': EXECUTOR,
        'source_sha': HEAD,
        'result': 'FAIL',
        'modules': ['test_task_advance'],
        'ran_total': 1,
        'elapsed_s': 1.0,
    }
    value.update(changes)
    value['receipt_digest'] = hashlib.sha256(_json_bytes(value)).hexdigest()
    return value


def outcome(**changes):
    value = {'executor_task': EXECUTOR, 'sha': HEAD, 'result': 'TIMEOUT'}
    value.update(changes)
    return value


def unreachable(code='SSH_UNAVAILABLE', **changes):
    return outcome(result='UNAVAILABLE', code=code, **changes)


class Ec2Waiting2602Tests(unittest.TestCase):
    def test_second_timeout_waits_for_owner(self):
        record = base_record()
        record['remote_test_outcomes'] = [outcome(), outcome()]
        self.assertEqual(
            task_advance._remote_test_status(record, d6()), ('TIMEOUT', None, None))
        self.assertEqual(
            task_advance._pre_review_action(record, d6(), PR, {}, HEAD),
            'WAITING_OWNER')
        self.assertEqual(task_advance.decide(record, d6(), PR, {}), 'WAITING_OWNER')

    def test_second_timeout_records_owner_notice(self):
        codes, result = task_advance._remote_test_waiting_result(
            '1' * 32, 17, d6(), 'TIMEOUT')
        self.assertEqual(codes, ['REMOTE_TEST_TIMEOUT', 'OWNER_NOTICE'])
        self.assertEqual(result['owner_notice'], {
            'task_id': '1' * 32, 'pr': 17, 'head': HEAD,
            'reason': 'REMOTE_TEST_TIMEOUT'})

    def test_first_timeout_still_reruns(self):
        record = base_record()
        record['remote_test_outcomes'] = [outcome()]
        self.assertEqual(
            task_advance._pre_review_action(record, d6(), PR, {}, HEAD),
            'RUN_REMOTE_TEST')
        self.assertEqual(task_advance.decide(record, d6(), PR, {}), 'RUN_REMOTE_TEST')

    def test_first_unreachable_runner_reruns_once(self):
        for code in ('SSH_UNAVAILABLE', 'SSH_FAILED'):
            with self.subTest(code=code):
                record = base_record()
                record['remote_test_outcomes'] = [unreachable(code)]
                self.assertEqual(
                    task_advance._remote_test_status(record, d6()),
                    ('UNREACHABLE', None, None))
                self.assertEqual(
                    task_advance._pre_review_action(record, d6(), PR, {}, HEAD),
                    'RUN_REMOTE_TEST')
                self.assertEqual(
                    task_advance.decide(record, d6(), PR, {}), 'RUN_REMOTE_TEST')

    def test_second_unreachable_runner_waits_for_owner(self):
        for code in ('SSH_UNAVAILABLE', 'SSH_FAILED'):
            with self.subTest(code=code):
                record = base_record()
                record['remote_test_outcomes'] = [unreachable(code), unreachable(code)]
                self.assertEqual(
                    task_advance._pre_review_action(record, d6(), PR, {}, HEAD),
                    'WAITING_OWNER')
                self.assertEqual(
                    task_advance.decide(record, d6(), PR, {}), 'WAITING_OWNER')

    def test_second_unreachable_runner_records_owner_notice(self):
        codes, result = task_advance._remote_test_waiting_result(
            '1' * 32, 17, d6(), 'UNREACHABLE')
        self.assertEqual(codes, ['REMOTE_TEST_UNREACHABLE', 'OWNER_NOTICE'])
        self.assertEqual(result['owner_notice'], {
            'task_id': '1' * 32, 'pr': 17, 'head': HEAD,
            'reason': 'REMOTE_TEST_UNREACHABLE'})

    def test_other_unavailable_codes_keep_the_existing_rerun(self):
        record = base_record()
        record['remote_test_outcomes'] = [unreachable('REMOTE_BUSY')]
        self.assertEqual(task_advance._remote_test_status(record, d6()),
                         (None, None, None))
        self.assertEqual(
            task_advance._pre_review_action(record, d6(), PR, {}, HEAD),
            'RUN_REMOTE_TEST')

    def test_counts_use_only_this_head_and_executor(self):
        record = base_record()
        record['remote_test_outcomes'] = [
            unreachable(sha=OTHER_HEAD),
            unreachable(executor_task=OTHER_EXECUTOR),
            unreachable(),
        ]
        self.assertEqual(
            task_advance._remote_test_unreachable_count(record, d6()), 1)
        self.assertEqual(
            task_advance._pre_review_action(record, d6(), PR, {}, HEAD),
            'RUN_REMOTE_TEST')

    def test_real_test_failure_still_uses_ec2_repair(self):
        record = base_record()
        record['remote_test_receipts'] = [
            receipt(record, failure_excerpt='AssertionError: expected value')]
        self.assertEqual(task_advance._remote_test_status(record, d6())[0], 'FAIL')
        self.assertEqual(
            task_advance._pre_review_action(record, d6(), PR, {}, HEAD),
            'REPAIR_REVIEW')
        self.assertEqual(task_advance.decide(record, d6(), PR, {}), 'REPAIR_REVIEW')

    def test_pass_still_dispatches_review(self):
        record = base_record()
        value = {
            'task_id': record['task_id'],
            'run_id': record['run_id'],
            'executor_task': EXECUTOR,
            'source_sha': HEAD,
            'result': 'PASS',
            'modules': ['test_task_advance'],
            'ran_total': 1,
            'elapsed_s': 1.0,
            'final': ['OK'],
            'failures': [],
        }
        value['receipt_digest'] = hashlib.sha256(_json_bytes(value)).hexdigest()
        record['remote_test_receipts'] = [value]
        self.assertEqual(
            task_advance._pre_review_action(record, d6(), PR, {}, HEAD),
            'DISPATCH_REVIEW')


if __name__ == '__main__':
    unittest.main()
