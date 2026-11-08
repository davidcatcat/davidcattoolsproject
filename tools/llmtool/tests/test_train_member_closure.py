"""#1182 T4b1: task advance --integration-pr closes a member of a merged merge train.

Real Git repository and real merge-train records in temporary directories; only
the GitHub PR snapshots are fakes.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool import merge_train
from llmtool import merge_train_store
from llmtool import task_advance
from llmtool.errors import LLMToolError


TASK_A = 'a1' * 16
TASK_B = 'b2' * 16
TASK_C = 'c3' * 16
TRAIN_PR = 541
MISSING_SHA = 'f' * 40

_GIT_ENV = dict(
    os.environ, GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1',
    GIT_AUTHOR_NAME='test', GIT_AUTHOR_EMAIL='test@invalid',
    GIT_COMMITTER_NAME='test', GIT_COMMITTER_EMAIL='test@invalid',
    GIT_AUTHOR_DATE='2026-11-08T00:00:00Z', GIT_COMMITTER_DATE='2026-11-08T00:00:00Z')


def _executor_task():
    for value in ('train-t4b1-1182-20261108', 'executor-1', 'llmtool-task-1', 'task-1',
                  'a' * 32):
        if merge_train_store._EXECUTOR_TASK.fullmatch(value):
            return value
    raise AssertionError('no executor_task fixture matches task_store.D6_TASK_ID')


class TrainMemberClosureTests(unittest.TestCase):

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(os.path.realpath(temporary.name))
        self.trains = base / 'store' / 'merge-trains'
        (base / 'store').mkdir()
        # Patch the record root lookup so no real TaskStore root is touched.
        patcher = patch.object(merge_train_store, '_directory_path',
                               lambda _root: self.trains)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.repo = str(base / 'repo')
        os.mkdir(self.repo)
        self.git('init', '-q')
        self.git('checkout', '-q', '-b', 'main')
        self.main = self.commit_file('base.txt', 'base\n')
        self.head_a = self.branch_commit('a', 'a.txt')
        self.head_b = self.branch_commit('b', 'b.txt')
        self.head_other = self.branch_commit('other', 'other.txt')
        built = merge_train.build_candidate(
            self.repo, self.main, [('a', self.head_a), ('b', self.head_b)])
        self.assertEqual(built['merged'], ['a', 'b'])
        self.train_head = built['head']
        self.train_tree = self.git('rev-parse', self.train_head + '^{tree}')
        self.squash = self.git('commit-tree', self.train_tree, '-p', self.main,
                               '-m', 'train squash')

    # Git and record fixtures -------------------------------------------------

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.repo, env=_GIT_ENV, check=True,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE).stdout.decode('utf-8').strip()

    def commit_file(self, name, text):
        Path(self.repo, name).write_text(text, encoding='utf-8')
        self.git('add', name)
        self.git('commit', '-q', '-m', 'add ' + name)
        return self.git('rev-parse', 'HEAD')

    def branch_commit(self, branch, name):
        self.git('checkout', '-q', '-b', branch, self.main)
        head = self.commit_file(name, branch + '\n')
        self.git('checkout', '-q', 'main')
        return head

    def member(self, task_id, pr, head):
        return {'task_id': task_id, 'pr': pr, 'head': head,
                'executor_task': _executor_task(), 'review_version': 1,
                'linux_check_run_id': None, 'macos_check_run_id': None,
                'remote_test_receipt_digest': 'ab' * 8}

    def make_train(self, train_id='t1', *, state='MERGED', train_head=None, merge_sha=None,
                   main_sha=None, members=None):
        train_head = self.train_head if train_head is None else train_head
        merge_sha = self.squash if merge_sha is None else merge_sha
        members = members or [self.member(TASK_A, 17, self.head_a),
                              self.member(TASK_B, 18, self.head_b)]
        record = merge_train_store.create({
            'schema_version': 1, 'train_id': train_id,
            'main_sha': self.main if main_sha is None else main_sha,
            'members': members, 'excluded': [],
            'train_branch': 'llmtool/train-' + train_id, 'train_head': train_head,
            'train_pr': TRAIN_PR, 'state': 'BUILT', 'merge_attempt': None,
            'merge_sha': None, 'created_at': '2026-11-08T00:00:00Z'})
        if state == 'BUILT':
            return record
        attempt = {'pr': TRAIN_PR, 'head_sha': train_head, 'state': 'CLAIMED',
                   'claimed_at': '2026-11-08T00:01:00Z'}
        record = merge_train_store.compare_and_update(
            train_id, record['record_sha256'], state='MERGE_CLAIMED', merge_attempt=attempt)
        if state == 'MERGE_CLAIMED':
            return record
        record = merge_train_store.compare_and_update(
            train_id, record['record_sha256'], state='MERGED',
            merge_attempt=dict(attempt, state='MERGED'), merge_sha=merge_sha)
        if state == 'CLOSED':
            record = merge_train_store.compare_and_update(
                train_id, record['record_sha256'], state='CLOSED')
        return record

    def task(self, task_id=TASK_A, head=None, pr=17):
        head = self.head_a if head is None else head
        return {'task_id': task_id, 'run_id': '2' * 32,
                'completion': {'state': 'PM_EVIDENCE_READY', 'evidence': {
                    'result': {'result_head': head}, 'review_pr_ci': {'stage': 'READY'}}},
                'review_dispatch_claim': [
                    {'pr': pr, 'head_sha': head, 'version': 1, 'state': 'COMPLETED'}],
                'completion_events': [{'source': 'REVIEWER', 'source_sha': head,
                                       'version': 1, 'outcome': 'REVIEW_PASS'}]}

    def integration(self, *, head=None, merge_sha=None, merged=True, number=TRAIN_PR):
        return {'number': number, 'merged': merged,
                'head': {'sha': self.train_head if head is None else head},
                'merge_commit_sha': self.squash if merge_sha is None else merge_sha}

    def target(self, task, integration, *, integration_pr=TRAIN_PR, git_runner=None,
               compare='ahead'):
        head = task['review_dispatch_claim'][0]['head_sha']
        member_pr = {'number': 17, 'merged': False, 'head': {'sha': head}}

        def get(path):
            if path == f'pulls/{integration_pr}':
                return integration
            if path.startswith('compare/'):
                return {'status': compare}
            return None

        return task_advance._merged_review_target(
            task, task['review_dispatch_claim'], task['completion_events'], member_pr,
            integration_pr, get, root=self.repo, git_runner=git_runner)

    def assert_not_closed(self, task, integration, **kwargs):
        with patch.object(task_advance, '_integration_delta_verified',
                          return_value=True) as old_path:
            self.assertIsNone(self.target(task, integration, **kwargs))
        old_path.assert_not_called()

    # Closure -----------------------------------------------------------------

    def test_squash_merged_clean_train_member_closes(self):
        self.make_train()
        with patch.object(task_advance, '_integration_delta_verified') as old_path:
            self.assertEqual(self.target(self.task(), self.integration()),
                             (TRAIN_PR, self.squash))
            self.assertEqual(self.target(self.task(TASK_B, self.head_b, 18),
                                         self.integration()), (TRAIN_PR, self.squash))
        old_path.assert_not_called()

    def test_closed_train_record_also_closes_its_member(self):
        self.make_train(state='CLOSED')
        self.assertEqual(self.target(self.task(), self.integration()), (TRAIN_PR, self.squash))

    def test_advance_records_the_same_github_merge_event(self):
        self.make_train()
        task = self.task()
        member_pr = {'number': 17, 'merged': False, 'head': {'sha': self.head_a}}
        prs = {17: member_pr, TRAIN_PR: self.integration()}
        saved = []

        def get(path):
            if path.startswith('pulls/'):
                return prs.get(int(path.split('/')[1]))
            return []

        store = type('S', (), {'get': lambda _self, _id: task})()
        with patch.object(task_advance, '_review_outcomes', return_value=[]):
            result = task_advance.advance(
                task_id=TASK_A, store=store, pr_number=17, integration_pr=TRAIN_PR,
                github_get=get, cwd=self.repo,
                record_github_event_call=lambda *args: saved.append(args))
        self.assertEqual(result['step'], 'CLOSED')
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0][4], TRAIN_PR)
        self.assertEqual(saved[0][-3:], ('merge', 'MERGED', self.squash))

    # Not closed: record and member binding ----------------------------------

    def test_unlisted_task_does_not_close(self):
        self.make_train()
        self.assert_not_closed(self.task(TASK_C, self.head_a), self.integration())

    def test_another_reviewed_head_does_not_close(self):
        self.make_train()
        self.assert_not_closed(self.task(head=self.head_other), self.integration())
        # The member's own head reviewed under another PR number.
        self.assert_not_closed(self.task(pr=99), self.integration())

    def test_train_not_merged_does_not_close(self):
        for state in ('BUILT', 'MERGE_CLAIMED'):
            with self.subTest(state=state):
                train_id = 't-' + state.lower().replace('_', '-')
                record = self.make_train(train_id, state=state)
                self.assert_not_closed(self.task(), self.integration())
                os.unlink(self.trains / (record['train_id'] + '.json'))

    def test_ambiguous_store_does_not_close(self):
        self.make_train('t1')
        self.make_train('t2', state='BUILT')
        self.assert_not_closed(self.task(), self.integration())

    def test_unreadable_store_does_not_close(self):
        self.make_train()
        (self.trains / 'not a record').write_text('x', encoding='utf-8')
        self.assert_not_closed(self.task(), self.integration())

    # Not closed: integration PR snapshot ------------------------------------

    def test_snapshot_mismatch_does_not_close(self):
        self.make_train()
        cases = {
            'not merged': self.integration(merged=False),
            'other head': self.integration(head=self.head_a),
            'other merge sha': self.integration(merge_sha=self.main),
            'other number': self.integration(number=TRAIN_PR + 1),
            'missing': None,
        }
        for name, integration in cases.items():
            with self.subTest(name):
                self.assert_not_closed(self.task(), integration)

    # Not closed: Git evidence -----------------------------------------------

    def test_squash_with_two_parents_does_not_close(self):
        merge = self.git('commit-tree', self.train_tree, '-p', self.main,
                         '-p', self.train_head, '-m', 'merge commit')
        self.make_train(merge_sha=merge)
        self.assert_not_closed(self.task(), self.integration(merge_sha=merge))

    def test_squash_with_another_tree_does_not_close(self):
        other_tree = self.git('rev-parse', self.head_a + '^{tree}')
        merge = self.git('commit-tree', other_tree, '-p', self.main, '-m', 'partial squash')
        self.make_train(merge_sha=merge)
        self.assert_not_closed(self.task(), self.integration(merge_sha=merge))

    def test_moved_main_parent_does_not_close(self):
        moved = self.git('commit-tree', self.git('rev-parse', self.head_other + '^{tree}'),
                         '-p', self.main, '-m', 'main moved')
        merge = self.git('commit-tree', self.train_tree, '-p', moved, '-m', 'squash on moved')
        self.make_train(merge_sha=merge)
        self.assert_not_closed(self.task(), self.integration(merge_sha=merge))

    def test_extra_train_commit_does_not_close(self):
        tree = self.git('merge-tree', '--write-tree', self.train_head,
                        self.head_other).splitlines()[0]
        extra = self.git('commit-tree', tree, '-p', self.train_head, '-m', 'extra')
        merge = self.git('commit-tree', tree, '-p', self.main, '-m', 'squash with extra')
        self.make_train(train_head=extra, merge_sha=merge)
        self.assert_not_closed(self.task(), self.integration(head=extra, merge_sha=merge))

    def test_missing_commit_fetches_once_then_does_not_close(self):
        self.make_train(merge_sha=MISSING_SHA)
        calls = []

        def runner(root, args):
            calls.append(list(args))
            return task_advance._publish_run_git(root, args)

        self.assert_not_closed(self.task(), self.integration(merge_sha=MISSING_SHA),
                               git_runner=runner)
        fetches = [args for args in calls if args[0] == 'fetch']
        self.assertEqual(fetches, [['fetch', 'origin', 'main', f'refs/pull/{TRAIN_PR}/head']])

    # Non-train integration PR ----------------------------------------------

    def test_non_train_integration_pr_behaves_as_today(self):
        self.make_train()  # recorded for TRAIN_PR only
        integration = self.integration(number=27, head='c' * 40, merge_sha='d' * 40)
        with patch.object(task_advance, '_integration_delta_verified',
                          return_value=True) as old_path:
            self.assertEqual(self.target(self.task(), integration, integration_pr=27),
                             (27, 'd' * 40))
        self.assertEqual(old_path.call_args.args[1:5], (self.head_a, 'c' * 40, 'd' * 40, 27))
        with patch.object(task_advance, '_integration_delta_verified', return_value=False):
            self.assertIsNone(self.target(self.task(), integration, integration_pr=27))
        with patch.object(task_advance, '_integration_delta_verified', return_value=True):
            self.assertIsNone(self.target(self.task(), integration, integration_pr=27,
                                          compare='diverged'))

    def test_no_train_records_keeps_integration_path(self):
        integration = self.integration(number=27, head='c' * 40, merge_sha='d' * 40)
        with patch.object(task_advance, '_integration_delta_verified', return_value=True):
            self.assertEqual(self.target(self.task(), integration, integration_pr=27),
                             (27, 'd' * 40))

    # Advance without --integration-pr: a train already closed the Task ------

    def merge_event(self, *, issue_number=TRAIN_PR, merge_sha=None, status='MERGED'):
        """The GitHub merge event an earlier train closure recorded on the Task."""
        merge_sha = self.squash if merge_sha is None else merge_sha
        return {'source': 'GITHUB', 'source_sha': merge_sha, 'state': 'ACKED',
                'issue_number': issue_number, 'comment_id': None,
                'marker_type': 'merge', 'marker_status': status, 'merge_sha': merge_sha}

    def closed_task(self, task_id=TASK_A, head=None, pr=17, **event):
        task = self.task(task_id, head, pr)
        task['completion_events'].append(self.merge_event(**event))
        return task

    def run_advance(self, task, *, integration_pr=None, integration=None):
        head = task['review_dispatch_claim'][0]['head_sha']
        prs = {17: {'number': 17, 'merged': False, 'state': 'closed',
                    'head': {'sha': head}},
               TRAIN_PR: self.integration() if integration is None else integration}
        saved = []

        def get(path):
            if path.startswith('pulls/') and path[len('pulls/'):].isdigit():
                return prs.get(int(path[len('pulls/'):]))
            return []

        store = type('S', (), {'get': lambda _self, _id: task})()
        with patch.object(task_advance, '_review_outcomes', return_value=[]):
            result = task_advance.advance(
                task_id=task['task_id'], store=store, integration_pr=integration_pr,
                github_get=get, cwd=self.repo,
                record_github_event_call=lambda *args: saved.append(args))
        return result, saved

    def assert_today(self, task, **kwargs):
        """The closed-member check falls through: same step as a Task with no event."""
        plain = self.task(task['task_id'], task['review_dispatch_claim'][0]['head_sha'],
                          task['review_dispatch_claim'][0]['pr'])
        expected, _ = self.run_advance(plain, **kwargs)
        self.assertNotEqual(expected['step'], 'CLOSED')
        result, saved = self.run_advance(task, **kwargs)
        self.assertEqual((result['step'], result['next']), (expected['step'], expected['next']))
        self.assertEqual(saved, [])

    def test_advance_reports_closed_for_a_train_closed_task(self):
        for state in ('MERGED', 'CLOSED'):
            with self.subTest(state=state):
                record = self.make_train('t-' + state.lower(), state=state)
                for task in (self.closed_task(), self.closed_task(TASK_B, self.head_b, 18)):
                    result, saved = self.run_advance(task)
                    self.assertEqual((result['step'], result['next']), ('CLOSED', 'CLOSED'))
                    # No second MERGED event is recorded.
                    self.assertEqual(saved, [])
                os.unlink(self.trains / (record['train_id'] + '.json'))

    def test_closed_member_check_writes_nothing_to_github(self):
        self.make_train()
        runner_calls = []
        task = self.closed_task()
        prs = {17: {'number': 17, 'merged': False, 'head': {'sha': self.head_a}},
               TRAIN_PR: self.integration()}

        def get(path):
            if path.startswith('pulls/') and path[len('pulls/'):].isdigit():
                return prs.get(int(path[len('pulls/'):]))
            return []

        store = type('S', (), {'get': lambda _self, _id: task})()
        result = task_advance.advance(
            task_id=TASK_A, store=store, github_get=get, cwd=self.repo,
            publish_gh_runner=lambda *args: runner_calls.append(args),
            merge_gh_runner=lambda *args: runner_calls.append(args),
            record_github_event_call=lambda *args: runner_calls.append(args))
        self.assertEqual(result['step'], 'CLOSED')
        self.assertEqual(runner_calls, [])

    def test_advance_with_integration_pr_keeps_its_path(self):
        self.make_train()
        with patch.object(task_advance, '_train_closed_member') as closed_member:
            result, saved = self.run_advance(self.closed_task(), integration_pr=TRAIN_PR)
        closed_member.assert_not_called()
        self.assertEqual(result['step'], 'CLOSED')
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0][-3:], ('merge', 'MERGED', self.squash))

    def test_no_merge_event_keeps_todays_step(self):
        self.make_train()
        self.assert_today(self.task())
        self.assert_today(self.closed_task(status='OPEN'))

    def test_no_train_record_keeps_todays_step(self):
        self.make_train()
        self.assert_today(self.closed_task(issue_number=27))

    def test_train_in_another_state_keeps_todays_step(self):
        for state in ('BUILT', 'MERGE_CLAIMED'):
            with self.subTest(state=state):
                record = self.make_train('t-' + state.lower().replace('_', '-'), state=state)
                self.assert_today(self.closed_task())
                os.unlink(self.trains / (record['train_id'] + '.json'))

    def test_task_or_head_not_listed_keeps_todays_step(self):
        self.make_train()
        self.assert_today(self.closed_task(TASK_C, self.head_a))
        self.assert_today(self.closed_task(head=self.head_other))

    def test_other_merge_sha_keeps_todays_step(self):
        self.make_train()
        self.assert_today(self.closed_task(merge_sha=self.main))

    def test_train_proof_failure_keeps_todays_step(self):
        self.make_train()
        self.assert_today(self.closed_task(),
                          integration=self.integration(merged=False))

    def test_store_read_error_does_not_close(self):
        self.make_train()
        (self.trains / 'not a record').write_text('x', encoding='utf-8')
        self.assert_today(self.closed_task())

    def test_store_refusal_does_not_close(self):
        self.make_train()
        refusal = LLMToolError('TRAIN_RECORD_UNAVAILABLE')
        with patch.object(merge_train_store, 'find_by_pr', side_effect=refusal):
            self.assert_today(self.closed_task())


if __name__ == '__main__':
    unittest.main()
