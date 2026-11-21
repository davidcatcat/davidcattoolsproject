import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool.errors import LLMToolError
from llmtool import task_completion


root = None
SHA = 'a' * 40
snapshot = None


def run(changed=('a.py',), untracked=b'', read=(), write=('a.py',), extra=()):
    def fake_git(root, args, limit=0):
        if args and args[0] == 'ls-files':
            return untracked
        return b''

    patches = [
        patch.object(task_completion, 'collect_git_snapshot', return_value=snapshot),
        patch.object(task_completion, '_git', side_effect=fake_git),
        patch.object(task_completion, '_worktree_diff', return_value=(list(changed), b'diff')),
    ]
    patches.extend(extra)
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        return task_completion._worktree_snapshot(
            root=root,
            source_sha=SHA,
            read_paths=list(read),
            write_paths=list(write),
        )


class TestTaskCompletionWorktreeCodes(unittest.TestCase):
    def setUp(self):
        global root, SHA, snapshot
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(patch.stopall)
        root = self.temp_dir.name
        Path(root, 'a.py').write_text('x = 1\n', encoding='utf-8')
        SHA = 'a' * 40
        snapshot = task_completion.GitSnapshot(
            root=root,
            common_dir=root + '/.git',
            branch='b',
            head=SHA,
            clean=False,
        )

    def test_control(self):
        res = run()
        self.assertIsInstance(res, dict)
        self.assertEqual(res.get('changed_paths'), ['a.py'])

    def test_case_1_untracked_invalid_utf8_refuses_worktree_path_invalid(self):
        with self.assertRaises(LLMToolError) as ctx:
            run(untracked=b'\xff\x00')
        self.assertEqual(ctx.exception.completion_code, 'WORKTREE_PATH_INVALID')

    def test_case_2_changed_traversal_refuses_worktree_path_invalid(self):
        with self.assertRaises(LLMToolError) as ctx:
            run(changed=('../x',), write=('../x',))
        self.assertEqual(ctx.exception.completion_code, 'WORKTREE_PATH_INVALID')

    def test_case_3_read_scope_limit_refuses_worktree_scope_invalid(self):
        # #2281: the scope limit is 1024 paths (read and write union); the write
        # path a.py makes 1024 distinct read names reach 1025 in total.
        read_names = tuple(f'r{i}.py' for i in range(1024))
        with self.assertRaises(LLMToolError) as ctx:
            run(read=read_names)
        self.assertEqual(ctx.exception.completion_code, 'WORKTREE_SCOPE_INVALID')

    def test_case_4_read_absolute_refuses_worktree_path_invalid(self):
        with self.assertRaises(LLMToolError) as ctx:
            run(read=['/abs.py'])
        self.assertEqual(ctx.exception.completion_code, 'WORKTREE_PATH_INVALID')

    def test_case_5_read_path_through_regular_file_refuses_worktree_file_unavailable(self):
        with self.assertRaises(LLMToolError) as ctx:
            run(read=['a.py/b.py'])
        self.assertEqual(ctx.exception.completion_code, 'WORKTREE_FILE_UNAVAILABLE')

    def test_case_6_tree_limit_exceeded_refuses_worktree_files_too_large(self):
        with self.assertRaises(LLMToolError) as ctx:
            run(extra=[patch.object(task_completion, '_PROPOSAL_TREE_LIMIT', 2)])
        self.assertEqual(ctx.exception.completion_code, 'WORKTREE_FILES_TOO_LARGE')

    def test_case_7_tree_limit_control_returns_dict(self):
        res = run(extra=[patch.object(task_completion, '_PROPOSAL_TREE_LIMIT', 1024)])
        self.assertIsInstance(res, dict)
        self.assertEqual(res.get('changed_paths'), ['a.py'])


if __name__ == '__main__':
    unittest.main()
