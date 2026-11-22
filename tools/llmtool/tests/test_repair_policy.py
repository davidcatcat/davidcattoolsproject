"""#1502 WP-D: policy.json carries the repair, Debug, split and test-failure limits."""

import datetime
import json
import os
import tempfile
import unittest

from llmtool import repair_policy as rp
from llmtool.errors import LLMToolError

# A small valid policy (the limits before #2367) for the pure decision tests.
GOOD = {'schema_version': 1, 'repair_max': 2, 'parent_debug_max': 1,
        'group_debug_max': 1, 'child_max': 3, 'replan_max': 1,
        'test_fail_max': 2, 'review_fail_max': 3,
        'waiting_notify_after_hours': 24}
# #2367 (Owner 2026-11-19): the repository policy. 5 review repairs and 5
# EC2/CI test repairs per work item; review_fail_max is the FAIL after the
# last repair; each split child owns one Debug within the group limit.
OWNER = dict(GOOD, repair_max=5, test_fail_max=5, review_fail_max=6, parent_debug_max=3, group_debug_max=3)
# #2281 L0b: the optional limit-table keys (rows 2, 15, 16) in the repository
# policy; L7 adds row 17, the D6 writer lease length in hours.
LIMIT_TABLE_2281 = {'spec_preflight_large_file_lines': 2000, 'merge_train_members_max': 6,
                    'merge_train_files_per_pr_max': 100, 'revert_scope_prs_max': 5,
                    'd6_writer_lease_hours': 24}


class PolicyFileTests(unittest.TestCase):
    def _write(self, value):
        handle, path = tempfile.mkstemp(suffix='.json')
        with os.fdopen(handle, 'w') as stream:
            stream.write(value if type(value) is str else json.dumps(value))
        self.addCleanup(os.unlink, path)
        return path

    def test_repository_policy_has_the_owner_limits(self):
        self.assertEqual(rp.load_policy(),
                         dict(OWNER, timeout_continuation_max=1,
                              route_failure_pause_after=3, **LIMIT_TABLE_2281))
        self.assertEqual(rp.policy_path().name, 'policy.json')

    def test_repair_attempts_max_is_read_from_the_policy(self):
        # #2367: one source for the stored claim range and the run budget bound.
        self.assertEqual(rp.repair_attempts_max(), 5)
        self.assertEqual(rp.repair_attempts_max(self._write(GOOD)), 2)
        self.assertEqual(rp.repair_attempts_max(self._write(dict(GOOD, test_fail_max=4))), 4)
        # Unreadable: no repair is admitted (fail closed), never a default.
        self.assertEqual(rp.repair_attempts_max(self._write('not json')), 0)

    def test_review_fail_max_is_the_fail_after_the_last_repair(self):
        policy = rp.load_policy()
        self.assertEqual(policy['review_fail_max'], policy['repair_max'] + 1)

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

    def test_small_policy_allows_all_repairs_before_the_debug_threshold(self):
        action = lambda count, status=None: rp.review_action(
            kind='root', fail_count=count, debug_status=status, policy=self.P)
        self.assertEqual(action(1), 'REPAIR')
        self.assertEqual(action(2), 'REPAIR')
        self.assertEqual(action(2, 'RCA_CONFIRMED'), 'REPAIR')
        self.assertEqual(action(2, 'RCA_UNCONFIRMED'), 'REPAIR')
        self.assertEqual(action(2, 'BLOCKED'), 'REPAIR')
        self.assertEqual(action(3, 'RCA_CONFIRMED'), 'SPLIT')

    def test_child_shares_one_group_debug_and_replan_never_splits(self):
        child = lambda count, status=None, used=0: rp.review_action(
            kind='child', fail_count=count, debug_status=status,
            group_debug_used=used, policy=self.P)
        self.assertEqual(child(2), 'REPAIR')
        self.assertEqual(child(2, used=1), 'REPAIR')
        self.assertEqual(child(2, used=rp.UNKNOWN), 'REPAIR')
        self.assertEqual(child(2, 'RCA_CONFIRMED', 1), 'REPAIR')
        self.assertEqual(child(3, 'RCA_CONFIRMED', 1), 'PM_DECIDE')
        self.assertEqual(rp.review_action(kind='replan', fail_count=3, policy=self.P),
                         'FAILED_FINAL')
        self.assertEqual(rp.review_action(kind='replan', fail_count=2,
                                          debug_status='BLOCKED', policy=self.P),
                         'REPAIR')


