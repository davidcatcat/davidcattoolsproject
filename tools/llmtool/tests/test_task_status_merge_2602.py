"""Issue #2602 item s: a persisted MERGED merge_attempt completes a Task."""

import tempfile
import unittest
from pathlib import Path

from llmtool import task_status
from llmtool.task_store import TaskStore
from test_task_status import record


_MERGE_ATTEMPT = {
    'claimed_at': '2026-01-02T03:04:05Z',
    'head_sha': 'ab' * 20,
    'pr': 2602,
    'state': 'MERGED',
}


def _has_github_merge_event(record):
    events = record.get('completion_events')
    events = events if type(events) is list else []
    return any(type(event) is dict and event.get('source') == 'GITHUB' and
               event.get('marker_type') == 'merge' and
               event.get('marker_status') == 'MERGED'
               for event in events)


def _summary_states(root):
    store = TaskStore(Path(root).resolve() / 'tasks')
    result = task_status.task_status(store=store)
    return {item['task_id']: item['state'] for item in result['tasks']}


class TaskStatusMergeAttemptTests(unittest.TestCase):
    def test_merged_merge_attempt_completes_task(self):
        with tempfile.TemporaryDirectory() as root:
            value = record(root, state='PM_PENDING',
                           merge_attempt=dict(_MERGE_ATTEMPT))
            self.assertFalse(_has_github_merge_event(value))
            self.assertEqual(task_status.derive_state(value), 'COMPLETED')
            self.assertEqual(_summary_states(root)[value['task_id']], 'COMPLETED')

    def test_abandonment_still_beats_merged_merge_attempt(self):
        with tempfile.TemporaryDirectory() as root:
            value = record(root, state='PM_PENDING',
                           merge_attempt=dict(_MERGE_ATTEMPT))
            store = TaskStore(Path(root).resolve() / 'tasks')
            store.abandon(value['task_id'], pm_session_id=value['pm_session_id'],
                          reason='abandoned in test')
            current = store.get(value['task_id'])
            self.assertEqual(current['merge_attempt']['state'], 'MERGED')
            self.assertEqual(task_status.derive_state(current), 'ABANDONED')
            self.assertEqual(_summary_states(root)[value['task_id']], 'ABANDONED')

    def test_non_merged_merge_attempt_keeps_running(self):
        with tempfile.TemporaryDirectory() as root:
            value = record(root, state='PM_PENDING',
                           merge_attempt=dict(_MERGE_ATTEMPT, state='CLAIMED'))
            self.assertEqual(task_status.derive_state(value), 'RUNNING')
            self.assertEqual(_summary_states(root)[value['task_id']], 'RUNNING')

    def test_missing_merge_attempt_keeps_running(self):
        with tempfile.TemporaryDirectory() as root:
            value = record(root, state='PM_PENDING')
            self.assertEqual(task_status.derive_state(value), 'RUNNING')
            self.assertEqual(_summary_states(root)[value['task_id']], 'RUNNING')


if __name__ == '__main__':
    unittest.main()
