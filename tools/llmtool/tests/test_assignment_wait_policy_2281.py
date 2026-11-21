"""#2281 row 18 (basis #819): the inbox default Developer assignment wait.

The row 18 policy key ``developer_assignment_wait_seconds`` was merged in
PR #2478, but the Developer assignment inbox still kept its own hard-coded
wait; a wait that uses the default now follows the policy key:
A1: with the repository tools/llmtool/policy.json (key absent) the inbox's
    default assignment wait is unchanged: a wait without an explicit
    ``timeout`` polls to ``DEFAULT_WAIT_SECONDS`` (540), and the loader
    default equals it.
A2: a policy that sets the key to another allowed value: a wait that uses
    the default stops at the configured seconds, not at 540.
A3: a configured value above the inbox's allowed wait maximum, or an
    invalid policy, ends in a clear refusal (ASSIGNMENT_WAIT_TIMEOUT_INVALID
    or POLICY_INVALID); nothing falls back silently to the old hard-coded
    value.
A4: a caller that passes an explicit wait keeps that value (even when the
    policy is unreadable); an explicit wait above the maximum is still
    refused.
The policy is given in-process through a temporary policy file; nothing
here touches the network.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from llmtool import repair_policy as rp
from llmtool.developer_assignment_inbox import (AssignmentRefused,
                                                DEFAULT_WAIT_SECONDS,
                                                MAX_WAIT_SECONDS,
                                                wait_assignments)
from llmtool.errors import LLMToolError

# The repository limits before #2281 L0b (#2367 values); the row 18 key is
# optional and omitted, exactly as in the repository policy.json.
BASE = {'schema_version': 1, 'repair_max': 5, 'parent_debug_max': 1,
        'group_debug_max': 3, 'child_max': 3, 'replan_max': 1,
        'test_fail_max': 5, 'review_fail_max': 6,
        'waiting_notify_after_hours': 24}
# #2281 row 18 (basis #819): Developer assignment wait in seconds.
ROW18_KEY = 'developer_assignment_wait_seconds'
# The repository policy.json (A1), read in-process; no network.
REPO_POLICY = Path(__file__).resolve().parents[1] / 'policy.json'
_NOW = datetime(2024, 5, 1, tzinfo=timezone.utc)


class _EmptyStore:
    """No assignments, so a waiting call runs to the deadline."""

    def get_developer_assignments(self):
        return []


class _FakeClock:
    """A deterministic monotonic clock whose sleep advances the time."""

    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def sleep(self, delta):
        self.value += delta


class AssignmentWaitPolicyTests(unittest.TestCase):
    def _write(self, value):
        handle, path = tempfile.mkstemp(suffix='.json')
        with os.fdopen(handle, 'w') as stream:
            stream.write(value if type(value) is str else json.dumps(value))
        self.addCleanup(os.unlink, path)
        return path

    def _wait_to_deadline(self, policy_path, timeout=None):
        """Run a waiting call with no pending work; the clock stops at the bound."""
        clock = _FakeClock()
        result = wait_assignments(
            _EmptyStore(), 'w1', wait=True, timeout=timeout,
            policy_path=policy_path, clock=clock, sleep=clock.sleep, now=_NOW)
        self.assertTrue(result['timed_out'])
        return clock.value

    # A1
    def test_absent_key_keeps_the_inbox_default_wait(self):
        # The repository policy.json omits the row 18 key...
        with open(REPO_POLICY, 'rb') as stream:
            self.assertNotIn(ROW18_KEY,
                             json.loads(stream.read().decode('utf-8')))
        # ...so the loader default is the unchanged hard-coded wait, and a
        # wait that uses the default polls exactly to DEFAULT_WAIT_SECONDS.
        self.assertEqual(rp.DEVELOPER_ASSIGNMENT_WAIT_SECONDS, DEFAULT_WAIT_SECONDS)
        self.assertEqual(rp.assignment_wait_seconds(REPO_POLICY),
                         DEFAULT_WAIT_SECONDS)
        self.assertEqual(self._wait_to_deadline(self._write(BASE)),
                         DEFAULT_WAIT_SECONDS)

    # A2
    def test_configured_value_moves_the_default_deadline(self):
        for value in (1, 300, MAX_WAIT_SECONDS):
            self.assertEqual(self._wait_to_deadline(self._write(dict(BASE,
                                            **{ROW18_KEY: value}))), value)
        # 300, not the old 540: the default followed the policy.
        self.assertNotEqual(self._wait_to_deadline(self._write(dict(BASE,
                                            **{ROW18_KEY: 300}))),
                            DEFAULT_WAIT_SECONDS)

    # A3
    def test_value_above_the_maximum_refuses(self):
        path = self._write(dict(BASE, **{ROW18_KEY: MAX_WAIT_SECONDS + 1}))
        with self.assertRaises(AssignmentRefused) as caught:
            self._wait_to_deadline(path)
        self.assertEqual(caught.exception.assignment_code,
                         'ASSIGNMENT_WAIT_TIMEOUT_INVALID')

    def test_invalid_policy_refuses(self):
        # A configured value that is not a positive whole number...
        bad = dict(BASE, **{ROW18_KEY: 'soon'})
        with self.assertRaises(LLMToolError) as caught:
            self._wait_to_deadline(self._write(bad))
        self.assertEqual(str(caught.exception), 'POLICY_INVALID')
        # ...as is a policy that misses a required key; neither falls back
        # to the old hard-coded wait.
        missing = dict(BASE)
        del missing['review_fail_max']
        with self.assertRaises(LLMToolError) as caught:
            self._wait_to_deadline(self._write(dict(missing, **{ROW18_KEY: 300})))
        self.assertEqual(str(caught.exception), 'POLICY_INVALID')

    # A4
    def test_explicit_wait_keeps_its_value(self):
        path = self._write(dict(BASE, **{ROW18_KEY: 300}))
        self.assertEqual(self._wait_to_deadline(path, timeout=120), 120)
        # Even with an unreadable policy: the explicit wait is the caller's.
        self.assertEqual(self._wait_to_deadline(self._write('not a policy'),
                                                timeout=120), 120)
        # An explicit wait above the inbox maximum is still refused.
        with self.assertRaises(AssignmentRefused) as caught:
            self._wait_to_deadline(path, timeout=MAX_WAIT_SECONDS + 1)
        self.assertEqual(caught.exception.assignment_code,
                         'ASSIGNMENT_WAIT_TIMEOUT_INVALID')


if __name__ == '__main__':
    unittest.main()