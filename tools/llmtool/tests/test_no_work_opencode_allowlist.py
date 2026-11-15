"""Tests for OpenCode zero-work post-run refusal allow-list (#2098 design #2113)."""

import os
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from llmtool.task_submission_cli import (
    _POST_RUN_NO_WORK_REFUSALS,
    _d6_unchanged_probe,
    _launched_run_did_no_work,
    _paths_overlap,
)


def _synthetic_dev(error_code='OPENCODE_PROVIDER_FAILED', **overrides):
    dev = {
        'provider_profile': 'opencode',
        'state': 'DEVELOPER_OUTCOME_UNKNOWN',
        'error_code': error_code,
        'artifact': None,
        'artifact_sha256': None,
        'source_sha': 'a' * 40,
        'execution': {'cleanup_status': 'CONFIRMED'},
        'write_paths': ['out.txt'],
    }
    dev.update(overrides)
    return dev


class TestNoWorkOpenCodeAllowlist(unittest.TestCase):

    def test_post_run_no_work_refusals_pinned(self):
        expected = frozenset({
            ('opencode', 'DEVELOPER_OUTCOME_UNKNOWN', 'OPENCODE_PROVIDER_FAILED'),
            ('opencode', 'DEVELOPER_OUTCOME_UNKNOWN', 'OPENCODE_RATE_LIMITED'),
            ('opencode', 'DEVELOPER_OUTCOME_UNKNOWN', 'OPENCODE_FREE_LIMIT'),
            ('opencode', 'DEVELOPER_OUTCOME_UNKNOWN', 'OPENCODE_OUTPUT_TRUNCATED'),
        })
        self.assertEqual(_POST_RUN_NO_WORK_REFUSALS, expected)

    def test_opencode_provider_failed_returns_true(self):
        dev = _synthetic_dev('OPENCODE_PROVIDER_FAILED')
        self.assertTrue(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_opencode_rate_limited_returns_true(self):
        dev = _synthetic_dev('OPENCODE_RATE_LIMITED')
        self.assertTrue(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_opencode_free_limit_returns_true(self):
        dev = _synthetic_dev('OPENCODE_FREE_LIMIT')
        self.assertTrue(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_opencode_output_truncated_returns_true(self):
        dev = _synthetic_dev('OPENCODE_OUTPUT_TRUNCATED')
        self.assertTrue(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_cleanup_status_unknown_returns_false(self):
        dev = _synthetic_dev(execution={'cleanup_status': 'UNKNOWN'})
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_d6_unchanged_returning_false_returns_false(self):
        dev = _synthetic_dev()
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha, write_paths=None: False))

    def test_opencode_cleanup_unknown_returns_false(self):
        dev = _synthetic_dev('OPENCODE_CLEANUP_UNKNOWN')
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_opencode_exit_unknown_returns_false(self):
        dev = _synthetic_dev('OPENCODE_EXIT_UNKNOWN')
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_unlisted_opencode_output_invalid_returns_false(self):
        dev = _synthetic_dev('OPENCODE_OUTPUT_INVALID')
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_gw_agent_provider_profile_returns_false(self):
        dev = _synthetic_dev(provider_profile='gw-agent')
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_non_none_artifact_returns_false(self):
        dev = _synthetic_dev(artifact='diff-data')
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_non_empty_changed_files_returns_false(self):
        dev = _synthetic_dev(changed_files=['example.py'])
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_missing_write_paths_returns_false(self):
        dev = _synthetic_dev()
        del dev['write_paths']
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_empty_write_paths_returns_false(self):
        dev = _synthetic_dev(write_paths=[])
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_non_list_write_paths_returns_false(self):
        dev = _synthetic_dev(write_paths='out.txt')
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_non_str_element_write_paths_returns_false(self):
        dev = _synthetic_dev(write_paths=['out.txt', 123])
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_d6_unchanged_called_with_developer_write_paths(self):
        calls = []

        def probe(sha, write_paths=None):
            calls.append((sha, write_paths))
            return True

        dev = _synthetic_dev(write_paths=['src/module.py', 'docs/guide.md'])
        self.assertTrue(_launched_run_did_no_work(dev, probe))
        self.assertEqual(len(calls), 1)
        sha, write_paths = calls[0]
        self.assertEqual(sha, 'a' * 40)
        self.assertEqual(write_paths, ('src/module.py', 'docs/guide.md'))


class TestPathsOverlap(unittest.TestCase):

    def test_ignored_file_equal_to_write_path(self):
        self.assertTrue(_paths_overlap({'out.txt'}, ('out.txt',)))

    def test_ignored_dir_containing_write_path(self):
        self.assertTrue(_paths_overlap({'build/'}, ('build/out.txt',)))

    def test_unrelated_ignored_dir_no_overlap(self):
        self.assertFalse(_paths_overlap({'.venv/'}, ('build/out.txt',)))

    def test_dirty_file_under_written_dir(self):
        self.assertTrue(_paths_overlap({'src/module.py'}, ('src',)))
        self.assertTrue(_paths_overlap({'src/module.py'}, ('src/',)))

    def test_empty_or_none_paths_no_overlap(self):
        self.assertFalse(_paths_overlap(set(), ('out.txt',)))
        self.assertFalse(_paths_overlap({'out.txt'}, ()))
        self.assertFalse(_paths_overlap(None, ('out.txt',)))
        self.assertFalse(_paths_overlap({'out.txt'}, None))


class TestD6UnchangedProbe(unittest.TestCase):

    def test_d6_unchanged_overlap_returns_false(self):
        reg = SimpleNamespace(root='/fake/root')
        snap = SimpleNamespace(head='a' * 40)
        with patch('llmtool.task_completion._worktree_diff', return_value=((), b'')), \
             patch('llmtool.task_submission_cli._git_status_paths', return_value=('build/',)):
            d6_unchanged = _d6_unchanged_probe(None, (reg, snap))
            self.assertFalse(d6_unchanged('a' * 40, write_paths=('build/out.txt',)))

    def test_d6_unchanged_clean_no_overlap_returns_true(self):
        reg = SimpleNamespace(root='/fake/root')
        snap = SimpleNamespace(head='a' * 40)
        with patch('llmtool.task_completion._worktree_diff', return_value=((), b'')), \
             patch('llmtool.task_submission_cli._git_status_paths', return_value=()):
            d6_unchanged = _d6_unchanged_probe(None, (reg, snap))
            self.assertTrue(d6_unchanged('a' * 40, write_paths=('src/main.py',)))

    def test_d6_unchanged_write_paths_none_preserves_behavior(self):
        reg = SimpleNamespace(root='/fake/root')
        snap = SimpleNamespace(head='a' * 40)
        with patch('llmtool.task_completion._worktree_diff', return_value=((), b'')), \
             patch('llmtool.task_submission_cli._git_status_paths', return_value=()):
            d6_unchanged = _d6_unchanged_probe(None, (reg, snap))
            self.assertTrue(d6_unchanged('a' * 40, write_paths=None))
            self.assertFalse(d6_unchanged('b' * 40, write_paths=None))

    def test_d6_unchanged_ignored_dir_overlap(self):
        reg = SimpleNamespace(root='/fake/root')
        snap = SimpleNamespace(head='a' * 40)
        with patch('llmtool.task_completion._worktree_diff', return_value=((), b'')), \
             patch('llmtool.task_submission_cli._git_status_paths', return_value=('build/',)):
            d6_unchanged = _d6_unchanged_probe(None, (reg, snap))
            self.assertFalse(d6_unchanged('a' * 40, write_paths=('build/out.txt',)))
        with patch('llmtool.task_completion._worktree_diff', return_value=((), b'')), \
             patch('llmtool.task_submission_cli._git_status_paths', return_value=('.venv/',)):
            d6_unchanged = _d6_unchanged_probe(None, (reg, snap))
            self.assertTrue(d6_unchanged('a' * 40, write_paths=('build/out.txt',)))

    def test_d6_unchanged_exception_fails_closed(self):
        reg = SimpleNamespace(root='/fake/root')
        snap = SimpleNamespace(head='a' * 40)
        with patch('llmtool.task_completion._worktree_diff', return_value=((), b'')), \
             patch('llmtool.task_submission_cli._git_status_paths', side_effect=RuntimeError('inspection failed')):
            d6_unchanged = _d6_unchanged_probe(None, (reg, snap))
            self.assertFalse(d6_unchanged('a' * 40, write_paths=('src/main.py',)))
            self.assertFalse(d6_unchanged('a' * 40, write_paths=None))


if __name__ == '__main__':
    unittest.main()
