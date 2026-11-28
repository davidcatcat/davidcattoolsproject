"""#2793 A13.2: an unusable TestProfile fails closed at the advance gates.

Review finding F1 of PR #2952: with an unusable profile the required module
set looked empty, so a stored receipt covered the head and review could be
dispatched. These tests drive the real status, summary, decision, advance and
CI failure overlap code with the default provider made unusable.
"""

import contextlib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from llmtool import ci_failure_overlap, remote_test, task_advance, test_profile

try:
    from tests.test_a13_remote_profile_wiring_r1 import unusable_cases
    from tests.test_task_advance import HEAD, PR, base_record, d6, remote_receipt
except ImportError:
    from test_a13_remote_profile_wiring_r1 import unusable_cases
    from test_task_advance import HEAD, PR, base_record, d6, remote_receipt

MODULE = 'test_task_advance'
PATH = f'tools/llmtool/tests/{MODULE}.py'


def covered_record():
    """A Task whose declared module has a stored exact-head PASS receipt."""
    record = base_record(remote_test_outcomes=[])
    record['developer'] = dict(record['developer'], read_paths=[PATH], write_paths=[])
    record['remote_test_receipts'] = [remote_receipt(record)]
    return record


class Store:
    def __init__(self, record):
        self.record = record

    def get(self, task_id):
        if task_id != self.record['task_id']:
            raise KeyError(task_id)
        return self.record

    def task_ids(self):
        return [self.record['task_id']]

    def record_published_pr(self, _task_id, pr, head_sha):
        return {'pr': pr, 'head_sha': head_sha}

    def append_remote_test_outcome(self, _task_id, outcome):
        self.record['remote_test_outcomes'].append(outcome)
        return {'outcome': outcome,
                'counter': len(self.record['remote_test_outcomes']) - 1}


class ReceiptStatusGateTests(unittest.TestCase):
    def test_default_profile_results_are_unchanged(self):
        record = covered_record()
        self.assertEqual(task_advance._remote_test_status(record, d6())[0], 'PASS')
        self.assertEqual(task_advance._remote_test_receipt_summary(record, HEAD)['result'],
                         'PASS')
        self.assertEqual(task_advance.decide(record, d6(), PR, {}), 'DISPATCH_REVIEW')
        self.assertEqual(task_advance._remote_test_status(base_record(), d6()),
                         ('ADMISSION_DENIED', None, None))
        self.assertEqual(task_advance._declared_test_modules(record), (MODULE,))

    def test_f1_stored_pass_receipt_never_covers_the_head(self):
        record = covered_record()
        for label, unusable in unusable_cases():
            with self.subTest(label), unusable:
                self.assertEqual(task_advance._remote_test_status(record, d6()),
                                 (None, None, None))
                self.assertEqual(task_advance._remote_test_status(
                    record, d6(), ('test_extra_2793',)), (None, None, None))
                self.assertIsNone(task_advance._remote_test_receipt_summary(record, HEAD))
                self.assertEqual(task_advance.decide(record, d6(), PR, {}),
                                 'RUN_REMOTE_TEST')

    def test_f1_admission_denied_outcome_never_allows_review(self):
        # No declared module and a stored denial: DISPATCH_REVIEW by default.
        record = base_record()
        self.assertEqual(task_advance.decide(record, d6(), PR, {}), 'DISPATCH_REVIEW')
        for label, unusable in unusable_cases():
            with self.subTest(label), unusable:
                self.assertEqual(task_advance._remote_test_status(record, d6()),
                                 (None, None, None))
                self.assertEqual(task_advance.decide(record, d6(), PR, {}),
                                 'RUN_REMOTE_TEST')

    def test_f1_refused_review_claim_is_not_redispatched(self):
        record = covered_record()
        record['review_dispatch_claim'] = [{
            'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'UNKNOWN',
            'failure_reason': 'REVIEW_OUTPUT_UNUSABLE'}]
        with patch.object(task_advance, '_failure_reason',
                          return_value='REVIEW_OUTPUT_UNUSABLE'):
            self.assertEqual(task_advance.decide(record, d6(), PR, {}),
                             'REVIEW_REDISPATCH')
            for label, unusable in unusable_cases():
                with self.subTest(label), unusable:
                    self.assertEqual(task_advance.decide(record, d6(), PR, {}),
                                     'RUN_REMOTE_TEST')

    def test_stored_fail_receipt_stays_a_fail(self):
        record = covered_record()
        record['remote_test_receipts'] = [remote_receipt(record, result='FAIL')]
        for label, unusable in unusable_cases():
            with self.subTest(label), unusable:
                self.assertEqual(task_advance._remote_test_status(record, d6())[0], 'FAIL')
                self.assertEqual(task_advance._remote_test_receipt_summary(
                    record, HEAD)['result'], 'FAIL')

    def test_skipped_only_receipt_is_never_pass(self):
        record = covered_record()
        record['remote_test_receipts'] = [remote_receipt(
            record, final=['OK (skipped=1)'], module_results=[dict(
                module=MODULE, ran=1, skipped=1, final='OK (skipped=1)')])]
        self.assertIsNone(task_advance._remote_test_status(record, d6())[0])
        self.assertIsNone(task_advance._remote_test_receipt_summary(record, HEAD))


