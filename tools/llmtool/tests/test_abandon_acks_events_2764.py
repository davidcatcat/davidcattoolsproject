"""#2764: abandoning a Task acknowledges its unacknowledged completion events.

A real TaskStore and a real record-pm Task with real completion events; no
provider is called and no credential-shaped value is used.
"""

import io
import json
import os
import tempfile
import unittest
import uuid
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from llmtool import active_pm_inbox
from llmtool.task_store import TaskStore
from llmtool.task_submission_cli import run_task_record_pm

ACCEPTANCE = 'the proposal is recorded'
_RESPONSE = json.dumps({'schema_version': 1, 'summary': 'Active PM plan.',
    'developer_assignment': {'route': 'codex', 'risk': 'LOW',
        'goal': 'Produce a proposal diff.', 'acceptance': [ACCEPTANCE]}})


class AbandonAcksEventsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve()
        self.cwd = self.home / 'repo'
        self.cwd.mkdir()
        self.config_dir = self.home / 'claude'
        self.config_dir.mkdir(mode=0o700)
        self.environ = {'HOME': str(self.home), 'PATH': '/usr/bin:/bin',
                        'CLAUDE_CONFIG_DIR': str(self.config_dir)}
        environment = patch.dict(os.environ, self.environ)
        environment.start()
        self.addCleanup(environment.stop)
        self.store = TaskStore(self.home / 'tasks')

    def record_task(self):
        native = str(uuid.uuid4())
        transcript = active_pm_inbox.claude_session_file(
            native, str(self.cwd), self.environ)
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text('{}\n', encoding='utf-8')
        task = run_task_record_pm(
            Namespace(stdin_json=True, pm_session_id=native, pm_runtime='claude-code',
                      pm_cwd=str(self.cwd)),
            store=self.store, return_record=True,
            input_stream=io.StringIO(json.dumps({'request': 'Add a bounded test.',
                                                 'response': _RESPONSE})))
        return native, task

    def add_event(self, task, source, session_id):
        event_id = uuid.uuid4().hex
        self.store.record_completion_event(
            task['task_id'], event_id=event_id, run_id=task['run_id'],
            session_id=session_id, source=source, source_sha='a' * 40,
            sequence=0, payload_sha256=('b' if source != 'REVIEWER' else 'c') * 64)
        return event_id

    def abandon(self, native, task):
        return self.store.abandon(task['task_id'], pm_session_id='pm-' + native,
                                  reason='Abandoned by its PM')

    def test_pending_developer_and_reviewer_events_leave_the_inbox(self):
        native, task = self.record_task()
        self.add_event(task, 'DIRECT_D6_DEVELOPER', 'dev-session-1')
        self.add_event(task, 'REVIEWER', 'reviewer-session-1')
        before = self.store.get(task['task_id'])

        saved = self.abandon(native, task)

        self.assertEqual(set(saved['abandonment']),
                         {'reason', 'abandoned_at', 'pm_session_id'})
        self.assertEqual(saved['abandonment']['pm_session_id'], 'pm-' + native)
        self.assertEqual(saved['state'], before['state'])
        self.assertNotIn('abandonment', before)
        changed = {key for key in set(before) | set(saved)
                   if before.get(key) != saved.get(key)}
        # updated_at is second-granular, so it may be unchanged in a fast test.
        self.assertEqual(changed - {'updated_at'},
                         {'abandonment', 'completion_events', 'record_sha256'})
        events = self.store.get(task['task_id'])['completion_events']
        self.assertEqual([event['state'] for event in events], ['ACKED', 'ACKED'])
        for event in events:
            self.assertIsNotNone(event['consumed_at'])
            self.assertIsNotNone(event['acked_at'])
            self.assertEqual(
                event['pm_decision_sha256'],
                active_pm_inbox.ack_decision_sha256(event['event_id']))
        inbox = active_pm_inbox.read_inbox(self.store, 'pm-' + native)
        self.assertEqual(inbox['events'], [])
        self.assertFalse(any(event['state'] == 'PENDING'
                             for event in self.store.get(task['task_id'])['completion_events']))

    def test_an_already_acked_event_is_unchanged(self):
        native, task = self.record_task()
        acked_id = self.add_event(task, 'DIRECT_D6_DEVELOPER', 'dev-session-1')
        active_pm_inbox.ack(self.store, acked_id, 'pm-' + native)
        acked_before = next(
            event for event in self.store.get(task['task_id'])['completion_events']
            if event['event_id'] == acked_id)
        self.add_event(task, 'REVIEWER', 'reviewer-session-1')

        self.abandon(native, task)

        events = self.store.get(task['task_id'])['completion_events']
        acked_after = next(event for event in events if event['event_id'] == acked_id)
        self.assertEqual(acked_after, acked_before)
        self.assertEqual([event['state'] for event in events], ['ACKED', 'ACKED'])
        self.assertEqual(active_pm_inbox.read_inbox(self.store, 'pm-' + native)['events'], [])

    def test_a_task_without_events_abandons_as_before(self):
        native, task = self.record_task()
        before = self.store.get(task['task_id'])

        saved = self.abandon(native, task)

        self.assertNotIn('completion_events', saved)
        self.assertEqual(saved['state'], before['state'])
        self.assertEqual(saved['abandonment']['reason'], 'Abandoned by its PM')
        self.assertEqual(saved['abandonment']['pm_session_id'], 'pm-' + native)
        self.assertEqual(active_pm_inbox.read_inbox(self.store, 'pm-' + native)['events'], [])


if __name__ == '__main__':
    unittest.main()
