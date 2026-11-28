"""#2793 A13.0 wiring: CI profile wiring through merge_train and post-merge mac_full_due."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import (  # noqa: E402
    ci_profile,
    mac_full_due,
    merge_train,
    merge_train_ops,
    merge_train_store,
    task_completion,
)
from llmtool.ci_profile import CiProfile, CiProfileError, default_profile  # noqa: E402

TRAIN_ID = 'train-a13-wiring'
TRAIN_PR = 901
TASK_A = 'a' * 32
TASK_B = 'b' * 32
PR_A = 101
PR_B = 102
EXECUTOR = 'train-a13-2793-20261128'
MERGE_SHA = 'c' * 40

ALT_PROFILE = CiProfile(
    linux_code_check='alt-linux-check',
    docs_check='alt-docs-check',
    gateway_check='alt-gateway-check',
    ai_loop_check='alt-ai-loop-check',
    macos_check='alt-macos-check',
    linux_workflow_file='alt-linux-ci.yml',
    macos_workflow_file='alt-mac-ci.yml',
)


def _git(root, *args):
    env = {
        'PATH': os.environ.get('PATH') or '/usr/bin:/bin',
        'HOME': str(root),
        'LC_ALL': 'C',
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': '/dev/null',
        'GIT_AUTHOR_NAME': 'Test',
        'GIT_AUTHOR_EMAIL': 'test@example.invalid',
        'GIT_COMMITTER_NAME': 'Test',
        'GIT_COMMITTER_EMAIL': 'test@example.invalid',
        'GIT_AUTHOR_DATE': '2026-11-28T00:00:00Z',
        'GIT_COMMITTER_DATE': '2026-11-28T00:00:00Z',
    }
    completed = subprocess.run(
        ['git', *args], cwd=str(root), env=env, check=True,
        capture_output=True, text=True,
    )
    return completed.stdout.strip()


def _run(name, head, conclusion='success', status='completed'):
    return {'name': name, 'head_sha': head, 'status': status, 'conclusion': conclusion}


def _pull(number, head):
    return {
        'number': number,
        'state': 'open',
        'merged': False,
        'mergeable': True,
        'head': {'sha': head},
        'labels': [],
    }


def _task(task_id, pr, head, version=1):
    return {
        'task_id': task_id,
        'run_id': 'e' * 32,
        'merge_attempt': None,
        'review_dispatch_claim': [{
            'pr': pr,
            'head_sha': head,
            'state': 'COMPLETED',
            'version': version,
            'review_result': {'verdict': 'PASS'},
        }],
        'completion_events': [{
            'source': 'REVIEWER',
            'source_sha': head,
            'version': version,
            'outcome': 'REVIEW_PASS',
        }],
    }


def _member(task_id, pr, head):
    return {
        'task_id': task_id,
        'pr': pr,
        'head': head,
        'executor_task': EXECUTOR,
        'review_version': 1,
        'linux_check_run_id': None,
        'macos_check_run_id': None,
        'remote_test_receipt_digest': 'abcdef12',
    }


class _FakeGitHub:
    """Serves fixed GitHub reads for merge_train and mac_full_due, recording paths."""

    def __init__(self, main_sha):
        self.main_sha = main_sha
        self.pulls = {}
        self.files = {}
        self.checks = {}
        self.comments = {}
        self.workflow_runs = {}
        self.reads = []

    def __call__(self, path):
        self.reads.append(path)
        if path == 'branches/main':
            return {
                'commit': {
                    'sha': self.main_sha,
                    'commit': {'committer': {'date': '2026-11-28T00:00:00Z'}},
                },
            }
        if path == f'commits/{self.main_sha}/pulls':
            return []
        if path in self.workflow_runs:
            return {'workflow_runs': copy.deepcopy(self.workflow_runs[path])}
        parts = path.split('/')
        if parts[0] == 'pulls' and path.endswith('/files?per_page=100'):
            return copy.deepcopy(self.files[int(parts[1])])
        if parts[0] == 'pulls' and len(parts) == 2:
            return copy.deepcopy(self.pulls[int(parts[1])])
        if parts[0] == 'commits' and path.endswith('/check-runs?per_page=100'):
            runs = self.checks[parts[1]]
            return {'total_count': len(runs), 'check_runs': copy.deepcopy(runs)}
        if parts[0] == 'issues' and path.endswith('/comments?per_page=100'):
            return copy.deepcopy(self.comments.get(int(parts[1]), []))
        raise LookupError(path)


class _FakeGh:
    """Records gh commands and applies squash merge state to the fake PR."""

    def __init__(self, github):
        self.github = github
        self.calls = []

    def __call__(self, root, args):
        self.calls.append(list(args))
        if args[:2] == ['pr', 'merge']:
            self.github.pulls[int(args[2])].update(
                state='closed',
                merged=True,
                mergeable=None,
                merge_commit_sha=MERGE_SHA,
            )
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    def named(self, verb):
        return [call for call in self.calls if call[:2] == ['pr', verb]]

    def workflow_runs(self):
        return [call for call in self.calls if call[:2] == ['workflow', 'run']]


class _FakeStore:
    def __init__(self, records):
        self.records = records

    def get(self, task_id):
        return copy.deepcopy(self.records[task_id])


class MergeTrainCiProfileWiringTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if shutil.which('git') is None:
            raise unittest.SkipTest('git is required')
        cls._repo_dir = tempfile.TemporaryDirectory(prefix='merge-train-a13-')
        root = Path(cls._repo_dir.name) / 'repo'
        root.mkdir()
        _git(root, 'init', '-q')
        _git(root, 'symbolic-ref', 'HEAD', 'refs/heads/main')
        (root / 'base.txt').write_text('base\n', encoding='utf-8')
        _git(root, 'add', 'base.txt')
        _git(root, 'commit', '-q', '-m', 'base')
        main = _git(root, 'rev-parse', 'HEAD')
        heads = []
        for name in ('a', 'b'):
            _git(root, 'checkout', '-q', '-b', 'member-' + name, main)
            (root / (name + '.txt')).write_text(name + '\n', encoding='utf-8')
            _git(root, 'add', name + '.txt')
            _git(root, 'commit', '-q', '-m', 'member ' + name)
            heads.append(_git(root, 'rev-parse', 'HEAD'))
        _git(root, 'checkout', '-q', 'main')
        built = merge_train.build_candidate(
            str(root), main, [(TASK_A, heads[0]), (TASK_B, heads[1])],
        )
        if built['head'] is None:
            raise AssertionError(built)
        cls.root = str(root)
        cls.main = main
        cls.head_a, cls.head_b = heads
        cls.train_head = built['head']

    @classmethod
    def tearDownClass(cls):
        cls._repo_dir.cleanup()

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='merge-train-a13-root-')
        self.addCleanup(directory.cleanup)
        self.train_root = str(Path(directory.name) / 'tasks')
        self.github = _FakeGitHub(self.main)
        self.gh = _FakeGh(self.github)
        self.store = _FakeStore({
            TASK_A: _task(TASK_A, PR_A, self.head_a),
            TASK_B: _task(TASK_B, PR_B, self.head_b),
        })
        self.github.pulls[PR_A] = _pull(PR_A, self.head_a)
        self.github.pulls[PR_B] = _pull(PR_B, self.head_b)
        self.github.pulls[TRAIN_PR] = _pull(TRAIN_PR, self.train_head)
        self.github.files[TRAIN_PR] = [{'filename': 'tools/llmtool/llmtool/example.py'}]
        self.github.workflow_runs[mac_full_due.runs_path(ALT_PROFILE.macos_workflow_file)] = []
        patcher = mock.patch.object(
            task_completion, 'read_authenticated_main_sha', return_value=self.main,
        )
        self.read_main = patcher.start()
        self.addCleanup(patcher.stop)

    def _create(self):
        record = {
            'schema_version': 1,
            'train_id': TRAIN_ID,
            'main_sha': self.main,
            'members': [_member(TASK_A, PR_A, self.head_a), _member(TASK_B, PR_B, self.head_b)],
            'excluded': [],
            'train_branch': 'llmtool/train-' + TRAIN_ID,
            'train_head': self.train_head,
            'train_pr': TRAIN_PR,
            'state': 'BUILT',
            'merge_attempt': None,
            'merge_sha': None,
            'created_at': '2026-11-28T00:00:00Z',
        }
        return merge_train_store.create(record, root=self.train_root)

    def _record(self):
        return merge_train_store.get(TRAIN_ID, root=self.train_root)

    def _merge(self):
        return merge_train_ops.merge_train(
            TRAIN_ID,
            store=self.store,
            github_get=self.github,
            gh_runner=self.gh,
            root=self.root,
            train_root=self.train_root,
        )

    def test_substituted_profile_merges_and_uses_substituted_mac_workflow(self):
        defaults = default_profile()
        self._create()
        self.github.checks[self.train_head] = [
            _run(ALT_PROFILE.linux_code_check, self.train_head),
            _run(ALT_PROFILE.macos_check, self.train_head),
        ]
        with mock.patch.object(ci_profile, 'default_profile', return_value=ALT_PROFILE):
            result = self._merge()

        self.assertEqual(result['step'], 'MERGED', result)
        self.assertEqual(result['codes'], [])
        self.assertEqual(
            self.gh.named('merge'),
            [['pr', 'merge', str(TRAIN_PR), '--squash', '--match-head-commit', self.train_head]],
        )
        record = self._record()
        self.assertEqual(record['state'], 'MERGED')
        self.assertEqual(record['merge_sha'], MERGE_SHA)

        expected_runs_path = mac_full_due.runs_path(ALT_PROFILE.macos_workflow_file)
        default_runs_path = mac_full_due.runs_path(defaults.macos_workflow_file)
        self.assertIn(expected_runs_path, self.github.reads)
        self.assertNotIn(default_runs_path, self.github.reads)
        self.assertEqual(
            self.gh.workflow_runs(),
            [['workflow', 'run', ALT_PROFILE.macos_workflow_file, '--ref', 'main']],
        )

    def test_substituted_profile_rejects_original_check_names(self):
        defaults = default_profile()
        self._create()
        self.github.checks[self.train_head] = [
            _run(defaults.linux_code_check, self.train_head),
            _run(defaults.macos_check, self.train_head),
        ]
        before = self._record()
        with mock.patch.object(ci_profile, 'default_profile', return_value=ALT_PROFILE):
            result = self._merge()

        self.assertEqual(
            result,
            {'step': 'MERGE_REFUSED', 'codes': ['MERGE_REQUIRED_CHECK_MISSING'], 'result': None},
        )
        self.assertEqual(self.gh.named('merge'), [])
        self.assertEqual(self.gh.calls, [])
        self.assertEqual(self._record()['state'], 'BUILT')
        self.assertEqual(self._record(), before)

    def test_missing_raising_or_wrong_type_profile_refuses_without_merge(self):
        self._create()
        self.github.checks[self.train_head] = [
            _run(ALT_PROFILE.linux_code_check, self.train_head),
            _run(ALT_PROFILE.macos_check, self.train_head),
        ]
        cases = (
            ('missing_none', {'return_value': None}),
            ('raising_ci_profile_error', {'side_effect': CiProfileError()}),
            ('raising_runtime_error', {'side_effect': RuntimeError('missing profile')}),
            ('wrong_type_str', {'return_value': 'check (Linux 3.14)'}),
            ('wrong_type_dict', {'return_value': {'linux_code_check': 'check (Linux 3.14)'}}),
            (
                'wrong_type_namespace',
                {'return_value': types.SimpleNamespace(linux_code_check='check (Linux 3.14)')},
            ),
            ('wrong_type_list', {'return_value': [ALT_PROFILE, default_profile()]}),
        )
        for kind, patch_kwargs in cases:
            with self.subTest(kind=kind):
                before = self._record()
                with mock.patch.object(ci_profile, 'default_profile', **patch_kwargs):
                    result = self._merge()
                self.assertEqual(
                    result,
                    {'step': 'MERGE_REFUSED', 'codes': ['MERGE_CI_PROFILE_INVALID'], 'result': None},
                )
                self.assertEqual(self.gh.named('merge'), [])
                self.assertEqual(self.gh.calls, [])
                self.assertEqual(self._record(), before)


if __name__ == '__main__':
    unittest.main()
