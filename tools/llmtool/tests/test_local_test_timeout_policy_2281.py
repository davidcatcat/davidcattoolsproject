"""#2281 row 10: the task complete local test timeout from policy.

R1: local_test_timeout_seconds is an optional positive policy key with no
    upper bound; an absent key gives the caller default 600, and a present key
    of the wrong type, zero or negative refuses POLICY_INVALID.
R2: the task complete local test run uses the policy value per module and
    keeps the whole-run bound at three times it; without the key the existing
    module defaults stay 600 s and 1800 s. An invalid policy refuses before any
    test starts.
R3: patching the task_completion module defaults still works when the key is
    absent.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from llmtool import repair_policy as rp
from llmtool import task_completion
from llmtool.errors import LLMToolError

# The required policy keys, unchanged by this optional row 10 key.
BASE = {'schema_version': 1, 'repair_max': 5, 'parent_debug_max': 1,
        'group_debug_max': 3, 'child_max': 3, 'replan_max': 1,
        'test_fail_max': 5, 'review_fail_max': 6,
        'waiting_notify_after_hours': 24}
KEY = 'local_test_timeout_seconds'


class LocalTestTimeoutPolicyTests(unittest.TestCase):
    def _write(self, value):
        handle, path = tempfile.mkstemp(suffix='.json')
        with os.fdopen(handle, 'w') as stream:
            stream.write(value if type(value) is str else json.dumps(value))
        self.addCleanup(os.unlink, path)
        return path

    # R1
    def test_absent_key_returns_the_caller_default_600(self):
        path = self._write(BASE)
        self.assertEqual(rp.LOCAL_TEST_TIMEOUT_SECONDS, 600)
        self.assertEqual(rp.local_test_timeout_seconds(path),
                         rp.LOCAL_TEST_TIMEOUT_SECONDS)
        self.assertEqual(rp.local_test_timeout_seconds(path, default=41), 41)

    def test_present_key_returns_its_value_with_no_upper_bound(self):
        for value in (1, 7, 10 ** 9):
            with self.subTest(value=value):
                path = self._write(dict(BASE, **{KEY: value}))
                self.assertEqual(rp.local_test_timeout_seconds(path), value)

    def test_invalid_present_value_refuses_policy_invalid(self):
        for value in (0, -1, -2000, '5', True, False, 2.5, None, [5], {'value': 5}):
            with self.subTest(value=value):
                with self.assertRaises(LLMToolError) as caught:
                    rp.load_policy(self._write({**BASE, KEY: value}))
                self.assertEqual(str(caught.exception), 'POLICY_INVALID')

    # R2 and R3
    def test_module_defaults_stay_600_and_1800(self):
        self.assertEqual(task_completion._TEST_TIMEOUT,
                         rp.LOCAL_TEST_TIMEOUT_SECONDS)
        self.assertEqual(task_completion._TEST_TOTAL_TIMEOUT,
                         3 * rp.LOCAL_TEST_TIMEOUT_SECONDS)

    def test_absent_key_keeps_600_and_1800(self):
        with patch.object(rp, 'policy_path', return_value=self._write(BASE)):
            self.assertEqual(task_completion._local_test_timeouts(), (600, 1800))

    def test_present_key_sets_per_module_and_three_times_total(self):
        for value in (1, 7, 600):
            with self.subTest(value=value):
                path = self._write(dict(BASE, **{KEY: value}))
                with patch.object(rp, 'policy_path', return_value=path):
                    self.assertEqual(task_completion._local_test_timeouts(),
                                     (value, 3 * value))

    # R3
    def test_absent_key_keeps_patchable_module_defaults(self):
        with patch.object(rp, 'policy_path', return_value=self._write(BASE)), \
                patch.object(task_completion, '_TEST_TIMEOUT', 4), \
                patch.object(task_completion, '_TEST_TOTAL_TIMEOUT', 9):
            self.assertEqual(task_completion._local_test_timeouts(), (4, 9))

    def test_invalid_policy_refuses_before_any_test_starts(self):
        for value in (0, -1, '5', True, 2.5, None):
            with self.subTest(value=value):
                with tempfile.TemporaryDirectory() as root, \
                        tempfile.TemporaryDirectory() as home:
                    with open(os.path.join(root, 'quiet.py'), 'w',
                              encoding='utf-8') as handle:
                        handle.write('pass\n')
                    path = self._write({**BASE, KEY: value})
                    with patch.object(rp, 'policy_path', return_value=path), \
                            patch.object(task_completion.subprocess, 'Popen',
                                         side_effect=AssertionError('test started')) as popen:
                        with self.assertRaises(LLMToolError) as caught:
                            task_completion._run_registered_tests(
                                root, 'a' * 40, [('quiet.py', 'direct')], home=home)
                        self.assertEqual(str(caught.exception), 'POLICY_INVALID')
                        popen.assert_not_called()


if __name__ == '__main__':
    unittest.main()
