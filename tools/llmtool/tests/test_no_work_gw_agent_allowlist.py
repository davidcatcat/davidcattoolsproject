"""Tests for gw-agent zero-work post-run refusal allow-list (#2098)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from llmtool.task_submission_cli import (
    _POST_RUN_NO_WORK_GW_AGENT_REFUSALS,
    _launched_run_did_no_work,
)


def _synthetic_dev(error_code='DEVELOPER_GW_AGENT_CONFIG_INVALID', **overrides):
    dev = {
        'provider_profile': 'gw-agent',
        'state': 'DEVELOPER_UNAVAILABLE',
        'error_code': error_code,
        'artifact': None,
        'artifact_sha256': None,
        'source_sha': 'a' * 40,
        'execution': {'cleanup_status': 'CONFIRMED'},
        'write_paths': ['out.txt'],
    }
    dev.update(overrides)
    return dev


class TestNoWorkGwAgentAllowlist(unittest.TestCase):

    def test_post_run_no_work_gw_agent_refusals_pinned(self):
        expected = frozenset({
            ('gw-agent', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_CONFIG_INVALID'),
            ('gw-agent', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_PROFILE_INVALID'),
            ('gw-agent', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_REQUEST_INVALID'),
            ('gw-agent', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_CREDENTIAL_MISSING'),
            ('gw-agent', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_SESSION_UNAVAILABLE'),
            ('gw-agent', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_SESSION_HOME_UNSAFE'),
            ('gw-agent', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_SESSION_LOCKED'),
            ('gw-agent', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_SESSION_BINDING_FAILED'),
            ('gw-agent', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_TASK_INVALID'),
            ('gw-agent', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_TASK_UNKNOWN_FIELDS'),
            ('gw-agent', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_TASK_SCHEMA_VERSION_INVALID'),
            ('gw-agent', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_TASK_TASK_ID_INVALID'),
            ('gw-agent', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_TASK_SOURCE_SHA_INVALID'),
            ('gw-agent', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_TASK_GOAL_INVALID'),
            ('gw-agent', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_TASK_PATHS_INVALID'),
            ('gw-agent', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_TASK_EGRESS_BASIS_INVALID'),
            ('gw-agent', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_TASK_DATA_CLASS_INVALID'),
            ('gw-agent', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_TASK_BUDGETS_INVALID'),
            ('gw-agent', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_DISPATCH_FAILED'),
            ('gw-agent', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_DISPATCH_DENIED'),
            ('gw-agent', 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_DISPATCH_DENIED'),
        })
        self.assertEqual(_POST_RUN_NO_WORK_GW_AGENT_REFUSALS, expected)

    def test_gw_agent_listed_codes_clean_d6_skipped(self):
        codes = [
            'DEVELOPER_GW_AGENT_CONFIG_INVALID',
            'DEVELOPER_GW_AGENT_PROFILE_INVALID',
            'DEVELOPER_GW_AGENT_REQUEST_INVALID',
            'DEVELOPER_GW_AGENT_CREDENTIAL_MISSING',
            'DEVELOPER_GW_AGENT_SESSION_UNAVAILABLE',
            'DEVELOPER_GW_AGENT_SESSION_HOME_UNSAFE',
            'DEVELOPER_GW_AGENT_SESSION_LOCKED',
            'DEVELOPER_GW_AGENT_SESSION_BINDING_FAILED',
            'DEVELOPER_GW_AGENT_TASK_INVALID',
            'DEVELOPER_GW_AGENT_TASK_UNKNOWN_FIELDS',
            'DEVELOPER_GW_AGENT_TASK_SCHEMA_VERSION_INVALID',
            'DEVELOPER_GW_AGENT_TASK_TASK_ID_INVALID',
            'DEVELOPER_GW_AGENT_TASK_SOURCE_SHA_INVALID',
            'DEVELOPER_GW_AGENT_TASK_GOAL_INVALID',
            'DEVELOPER_GW_AGENT_TASK_PATHS_INVALID',
            'DEVELOPER_GW_AGENT_TASK_EGRESS_BASIS_INVALID',
            'DEVELOPER_GW_AGENT_TASK_DATA_CLASS_INVALID',
            'DEVELOPER_GW_AGENT_TASK_BUDGETS_INVALID',
            'DEVELOPER_DISPATCH_FAILED',
            'DEVELOPER_DISPATCH_DENIED',
            'DEVELOPER_GW_AGENT_DISPATCH_DENIED',
        ]
        for code in codes:
            with self.subTest(code=code):
                dev = _synthetic_dev(code)
                self.assertTrue(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_gw_agent_unknown_cleanup_counts(self):
        dev = _synthetic_dev(execution={'cleanup_status': 'UNKNOWN'})
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_gw_agent_unlisted_code_counts(self):
        unlisted = [
            'DEVELOPER_GW_AGENT_UNLISTED_ERROR',
            'DEVELOPER_GW_AGENT_QUOTA_EXHAUSTED',
            'DEVELOPER_GW_AGENT_GATEWAY_TIMEOUT',
            'DEVELOPER_GW_AGENT_HTTP_429',
        ]
        for code in unlisted:
            with self.subTest(code=code):
                dev = _synthetic_dev(code)
                self.assertFalse(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_gw_agent_dirty_d6_counts(self):
        dev = _synthetic_dev()
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha, write_paths=None: False))

    def test_another_provider_with_developer_unavailable_counts(self):
        other_providers = ['codex', 'opencode', 'claude', 'agy', 'other-provider']
        for provider in other_providers:
            with self.subTest(provider=provider):
                dev = _synthetic_dev(provider_profile=provider)
                self.assertFalse(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_never_listed_codes_count(self):
        never_listed = [
            'DEVELOPER_INTERRUPTED',
            'DEVELOPER_RESULT_UNKNOWN',
            'DEVELOPER_RESULT_PERSISTENCE_REJECTED',
            'DEVELOPER_GW_AGENT_WRITE_SCOPE_VIOLATION',
            'DEVELOPER_GW_AGENT_OUTPUT_TOO_LARGE',
            'DEVELOPER_GW_AGENT_AUDIT_INVALID',
            'DEVELOPER_GW_AGENT_TASK_ID_MISMATCH',
        ]
        for code in never_listed:
            with self.subTest(code=code):
                dev = _synthetic_dev(code)
                self.assertFalse(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_non_none_artifact_counts(self):
        dev = _synthetic_dev(artifact='diff-data')
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_non_none_artifact_sha256_counts(self):
        dev = _synthetic_dev(artifact_sha256='b' * 64)
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_non_empty_changed_files_counts(self):
        dev = _synthetic_dev(changed_files=['example.py'])
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_missing_write_paths_counts(self):
        dev = _synthetic_dev()
        del dev['write_paths']
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_empty_write_paths_counts(self):
        dev = _synthetic_dev(write_paths=[])
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_non_list_write_paths_counts(self):
        dev = _synthetic_dev(write_paths='out.txt')
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_non_str_element_write_paths_counts(self):
        dev = _synthetic_dev(write_paths=['out.txt', 123])
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha, write_paths=None: True))

    def test_d6_unchanged_called_with_developer_write_paths(self):
        calls = []

        def probe(sha, write_paths=None):
            calls.append((sha, write_paths))
            return True

        dev = _synthetic_dev(write_paths=['src/main.py', 'docs/readme.md'])
        self.assertTrue(_launched_run_did_no_work(dev, probe))
        self.assertEqual(len(calls), 1)
        sha, write_paths = calls[0]
        self.assertEqual(sha, 'a' * 40)
        self.assertEqual(write_paths, ('src/main.py', 'docs/readme.md'))


if __name__ == '__main__':
    unittest.main()
