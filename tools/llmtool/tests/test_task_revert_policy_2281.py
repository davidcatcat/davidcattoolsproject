"""#2281 row 16: the revert scope limit comes from policy.json. No network.

A1: revert_scope_prs_max drives the limit through the existing policy loader;
    at its current value the previous behaviour is unchanged.
A2: an absent key keeps the previous default and an invalid policy is refused
    POLICY_INVALID, with no silent fallback.
A3: a revert over the configured maximum is still REVERT_SCOPE_TOO_LARGE.
Order: the limit is pinned against its neighbours (admission docstring
    steps 6 and 7): a well-formed scope over the configured maximum is
    REVERT_SCOPE_TOO_LARGE before a non-dict or unknown-code dependency
    result is read; an unsorted or repeating scope of three PRs is
    REVERT_INCIDENT_INVALID, not REVERT_SCOPE_TOO_LARGE; and a scope equal
    to the configured maximum passes the limit, so a non-dict dependency
    result is REVERT_DEPENDENCY_UNKNOWN.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import main_incident as mi  # noqa: E402
from llmtool import main_incident_store as store  # noqa: E402
from llmtool import repair_policy as rp  # noqa: E402
from llmtool import task_revert as tr  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402

# The repository limits other than the #2281 optional keys (#2367 values).
BASE = {'schema_version': 1, 'repair_max': 5, 'parent_debug_max': 1,
        'group_debug_max': 3, 'child_max': 3, 'replan_max': 1,
        'test_fail_max': 5, 'review_fail_max': 6,
        'waiting_notify_after_hours': 24}

PASS_SHA = 'a' * 40
FAIL_SHA = 'c' * 40
OWNER = 'pm-owner-session'
STAMP = '2026-11-13T00:00:00Z'


def observation(pr):
    rows = [{'pr': pr, 'merge_sha': FAIL_SHA}]
    return {
        'observed_at': STAMP, 'signal': [mi.LINUX], 'failing_head': FAIL_SHA,
        'failing_runs': [{'kind': mi.LINUX, 'id': 101, 'conclusion': 'failure'}],
        'class': mi.CODE,
        'evidence': [{'code': 'TEST_FAILURE', 'tests': ['tests.test_mod.Case.test_x']}],
        'range': [{'sha': FAIL_SHA, 'pr': pr}], 'candidate_rows': rows,
        'attribution': mi.attribute(rows), 'closure_available': False,
    }


class RevertScopePolicyTests(unittest.TestCase):
    def _policy_file(self, **overrides):
        handle, path = tempfile.mkstemp(suffix='.json')
        with os.fdopen(handle, 'w') as stream:
            stream.write(json.dumps(dict(BASE, **overrides)))
        self.addCleanup(os.unlink, path)
        return path

    def _record(self, scope):
        record = store.new_record(pm_session_id=OWNER, observation=observation(scope[0]),
                                  last_good_head=PASS_SHA, created_at=STAMP)
        record['confirmations'].append({
            'pm_session_id': OWNER, 'confirmed_at': STAMP, 'scope': list(scope),
            'reason': 'revert the failing merges', 'observation_index': 0,
            'evidence_sha256': store.observation_digest(record['observations'][0]),
        })
        return record

    def _admit(self, scope, path=None):
        return tr.admit(self._record(scope), {'code': mi.NO_DEPENDENT}, path)

    # A1
    def test_repository_value_keeps_the_previous_behaviour(self):
        self.assertEqual(rp.load_policy()['revert_scope_prs_max'], 5)
        self.assertEqual(tr.scope_prs_max(), 5)
        self.assertEqual(self._admit((12, 13, 14, 15, 16))['code'], tr.ADMITTED)
        self.assertEqual(self._admit((12, 13, 14, 15, 16, 17))['code'],
                         tr.REVERT_SCOPE_TOO_LARGE)

    def test_configured_limit_is_the_maximum(self):
        path = self._policy_file(revert_scope_prs_max=2)
        self.assertEqual(tr.scope_prs_max(path), 2)
        self.assertEqual(self._admit((12, 13), path)['code'], tr.ADMITTED)
        self.assertEqual(self._admit((12, 13, 14), path)['code'],
                         tr.REVERT_SCOPE_TOO_LARGE)

    def test_larger_configured_limit_admits_a_larger_scope(self):
        path = self._policy_file(revert_scope_prs_max=9)
        result = self._admit((12, 13, 14, 15, 16, 17), path)
        self.assertEqual(result['code'], tr.ADMITTED)
        self.assertFalse(result['refused'])

    # A2
    def test_absent_key_keeps_the_previous_default(self):
        path = self._policy_file()
        self.assertNotIn('revert_scope_prs_max', rp.load_policy(path))
        self.assertEqual(tr.MAX_SCOPE_PRS, 5)
        self.assertEqual(tr.scope_prs_max(path), tr.MAX_SCOPE_PRS)
        self.assertEqual(self._admit((12, 13, 14, 15, 16), path)['code'], tr.ADMITTED)
        self.assertEqual(self._admit((12, 13, 14, 15, 16, 17), path)['code'],
                         tr.REVERT_SCOPE_TOO_LARGE)

    def test_invalid_value_is_refused_fail_closed(self):
        for bad in (0, -1, '5', True, False, 2.5, None, [5], {'value': 5}):
            path = self._policy_file(revert_scope_prs_max=bad)
            with self.subTest(bad=bad):
                with self.assertRaises(LLMToolError) as caught:
                    tr.scope_prs_max(path)
                self.assertEqual(str(caught.exception), 'POLICY_INVALID')
                with self.assertRaises(LLMToolError) as caught:
                    self._admit((12,), path)
                self.assertEqual(str(caught.exception), 'POLICY_INVALID')

    def test_unreadable_policy_is_refused_fail_closed(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, directory)
        with self.assertRaises(LLMToolError) as caught:
            tr.scope_prs_max(os.path.join(directory, 'missing.json'))
        self.assertEqual(str(caught.exception), 'POLICY_INVALID')

    # A3
    def test_over_the_configured_maximum_is_refused_as_before(self):
        path = self._policy_file(revert_scope_prs_max=3)
        self.assertEqual(self._admit((12, 13, 14, 15), path),
                         {'code': tr.REVERT_SCOPE_TOO_LARGE, 'refused': True})

    # Order (admission docstring steps 6 and 7): the scope shape check,
    # then the policy.json limit, then the dependency result.
    def test_over_the_maximum_is_refused_before_the_dependency_is_read(self):
        path = self._policy_file(revert_scope_prs_max=2)
        record = self._record((12, 13, 14))
        self.assertEqual(tr.admit(record, None, path),
                         {'code': tr.REVERT_SCOPE_TOO_LARGE, 'refused': True})
        self.assertEqual(
            tr.admit(record, {'code': 'UNKNOWN_DEPENDENCY_CODE'}, path),
            {'code': tr.REVERT_SCOPE_TOO_LARGE, 'refused': True})

    def test_unsorted_or_repeating_scope_is_invalid_not_too_large(self):
        path = self._policy_file(revert_scope_prs_max=2)
        self.assertEqual(self._admit((14, 12, 13), path),
                         {'code': tr.REVERT_INCIDENT_INVALID, 'refused': True})
        self.assertEqual(self._admit((12, 13, 12), path),
                         {'code': tr.REVERT_INCIDENT_INVALID, 'refused': True})

    def test_scope_equal_to_the_maximum_reaches_the_dependency_check(self):
        path = self._policy_file(revert_scope_prs_max=2)
        record = self._record((12, 13))
        self.assertEqual(tr.admit(record, None, path),
                         {'code': tr.REVERT_DEPENDENCY_UNKNOWN, 'refused': True})


if __name__ == '__main__':
    unittest.main()
