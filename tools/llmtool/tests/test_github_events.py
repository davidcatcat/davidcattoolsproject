"""Offline, metadata-only GitHub event polling tests."""

import json
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import test_active_pm_inbox as inbox_fixture
from llmtool import github_events, task_submission_cli
from llmtool.cli import main


def github_responder(responses):
    """Match fake responses by endpoint and stable parsed query fields."""
    def get(url):
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        if parsed.path.endswith('/comments'):
            # The since value is derived from a wall-clock event timestamp.
            # Validate its presence/format, while routing on the endpoint and
            # stable per_page parameter.
            if 'since' in query:
                assert len(query['since']) == 1
                assert query['since'][0].endswith('Z')
            key = (parsed.path, query.get('per_page', [None])[0],
                   query.get('page', ['1'])[0])
            if key not in responses:
                key = key[:2]
        else:
            key = (parsed.path, None)
        return responses[key]
    return get


class GitHubEventsTests(inbox_fixture.ActivePMInboxTests):
    def test_review_dispatch_claim_binds_pr_for_merge_poll(self):
        task = self._ready_task()
        task['review_dispatch_claim'] = {
            'executor_task': 'executor-one', 'pr': 84, 'head_sha': 'a' * 40,
            'version': 1, 'state': 'DISPATCHED', 'reviewer_task': 'reviewer-one',
            'native_session_id': 'session-one', 'account_mode': 'DEFAULT_SECOND_ACCOUNT',
            'claimed_at': '2026-01-01T00:00:00Z', 'updated_at': '2026-01-01T00:00:00Z'}
        class ClaimBoundStore:
            def task_ids(inner):
                return self.store.task_ids()
            def get(inner, task_id):
                value = self.store.get(task_id)
                if task_id == task['task_id']:
                    value['review_dispatch_claim'] = task['review_dispatch_claim']
                return value
            def record_completion_event(inner, task_id, **kwargs):
                return self.store.record_completion_event(task_id, **kwargs)
        responses = {('issues/84/comments', '100'): [
                         {'id': 8, 'body': '<!-- activity: type=decision actor=Owner target=PM status=GO -->',
                          'user': {'login': 'Owner'}, 'created_at': 'x', 'updated_at': 'x',
                          'author_association': 'OWNER'}],
                     ('pulls/84', None): {'merged': True, 'merge_commit_sha': 'b' * 40,
                                          'head': {'sha': 'c' * 40}},
                     ('compare/' + 'a' * 40 + '...' + 'c' * 40, None):
                         {'status': 'ahead'}}
        result = github_events.poll(ClaimBoundStore(), task['pm_session_id'], [84],
                                    github_get=github_responder(responses))
        self.assertEqual(len(result['events']), 1)
        self.assertEqual(result['events'][0]['marker_status'], 'MERGED')

    def test_owner_comment_once_restart_and_inbox(self):
        task = self._ready_task()
        task['completion']['evidence']['review_pr_ci'] = {
            'stage': 'EXECUTOR_RESULT', 'formal_review_state': 'NOT_STARTED',
            'ci_state': 'NOT_STARTED', 'pr_number': 77}
        self.store.update(task['task_id'], completion=task['completion'])
        responses = {
            ('issues/77/comments', '100'): [
                {'id': 9, 'body': '<!-- activity: type=decision actor=Owner target=PM status=GO -->\nsecret body',
                 'user': {'login': 'Owner'}, 'created_at': '2026-01-01T00:00:00Z',
                 'updated_at': '2026-01-01T00:00:00Z', 'author_association': 'OWNER'}],
            ('pulls/77', None): {'merged': False}}
        get = github_responder(responses)
        first = github_events.poll(self.store, task['pm_session_id'], [77],
                                   owner_comment_ids=[9], github_get=get)
        again = github_events.poll(TaskStoreProxy(self.store), task['pm_session_id'], [77],
                                   owner_comment_ids=[9], github_get=get)
        self.assertEqual(len(first['events']), 1)
        self.assertEqual(first['events'][0]['event_id'], again['events'][0]['event_id'])
        inbox = self._inbox()['events']
        self.assertEqual(len(inbox), 1)
        self.assertEqual(inbox[0]['source'], 'GITHUB')
        self.assertNotIn('secret body', json.dumps(self.store.get(task['task_id'])))

    def test_non_owner_and_edited_markers_ignored(self):
        task = self._ready_task()
        task['completion']['evidence']['review_pr_ci'] = {
            'stage': 'EXECUTOR_RESULT', 'formal_review_state': 'NOT_STARTED',
            'ci_state': 'NOT_STARTED', 'pr_number': 78}
        self.store.update(task['task_id'], completion=task['completion'])
        responses = {('issues/78/comments', '100'): [
            {'id': 1, 'body': '<!-- activity: type=decision actor=Contributor target=PM status=GO -->',
             'user': {'login': 'Someone'}, 'created_at': 'x', 'updated_at': 'x'},
            {'id': 2, 'body': '<!-- activity: type=decision actor=Owner target=PM status=GO -->',
             'user': {'login': 'Owner'}, 'created_at': 'x', 'updated_at': 'y',
             'author_association': 'OWNER'}],
            ('pulls/78', None): {'merged': False}}
        self.assertEqual(github_events.poll(self.store, task['pm_session_id'], [78],
            owner_comment_ids=[1, 2],
            github_get=github_responder(responses))['events'], [])

    def test_multiple_events_in_one_poll_have_unique_sequences(self):
        task = self._ready_task()
        task['completion']['evidence']['review_pr_ci'] = {
            'stage': 'EXECUTOR_RESULT', 'formal_review_state': 'NOT_STARTED',
            'ci_state': 'NOT_STARTED', 'pr_number': 81}
        self.store.update(task['task_id'], completion=task['completion'])
        comments = [
            {'id': 11, 'body': '<!-- activity: type=owner_request actor=Owner target=PM status=ANSWERED -->',
             'user': {'login': 'davidcatcat'}, 'created_at': 'x', 'updated_at': 'x',
             'author_association': 'OWNER'},
            {'id': 12, 'body': '<!-- activity: type=owner_request actor=Owner target=PM status=ANSWERED -->',
             'user': {'login': 'davidcatcat'}, 'created_at': 'x', 'updated_at': 'x',
             'author_association': 'OWNER'},
        ]
        responses = {('issues/81/comments', '100'): comments,
                     ('pulls/81', None): {'merged': True, 'merge_commit_sha': 'b' * 40}}
        out = github_events.poll(self.store, task['pm_session_id'], [81],
                                 owner_comment_ids=[11, 12],
                                 github_get=github_responder(responses))
        self.assertEqual(len(out['events']), 2)
        saved = self.store.get(task['task_id'])['completion_events']
        self.assertEqual([event['sequence'] for event in saved], [0, 1])

    def test_owner_marker_requires_github_owner_attribution(self):
        task = self._ready_task()
        task['completion']['evidence']['review_pr_ci'] = {
            'stage': 'EXECUTOR_RESULT', 'formal_review_state': 'NOT_STARTED',
            'ci_state': 'NOT_STARTED', 'pr_number': 82}
        self.store.update(task['task_id'], completion=task['completion'])
        responses = {('issues/82/comments', '100'): [
            {'id': 21, 'body': '<!-- activity: type=owner_request actor=Owner target=PM status=ANSWERED -->',
             'user': {'login': 'attacker'}, 'created_at': 'x', 'updated_at': 'x',
             'author_association': 'CONTRIBUTOR'}],
            ('pulls/82', None): {'merged': False}}
        result = github_events.poll(self.store, task['pm_session_id'], [82],
                                    owner_comment_ids=[21],
                                    github_get=github_responder(responses))
        self.assertEqual(result['events'], [])

    def test_unallowlisted_owner_marker_is_not_recorded(self):
        task = self._ready_task()
        task['completion']['evidence']['review_pr_ci'] = {
            'stage': 'EXECUTOR_RESULT', 'formal_review_state': 'NOT_STARTED',
            'ci_state': 'NOT_STARTED', 'pr_number': 85}
        self.store.update(task['task_id'], completion=task['completion'])
        responses = {('issues/85/comments', '100'): [
            {'id': 22, 'body': '<!-- activity: type=owner_request actor=Owner target=PM status=ANSWERED -->',
             'user': {'login': 'davidcatcat'}, 'created_at': 'x', 'updated_at': 'x',
             'author_association': 'OWNER'}],
            ('pulls/85', None): {'merged': False}}
        result = github_events.poll(self.store, task['pm_session_id'], [85],
                                    owner_comment_ids=[23],
                                    github_get=github_responder(responses))
        self.assertEqual(result['events'], [])

    def test_default_allowlist_suppresses_comments_but_keeps_merge_events(self):
        task = self._ready_task()
        task['review_dispatch_claim'] = {
            'executor_task': 'executor-one', 'pr': 86, 'head_sha': 'a' * 40,
            'version': 1, 'state': 'DISPATCHED', 'reviewer_task': 'reviewer-one',
            'native_session_id': 'session-one', 'account_mode': 'DEFAULT_SECOND_ACCOUNT',
            'claimed_at': '2026-01-01T00:00:00Z', 'updated_at': '2026-01-01T00:00:00Z'}
        class ClaimBoundStore:
            def task_ids(inner):
                return self.store.task_ids()
            def get(inner, task_id):
                value = self.store.get(task_id)
                if task_id == task['task_id']:
                    value['review_dispatch_claim'] = task['review_dispatch_claim']
                return value
            def record_completion_event(inner, task_id, **kwargs):
                return self.store.record_completion_event(task_id, **kwargs)
        responses = {
            ('issues/86/comments', '100'): [
                {'id': 24, 'body': '<!-- activity: type=owner_request actor=Owner target=PM status=ANSWERED -->',
                 'user': {'login': 'davidcatcat'}, 'created_at': 'x', 'updated_at': 'x',
                 'author_association': 'OWNER'}],
            ('pulls/86', None): {'merged': True, 'merge_commit_sha': 'b' * 40,
                                'head': {'sha': 'c' * 40}},
            ('compare/' + 'a' * 40 + '...' + 'c' * 40, None): {'status': 'identical'}}
        result = github_events.poll(ClaimBoundStore(), task['pm_session_id'], [86],
                                    github_get=github_responder(responses))
        self.assertEqual(len(result['events']), 1)
        self.assertEqual(result['events'][0]['marker_status'], 'MERGED')

    def test_invalid_owner_comment_allowlists_raise(self):
        task = self._ready_task()
        for allowlist in ('9', [True], [0], [2**63], list(range(1, 502))):
            with self.subTest(allowlist=type(allowlist).__name__):
                with self.assertRaises(github_events.LLMToolError):
                    github_events.poll(self.store, task['pm_session_id'], [87],
                                       owner_comment_ids=allowlist,
                                       github_get=lambda _url: [])

    def test_first_poll_paginates_past_old_comments(self):
        task = self._ready_task()
        task['completion']['evidence']['review_pr_ci'] = {
            'stage': 'EXECUTOR_RESULT', 'formal_review_state': 'NOT_STARTED',
            'ci_state': 'NOT_STARTED', 'pr_number': 83}
        self.store.update(task['task_id'], completion=task['completion'])
        old = [{'id': i, 'body': 'ordinary comment', 'user': {'login': 'someone'},
                'created_at': 'x', 'updated_at': 'x'} for i in range(100)]
        recent = [{'id': 121, 'body': '<!-- activity: type=owner_request actor=Owner target=PM status=ANSWERED -->',
                   'user': {'login': 'davidcatcat'}, 'created_at': 'x', 'updated_at': 'x',
                   'author_association': 'OWNER'}]
        responses = {('issues/83/comments', '100', '1'): old,
                     ('issues/83/comments', '100', '2'): recent,
                     ('pulls/83', None): {'merged': False}}
        result = github_events.poll(self.store, task['pm_session_id'], [83],
                                    owner_comment_ids=[121],
                                    github_get=github_responder(responses))
        self.assertEqual([event['comment_id'] for event in result['events']], [121])

    def test_merged_pr_records_sha_and_cli_routes(self):
        task = self._ready_task()
        task['completion']['evidence']['review_pr_ci'] = {
            'stage': 'EXECUTOR_RESULT', 'formal_review_state': 'NOT_STARTED',
            'ci_state': 'NOT_STARTED', 'pr_number': 79}
        self.store.update(task['task_id'], completion=task['completion'])
        responses = {('issues/79/comments', '100'): [],
                     ('pulls/79', None): {'merged': True, 'merge_commit_sha': 'a' * 40}}
        out = github_events.poll(self.store, task['pm_session_id'], [79],
                                 github_get=github_responder(responses))
        self.assertEqual(out['events'], [])
        with patch.object(task_submission_cli, 'run_task_github_events', return_value=23) as handler:
            self.assertEqual(main(['task', 'github-events', '--pm-session', self.session,
                                   '--issue', '79']), 23)
        handler.assert_called_once()

    def test_cli_reports_owner_comments_disabled_and_records_verified_merge(self):
        task = self._ready_task()
        task['review_dispatch_claim'] = {
            'executor_task': 'executor-one', 'pr': 88, 'head_sha': 'a' * 40,
            'version': 1, 'state': 'DISPATCHED', 'reviewer_task': 'reviewer-one',
            'native_session_id': 'session-one', 'account_mode': 'DEFAULT_SECOND_ACCOUNT',
            'claimed_at': '2026-01-01T00:00:00Z', 'updated_at': '2026-01-01T00:00:00Z'}

        class ClaimBoundStore:
            def task_ids(inner):
                return self.store.task_ids()
            def get(inner, task_id):
                value = self.store.get(task_id)
                if task_id == task['task_id']:
                    value['review_dispatch_claim'] = task['review_dispatch_claim']
                return value
            def record_completion_event(inner, task_id, **kwargs):
                return self.store.record_completion_event(task_id, **kwargs)

        responses = {
            ('issues/88/comments', '100'): [
                {'id': 25,
                 'body': '<!-- activity: type=decision actor=Owner target=PM status=GO -->',
                 'user': {'login': 'davidcatcat'}, 'created_at': 'x', 'updated_at': 'x',
                 'author_association': 'OWNER'}],
            ('pulls/88', None): {'merged': True, 'merge_commit_sha': 'b' * 40,
                                'head': {'sha': 'c' * 40}},
            ('compare/' + 'a' * 40 + '...' + 'c' * 40, None): {'status': 'identical'}}
        output = StringIO()
        args = Namespace(pm_session=task['pm_session_id'], issue=[88], since=None)
        with patch.object(github_events, 'poll', wraps=github_events.poll) as poll:
            self.assertEqual(task_submission_cli.run_task_github_events(
                args, store=ClaimBoundStore(), stdout=output,
                github_get=github_responder(responses)), 0)

        poll.assert_called_once()
        self.assertNotIn('owner_comment_ids', poll.call_args.kwargs)
        result = json.loads(output.getvalue())
        self.assertEqual(result['comment_events'], 'DISABLED_NO_TRUSTED_OWNER_SOURCE')
        self.assertEqual(set(result) - {'comment_events'}, {'pm_session_id', 'events'})
        self.assertEqual(len(result['events']), 1)
        self.assertEqual(result['events'][0]['marker_status'], 'MERGED')
        self.assertIsNone(result['events'][0]['comment_id'])


