"""Active-PM completion inbox: session binding, delivery, ack and recovery (#352 G6).

Everything is offline. A Claude Code session is represented only by its local
transcript file under a temporary CLAUDE_CONFIG_DIR.
"""

import json
import os
import uuid
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch
import unittest

from llmtool import active_pm_inbox
from llmtool.cli import main
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore
from llmtool.task_submission_cli import (
    run_task_ack, run_task_complete, run_task_inbox, run_task_record_pm,
    run_task_show)

# Reuse the registered-D6 completion fixture without re-collecting its tests.
import test_task_completion as completion_fixture

_RESPONSE = json.dumps({'schema_version': 1, 'summary': 'Active PM plan.',
    'developer_assignment': {'route': 'codex', 'risk': 'LOW',
        'goal': 'Add bounded test.', 'acceptance': ['test passes']}})


class ActivePMInboxTests(unittest.TestCase):
    setUp_fixture = completion_fixture.TaskCompletionTests.setUp
    _git = completion_fixture.TaskCompletionTests._git
    _write_registration = completion_fixture.TaskCompletionTests._write_registration
    _write_env2_marker = completion_fixture.TaskCompletionTests._write_env2_marker
    _task = completion_fixture.TaskCompletionTests._task
    _mock_complete_evidence = completion_fixture.TaskCompletionTests._mock_complete_evidence

    def setUp(self):
        self.setUp_fixture()
        self.claude_home = self.base / 'claude-config'
        self.pm_cwd = self.base / 'pm-root'
        self.pm_cwd.mkdir()
        env = patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(self.claude_home),
                                      'HOME': str(self.base / 'home')})
        env.start()
        self.addCleanup(env.stop)
        self.session = self._claude_session()
        self.store = TaskStore(self.base / 'tasks')

    def _claude_session(self, cwd=None):
        native = str(uuid.uuid4())
        path = active_pm_inbox.claude_session_file(native, str(cwd or self.pm_cwd))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}\n')
        return native

    def _record_pm(self, session=None, store=None, cwd=None):
        out = StringIO()
        with redirect_stdout(out):
            run_task_record_pm(Namespace(stdin_json=True,
                pm_session_id=self.session if session is None else session,
                pm_runtime=None, pm_cwd=str(cwd or self.pm_cwd)),
                store=store or self.store,
                input_stream=StringIO(json.dumps({'request': 'Add a bounded test.',
                                                  'response': _RESPONSE})))
        return json.loads(out.getvalue())

    def _ready_task(self, session=None):
        """A record-pm Task (bound to a PM session) waiting for its Executor result."""
        scratch = TaskStore(self.base / ('scratch-' + uuid.uuid4().hex))
        record = scratch.get(self._record_pm(session, store=scratch)['task_id'])
        record.pop('record_sha256')
        template = self._task()
        completion = json.loads(json.dumps(template['completion']))
        completion['evidence'].update(task_id=record['task_id'], run_id=record['run_id'],
            contract_digest=record['task_binding']['contract_digest'])
        record.update(developer=template['developer'], completion=completion)
        self.store.create(record)
        return record

    def _complete(self, task, *, tests=None):
        git, default_tests, _ = self._mock_complete_evidence(task)
        forbidden = AssertionError('an active PM runtime is never called as a provider')
        out = StringIO()
        with patch('llmtool.task_submission_cli.task_completion.collect_executor_result',
                   return_value=git), \
             patch('llmtool.task_submission_cli.task_completion.run_bounded_tests',
                   return_value=tests or default_tests), \
             patch('llmtool.task_submission_cli.resolve_profile_config',
                   side_effect=forbidden), \
             patch('llmtool.task_submission_cli.create_client', side_effect=forbidden), \
             redirect_stdout(out):
            code = run_task_complete(Namespace(task_id=task['task_id'],
                executor_task=self.task_id, reviewer_task=None, pr=None), store=self.store)
        return code, json.loads(out.getvalue())

    def _inbox(self, session=None, store=None, **kwargs):
        out = StringIO()
        args = Namespace(pm_session=self.session if session is None else session,
                         wait=kwargs.pop('wait', False), timeout=kwargs.pop('timeout', 0))
        self.assertEqual(run_task_inbox(args, store=store or self.store, stdout=out,
                                        **kwargs), 0)
        return json.loads(out.getvalue())

    def _ack(self, event_id, session=None):
        out = StringIO()
        run_task_ack(Namespace(event_id=event_id,
                               pm_session=self.session if session is None else session),
                     store=self.store, stdout=out)
        return json.loads(out.getvalue())

    # Session identity -----------------------------------------------------

    def test_record_pm_stores_observed_claude_session_and_recovery(self):
        view = self._record_pm()
        self.assertEqual(view['pm_session_id'], 'pm-' + self.session)
        provenance = view['pm_provenance']
        self.assertEqual(provenance['pm_runtime'], 'claude-code')
        self.assertEqual(provenance['pm_native_session_id'], self.session)
        self.assertEqual(provenance['pm_cwd'], str(self.pm_cwd))
        self.assertEqual(provenance['native_session_id_status'],
                         'LOCAL_SESSION_FILE_OBSERVED')
        self.assertIs(provenance['session_restored'], False)
        self.assertEqual(view['pm_recovery'], {
            'runtime': 'claude-code', 'cwd': str(self.pm_cwd),
            'resume_argv': ['claude', '--resume', self.session]})

    def test_record_pm_refuses_unverified_or_malformed_sessions_without_task(self):
        other_dir = self.base / 'other-root'
        other_dir.mkdir()
        cases = (
            (str(uuid.uuid4()), self.pm_cwd),          # no transcript
            (self.session, other_dir),                  # transcript is for another cwd
            (self.session.upper(), self.pm_cwd),        # not the lowercase UUID form
            ('pm-' + self.session, self.pm_cwd),
            (self.session, self.base / 'missing-dir'),
        )
        for session, cwd in cases:
            with self.subTest(session=session, cwd=str(cwd)), \
                    self.assertRaises(LLMToolError):
                self._record_pm(session, cwd=cwd)
        with self.assertRaises(LLMToolError):
            run_task_record_pm(Namespace(stdin_json=True, pm_session_id=None,
                pm_runtime='claude-code', pm_cwd=None), store=self.store,
                input_stream=StringIO('{}'))
        self.assertEqual(self.store.task_ids(), [])

    def test_task_store_rejects_inconsistent_session_binding(self):
        record = self.store.get(self._record_pm()['task_id'])
        for field, value in (('pm_native_session_id', str(uuid.uuid4())),
                             ('pm_runtime', 'codex'), ('pm_cwd', 'relative/dir'),
                             ('native_session_id_status', 'UNAVAILABLE')):
            changed = {**record, 'task_id': uuid.uuid4().hex,
                       'pm_provenance': {**record['pm_provenance'], field: value}}
            with self.subTest(field=field), self.assertRaises(LLMToolError):
                TaskStore(self.base / 'invalid').create(changed)

    # Delivery -------------------------------------------------------------

    def test_completion_event_is_delivered_with_the_handoff(self):
        task = self._ready_task()
        code, view = self._complete(task)
        self.assertEqual(code, 0)
        self.assertEqual(view['completion']['state'], 'PM_EVIDENCE_READY')
        self.assertEqual([event['state'] for event in view['completion_events']],
                         ['PENDING'])
        inbox = self._inbox()
        self.assertEqual(inbox['pm_session_id'], 'pm-' + self.session)
        self.assertIs(inbox['timed_out'], False)
        (event,) = inbox['events']
        self.assertEqual(event['task_id'], task['task_id'])
        self.assertEqual(event['run_id'], task['run_id'])
        self.assertEqual(event['source'], 'DIRECT_D6_DEVELOPER')
        self.assertIs(event['current'], True)
        self.assertEqual(event['active_pm_handoff'], view['active_pm_handoff'])
        self.assertEqual(event['pm_recovery']['resume_argv'][-1], self.session)

    def test_pm_absent_then_later_read_after_restart(self):
        task = self._ready_task()
        self._complete(task)
        # A new process (fresh store object) reads the same durable inbox, twice.
        later = TaskStore(self.store.root)
        first = self._inbox(store=later)['events']
        again = self._inbox(store=TaskStore(self.store.root))['events']
        self.assertEqual([event['event_id'] for event in first],
                         [event['event_id'] for event in again])
        self.assertEqual(len(first), 1)

    def test_wait_returns_when_the_event_arrives(self):
        task = self._ready_task()
        self.assertEqual(self._inbox()['events'], [])
        calls = []

        def sleep(seconds):
            calls.append(seconds)
            if len(calls) == 1:
                self._complete(task)

        inbox = self._inbox(wait=True, timeout=30, sleep=sleep)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(inbox['events']), 1)
        self.assertIs(inbox['timed_out'], False)

    def test_wait_timeout_returns_empty(self):
        self._ready_task()
        clock = iter(range(0, 1000, 5))
        result = active_pm_inbox.wait_inbox(
            self.store, 'pm-' + self.session, wait=True, timeout=12,
            clock=lambda: next(clock), sleep=lambda seconds: None)
        self.assertEqual(result['events'], [])
        self.assertIs(result['timed_out'], True)
        with self.assertRaises(LLMToolError):
            active_pm_inbox.wait_inbox(self.store, 'pm-' + self.session, timeout=3601)

    # Duplicates, crashes and acknowledgement -----------------------------

    def test_duplicate_completion_never_creates_a_second_event(self):
        task = self._ready_task()
        self._complete(task)
        self._complete(task)  # same evidence re-verified
        active_pm_inbox.record_ready_event(self.store, task['task_id'])
        saved = self.store.get(task['task_id'])
        self.assertEqual(len(saved['completion_events']), 1)
        self.assertEqual(len(self._inbox()['events']), 1)

    def test_crash_between_ready_state_and_event_is_reconciled_by_inbox(self):
        task = self._ready_task()
        with patch('llmtool.active_pm_inbox.record_ready_event',
                   side_effect=LLMToolError('simulated crash before the event write')):
            code, view = self._complete(task)
        self.assertEqual(code, 0)
        self.assertEqual(view['completion']['state'], 'PM_EVIDENCE_READY')
        self.assertNotIn('completion_events', view)
        (event,) = self._inbox()['events']
        self.assertEqual(event['task_id'], task['task_id'])
        self.assertEqual(len(self.store.get(task['task_id'])['completion_events']), 1)

    def test_crash_between_consume_and_ack_redelivers_until_acked(self):
        task = self._ready_task()
        self._complete(task)
        (event,) = self._inbox()['events']
        digest = active_pm_inbox.ack_decision_sha256(event['event_id'])
        self.store.consume_completion_event(task['task_id'], event['event_id'],
            pm_session_id='pm-' + self.session, pm_decision_sha256=digest)
        # The process stops here; after restart the event is still delivered.
        (pending,) = self._inbox(store=TaskStore(self.store.root))['events']
        self.assertEqual(pending['state'], 'PM_CONSUMED')
        self.assertEqual(self._ack(event['event_id'])['event']['state'], 'ACKED')
        self.assertEqual(self._inbox()['events'], [])

    def test_ack_is_idempotent_and_acked_events_are_never_redelivered(self):
        task = self._ready_task()
        self._complete(task)
        (event,) = self._inbox()['events']
        first = self._ack(event['event_id'])
        self.assertIs(first['acknowledged_now'], True)
        self.assertEqual(first['task_id'], task['task_id'])
        second = self._ack(event['event_id'])
        self.assertIs(second['acknowledged_now'], False)
        self.assertEqual(second['event'], first['event'])
        self._complete(task)  # the same evidence again: no new delivery
        self.assertEqual(self._inbox()['events'], [])
        self.assertEqual(len(self.store.get(task['task_id'])['completion_events']), 1)

    def test_wrong_pm_session_is_refused(self):
        task = self._ready_task()
        self._complete(task)
        (event,) = self._inbox()['events']
        other = self._claude_session()
        self.assertEqual(self._inbox(session=other)['events'], [])
        with self.assertRaises(LLMToolError):
            self._ack(event['event_id'], session=other)
        with self.assertRaises(LLMToolError):
            self.store.ack_completion_event(task['task_id'], event['event_id'],
                pm_session_id='pm-' + other,
                pm_decision_sha256=active_pm_inbox.ack_decision_sha256(event['event_id']))
        for bad in ('not-a-session', self.session.upper(), ''):
            with self.subTest(bad=bad), self.assertRaises(LLMToolError):
                self._inbox(session=bad)
        self.assertEqual(self.store.get(task['task_id'])['completion_events'][0]['state'],
                         'PENDING')

    def test_missing_gates_produce_no_event(self):
        task = self._ready_task()
        git, tests, _ = self._mock_complete_evidence(task)
        code, view = self._complete(task, tests=[dict(tests[0], exit_code=1)])
        self.assertEqual(code, 1)
        self.assertEqual(view['completion']['state'], 'WAITING_EXECUTOR')
        self.assertEqual(self._inbox()['events'], [])

    def test_cli_inbox_and_ack_round_trip(self):
        home = self.base / 'home'
        store = TaskStore(home / '.local' / 'state' / 'llmtool' / 'tasks')
        self.store = store
        task = self._ready_task()
        self._complete(task)
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(['task', 'inbox', '--pm-session', self.session,
                                   '--wait', '--timeout', '5']), 0)
        (event,) = json.loads(out.getvalue())['events']
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(['task', 'ack', event['event_id'],
                                   '--pm-session', self.session]), 0)
        self.assertEqual(json.loads(out.getvalue())['event']['state'], 'ACKED')
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(['task', 'show', task['task_id']]), 0)
        self.assertEqual(json.loads(out.getvalue())['completion_events'][0]['state'],
                         'ACKED')


if __name__ == '__main__':
    unittest.main()
