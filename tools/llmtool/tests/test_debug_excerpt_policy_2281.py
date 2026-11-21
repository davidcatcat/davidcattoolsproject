"""#2281 row 19: the AI Debug excerpt size limit is optional in policy.json.

A1: load_policy accepts ai_debug_excerpt_max_chars set to a positive whole
    number, and still accepts the repository policy.json, which omits it.
A2: zero, negative and non-integer values refuse POLICY_INVALID.
A3: when the key is absent, ai_debug_excerpt_max_chars() reports the hard-coded
    start_failure limit.
"""

import json
import os
import tempfile
import unittest

from llmtool import repair_policy as rp
from llmtool import start_failure
from llmtool.errors import LLMToolError

BASE = {'schema_version': 1, 'repair_max': 5, 'parent_debug_max': 1,
        'group_debug_max': 3, 'child_max': 3, 'replan_max': 1,
        'test_fail_max': 5, 'review_fail_max': 6,
        'waiting_notify_after_hours': 24}
KEY = 'ai_debug_excerpt_max_chars'


class DebugExcerptPolicyTest(unittest.TestCase):
    def _path(self, extra):
        data = dict(BASE)
        data.update(extra)
        directory = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, directory)
        self.addCleanup(os.remove, os.path.join(directory, 'policy.json'))
        path = os.path.join(directory, 'policy.json')
        with open(path, 'w', encoding='utf-8') as stream:
            json.dump(data, stream)
        return path

    def test_positive_value_is_accepted(self):
        self.assertEqual(rp.load_policy(self._path({KEY: 6000}))[KEY], 6000)

    def test_repository_policy_omits_the_key(self):
        self.assertNotIn(KEY, rp.load_policy())

    def test_invalid_values_are_refused(self):
        for value in (0, -1, 1.5, '6000', True):
            with self.subTest(value=value):
                with self.assertRaises(LLMToolError):
                    rp.load_policy(self._path({KEY: value}))

    def test_absent_key_reports_start_failure_default(self):
        self.assertEqual(rp.ai_debug_excerpt_max_chars(self._path({})),
                         start_failure.HIGH_DEBUG_EXCERPT_MAX_CHARS)


if __name__ == '__main__':
    unittest.main()