class AdvanceGateTests(unittest.TestCase):
    def setUp(self):
        patcher = patch('llmtool.source_freshness.code_tree', return_value='d' * 64)
        patcher.start()
        self.addCleanup(patcher.stop)

    @contextlib.contextmanager
    def runtime(self):
        def remote_get(path):
            return ({'number': 17, 'head': {'sha': HEAD}}
                    if path == 'pulls/17' else [])

        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
                patch.object(task_advance, '_pr_snapshot', return_value=PR), \
                patch.object(remote_test, 'read_d6_registration',
                             return_value=SimpleNamespace(result_sha=HEAD)), \
                patch.object(remote_test, '_github_get', remote_get), \
                patch.object(remote_test, '_ssh_transport') as transport:
            yield transport

    def test_f1_advance_runs_no_review_and_reports_the_profile_code(self):
        for label, unusable in unusable_cases():
            record = covered_record()
            dispatch = Mock()
            with self.subTest(label), self.runtime() as transport, unusable:
                for _attempt in range(2):
                    result = task_advance.advance(
                        task_id=record['task_id'], store=Store(record), pr_number=17,
                        github_get=lambda _path: [], dispatch_review_call=dispatch)
                    self.assertEqual(result['step'], 'RUN_REMOTE_TEST')
                    self.assertEqual(result['codes'], ['TEST_PROFILE_INVALID'])
                    self.assertNotIn('remote_test', result['result'])
                dispatch.assert_not_called()
                transport.assert_not_called()
                self.assertEqual(
                    {(item['result'], item['code'])
                     for item in record['remote_test_outcomes']},
                    {('UNKNOWN', 'TEST_PROFILE_INVALID')})


class OverlapGateTests(unittest.TestCase):
    RUN = {'output': {'summary': 'failing modules: test_unrelated_2793'}}

    @staticmethod
    def get(path):
        return ([{'filename': 'src/module.py'}]
                if path.startswith('pulls/17/files') else [])

    def status(self, record):
        return task_advance._ci_failure_overlap_status(self.RUN, record, 17, self.get)

    def test_default_profile_reports_no_overlap(self):
        self.assertEqual(self.status(covered_record()),
                         (ci_failure_overlap.NO_OVERLAP, ['test_unrelated_2793']))

    def test_unusable_profile_reports_unknown(self):
        record = covered_record()
        for label, unusable in unusable_cases():
            with self.subTest(label), unusable:
                self.assertIsNone(task_advance._declared_test_modules(record))
                self.assertEqual(self.status(record),
                                 (ci_failure_overlap.UNKNOWN, ['test_unrelated_2793']))

    def test_real_default_provider_is_restored(self):
        for label, unusable in unusable_cases():
            with self.subTest(label):
                with unusable:
                    pass
                self.assertIs(type(test_profile.default_profile()),
                              test_profile.TestProfile)


if __name__ == '__main__':
    unittest.main()
