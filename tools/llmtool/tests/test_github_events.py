"""Offline, metadata-only GitHub event polling tests."""

import json
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

import test_active_pm_inbox as inbox_fixture
from llmtool import github_events
from llmtool.cli import main


class GitHubEventsTests(inbox_fixture.ActivePMInboxTests):
    def test_owner_comment_once_restart_and_inbox(self):
        task = self._ready_task()
        task['completion']['evidence']['review_pr_ci'] = {
            'stage': 'EXECUTOR_RESULT', 'formal_review_state': 'NOT_STARTED',
            'ci_state': 'NOT_STARTED', 'pr_number': 77}
        self.store.update(task['task_id'], completion=task['completion'])
        responses = {
            'issues/77/comments?per_page=100': [
                {'id': 9, 'body': '<!-- activity: type=decision actor=Owner status=GO -->\nsecret body',
                 'user': {'login': 'Owner'}, 'created_at': '2026-01-01T00:00:00Z',
                 'updated_at': '2026-01-01T00:00:00Z'}],
            'pulls/77': {'merged': False}}
        get = lambda path: responses[path]
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
        responses = {'issues/78/comments?per_page=100': [
            {'id': 1, 'body': '<!-- activity: type=decision actor=Contributor status=GO -->',
             'user': {'login': 'Someone'}, 'created_at': 'x', 'updated_at': 'x'},
            {'id': 2, 'body': '<!-- activity: type=decision actor=Owner status=GO -->',
             'user': {'login': 'Owner'}, 'created_at': 'x', 'updated_at': 'y'}],
            'pulls/78': {'merged': False}}
        self.assertEqual(github_events.poll(self.store, task['pm_session_id'], [78],
            github_get=lambda path: responses[path])['events'], [])

    def test_merged_pr_records_sha_and_cli_routes(self):
        task = self._ready_task()
        task['completion']['evidence']['review_pr_ci'] = {
            'stage': 'EXECUTOR_RESULT', 'formal_review_state': 'NOT_STARTED',
            'ci_state': 'NOT_STARTED', 'pr_number': 79}
        self.store.update(task['task_id'], completion=task['completion'])
        responses = {'issues/79/comments?per_page=100': [],
                     'pulls/79': {'merged': True, 'merge_commit_sha': 'a' * 40}}
        out = github_events.poll(self.store, task['pm_session_id'], [79],
                                 github_get=lambda path: responses[path])
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
