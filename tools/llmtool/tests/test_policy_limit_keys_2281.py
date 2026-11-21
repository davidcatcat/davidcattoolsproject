"""#2281 L0b: policy.json keys for limit-table rows 2, 15, 16 and 18.

A1: the repository policy.json has one key per limit at the value recorded in
    the #2281 limit table, and load_policy accepts them. Row 18 is omitted
    from the repository policy.json; its key is optional, and load_policy
    accepts a policy that sets it to a positive whole number.
A2: a present key of the wrong type, zero or negative refuses POLICY_INVALID;
    nothing falls back silently.
A3: an absent key keeps the caller default; the row 18 default (the Developer
    assignment wait) equals
    developer_assignment_inbox.DEFAULT_WAIT_SECONDS at its point of use;
    existing keys and repair_policy behaviour are unchanged.
"""

import json
import os
import tempfile
import unittest

from llmtool import repair_policy as rp
from llmtool.errors import LLMToolError

# The repository limits before #2281 L0b (#2367 values).
BASE = {'schema_version': 1, 'repair_max': 5, 'parent_debug_max': 1,
        'group_debug_max': 3, 'child_max': 3, 'replan_max': 1,
        'test_fail_max': 5, 'review_fail_max': 6,
        'waiting_notify_after_hours': 24}
# #2281 limit table: row 2 (2000 lines), row 15 (6 members, 100 files per PR),
# row 16 (5 PRs). Config home policy.json for all three.
LIMITS = {'spec_preflight_large_file_lines': 2000, 'merge_train_members_max': 6,
          'merge_train_files_per_pr_max': 100, 'revert_scope_prs_max': 5}
# #2281 L7: row 17 (D6 writer lease, 24 hours) joins the same optional positive
# keys; its own behaviour is pinned in test_policy_d6_lease_2281.
LATER_LIMITS = {'d6_writer_lease_hours': 24}
# #2281 row 18 (basis #819): Developer assignment wait in seconds.
ROW18_KEY = 'developer_assignment_wait_seconds'


