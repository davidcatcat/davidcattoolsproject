"""#2199: security verdict PR comment merge gate on direct and merge-train paths."""

import copy
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import (  # noqa: E402
    mac_full_suite,
    merge_train,
    merge_train_ops,
    merge_train_store,
    task_advance,
    task_completion,
)


HEAD = 'a' * 40
OTHER_HEAD = 'b' * 40
MAIN_SHA = 'e' * 40
LINUX = 'check (Linux 3.14)'
MACOS = 'check (macOS 3.13)'
CODE_FILES = ['tools/llmtool/llmtool/module.py']

TRAIN_ID = 'train-2199'
TRAIN_PR = 900
TASK_A = 'a' * 32
TASK_B = 'b' * 32
PR_A = 101
PR_B = 102
EXECUTOR = 'train-2199-20261123'
MERGE_SHA = 'c' * 40


def _verdict_comment(comment_id, status, head, *, version=91, actor='Codex',
                     created_at='2026-11-23T00:00:00Z', updated_at=None,
                     extra_lines=()):
    lines = [
        f'<!-- activity: type=review_verdict actor={actor} target=PM status={status} -->',
        f'Verdict: {status} | Version: v{version} | HEAD: {head}',
        *extra_lines,
    ]
    return {
        'id': comment_id,
        'body': '\n'.join(lines),
        'created_at': created_at,
        'updated_at': created_at if updated_at is None else updated_at,
    }


def _fake_github(comments=None, paths=CODE_FILES):
    pr = {
        'number': 17,
        'head': {'sha': HEAD},
        'state': 'open',
        'merged': False,
        'mergeable': True,
        'labels': [{'name': 'mac-ci'}],
    }
    runs = [
        {'id': 1, 'name': LINUX, 'head_sha': HEAD, 'status': 'completed', 'conclusion': 'success'},
        {'id': 2, 'name': MACOS, 'head_sha': HEAD, 'status': 'completed', 'conclusion': 'success'},
    ]

    def github_get(path):
        if path == f'commits/{HEAD}/check-runs?per_page=100':
            return {'total_count': len(runs), 'check_runs': runs}
        if path == 'pulls/17/files?per_page=100':
            return [{'filename': item} for item in paths]
        if path == 'pulls/17':
            return pr
        if path == 'branches/main':
            return {'commit': {'sha': MAIN_SHA}}
        if path == f'compare/{MAIN_SHA}...{HEAD}':
            return {'status': 'ahead'}
        if path == 'issues/17/comments?per_page=100':
            if callable(comments):
                return comments()
            return [] if comments is None else copy.deepcopy(comments)
        raise AssertionError(f'unexpected read: {path}')

    return pr, github_get


def _run_merge_once(comments=None, *, formal_version=2):
    pr, github_get = _fake_github(comments=comments)
    claim = {
        'pr': 17,
        'head_sha': HEAD,
        'version': formal_version,
        'state': 'COMPLETED',
        'review_result': {'verdict': 'PASS'},
    }
    event = {
        'source': 'REVIEWER',
        'source_sha': HEAD,
        'version': formal_version,
        'outcome': 'REVIEW_PASS',
    }
    record = {
        'task_id': '1' * 32,
        'record_sha256': 'r' * 64,
        'review_dispatch_claim': [claim],
    }
    updates = []
    calls = []

    class Store:
        def compare_and_update(self, *args, **kwargs):
            updates.append((args, kwargs))

    def gh_runner(_root, args):
        calls.append(list(args))
        return type('Result', (), {'returncode': 0})()

    result = task_advance._merge_once(
        store=Store(),
        record=record,
        number=17,
        head=HEAD,
        pr=pr,
        events=[event],
        github_get=github_get,
        gh_runner=gh_runner,
        root='/repo',
    )
    return result, calls, updates


def _assert_has_next_step(testcase, result):
    detail = result.get('result')
    testcase.assertIsInstance(detail, dict, result)
    message = detail.get('message', '')
    next_step = detail.get('next_step', '')
    testcase.assertTrue(isinstance(message, str) and message.strip(), result)
    testcase.assertTrue(isinstance(next_step, str) and next_step.strip(), result)
    testcase.assertIn('next step', message.lower())
    testcase.assertTrue(
        'new head' in message.lower() or 'security verdict' in message.lower(),
        message,
    )


