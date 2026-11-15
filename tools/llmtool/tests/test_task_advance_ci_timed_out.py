import unittest
from unittest.mock import Mock, patch

from llmtool import task_advance
try:
    import test_task_advance as tta
except ImportError:
    from tests import test_task_advance as tta


def setUpModule():
    tta.setUpModule()


def tearDownModule():
    tta.tearDownModule()


class TaskAdvanceCiTimedOutTests(unittest.TestCase):
    def setUp(self):
        self.helper = tta.TaskAdvanceCiFailRepairTests(
            methodName='test_neutral_keeps_ci_failed')

    def test_owner_cases_1_2_timed_out_routes_to_repair(self):
        result, dispatched, calls, repaired, gh_calls = self.helper.linux('timed_out')
        self.assertEqual(result['step'], 'CI_FAILED')
        self.assertEqual(len(repaired), 1)
        self.assertEqual(repaired[0]['ci_failure']['check_name'], 'check (Linux 3.14)')
        self.assertEqual(repaired[0]['ci_failure']['conclusion'], 'failure')

        result, calls, repaired = self.helper.mac_after_pass('timed_out')
        self.assertEqual(result['step'], 'CI_FAILED')
        self.assertEqual(len(repaired), 1)
        self.assertEqual(repaired[0]['ci_failure']['check_name'], 'check (macOS 3.13)')
        self.assertEqual(repaired[0]['ci_failure']['conclusion'], 'failure')

    def test_owner_case_3_no_double_count(self):
        claim = {
            'executor_task': 'executor-1',
            'pr': 17,
            'head_sha': tta.HEAD,
            'family': 'pr-17-ec2',
            'state': 'DISPATCHED',
            'version': 1,
            'attempt': 1,
            'repair_task_id': '1' * 31 + '9',
            'comment_id': None,
            'packet_sha256': 'f' * 64,
            'claimed_at': '2026-11-13T01:00:00Z',
        }
        rec = tta.base_record(review_repair_claims=[claim])
        result, dispatched, calls, repaired, gh_calls = self.helper.linux('timed_out', record=rec)
        self.assertEqual(len(repaired), 0)

    def test_owner_case_4_used_up_test_count_stops(self):
        used = [
            {'executor_task': 'executor-1', 'pr': 17, 'head_sha': 'b' * 40,
             'version': n, 'attempt': n, 'family': 'pr-17-ec2', 'state': 'FAILED',
             'repair_task_id': f'{n:032x}', 'comment_id': None,
             'packet_sha256': 'f' * 64, 'claimed_at': f'2026-11-13T0{n}:00:00Z'}
            for n in (1, 2)
        ]
        rec = tta.base_record(review_repair_claims=used)
        result, dispatched, calls, repaired, gh_calls = self.helper.linux('timed_out', record=rec)
        self.assertEqual(result['step'], 'STOPPED')
        self.assertEqual(result['codes'], ['TEST_FAIL_LIMIT_REACHED'])
        self.assertEqual(len(repaired), 0)

    def test_owner_case_5_mac_capacity_exemption_never_applies_to_timed_out(self):
        with patch.object(task_advance, '_mac_capacity_cancel', Mock()) as mock_cancel:
            result, calls, repaired = self.helper.mac_after_pass('timed_out')
            mock_cancel.assert_not_called()

    def test_owner_case_6_env_conclusions_wait_uncounted(self):
        for conclusion in ('cancelled', 'startup_failure'):
            expected_code = 'CI_ENV_' + conclusion.upper()
            with self.subTest(conclusion=conclusion, check='linux'):
                result, dispatched, calls, repaired, gh_calls = self.helper.linux(conclusion)
                self.assertEqual(result['step'], 'WAITING_ENV')
                self.assertEqual(result['codes'], [expected_code])
                self.assertEqual(len(repaired), 0)
            with self.subTest(conclusion=conclusion, check='mac'):
                result, calls, repaired = self.helper.mac_after_pass(conclusion)
                self.assertEqual(result['step'], 'WAITING_ENV')
                self.assertEqual(result['codes'], [expected_code])
                self.assertEqual(len(repaired), 0)

    def test_owner_case_7_success_dispatches_review(self):
        result, dispatched, calls, repaired, gh_calls = self.helper.linux('success')
        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        self.assertEqual(len(repaired), 0)

    def test_owner_case_8_timed_out_stale_head_waits_ci(self):
        stale_run = tta.check_run(1, head='c' * 40, conclusion='timed_out')
        reply = ({'total_count': 1, 'check_runs': [stale_run]}, self.helper.FILES)
        result, dispatched, calls, repaired, gh_calls = tta.advance_with_ci_repair(reply)
        self.assertEqual(len(repaired), 0)
        self.assertNotEqual(result['step'], 'DISPATCH_REVIEW')


if __name__ == '__main__':
    unittest.main()