class AdmissionTests(unittest.TestCase):
    P = GOOD

    def test_two_repairs_are_admitted_with_a_two_repair_cap(self):
        admit = lambda done, rca=False: rp.admit_repair(
            repair_count=done, rca_confirmed=rca, policy=self.P)
        self.assertIsNone(admit(0))
        self.assertIsNone(admit(1))
        self.assertIsNone(admit(1, True))
        self.assertEqual(admit(2, True), 'REPAIR_LIMIT_REACHED')
        self.assertEqual(admit(rp.UNKNOWN, True), 'REPAIR_LIMIT_REACHED')

    def test_one_parent_debug_per_task_and_one_group_debug_per_lineage(self):
        debug = lambda kind, parent=0, group=0: rp.admit_debug(
            kind=kind, parent_debug_used=parent, group_debug_used=group, policy=self.P)
        self.assertIsNone(debug('root'))
        self.assertEqual(debug('root', parent=1), 'DEBUG_PARENT_USED')
        self.assertEqual(debug('replan', parent=rp.UNKNOWN), 'DEBUG_PARENT_USED')
        # #2367: a child's own Debug (parent_debug_used) is its one share.
        self.assertEqual(debug('child', parent=1), 'DEBUG_GROUP_USED')
        self.assertEqual(debug('child', parent=rp.UNKNOWN), 'DEBUG_GROUP_USED')
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
        # Issue #2373: A CLOSED split child with replaces also meets the obligation.
        successor = {'id': 'c2', 'kind': 'child', 'state': 'CLOSED', 'replaces': ['b']}
        self.assertEqual(rp.root_outcome([child('a', 'CLOSED'), child('b', 'DROPPED'), successor]),
                         'CLOSED')
        self.assertEqual(rp.root_outcome([child('a', 'CLOSED'), child('b', 'DROPPED'),
                                          dict(successor, state='OPEN')]), 'OPEN')
        self.assertEqual(rp.root_outcome([child('a', 'CLOSED'), child('b', 'DROPPED'),
                                          dict(successor, state='DROPPED')]), 'FAILED')

    def test_auth_waiting_notifies_at_once_others_after_24_hours(self):
        due = lambda reason, since: rp.waiting_notice_due(
            reason=reason, waiting_since=since, now='2026-11-13T12:00:00Z', policy=self.P)
        self.assertTrue(due('AUTH', '2026-11-13T11:59:59Z'))
        self.assertFalse(due('ENV', '2026-11-12T12:00:01Z'))
        self.assertTrue(due('ENV', '2026-11-12T12:00:00Z'))
        self.assertTrue(due('NO_REVIEWER', 'yesterday'))
        self.assertTrue(due('OTHER', '2026-11-13T11:59:59Z'))

    def test_non_auth_waiting_moves_with_the_configured_hours(self):
        # #2281 row 21 / PR #2427: the Owner notice is timed by
        # waiting_notify_after_hours, so a value other than 24 moves the time.
        fmt = '%Y-%m-%dT%H:%M:%SZ'
        base = datetime.datetime(2026, 11, 13, 0, 0, 0)
        for hours in (1, 7, 48, 1000):
            policy = dict(self.P, waiting_notify_after_hours=hours)
            at = base + datetime.timedelta(hours=hours)
            with self.subTest(hours=hours):
                # One second before the configured hours have passed: not due.
                self.assertFalse(rp.waiting_notice_due(
                    reason='ENV', waiting_since=base.strftime(fmt),
                    now=(at - datetime.timedelta(seconds=1)).strftime(fmt), policy=policy))
                # Exactly the configured hours have passed: due.
                self.assertTrue(rp.waiting_notice_due(
                    reason='ENV', waiting_since=base.strftime(fmt),
                    now=at.strftime(fmt), policy=policy))

    def test_every_non_auth_waiting_reason_follows_the_configured_hours(self):
        # A2: every WAITING reason other than AUTH follows the configured value.
        fmt = '%Y-%m-%dT%H:%M:%SZ'
        base = datetime.datetime(2026, 11, 13, 0, 0, 0)
        hours = 6
        policy = dict(self.P, waiting_notify_after_hours=hours)
        at = base + datetime.timedelta(hours=hours)
        for reason in ('QUOTA', 'ROUTE_UNAVAILABLE', 'NO_REVIEWER', 'DISPUTE', 'ENV'):
            with self.subTest(reason=reason):
                # One second short of the configured hours: not due.
                self.assertFalse(rp.waiting_notice_due(
                    reason=reason, waiting_since=base.strftime(fmt),
                    now=(at - datetime.timedelta(seconds=1)).strftime(fmt), policy=policy))
                # Exactly the configured hours: due.
                self.assertTrue(rp.waiting_notice_due(
                    reason=reason, waiting_since=base.strftime(fmt),
                    now=at.strftime(fmt), policy=policy))

    def test_auth_and_unknown_reason_due_at_once_for_a_large_value(self):
        # A3: AUTH is due at once even for a large configured value, and a
        # reason that is not a known WAITING reason is due at once: at the
        # moment of WAITING entry, now equals waiting_since.
        policy = dict(self.P, waiting_notify_after_hours=1000)
        at_once = '2026-11-13T00:00:00Z'
        self.assertTrue(rp.waiting_notice_due(reason='AUTH', waiting_since=at_once,
                                              now=at_once, policy=policy))
        self.assertTrue(rp.waiting_notice_due(reason='NOT_A_WAITING_REASON',
                                              waiting_since=at_once, now=at_once, policy=policy))

    def test_zero_hours_is_due_at_once_for_a_known_non_auth_reason(self):
        # A4: with the value configured to 0, a known non-AUTH WAITING reason
        # is due at once, at the moment of WAITING entry.
        policy = dict(self.P, waiting_notify_after_hours=0)
        at_once = '2026-11-13T00:00:00Z'
        self.assertTrue(rp.waiting_notice_due(reason='ENV', waiting_since=at_once,
                                              now=at_once, policy=policy))


