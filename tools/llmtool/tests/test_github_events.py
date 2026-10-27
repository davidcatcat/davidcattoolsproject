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
            key = (parsed.path, query.get('per_page', [None])[0])
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
                {'id': 9, 'body': '<!-- activity: type=decision actor=Owner status=GO -->\nsecret body',
                 'user': {'login': 'Owner'}, 'created_at': '2026-01-01T00:00:00Z',
                 'updated_at': '2026-01-01T00:00:00Z'}],
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
            {'id': 1, 'body': '<!-- activity: type=decision actor=Contributor status=GO -->',
             'user': {'login': 'Someone'}, 'created_at': 'x', 'updated_at': 'x'},
            {'id': 2, 'body': '<!-- activity: type=decision actor=Owner status=GO -->',
             'user': {'login': 'Owner'}, 'created_at': 'x', 'updated_at': 'y'}],
            ('pulls/78', None): {'merged': False}}
        self.assertEqual(github_events.poll(self.store, task['pm_session_id'], [78],
            github_get=github_responder(responses))['events'], [])

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
