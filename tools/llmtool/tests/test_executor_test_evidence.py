"""#2119 WP-3 slice 1: expected_remote_modules classifies declared test paths."""
import unittest

from llmtool.errors import LLMToolError
from llmtool.executor_test_evidence import MAX_REMOTE_MODULES, expected_remote_modules

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

    def test_more_than_the_limit_makes_every_remote_path_unsupported(self):
        paths = [TESTS + 'test_m%d.py' % index for index in range(MAX_REMOTE_MODULES + 1)]
        remote, tui, unsupported = expected_remote_modules(paths, [])
        self.assertEqual(len(remote), MAX_REMOTE_MODULES + 1)
        self.assertEqual(unsupported, tuple(sorted(paths)))
        limit = expected_remote_modules(paths[:MAX_REMOTE_MODULES], [])
        self.assertEqual(limit[2], ())

    def test_malformed_input_refuses(self):
        for read, write in ((None, []), ([], 'a.py'), ([7], []), ([], [None])):
            with self.subTest(read=read, write=write):
                with self.assertRaises(LLMToolError):
                    expected_remote_modules(read, write)


if __name__ == '__main__':
    unittest.main()
