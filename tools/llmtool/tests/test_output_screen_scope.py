"""Sensitive output screening applies to added diff content only."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool.task_submission_cli import _strict_developer_output_rejection


def _screened_looking_value():
    return 'AK' + 'IA' + ('A' * 16)


def _tracked_diff(old, new):
    return (
        'diff --git a/example.py b/example.py\n'
        '--- a/example.py\n'
        '+++ b/example.py\n'
        '@@ -1 +1 @@\n'
        '-' + old + '\n'
        '+' + new + '\n'
    )


class AddedLineScreenScopeTests(unittest.TestCase):
    def test_match_on_removed_line_is_allowed(self):
        rejection = _strict_developer_output_rejection(
            _tracked_diff(_screened_looking_value(), 'safe'), ['example.py'])
        self.assertIsNone(rejection)

    def test_match_on_added_line_is_rejected(self):
        rejection = _strict_developer_output_rejection(
            _tracked_diff('safe', _screened_looking_value()), ['example.py'])
        self.assertEqual(rejection, 'DEVELOPER_OUTPUT_SENSITIVE')

    def test_match_in_new_file_is_rejected(self):
        patch = (
            'diff --git a/new.py b/new.py\n'
            'new file mode 100644\n'
            '--- /dev/null\n'
            '+++ b/new.py\n'
            '@@ -0,0 +1 @@\n'
            '+' + _screened_looking_value() + '\n'
        )
        rejection = _strict_developer_output_rejection(
            patch, ['new.py'], allow_new_files=True)
        self.assertEqual(rejection, 'DEVELOPER_OUTPUT_SENSITIVE')

    def test_context_line_is_not_screened(self):
        value = _screened_looking_value()
        patch = (
            'diff --git a/example.py b/example.py\n'
            '--- a/example.py\n'
            '+++ b/example.py\n'
            '@@ -1,2 +1,2 @@\n'
            ' ' + value + '\n'
            '-old\n'
            '+new\n'
        )
        rejection = _strict_developer_output_rejection(patch, ['example.py'])
        self.assertIsNone(rejection)


if __name__ == '__main__':
    unittest.main()
