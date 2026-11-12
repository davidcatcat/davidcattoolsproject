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


class AdmissionTests(unittest.TestCase):
    P = GOOD

    def test_second_repair_needs_a_bound_rca_and_two_is_the_limit(self):
        admit = lambda done, rca=False: rp.admit_repair(
            repair_count=done, rca_confirmed=rca, policy=self.P)
        self.assertIsNone(admit(0))
        self.assertEqual(admit(1), 'REPAIR_DEBUG_REQUIRED')
        self.assertIsNone(admit(1, True))
        self.assertEqual(admit(2, True), 'REPAIR_LIMIT_REACHED')
        self.assertEqual(admit(rp.UNKNOWN, True), 'REPAIR_LIMIT_REACHED')

    def test_one_parent_debug_per_task_and_one_group_debug_per_lineage(self):
        debug = lambda kind, parent=0, group=0: rp.admit_debug(
            kind=kind, parent_debug_used=parent, group_debug_used=group, policy=self.P)
        self.assertIsNone(debug('root'))
        self.assertEqual(debug('root', parent=1), 'DEBUG_PARENT_USED')
        self.assertEqual(debug('replan', parent=rp.UNKNOWN), 'DEBUG_PARENT_USED')
        self.assertIsNone(debug('child', parent=1))
        self.assertEqual(debug('child', group=1), 'DEBUG_GROUP_USED')

    def test_test_failures_repair_twice_then_stop(self):
        action = lambda done: rp.test_failure_action(test_fail_count=done, policy=self.P)
        self.assertEqual([action(0), action(1), action(2), action(rp.UNKNOWN)],
                         ['REPAIR', 'REPAIR', 'STOPPED', 'STOPPED'])

    def test_unknown_kind_and_zero_fail_count_refuse(self):
        for kwargs, code in ((dict(kind='leaf', fail_count=1), 'LINEAGE_KIND_INVALID'),
                             (dict(kind='root', fail_count=0), 'REVIEW_FAIL_COUNT_INVALID')):
            with self.assertRaises(LLMToolError) as caught:
                rp.review_action(policy=self.P, **kwargs)
            self.assertEqual(str(caught.exception), code)


class LineageTests(unittest.TestCase):
    P = GOOD

    def test_only_a_root_splits_and_never_into_a_fourth_child(self):
        split = lambda kind, have, new: rp.admit_split(
            kind=kind, child_count=have, new_children=new, policy=self.P)
        self.assertIsNone(split('root', 0, 3))
        self.assertEqual(split('root', 3, 1), 'SPLIT_CHILD_LIMIT')
        self.assertEqual(split('root', rp.UNKNOWN, 1), 'SPLIT_CHILD_LIMIT')
        self.assertEqual(split('child', 0, 1), 'SPLIT_NOT_ROOT')
        self.assertEqual(split('replan', 0, 1), 'SPLIT_NOT_ROOT')
        self.assertEqual(split('root', 0, 0), 'SPLIT_CHILDREN_INVALID')

    def test_one_replan_per_lineage_from_a_child_only(self):
        replan = lambda kind, used, replaces=('c1',): rp.admit_replan(
            kind=kind, replan_used=used, replaces=list(replaces), policy=self.P)
        self.assertIsNone(replan('child', 0))
        self.assertEqual(replan('child', 1), 'REPLAN_USED')
        self.assertEqual(replan('child', rp.UNKNOWN), 'REPLAN_USED')
        self.assertEqual(replan('root', 0), 'REPLAN_NOT_CHILD')
        self.assertEqual(replan('child', 0, ()), 'REPLAN_REPLACES_INVALID')

    def test_drop_needs_a_reason(self):
        self.assertIsNone(rp.admit_drop(reason='original behaviour unchanged: see PR'))
        for reason in (None, '', '   ', 'x' * 501):
            self.assertEqual(rp.admit_drop(reason=reason), 'DROP_REASON_REQUIRED')


class LineageOutcomeTests(unittest.TestCase):
    P = GOOD

    def test_owner_notice_for_every_unsuccessful_end_only(self):
        self.assertEqual([rp.owner_notice(state) for state in
                          ('DROPPED', 'STOPPED', 'FAILED_FINAL', 'CLOSED', 'OPEN')],
                         [True, True, True, False, False])

    def test_root_closes_only_when_every_obligation_closed(self):
        child = lambda name, state: {'id': name, 'kind': 'child', 'state': state}
        self.assertEqual(rp.root_outcome([child('a', 'CLOSED'), child('b', 'CLOSED')]), 'CLOSED')
        self.assertEqual(rp.root_outcome([child('a', 'CLOSED'), child('b', 'OPEN')]), 'OPEN')
        self.assertEqual(rp.root_outcome([child('a', 'CLOSED'), child('b', 'DROPPED')]), 'FAILED')
        replan = {'id': 'r', 'kind': 'replan', 'state': 'CLOSED', 'replaces': ['b']}
        # A CLOSED REPLAN meets the obligation of the child it replaced.
        self.assertEqual(rp.root_outcome([child('a', 'CLOSED'), child('b', 'STOPPED'), replan]),
                         'CLOSED')
        failed = dict(replan, state='FAILED_FINAL')
        self.assertEqual(rp.root_outcome([child('a', 'CLOSED'), child('b', 'STOPPED'), failed]),
                         'FAILED')

    def test_auth_waiting_notifies_at_once_others_after_24_hours(self):
        due = lambda reason, since: rp.waiting_notice_due(
            reason=reason, waiting_since=since, now='2026-11-13T12:00:00Z', policy=self.P)
        self.assertTrue(due('AUTH', '2026-11-13T11:59:59Z'))
        self.assertFalse(due('ENV', '2026-11-12T12:00:01Z'))
        self.assertTrue(due('ENV', '2026-11-12T12:00:00Z'))
        self.assertTrue(due('NO_REVIEWER', 'yesterday'))
        self.assertTrue(due('OTHER', '2026-11-13T11:59:59Z'))


if __name__ == '__main__':
    unittest.main()
