import os
import tempfile
import unittest
from unittest.mock import patch

from llmtool.errors import LLMToolError
from llmtool import task_completion


def snap(head, clean=True):
    return task_completion.GitSnapshot(
        root='/r',
        common_dir='/r/.git',
        branch='b',
        head=head,
        clean=clean,
    )


class TaskCompletionTestCodesTest(unittest.TestCase):

    def setUp(self):
        self._root_td = tempfile.TemporaryDirectory()
        self._home_td = tempfile.TemporaryDirectory()
        self.root = self._root_td.name
        self.home = self._home_td.name
        with open(os.path.join(self.root, 'quiet.py'), 'w', encoding='utf-8') as f:
            f.write('pass\n')
        with open(os.path.join(self.root, 'slow.py'), 'w', encoding='utf-8') as f:
            f.write('import time\ntime.sleep(30)\n')
        with open(os.path.join(self.root, 'loud.py'), 'w', encoding='utf-8') as f:
            f.write("print('x' * 1000)\n")

    def tearDown(self):
        self._root_td.cleanup()
        self._home_td.cleanup()

    def test_run_registered_tests_refuses_runner_unavailable(self):
        # Case 1: patch task_completion.subprocess.Popen with side_effect=OSError
        with patch.object(task_completion.subprocess, 'Popen', side_effect=OSError):
            with self.assertRaises(LLMToolError) as ctx:
                task_completion._run_registered_tests(
                    self.root, 'a' * 40, [('quiet.py', 'direct')], home=self.home
                )
            self.assertEqual(ctx.exception.completion_code, 'TEST_RUNNER_UNAVAILABLE')

    def test_run_registered_tests_refuses_runner_timeout(self):
        # Case 2: patch task_completion._TEST_TIMEOUT to 0.5 and run slow.py
        with patch.object(task_completion, '_TEST_TIMEOUT', 0.5):
            with self.assertRaises(LLMToolError) as ctx:
                task_completion._run_registered_tests(
                    self.root, 'a' * 40, [('slow.py', 'direct')], home=self.home
                )
            self.assertEqual(ctx.exception.completion_code, 'TEST_RUNNER_TIMEOUT')

    def test_run_registered_tests_refuses_output_too_large(self):
        # Case 3: patch task_completion._TEST_OUTPUT_LIMIT to 100 and run loud.py
        with patch.object(task_completion, '_TEST_OUTPUT_LIMIT', 100):
            with self.assertRaises(LLMToolError) as ctx:
                task_completion._run_registered_tests(
                    self.root, 'a' * 40, [('loud.py', 'direct')], home=self.home
                )
            self.assertEqual(ctx.exception.completion_code, 'TEST_OUTPUT_TOO_LARGE')

    def test_run_registered_tests_control_quiet_passed(self):
        # Case 4 (control): quiet.py returns one evidence item with status 'PASSED'
        evidence = task_completion._run_registered_tests(
            self.root, 'a' * 40, [('quiet.py', 'direct')], home=self.home
        )
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]['status'], 'PASSED')

    def test_run_bounded_tests_refuses_source_mismatch_head(self):
        # Case 5: patch collect_git_snapshot to return snap('9' * 40)
        with patch.object(task_completion, 'collect_git_snapshot', return_value=snap('9' * 40)):
            with self.assertRaises(LLMToolError) as ctx:
                task_completion.run_bounded_tests(
                    root='/r',
                    source_sha='a' * 40,
                    result_sha='b' * 40,
                    read_paths=[],
                    write_paths=[],
                )
            self.assertEqual(ctx.exception.completion_code, 'TEST_SOURCE_MISMATCH')

    def test_run_bounded_tests_refuses_source_mismatch_unclean(self):
        # Case 6: snap('b' * 40, clean=False) also refuses TEST_SOURCE_MISMATCH
        with patch.object(task_completion, 'collect_git_snapshot', return_value=snap('b' * 40, clean=False)):
            with self.assertRaises(LLMToolError) as ctx:
                task_completion.run_bounded_tests(
                    root='/r',
                    source_sha='a' * 40,
                    result_sha='b' * 40,
                    read_paths=[],
                    write_paths=[],
                )
            self.assertEqual(ctx.exception.completion_code, 'TEST_SOURCE_MISMATCH')

    def test_run_bounded_tests_refuses_source_changed(self):
        # Case 7: patch collect_git_snapshot with side_effect [snap('b' * 40), snap('c' * 40)],
        # _committed_changed_paths to return ['tools/llmtool/llmtool/x.py'] and
        # _run_registered_tests to return []
        with patch.object(
            task_completion,
            'collect_git_snapshot',
            side_effect=[snap('b' * 40), snap('c' * 40)],
        ), patch.object(
            task_completion,
            '_committed_changed_paths',
            return_value=['tools/llmtool/llmtool/x.py'],
        ), patch.object(
            task_completion,
            '_run_registered_tests',
            return_value=[],
        ):
            with self.assertRaises(LLMToolError) as ctx:
                task_completion.run_bounded_tests(
                    root='/r',
                    source_sha='a' * 40,
                    result_sha='b' * 40,
                    read_paths=['tools/llmtool/tests/test_x.py'],
                    write_paths=['tools/llmtool/llmtool/x.py'],
                )
            self.assertEqual(ctx.exception.completion_code, 'TEST_SOURCE_CHANGED')

    def test_run_bounded_worktree_tests_refuses_binding_invalid(self):
        # Case 8: diff_sha256='bad', changed_paths=['x.py']
        with self.assertRaises(LLMToolError) as ctx:
            task_completion.run_bounded_worktree_tests(
                root='/r',
                source_sha='a' * 40,
                diff_sha256='bad',
                changed_paths=['x.py'],
                read_paths=[],
                write_paths=[],
            )
        self.assertEqual(ctx.exception.completion_code, 'TEST_BINDING_INVALID')

        # Case 8: diff_sha256='1' * 64, changed_paths=[]
        with self.assertRaises(LLMToolError) as ctx:
            task_completion.run_bounded_worktree_tests(
                root='/r',
                source_sha='a' * 40,
                diff_sha256='1' * 64,
                changed_paths=[],
                read_paths=[],
                write_paths=[],
            )
        self.assertEqual(ctx.exception.completion_code, 'TEST_BINDING_INVALID')

    def test_run_bounded_worktree_tests_refuses_source_mismatch(self):
        # Case 9: patch _worktree_snapshot to return {'diff_sha256': '0' * 64, 'changed_paths': ['tools/llmtool/llmtool/x.py']}
        with patch.object(
            task_completion,
            '_worktree_snapshot',
            return_value={'diff_sha256': '0' * 64, 'changed_paths': ['tools/llmtool/llmtool/x.py']},
        ):
            with self.assertRaises(LLMToolError) as ctx:
                task_completion.run_bounded_worktree_tests(
                    root='/r',
                    source_sha='a' * 40,
                    diff_sha256='1' * 64,
                    changed_paths=['tools/llmtool/llmtool/x.py'],
                    read_paths=[],
                    write_paths=[],
                )
            self.assertEqual(ctx.exception.completion_code, 'TEST_SOURCE_MISMATCH')

    def test_run_bounded_worktree_tests_refuses_source_changed(self):
        # Case 10: _worktree_snapshot side_effect returns diff '1' * 64 then '2' * 64 (same paths),
        # _run_registered_tests returns []
        changed = ['tools/llmtool/llmtool/x.py']
        with patch.object(
            task_completion,
            '_worktree_snapshot',
            side_effect=[
                {'diff_sha256': '1' * 64, 'changed_paths': changed},
                {'diff_sha256': '2' * 64, 'changed_paths': changed},
            ],
        ), patch.object(
            task_completion,
            '_run_registered_tests',
            return_value=[],
        ):
            with self.assertRaises(LLMToolError) as ctx:
                task_completion.run_bounded_worktree_tests(
                    root='/r',
                    source_sha='a' * 40,
                    diff_sha256='1' * 64,
                    changed_paths=changed,
                    read_paths=['tools/llmtool/tests/test_x.py'],
                    write_paths=[],
                )
            self.assertEqual(ctx.exception.completion_code, 'TEST_SOURCE_CHANGED')


if __name__ == '__main__':
    unittest.main()
