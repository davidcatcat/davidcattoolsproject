"""Ack one inbox event by Task ID (#2767).

A real TaskStore and real local PM transcripts are used; no provider is called.
These Tasks carry no Developer assignment and no owner_assignment_ref, so they
need neither the worker-policy fixture nor a repository-config patch.
"""

import argparse
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
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore
from llmtool.task_submission_cli import (add_task_submission_parsers, run_task_ack,
                                         run_task_record_pm)
from review_trust_fixture import start_module as _start_trust_fixture
from review_trust_fixture import stop_module as _stop_trust_fixture


def setUpModule():
    _start_trust_fixture()


def tearDownModule():
    _stop_trust_fixture()


_RESPONSE = json.dumps({
    'schema_version': 1, 'summary': 'Active PM plan.',
    'developer_assignment': {
        'route': 'codex', 'risk': 'LOW',
        'goal': 'Produce a proposal diff.',
        'acceptance': ['the proposal is recorded'],
    },
})
_NOT_FOUND = 'completion event not found for this PM session'


class AckByTaskTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve()
        self.cwd = self.home / 'repo'
        self.cwd.mkdir()
        self.config = self.home / 'claude-config'
        self.config.mkdir(mode=0o700)
        environment = patch.dict(os.environ, {
            'HOME': str(self.home),
            'CLAUDE_CONFIG_DIR': str(self.config),
            'PATH': '/usr/bin:/bin',
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.store = TaskStore(self.home / 'tasks')

    def native_session(self):
        native = str(uuid.uuid4())
        transcript = active_pm_inbox.claude_session_file(
            native, str(self.cwd), {'CLAUDE_CONFIG_DIR': str(self.config)})
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text('{}\n', encoding='utf-8')
        return native

    def session_key(self, native):
        return active_pm_inbox.pm_session_key(native)

    def record_task(self, native):
        return run_task_record_pm(
            Namespace(stdin_json=True, pm_session_id=native, pm_runtime='claude-code',
                      pm_cwd=str(self.cwd)),
            store=self.store, return_record=True,
            input_stream=io.StringIO(json.dumps({
                'request': 'Add a bounded test.',
                'response': _RESPONSE,
            })))

    def record_event(self, task, event_id, sequence=0):
        return self.store.record_completion_event(
            task['task_id'], event_id=event_id, run_id=task['run_id'],
            session_id='dev-test-session', source='DIRECT_D6_DEVELOPER',
            source_sha='a' * 40, sequence=sequence, payload_sha256='b' * 64)['event']

    def test_an_owned_record_becomes_acked_by_task(self):
        native = self.native_session()
        task = self.record_task(native)
        event_id = uuid.uuid4().hex
        self.record_event(task, event_id)

        result = active_pm_inbox.ack(
            self.store, event_id, self.session_key(native), task_id=task['task_id'])

        self.assertEqual(result['task_id'], task['task_id'])
        self.assertTrue(result['acknowledged_now'])
        self.assertEqual(result['event']['state'], 'ACKED')
        self.assertEqual(
            self.store.get(task['task_id'])['completion_events'][0]['state'], 'ACKED')

    def test_the_task_path_does_not_scan_every_record(self):
        native = self.native_session()
        task = self.record_task(native)
        event_id = uuid.uuid4().hex
        self.record_event(task, event_id)

        with patch.object(active_pm_inbox, '_session_records',
                          side_effect=AssertionError('session scan used')):
            result = active_pm_inbox.ack(
                self.store, event_id, self.session_key(native),
                task_id=task['task_id'])

        self.assertTrue(result['acknowledged_now'])

    def test_another_sessions_record_is_refused_and_nothing_is_written(self):
        owner = self.native_session()
        other = self.native_session()
        task = self.record_task(owner)
        event_id = uuid.uuid4().hex
        self.record_event(task, event_id)
        before = self.store.get(task['task_id'])

        with self.assertRaises(LLMToolError) as caught:
            active_pm_inbox.ack(
                self.store, event_id, self.session_key(other), task_id=task['task_id'])

        self.assertEqual(str(caught.exception), _NOT_FOUND)
        self.assertEqual(self.store.get(task['task_id']), before)

    def test_a_wrong_task_id_for_the_event_is_refused(self):
        native = self.native_session()
        first = self.record_task(native)
        second = self.record_task(native)
        event_id = uuid.uuid4().hex
        self.record_event(first, event_id)
        before_first = self.store.get(first['task_id'])
        before_second = self.store.get(second['task_id'])

        with self.assertRaises(LLMToolError) as caught:
            active_pm_inbox.ack(
                self.store, event_id, self.session_key(native),
                task_id=second['task_id'])

        self.assertEqual(str(caught.exception), _NOT_FOUND)
        self.assertEqual(self.store.get(first['task_id']), before_first)
        self.assertEqual(self.store.get(second['task_id']), before_second)

    def test_a_repeat_with_the_task_id_is_inert(self):
        native = self.native_session()
        task = self.record_task(native)
        event_id = uuid.uuid4().hex
        self.record_event(task, event_id)
        first = active_pm_inbox.ack(
            self.store, event_id, self.session_key(native), task_id=task['task_id'])
        self.assertTrue(first['acknowledged_now'])
        settled = self.store.get(task['task_id'])

        second = active_pm_inbox.ack(
            self.store, event_id, self.session_key(native), task_id=task['task_id'])

        self.assertFalse(second['acknowledged_now'])
        self.assertEqual(second['event']['state'], 'ACKED')
        self.assertEqual(self.store.get(task['task_id']), settled)

    def test_a_compacted_tombstone_reports_not_acknowledged_now(self):
        native = self.native_session()
        task = self.record_task(native)
        session = self.session_key(native)
        first_event = uuid.uuid4().hex
        self.record_event(task, first_event, sequence=0)
        self.assertTrue(active_pm_inbox.ack(
            self.store, first_event, session, task_id=task['task_id'])['acknowledged_now'])
        for sequence in range(1, 32):
            event_id = uuid.uuid4().hex
            self.record_event(task, event_id, sequence=sequence)
            self.assertTrue(active_pm_inbox.ack(
                self.store, event_id, session,
                task_id=task['task_id'])['acknowledged_now'])
        self.record_event(task, uuid.uuid4().hex, sequence=32)

        stored = self.store.get(task['task_id'])
        self.assertEqual(len(stored['completion_events']), 32)
        self.assertEqual(stored['completion_event_tombstones'][0]['event_id'], first_event)

        result = active_pm_inbox.ack(
            self.store, first_event, session, task_id=task['task_id'])

        self.assertFalse(result['acknowledged_now'])
        self.assertTrue(result['event']['compacted'])
        self.assertEqual(result['event']['state'], 'ACKED')

    def test_the_path_without_task_is_unchanged(self):
        native = self.native_session()
        task = self.record_task(native)
        event_id = uuid.uuid4().hex
        self.record_event(task, event_id)

        result = active_pm_inbox.ack(
            self.store, event_id, self.session_key(native))

        self.assertTrue(result['acknowledged_now'])
        self.assertEqual(result['event']['state'], 'ACKED')

    def test_the_cli_passes_the_optional_task_id(self):
        native = self.native_session()
        task = self.record_task(native)
        event_id = uuid.uuid4().hex
        self.record_event(task, event_id)
        output = io.StringIO()

        self.assertEqual(run_task_ack(
            Namespace(event_id=event_id, pm_session=native, task=task['task_id']),
            store=self.store, stdout=output), 0)

        result = json.loads(output.getvalue())
        self.assertTrue(result['acknowledged_now'])
        self.assertEqual(result['task_id'], task['task_id'])

    def test_the_ack_parser_has_an_optional_task(self):
        parser = argparse.ArgumentParser()
        add_task_submission_parsers(parser.add_subparsers(dest='action'))
        event_id = 'a' * 32
        task_id = 'b' * 32
        session = 'pm-' + str(uuid.uuid4())
        with_task = parser.parse_args(
            ['ack', event_id, '--pm-session', session, '--task', task_id])
        without_task = parser.parse_args(
            ['ack', event_id, '--pm-session', session])
        self.assertEqual(with_task.task, task_id)
        self.assertIsNone(without_task.task)


if __name__ == '__main__':
    unittest.main()