class LineageBoundTests(unittest.TestCase):
    """#1502 WP-D spec 3-3: the automatic loop of one lineage is bounded."""

    P = GOOD

    def _worst_case(self, policy):
        # Every review FAILs, every Debug confirms a cause, every test repair is used.
        runs = {'review': 0, 'test': 0, 'debug': 0}
        lineage = {'group_debug': 0}

        def task(kind):
            fails, debug_status, own_debug = 0, None, 0
            runs['review'] += 1
            while True:
                fails += 1
                action = rp.review_action(kind=kind, fail_count=fails, debug_status=debug_status,
                                          group_debug_used=lineage['group_debug'], policy=policy)
                if action == 'DEBUG':
                    self.assertIsNone(rp.admit_debug(kind=kind, parent_debug_used=own_debug,
                                                     group_debug_used=lineage['group_debug'],
                                                     policy=policy))
                    runs['debug'] += 1
                    own_debug += 1
                    lineage['group_debug'] += 1
                    debug_status = 'RCA_CONFIRMED'
                    action = rp.review_action(kind=kind, fail_count=fails, debug_status=debug_status,
                                              group_debug_used=lineage['group_debug'], policy=policy)
                if action != 'REPAIR':
                    break
                runs['review'] += 1
            for done in range(policy['test_fail_max'] + 1):
                if rp.test_failure_action(test_fail_count=done, policy=policy) == 'REPAIR':
                    runs['test'] += 1
            return action

        self.assertEqual(task('root'), 'SPLIT')
        for _child in range(3):
            self.assertEqual(task('child'), 'PM_DECIDE')
        self.assertEqual(task('replan'), 'FAILED_FINAL')
        return runs

    def test_lineage_worst_case_is_bounded(self):
        runs = self._worst_case(self.P)
        self.assertEqual(runs['review'], 15)
        self.assertEqual(runs['review'] + runs['test'], 25)
        self.assertEqual(runs['debug'], 0)

    def test_owner_lineage_worst_case_is_bounded(self):
        # ROOT, children and REPLAN share three Debugs.
        runs = self._worst_case(OWNER)
        self.assertEqual(runs['review'], 26)
        self.assertEqual(runs['review'] + runs['test'], 51)
        self.assertEqual(runs['debug'], 3)


