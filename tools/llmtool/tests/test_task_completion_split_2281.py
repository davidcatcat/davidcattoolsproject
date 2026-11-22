"""#2281: task_completion size and name-availability checks."""

import json
from pathlib import Path
import unittest

from llmtool import sensitive_paths
from llmtool import task_completion


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MODULE_DIR = REPOSITORY_ROOT / 'tools' / 'llmtool' / 'llmtool'
SPLIT_MODULES = (
    'task_completion_part1.py',
    'task_completion_part2.py',
    'task_completion_part3.py',
    'task_completion_part4.py',
)
TASK_COMPLETION_LIMIT = 81920
PART_LIMIT = 24576

PUBLIC_NAMES = (
    'BOOTSTRAP_SOURCES',
    'REQUIRED_BOOTSTRAP_SOURCES',
    'ENV2_PASS_MARKER_SCHEMA1_KEYS',
    'ENV2_PASS_MARKER_SCHEMA2_KEYS',
    'review_dispatch_intent',
    'validate_review_dispatch_intent',
    'review_dispatch_blocked',
    'review_handoff_record',
    'review_return_decision_allowed',
    'review_return_record',
    'validate_review_return_record',
    'review_repair_intent',
    'validate_review_repair_intent',
    'review_handoff_pending',
    'review_handoff_unknown',
    'review_handoff_dispatching',
    'review_handoff_not_dispatched',
    'validate_review_handoff_record',
    'normalize_review_handoff_record',
    'validate_review_dispatch_receipt',
    'D6Registration',
    'read_d6_registration',
    'validate_env2_pass_marker',
    'GitSnapshot',
    'collect_git_snapshot',
    'bind_executor_task',
    'validate_task_binding',
    'validate_host_worker_receipt',
    'read_authenticated_main_sha',
    'ensure_main_commit',
    'head_adds_only_main',
    'collect_executor_result',
    'collect_claude_result',
    'collect_executor_worktree_result',
    'verify_artifact_applied',
    'run_bounded_tests',
    'run_bounded_worktree_tests',
    'collect_github_evidence',
    'is_formal_claude_pass',
)


class TaskCompletionSplitTests(unittest.TestCase):
    def test_module_sizes(self):
        path = MODULE_DIR / 'task_completion.py'
        self.assertLessEqual(path.stat().st_size, TASK_COMPLETION_LIMIT)
        for name in SPLIT_MODULES:
            with self.subTest(module=name):
                part = MODULE_DIR / name
                self.assertTrue(part.is_file())
                self.assertLessEqual(part.stat().st_size, PART_LIMIT)

    def test_public_names_still_available(self):
        for name in PUBLIC_NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(task_completion, name))

    def test_split_modules_are_rule_files(self):
        rules = json.loads(
            (REPOSITORY_ROOT / 'tools' / 'llmtool' / 'sensitive-paths.json')
            .read_text(encoding='utf-8'))
        for name in SPLIT_MODULES:
            relative = 'tools/llmtool/llmtool/' + name
            with self.subTest(module=name):
                self.assertIn(relative, rules['exact'])
                self.assertTrue(sensitive_paths.is_sensitive(relative))


if __name__ == '__main__':
    unittest.main()
