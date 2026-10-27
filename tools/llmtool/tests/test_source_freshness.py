import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import warnings
from unittest.mock import patch

import llmtool.source_freshness as freshness
from llmtool import task_submission_cli
from llmtool.cli import main
from llmtool.errors import LLMToolError


class SourceFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package_file = self.root / 'tools' / 'llmtool' / 'llmtool' / '__init__.py'
        self.package_file.parent.mkdir(parents=True)
        self.package_file.touch()
        self._git('init', '-q', '-b', 'main')
        self._git('config', 'user.name', 'Test')
        self._git('config', 'user.email', 'test@example.invalid')
        (self.root / 'tracked.txt').write_text('one\n')
        self._git('add', 'tracked.txt', 'tools/llmtool/llmtool/__init__.py')
        self._git('commit', '-q', '-m', 'first')
        self._git('update-ref', 'refs/remotes/origin/main', self._git('rev-parse', 'HEAD'))
        self.package_patch = patch.object(freshness.llmtool, '__file__',
                                          str(self.package_file))
        self.package_patch.start()
        self.addCleanup(self.package_patch.stop)
        self.allow_patch = patch.dict(os.environ, {}, clear=False)
        self.allow_patch.start()
        self.addCleanup(self.allow_patch.stop)
        os.environ.pop('LLMTOOL_ALLOW_STALE_SOURCE', None)

    def _git(self, *argv):
        result = subprocess.run(['git', *argv], cwd=self.root, check=True,
                                capture_output=True, text=True)
        return result.stdout.strip()

    def _stale(self):
        (self.root / 'tracked.txt').write_text('two\n')
        self._git('commit', '-q', '-am', 'second')

    def test_ok(self):
        self.assertEqual(freshness.source_freshness(), 'OK')
        self.assertEqual(freshness.require_fresh_source(), 'OK')

    def test_stale(self):
        self._stale()
        self.assertEqual(freshness.source_freshness(), 'STALE')
        with self.assertRaises(LLMToolError) as caught:
            freshness.require_fresh_source()
        self.assertEqual(caught.exception.completion_code, 'LLMTOOL_SOURCE_STALE')

    def test_dirty(self):
        (self.root / 'tracked.txt').write_text('dirty\n')
        self.assertEqual(freshness.source_freshness(), 'DIRTY')
        with self.assertRaises(LLMToolError) as caught:
            freshness.require_fresh_source()
        self.assertEqual(caught.exception.completion_code, 'LLMTOOL_SOURCE_DIRTY')

    def test_unknown(self):
        self.package_patch.stop()
        self.package_patch = patch.object(freshness.llmtool, '__file__',
                                          str(Path(self.temp.name) / 'not-a-repo' /
                                              'tools/llmtool/llmtool/__init__.py'))
        self.package_patch.start()
        self.assertEqual(freshness.source_freshness(), 'UNKNOWN')
        with self.assertRaises(LLMToolError) as caught:
            freshness.require_fresh_source()
        self.assertEqual(caught.exception.completion_code, 'LLMTOOL_SOURCE_UNKNOWN')

    def test_override_warns_and_returns_state(self):
        self._stale()
        os.environ['LLMTOOL_ALLOW_STALE_SOURCE'] = '1'
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            self.assertEqual(freshness.require_fresh_source(), 'STALE')
        self.assertTrue(caught)
        self.assertIn('LLMTOOL_SOURCE_STALE', str(caught[0].message))

    def test_cli_refuses_before_handlers_when_source_is_stale(self):
        cases = (
            (['task', 'delegate', 't', '--provider', 'codex',
              '--selection-source', 'PM', '--executor-task', 'e',
              '--read-path', 'a.txt', '--write-path', 'a.txt',
              '--acceptance', 'done', '--egress-basis', 'fixture'],
             'run_task_delegate'),
            (['task', 'remote-test', 't', '--executor-task', 'e', '--sha', 's',
              '--ref', 'r'], 'run_task_remote_test'),
            (['task', 'review-dispatch', 't', '--executor-task', 'e', '--pr', '1'],
             'run_task_review_dispatch'),
        )
        for argv, handler_name in cases:
            with self.subTest(handler=handler_name), \
                    patch.object(freshness, 'source_freshness', return_value='STALE'), \
                    patch(f'llmtool.task_submission_cli.{handler_name}') as handler:
                self.assertEqual(main(argv), 1)
                handler.assert_not_called()

    def test_assignment_scope_errors_have_stable_codes_without_paths(self):
        cases = (
            ((['a.txt'], ['b.txt']), 'WRITE_PATH_NOT_IN_READ_PATHS'),
            ((['../secret'], ['../secret']), 'PATH_OUTSIDE_REPO'),
            ((['a.txt', 'a.txt'], ['a.txt']), 'DUPLICATE_PATH'),
        )
        for paths, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(LLMToolError) as caught:
                    task_submission_cli._assignment_paths(*paths)
                self.assertIn(code, str(caught.exception))
                self.assertNotIn('../secret', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
