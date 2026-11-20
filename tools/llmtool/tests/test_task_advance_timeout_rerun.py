import hashlib
import unittest

from llmtool import task_advance
from llmtool.task_store import _json_bytes


HEAD = 'a' * 40
OTHER_HEAD = 'b' * 40
EXECUTOR = 'executor-1'
PR = {'number': 17, 'head': {'sha': HEAD}, 'merged': False}


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
    return {'result_head': head, 'root': '/repo/task', 'recorded_result_head': head,
            'dirty': False}


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


class TaskAdvanceTimeoutRerunTests(unittest.TestCase):
    def test_one_timeout_returns_run_remote_test_after_pr(self):
        record = base_record()
        record['remote_test_outcomes'] = [outcome()]
        self.assertEqual(
            task_advance._pre_review_action(record, d6(), PR, {}, HEAD),
            'RUN_REMOTE_TEST')
        self.assertEqual(task_advance.decide(record, d6(), PR, {}), 'RUN_REMOTE_TEST')

    def test_one_timeout_returns_publish_before_pr(self):
        record = base_record()
        record['remote_test_outcomes'] = [outcome()]
        self.assertEqual(task_advance.decide(record, d6(), None, {}), 'PUBLISH')

    def test_two_timeouts_return_needs_attention_after_pr(self):
        record = base_record()
        record['remote_test_outcomes'] = [outcome(), outcome()]
        self.assertEqual(
            task_advance._pre_review_action(record, d6(), PR, {}, HEAD),
            'NEEDS_ATTENTION')
        self.assertEqual(task_advance.decide(record, d6(), PR, {}), 'NEEDS_ATTENTION')

    def test_two_timeouts_return_publish_before_pr(self):
        record = base_record()
        record['remote_test_outcomes'] = [outcome(), outcome()]
        self.assertEqual(task_advance.decide(record, d6(), None, {}), 'PUBLISH')

    def test_sha_mismatch_returns_needs_attention_after_pr(self):
        record = base_record()
        record['remote_test_receipts'] = [receipt(record, result='SHA_MISMATCH')]
        self.assertEqual(
            task_advance._pre_review_action(record, d6(), PR, {}, HEAD),
            'NEEDS_ATTENTION')
        self.assertEqual(task_advance.decide(record, d6(), PR, {}), 'NEEDS_ATTENTION')

    def test_sha_mismatch_returns_publish_before_pr(self):
        record = base_record()
        record['remote_test_receipts'] = [receipt(record, result='SHA_MISMATCH')]
        self.assertEqual(task_advance.decide(record, d6(), None, {}), 'PUBLISH')
