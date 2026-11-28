"""#2119 WP-3 slice 1: expected_remote_modules classifies declared test paths."""
import re
import unittest
from unittest import mock

from llmtool.errors import LLMToolError
import llmtool.executor_test_evidence as executor_test_evidence
from llmtool.executor_test_evidence import expected_remote_modules
from llmtool.test_profile import TestProfile

TESTS = 'tools/llmtool/tests/'


class ExpectedRemoteModulesTests(unittest.TestCase):
    def test_remote_and_tui_sets_from_read_and_write_paths(self):
        read = [TESTS + 'test_task_store.py', TESTS + 'test_chat_tui_pty.py', 'README.md']
        write = [TESTS + 'test_tui_layout.py', TESTS + 'test_task_store.py']
        remote, tui, unsupported = expected_remote_modules(read, write)
        expected_remote = ('test_chat_tui_pty', 'test_task_store', 'test_tui_layout')
        self.assertEqual(remote, expected_remote)
        self.assertEqual(tui, ('test_chat_tui_pty', 'test_tui_layout'))
        self.assertEqual(unsupported, ())

    def test_test_shaped_paths_outside_the_pattern_are_unsupported(self):
        odd = ['scripts/test_monitor.py', TESTS + 'test_Upper.py', 'tests/test_x.py']
        remote, tui, unsupported = expected_remote_modules(odd, [])
        self.assertEqual((remote, tui), ((), ()))
        self.assertEqual(unsupported, tuple(sorted(odd)))

    def test_non_test_files_are_ignored(self):
        paths = ['tools/llmtool/llmtool/task_store.py', TESTS + 'tui_pty_driver.py']
        self.assertEqual(expected_remote_modules(paths, []), ((), (), ()))

    def test_large_remote_module_set_remains_supported(self):
        paths = [TESTS + f'test_m{index:03}.py' for index in range(100)]
        remote, tui, unsupported = expected_remote_modules(paths, [])
        self.assertEqual(remote, tuple(path.rsplit('/', 1)[-1][:-3] for path in paths))
        self.assertEqual(tui, ())
        self.assertEqual(unsupported, ())

    def test_malformed_input_refuses(self):
        for read, write in ((None, []), ([], 'a.py'), ([7], []), ([], [None])):
            with self.subTest(read=read, write=write):
                with self.assertRaises(LLMToolError):
                    expected_remote_modules(read, write)

    def test_remote_paths_come_from_the_default_test_profile(self):
        # The declared paths match the default profile's unittest pattern but
        # break the existing lowercase stem constraint, so they are refused.
        paths = [TESTS + 'test_Upper.py', TESTS + 'test_toolongstem' + 'a' * 75 + '.py']
        remote, tui, unsupported = expected_remote_modules(paths, [])
        self.assertEqual((remote, tui), ((), ()))
        self.assertEqual(unsupported, tuple(sorted(paths)))

    def test_non_default_profile_changes_the_remote_set(self):
        profile = TestProfile((
            (re.compile(r'remote/tests/test_[a-z0-9_]+\.py\Z'), 'unittest'),
            (re.compile(r'scripts/test_[a-z0-9_]+\.py\Z'), 'script'),
        ))
        with mock.patch.object(
                executor_test_evidence, 'default_profile', return_value=profile):
            remote, tui, unsupported = expected_remote_modules(
                ['remote/tests/test_alpha.py', 'scripts/test_alpha.py',
                 TESTS + 'test_task_store.py'], [])
        self.assertEqual(remote, ('test_alpha',))
        self.assertEqual(tui, ())
        self.assertEqual(unsupported, ('scripts/test_alpha.py',
                                       TESTS + 'test_task_store.py'))

    def test_invalid_profile_refuses_without_default_fallback(self):
        cases = (
            None,
            ('not', 'a', 'profile'),
            TestProfile((
                (re.compile(r'scripts/test_[a-z0-9_]+\.py\Z'), 'script'),)),
        )
        for invalid in cases:
            with self.subTest(invalid=invalid):
                with mock.patch.object(
                        executor_test_evidence, 'default_profile',
                        return_value=invalid):
                    with self.assertRaises(LLMToolError) as caught:
                        expected_remote_modules([], [])
                self.assertEqual(str(caught.exception),
                                 'EXECUTOR_TEST_PATHS_INVALID')


if __name__ == '__main__':
    unittest.main()
