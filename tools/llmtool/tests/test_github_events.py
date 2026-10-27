"""Offline, metadata-only GitHub event polling tests."""

import json
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import test_active_pm_inbox as inbox_fixture
from llmtool import github_events
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
        first = github_events.poll(self.store, task['pm_session_id'], [77], github_get=get)
        again = github_events.poll(TaskStoreProxy(self.store), task['pm_session_id'], [77], github_get=get)
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
                                 github_get=github_responder(responses))
        self.assertEqual(len(out['events']), 3)
        saved = self.store.get(task['task_id'])['completion_events']
        self.assertEqual([event['sequence'] for event in saved], [0, 1, 2])

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
                                    github_get=github_responder(responses))
        self.assertEqual(result['events'], [])

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
        self.assertEqual(out['events'][0]['source_sha'], 'a' * 40)
        with patch('llmtool.cli.run_task_github_events', return_value=23) as handler:
            self.assertEqual(main(['task', 'github-events', '--pm-session', self.session,
                                   '--issue', '79']), 23)
        handler.assert_called_once()


class TaskStoreProxy:
    """Fresh store object over the same durable directory."""
    def __new__(cls, store):
        from llmtool.task_store import TaskStore
        return TaskStore(store.root)
