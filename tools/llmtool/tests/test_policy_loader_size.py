"""#2281 L0a: policy.json size bound -- a file within the stated bound loads,
a file above it is refused POLICY_INVALID (fail closed), and the default
policy keeps loading unchanged."""

import json
import os
import tempfile
import unittest

from llmtool import repair_policy as rp
from llmtool.errors import LLMToolError

# The pre-#2281 L0a fixed cap, which had no stated technical reason.
OLD_MAX_BYTES = 4096
GOOD = {'schema_version': 1, 'repair_max': 2, 'parent_debug_max': 1,
        'group_debug_max': 1, 'child_max': 3, 'replan_max': 1,
        'test_fail_max': 2, 'review_fail_max': 3,
        'waiting_notify_after_hours': 24, 'timeout_continuation_max': 1}


class PolicyLoaderSizeTests(unittest.TestCase):
    def _write(self, value):
        handle, path = tempfile.mkstemp(suffix='.json')
        with os.fdopen(handle, 'w') as stream:
            stream.write(value)
        self.addCleanup(os.unlink, path)
        return path

    def _padded(self, length):
        # A valid policy JSON padded with whitespace to an exact byte length.
        body = json.dumps(GOOD)
        self.assertLessEqual(len(body), length)
        return body + ' ' * (length - len(body))

    def test_bound_was_raised_above_the_old_cap(self):
        self.assertGreater(rp._MAX_BYTES, OLD_MAX_BYTES)

    def test_file_above_the_old_cap_loads_within_the_bound(self):
        for length in (8192, rp._MAX_BYTES):
            with self.subTest(length=length):
                self.assertGreater(length, OLD_MAX_BYTES)
                self.assertEqual(rp.load_policy(self._padded(length)), GOOD)

    def test_file_above_the_bound_refuses_policy_invalid(self):
        # Fail closed: even a valid JSON is refused once it exceeds the bound.
        for length in (rp._MAX_BYTES + 1, rp._MAX_BYTES + 1024):
            with self.subTest(length=length):
                with self.assertRaises(LLMToolError) as caught:
                    rp.load_policy(self._padded(length))
                self.assertEqual(str(caught.exception), 'POLICY_INVALID')

    def test_default_policy_is_unchanged(self):
        self.assertEqual(rp.load_policy(), GOOD)
        self.assertEqual(rp.policy_path().name, 'policy.json')


if __name__ == '__main__':
    unittest.main()