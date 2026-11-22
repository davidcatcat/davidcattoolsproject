"""#2622 (Owner decisions relayed in the issue): the consecutive route-failure
pause count is a policy.json key with the value 3, not hard-coded in a route.

A1: load_policy accepts the optional key route_failure_pause_after; the
    repository policy.json sets it to 3; when the key is absent the accessor
    reports 3.
A2: zero, negative, non-integer and boolean values refuse POLICY_INVALID,
    fail closed like the other optional keys; nothing falls back silently.
"""

import json
import os
import tempfile
import unittest

from llmtool import repair_policy as rp
from llmtool.errors import LLMToolError

# The required repository limits; the #2622 key is optional.
BASE = {'schema_version': 1, 'repair_max': 5, 'parent_debug_max': 1,
        'group_debug_max': 3, 'child_max': 3, 'replan_max': 1,
        'test_fail_max': 5, 'review_fail_max': 6,
        'waiting_notify_after_hours': 24}
KEY = 'route_failure_pause_after'


class RouteFailurePauseAfterPolicyTests(unittest.TestCase):
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
    def test_key_is_optional_positive(self):
        self.assertIn(KEY, rp.OPTIONAL_POSITIVE_POLICY_KEYS)
        self.assertNotIn(KEY, rp.POLICY_KEYS)
        self.assertNotIn(KEY, rp.OPTIONAL_POLICY_LIMITS)

    def test_repository_policy_sets_the_owner_value(self):
        self.assertEqual(rp.load_policy()[KEY], 3)
        self.assertEqual(rp.ROUTE_FAILURE_PAUSE_AFTER, 3)

    def test_positive_value_is_accepted_and_reported(self):
        for value in (1, 3, 10 ** 9):
            with self.subTest(value=value):
                path = self._write(dict(BASE, **{KEY: value}))
                self.assertEqual(rp.load_policy(path)[KEY], value)
                self.assertEqual(rp.route_failure_pause_after(path), value)

    def test_absent_key_reports_the_default_three(self):
        path = self._write(BASE)
        self.assertEqual(rp.load_policy(path), BASE)
        self.assertNotIn(KEY, rp.load_policy(path))
        self.assertEqual(rp.route_failure_pause_after(path), 3)

    # A2
    def test_invalid_values_refuse_policy_invalid(self):
        for value in (0, -1, -3, '3', True, False, 3.0, 2.5, None, [3],
                      {'value': 3}):
            with self.subTest(value=value):
                self._refuses({**BASE, KEY: value})

    def test_refusal_never_falls_back_to_the_default(self):
        # A refused policy admits no repair either: it is read whole, never
        # partially, and never repaired to the default 3.
        self.assertEqual(
            rp.repair_attempts_max(self._write(dict(BASE, **{KEY: 0}))), 0)


if __name__ == '__main__':
    unittest.main()