class DirectSecurityCommentGateTests(unittest.TestCase):
    def test_security_fail_or_fix_comment_with_newer_formal_pass_refused(self):
        formal_pass = _verdict_comment(1, 'PASS', HEAD, version=2, actor='Codex')
        for status in ('FAIL', 'FIX', 'BLOCKED'):
            with self.subTest(status=status):
                sec_comment = _verdict_comment(2, status, HEAD, version=91, actor='Codex')
                result, calls, updates = _run_merge_once([sec_comment, formal_pass])
                self.assertEqual(result['step'], 'MERGE_REFUSED')
                self.assertEqual(result['codes'], ['MERGE_SECURITY_REVIEW_NOT_PASS'])
                _assert_has_next_step(self, result)
                self.assertEqual(calls, [])
                self.assertEqual(updates, [])

    def test_unreadable_comment_data_refused(self):
        def boom():
            raise RuntimeError('github down')

        cases = [
            ('read_raises', boom),
            ('non_list', {'message': 'bad'}),
            ('truncated_page', [{'id': i, 'body': 'ok'} for i in range(100)]),
            ('non_dict_item', ['corrupt']),
            ('missing_body', [{'id': 1}]),
            ('non_str_body', [{'id': 1, 'body': 123}]),
        ]
        for label, payload in cases:
            with self.subTest(label=label):
                result, calls, updates = _run_merge_once(payload)
                self.assertEqual(result['step'], 'MERGE_REFUSED')
                self.assertEqual(result['codes'], ['MERGE_SECURITY_REVIEW_UNKNOWN'])
                _assert_has_next_step(self, result)
                self.assertEqual(calls, [])
                self.assertEqual(updates, [])

    def test_short_incomplete_or_mismatched_comment_data_refused(self):
        binding_head = (
            f'LLMTOOL-REVIEW-BINDING v1 task={"1" * 32} run={"2" * 32} '
            f'contract={"3" * 64} head={HEAD}'
        )
        cases = [
            ('short_one_line_pass', [{'id': 1, 'body': f'Verdict: PASS | Version: v91 | HEAD: {HEAD}'}]),
            ('short_one_line_fail', [{'id': 1, 'body': f'Verdict: FAIL | Version: v91 | HEAD: {HEAD}'}]),
            ('short_marker_only', [{'id': 1, 'body': '<!-- activity: type=review_verdict actor=Codex target=PM status=PASS -->'}]),
            ('incomplete_missing_head', [{
                'id': 1,
                'body': '<!-- activity: type=review_verdict actor=Codex target=PM status=PASS -->\nVerdict: PASS | Version: v91',
            }]),
            ('head_mismatch_with_binding', [{
                'id': 1,
                'body': '\n'.join([
                    '<!-- activity: type=review_verdict actor=Codex target=PM status=PASS -->',
                    f'Verdict: PASS | Version: v91 | HEAD: {OTHER_HEAD}',
                    binding_head,
                ]),
                'created_at': '2026-11-23T00:00:00Z',
                'updated_at': '2026-11-23T00:00:00Z',
            }]),
            ('edited_security_pass', [
                _verdict_comment(1, 'PASS', HEAD, version=91,
                                 created_at='2026-11-23T00:00:00Z',
                                 updated_at='2026-11-23T00:01:00Z'),
            ]),
        ]
        for label, comments in cases:
            with self.subTest(label=label):
                result, calls, updates = _run_merge_once(comments)
                self.assertEqual(result['step'], 'MERGE_REFUSED')
                self.assertIn(result['codes'][0],
                              ('MERGE_SECURITY_REVIEW_UNKNOWN', 'MERGE_SECURITY_REVIEW_NOT_PASS'))
                _assert_has_next_step(self, result)
                self.assertEqual(calls, [])
                self.assertEqual(updates, [])

    def test_no_security_verdict_merges(self):
        tracking = {'id': 1, 'body': '<!-- activity: type=task actor=PM target=PR status=TRACKING -->\nEC2 PASS'}
        formal = _verdict_comment(2, 'PASS', HEAD, version=2, actor='Codex')
        result, calls, _updates = _run_merge_once([tracking, formal])
        self.assertEqual(result['step'], 'MERGED')
        self.assertEqual(sum(call[:2] == ['pr', 'merge'] for call in calls), 1)

    def test_security_pass_at_exact_head_merges(self):
        sec_pass = _verdict_comment(1, 'PASS', HEAD, version=91, actor='Codex')
        result, calls, _updates = _run_merge_once([sec_pass])
        self.assertEqual(result['step'], 'MERGED')
        self.assertEqual(sum(call[:2] == ['pr', 'merge'] for call in calls), 1)

    def test_security_verdict_on_another_head_does_not_block(self):
        old_sec_fail = _verdict_comment(1, 'FAIL', OTHER_HEAD, version=91, actor='Codex')
        result, calls, _updates = _run_merge_once([old_sec_fail])
        self.assertEqual(result['step'], 'MERGED')
        self.assertEqual(sum(call[:2] == ['pr', 'merge'] for call in calls), 1)