class TaskStoreProxy:
    """Fresh store object over the same durable directory."""
    def __new__(cls, store):
        from llmtool.task_store import TaskStore
        return TaskStore(store.root)

from unittest import TestCase

class GitHubApiPathSincePattern(TestCase):
    """Exact since query path validation for task_completion._GITHUB_API_PATH."""

    def _fullmatch(self):
        from llmtool import task_completion
        return task_completion._GITHUB_API_PATH.fullmatch

    def test_since_path_poller_builds_is_accepted(self):
        fullmatch = self._fullmatch()
        self.assertTrue(fullmatch(
            'issues/88/comments?per_page=100&page=1&since=2025-01-01T00:00:00Z'))
        self.assertTrue(fullmatch(
            'issues/1234567/comments?per_page=1&page=999&since=2025-12-31T23:59:59Z'))

    def test_since_path_refusals(self):
        fullmatch = self._fullmatch()
        refused = [
            'issues/88/comments?per_page=100&page=1&since=2025-01-01T00:00:00+00:00',
            'issues/88/comments?per_page=100&page=1&since=2025-01-01T00:00:00.123Z',
            'issues/88/comments?per_page=100&page=1&since=2025-01-01T00:00:00',
            'issues/12345678/comments?per_page=100&page=1&since=2025-01-01T00:00:00Z',
            'issues/88/comments?per_page=1000&page=1&since=2025-01-01T00:00:00Z',
            'issues/88/comments?per_page=100&page=1000&since=2025-01-01T00:00:00Z',
            'issues/88/comments?per_page=100&page=1&since=2025-01-01T00:00:0Z',
            'issues/88/comments?per_page=100&page=1&since=2025-01-01T00:00:00Z&extra=1',
            'issues/88/comments?per_page=100&page=1&since=2025-01-01T00:00:00Zx',
            'issues/../88/comments?per_page=100&page=1&since=2025-01-01T00:00:00Z',
            '/issues/88/comments?per_page=100&page=1&since=2025-01-01T00:00:00Z',
            'issues/88/comments?per_page=100&page=1&since=2025-01-01T00:00:00Z%00',
            'issues/88/comments?per_page=100&page=1&since=2025-01-01T00:00:00Z' + ' ',
            'issues/88/comments?per_page=100&page=1&since=2025-01-01T00:00:00Z\n',
            'issues/88:comments?per_page=100&page=1&since=2025-01-01T00:00:00Z',
            'issues/88/comments?per_page=100&page=1&since=2025-01-01T00:00:00Z&other=1:2',
        ]
        for path in refused:
            with self.subTest(path=path):
                self.assertIsNone(fullmatch(path))

    def test_existing_accepted_paths_stay_accepted(self):
        fullmatch = self._fullmatch()
        accepted = [
            'pulls/88',
            'issues/88/comments?per_page=100&page=1',
            'compare/' + 'a' * 40 + '...' + 'b' * 40,
        ]
        for path in accepted:
            with self.subTest(path=path):
                self.assertTrue(fullmatch(path))
