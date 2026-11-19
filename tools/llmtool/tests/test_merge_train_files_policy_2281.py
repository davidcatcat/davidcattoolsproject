"""#2281 row 15b: the merge-train per-PR file maximum comes from policy.json.

A1: member_check reads merge_train_files_per_pr_max through the one policy
    loader; at its current value (100) train eligibility is unchanged.
A2: an absent key keeps the previous default (MAX_PR_FILES = 100); an invalid
    or unreadable policy refuses POLICY_INVALID, with no silent fallback.
A3: a member PR over the configured maximum is refused exactly as before, with
    the same refusal code TRAIN_MEMBER_PR_UNAVAILABLE.
"""

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import merge_train_eligibility as eligibility  # noqa: E402
from llmtool import repair_policy, sensitive_paths, task_store  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402

TASK_ID = '0123456789abcdef0123456789abcdef'
EXECUTOR = 'train-member-one'
PR = 41
HEAD = '1' * 40
LINUX = 'check (Linux 3.14)'
MACOS = 'check (macOS 3.13)'
RULE_FILE = 'tools/llmtool/worker-dispatch.json'
RULES = {'schema_version': 1, 'exact': [RULE_FILE], 'prefixes': ['tools/llmtool/rules/'],
         'globs': []}
PULL = f'pulls/{PR}'
CHECKS = f'commits/{HEAD}/check-runs?per_page=100'
FILES = f'pulls/{PR}/files?per_page=100'
COMMENTS = f'issues/{PR}/comments?per_page=100'

# The required policy keys without any #2281 optional limit.
BASE = {'schema_version': 1, 'repair_max': 5, 'parent_debug_max': 1,
        'group_debug_max': 3, 'child_max': 3, 'replan_max': 1,
        'test_fail_max': 5, 'review_fail_max': 6,
        'waiting_notify_after_hours': 24}


def _files(count):
    return [{'filename': f'tools/llmtool/llmtool/mod_{index}.py'} for index in range(count)]


def _receipt(head, result='PASS', ran_total=3):
    body = {'source_sha': head, 'result': result,
            'modules': ['test_merge_train_files_policy_2281'], 'ran_total': ran_total,
            'elapsed_s': 1.5}
    body['receipt_digest'] = hashlib.sha256(task_store._json_bytes(body)).hexdigest()
    return body


def _run(run_id, name):
    return {'id': run_id, 'name': name, 'head_sha': HEAD, 'status': 'completed',
            'conclusion': 'success'}


def _record():
    return {
        'task_id': TASK_ID,
        'run_id': 'run-1',
        'completion': {'state': 'PM_EVIDENCE_READY', 'executor_task_id': EXECUTOR},
        'review_dispatch_claim': [
            {'pr': PR, 'head_sha': HEAD, 'state': 'COMPLETED', 'version': 2,
             'review_result': {'verdict': 'PASS'}},
        ],
        'completion_events': [
            {'event_id': 'e' * 32, 'source': 'REVIEWER', 'source_sha': HEAD,
             'version': 2, 'outcome': 'REVIEW_PASS', 'state': 'PENDING'},
        ],
        'remote_test_receipts': [_receipt(HEAD)],
        'merge_attempt': None,
    }


def _routes(files):
    return {
        PULL: {'number': PR, 'state': 'open', 'merged': False, 'mergeable': True,
               'head': {'sha': HEAD}, 'labels': []},
        CHECKS: {'total_count': 2, 'check_runs': [_run(101, LINUX), _run(120, MACOS)]},
        FILES: files,
        COMMENTS: [],
    }


