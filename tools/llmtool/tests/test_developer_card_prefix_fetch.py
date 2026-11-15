"""Tests for fetching main on developer card prefix build errors (#2029)."""

import hashlib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from llmtool.errors import LLMToolError
from llmtool import skill_ledger
from llmtool import skill_manifest
from llmtool import source_freshness
from llmtool import task_completion
from llmtool import task_submission_cli


class TestDeveloperCardPrefixFetch(unittest.TestCase):

    def setUp(self):
        self.task = {'task_id': 'a' * 32, 'run_id': 'b' * 32}
        self.registration = SimpleNamespace(root='/r')
        self.pinned = 'c' * 40
        self.successful_built = {
            'commit': self.pinned,
            'route': 'agy',
            'cards': [{'text': 'card'}],
        }

    @patch.object(skill_manifest, 'role_card_prefix', return_value='prefix')
    @patch.object(skill_manifest, 'build_role_cards')
    @patch.object(source_freshness, '_fetch_origin_main')
    @patch.object(task_completion, 'ensure_main_commit')
    @patch.object(skill_ledger, 'pin_dispatch', return_value='c' * 40)
    def test_build_succeeds_first_time(
        self, mock_pin, mock_ensure, mock_fetch, mock_build, mock_prefix
    ):
        mock_build.side_effect = [self.successful_built]
        result = task_submission_cli._developer_card_prefix(
            self.task, self.registration, 'agy')
        expected_sha = hashlib.sha256(b'prefix').hexdigest()
        self.assertEqual(result, ('prefix', expected_sha))
        mock_ensure.assert_called_once_with('/r', 'c' * 40)
        mock_fetch.assert_not_called()
        self.assertEqual(mock_build.call_count, 1)

    @patch.object(skill_manifest, 'role_card_prefix', return_value='prefix')
    @patch.object(skill_manifest, 'build_role_cards')
    @patch.object(source_freshness, '_fetch_origin_main')
    @patch.object(task_completion, 'ensure_main_commit')
    @patch.object(skill_ledger, 'pin_dispatch', return_value='c' * 40)
    def test_build_not_on_main_then_succeeds(
        self, mock_pin, mock_ensure, mock_fetch, mock_build, mock_prefix
    ):
        mock_build.side_effect = [
            skill_manifest.SkillManifestError(skill_manifest.SKILL_MANIFEST_COMMIT_NOT_ON_MAIN),
            self.successful_built,
        ]
        result = task_submission_cli._developer_card_prefix(
            self.task, self.registration, 'agy')
        expected_sha = hashlib.sha256(b'prefix').hexdigest()
        self.assertEqual(result, ('prefix', expected_sha))
        mock_ensure.assert_called_once_with('/r', 'c' * 40)
        mock_fetch.assert_called_once_with('/r')
        self.assertEqual(mock_build.call_count, 2)

    @patch.object(skill_manifest, 'role_card_prefix', return_value='prefix')
    @patch.object(skill_manifest, 'build_role_cards')
    @patch.object(source_freshness, '_fetch_origin_main')
    @patch.object(task_completion, 'ensure_main_commit')
    @patch.object(skill_ledger, 'pin_dispatch', return_value='c' * 40)
    def test_build_not_local_then_succeeds(
        self, mock_pin, mock_ensure, mock_fetch, mock_build, mock_prefix
    ):
        mock_build.side_effect = [
            skill_manifest.SkillManifestError(skill_manifest.SKILL_MANIFEST_COMMIT_NOT_LOCAL),
            self.successful_built,
        ]
        result = task_submission_cli._developer_card_prefix(
            self.task, self.registration, 'agy')
        expected_sha = hashlib.sha256(b'prefix').hexdigest()
        self.assertEqual(result, ('prefix', expected_sha))
        mock_ensure.assert_called_once_with('/r', 'c' * 40)
        mock_fetch.assert_called_once_with('/r')
        self.assertEqual(mock_build.call_count, 2)

    @patch.object(skill_manifest, 'role_card_prefix', return_value='prefix')
    @patch.object(skill_manifest, 'build_role_cards')
    @patch.object(source_freshness, '_fetch_origin_main')
    @patch.object(task_completion, 'ensure_main_commit')
    @patch.object(skill_ledger, 'pin_dispatch', return_value='c' * 40)
    def test_build_not_on_main_twice(
        self, mock_pin, mock_ensure, mock_fetch, mock_build, mock_prefix
    ):
        mock_build.side_effect = [
            skill_manifest.SkillManifestError(skill_manifest.SKILL_MANIFEST_COMMIT_NOT_ON_MAIN),
            skill_manifest.SkillManifestError(skill_manifest.SKILL_MANIFEST_COMMIT_NOT_ON_MAIN),
        ]
        with self.assertRaises(LLMToolError) as ctx:
            task_submission_cli._developer_card_prefix(
                self.task, self.registration, 'agy')
        self.assertEqual(str(ctx.exception), skill_manifest.SKILL_MANIFEST_COMMIT_NOT_ON_MAIN)
        mock_ensure.assert_called_once_with('/r', 'c' * 40)
        mock_fetch.assert_called_once_with('/r')
        self.assertEqual(mock_build.call_count, 2)

    @patch.object(skill_manifest, 'role_card_prefix', return_value='prefix')
    @patch.object(skill_manifest, 'build_role_cards')
    @patch.object(source_freshness, '_fetch_origin_main')
    @patch.object(task_completion, 'ensure_main_commit')
    @patch.object(skill_ledger, 'pin_dispatch', return_value='c' * 40)
    def test_build_path_missing_first(
        self, mock_pin, mock_ensure, mock_fetch, mock_build, mock_prefix
    ):
        mock_build.side_effect = [
            skill_manifest.SkillManifestError(skill_manifest.SKILL_MANIFEST_PATH_MISSING),
        ]
        with self.assertRaises(LLMToolError) as ctx:
            task_submission_cli._developer_card_prefix(
                self.task, self.registration, 'agy')
        self.assertEqual(str(ctx.exception), 'AGY_ROLE_CARD_MISSING')
        mock_ensure.assert_called_once_with('/r', 'c' * 40)
        mock_fetch.assert_not_called()
        self.assertEqual(mock_build.call_count, 1)


if __name__ == '__main__':
    unittest.main()
