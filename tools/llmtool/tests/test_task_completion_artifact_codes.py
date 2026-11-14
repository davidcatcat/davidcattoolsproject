import hashlib
import unittest
from unittest.mock import patch

from llmtool.errors import LLMToolError
from llmtool import task_completion

B = 'b' * 40
TREE = 't' * 40
ENTRY = b'100644 blob ' + B.encode() + b'\ta.py\0'


def fake_git(fn):
    return patch.object(
        task_completion,
        '_git',
        side_effect=lambda root, args, limit=0: fn(args),
    )


class TestTaskCompletionArtifactCodes(unittest.TestCase):

    def test_tree_entry_empty_output_returns_none(self):
        with fake_git(lambda args: b''):
            self.assertIsNone(task_completion._tree_entry('/r', TREE, 'a.py'))

    def test_tree_entry_valid_entry_returns_mode_and_blob_sha(self):
        with fake_git(lambda args: ENTRY):
            self.assertEqual(
                task_completion._tree_entry('/r', TREE, 'a.py'),
                ('100644', B),
            )

    def test_tree_entry_missing_trailing_nul_refuses(self):
        with fake_git(lambda args: ENTRY[:-1]):
            with self.assertRaises(LLMToolError) as ctx:
                task_completion._tree_entry('/r', TREE, 'a.py')
            self.assertEqual(
                ctx.exception.completion_code,
                'DEVELOPER_ARTIFACT_CHECK_UNKNOWN',
            )

    def test_tree_entry_two_nuls_refuses(self):
        with fake_git(lambda args: ENTRY + b'x\0'):
            with self.assertRaises(LLMToolError) as ctx:
                task_completion._tree_entry('/r', TREE, 'a.py')
            self.assertEqual(
                ctx.exception.completion_code,
                'DEVELOPER_ARTIFACT_CHECK_UNKNOWN',
            )

    def test_tree_entry_symlink_mode_refuses(self):
        entry = b'120000 blob ' + B.encode() + b'\ta.py\0'
        with fake_git(lambda args: entry):
            with self.assertRaises(LLMToolError) as ctx:
                task_completion._tree_entry('/r', TREE, 'a.py')
            self.assertEqual(
                ctx.exception.completion_code,
                'DEVELOPER_ARTIFACT_TREE_ENTRY_UNSUPPORTED',
            )

    def test_tree_entry_commit_type_refuses(self):
        entry = b'160000 commit ' + B.encode() + b'\ta.py\0'
        with fake_git(lambda args: entry):
            with self.assertRaises(LLMToolError) as ctx:
                task_completion._tree_entry('/r', TREE, 'a.py')
            self.assertEqual(
                ctx.exception.completion_code,
                'DEVELOPER_ARTIFACT_TREE_ENTRY_UNSUPPORTED',
            )

    def test_tree_entry_path_mismatch_refuses(self):
        entry = b'100644 blob ' + B.encode() + b'\tb.py\0'
        with fake_git(lambda args: entry):
            with self.assertRaises(LLMToolError) as ctx:
                task_completion._tree_entry('/r', TREE, 'a.py')
            self.assertEqual(
                ctx.exception.completion_code,
                'DEVELOPER_ARTIFACT_TREE_ENTRY_UNSUPPORTED',
            )

    def test_proposal_blob_size_valid_returns_size(self):
        def route(args):
            if args and args[0] == 'ls-tree':
                return ENTRY
            if args and args[0] == 'cat-file':
                return b'12\n'
            return b''

        with fake_git(route):
            self.assertEqual(
                task_completion._proposal_blob_size('/r', TREE, 'a.py'),
                12,
            )

    def test_proposal_blob_size_empty_ls_tree_returns_zero(self):
        def route(args):
            if args and args[0] == 'ls-tree':
                return b''
            return b''

        with fake_git(route):
            self.assertEqual(
                task_completion._proposal_blob_size('/r', TREE, 'a.py'),
                0,
            )

    def test_proposal_blob_size_non_numeric_size_refuses(self):
        def route(args):
            if args and args[0] == 'ls-tree':
                return ENTRY
            if args and args[0] == 'cat-file':
                return b'x\n'
            return b''

        with fake_git(route):
            with self.assertRaises(LLMToolError) as ctx:
                task_completion._proposal_blob_size('/r', TREE, 'a.py')
            self.assertEqual(
                ctx.exception.completion_code,
                'DEVELOPER_ARTIFACT_CHECK_UNKNOWN',
            )

    def test_proposal_blob_size_leading_zero_refuses(self):
        def route(args):
            if args and args[0] == 'ls-tree':
                return ENTRY
            if args and args[0] == 'cat-file':
                return b'01\n'
            return b''

        with fake_git(route):
            with self.assertRaises(LLMToolError) as ctx:
                task_completion._proposal_blob_size('/r', TREE, 'a.py')
            self.assertEqual(
                ctx.exception.completion_code,
                'DEVELOPER_ARTIFACT_CHECK_UNKNOWN',
            )

    def test_proposal_blob_size_exceeding_git_limit_refuses(self):
        def route(args):
            if args and args[0] == 'ls-tree':
                return ENTRY
            if args and args[0] == 'cat-file':
                return str(task_completion._GIT_LIMIT + 1).encode()
            return b''

        with fake_git(route):
            with self.assertRaises(LLMToolError) as ctx:
                task_completion._proposal_blob_size('/r', TREE, 'a.py')
            self.assertEqual(
                ctx.exception.completion_code,
                'DEVELOPER_ARTIFACT_BLOB_TOO_LARGE',
            )

    def test_verify_artifact_applied_ab_mismatch_refuses(self):
        artifact = 'diff --git a/x.py b/y.py\n'
        artifact_sha256 = hashlib.sha256(artifact.encode('utf-8')).hexdigest()
        source_sha = 'a' * 40
        result_sha = 'c' * 40
        changed_paths = ['x.py']
        write_paths = ['x.py']
        with self.assertRaises(LLMToolError) as ctx:
            task_completion.verify_artifact_applied(
                '/r',
                artifact,
                artifact_sha256,
                source_sha,
                result_sha,
                changed_paths,
                write_paths,
            )
        self.assertEqual(
            ctx.exception.completion_code,
            'DEVELOPER_ARTIFACT_INVALID',
        )

    def test_verify_artifact_applied_parent_dir_traversal_refuses(self):
        artifact = 'diff --git a/../x.py b/../x.py\n'
        artifact_sha256 = hashlib.sha256(artifact.encode('utf-8')).hexdigest()
        source_sha = 'a' * 40
        result_sha = 'c' * 40
        changed_paths = ['../x.py']
        write_paths = ['../x.py']
        with self.assertRaises(LLMToolError) as ctx:
            task_completion.verify_artifact_applied(
                '/r',
                artifact,
                artifact_sha256,
                source_sha,
                result_sha,
                changed_paths,
                write_paths,
            )
        self.assertEqual(
            ctx.exception.completion_code,
            'DEVELOPER_ARTIFACT_INVALID',
        )


if __name__ == '__main__':
    unittest.main()