class FakeGitHub:
    """Read-only fake: a route value is returned; a missing route fails the read."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        if path not in self.routes:
            raise LookupError('unrouted read')
        return copy.deepcopy(self.routes[path])


class MemoryStore:
    """In-memory TaskStore stand-in that fails the test on any write."""

    def __init__(self, records):
        self.records = records

    def get(self, task_id):
        if task_id not in self.records:
            raise LLMToolError('TASK_NOT_FOUND')
        return copy.deepcopy(self.records[task_id])

    def compare_and_update(self, *args, **kwargs):
        raise AssertionError('member_check must not write the TaskStore')


class _Base(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(sensitive_paths, '_read_list',
                                    return_value=copy.deepcopy(RULES))
        patcher.start()
        self.addCleanup(patcher.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.train_root = self.home / 'tasks'

    def policy(self, data):
        """A temp policy.json holding ``data``, refused or read like the real one."""
        handle, path = tempfile.mkstemp(suffix='.json', dir=self.home)
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            stream.write(data if type(data) is str else json.dumps(data))
        os.chmod(path, 0o600)
        self.addCleanup(os.unlink, path)
        return path

    def check(self, files, policy_path=None):
        self.github = FakeGitHub(_routes(files))
        return eligibility.member_check(
            MemoryStore({TASK_ID: _record()}), TASK_ID, PR, self.github,
            train_root=self.train_root, sleep=lambda seconds: None,
            policy_path=policy_path)

    def assertRefused(self, result, code):
        self.assertEqual(result, {'eligible': False, 'code': code, 'member': None})


class CurrentValueTests(_Base):
    """A1: with the key at its current value, eligibility is unchanged."""

    def test_repository_policy_holds_the_table_value(self):
        self.assertEqual(repair_policy.load_policy()['merge_train_files_per_pr_max'], 100)
        self.assertEqual(eligibility.MAX_PR_FILES, 100)
        self.assertEqual(eligibility.FILES_PAGE, 100)

    def test_current_value_boundary_is_unchanged(self):
        self.assertTrue(self.check(_files(99))['eligible'])
        # The CI gate reads the same one page of at most 100 entries first and
        # treats a full page as possibly truncated, so a 100-entry PR is refused
        # there. The outcome (refused) is what stays unchanged; the exact
        # file-limit code is asserted on _rule_file_code below.
        self.assertFalse(self.check(_files(100))['eligible'])

    def test_rule_file_helper_keeps_the_same_boundary(self):
        self.assertIsNone(eligibility._rule_file_code(_files(99)))
        self.assertEqual(eligibility._rule_file_code(_files(100)),
                         'TRAIN_MEMBER_PR_UNAVAILABLE')


class AbsentAndInvalidPolicyTests(_Base):
    """A2: an absent key keeps the default; an invalid policy fails closed."""

    def test_absent_key_keeps_the_previous_default(self):
        path = self.policy(BASE)
        self.assertEqual(eligibility._max_pr_files(path), 100)
        self.assertTrue(self.check(_files(5), policy_path=path)['eligible'])
        # As at the current value, a 100-entry page is refused by the CI gate
        # before the file maximum is consulted, so only refusal is asserted.
        self.assertFalse(self.check(_files(100), policy_path=path)['eligible'])

    def test_missing_policy_file_refuses_policy_invalid(self):
        missing = str(self.home / 'absent.json')
        self.assertIsNone(eligibility._max_pr_files(missing))
        self.assertRefused(self.check(_files(1), policy_path=missing),
                           eligibility.POLICY_INVALID)
        self.assertEqual(eligibility.POLICY_INVALID, 'POLICY_INVALID')

    def test_invalid_value_refuses_policy_invalid_without_fallback(self):
        # The loader already refuses these (test_policy_limit_keys_2281 A2);
        # the train check must refuse rather than judge with the default.
        for value in (0, -1, '5', True, 2.5, None, [5], {'value': 5}):
            with self.subTest(value=value):
                path = self.policy(dict(BASE, merge_train_files_per_pr_max=value))
                self.assertIsNone(eligibility._max_pr_files(path))
                self.assertRefused(self.check(_files(1), policy_path=path),
                                   eligibility.POLICY_INVALID)

    def test_unreadable_policy_refuses_policy_invalid(self):
        self.assertRefused(self.check(_files(1), policy_path=self.policy('{not json')),
                           eligibility.POLICY_INVALID)


class ConfiguredMaximumTests(_Base):
    """A3: the configured maximum decides, with the same refusal code."""

    def test_configured_value_is_read_through_the_loader(self):
        path = self.policy(dict(BASE, merge_train_files_per_pr_max=2))
        self.assertEqual(eligibility._max_pr_files(path), 2)
        self.assertTrue(self.check(_files(2), policy_path=path)['eligible'])

    def test_over_the_configured_maximum_uses_the_existing_code(self):
        path = self.policy(dict(BASE, merge_train_files_per_pr_max=4))
        self.assertRefused(self.check(_files(5), policy_path=path),
                           'TRAIN_MEMBER_PR_UNAVAILABLE')
        self.assertIsNone(eligibility._rule_file_code(_files(4), 4))
        self.assertEqual(eligibility._rule_file_code(_files(5), 4),
                         'TRAIN_MEMBER_PR_UNAVAILABLE')

    def test_rule_file_still_refuses_within_the_maximum(self):
        path = self.policy(dict(BASE, merge_train_files_per_pr_max=10))
        files = _files(1) + [{'filename': RULE_FILE}]
        self.assertRefused(self.check(files, policy_path=path), 'TRAIN_MEMBER_RULE_FILE')

    def test_truncated_file_page_still_refuses(self):
        # A 100-entry page may be truncated whatever the configured maximum.
        path = self.policy(dict(BASE, merge_train_files_per_pr_max=1000))
        self.assertEqual(eligibility._rule_file_code(_files(100), 1000),
                         'TRAIN_MEMBER_PR_UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()