class OwnerRepairLimitTests(unittest.TestCase):
    """#2367 (Owner 2026-11-19): Five repairs, with Debug first required after the third."""

    P = OWNER

    def test_fourth_repair_first_requires_debug_and_sixth_escalates(self):
        action = lambda count, status=None: rp.review_action(
            kind='root', fail_count=count, debug_status=status, policy=self.P)
        self.assertEqual(action(1), 'REPAIR')
        self.assertEqual(action(2), 'REPAIR')
        self.assertEqual(action(2, 'RCA_UNCONFIRMED'), 'REPAIR')
        self.assertEqual(action(4), 'DEBUG')
        self.assertEqual(action(4, 'RCA_UNCONFIRMED'), 'DEBUG')
        for count in (2, 3, 4, 5):
            with self.subTest(count=count):
                self.assertEqual(action(count, 'RCA_CONFIRMED'), 'REPAIR')
        self.assertEqual(action(3), 'REPAIR')
        self.assertEqual(action(6, 'RCA_CONFIRMED'), 'SPLIT')
        self.assertEqual(action(rp.UNKNOWN, 'RCA_CONFIRMED'), 'SPLIT')

    def test_admit_repair_requires_rca_for_fourth_and_fifth(self):
        admit = lambda done, rca=False: rp.admit_repair(
            repair_count=done, rca_confirmed=rca, policy=self.P)
        self.assertIsNone(admit(0))
        for done in (1, 2, 3, 4):
            with self.subTest(done=done):
                expected = None if done < 3 else 'REPAIR_DEBUG_REQUIRED'
                self.assertEqual(admit(done), expected)
                self.assertIsNone(admit(done, True))
        self.assertEqual(admit(5, True), 'REPAIR_LIMIT_REACHED')
        self.assertEqual(admit(rp.UNKNOWN, True), 'REPAIR_LIMIT_REACHED')

    def test_test_failures_repair_five_times_then_stop(self):
        action = lambda done: rp.test_failure_action(test_fail_count=done, policy=self.P)
        self.assertEqual([action(done) for done in range(6)],
                         ['REPAIR'] * 5 + ['STOPPED'])
        self.assertEqual(action(rp.UNKNOWN), 'STOPPED')

    def test_split_child_three_gets_its_own_debug_and_repairs_to_five(self):
        # Evidence shape #2302 split child 3: two sibling children used their
        # Debugs; before #2367 child 3 got DEBUG_GROUP_USED before repair 2.
        siblings = 2
        self.assertIsNone(rp.admit_debug(kind='child', parent_debug_used=0,
                                         group_debug_used=siblings, policy=self.P))
        child = lambda count, status=None: rp.review_action(
            kind='child', fail_count=count, debug_status=status,
            group_debug_used=siblings, policy=self.P)
        self.assertEqual(child(2), 'REPAIR')
        self.assertEqual(child(4), 'DEBUG')
        for count in (2, 3, 4, 5):
            with self.subTest(count=count):
                self.assertEqual(child(count, 'RCA_CONFIRMED'), 'REPAIR')
        self.assertEqual(child(6, 'RCA_CONFIRMED'), 'PM_DECIDE')
        # Its one Debug used, or the group full, refuses a second Debug.
        self.assertEqual(rp.admit_debug(kind='child', parent_debug_used=1,
                                        group_debug_used=siblings + 1, policy=self.P),
                         'DEBUG_GROUP_USED')
        self.assertEqual(rp.admit_debug(kind='child', parent_debug_used=0,
                                        group_debug_used=3, policy=self.P),
                         'DEBUG_GROUP_USED')


if __name__ == '__main__':
    unittest.main()
