"""#1502 WP-D: policy.json carries the repair, Debug, split and test-failure limits."""

import json
import os
import tempfile
import unittest

from llmtool import repair_policy as rp
from llmtool.errors import LLMToolError

GOOD = {'schema_version': 1, 'repair_max': 2, 'parent_debug_max': 1,
        'group_debug_max': 1, 'child_max': 3, 'replan_max': 1,
        'test_fail_max': 2, 'review_fail_max': 3,
        'waiting_notify_after_hours': 24}


class PolicyFileTests(unittest.TestCase):
    def _write(self, value):
        handle, path = tempfile.mkstemp(suffix='.json')
        with os.fdopen(handle, 'w') as stream:
            stream.write(value if type(value) is str else json.dumps(value))
        self.addCleanup(os.unlink, path)
        return path

    def test_repository_policy_has_the_owner_limits(self):
        self.assertEqual(rp.load_policy(), GOOD)
        self.assertEqual(rp.policy_path().name, 'policy.json')

    def test_valid_file_loads(self):
        self.assertEqual(rp.load_policy(self._write(GOOD)), GOOD)

    def test_bad_files_refuse_policy_invalid(self):
        bad = [dict(GOOD, schema_version=2), dict(GOOD, repair_max=True),
               dict(GOOD, child_max=-1), dict(GOOD, replan_max='1'),
               dict(GOOD, extra=1),
               {key: value for key, value in GOOD.items() if key != 'test_fail_max'},
               [GOOD], 'not json', ' ' * 5000]
        for value in bad:
            with self.subTest(value=str(value)[:40]):
                with self.assertRaises(LLMToolError) as caught:
                    rp.load_policy(self._write(value))
                self.assertEqual(str(caught.exception), 'POLICY_INVALID')

    def test_missing_file_refuses(self):
        missing = os.path.join(tempfile.mkdtemp(), 'policy.json')
        with self.assertRaises(LLMToolError) as caught:
            rp.load_policy(missing)
        self.assertEqual(str(caught.exception), 'POLICY_INVALID')


class ReviewCountingTests(unittest.TestCase):
    P = GOOD

    def test_one_head_counts_once_however_often_it_is_reviewed(self):
        # v1 and v2 FAIL on one HEAD: the map keeps one outcome per HEAD.
        self.assertEqual(rp.review_fail_count({'a' * 40: 'FAIL'}), 1)
        self.assertEqual(rp.review_fail_count({'a' * 40: 'FAIL', 'b' * 40: 'FAIL_NO_BLOCKING',
                                               'c' * 40: 'PASS', 'd' * 40: 'FAIL'}), 2)

    def test_unknown_outcome_makes_the_count_unknown_and_used_up(self):
        self.assertEqual(rp.review_fail_count({'a' * 40: 'UNKNOWN'}), rp.UNKNOWN)
        self.assertEqual(rp.review_fail_count(None), rp.UNKNOWN)
        self.assertEqual(rp.review_action(kind='root', fail_count=rp.UNKNOWN, policy=self.P),
                         'SPLIT')

    def test_root_fail_two_needs_debug_and_only_rca_confirmed_repairs(self):
        action = lambda count, status=None: rp.review_action(
            kind='root', fail_count=count, debug_status=status, policy=self.P)
        self.assertEqual(action(1), 'REPAIR')
        self.assertEqual(action(2), 'DEBUG')
        self.assertEqual(action(2, 'RCA_CONFIRMED'), 'REPAIR')
        self.assertEqual(action(2, 'RCA_UNCONFIRMED'), 'SPLIT')
        self.assertEqual(action(2, 'BLOCKED'), 'SPLIT')
        self.assertEqual(action(3, 'RCA_CONFIRMED'), 'SPLIT')

    def test_child_shares_one_group_debug_and_replan_never_splits(self):
        child = lambda count, status=None, used=0: rp.review_action(
            kind='child', fail_count=count, debug_status=status,
            group_debug_used=used, policy=self.P)
        self.assertEqual(child(2), 'DEBUG')
        self.assertEqual(child(2, used=1), 'PM_DECIDE')
        self.assertEqual(child(2, used=rp.UNKNOWN), 'PM_DECIDE')
        self.assertEqual(child(2, 'RCA_CONFIRMED', 1), 'REPAIR')
        self.assertEqual(child(3, 'RCA_CONFIRMED', 1), 'PM_DECIDE')
        self.assertEqual(rp.review_action(kind='replan', fail_count=3, policy=self.P),
                         'FAILED_FINAL')
        self.assertEqual(rp.review_action(kind='replan', fail_count=2,
                                          debug_status='BLOCKED', policy=self.P),
                         'FAILED_FINAL')


if __name__ == '__main__':
    unittest.main()
