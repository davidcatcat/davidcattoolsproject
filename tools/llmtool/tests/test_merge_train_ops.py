"""Tests for the merge-train merge and close steps (#1182 T4b2)."""

import copy
import fcntl
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

from llmtool import merge_train, merge_train_ops, merge_train_store, task_completion  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402

TRAIN_ID = 'train-1182'
TRAIN_PR = 900
TASK_A = 'a' * 32
TASK_B = 'b' * 32
PR_A = 101
PR_B = 102
EXECUTOR = 'train-t4b2-1182-20261108'
MERGE_SHA = 'c' * 40
OTHER_SHA = 'd' * 40
LINUX = 'check (Linux 3.14)'
MACOS = 'check (macOS 3.13)'


def _git(root, *args):
    env = {
        'PATH': os.environ.get('PATH') or '/usr/bin:/bin', 'HOME': str(root), 'LC_ALL': 'C',
        'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
        'GIT_AUTHOR_NAME': 'Test', 'GIT_AUTHOR_EMAIL': 'test@example.invalid',
        'GIT_COMMITTER_NAME': 'Test', 'GIT_COMMITTER_EMAIL': 'test@example.invalid',
        'GIT_AUTHOR_DATE': '2026-11-08T00:00:00Z',
        'GIT_COMMITTER_DATE': '2026-11-08T00:00:00Z',
    }
    completed = subprocess.run(['git', *args], cwd=str(root), env=env, check=True,
                               capture_output=True, text=True)
    return completed.stdout.strip()


def _run(name, head, conclusion='success', status='completed'):
    return {'name': name, 'head_sha': head, 'status': status, 'conclusion': conclusion}


def _pull(number, head):
    return {'number': number, 'state': 'open', 'merged': False, 'mergeable': True,
            'head': {'sha': head}, 'labels': []}


def _task(task_id, pr, head, version=1):
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
            'review_version': 1, 'linux_check_run_id': None, 'macos_check_run_id': None,
            'remote_test_receipt_digest': 'abcdef12'}


class FakeGitHub:
    """Serves fixed GitHub reads and records every path read."""

    def __init__(self):
        self.pulls, self.files, self.checks = {}, {}, {}
        self.reads, self.hooks = [], {}

    def __call__(self, path):
        self.reads.append(path)
        hook = self.hooks.pop(path, None)
        if hook is not None:
            hook()
        parts = path.split('/')
        if parts[0] == 'pulls' and path.endswith('/files?per_page=100'):
            return copy.deepcopy(self.files[int(parts[1])])
        if parts[0] == 'pulls' and len(parts) == 2:
            return copy.deepcopy(self.pulls[int(parts[1])])
        if parts[0] == 'commits' and path.endswith('/check-runs?per_page=100'):
            runs = self.checks[parts[1]]
            return {'total_count': len(runs), 'check_runs': copy.deepcopy(runs)}
        raise LookupError(path)


class FakeGh:
    """Records gh commands; a merge can change the fake PR or leave it open."""

    def __init__(self, github):
        self.github = github
        self.calls = []
        self.merge_returncode = 0
        self.merge_effect = True
        # PR numbers whose ``pr close`` exits 1, or raises.
        self.close_fail = set()
        self.close_raise = set()

    def __call__(self, root, args):
        self.calls.append(list(args))
        if args[:2] == ['pr', 'close']:
            number = int(args[2])
            if number in self.close_raise:
                raise OSError('gh failed')
            if number in self.close_fail:
                return SimpleNamespace(returncode=1, stdout='', stderr='')
            self.github.pulls[number]['state'] = 'closed'
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        if args[:2] == ['pr', 'merge']:
            if self.merge_effect:
                self.github.pulls[int(args[2])].update(
                    state='closed', merged=True, mergeable=None, merge_commit_sha=MERGE_SHA)
            return SimpleNamespace(returncode=self.merge_returncode, stdout='', stderr='')
        if args[:2] == ['pr', 'edit'] and '--add-label' in args:
            label = args[args.index('--add-label') + 1]
            self.github.pulls[int(args[2])]['labels'].append({'name': label})
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    def named(self, verb):
        return [call for call in self.calls if call[:2] == ['pr', verb]]


class FakeStore:
    def __init__(self, records):
        self.records = records

    def get(self, task_id):
        return copy.deepcopy(self.records[task_id])


class MergeTrainOpsTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if shutil.which('git') is None:
            raise unittest.SkipTest('git is required')
        cls._repo_dir = tempfile.TemporaryDirectory(prefix='merge-train-ops-')
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
        tree = _git(root, 'rev-parse', built['head'] + '^{tree}')
        cls.root = str(root)
        cls.main = main
        cls.head_a, cls.head_b = heads
        cls.train_head = built['head']
        cls.extra_head = _git(root, 'commit-tree', tree, '-p', built['head'], '-m', 'extra')

    @classmethod
    def tearDownClass(cls):
        cls._repo_dir.cleanup()

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='merge-train-root-')
        self.addCleanup(directory.cleanup)
        self.train_root = str(Path(directory.name) / 'tasks')
        self.github = FakeGitHub()
        self.gh = FakeGh(self.github)
        self.store = FakeStore({TASK_A: _task(TASK_A, PR_A, self.head_a),
                                TASK_B: _task(TASK_B, PR_B, self.head_b)})
        self.github.pulls[PR_A] = _pull(PR_A, self.head_a)
        self.github.pulls[PR_B] = _pull(PR_B, self.head_b)
        self._set_train_head(self.train_head)
        patcher = mock.patch.object(task_completion, 'read_authenticated_main_sha',
                                    return_value=self.main)
        self.read_main = patcher.start()
        self.addCleanup(patcher.stop)

    def _set_train_head(self, head):
        self.github.pulls[TRAIN_PR] = _pull(TRAIN_PR, head)
        self.github.files[TRAIN_PR] = [{'filename': 'tools/llmtool/llmtool/example.py'}]
        self.github.checks[head] = [_run(LINUX, head), _run(MACOS, head)]

    def _create(self, head=None, train_pr=TRAIN_PR):
        record = {
            'schema_version': 1, 'train_id': TRAIN_ID, 'main_sha': self.main,
            'members': [_member(TASK_A, PR_A, self.head_a), _member(TASK_B, PR_B, self.head_b)],
            'excluded': [], 'train_branch': 'llmtool/train-' + TRAIN_ID,
            'train_head': head or self.train_head, 'train_pr': train_pr, 'state': 'BUILT',
            'merge_attempt': None, 'merge_sha': None, 'created_at': '2026-11-08T00:00:00Z'}
        return merge_train_store.create(record, root=self.train_root)

    def _record(self):
        return merge_train_store.get(TRAIN_ID, root=self.train_root)

    def _merge(self):
        return merge_train_ops.merge_train(
            TRAIN_ID, store=self.store, github_get=self.github, gh_runner=self.gh,
            root=self.root, train_root=self.train_root)

    def _close(self, advance_call, gh_runner=None, github_get=None):
        return merge_train_ops.close_train(
            TRAIN_ID, store=self.store, advance_call=advance_call,
            github_get=self.github if github_get is None else github_get,
            gh_runner=self.gh if gh_runner is None else gh_runner, root=self.root,
            train_root=self.train_root)

    def _assert_refused(self, result, code, state='BUILT'):
        self.assertEqual(result['step'], 'MERGE_REFUSED', result)
        self.assertEqual(result['codes'], [code])
        self.assertEqual(self.gh.named('merge'), [])
        self.assertEqual(self._record()['state'], state)

    # merge_train

    def test_clean_merge(self):
        self._create()
        result = self._merge()
        self.assertEqual(result['step'], 'MERGED', result)
        self.assertEqual(result['codes'], [])
        self.assertEqual(self.gh.named('merge'), [[
            'pr', 'merge', str(TRAIN_PR), '--squash', '--match-head-commit', self.train_head]])
        record = self._record()
        self.assertEqual(record['state'], 'MERGED')
        self.assertEqual(record['merge_sha'], MERGE_SHA)
        self.assertEqual(record['merge_attempt']['state'], 'MERGED')
        self.assertEqual(record['merge_attempt']['pr'], TRAIN_PR)
        self.assertEqual(record['merge_attempt']['head_sha'], self.train_head)
        comments = self.gh.named('comment')
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0][:3], ['pr', 'comment', str(TRAIN_PR)])
        body = comments[0][-1]
        for text in (TRAIN_ID, self.train_head, TASK_A, TASK_B, 'pr=101', 'pr=102',
                     self.head_a, self.head_b, 'review_version=1'):
            self.assertIn(text, body)
        # Member-head checks are never read.
        for head in (self.head_a, self.head_b):
            self.assertFalse(any(path.startswith('commits/' + head) for path in self.github.reads))

    def test_not_mergeable_without_train_pr(self):
        self._create(train_pr=None)
        self._assert_refused(self._merge(), merge_train_ops.TRAIN_NOT_MERGEABLE)

    def test_main_moved_supersedes(self):
        self._create()
        self.read_main.return_value = OTHER_SHA
        result = self._merge()
        self.assertEqual(result['step'], 'SUPERSEDED', result)
        self.assertEqual(result['codes'], [merge_train_ops.TRAIN_MAIN_MOVED])
        self.assertEqual(self._record()['state'], 'SUPERSEDED')
        self.assertEqual(self.gh.calls, [])
        # A superseded train is never merged afterwards.
        self.read_main.return_value = self.main
        self._assert_refused(self._merge(), merge_train_ops.TRAIN_NOT_MERGEABLE,
                             state='SUPERSEDED')

    def test_main_unavailable_refuses(self):
        self._create()
        self.read_main.side_effect = LLMToolError('main unavailable')
        self._assert_refused(self._merge(), merge_train_ops.TRAIN_MAIN_UNAVAILABLE)
        self.assertEqual(self.gh.calls, [])

    def test_stale_train_pr_refuses(self):
        self._create()
        cases = {'head': {'head': {'sha': OTHER_SHA}}, 'not_mergeable': {'mergeable': False},
                 'mergeable_unknown': {'mergeable': None}, 'closed': {'state': 'closed'},
                 'merged_elsewhere': {'merged': True, 'state': 'closed',
                                      'head': {'sha': OTHER_SHA}}}
        for name, change in cases.items():
            with self.subTest(name):
                self.github.pulls[TRAIN_PR] = dict(_pull(TRAIN_PR, self.train_head), **change)
                self._assert_refused(self._merge(), merge_train_ops.TRAIN_PR_STALE)

    def test_extra_commit_refuses_delta(self):
        self._create(head=self.extra_head)
        self._set_train_head(self.extra_head)
        self._assert_refused(self._merge(), merge_train_ops.TRAIN_DELTA_UNVERIFIED)
        self.assertEqual(self.gh.calls, [])

    def test_member_with_newer_review_claim_refuses(self):
        self._create()
        self.store.records[TASK_B]['review_dispatch_claim'].append({
            'pr': PR_B, 'head_sha': self.head_b, 'state': 'DISPATCHED', 'version': 2})
        result = self._merge()
        self._assert_refused(result, merge_train_ops.TRAIN_MEMBER_CHANGED)
        self.assertEqual(result['result']['task_id'], TASK_B)

    def test_member_with_pushed_head_refuses(self):
        self._create()
        self.github.pulls[PR_A]['head']['sha'] = OTHER_SHA
        result = self._merge()
        self._assert_refused(result, merge_train_ops.TRAIN_MEMBER_CHANGED)
        self.assertEqual(result['result']['task_id'], TASK_A)

    def test_member_with_merge_attempt_or_closed_pr_refuses(self):
        self._create()
        self.store.records[TASK_A]['merge_attempt'] = {
            'pr': PR_A, 'head_sha': self.head_a, 'state': 'UNKNOWN', 'claimed_at': 'x'}
        result = self._merge()
        self._assert_refused(result, merge_train_ops.TRAIN_MEMBER_CHANGED)
        self.assertEqual(result['result']['task_id'], TASK_A)
        self.store.records[TASK_A]['merge_attempt'] = None
        self.github.pulls[PR_B]['state'] = 'closed'
        result = self._merge()
        self._assert_refused(result, merge_train_ops.TRAIN_MEMBER_CHANGED)
        self.assertEqual(result['result']['task_id'], TASK_B)

    def test_missing_macos_adds_mac_ci_once(self):
        self._create()
        self.github.checks[self.train_head] = [_run(LINUX, self.train_head)]
        result = self._merge()
        self.assertEqual(result['step'], 'WAITING_CI', result)
        self.assertEqual(self.gh.named('edit'), [
            ['pr', 'edit', str(TRAIN_PR), '--add-label', 'mac-ci']])
        self.assertEqual(self._record()['state'], 'BUILT')
        # With the label present a still-missing check is refused, not relabelled.
        self._assert_refused(self._merge(), 'MERGE_REQUIRED_CHECK_MISSING')
        self.assertEqual(len(self.gh.named('edit')), 1)

    def test_skipped_macos_adds_mac_ci(self):
        self._create()
        self.github.checks[self.train_head] = [
            _run(LINUX, self.train_head), _run(MACOS, self.train_head, 'skipped')]
        self.assertEqual(self._merge()['step'], 'WAITING_CI')
        self.assertEqual(len(self.gh.named('edit')), 1)
        self._assert_refused(self._merge(), 'MERGE_MACOS_CHECK_REQUIRED')
        self.assertEqual(len(self.gh.named('edit')), 1)

    def test_cancelled_or_skipped_linux_refuses(self):
        self._create()
        for conclusion, code in (('cancelled', 'MERGE_CHECKS_NOT_GREEN'),
                                 ('skipped', 'MERGE_REQUIRED_CHECK_SKIPPED')):
            with self.subTest(conclusion):
                self.github.checks[self.train_head] = [
                    _run(LINUX, self.train_head, conclusion), _run(MACOS, self.train_head)]
                self._assert_refused(self._merge(), code)
                self.assertEqual(self.gh.calls, [])

    def test_claim_conflict_refuses_before_merge(self):
        self._create()
        other_claim = {'pr': TRAIN_PR, 'head_sha': self.train_head, 'state': 'CLAIMED',
                       'claimed_at': '2026-11-08T00:00:01Z'}

        def concurrent_claim():
            current = self._record()
            merge_train_store.compare_and_update(
                TRAIN_ID, current['record_sha256'], root=self.train_root,
                state='MERGE_CLAIMED', merge_attempt=other_claim)

        self.github.hooks[f'commits/{self.train_head}/check-runs?per_page=100'] = concurrent_claim
        self._assert_refused(self._merge(), merge_train_ops.TRAIN_CLAIM_CONFLICT,
                             state='MERGE_CLAIMED')
        self.assertEqual(self._record()['merge_attempt'], other_claim)

    def test_ambiguous_merge_becomes_unknown_then_retries(self):
        self._create()
        self.gh.merge_returncode = 1
        self.gh.merge_effect = False
        result = self._merge()
        self.assertEqual(result['step'], 'MERGE_REFUSED', result)
        self.assertEqual(result['codes'], [merge_train_ops.TRAIN_MERGE_OUTCOME_UNKNOWN])
        record = self._record()
        self.assertEqual(record['state'], 'UNKNOWN')
        self.assertEqual(record['merge_attempt']['state'], 'UNKNOWN')
        self.assertIsNone(record['merge_sha'])
        self.assertEqual(self.gh.named('comment'), [])
        first_claim = record['merge_attempt']['claimed_at']
        # UNKNOWN with the PR still open retries the full gate and merges once more.
        self.gh.merge_returncode = 0
        self.gh.merge_effect = True
        self.assertEqual(self._merge()['step'], 'MERGED')
        record = self._record()
        self.assertEqual(record['state'], 'MERGED')
        self.assertNotEqual(record['merge_attempt']['claimed_at'], first_claim)
        self.assertEqual(len(self.gh.named('merge')), 2)

    def test_successful_command_without_merged_read_is_unknown(self):
        self._create()
        self.gh.merge_effect = False
        result = self._merge()
        self.assertEqual(result['codes'], [merge_train_ops.TRAIN_MERGE_OUTCOME_UNKNOWN])
        self.assertEqual(self._record()['state'], 'UNKNOWN')

    def test_failed_command_with_merged_read_is_merged(self):
        self._create()
        self.gh.merge_returncode = 1
        result = self._merge()
        self.assertEqual(result['step'], 'MERGED', result)
        self.assertEqual(self._record()['merge_sha'], MERGE_SHA)
        self.assertEqual(len(self.gh.named('comment')), 1)

    def test_unknown_resolved_from_merged_read_without_new_merge(self):
        self._create()
        self.gh.merge_effect = False
        self._merge()
        self.assertEqual(self._record()['state'], 'UNKNOWN')
        # The merge landed later; main now contains it and must not supersede.
        self.github.pulls[TRAIN_PR].update(state='closed', merged=True,
                                           merge_commit_sha=MERGE_SHA)
        self.read_main.return_value = MERGE_SHA
        result = self._merge()
        self.assertEqual(result['step'], 'MERGED', result)
        record = self._record()
        self.assertEqual(record['state'], 'MERGED')
        self.assertEqual(record['merge_sha'], MERGE_SHA)
        self.assertEqual(len(self.gh.named('merge')), 1)
        self.assertEqual(len(self.gh.named('comment')), 1)

    def test_exit_zero_merge_unconfirmed_then_settled_without_second_merge(self):
        self._create()
        # The merge command exits 0, but the next PR read still shows the PR open.
        self.gh.merge_returncode = 0
        self.gh.merge_effect = False
        result = self._merge()
        self.assertEqual(result['step'], 'MERGE_REFUSED', result)
        self.assertEqual(result['codes'], [merge_train_ops.TRAIN_MERGE_OUTCOME_UNKNOWN])
        record = self._record()
        self.assertEqual(record['state'], 'UNKNOWN')
        self.assertEqual(record['merge_attempt']['state'], 'UNKNOWN')
        self.assertIsNone(record['merge_sha'])
        self.assertEqual(self.gh.named('comment'), [])
        self.assertEqual(len(self.gh.named('merge')), 1)
        claimed_at = record['merge_attempt']['claimed_at']
        # A later fresh read shows the PR merged at train_head with a merge commit.
        settled_sha = 'f' * 40
        self.github.pulls[TRAIN_PR].update(state='closed', merged=True, mergeable=None,
                                           merge_commit_sha=settled_sha)
        result = self._merge()
        self.assertEqual(result['step'], 'MERGED', result)
        self.assertEqual(result['codes'], [])
        self.assertEqual(result['result']['merge_sha'], settled_sha)
        record = self._record()
        self.assertEqual(record['state'], 'MERGED')
        self.assertEqual(record['merge_sha'], settled_sha)
        self.assertEqual(record['merge_attempt']['state'], 'MERGED')
        self.assertEqual(record['merge_attempt']['claimed_at'], claimed_at)
        comments = self.gh.named('comment')
        self.assertEqual(len(comments), 1)
        self.assertIn(settled_sha, comments[0][-1])
        # The merge command ran exactly once in total.
        self.assertEqual(self.gh.named('merge'), [[
            'pr', 'merge', str(TRAIN_PR), '--squash', '--match-head-commit', self.train_head]])

    def _fail_writes(self, state, count):
        """Patch the store so the first ``count`` writes to ``state`` fail; record each try."""
        real = merge_train_store.compare_and_update
        attempts = []

        def update(train_id, expected_sha256, root=None, **changes):
            if changes.get('state') == state:
                attempts.append(changes)
                if len(attempts) <= count:
                    raise LLMToolError('TRAIN_RECORD_UNAVAILABLE')
            return real(train_id, expected_sha256, root=root, **changes)

        patcher = mock.patch.object(merge_train_store, 'compare_and_update', side_effect=update)
        return patcher, attempts

    def _fail_unknown_writes(self, count):
        return self._fail_writes('UNKNOWN', count)

    def _leave_claimed(self):
        """Run one ambiguous merge whose UNKNOWN write fails twice."""
        self._create()
        self.gh.merge_effect = False
        patcher, attempts = self._fail_unknown_writes(2)
        with patcher:
            result = self._merge()
        self.assertEqual(result['codes'], [merge_train_ops.TRAIN_MERGE_OUTCOME_UNKNOWN])
        self.assertEqual(len(attempts), 2)
        record = self._record()
        self.assertEqual(record['state'], 'MERGE_CLAIMED')
        self.assertEqual(len(self.gh.named('merge')), 1)
        return record

    def test_failed_unknown_write_is_retried_once(self):
        self._create()
        self.gh.merge_effect = False
        patcher, attempts = self._fail_unknown_writes(1)
        with patcher:
            result = self._merge()
        self.assertEqual(result['codes'], [merge_train_ops.TRAIN_MERGE_OUTCOME_UNKNOWN])
        self.assertEqual(len(attempts), 2)
        record = self._record()
        self.assertEqual(record['state'], 'UNKNOWN')
        self.assertEqual(record['merge_attempt']['state'], 'UNKNOWN')

    def test_claimed_record_moves_to_unknown_without_merge(self):
        claimed = self._leave_claimed()
        main_reads = self.read_main.call_count
        # Recovery whose UNKNOWN write fails again leaves MERGE_CLAIMED and never merges.
        patcher, attempts = self._fail_unknown_writes(2)
        with patcher:
            result = self._merge()
        self.assertEqual(result['step'], 'MERGE_REFUSED', result)
        self.assertEqual(result['codes'], [merge_train_ops.TRAIN_MERGE_OUTCOME_UNKNOWN])
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self._record()['state'], 'MERGE_CLAIMED')
        self.assertEqual(len(self.gh.named('merge')), 1)
        # A fresh read that does not show the merge records UNKNOWN, still without a merge.
        result = self._merge()
        self.assertEqual(result['step'], 'MERGE_REFUSED', result)
        self.assertEqual(result['codes'], [merge_train_ops.TRAIN_MERGE_OUTCOME_UNKNOWN])
        record = self._record()
        self.assertEqual(record['state'], 'UNKNOWN')
        self.assertEqual(record['merge_attempt'],
                         dict(claimed['merge_attempt'], state='UNKNOWN'))
        self.assertIsNone(record['merge_sha'])
        self.assertEqual(len(self.gh.named('merge')), 1)
        self.assertEqual(self.gh.named('comment'), [])
        self.assertEqual(self.read_main.call_count, main_reads)
        # A later call merges only after the full recheck.
        self.gh.merge_effect = True
        self.assertEqual(self._merge()['step'], 'MERGED')
        self.assertEqual(self._record()['state'], 'MERGED')
        self.assertEqual(len(self.gh.named('merge')), 2)
        # Main is read before the gates and again right before the claim.
        self.assertEqual(self.read_main.call_count, main_reads + 2)

    def test_claimed_record_resolved_from_merged_read_without_new_merge(self):
        self._leave_claimed()
        # The merge landed later; main now contains it.
        self.github.pulls[TRAIN_PR].update(state='closed', merged=True,
                                           merge_commit_sha=MERGE_SHA)
        self.read_main.return_value = MERGE_SHA
        result = self._merge()
        self.assertEqual(result['step'], 'MERGED', result)
        self.assertEqual(result['codes'], [])
        record = self._record()
        self.assertEqual(record['state'], 'MERGED')
        self.assertEqual(record['merge_sha'], MERGE_SHA)
        self.assertEqual(record['merge_attempt']['state'], 'MERGED')
        self.assertEqual(len(self.gh.named('merge')), 1)
        self.assertEqual(len(self.gh.named('comment')), 1)

    # High Debug finding 1: the provenance comment follows a durable MERGED record.

    def _mark_train_merged(self):
        self.github.pulls[TRAIN_PR].update(state='closed', merged=True, mergeable=None,
                                           merge_commit_sha=MERGE_SHA)
        self.read_main.return_value = MERGE_SHA

    def _assert_record_failed(self, result):
        self.assertEqual(result['step'], 'MERGED', result)
        self.assertEqual(result['codes'], [merge_train_ops.TRAIN_RESULT_RECORD_FAILED])
        self.assertEqual(result['result'], {'train_id': TRAIN_ID, 'pr': TRAIN_PR,
                                            'head': self.train_head, 'merge_sha': MERGE_SHA})
        self.assertEqual(self.gh.named('comment'), [])
        self.assertIsNone(self._record()['merge_sha'])

    def _assert_recovers_with_one_comment(self):
        merges = len(self.gh.named('merge'))
        result = self._merge()
        self.assertEqual(result['step'], 'MERGED', result)
        self.assertEqual(result['codes'], [])
        record = self._record()
        self.assertEqual(record['state'], 'MERGED')
        self.assertEqual(record['merge_sha'], MERGE_SHA)
        self.assertEqual(record['merge_attempt']['state'], 'MERGED')
        self.assertEqual(len(self.gh.named('comment')), 1)
        self.assertEqual(len(self.gh.named('merge')), merges)
        # A MERGED train is never merged or commented on again.
        result = self._merge()
        self.assertEqual(result['step'], 'MERGE_REFUSED', result)
        self.assertEqual(result['codes'], [merge_train_ops.TRAIN_NOT_MERGEABLE])
        self.assertEqual(self._record()['state'], 'MERGED')
        self.assertEqual(len(self.gh.named('comment')), 1)
        self.assertEqual(len(self.gh.named('merge')), merges)

    def test_failed_merged_writes_from_built_post_no_comment(self):
        self._create()
        patcher, attempts = self._fail_writes('MERGED', 2)
        with patcher:
            result = self._merge()
        self._assert_record_failed(result)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self._record()['state'], 'MERGE_CLAIMED')
        self.assertEqual(len(self.gh.named('merge')), 1)
        self._assert_recovers_with_one_comment()

    def test_failed_merged_writes_from_unknown_post_no_comment(self):
        self._create()
        self.gh.merge_effect = False
        self._merge()
        self.assertEqual(self._record()['state'], 'UNKNOWN')
        self._mark_train_merged()
        patcher, attempts = self._fail_writes('MERGED', 2)
        with patcher:
            result = self._merge()
        self._assert_record_failed(result)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self._record()['state'], 'UNKNOWN')
        self.assertEqual(len(self.gh.named('merge')), 1)
        self._assert_recovers_with_one_comment()

    def test_failed_merged_writes_from_claimed_post_no_comment(self):
        self._leave_claimed()
        self._mark_train_merged()
        patcher, attempts = self._fail_writes('MERGED', 2)
        with patcher:
            result = self._merge()
        self._assert_record_failed(result)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self._record()['state'], 'MERGE_CLAIMED')
        self.assertEqual(len(self.gh.named('merge')), 1)
        self._assert_recovers_with_one_comment()

    def test_first_merged_write_fails_second_succeeds_posts_one_comment(self):
        self._create()
        patcher, attempts = self._fail_writes('MERGED', 1)
        with patcher:
            result = self._merge()
        self.assertEqual(result['step'], 'MERGED', result)
        self.assertEqual(result['codes'], [])
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self._record()['state'], 'MERGED')
        self.assertEqual(len(self.gh.named('comment')), 1)
        self.assertEqual(len(self.gh.named('merge')), 1)

    # High Debug finding 2: one merge_train call per train at a time.

    def _operation_lock_path(self):
        directory = Path(self.train_root).parent / merge_train_store.DIRECTORY_NAME
        return directory / merge_train_ops._operation_lock_name(TRAIN_ID)

    def _hold_operation_lock(self):
        fd = os.open(self._operation_lock_path(), os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd

    def _operation_lock_free(self):
        fd = os.open(self._operation_lock_path(), os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        finally:
            os.close(fd)
        return True

    def test_held_operation_lock_refuses_without_side_effect(self):
        self._create()
        fd = self._hold_operation_lock()
        self._assert_refused(self._merge(), merge_train_ops.TRAIN_OPERATION_BUSY)
        self.assertEqual(self.gh.calls, [])
        self.assertEqual(self.github.reads, [])
        self.read_main.assert_not_called()
        fcntl.flock(fd, fcntl.LOCK_UN)
        self.assertEqual(self._merge()['step'], 'MERGED')
        # The dot-file lock is not a train record.
        self.assertEqual(
            merge_train_store.find_by_pr(TRAIN_PR, root=self.train_root)['train_id'], TRAIN_ID)

    def test_live_claim_is_not_recovered_while_lock_held(self):
        self._leave_claimed()
        reads = len(self.github.reads)
        self._hold_operation_lock()
        result = self._merge()
        self.assertEqual(result['step'], 'MERGE_REFUSED', result)
        self.assertEqual(result['codes'], [merge_train_ops.TRAIN_OPERATION_BUSY])
        self.assertEqual(self._record()['state'], 'MERGE_CLAIMED')
        self.assertEqual(len(self.github.reads), reads)
        self.assertEqual(len(self.gh.named('merge')), 1)

    def test_operation_lock_held_for_the_call_and_released(self):
        self._create()
        seen = []
        self.github.hooks[f'commits/{self.train_head}/check-runs?per_page=100'] = (
            lambda: seen.append(self._operation_lock_free()))
        self.assertEqual(self._merge()['step'], 'MERGED')
        self.assertEqual(seen, [False])
        self.assertTrue(self._operation_lock_free())

    # High Debug finding 3: authenticated main is read again right before the claim.

    def test_main_moved_during_ci_read_supersedes_without_claim(self):
        self._create()

        def move_main():
            self.read_main.return_value = OTHER_SHA

        self.github.hooks[f'commits/{self.train_head}/check-runs?per_page=100'] = move_main
        result = self._merge()
        self.assertEqual(result['step'], 'SUPERSEDED', result)
        self.assertEqual(result['codes'], [merge_train_ops.TRAIN_MAIN_MOVED])
        record = self._record()
        self.assertEqual(record['state'], 'SUPERSEDED')
        self.assertIsNone(record['merge_attempt'])
        self.assertEqual(self.gh.calls, [])
        self.assertEqual(self.read_main.call_count, 2)

    def test_main_unavailable_before_claim_refuses(self):
        self._create()

        def lose_main():
            self.read_main.side_effect = LLMToolError('main unavailable')

        self.github.hooks[f'commits/{self.train_head}/check-runs?per_page=100'] = lose_main
        self._assert_refused(self._merge(), merge_train_ops.TRAIN_MAIN_UNAVAILABLE)
        self.assertIsNone(self._record()['merge_attempt'])
        self.assertEqual(self.gh.calls, [])

    # close_train

    def _merged_train(self):
        self._create()
        self.assertEqual(self._merge()['step'], 'MERGED')

    def test_close_all_members_closed(self):
        self._merged_train()
        calls = []

        def advance_call(**kwargs):
            calls.append(kwargs)
            return {'step': 'CLOSED', 'codes': []}

        result = self._close(advance_call)
        self.assertEqual(result['step'], 'CLOSED', result)
        self.assertEqual(calls, [
            {'task_id': TASK_A, 'pr_number': PR_A, 'integration_pr': TRAIN_PR},
            {'task_id': TASK_B, 'pr_number': PR_B, 'integration_pr': TRAIN_PR}])
        self.assertEqual(self._record()['state'], 'CLOSED')

    def test_close_with_one_member_open(self):
        self._merged_train()
        calls = []

        def advance_call(**kwargs):
            calls.append(kwargs['task_id'])
            if kwargs['task_id'] == TASK_B:
                return {'step': 'READY_FOR_INTEGRATION', 'codes': []}
            return {'step': 'CLOSED', 'codes': []}

        result = self._close(advance_call)
        self.assertEqual(result['codes'], [merge_train_ops.TRAIN_MEMBERS_OPEN])
        self.assertEqual(result['result']['open'], [TASK_B])
        self.assertEqual(calls, [TASK_A, TASK_B])
        self.assertEqual(self._record()['state'], 'MERGED')

    def test_close_member_error_counts_as_open(self):
        self._merged_train()

        def advance_call(**kwargs):
            if kwargs['task_id'] == TASK_A:
                raise RuntimeError('advance failed')
            return {'step': 'CLOSED'}

        result = self._close(advance_call)
        self.assertEqual(result['codes'], [merge_train_ops.TRAIN_MEMBERS_OPEN])
        self.assertEqual(result['result']['open'], [TASK_A])
        self.assertEqual(self._record()['state'], 'MERGED')

    def test_close_refuses_unless_merged(self):
        self._create()
        calls = []
        result = self._close(lambda **kwargs: calls.append(kwargs))
        self.assertEqual(result['step'], 'CLOSE_REFUSED')
        self.assertEqual(result['codes'], [merge_train_ops.TRAIN_NOT_MERGED])
        self.assertEqual(calls, [])
        self.assertEqual(self._record()['state'], 'BUILT')

    # A member with a null receipt digest (docs-only or scripts-only PR)

    def _create_null_digest(self):
        record = {
            'schema_version': 1, 'train_id': TRAIN_ID, 'main_sha': self.main,
            'members': [dict(_member(TASK_A, PR_A, self.head_a),
                             remote_test_receipt_digest=None),
                        _member(TASK_B, PR_B, self.head_b)],
            'excluded': [], 'train_branch': 'llmtool/train-' + TRAIN_ID,
            'train_head': self.train_head, 'train_pr': TRAIN_PR, 'state': 'BUILT',
            'merge_attempt': None, 'merge_sha': None, 'created_at': '2026-11-08T00:00:00Z'}
        return merge_train_store.create(record, root=self.train_root)

    def test_member_unchanged_treats_null_digest_like_any_member(self):
        member = _member(TASK_A, PR_A, self.head_a)
        null_member = dict(member, remote_test_receipt_digest=None)
        self.assertTrue(merge_train_ops._member_unchanged(member, self.store, self.github))
        self.assertTrue(merge_train_ops._member_unchanged(null_member, self.store,
                                                          self.github))
        self.github.pulls[PR_A]['head']['sha'] = OTHER_SHA
        self.assertFalse(merge_train_ops._member_unchanged(member, self.store, self.github))
        self.assertFalse(merge_train_ops._member_unchanged(null_member, self.store,
                                                           self.github))

    def test_train_with_null_digest_member_merges(self):
        self._create_null_digest()
        result = self._merge()
        self.assertEqual(result['step'], 'MERGED', result)
        record = self._record()
        self.assertEqual(record['state'], 'MERGED')
        self.assertIsNone(record['members'][0]['remote_test_receipt_digest'])

    # close_train closes still-open member PRs after the record is CLOSED

    def _all_closed(self, **kwargs):
        return {'step': 'CLOSED', 'codes': []}

    def _expected_comment(self):
        return (f'Closed by llmtool merge train {TRAIN_ID}: this change was merged in '
                f'train PR #{TRAIN_PR} as squash commit {MERGE_SHA}.')

    def test_close_closes_only_open_member_prs_with_fixed_comment(self):
        self._merged_train()
        # PR_B was merged on its own already; it is left alone.
        self.github.pulls[PR_B].update(state='closed', merged=True)
        states = []

        def runner(root, args):
            # The record is already CLOSED when a member PR is closed.
            states.append(self._record()['state'])
            return self.gh(root, args)

        result = self._close(self._all_closed, gh_runner=runner)
        self.assertEqual(result['step'], 'CLOSED', result)
        self.assertEqual(result['codes'], [])
        self.assertEqual(result['result'], {
            'train_id': TRAIN_ID, 'tasks': [TASK_A, TASK_B],
            'closed_prs': [PR_A], 'failed_prs': []})
        self.assertEqual(self.gh.named('close'), [
            ['pr', 'close', str(PR_A), '--comment', self._expected_comment()]])
        self.assertEqual(states, ['CLOSED'])
        self.assertEqual(self._record()['state'], 'CLOSED')
        comment = self.gh.named('close')[0][-1]
        for text in (TRAIN_ID, '#' + str(TRAIN_PR), MERGE_SHA):
            self.assertIn(text, comment)

    def test_close_closes_every_open_member_pr_once(self):
        self._merged_train()
        result = self._close(self._all_closed)
        self.assertEqual(result['codes'], [])
        self.assertEqual(result['result']['closed_prs'], [PR_A, PR_B])
        self.assertEqual(self.gh.named('close'), [
            ['pr', 'close', str(PR_A), '--comment', self._expected_comment()],
            ['pr', 'close', str(PR_B), '--comment', self._expected_comment()]])
        # The merge and its provenance comment are not repeated.
        self.assertEqual(len(self.gh.named('merge')), 1)
        self.assertEqual(len(self.gh.named('comment')), 1)

    def test_close_leaves_closed_or_merged_member_prs_alone(self):
        self._merged_train()
        self.github.pulls[PR_A].update(state='closed', merged=False)
        self.github.pulls[PR_B].update(state='closed', merged=True)
        result = self._close(self._all_closed)
        self.assertEqual(result['step'], 'CLOSED', result)
        self.assertEqual(result['codes'], [])
        self.assertEqual(result['result']['closed_prs'], [])
        self.assertEqual(result['result']['failed_prs'], [])
        self.assertEqual(self.gh.named('close'), [])

    def test_close_failed_pr_close_keeps_closed(self):
        self._merged_train()
        self.gh.close_fail.add(PR_A)
        result = self._close(self._all_closed)
        self.assertEqual(result['step'], 'CLOSED', result)
        self.assertEqual(result['codes'], [merge_train_ops.TRAIN_MEMBER_PR_CLOSE_FAILED])
        self.assertEqual(result['result']['closed_prs'], [PR_B])
        self.assertEqual(result['result']['failed_prs'], [PR_A])
        self.assertEqual(self._record()['state'], 'CLOSED')
        # One close per open PR, never a retry.
        self.assertEqual([call[2] for call in self.gh.named('close')],
                         [str(PR_A), str(PR_B)])

    def test_close_raising_runner_is_reported_per_pr(self):
        self._merged_train()
        self.gh.close_raise.add(PR_B)
        result = self._close(self._all_closed)
        self.assertEqual(result['step'], 'CLOSED', result)
        self.assertEqual(result['codes'], [merge_train_ops.TRAIN_MEMBER_PR_CLOSE_FAILED])
        self.assertEqual(result['result']['closed_prs'], [PR_A])
        self.assertEqual(result['result']['failed_prs'], [PR_B])
        self.assertEqual(self._record()['state'], 'CLOSED')

    def test_close_failed_pr_read_is_reported_per_pr(self):
        self._merged_train()
        del self.github.pulls[PR_A]
        self.github.pulls[PR_B]['merged'] = 'no'
        result = self._close(self._all_closed)
        self.assertEqual(result['step'], 'CLOSED', result)
        self.assertEqual(result['codes'], [merge_train_ops.TRAIN_MEMBER_PR_CLOSE_FAILED])
        self.assertEqual(result['result']['closed_prs'], [])
        self.assertEqual(result['result']['failed_prs'], [PR_A, PR_B])
        self.assertEqual(self.gh.named('close'), [])
        self.assertEqual(self._record()['state'], 'CLOSED')

    def test_close_unrecognized_pr_state_is_reported_per_pr(self):
        self._merged_train()
        # A state other than open or closed is not proof the PR is done.
        self.github.pulls[PR_A].update(state='draft', merged=False)
        result = self._close(self._all_closed)
        self.assertEqual(result['step'], 'CLOSED', result)
        self.assertEqual(result['codes'], [merge_train_ops.TRAIN_MEMBER_PR_CLOSE_FAILED])
        self.assertEqual(result['result']['closed_prs'], [PR_B])
        self.assertEqual(result['result']['failed_prs'], [PR_A])
        # Only the open PR gets a close command.
        self.assertEqual(self.gh.named('close'), [
            ['pr', 'close', str(PR_B), '--comment', self._expected_comment()]])
        self.assertEqual(self._record()['state'], 'CLOSED')

    def test_close_empty_pr_state_is_reported_per_pr(self):
        self._merged_train()
        self.github.pulls[PR_A].update(state='', merged=False)
        self.github.pulls[PR_B].update(state='OPEN', merged=False)
        result = self._close(self._all_closed)
        self.assertEqual(result['step'], 'CLOSED', result)
        self.assertEqual(result['codes'], [merge_train_ops.TRAIN_MEMBER_PR_CLOSE_FAILED])
        self.assertEqual(result['result']['closed_prs'], [])
        self.assertEqual(result['result']['failed_prs'], [PR_A, PR_B])
        self.assertEqual(self.gh.named('close'), [])
        self.assertEqual(self._record()['state'], 'CLOSED')

    def _member_reads(self):
        return [path for path in self.github.reads if path in (f'pulls/{PR_A}', f'pulls/{PR_B}')]

    def test_close_waiting_members_touches_no_pr(self):
        self._merged_train()
        reads = self._member_reads()
        calls = list(self.gh.calls)

        def advance_call(**kwargs):
            if kwargs['task_id'] == TASK_B:
                return {'step': 'READY_FOR_INTEGRATION', 'codes': []}
            return {'step': 'CLOSED', 'codes': []}

        result = self._close(advance_call)
        self.assertEqual(result['step'], 'WAITING_MEMBERS', result)
        self.assertEqual(self.gh.calls, calls)
        self.assertEqual(self._member_reads(), reads)
        self.assertEqual(self._record()['state'], 'MERGED')

    def test_close_refused_touches_no_pr(self):
        self._create()
        result = self._close(self._all_closed)
        self.assertEqual(result['step'], 'CLOSE_REFUSED', result)
        self.assertEqual(self.gh.calls, [])
        self.assertEqual(self.github.reads, [])

    def test_close_record_write_failure_touches_no_pr(self):
        self._merged_train()
        calls = list(self.gh.calls)
        reads = self._member_reads()
        patcher, attempts = self._fail_writes('CLOSED', 1)
        with patcher:
            result = self._close(self._all_closed)
        self.assertEqual(result['step'], 'CLOSE_REFUSED', result)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(self.gh.calls, calls)
        self.assertEqual(self._member_reads(), reads)
        self.assertEqual(self._record()['state'], 'MERGED')


if __name__ == '__main__':
    unittest.main()
