"""#2281 limit 15b: the merge train per-PR file maximum comes from policy.json.

Redo of #2403. The check runs through the real member_check and the real
repair_policy loader, which reads a temporary policy.json; only GitHub and the
TaskStore are fakes (the shared fixtures of test_merge_train_eligibility).
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import test_merge_train_eligibility as base  # noqa: E402
from llmtool import merge_train_eligibility as eligibility  # noqa: E402
from llmtool import repair_policy  # noqa: E402

KEY = 'merge_train_files_per_pr_max'


def _files(count):
    return [{'filename': f'tools/llmtool/llmtool/module_{index:03}.py'}
            for index in range(count)]


class FilesMaxTests(base._Base):
    def setUp(self):
        super().setUp()
        with open(repair_policy.policy_path(), encoding='utf-8') as stream:
            self.policy = json.load(stream)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.policy_file = Path(directory.name) / 'policy.json'
        patcher = mock.patch.object(repair_policy, 'policy_path',
                                    return_value=self.policy_file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_with(self, value, count, absent=False):
        policy = dict(self.policy)
        if absent:
            policy.pop(KEY, None)
        else:
            policy[KEY] = value
        self.policy_file.write_text(json.dumps(policy), encoding='utf-8')
        return self.check(routes=base._routes(**{base.FILES: _files(count)}))

    def test_configured_maximum_keeps_greater_or_equal(self):
        # Codex v3 F1 on #2403: a PR with exactly the maximum is refused.
        # A full 100-entry page is also refused earlier by the merge-check file
        # read, so only the refusal itself is asserted here.
        self.assertFalse(self.run_with(100, 100)['eligible'])
        result = self.run_with(100, 99)
        self.assertTrue(result['eligible'], result)

    def test_configured_lower_maximum_is_enforced(self):
        self.assertRefused(self.run_with(5, 5), eligibility.TRAIN_MEMBER_PR_UNAVAILABLE)
        self.assertRefused(self.run_with(5, 6), eligibility.TRAIN_MEMBER_PR_UNAVAILABLE)
        result = self.run_with(5, 4)
        self.assertTrue(result['eligible'], result)

    def test_absent_key_keeps_previous_default(self):
        self.assertFalse(self.run_with(None, 100, absent=True)['eligible'])
        result = self.run_with(None, 99, absent=True)
        self.assertTrue(result['eligible'], result)

    def test_invalid_value_refuses_before_any_read(self):
        for value in (0, -1, '100', True, 1.5, 101):
            with self.subTest(value=value):
                result = self.run_with(value, 1)
                self.assertRefused(result, eligibility.POLICY_INVALID)
                self.assertEqual(self.github.calls, [])
                self.assertEqual(self.store.writes, [])

    def test_unreadable_policy_refuses(self):
        self.policy_file.write_text('{', encoding='utf-8')
        result = self.check()
        self.assertRefused(result, eligibility.POLICY_INVALID)
        self.assertEqual(self.store.writes, [])


if __name__ == '__main__':
    unittest.main()