class PolicyLimitKeysTests(unittest.TestCase):
    def _write(self, value):
        handle, path = tempfile.mkstemp(suffix='.json')
        with os.fdopen(handle, 'w') as stream:
            stream.write(value if type(value) is str else json.dumps(value))
        self.addCleanup(os.unlink, path)
        return path

    def _refuses(self, value):
        with self.assertRaises(LLMToolError) as caught:
            rp.load_policy(self._write(value))
        self.assertEqual(str(caught.exception), 'POLICY_INVALID')

    # A1
    def test_keys_are_the_optional_positive_keys(self):
        self.assertEqual(set(rp.OPTIONAL_POSITIVE_POLICY_KEYS),
                         {*LIMITS, *LATER_LIMITS, ROW18_KEY})
        # Optional: none of them is required by the loader.
        self.assertFalse(set(LIMITS) & set(rp.POLICY_KEYS))
        self.assertFalse(set(LIMITS) & set(rp.OPTIONAL_POLICY_LIMITS))
        self.assertNotIn(ROW18_KEY, rp.POLICY_KEYS)
        self.assertNotIn(ROW18_KEY, rp.OPTIONAL_POLICY_LIMITS)

    def test_repository_policy_has_each_limit_at_its_table_value(self):
        policy = rp.load_policy()
        for key, value in LIMITS.items():
            with self.subTest(key=key):
                self.assertIs(type(policy[key]), int)
                self.assertEqual(policy[key], value)
        # Row 18 is not in the repository policy.json; the repository file is
        # still accepted because the key is optional.
        self.assertNotIn(ROW18_KEY, policy)

    def test_loader_accepts_the_keys(self):
        self.assertEqual(rp.load_policy(self._write(dict(BASE, **LIMITS))), dict(BASE, **LIMITS))
        for key in LIMITS:
            with self.subTest(key=key):
                self.assertEqual(rp.load_policy(self._write(dict(BASE, **{key: 1})))[key], 1)
        # Row 18 is optional: a policy setting it to a positive whole number is
        # accepted, as is one omitting it.
        self.assertEqual(
            rp.load_policy(self._write(dict(BASE, **{ROW18_KEY: 540}))),
            dict(BASE, **{ROW18_KEY: 540}))
        self.assertEqual(rp.load_policy(self._write(dict(BASE, **{ROW18_KEY: 1}))),
                         dict(BASE, **{ROW18_KEY: 1}))

    def test_no_upper_validation_bound(self):
        for key in (*LIMITS, ROW18_KEY):
            for value in (1001, 10 ** 9):
                with self.subTest(key=key, value=value):
                    self.assertEqual(
                        rp.load_policy(self._write(dict(BASE, **{key: value})))[key], value)

    # A2
    def test_invalid_value_refuses_policy_invalid(self):
        for key in (*LIMITS, ROW18_KEY):
            for value in (0, -1, -2000, '5', True, False, 2.5, None, [5], {'value': 5}):
                with self.subTest(key=key, value=value):
                    self._refuses({**BASE, **LIMITS, key: value})

    def test_invalid_value_gives_no_fallback_through_repair_attempts_max(self):
        # A policy refused for a bad new key admits no repair (fail closed),
        # exactly like any other invalid policy; it is never read partially.
        path = self._write(dict(BASE, merge_train_members_max=0))
        self.assertEqual(rp.repair_attempts_max(path), 0)

    # A3
    def test_absent_keys_keep_the_caller_default(self):
        policy = rp.load_policy(self._write(BASE))
        self.assertEqual(policy, BASE)
        for key in LIMITS:
            with self.subTest(key=key):
                self.assertNotIn(key, policy)
                self.assertEqual(policy.get(key, 'caller-default'), 'caller-default')
        # One key present, the others absent.
        partial = rp.load_policy(self._write(dict(BASE, revert_scope_prs_max=7)))
        self.assertEqual(partial['revert_scope_prs_max'], 7)
        self.assertNotIn('merge_train_members_max', partial)

    def test_absent_row18_key_keeps_the_current_hard_coded_wait(self):
        # The row 18 default equals the Developer assignment wait at its
        # point of use, so callers wired later see the unchanged value.
        from llmtool.developer_assignment_inbox import DEFAULT_WAIT_SECONDS
        self.assertEqual(rp.DEVELOPER_ASSIGNMENT_WAIT_SECONDS, DEFAULT_WAIT_SECONDS)
        self.assertEqual(rp.assignment_wait_seconds(self._write(BASE)),
                         DEFAULT_WAIT_SECONDS)
        # The repository policy.json omits the key: the reported value is the
        # caller default.
        self.assertEqual(rp.assignment_wait_seconds(), DEFAULT_WAIT_SECONDS)
        # A policy setting the key wins over the default.
        self.assertEqual(
            rp.assignment_wait_seconds(self._write(dict(BASE, **{ROW18_KEY: 600}))),
            600)

    def test_existing_keys_and_behaviour_are_unchanged(self):
        self.assertEqual(rp.POLICY_KEYS, (
            'repair_max', 'parent_debug_max', 'group_debug_max', 'child_max',
            'replan_max', 'test_fail_max', 'review_fail_max', 'waiting_notify_after_hours'))
        self.assertEqual(rp.OPTIONAL_POLICY_LIMITS, {'timeout_continuation_max': (0, 3)})
        policy = rp.load_policy()
        self.assertEqual({key: policy[key] for key in BASE}, dict(BASE, parent_debug_max=3))
        self.assertEqual(policy['timeout_continuation_max'], 1)
        self.assertEqual(set(policy), {*BASE, 'timeout_continuation_max', *LIMITS,
                                       *LATER_LIMITS})
        for key, value in LATER_LIMITS.items():
            with self.subTest(key=key):
                self.assertEqual(policy[key], value)
        self.assertEqual(rp.repair_attempts_max(), 5)
        # The existing refusals still hold with the new keys present.
        for bad in (dict(BASE, repair_max=-1), dict(BASE, timeout_continuation_max=4),
                    dict(BASE, extra=1), dict(BASE, schema_version=2),
                    {key: value for key, value in BASE.items() if key != 'repair_max'}):
            with self.subTest(bad=str(bad)[:60]):
                self._refuses(dict(bad, **LIMITS))
        # The decision functions read only their own keys.
        with_limits = dict(BASE, **LIMITS)
        for policy in (BASE, with_limits):
            with self.subTest(limits=policy is with_limits):
                self.assertEqual(rp.review_action(kind='root', fail_count=1, policy=policy),
                                 'REPAIR')
                self.assertEqual(rp.admit_repair(repair_count=5, rca_confirmed=True,
                                                 policy=policy), 'REPAIR_LIMIT_REACHED')
                self.assertEqual(rp.test_failure_action(test_fail_count=4, policy=policy),
                                 'REPAIR')
                self.assertEqual(rp.admit_split(kind='root', child_count=0, new_children=3,
                                                policy=policy), None)


if __name__ == '__main__':
    unittest.main()
