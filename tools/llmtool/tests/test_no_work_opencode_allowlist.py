"""Tests for OpenCode zero-work post-run refusal allow-list (#2098 design #2113)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from llmtool.task_submission_cli import (
    _POST_RUN_NO_WORK_REFUSALS,
    _launched_run_did_no_work,
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
        self.assertTrue(_launched_run_did_no_work(dev, lambda sha: True))

    def test_opencode_rate_limited_returns_true(self):
        dev = _synthetic_dev('OPENCODE_RATE_LIMITED')
        self.assertTrue(_launched_run_did_no_work(dev, lambda sha: True))

    def test_opencode_free_limit_returns_true(self):
        dev = _synthetic_dev('OPENCODE_FREE_LIMIT')
        self.assertTrue(_launched_run_did_no_work(dev, lambda sha: True))

    def test_opencode_output_truncated_returns_true(self):
        dev = _synthetic_dev('OPENCODE_OUTPUT_TRUNCATED')
        self.assertTrue(_launched_run_did_no_work(dev, lambda sha: True))

    def test_cleanup_status_unknown_returns_false(self):
        dev = _synthetic_dev(execution={'cleanup_status': 'UNKNOWN'})
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha: True))

    def test_d6_unchanged_returning_false_returns_false(self):
        dev = _synthetic_dev()
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha: False))

    def test_opencode_cleanup_unknown_returns_false(self):
        dev = _synthetic_dev('OPENCODE_CLEANUP_UNKNOWN')
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha: True))

    def test_opencode_exit_unknown_returns_false(self):
        dev = _synthetic_dev('OPENCODE_EXIT_UNKNOWN')
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha: True))

    def test_unlisted_opencode_output_invalid_returns_false(self):
        dev = _synthetic_dev('OPENCODE_OUTPUT_INVALID')
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha: True))

    def test_gw_agent_provider_profile_returns_false(self):
        dev = _synthetic_dev(provider_profile='gw-agent')
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha: True))

    def test_non_none_artifact_returns_false(self):
        dev = _synthetic_dev(artifact='diff-data')
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha: True))

    def test_non_empty_changed_files_returns_false(self):
        dev = _synthetic_dev(changed_files=['example.py'])
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha: True))


if __name__ == '__main__':
    unittest.main()
