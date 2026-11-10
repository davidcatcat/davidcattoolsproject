import hashlib
import unittest
from unittest.mock import patch

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


class FakeStore:
    def __init__(self, record):
        self.record = record

    def get(self, _task_id):
        return self.record


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

    def test_two_timeouts_return_needs_attention_before_pr(self):
        record = base_record()
        record['remote_test_outcomes'] = [outcome(), outcome()]
        self.assertEqual(task_advance.decide(record, d6(), None, {}), 'NEEDS_ATTENTION')

    def test_sha_mismatch_returns_needs_attention_after_pr(self):
        record = base_record()
        record['remote_test_receipts'] = [receipt(record, result='SHA_MISMATCH')]
        self.assertEqual(
            task_advance._pre_review_action(record, d6(), PR, {}, HEAD),
            'NEEDS_ATTENTION')
        self.assertEqual(task_advance.decide(record, d6(), PR, {}), 'NEEDS_ATTENTION')

    def test_sha_mismatch_returns_needs_attention_before_pr(self):
        record = base_record()
        record['remote_test_receipts'] = [receipt(record, result='SHA_MISMATCH')]
        self.assertEqual(task_advance.decide(record, d6(), None, {}), 'NEEDS_ATTENTION')

    @patch.object(task_advance, '_run_remote_test')
    def test_pre_pr_remote_gate_reruns_once_after_one_timeout_and_passes(self, mock_run):
        record = base_record()
        record['remote_test_outcomes'] = [outcome()]
        store = FakeStore(record)

        def do_rerun(st, task_id, executor_task, head, ref, pr):
            store.record = dict(record, remote_test_receipts=[
                receipt(record, result='PASS')])
            return 0

        mock_run.side_effect = do_rerun

        status, hold = task_advance._pre_pr_remote_gate(
            record, store, EXECUTOR, HEAD, 'feat/task')
        self.assertEqual(status, 'PASS')
        self.assertIsNone(hold)
        mock_run.assert_called_once_with(
            store, record['task_id'], EXECUTOR, HEAD, 'refs/heads/feat/task', None)

    @patch.object(task_advance, '_run_remote_test')
    def test_pre_pr_remote_gate_reruns_once_after_one_timeout_and_holds_on_second(self, mock_run):
        record = base_record()
        record['remote_test_outcomes'] = [outcome()]
        store = FakeStore(record)

        def do_rerun(st, task_id, executor_task, head, ref, pr):
            store.record = dict(record, remote_test_outcomes=[outcome(), outcome()])
            return 0

        mock_run.side_effect = do_rerun

        status, hold = task_advance._pre_pr_remote_gate(
            record, store, EXECUTOR, HEAD, 'feat/task')
        self.assertEqual(status, 'TIMEOUT')
        self.assertIsNotNone(hold)
        self.assertEqual(hold['step'], 'NEEDS_ATTENTION')
        self.assertEqual(hold['codes'], ['REMOTE_TEST_TIMEOUT'])
        mock_run.assert_called_once_with(
            store, record['task_id'], EXECUTOR, HEAD, 'refs/heads/feat/task', None)

    @patch.object(task_advance, '_run_remote_test')
    def test_pre_pr_remote_gate_holds_after_two_timeouts_without_rerun(self, mock_run):
        record = base_record()
        record['remote_test_outcomes'] = [outcome(), outcome()]
        store = FakeStore(record)

        status, hold = task_advance._pre_pr_remote_gate(
            record, store, EXECUTOR, HEAD, 'feat/task')
        self.assertEqual(status, 'TIMEOUT')
        self.assertIsNotNone(hold)
        self.assertEqual(hold['step'], 'NEEDS_ATTENTION')
        self.assertEqual(hold['codes'], ['REMOTE_TEST_TIMEOUT'])
        mock_run.assert_not_called()

    @patch.object(task_advance, '_run_remote_test')
    def test_pre_pr_remote_gate_holds_on_sha_mismatch_without_rerun(self, mock_run):
        record = base_record()
        record['remote_test_receipts'] = [receipt(record, result='SHA_MISMATCH')]
        store = FakeStore(record)

        status, hold = task_advance._pre_pr_remote_gate(
            record, store, EXECUTOR, HEAD, 'feat/task')
        self.assertEqual(status, 'SHA_MISMATCH')
        self.assertIsNotNone(hold)
        self.assertEqual(hold['step'], 'NEEDS_ATTENTION')
        self.assertEqual(hold['codes'], ['REMOTE_TEST_SHA_MISMATCH'])
        mock_run.assert_not_called()
