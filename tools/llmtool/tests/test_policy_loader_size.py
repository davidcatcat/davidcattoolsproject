"""#2281 L0a: the policy.json byte bound of load_policy and its stated reason.

The old fixed 4096-byte cap had no stated technical reason. The new bound
(_MAX_BYTES, with its reason written next to it in repair_policy.py) must be
wide enough for the largest possible valid policy.json once the planned #2281
keys are added, so a schema-valid file above the old cap loads, while a file
beyond the bound still refuses POLICY_INVALID (fail closed, no fallback).
"""

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


class PolicyLoaderSizeTests(unittest.TestCase):
    def _write(self, text):
        handle, path = tempfile.mkstemp(suffix='.json')
        with os.fdopen(handle, 'w') as stream:
            stream.write(text)
        self.addCleanup(os.unlink, path)
        return path

    def test_file_above_the_old_cap_loads_within_the_bound(self):
        # A schema-valid policy.json above the old 4096-byte cap must load:
        # the bound is the one stated next to _MAX_BYTES, not the old cap.
        # JSON allows whitespace between tokens, so the file is padded with
        # it to stand in for the room the planned #2281 keys need.
        head, tail = json.dumps(GOOD).split('"repair_max"', 1)
        text = head + (' ' * 6000) + '"repair_max"' + tail
        self.assertEqual(rp._MAX_BYTES, 16 * 1024)
        self.assertGreater(len(text.encode('utf-8')), 4096)
        self.assertLessEqual(len(text.encode('utf-8')), rp._MAX_BYTES)
        self.assertEqual(rp.load_policy(self._write(text)), GOOD)

    def test_file_of_exactly_the_bound_loads_and_one_byte_more_refuses(self):
        base = json.dumps(GOOD)
        text = base + ' ' * (rp._MAX_BYTES - len(base))
        self.assertEqual(len(text.encode('utf-8')), rp._MAX_BYTES)
        self.assertEqual(rp.load_policy(self._write(text)), GOOD)
        with self.assertRaises(LLMToolError) as caught:
            rp.load_policy(self._write(text + ' '))
        self.assertEqual(str(caught.exception), 'POLICY_INVALID')

    def test_file_above_the_bound_refuses_policy_invalid(self):
        # Fail closed: one byte beyond the bound is POLICY_INVALID, never a
        # partial parse or silent fallback.
        text = json.dumps(GOOD) + ' ' * (rp._MAX_BYTES + 500)
        self.assertGreater(len(text.encode('utf-8')), rp._MAX_BYTES)
        with self.assertRaises(LLMToolError) as caught:
            rp.load_policy(self._write(text))
        self.assertEqual(str(caught.exception), 'POLICY_INVALID')

    def test_repository_policy_is_unchanged(self):
        # #2281 L0b: rows 2, 15 and 16 are now recorded in policy.json, so the
        # pinned contents gain exactly those keys at the quoted values.
        self.assertEqual(rp.load_policy(), dict(GOOD, timeout_continuation_max=1,
                                                spec_preflight_large_file_lines=2000,
                                                merge_train_max_members=6,
                                                merge_train_max_files_per_pr=100,
                                                revert_max_prs=5))
        self.assertEqual(rp.policy_path().name, 'policy.json')


if __name__ == '__main__':
    unittest.main()
