"""Active-PM completion inbox: session binding, delivery, ack and recovery (#352 G6).

Everything is offline. A Claude Code session is represented only by its local
transcript file under a temporary CLAUDE_CONFIG_DIR.
"""

import hashlib
import json
import os
import threading
import uuid
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch
import unittest

from llmtool import active_pm_inbox
from llmtool import task_store as task_store_module
from llmtool.cli import main
from llmtool.errors import LLMToolError
from llmtool.task_index import TaskStatusIndex
from llmtool.task_store import TaskStore
from llmtool.task_submission_cli import (
    _active_pm_handoff, run_task_ack, run_task_complete, run_task_inbox, run_task_record_pm,
    run_task_show)

# Reuse the registered-D6 completion fixture without re-collecting its tests.
import test_task_completion as completion_fixture

_RESPONSE = json.dumps({'schema_version': 1, 'summary': 'Active PM plan.',
    'developer_assignment': {'route': 'codex', 'risk': 'LOW',
        'goal': 'Add bounded test.', 'acceptance': ['test passes']}})


class _DisabledTaskIndex(TaskStatusIndex):
    def __init__(self, store):
        self.store = store
        self.enabled = False


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
        self.codex_home = self.base / 'codex-config'
        self.pm_cwd = self.base / 'pm-root'
        self.pm_cwd.mkdir()
        env = patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(self.claude_home),
                                      'CODEX_HOME': str(self.codex_home),
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

    def _codex_session(self, cwd=None, *, metadata=None, name_id=None):
        native = str(uuid.uuid4())
        session_cwd = str(cwd or self.pm_cwd)
        path = (self.codex_home / 'sessions' / '2026' / '10' / '30' /
                ('rollout-20261030-120000-' + (name_id or native) + '.jsonl'))
        path.parent.mkdir(parents=True, exist_ok=True)
        if metadata is None:
            metadata = {'timestamp': '2026-10-30T12:00:00Z', 'type': 'session_meta',
                        'payload': {'id': native, 'cwd': session_cwd}}
        path.write_text(json.dumps(metadata) + '\n')
        return native, path

    def _record_pm(self, session=None, store=None, cwd=None, runtime=None):
        out = StringIO()
        with redirect_stdout(out):
            run_task_record_pm(Namespace(stdin_json=True,
                pm_session_id=self.session if session is None else session,
                pm_runtime=runtime, pm_cwd=str(cwd or self.pm_cwd)),
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

    def test_adopt_pm_session_moves_open_inbox_events_and_is_idempotent(self):
        first = self._ready_task()
        second = self._ready_task()
        self._complete(first)
        self._complete(second)
        third_native = self._claude_session()
        third = self._ready_task(session=third_native)
        self._complete(third)
        closed = self._record_pm()
        self.store.abandon(closed['task_id'], pm_session_id='pm-' + self.session,
                           reason='Closed before adoption')

        before = active_pm_inbox.read_inbox(self.store, 'pm-' + self.session)
        self.assertEqual({event['task_id'] for event in before['events']},
                         {first['task_id'], second['task_id']})
        old_path = active_pm_inbox.claude_session_file(self.session, str(self.pm_cwd))
        old_path.unlink()
        new_native = self._claude_session()
        new_key = 'pm-' + new_native
        result = active_pm_inbox.adopt_pm_session(
            self.store, 'pm-' + self.session, new_key,
            old_native_id=self.session, cwd=str(self.pm_cwd))
        self.assertEqual(set(result['moved']), {first['task_id'], second['task_id']})
        self.assertEqual(result['skipped'], [])

        after = active_pm_inbox.read_inbox(self.store, new_key)
        self.assertEqual({event['event_id'] for event in after['events']},
                         {event['event_id'] for event in before['events']})
        self.assertEqual(active_pm_inbox.read_inbox(
            self.store, 'pm-' + self.session)['events'], [])
        for task_id in result['moved']:
            self.assertEqual(self.store.get(task_id)['pm_provenance'][
                'native_session_id_status'], 'LOCAL_SESSION_FILE_OBSERVED')
        self.assertEqual(self.store.get(third['task_id'])['pm_session_id'],
                         'pm-' + third_native)
        self.assertEqual(self.store.get(closed['task_id'])['pm_session_id'],
                         'pm-' + self.session)
        for task_id in result['moved']:
            record = self.store.get(task_id)
            self.assertEqual(record['pm_session_id'], new_key)
            self.assertEqual(len(record['pm_session_adoptions']), 1)
            audit = record['pm_session_adoptions'][0]
            self.assertEqual(audit['old_session_key'], 'pm-' + self.session)
            self.assertEqual(audit['new_session_key'], new_key)
            self.assertIn('at', audit)
        snapshots = {task_id: self.store.get(task_id) for task_id in result['moved']}
        again = active_pm_inbox.adopt_pm_session(
            self.store, 'pm-' + self.session, new_key,
            old_native_id=self.session, cwd=str(self.pm_cwd))
        self.assertEqual(again, {'moved': [], 'skipped': []})
        self.assertEqual({task_id: self.store.get(task_id) for task_id in result['moved']},
                         snapshots)

    def test_adoption_refusals_leave_records_unchanged(self):
        task = self._record_pm()
        before = self.store.get(task['task_id'])
        attempts = (
            ('pm-' + self.session, 'pm-' + str(uuid.uuid4()), self.session,
             'PM_SESSION_ADOPTION_OLD_SESSION_PRESENT'),
            ('pm-' + self.session, 'pm-' + self.session, self.session,
             'PM_SESSION_ADOPTION_KEYS_EQUAL'),
            ('malformed-key', 'pm-' + str(uuid.uuid4()), self.session,
             'PM_SESSION_ADOPTION_KEY_INVALID'),
        )
        for old_key, new_key, native_id, code in attempts:
            with self.subTest(code=code), self.assertRaises(LLMToolError) as caught:
                active_pm_inbox.adopt_pm_session(
                    self.store, old_key, new_key, old_native_id=native_id,
                    cwd=str(self.pm_cwd))
            self.assertEqual(caught.exception.task_store_error_code, code)
            self.assertEqual(self.store.get(task['task_id']), before)

        for caller_cwd in (str(self.base), str(self.pm_cwd) + '/'):
            with self.subTest(cwd=caller_cwd), self.assertRaises(LLMToolError) as caught:
                active_pm_inbox.adopt_pm_session(
                    self.store, 'pm-' + self.session, 'pm-' + str(uuid.uuid4()),
                    old_native_id=self.session, cwd=caller_cwd)
            self.assertEqual(caught.exception.task_store_error_code,
                             'PM_SESSION_ADOPTION_CWD_MISMATCH')
            self.assertEqual(self.store.get(task['task_id']), before)

    def test_codex_adoption_uses_recorded_runtime_and_observes_new_session(self):
        old_native, old_path = self._codex_session()
        task = self._record_pm(session=old_native, runtime='codex')
        new_native, _ = self._codex_session()
        old_path.unlink()
        before = self.store.get(task['task_id'])
        with self.assertRaises(LLMToolError) as caught:
            active_pm_inbox.adopt_pm_session(
                self.store, 'pm-' + old_native, 'pm-' + new_native,
                old_native_id=old_native, cwd=str(self.pm_cwd) + '/')
        self.assertEqual(caught.exception.task_store_error_code,
                         'PM_SESSION_ADOPTION_CWD_MISMATCH')
        self.assertEqual(self.store.get(task['task_id']), before)
        result = active_pm_inbox.adopt_pm_session(
            self.store, 'pm-' + old_native, 'pm-' + new_native,
            old_native_id=old_native, cwd=str(self.pm_cwd))
        self.assertEqual(result, {'moved': [task['task_id']], 'skipped': []})
        moved = self.store.get(task['task_id'])
        self.assertEqual(moved['pm_session_id'], 'pm-' + new_native)
        self.assertEqual(moved['pm_provenance']['pm_runtime'], 'codex')
        self.assertEqual(moved['pm_provenance']['pm_native_session_id'], new_native)

    def test_adoption_refuses_task_without_session_provenance(self):
        task = self._record_pm()
        before = self.store.get(task['task_id'])
        legacy = dict(before)
        legacy.pop('pm_provenance')
        old_path = active_pm_inbox.claude_session_file(self.session, str(self.pm_cwd))
        old_path.unlink()
        with patch.object(self.store, 'get', return_value=legacy):
            with self.assertRaises(LLMToolError) as caught:
                active_pm_inbox.adopt_pm_session(
                    self.store, 'pm-' + self.session, 'pm-' + str(uuid.uuid4()),
                    old_native_id=self.session, cwd=str(self.pm_cwd))
        self.assertEqual(caught.exception.task_store_error_code,
                         'PM_SESSION_ADOPTION_PROVENANCE_REQUIRED')
        self.assertEqual(self.store.get(task['task_id']), before)

    def test_adoption_skips_compare_and_swap_conflict_and_moves_other_tasks(self):
        conflicted = self._record_pm()
        movable = self._record_pm()
        old_key = 'pm-' + self.session
        new_key = 'pm-' + self._claude_session()
        self.store.get(conflicted['task_id'])
        original_adopt = self.store.adopt_pm_session

        def race(task_id, **kwargs):
            if task_id == conflicted['task_id']:
                current = self.store.get(task_id)
                current['pm_session_id'] = 'pm-' + str(uuid.uuid4())
                current['pm_provenance']['pm_native_session_id'] = current[
                    'pm_session_id'][3:]
                current['record_sha256'] = task_store_module._digest(current)
                self.store._write(current, create=False)
            return original_adopt(task_id, **kwargs)

        old_path = active_pm_inbox.claude_session_file(self.session, str(self.pm_cwd))
        old_path.unlink()
        with patch.object(self.store, 'adopt_pm_session', side_effect=race):
            result = active_pm_inbox.adopt_pm_session(
                self.store, old_key, new_key, old_native_id=self.session,
                cwd=str(self.pm_cwd))
        self.assertEqual(result['skipped'], [conflicted['task_id']])
        self.assertEqual(result['moved'], [movable['task_id']])
        self.assertNotEqual(self.store.get(conflicted['task_id'])['pm_session_id'], old_key)
        self.assertEqual(self.store.get(movable['task_id'])['pm_session_id'], new_key)

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

    def test_codex_session_is_verified_against_transcript_metadata_and_cwd(self):
        native, _ = self._codex_session()
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(run_task_record_pm(Namespace(
                stdin_json=True, pm_session_id=native, pm_runtime='codex',
                pm_cwd=str(self.pm_cwd)), store=self.store,
                input_stream=StringIO(json.dumps({'request': 'Add a bounded test.',
                                                  'response': _RESPONSE}))), 0)
        view = json.loads(output.getvalue())
        self.assertEqual(view['pm_provenance']['pm_runtime'], 'codex')
        self.assertEqual(view['pm_provenance']['pm_native_session_id'], native)
        self.assertEqual(view['pm_provenance']['pm_cwd'], str(self.pm_cwd))
        self.assertEqual(view['pm_recovery'], {
            'runtime': 'codex', 'cwd': str(self.pm_cwd),
            'resume_argv': ['codex', 'resume', native]})

    def test_codex_session_refuses_missing_malformed_wrong_cwd_and_unsafe_path(self):
        missing = str(uuid.uuid4())
        with self.assertRaises(LLMToolError):
            active_pm_inbox.observe_codex_session(missing, str(self.pm_cwd))

        wrong, _ = self._codex_session(cwd=self.base)
        malformed, malformed_path = self._codex_session()
        malformed_path.write_text('{bad json}\n')
        symlinked, _ = self._codex_session()
        target = self.codex_home / 'sessions' / '2026' / '10' / '30'
        unsafe = target / ('rollout-20261030-120000-' + symlinked + '.jsonl')
        unsafe.unlink()
        unsafe.symlink_to(self.pm_cwd / 'transcript.jsonl')
        (self.pm_cwd / 'transcript.jsonl').write_text('{}\n')
        for native, cwd in ((wrong, self.pm_cwd), (malformed, self.pm_cwd),
                            (symlinked, self.pm_cwd)):
            with self.subTest(native=native), self.assertRaises(LLMToolError):
                active_pm_inbox.observe_codex_session(native, str(cwd))
        self.assertEqual(self.store.task_ids(), [])

    def test_task_store_rejects_inconsistent_session_binding(self):
        record = self.store.get(self._record_pm()['task_id'])
        for field, value in (('pm_native_session_id', str(uuid.uuid4())),
                             ('pm_runtime', 'unknown-runtime'), ('pm_cwd', 'relative/dir'),
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

    def test_peek_reports_ready_event_without_writing_then_sees_recorded_event(self):
        task = self._ready_task()
        # Keep this READY handoff in the crash window before event recording.
        # Normal completion records it as part of run_task_complete.
        with patch('llmtool.active_pm_inbox.record_ready_event', return_value=None):
            self._complete(task)
        path = self.store.root / (task['task_id'] + '.json')
        before_bytes = path.read_bytes()
        before = self.store.get(task['task_id'])
        self.assertEqual(before.get('completion_events', []), [])
        with patch('llmtool.active_pm_inbox.record_ready_event',
                   side_effect=AssertionError('peek must not record events')):
            peek = active_pm_inbox.peek_inbox(self.store, 'pm-' + self.session)
        self.assertEqual(path.read_bytes(), before_bytes)
        self.assertEqual(self.store.get(task['task_id'])['record_sha256'],
                         before['record_sha256'])
        self.assertEqual(peek['events'], [])
        self.assertEqual(peek['pending'], [{
            'task_id': task['task_id'],
            'event_id': active_pm_inbox._handoff_event(
                before, _active_pm_handoff(before))['event_id'],
            'source': 'DIRECT_D6_DEVELOPER', 'session_id': before['developer']['session_id'],
            'state': 'NOT_YET_RECORDED',
        }])
        inbox = active_pm_inbox.read_inbox(self.store, 'pm-' + self.session)
        self.assertEqual(len(inbox['events']), 1)
        after = active_pm_inbox.peek_inbox(self.store, 'pm-' + self.session)
        self.assertEqual(after['pending'], [])
        self.assertEqual([event['event_id'] for event in after['events']],
                         [inbox['events'][0]['event_id']])

    def test_peek_excludes_acked_events_and_counts_unreadable_tasks(self):
        task = self._ready_task()
        self._complete(task)
        (event,) = self._inbox()['events']
        self._ack(event['event_id'])
        peek = active_pm_inbox.peek_inbox(self.store, 'pm-' + self.session)
        self.assertEqual(peek['events'], [])
        self.assertEqual(peek['pending'], [])

        other = self._ready_task()
        original_get = self.store.get

        def unreadable(task_id):
            if task_id == other['task_id']:
                raise LLMToolError('unreadable test task')
            return original_get(task_id)

        with patch.object(self.store, 'get', side_effect=unreadable):
            unreadable_peek = active_pm_inbox.peek_inbox(
                self.store, 'pm-' + self.session)
        self.assertEqual(unreadable_peek['unreadable_task_count'], 1)
        self.assertEqual(unreadable_peek['status'], 'ATTENTION')

    def test_inbox_index_matches_full_scan_and_skips_other_sessions(self):
        task = self._ready_task()
        self._complete(task)
        other_session = self._claude_session()
        other = self._ready_task(session=other_session)
        unreadable_id = uuid.uuid4().hex
        (self.store.root / (unreadable_id + '.json')).write_text('{')

        # Warm the disposable index, including its unreadable entry and the
        # newly written Task for the other PM.
        indexed = active_pm_inbox.read_inbox(self.store, 'pm-' + self.session)
        self.assertEqual(indexed['unreadable_task_count'], 1)
        self.assertEqual(len(indexed['events']), 1)
        newly_written = self._ready_task()

        calls = []
        original_get = self.store.get

        def counting_get(task_id):
            calls.append(task_id)
            return original_get(task_id)

        with patch.object(self.store, 'get', side_effect=counting_get):
            active_pm_inbox.read_inbox(self.store, 'pm-' + self.session)
        self.assertNotIn(other['task_id'], calls)
        self.assertIn(task['task_id'], calls)
        self.assertIn(newly_written['task_id'], calls)

        with patch.object(active_pm_inbox, 'TaskStatusIndex',
                          side_effect=lambda store: _DisabledTaskIndex(store)):
            full_scan = active_pm_inbox.read_inbox(self.store, 'pm-' + self.session)
        self.assertEqual(indexed, full_scan)

        event_id = indexed['events'][0]['event_id']
        active_pm_inbox.ack(self.store, event_id, 'pm-' + self.session)
        indexed_ack = active_pm_inbox.ack(self.store, event_id, 'pm-' + self.session)
        with patch.object(active_pm_inbox, 'TaskStatusIndex',
                          side_effect=lambda store: _DisabledTaskIndex(store)):
            full_scan_ack = active_pm_inbox.ack(
                self.store, event_id, 'pm-' + self.session)
        self.assertEqual(indexed_ack, full_scan_ack)

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

    def test_remote_test_receipt_event_reaches_original_pm_inbox(self):
        task = self._ready_task()
        receipt = {
            'task_id': task['task_id'], 'run_id': task['run_id'],
            'pm_session': task['pm_session_id'], 'executor_task': self.task_id,
            'source_sha': 'a' * 40, 'modules': ['test_remote_test'],
            'receipt_digest': 'b' * 64,
        }
        active_pm_inbox.record_remote_test_event(self.store, task['task_id'], receipt)
        events = self._inbox()['events']
        remote_events = [event for event in events if event['source'] == 'EC2_REMOTE_TEST']
        self.assertEqual(len(remote_events), 1)
        self.assertEqual(remote_events[0]['task_id'], task['task_id'])
        self.assertEqual(remote_events[0]['source_sha'], receipt['source_sha'])

    def test_remote_test_outcome_event_is_durable_and_reaches_original_pm(self):
        task = self._ready_task()
        outcome = {'result': 'UNKNOWN', 'code': 'RECEIPT_INVALID',
                   'executor_task': self.task_id, 'sha': 'a' * 40,
                   'modules': ['test_remote_test'], 'at': '2026-10-27T00:00:00Z'}
        saved = self.store.append_remote_test_outcome(task['task_id'], outcome)
        first = active_pm_inbox.record_remote_test_outcome_event(
            self.store, task['task_id'], saved['outcome'], saved['counter'])
        replay = active_pm_inbox.record_remote_test_outcome_event(
            self.store, task['task_id'], saved['outcome'], saved['counter'])
        self.assertEqual(first['event_id'], replay['event_id'])
        later = TaskStore(self.store.root)
        events = self._inbox(store=later)['events']
        remote = [event for event in events if event['source'] == 'EC2_REMOTE_TEST']
        self.assertEqual(len(remote), 1)
        self.assertEqual(remote[0]['event_id'], first['event_id'])
        self.assertNotEqual(outcome['result'], 'PASS')

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

    # Retention and the event cap ------------------------------------------

    def _fill(self, task, count, *, ack=False):
        """Store ``count`` unrelated events on ``task`` (optionally acknowledged)."""
        pm = 'pm-' + self.session
        ids = []
        for sequence in range(count):
            event_id = uuid.uuid4().hex
            self.store.record_completion_event(
                task['task_id'], event_id=event_id, run_id=task['run_id'],
                session_id='ses_Filler', source='HOST_WORKER', source_sha='a' * 40,
                sequence=sequence, payload_sha256=hashlib.sha256(
                    event_id.encode()).hexdigest())
            if ack:
                self._ack(event_id)
            ids.append(event_id)
        return ids

    def test_full_list_of_acked_events_compacts_oldest_and_still_delivers(self):
        task = self._ready_task()
        cap = task_store_module.MAX_COMPLETION_EVENTS
        filler = self._fill(task, cap, ack=True)
        code, view = self._complete(task)
        self.assertEqual(code, 0)
        saved = self.store.get(task['task_id'])
        self.assertEqual(len(saved['completion_events']), cap)
        (tombstone,) = saved['completion_event_tombstones']
        self.assertEqual(tombstone['event_id'], filler[0])
        inbox = self._inbox()
        self.assertEqual(inbox['status'], 'OK')
        self.assertEqual(inbox['blocked'], [])
        (event,) = inbox['events']
        self.assertIs(event['current'], True)
        # A compacted, acked event is never recreated or re-delivered.
        replay = self.store.record_completion_event(
            task['task_id'], event_id=filler[0], run_id=task['run_id'],
            session_id='ses_Filler', source='HOST_WORKER', source_sha='a' * 40,
            sequence=cap + 5, payload_sha256='f' * 64)
        self.assertIs(replay['created'], False)
        self.assertIs(replay['event']['compacted'], True)
        self.assertEqual(len(self.store.get(task['task_id'])['completion_events']), cap)
        # Stream positions of compacted events cannot be reused.
        with self.assertRaises(LLMToolError):
            self.store.record_completion_event(
                task['task_id'], event_id=uuid.uuid4().hex, run_id=task['run_id'],
                session_id='ses_Filler', source='HOST_WORKER', source_sha='a' * 40,
                sequence=0, payload_sha256='e' * 64)

    def test_full_list_of_unacked_events_is_a_visible_blocked_state_with_recovery(self):
        task = self._ready_task()
        cap = task_store_module.MAX_COMPLETION_EVENTS
        filler = self._fill(task, cap)
        code, view = self._complete(task)
        self.assertEqual(code, 0)  # the evidence itself is durable
        self.assertEqual(view['completion']['state'], 'PM_EVIDENCE_READY')
        inbox = self._inbox(wait=True, timeout=30,
                            sleep=lambda seconds: self.fail('blocked must return at once'))
        self.assertEqual(inbox['status'], 'ATTENTION')
        self.assertIs(inbox['timed_out'], False)
        self.assertEqual(inbox['blocked'], [{
            'task_id': task['task_id'], 'code': 'COMPLETION_EVENT_LIMIT',
            'unacknowledged_event_count': cap,
            'action': 'ACK_DELIVERED_EVENTS_THEN_READ_INBOX'}])
        self.assertEqual(len(inbox['events']), cap)
        self.assertFalse(any(event['current'] for event in inbox['events']))
        with self.assertRaises(LLMToolError) as limit:
            self.store.record_completion_event(
                task['task_id'], event_id=uuid.uuid4().hex, run_id=task['run_id'],
                session_id='ses_Filler', source='HOST_WORKER', source_sha='a' * 40,
                sequence=cap, payload_sha256='e' * 64)
        self.assertEqual(limit.exception.task_store_error_code, 'COMPLETION_EVENT_LIMIT')
        # Recovery: ack what was delivered, then read again.
        self._ack(filler[0])
        inbox = self._inbox()
        self.assertEqual(inbox['blocked'], [])
        self.assertEqual(inbox['status'], 'OK')
        current = [event for event in inbox['events'] if event['current']]
        self.assertEqual(len(current), 1)
        self.assertEqual(len(inbox['events']), cap)

    def test_tombstone_history_is_never_dropped_and_full_history_is_visible(self):
        task = self._ready_task()
        with patch.object(task_store_module, 'MAX_COMPLETION_EVENTS', 2), \
                patch.object(task_store_module, 'MAX_COMPLETION_TOMBSTONES', 1):
            ids = self._fill(task, 3, ack=True)
            saved = self.store.get(task['task_id'])
            self.assertEqual([event['event_id'] for event in saved['completion_events']],
                             ids[1:])
            self.assertEqual([item['event_id']
                              for item in saved['completion_event_tombstones']], [ids[0]])
            # A further event would need a second tombstone: refused, not evicted.
            with self.assertRaises(LLMToolError) as full:
                self.store.record_completion_event(
                    task['task_id'], event_id=uuid.uuid4().hex, run_id=task['run_id'],
                    session_id='ses_Filler', source='HOST_WORKER', source_sha='a' * 40,
                    sequence=3, payload_sha256='e' * 64)
            self.assertEqual(full.exception.task_store_error_code,
                             'COMPLETION_EVENT_HISTORY_LIMIT')
            # Replaying the compacted event is still inert (no re-delivery).
            replay = self.store.record_completion_event(
                task['task_id'], event_id=ids[0], run_id=task['run_id'],
                session_id='ses_Filler', source='HOST_WORKER', source_sha='a' * 40,
                sequence=9, payload_sha256='f' * 64)
            self.assertIs(replay['created'], False)
            self.assertIs(replay['event']['compacted'], True)
            # A repeat ack of the compacted event is the same idempotent success.
            again = self._ack(ids[0])
            self.assertIs(again['acknowledged_now'], False)
            self.assertEqual(again['event']['state'], 'ACKED')
            self.assertEqual(again['task_id'], task['task_id'])
            # The READY result cannot be recorded: shown, never silently lost.
            code, _ = self._complete(task)
            self.assertEqual(code, 0)
            inbox = self._inbox(wait=True, timeout=30,
                                sleep=lambda seconds: self.fail('must return at once'))
            self.assertEqual(inbox['status'], 'ATTENTION')
            self.assertEqual(inbox['events'], [])
            self.assertEqual(inbox['blocked'], [{
                'task_id': task['task_id'], 'code': 'COMPLETION_EVENT_HISTORY_LIMIT',
                'unacknowledged_event_count': 0, 'action': 'START_NEW_TASK_OR_RUN'}])
            after = self.store.get(task['task_id'])
            self.assertEqual(after['completion_events'], saved['completion_events'])
            self.assertEqual(after['completion_event_tombstones'],
                             saved['completion_event_tombstones'])

    def test_tombstones_are_store_only(self):
        task = self._ready_task()
        self._fill(task, task_store_module.MAX_COMPLETION_EVENTS + 1, ack=True)
        saved = self.store.get(task['task_id'])
        self.assertEqual(len(saved['completion_event_tombstones']), 1)
        with self.assertRaises(LLMToolError) as forged:
            self.store.update(task['task_id'], completion_event_tombstones=[])
        self.assertEqual(forged.exception.task_store_error_code,
                         'COMPLETION_EVENT_MUTATION_FORBIDDEN')
        with self.assertRaises(LLMToolError):
            TaskStore(self.base / 'copy').create(
                {key: value for key, value in saved.items()
                 if key not in ('completion_events', 'record_sha256')} |
                {'completion_event_tombstones': saved['completion_event_tombstones']})

    # Concurrency ----------------------------------------------------------

    def test_two_completions_racing_on_one_task_store_one_event(self):
        task = self._ready_task()
        git, tests, _ = self._mock_complete_evidence(task)
        barrier = threading.Barrier(2)
        outcomes = []

        def collect(**kwargs):
            barrier.wait(timeout=10)
            return git

        def run():
            try:
                outcomes.append(run_task_complete(Namespace(task_id=task['task_id'],
                    executor_task=self.task_id, reviewer_task=None, pr=None),
                    store=TaskStore(self.store.root)))
            except LLMToolError:
                outcomes.append('CONFLICT')

        forbidden = AssertionError('no provider call')
        with patch('llmtool.task_submission_cli.task_completion.collect_executor_result',
                   side_effect=collect), \
             patch('llmtool.task_submission_cli.task_completion.run_bounded_tests',
                   return_value=tests), \
             patch('llmtool.task_submission_cli.resolve_profile_config',
                   side_effect=forbidden), \
             redirect_stdout(StringIO()):
            threads = [threading.Thread(target=run) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
        self.assertIn(0, outcomes)
        self.assertEqual(len(outcomes), 2)
        saved = self.store.get(task['task_id'])
        self.assertEqual(saved['completion']['state'], 'PM_EVIDENCE_READY')
        self.assertEqual(len(saved['completion_events']), 1)
        self.assertEqual(len(self._inbox()['events']), 1)

    def test_concurrent_event_writers_neither_duplicate_nor_lose_events(self):
        task = self._ready_task()
        with patch('llmtool.active_pm_inbox.record_ready_event',
                   side_effect=LLMToolError('defer event')):
            self._complete(task)
        barrier = threading.Barrier(4)
        errors = []

        def ready_event():
            barrier.wait(timeout=10)
            try:
                active_pm_inbox.record_ready_event(TaskStore(self.store.root),
                                                   task['task_id'])
            except LLMToolError as exc:
                errors.append(exc)

        def other_event(sequence):
            barrier.wait(timeout=10)
            event_id = uuid.uuid4().hex
            try:
                TaskStore(self.store.root).record_completion_event(
                    task['task_id'], event_id=event_id, run_id=task['run_id'],
                    session_id='ses_Other' + str(sequence), source='HOST_WORKER',
                    source_sha='a' * 40, sequence=0, payload_sha256='d' * 64)
            except LLMToolError as exc:
                errors.append(exc)

        threads = [threading.Thread(target=ready_event) for _ in range(2)]
        threads += [threading.Thread(target=other_event, args=(n,)) for n in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(errors, [])
        events = self.store.get(task['task_id'])['completion_events']
        self.assertEqual(len(events), 3)
        self.assertEqual(sum(event['source'] == 'DIRECT_D6_DEVELOPER' for event in events), 1)

    # Session-file checks ----------------------------------------------------

    def test_session_file_must_be_a_regular_file_owned_by_the_user(self):
        target = self.base / 'elsewhere.jsonl'
        target.write_text('{}\n')
        linked = str(uuid.uuid4())
        path = active_pm_inbox.claude_session_file(linked, str(self.pm_cwd))
        path.symlink_to(target)
        with self.assertRaises(LLMToolError):
            active_pm_inbox.observe_claude_session(linked, str(self.pm_cwd))
        directory = str(uuid.uuid4())
        active_pm_inbox.claude_session_file(directory, str(self.pm_cwd)).mkdir()
        with self.assertRaises(LLMToolError):
            active_pm_inbox.observe_claude_session(directory, str(self.pm_cwd))
        with patch('llmtool.active_pm_inbox.os.getuid', return_value=os.getuid() + 1), \
                self.assertRaises(LLMToolError):
            active_pm_inbox.observe_claude_session(self.session, str(self.pm_cwd))
        self.assertEqual(active_pm_inbox.observe_claude_session(
            self.session, str(self.pm_cwd)), str(self.pm_cwd))

    def test_symlinked_directories_in_the_session_path_are_refused(self):
        real = self.base / 'real-projects'
        for level in ('project', 'projects', 'config'):
            native = str(uuid.uuid4())
            path = active_pm_inbox.claude_session_file(native, str(self.pm_cwd))
            target = real / level / native
            target.mkdir(parents=True)
            (target / path.name).write_text('{}\n')
            config = self.base / ('config-' + level)
            env = {'CLAUDE_CONFIG_DIR': str(config)}
            with self.subTest(level=level), patch.dict(os.environ, env):
                path = active_pm_inbox.claude_session_file(native, str(self.pm_cwd))
                if level == 'project':
                    path.parent.parent.mkdir(parents=True)
                    path.parent.symlink_to(target)
                elif level == 'projects':
                    config.mkdir()
                    (target / path.parent.name).mkdir()
                    (target / path.parent.name / path.name).write_text('{}\n')
                    path.parent.parent.symlink_to(target)
                else:
                    (target / 'projects' / path.parent.name).mkdir(parents=True)
                    (target / 'projects' / path.parent.name / path.name).write_text('{}\n')
                    config.symlink_to(target)
                self.assertTrue(path.exists())  # reachable only through the symlink
                with self.assertRaises(LLMToolError):
                    active_pm_inbox.observe_claude_session(native, str(self.pm_cwd))

    def test_session_lookup_needs_an_absolute_configuration_directory(self):
        for environ in ({}, {'HOME': ''}, {'CLAUDE_CONFIG_DIR': 'relative/claude'},
                        {'HOME': 'relative-home'}):
            with self.subTest(environ=environ), \
                    patch.dict(os.environ, environ, clear=True), \
                    self.assertRaises(LLMToolError):
                active_pm_inbox.observe_claude_session(self.session, str(self.pm_cwd))
        home = self.base / 'only-home'
        native = str(uuid.uuid4())
        with patch.dict(os.environ, {'HOME': str(home)}, clear=True):
            path = active_pm_inbox.claude_session_file(native, str(self.pm_cwd))
            self.assertEqual(path.parents[2], home / '.claude')
            path.parent.mkdir(parents=True)
            path.write_text('{}\n')
            self.assertEqual(active_pm_inbox.observe_claude_session(
                native, str(self.pm_cwd)), str(self.pm_cwd))

    def test_cwd_encoding_matches_claude_code_project_directories(self):
        base = active_pm_inbox.claude_session_file('x', '/').parent.parent
        cases = {
            '/Users/me/git/repo': '-Users-me-git-repo',
            '/Users/me/.local/share/w-x': '-Users-me--local-share-w-x',
            '/a/b.c_d e/\u00e9~x': '-a-b-c-d-e---x',
            '/': '-',
        }
        for cwd, encoded in cases.items():
            with self.subTest(cwd=cwd):
                self.assertEqual(active_pm_inbox.claude_session_file('x', cwd),
                                 base / encoded / 'x.jsonl')
        # The stored and looked-up directory is the normalized absolute path.
        (self.pm_cwd / 'sub').mkdir()
        dotted = str(self.pm_cwd) + '/sub/../.'
        self.assertEqual(active_pm_inbox.observe_claude_session(self.session, dotted),
                         str(self.pm_cwd))
        self.assertEqual(self._record_pm(cwd=dotted)['pm_provenance']['pm_cwd'],
                         str(self.pm_cwd))

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
