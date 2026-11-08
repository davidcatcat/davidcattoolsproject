"""#1182: a member of a merged merge train reports CLOSED.

T4b1 covers task advance --integration-pr. The later section covers task
advance and task drive without it, for a Task the train already closed.

Real Git repository, real TaskStore and real merge-train records in temporary
directories; only the GitHub PR snapshots and the write adapters are fakes.
"""

import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool import github_events
from llmtool import merge_train
from llmtool import merge_train_store
from llmtool import task_advance
from llmtool import task_drive
from llmtool import task_store
from llmtool import task_submission_cli
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, new_id


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
        self.tasks_root = base / 'tasks'
        # As in production, train records live beside the TaskStore root.
        self.trains = base / 'merge-trains'
        # Point the default root at the temp store so no real root is touched.
        patcher = patch.object(task_store, 'default_root', lambda: self.tasks_root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = TaskStore(self.tasks_root)
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

    # Advance and drive without --integration-pr: a train already closed the Task

    def stored_task(self, head):
        """Create a Task in the real TaskStore whose result head is ``head``."""
        task_id, run_id = new_id(), new_id()
        request = 'Bounded non-sensitive merge train member task.'
        task = task_submission_cli._record(
            task_id, run_id, 'pm-' + new_id(), 'pm-profile', request,
            state='PM_RESPONDED', provider_type='openai-compatible', model='pm-model',
            backend='NO_NATIVE_SESSION_ID_EXPOSED',
            request_sha=hashlib.sha256(request.encode()).hexdigest())
        task.update(
            pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                              'base_url': None},
            response='PM returned a bounded task.', error_code=None,
            pm_assignment={'schema_version': 1, 'route': 'codex', 'risk': 'LOW',
                           'goal': 'Land one change through a merge train.',
                           'acceptance': ['the merge train merges the change']},
            # The closure check reads only the result head from the evidence.
            completion={'state': 'WAITING_REVIEW', 'executor_task_id': None,
                        'reviewer_task_id': None, 'missing_gates': [],
                        'pm_dispatch': 'NOT_STARTED', 'pm_response': None,
                        'pm_recommendation': None, 'pm_usage': None,
                        'pm_error_code': None,
                        'evidence': {'task_id': task_id, 'run_id': run_id, 'result': {
                            'task_id': task_id, 'run_id': run_id, 'result_head': head}}})
        self.store.create(task)
        return task_id

    def record_merge(self, task_id, *, issue_number=TRAIN_PR, merge_sha=None,
                     status='MERGED'):
        """Store the merge event an earlier train closure wrote, by the same call."""
        merge_sha = self.squash if merge_sha is None else merge_sha
        event_id = hashlib.sha256(
            f'GITHUB\0merge\0{issue_number}\0{merge_sha}'.encode()).hexdigest()[:32]
        github_events._record(self.store, {'task_id': task_id}, 'github-task-advance',
                              event_id, issue_number, None, 'merge', status, merge_sha)

    def merge_events(self, task_id):
        return [event for event in self.store.get(task_id).get('completion_events', [])
                if event.get('source') == 'GITHUB' and event.get('marker_type') == 'merge']

    def member_train(self, train_id='t1', *, state='MERGED'):
        """Two stored Tasks at the train heads and a train record listing both."""
        a, b = self.stored_task(self.head_a), self.stored_task(self.head_b)
        record = self.make_train(train_id, state=state, members=[
            self.member(a, 17, self.head_a), self.member(b, 18, self.head_b)])
        return a, b, record

    def github_get(self, integration=None):
        prs = {TRAIN_PR: self.integration() if integration is None else integration}

        def get(path):
            if path.startswith('pulls/') and path[len('pulls/'):].isdigit():
                return prs.get(int(path[len('pulls/'):]))
            return []
        return get

    @staticmethod
    def write_adapters():
        """Fake every review, repair, publish, merge and event write; record any call."""
        calls = []

        def recorder(name):
            return lambda *_args, **_kwargs: calls.append(name)
        names = ('dispatch_review_call', 'record_review_result_call', 'repair_review_call',
                 'run_task_complete_call', 'record_github_event_call', 'publish_gh_runner',
                 'record_result_call', 'merge_gh_runner')
        return calls, {name: recorder(name) for name in names}

    def run_advance(self, task_id, *, integration_pr=None, integration=None):
        calls, adapters = self.write_adapters()
        with patch.object(task_advance, '_run_remote_test',
                          side_effect=lambda *_a, **_k: calls.append('ec2_remote_test')):
            result = task_advance.advance(
                task_id=task_id, store=self.store, integration_pr=integration_pr,
                github_get=self.github_get(integration), cwd=self.repo, **adapters)
        return result, calls

    def run_drive(self, task_id, *, merge=False, integration=None):
        calls, adapters = self.write_adapters()
        with patch.object(task_advance, '_run_remote_test',
                          side_effect=lambda *_a, **_k: calls.append('ec2_remote_test')), \
                patch.object(task_advance, 'advance',
                             wraps=task_advance.advance) as advance:
            result = task_drive.drive(
                task_id=task_id, store=self.store, merge=merge, max_steps=5,
                sleep=lambda _seconds: calls.append('sleep'),
                label_mac_ci_call=lambda **_kwargs: calls.append('label_mac_ci'),
                github_get=self.github_get(integration), cwd=self.repo, **adapters)
        return result, calls, advance

    def assert_today(self, task_id, head, **kwargs):
        """The closure check falls through: same step as a Task with no merge event."""
        expected, _ = self.run_advance(self.stored_task(head), **kwargs)
        self.assertNotEqual(expected['step'], 'CLOSED')
        before = self.merge_events(task_id)
        result, calls = self.run_advance(task_id, **kwargs)
        self.assertEqual((result['step'], result['next']), (expected['step'], expected['next']))
        self.assertEqual(calls, [])
        self.assertEqual(self.merge_events(task_id), before)

    def test_advance_reports_closed_for_a_train_closed_task(self):
        for state in ('MERGED', 'CLOSED'):
            with self.subTest(state=state):
                a, b, record = self.member_train('t-' + state.lower(), state=state)
                for task_id in (a, b):
                    # Without its merge event the same stored Task does not close.
                    before, _ = self.run_advance(task_id)
                    self.assertNotEqual(before['step'], 'CLOSED')
                    self.record_merge(task_id)
                    result, calls = self.run_advance(task_id)
                    self.assertEqual((result['step'], result['next']), ('CLOSED', 'CLOSED'))
                    # Nothing is written to GitHub; no second MERGED event is stored.
                    self.assertEqual(calls, [])
                    self.assertEqual(len(self.merge_events(task_id)), 1)
                os.unlink(self.trains / (record['train_id'] + '.json'))

    def test_drive_stops_at_closed_for_a_train_closed_task(self):
        a, _b, _record = self.member_train()
        before, _calls, _advance = self.run_drive(a)
        self.assertNotEqual(before['step'], 'CLOSED')
        self.record_merge(a)
        for merge in (False, True):
            with self.subTest(merge=merge):
                result, calls, advance = self.run_drive(a, merge=merge)
                self.assertEqual((result['step'], result['next'], result['steps']),
                                 ('CLOSED', 'CLOSED', 1))
                # One advance call without --integration-pr; no review, repair,
                # EC2, publish, merge, label or poll follows it.
                advance.assert_called_once()
                self.assertIs(advance.call_args.kwargs['merge'], False)
                self.assertNotIn('integration_pr', advance.call_args.kwargs)
                self.assertEqual(calls, [])
                self.assertEqual(len(self.merge_events(a)), 1)

    def test_advance_with_integration_pr_keeps_its_path(self):
        a, _b, _record = self.member_train()
        self.record_merge(a)
        expected, _ = self.run_advance(self.stored_task(self.head_a), integration_pr=TRAIN_PR)
        with patch.object(task_advance, '_train_closed_member') as closed_member:
            result, _calls = self.run_advance(a, integration_pr=TRAIN_PR)
        closed_member.assert_not_called()
        self.assertEqual((result['step'], result['next']), (expected['step'], expected['next']))

    def test_no_merge_event_keeps_todays_step(self):
        a, b, _record = self.member_train()
        self.assert_today(a, self.head_a)
        self.record_merge(b, status='OPEN')
        self.assert_today(b, self.head_b)

    def test_no_train_record_keeps_todays_step(self):
        a, _b, _record = self.member_train()
        self.record_merge(a, issue_number=27)
        self.assert_today(a, self.head_a)

    def test_train_in_another_state_keeps_todays_step(self):
        for state in ('BUILT', 'MERGE_CLAIMED'):
            with self.subTest(state=state):
                a, _b, record = self.member_train(
                    't-' + state.lower().replace('_', '-'), state=state)
                self.record_merge(a)
                self.assert_today(a, self.head_a)
                os.unlink(self.trains / (record['train_id'] + '.json'))

    def test_task_not_listed_keeps_todays_step(self):
        self.member_train()
        unlisted = self.stored_task(self.head_a)
        self.record_merge(unlisted)
        self.assert_today(unlisted, self.head_a)

    def test_head_not_listed_keeps_todays_step(self):
        # The train lists this Task, but at another head than its result head.
        moved, b = self.stored_task(self.head_other), self.stored_task(self.head_b)
        self.make_train(members=[self.member(moved, 17, self.head_a),
                                 self.member(b, 18, self.head_b)])
        self.record_merge(moved)
        self.assert_today(moved, self.head_other)

    def test_other_merge_sha_keeps_todays_step(self):
        a, _b, _record = self.member_train()
        self.record_merge(a, merge_sha=self.main)
        self.assert_today(a, self.head_a)

    def test_train_proof_failure_keeps_todays_step(self):
        a, _b, _record = self.member_train()
        self.record_merge(a)
        self.assert_today(a, self.head_a, integration=self.integration(merged=False))

    def test_store_read_error_does_not_close(self):
        a, _b, _record = self.member_train()
        self.record_merge(a)
        (self.trains / 'not a record').write_text('x', encoding='utf-8')
        self.assert_today(a, self.head_a)
        result, calls, _advance = self.run_drive(a)
        self.assertNotEqual(result['step'], 'CLOSED')
        self.assertEqual(calls, [])

    def test_store_refusal_does_not_close(self):
        a, _b, _record = self.member_train()
        self.record_merge(a)
        refusal = LLMToolError('TRAIN_RECORD_UNAVAILABLE')
        with patch.object(merge_train_store, 'find_by_pr', side_effect=refusal):
            self.assert_today(a, self.head_a)


if __name__ == '__main__':
    unittest.main()
