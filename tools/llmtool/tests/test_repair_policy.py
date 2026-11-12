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


if __name__ == '__main__':
    unittest.main()
