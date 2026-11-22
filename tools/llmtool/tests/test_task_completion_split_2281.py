import json
from pathlib import Path
import unittest
from unittest import mock

from llmtool import sensitive_paths
from llmtool import task_completion
from llmtool.errors import LLMToolError


ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / 'tools' / 'llmtool' / 'llmtool'
PART_NAMES = tuple(f'task_completion_part{number}.py' for number in range(1, 5))
TASK_COMPLETION_LIMIT = 80 * 1024
PART_LIMIT = 24 * 1024


class TaskCompletionSplitTests(unittest.TestCase):
    def test_split_file_sizes(self):
        self.assertLessEqual(
            (PACKAGE / 'task_completion.py').stat().st_size,
            TASK_COMPLETION_LIMIT)
        for name in PART_NAMES:
            with self.subTest(part=name):
                path = PACKAGE / name
                self.assertTrue(path.is_file())
                self.assertLessEqual(path.stat().st_size, PART_LIMIT)

    def test_public_names_still_available(self):
        names = (
            # Public API kept from the original module.
            'review_dispatch_intent', 'validate_review_dispatch_intent',
            'review_dispatch_blocked', 'review_handoff_record',
            'review_return_decision_allowed', 'review_return_record',
            'validate_review_return_record', 'review_repair_intent',
            'validate_review_repair_intent', 'review_handoff_pending',
            'review_handoff_unknown', 'review_handoff_dispatching',
            'review_handoff_not_dispatched', 'validate_review_handoff_record',
            'normalize_review_handoff_record', 'validate_review_dispatch_receipt',
            'D6Registration', 'read_d6_registration', 'validate_env2_pass_marker',
            'GitSnapshot', 'collect_git_snapshot', 'bind_executor_task',
            'validate_task_binding', 'validate_host_worker_receipt',
            'collect_executor_result', 'collect_claude_result',
            'collect_executor_worktree_result', 'verify_artifact_applied',
            'is_formal_claude_pass', 'run_bounded_tests',
            'run_bounded_worktree_tests', 'collect_github_evidence',
            'BOOTSTRAP_SOURCES', 'REQUIRED_BOOTSTRAP_SOURCES',
            'ENV2_PASS_MARKER_SCHEMA1_KEYS', 'ENV2_PASS_MARKER_SCHEMA2_KEYS',
            # Private helpers that other code and tests import or patch.
            '_stop', '_canonical_json_bytes', '_unique_pairs', '_git',
            '_worktree_diff', '_developer_changed_paths', '_developer_tip',
            '_merge_resolution_paths', '_remerge_diff_paths_raw',
            '_merge_tree_conflict_paths_raw', '_private_merge_read',
            '_is_ancestor', '_collect_registered_result_diff',
            '_segment_shape_valid', '_verify_recorded_segment',
            '_verify_artifact_segments', '_worktree_snapshot', '_tree_entry',
            '_tree_blob', '_proposal_blob_size', '_trust_config',
            '_trusted_slug', '_ci_trust', '_github_get', '_review_time',
            '_recorded_model_family', '_reviewer_author_family',
            '_review_family_compatible', '_company_gw_text_proposal_pm_session',
            '_native_session_identity', '_review_binding_line',
            '_canonical_verdict_body', '_newer_claim_pending', '_labels_equal',
            '_runner_label_alternatives', '_trusted_ci_runner',
            '_collect_one_ci_workflow', '_collect_ci_evidence', '_not_run_checks',
            '_registered_test', '_docs_only', '_docs_only_ci',
            '_committed_changed_paths', '_test_excerpt', '_run_registered_tests',
            'read_authenticated_main_sha', '_commit_present', 'ensure_main_commit',
            'head_adds_only_main',
        )
        for name in names:
            with self.subTest(name=name):
                self.assertTrue(hasattr(task_completion, name))

    def test_patch_on_moved_helper_still_applies(self):
        calls = []

        def fake_stop(code):
            calls.append(code)
            raise RuntimeError('sentinel')

        with mock.patch.object(task_completion, '_stop', side_effect=fake_stop):
            with self.assertRaises(RuntimeError):
                task_completion._canonical_json_bytes({'bad': object()}, 'BAD')
        self.assertEqual(calls, ['BAD'])

    def test_patch_crosses_part_boundary(self):
        calls = []

        def fake_ancestor(root, ancestor, descendant):
            calls.append((root, ancestor, descendant))
            return False

        with mock.patch.object(task_completion, '_is_ancestor', side_effect=fake_ancestor):
            with self.assertRaises(LLMToolError):
                task_completion._verify_recorded_segment(
                    'root', 'a' * 40, 'c' * 40, 'b' * 40, 'a' * 40)
        self.assertEqual(calls, [('root', 'a' * 40, 'b' * 40)])

    def test_new_modules_are_rule_files(self):
        rules = json.loads(
            (ROOT / 'tools' / 'llmtool' / 'sensitive-paths.json').read_text(
                encoding='utf-8'))
        self.assertTrue(sensitive_paths.is_sensitive(
            'tools/llmtool/llmtool/task_completion.py'))
        for name in PART_NAMES:
            relative = f'tools/llmtool/llmtool/{name}'
            with self.subTest(part=relative):
                self.assertIn(relative, rules['exact'])
                self.assertTrue(sensitive_paths.is_sensitive(relative))


if __name__ == '__main__':
    unittest.main()
