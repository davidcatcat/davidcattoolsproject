"""#2365 follow-up: a missing trusted main pin commit has its own stop code."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from llmtool import task_completion
from llmtool.errors import LLMToolError

_ENV = {'PATH': '/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
        'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.invalid',
        'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.invalid'}


def _git(root, *args):
    return subprocess.run(['/usr/bin/git', '-C', root, *args], env=_ENV, check=True,
                          stdin=subprocess.DEVNULL, capture_output=True,
                          text=True).stdout.strip()


class MainPinCommitMissingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = str(Path(temporary.name).resolve())
        _git(self.root, 'init', '-q', '-b', 'main')
        Path(self.root, 'a.txt').write_text('a\n', encoding='utf-8')
        _git(self.root, 'add', 'a.txt')
        _git(self.root, 'commit', '-q', '-m', 'base')
        self.source = _git(self.root, 'rev-parse', 'HEAD')
        _git(self.root, 'checkout', '-q', '-b', 'dev')
        Path(self.root, 'b.txt').write_text('b\n', encoding='utf-8')
        _git(self.root, 'add', 'b.txt')
        _git(self.root, 'commit', '-q', '-m', 'dev')
        _git(self.root, 'checkout', '-q', 'main')
        Path(self.root, 'c.txt').write_text('c\n', encoding='utf-8')
        _git(self.root, 'add', 'c.txt')
        _git(self.root, 'commit', '-q', '-m', 'main moved')
        self.main = _git(self.root, 'rev-parse', 'HEAD')
        _git(self.root, 'checkout', '-q', 'dev')
        _git(self.root, 'merge', '-q', '--no-edit', 'main')
        self.head = _git(self.root, 'rev-parse', 'HEAD')
        self.absent = 'e' * 40

    def _code(self, call):
        with self.assertRaises(LLMToolError) as caught:
            call()
        return caught.exception.completion_code

    def test_tip_with_merge_names_missing_pin(self):
        self.assertEqual(self._code(lambda: task_completion._developer_tip(
            self.root, self.source, self.head, self.absent)), 'MAIN_PIN_COMMIT_MISSING')

    def test_changed_paths_with_merge_names_missing_pin(self):
        self.assertEqual(self._code(lambda: task_completion._developer_changed_paths(
            self.root, self.source, self.head, self.absent)), 'MAIN_PIN_COMMIT_MISSING')

    def test_present_pin_is_unchanged(self):
        self.assertEqual(task_completion._developer_changed_paths(
            self.root, self.source, self.head, self.main), ('b.txt',))

    def test_head_without_merges_needs_no_pin(self):
        dev_tip = _git(self.root, 'rev-parse', 'HEAD^1')
        self.assertEqual(task_completion._developer_tip(
            self.root, self.source, dev_tip, self.absent), dev_tip)


if __name__ == '__main__':
    unittest.main()
