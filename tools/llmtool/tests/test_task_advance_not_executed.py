import unittest
from unittest.mock import patch

from llmtool import task_advance

try:
    from tests.test_task_advance import HEAD, PR, base_record, d6, remote_receipt
except ImportError:
    from test_task_advance import HEAD, PR, base_record, d6, remote_receipt


_code_tree_patch = None


def setUpModule():
    global _code_tree_patch
    _code_tree_patch = patch('llmtool.source_freshness.code_tree', return_value='d' * 64)
    _code_tree_patch.start()


def tearDownModule():
    global _code_tree_patch
    if _code_tree_patch is not None:
        _code_tree_patch.stop()


class TestTaskAdvanceNotExecuted(unittest.TestCase):
    def test_receipt_not_executed_predicates(self):
        valid = {'result': 'FAIL', 'ran_total': 0, 'failures': []}
        self.assertTrue(task_advance._receipt_not_executed(valid))

        has_failures = {'result': 'FAIL', 'ran_total': 0, 'failures': ['sample_failure']}
        self.assertFalse(task_advance._receipt_not_executed(has_failures))

        ran_positive = {'result': 'FAIL', 'ran_total': 3, 'failures': []}
        self.assertFalse(task_advance._receipt_not_executed(ran_positive))

        pass_result = {'result': 'PASS', 'ran_total': 0, 'failures': []}
        self.assertFalse(task_advance._receipt_not_executed(pass_result))

        missing_failures = {'result': 'FAIL', 'ran_total': 0}
        self.assertFalse(task_advance._receipt_not_executed(missing_failures))

        non_int_ran = {'result': 'FAIL', 'ran_total': '0', 'failures': []}
        self.assertFalse(task_advance._receipt_not_executed(non_int_ran))

        bool_ran = {'result': 'FAIL', 'ran_total': False, 'failures': []}
        self.assertFalse(task_advance._receipt_not_executed(bool_ran))

        self.assertFalse(task_advance._receipt_not_executed(None))
        self.assertFalse(task_advance._receipt_not_executed([]))
        self.assertFalse(task_advance._receipt_not_executed('FAIL'))

    def test_remote_test_status_not_executed_and_fail(self):
        record = base_record()

        receipt_not_exec = remote_receipt(record, result='FAIL', ran_total=0, failures=[])
        record['remote_test_receipts'] = [receipt_not_exec]
        status, excerpt, receipt = task_advance._remote_test_status(record, d6())
        self.assertEqual(status, 'NOT_EXECUTED')
        self.assertIsNone(excerpt)
        self.assertEqual(receipt['receipt_digest'], receipt_not_exec['receipt_digest'])

        receipt_has_failures = remote_receipt(record, result='FAIL', ran_total=0,
                                              failures=['sample_failure'])
        record['remote_test_receipts'] = [receipt_has_failures]
        status, excerpt, receipt = task_advance._remote_test_status(record, d6())
        self.assertEqual(status, 'FAIL')
        self.assertEqual(receipt['receipt_digest'], receipt_has_failures['receipt_digest'])

        receipt_ran_positive = remote_receipt(record, result='FAIL', ran_total=3, failures=[])
        record['remote_test_receipts'] = [receipt_ran_positive]
        status, excerpt, receipt = task_advance._remote_test_status(record, d6())
        self.assertEqual(status, 'FAIL')
        self.assertEqual(receipt['receipt_digest'], receipt_ran_positive['receipt_digest'])

        receipt_missing_failures = remote_receipt(record, result='FAIL', ran_total=0)
        record['remote_test_receipts'] = [receipt_missing_failures]
        status, excerpt, receipt = task_advance._remote_test_status(record, d6())
        self.assertEqual(status, 'FAIL')
        self.assertEqual(receipt['receipt_digest'], receipt_missing_failures['receipt_digest'])

    def test_routing_not_executed_receipt(self):
        record = base_record()
        receipt = remote_receipt(record, result='FAIL', ran_total=0, failures=[], skipped=1)
        record['remote_test_receipts'] = [receipt]

        action = task_advance._pre_review_action(record, d6(), PR, {'repair_claims': []}, HEAD)
        self.assertEqual(action, 'NEEDS_ATTENTION')
        self.assertNotEqual(action, 'REPAIR_REVIEW')

        summary = task_advance._remote_test_receipt_summary(record, HEAD)
        self.assertIsNotNone(summary)
        self.assertEqual(summary['result'], 'NOT_EXECUTED')
        self.assertEqual(summary['ran_total'], 0)
        self.assertEqual(summary['receipt_digest'], receipt['receipt_digest'][:8])

    def test_routing_fail_with_failures_and_zero_ran(self):
        record = base_record()
        receipt = remote_receipt(record, result='FAIL', ran_total=0,
                                 failures=['sample_failure'])
        record['remote_test_receipts'] = [receipt]

        action = task_advance._pre_review_action(record, d6(), PR, {'repair_claims': []}, HEAD)
        self.assertEqual(action, 'REPAIR_REVIEW')

        summary = task_advance._remote_test_receipt_summary(record, HEAD)
        self.assertIsNotNone(summary)
        self.assertEqual(summary['result'], 'FAIL')
        self.assertEqual(summary['ran_total'], 0)

    def test_routing_fail_with_positive_ran_total(self):
        record = base_record()
        receipt = remote_receipt(record, result='FAIL', ran_total=2, failures=[])
        record['remote_test_receipts'] = [receipt]

        action = task_advance._pre_review_action(record, d6(), PR, {'repair_claims': []}, HEAD)
        self.assertEqual(action, 'REPAIR_REVIEW')

        summary = task_advance._remote_test_receipt_summary(record, HEAD)
        self.assertIsNotNone(summary)
        self.assertEqual(summary['result'], 'FAIL')
        self.assertEqual(summary['ran_total'], 2)

    def test_decide_no_pr_branch(self):
        record = base_record()
        no_pr = None

        receipt_not_exec = remote_receipt(record, result='FAIL', ran_total=0, failures=[])
        record['remote_test_receipts'] = [receipt_not_exec]
        action = task_advance.decide(record, d6(), no_pr, {'repair_claims': []})
        self.assertEqual(action, 'NEEDS_ATTENTION')

        receipt_fail = remote_receipt(record, result='FAIL', ran_total=1, failures=['f'])
        record['remote_test_receipts'] = [receipt_fail]
        action = task_advance.decide(record, d6(), no_pr, {'repair_claims': []})
        self.assertEqual(action, 'REPAIR_REVIEW')

    def test_publish_remote_test_step_not_executed_hold(self):
        record = base_record()
        receipt_not_exec = remote_receipt(record, result='FAIL', ran_total=0, failures=[])
        record['remote_test_receipts'] = [receipt_not_exec]

        class DummyStore:
            def get(self, _task_id):
                return record

        with patch('llmtool.remote_test._task_path_modules', return_value=['sample_mod']):
            status, hold = task_advance._pre_pr_remote_gate(
                record, DummyStore(), 'executor-1', HEAD, 'sample-branch')
            self.assertEqual(status, 'NOT_EXECUTED')
            self.assertIsNotNone(hold)
            self.assertEqual(hold['step'], 'NEEDS_ATTENTION')
            self.assertEqual(hold['codes'], ['TEST_REMOTE_MODULE_NOT_EXECUTED'])
            self.assertEqual(hold['result']['remote_test']['result'], 'NOT_EXECUTED')

    def test_advance_not_executed_step_codes(self):
        record = base_record()
        receipt = remote_receipt(record, result='FAIL', ran_total=0, failures=[])
        record['remote_test_receipts'] = [receipt]

        class DummyStore:
            def get(self, _task_id):
                return record

            def task_ids(self):
                return [record['task_id']]

            def record_events_consumed(self, *args, **kwargs):
                pass

        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_review_outcomes', return_value=[]), \
             patch.object(task_advance, '_pr_snapshot', return_value=(PR, None)):
            res = task_advance.advance(task_id=record['task_id'], store=DummyStore(),
                                       publish_gh_runner=lambda *args: None)
            self.assertEqual(res['step'], 'NEEDS_ATTENTION')
            self.assertIn('TEST_REMOTE_MODULE_NOT_EXECUTED', res['codes'])


if __name__ == '__main__':
    unittest.main()
