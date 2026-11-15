"""#1230 refusal-code pin: OpenCode adapter worktree and source codes, before any process starts."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from llmtool import opencode_direct_developer as oc


class AdapterPreLaunchCodeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir='/private/tmp' if sys.platform == 'darwin' else None)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()

    def code(self, call):
        with self.assertRaises(oc.DirectOpenCodeError) as caught:
            call()
        return caught.exception.direct_opencode_code

    def test_worktree_that_is_not_a_directory_is_refused_before_launch(self):
        target = self.root/'file'
        target.write_text('x')
        with mock.patch.object(oc.subprocess, 'Popen', side_effect=AssertionError('no process')):
            self.assertEqual(self.code(lambda: oc.run(
                root=target, read_paths=['a.py'], write_paths=['a.py'], prompt='synthetic',
                prelaunch_validate=lambda: None)), 'OPENCODE_WORKTREE_INVALID')

    def test_unreadable_source_is_source_unavailable(self):
        with mock.patch.object(oc, '_read_scoped_file', side_effect=PermissionError('synthetic')):
            self.assertEqual(self.code(lambda: oc._screen_sources(self.root, ['a.py'])),
                             'OPENCODE_SOURCE_UNAVAILABLE')

    def test_adapter_refusal_keeps_its_own_code_and_missing_file_is_recorded(self):
        with mock.patch.object(oc, '_read_scoped_file', side_effect=oc.DirectOpenCodeError('OPENCODE_SOURCE_INVALID')):
            self.assertEqual(self.code(lambda: oc._screen_sources(self.root, ['a.py'])),
                             'OPENCODE_SOURCE_INVALID')
        with mock.patch.object(oc, '_read_scoped_file', return_value=None):
            result = oc._screen_sources(self.root, ['a.py'])
        self.assertIn({'path': 'a.py', 'exists': False}, result[3])


if __name__ == '__main__':
    unittest.main()
