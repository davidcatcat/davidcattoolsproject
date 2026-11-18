"""#2281 L0b: the table limits without a stated basis become configurable.

Row 2 (the spec_preflight large-file line threshold for gw-agent edits) is
2000 lines, row 15 (the merge-train members and files per PR) is 6 members
and 100 files per PR and row 16 (the revert scope) is 5 PRs; the config home
for all three is policy.json. The loader accepts each key at the quoted
value and refuses any other type, zero or negative value with
POLICY_INVALID (no silent fallback); a missing key keeps the caller
default. No caller is wired in this piece.
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
VALUES = {'spec_preflight_large_file_lines': 2000,
          'merge_train_max_members': 6,
          'merge_train_max_files_per_pr': 100,
          'revert_max_prs': 5}


class PolicyLimitKeysTests(unittest.TestCase):
    def _write(self, value):
        handle, path = tempfile.mkstemp(suffix='.json')
        with os.fdopen(handle, 'w') as stream:
            stream.write(value if type(value) is str else json.dumps(value))
        self.addCleanup(os.unlink, path)
        return path

    def test_repository_policy_has_the_quoted_values(self):
        # A1: one key per limit at the value quoted in the #2281 table, in
        # policy.json, and the loader accepts them.
        policy = rp.load_policy()
        for key, value in VALUES.items():
            self.assertEqual(policy[key], value)
        self.assertEqual(policy, dict(GOOD, timeout_continuation_max=1, **VALUES))

    def test_each_key_at_its_quoted_value_loads(self):
        for key, value in VALUES.items():
            with self.subTest(key=key):
                self.assertEqual(rp.load_policy(self._write(dict(GOOD, **{key: value}))),
                                 dict(GOOD, **{key: value}))

    def test_invalid_values_refuse_policy_invalid(self):
        # A2: wrong type, zero and negative are all refused with the existing
        # family code; there is no silent fallback to a default.
        for key in VALUES:
            with self.subTest(key=key):
                for value in (0, -1, True, str(VALUES[key]), VALUES[key] + 0.0,
                              [VALUES[key]]):
                    with self.subTest(value=value):
                        with self.assertRaises(LLMToolError) as caught:
                            rp.load_policy(self._write(dict(GOOD, **{key: value})))
                        self.assertEqual(str(caught.exception), 'POLICY_INVALID')

    def test_values_above_the_quoted_value_load(self):
        # #2281 design rule 3: no arbitrary upper ceiling. An authorized
        # increase above the quoted table value is accepted.
        for key, value in VALUES.items():
            with self.subTest(key=key):
                higher = value + 100
                self.assertEqual(rp.load_policy(self._write(dict(GOOD, **{key: higher}))),
                                 dict(GOOD, **{key: higher}))

    def test_missing_key_keeps_the_caller_default(self):
        # A3: none of the new keys is required, so a policy file without them
        # loads unchanged and every existing key behaves as before; the loader
        # injects nothing for an absent key, so the caller keeps its default.
        # A file with the keys loads them exactly as recorded.
        self.assertEqual(rp.load_policy(self._write(GOOD)), GOOD)
        self.assertEqual(rp.load_policy(self._write(dict(GOOD, **VALUES))),
                         dict(GOOD, **VALUES))

    def test_existing_keys_and_unknown_keys_are_unchanged(self):
        for value in (True, -1, '1', 1001):
            with self.assertRaises(LLMToolError) as caught:
                rp.load_policy(self._write(dict(GOOD, repair_max=value)))
            self.assertEqual(str(caught.exception), 'POLICY_INVALID')
        for value in (dict(GOOD, **VALUES, extra=1), dict(GOOD, timeout_continuation_max=4)):
            with self.assertRaises(LLMToolError) as caught:
                rp.load_policy(self._write(value))
            self.assertEqual(str(caught.exception), 'POLICY_INVALID')


if __name__ == '__main__':
    unittest.main()