def _git(root, *args):
    env = {
        'PATH': os.environ.get('PATH') or '/usr/bin:/bin', 'HOME': str(root), 'LC_ALL': 'C',
        'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
        'GIT_AUTHOR_NAME': 'Test', 'GIT_AUTHOR_EMAIL': 'test@example.invalid',
        'GIT_COMMITTER_NAME': 'Test', 'GIT_COMMITTER_EMAIL': 'test@example.invalid',
        'GIT_AUTHOR_DATE': '2026-11-23T00:00:00Z',
        'GIT_COMMITTER_DATE': '2026-11-23T00:00:00Z',
    }
    completed = subprocess.run(['git', *args], cwd=str(root), env=env, check=True,
                               capture_output=True, text=True)
    return completed.stdout.strip()


def _pull(number, head):
    return {'number': number, 'state': 'open', 'merged': False, 'mergeable': True,
            'head': {'sha': head}, 'labels': []}


def _run(name, head):
    return {'name': name, 'head_sha': head, 'status': 'completed', 'conclusion': 'success'}


def _task(task_id, pr, head, version=2):
    return {
        'task_id': task_id, 'run_id': 'e' * 32, 'merge_attempt': None,
        'review_dispatch_claim': [{
            'pr': pr, 'head_sha': head, 'state': 'COMPLETED', 'version': version,
            'review_result': {'verdict': 'PASS'}}],
        'completion_events': [{
            'source': 'REVIEWER', 'source_sha': head, 'version': version,
            'outcome': 'REVIEW_PASS'}],
    }


def _member(task_id, pr, head):
    return {'task_id': task_id, 'pr': pr, 'head': head, 'executor_task': EXECUTOR,
            'review_version': 2, 'linux_check_run_id': None, 'macos_check_run_id': None,
            'remote_test_receipt_digest': 'abcdef12'}


class _TrainGitHub:
    def __init__(self):
        self.pulls, self.files, self.checks, self.comments = {}, {}, {}, {}

    def __call__(self, path):
        parts = path.split('/')
        if parts[0] == 'pulls' and path.endswith('/files?per_page=100'):
            return copy.deepcopy(self.files[int(parts[1])])
        if parts[0] == 'pulls' and len(parts) == 2:
            return copy.deepcopy(self.pulls[int(parts[1])])
        if parts[0] == 'commits' and path.endswith('/check-runs?per_page=100'):
            runs = self.checks[parts[1]]
            return {'total_count': len(runs), 'check_runs': copy.deepcopy(runs)}
        if parts[0] == 'issues' and path.endswith('/comments?per_page=100'):
            payload = self.comments.get(int(parts[1]), [])
            if callable(payload):
                return payload()
            return copy.deepcopy(payload)
        raise LookupError(path)


class _TrainGh:
    def __init__(self, github):
        self.github = github
        self.calls = []

    def __call__(self, root, args):
        self.calls.append(list(args))
        if args[:2] == ['pr', 'merge']:
            self.github.pulls[int(args[2])].update(
                state='closed', merged=True, mergeable=None, merge_commit_sha=MERGE_SHA)
        return SimpleNamespace(returncode=0, stdout='', stderr='')


class _TrainStore:
    def __init__(self, records):
        self.records = records

    def get(self, task_id):
        return copy.deepcopy(self.records[task_id])


class MergeTrainSecurityCommentGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which('git') is None:
            raise unittest.SkipTest('git is required')
        cls._repo_dir = tempfile.TemporaryDirectory(prefix='merge-train-sec-')
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
            str(root), main, [(TASK_A, heads[0]), (TASK_B, heads[1])])
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
        directory = tempfile.TemporaryDirectory(prefix='merge-train-sec-root-')
        self.addCleanup(directory.cleanup)
        self.train_root = str(Path(directory.name) / 'tasks')
        self.github = _TrainGitHub()
        self.gh = _TrainGh(self.github)
        self.store = _TrainStore({
            TASK_A: _task(TASK_A, PR_A, self.head_a),
            TASK_B: _task(TASK_B, PR_B, self.head_b),
        })
        self.github.pulls[PR_A] = _pull(PR_A, self.head_a)
        self.github.pulls[PR_B] = _pull(PR_B, self.head_b)
        self.github.pulls[TRAIN_PR] = _pull(TRAIN_PR, self.train_head)
        self.github.files[TRAIN_PR] = [{'filename': 'tools/llmtool/llmtool/example.py'}]
        self.github.checks[self.train_head] = [
            _run(LINUX, self.train_head), _run(MACOS, self.train_head)]
        patcher = mock.patch.object(
            task_completion, 'read_authenticated_main_sha', return_value=self.main)
        patcher.start()
        self.addCleanup(patcher.stop)
        suite_patcher = mock.patch(
            'llmtool.mac_full_due.request_train_full',
            return_value=mac_full_suite.MAC_FULL_SUITE_DISPATCHED)
        suite_patcher.start()
        self.addCleanup(suite_patcher.stop)
        record = {
            'schema_version': 1, 'train_id': TRAIN_ID, 'main_sha': self.main,
            'members': [_member(TASK_A, PR_A, self.head_a), _member(TASK_B, PR_B, self.head_b)],
            'excluded': [], 'train_branch': 'llmtool/train-' + TRAIN_ID,
            'train_head': self.train_head, 'train_pr': TRAIN_PR, 'state': 'BUILT',
            'merge_attempt': None, 'merge_sha': None, 'created_at': '2026-11-23T00:00:00Z',
        }
        merge_train_store.create(record, root=self.train_root)

    def _merge(self):
        return merge_train_ops.merge_train(
            TRAIN_ID, store=self.store, github_get=self.github, gh_runner=self.gh,
            root=self.root, train_root=self.train_root)

    def test_member_security_fail_or_fix_comment_with_formal_pass_refused(self):
        for status in ('FAIL', 'FIX'):
            with self.subTest(status=status):
                self.github.comments[PR_B] = [
                    _verdict_comment(1, 'PASS', self.head_b, version=2),
                    _verdict_comment(2, status, self.head_b, version=91),
                ]
                result = self._merge()
                self.assertEqual(result['step'], 'MERGE_REFUSED')
                self.assertEqual(result['codes'], ['MERGE_SECURITY_REVIEW_NOT_PASS'])
                self.assertEqual(result['result']['task_id'], TASK_B)
                _assert_has_next_step(self, result)
                self.assertEqual([c for c in self.gh.calls if c[:2] == ['pr', 'merge']], [])

    def test_member_unreadable_or_short_comment_refused(self):
        def boom():
            raise RuntimeError('comments unavailable')

        for label, payload in (
            ('read_raises', boom),
            ('short_pass', [{'id': 1, 'body': f'Verdict: PASS | Version: v91 | HEAD: {self.head_a}'}]),
        ):
            with self.subTest(label=label):
                self.github.comments[PR_A] = payload
                result = self._merge()
                self.assertEqual(result['step'], 'MERGE_REFUSED')
                self.assertEqual(result['codes'], ['MERGE_SECURITY_REVIEW_UNKNOWN'])
                self.assertEqual(result['result']['task_id'], TASK_A)
                _assert_has_next_step(self, result)
                self.assertEqual([c for c in self.gh.calls if c[:2] == ['pr', 'merge']], [])

    def test_members_with_no_security_verdict_merge(self):
        self.github.comments[PR_A] = [_verdict_comment(1, 'PASS', self.head_a, version=2)]
        self.github.comments[PR_B] = []
        result = self._merge()
        self.assertEqual(result['step'], 'MERGED')

    def test_member_security_verdict_on_other_head_does_not_block(self):
        self.github.comments[PR_A] = [_verdict_comment(1, 'FAIL', OTHER_HEAD, version=91)]
        self.github.comments[PR_B] = [_verdict_comment(2, 'PASS', self.head_b, version=91)]
        result = self._merge()
        self.assertEqual(result['step'], 'MERGED')


if __name__ == '__main__':
    unittest.main()
