import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from llmtool import pm_caller
from llmtool import task_submission_cli
from llmtool.cli import parser_for_cli
from llmtool.task_store import TaskStore, new_id

OWNER = '3f2b8c1e-4d5a-4b6c-8d7e-9f0a1b2c3d4e'
OTHER = '7a6b5c4d-3e2f-4a1b-9c8d-7e6f5a4b3c2d'
CODEX = '019a3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d'


def _environment(claude='', codex=''):
    """Patch both caller variables; an empty value counts as unset."""
    return patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': claude,
                                   'CODEX_THREAD_ID': codex})


class _MutableStore:
    """A Task whose PM session can be changed between drive steps."""

    def __init__(self, record):
        self.record = record

    def get(self, _task_id):
        return dict(self.record)


class PMCallerStoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve()
        home = patch.dict(os.environ, {'HOME': str(self.home)})
        home.start()
        self.addCleanup(home.stop)
        self.store = TaskStore(self.home / 'tasks')

    def _task(self, native):
        request = 'Bounded non-sensitive caller check task.'
        task_id, run_id = new_id(), new_id()
        task = task_submission_cli._record(
            task_id, run_id, 'pm-' + native, 'pm-profile', request,
            state='PM_RESPONDED', provider_type='openai-compatible', model='pm-model',
            backend='NO_NATIVE_SESSION_ID_EXPOSED',
            request_sha=hashlib.sha256(request.encode()).hexdigest())
        task.update(pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                                      'base_url': None},
                    response='PM returned a bounded task.', error_code=None,
                    pm_assignment={'schema_version': 1, 'route': 'codex', 'risk': 'LOW',
                                   'goal': 'Check the caller.',
                                   'acceptance': ['the caller owns the Task']})
        self.store.create(task)
        self.assertEqual(self.store.get(task_id)['pm_session_id'], 'pm-' + native)
        return task_id

    def _refused(self, code, task_id, explicit=None):
        with self.assertRaises(pm_caller.PMCallerRefused) as caught:
            pm_caller.check_caller(self.store, task_id, explicit)
        self.assertEqual(caught.exception.code, code)

    def test_owner_from_claude_environment_is_allowed(self):
        task_id = self._task(OWNER)
        with _environment(claude=OWNER):
            self.assertEqual(pm_caller.check_caller(self.store, task_id), 'pm-' + OWNER)

    def test_other_session_is_refused(self):
        task_id = self._task(OWNER)
        with _environment(claude=OTHER):
            self._refused('PM_CALLER_NOT_TASK_OWNER', task_id)
        with _environment():
            self._refused('PM_CALLER_NOT_TASK_OWNER', task_id, explicit=OTHER)
            self._refused('PM_CALLER_NOT_TASK_OWNER', task_id, explicit='pm-' + OTHER)

    def test_missing_identity_is_refused(self):
        task_id = self._task(OWNER)
        with _environment():
            self._refused('PM_CALLER_SESSION_MISSING', task_id)
        with patch.dict(os.environ):
            os.environ.pop('CLAUDE_CODE_SESSION_ID', None)
            os.environ.pop('CODEX_THREAD_ID', None)
            self._refused('PM_CALLER_SESSION_MISSING', task_id)

    def test_invalid_identity_is_refused(self):
        task_id = self._task(OWNER)
        with _environment():
            self._refused('PM_CALLER_SESSION_INVALID', task_id, explicit='not a session')
            self._refused('PM_CALLER_SESSION_INVALID', task_id, explicit='')
        with _environment(claude='not a session'):
            self._refused('PM_CALLER_SESSION_INVALID', task_id)
        with _environment(codex='not a session'):
            self._refused('PM_CALLER_SESSION_INVALID', task_id)

    def test_ambiguous_identity_is_refused(self):
        task_id = self._task(OWNER)
        with _environment(claude=OWNER, codex=CODEX):
            self._refused('PM_CALLER_SESSION_AMBIGUOUS', task_id)
            # An explicit value that matches one of them picks it.
            self.assertEqual(pm_caller.check_caller(self.store, task_id, OWNER),
                             'pm-' + OWNER)

    def test_conflicting_identity_is_refused(self):
        task_id = self._task(OWNER)
        with _environment(claude=OTHER):
            self._refused('PM_CALLER_SESSION_CONFLICT', task_id, explicit=OWNER)
        with _environment(codex=CODEX):
            self._refused('PM_CALLER_SESSION_CONFLICT', task_id, explicit=OWNER)
        with _environment(claude=OTHER, codex=CODEX):
            self._refused('PM_CALLER_SESSION_CONFLICT', task_id, explicit=OWNER)
        with _environment(claude=OWNER):
            self.assertEqual(pm_caller.check_caller(self.store, task_id, 'pm-' + OWNER),
                             'pm-' + OWNER)

    def test_explicit_value_is_used_without_environment(self):
        task_id = self._task(OWNER)
        with _environment():
            self.assertEqual(pm_caller.check_caller(self.store, task_id, OWNER),
                             'pm-' + OWNER)

    def test_codex_thread_owner_is_allowed(self):
        task_id = self._task(CODEX)
        with _environment(codex=CODEX):
            self.assertEqual(pm_caller.check_caller(self.store, task_id), 'pm-' + CODEX)
        with _environment(claude=OWNER):
            self._refused('PM_CALLER_NOT_TASK_OWNER', task_id)

    def test_unreadable_or_unowned_record_is_refused(self):
        with _environment(claude=OWNER):
            self._refused('TASK_RECORD_UNAVAILABLE', new_id())
            no_owner = _MutableStore({'task_id': 't'})
            with self.assertRaises(pm_caller.PMCallerRefused) as caught:
                pm_caller.check_caller(no_owner, 't')
            self.assertEqual(caught.exception.code, 'PM_CALLER_NOT_TASK_OWNER')

    def test_caller_is_resolved_without_reading_the_task(self):
        store = Mock()
        with _environment():
            with self.assertRaises(pm_caller.PMCallerRefused):
                pm_caller.check_caller(store, 't')
        store.get.assert_not_called()

    def test_advance_adapter_refuses_before_advance_is_called(self):
        task_id = self._task(OWNER)
        args = parser_for_cli().parse_args(['task', 'advance', task_id, '--merge'])
        output = io.StringIO()
        with _environment(claude=OTHER), \
                patch('llmtool.task_advance.advance') as advance, \
                redirect_stdout(output):
            code = task_submission_cli.run_task_advance(args, store=self.store)
        advance.assert_not_called()
        self.assertEqual(code, 1)
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0]), {
            'task_id': task_id, 'step': 'REFUSED', 'result': None, 'next': 'REFUSED',
            'codes': ['PM_CALLER_NOT_TASK_OWNER'], 'acked': []})

    def test_advance_adapter_refuses_missing_identity(self):
        task_id = self._task(OWNER)
        args = parser_for_cli().parse_args(['task', 'advance', task_id])
        output = io.StringIO()
        with _environment(), patch('llmtool.task_advance.advance') as advance, \
                redirect_stdout(output):
            code = task_submission_cli.run_task_advance(args, store=self.store)
        advance.assert_not_called()
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())['codes'],
                         ['PM_CALLER_SESSION_MISSING'])

    def test_advance_adapter_runs_for_owner(self):
        task_id = self._task(OWNER)
        args = parser_for_cli().parse_args(['task', 'advance', task_id,
                                            '--pm-session', OWNER])
        output = io.StringIO()
        with _environment(), patch('llmtool.task_advance.advance', return_value={
                'task_id': task_id, 'step': 'CLOSED', 'codes': []}) as advance, \
                redirect_stdout(output):
            code = task_submission_cli.run_task_advance(args, store=self.store)
        self.assertEqual(code, 0)
        advance.assert_called_once()
        self.assertEqual(json.loads(output.getvalue())['step'], 'CLOSED')

    def test_drive_adapter_refuses_non_owner_before_any_call(self):
        task_id = self._task(OWNER)
        args = parser_for_cli().parse_args(['task', 'drive', task_id, '--merge'])
        advance, label = Mock(), Mock()
        output = io.StringIO()
        with _environment(claude=OTHER), redirect_stdout(output):
            code = task_submission_cli.run_task_drive(
                args, store=self.store, sleep=lambda _seconds: None,
                advance_call=advance, label_mac_ci_call=label)
        self.assertEqual(code, 1)
        advance.assert_not_called()
        label.assert_not_called()
        self.assertEqual(json.loads(output.getvalue()), {
            'task_id': task_id, 'step': 'REFUSED', 'result': None, 'next': 'REFUSED',
            'codes': ['PM_CALLER_NOT_TASK_OWNER'], 'acked': []})


class PMCallerDriveTests(unittest.TestCase):
    def _drive(self, store, advance, label, *argv):
        args = parser_for_cli().parse_args(['task', 'drive', 't', *argv])
        output = io.StringIO()
        with _environment(claude=OWNER), redirect_stdout(output):
            code = task_submission_cli.run_task_drive(
                args, store=store, sleep=lambda _seconds: None,
                advance_call=advance, label_mac_ci_call=label)
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        return code, json.loads(lines[0])

    def test_drive_stops_at_next_step_when_task_is_adopted_away(self):
        store = _MutableStore({'task_id': 't', 'pm_session_id': 'pm-' + OWNER})
        calls = []

        def advance(**kwargs):
            calls.append(kwargs)
            # Another session adopts the Task while this step runs.
            store.record['pm_session_id'] = 'pm-' + OTHER
            return {'task_id': 't', 'step': 'RUN_REMOTE_TEST', 'codes': []}

        label = Mock()
        code, result = self._drive(store, advance, label)
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 1)
        label.assert_not_called()
        self.assertEqual(result['step'], 'REFUSED')
        self.assertEqual(result['codes'], ['PM_CALLER_NOT_TASK_OWNER'])

    def test_drive_never_calls_label_helper_for_non_owner(self):
        store = _MutableStore({'task_id': 't', 'pm_session_id': 'pm-' + OWNER})
        calls = []

        def advance(**kwargs):
            calls.append(kwargs)
            store.record['pm_session_id'] = 'pm-' + OTHER
            return {'task_id': 't', 'step': 'WAITING_CI',
                    'codes': ['CI_WORKFLOW_JOBS_INVALID']}

        label = Mock(return_value={'step': 'WAITING_CI', 'codes': []})
        code, result = self._drive(store, advance, label, '--merge')
        label.assert_not_called()
        self.assertEqual(len(calls), 1)
        self.assertEqual(code, 1)
        self.assertEqual(result['step'], 'REFUSED')
        self.assertEqual(result['codes'], ['PM_CALLER_NOT_TASK_OWNER'])

    def test_label_refusal_on_last_step_is_not_lost_as_max_steps(self):
        store = _MutableStore({'task_id': 't', 'pm_session_id': 'pm-' + OWNER})
        calls = []

        def advance(**kwargs):
            calls.append(kwargs)
            store.record['pm_session_id'] = 'pm-' + OTHER
            return {'task_id': 't', 'step': 'WAITING_CI',
                    'codes': ['CI_WORKFLOW_JOBS_INVALID']}

        label = Mock(return_value={'step': 'WAITING_CI', 'codes': []})
        code, result = self._drive(store, advance, label, '--merge', '--max-steps', '1')
        label.assert_not_called()
        self.assertEqual(len(calls), 1)
        self.assertEqual(code, 1)
        self.assertEqual(result, {
            'task_id': 't', 'step': 'REFUSED', 'result': None, 'next': 'REFUSED',
            'codes': ['PM_CALLER_NOT_TASK_OWNER'], 'acked': []})

    def test_label_refusal_stops_drive_without_another_advance(self):
        store = _MutableStore({'task_id': 't', 'pm_session_id': 'pm-' + OWNER})
        calls = []
        sleeps = []

        def advance(**kwargs):
            calls.append(kwargs)
            store.record['pm_session_id'] = 'pm-' + OTHER
            return {'task_id': 't', 'step': 'WAITING_CI',
                    'codes': ['CI_WORKFLOW_JOBS_INVALID']}

        label = Mock(return_value={'step': 'WAITING_CI', 'codes': []})
        args = parser_for_cli().parse_args(['task', 'drive', 't', '--merge'])
        output = io.StringIO()
        with _environment(claude=OWNER), redirect_stdout(output):
            code = task_submission_cli.run_task_drive(
                args, store=store, sleep=sleeps.append,
                advance_call=advance, label_mac_ci_call=label)
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(sleeps, [])
        label.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())['codes'],
                         ['PM_CALLER_NOT_TASK_OWNER'])

    def test_drive_calls_label_helper_for_owner(self):
        store = _MutableStore({'task_id': 't', 'pm_session_id': 'pm-' + OWNER})
        results = iter([
            {'task_id': 't', 'step': 'WAITING_CI', 'codes': ['CI_WORKFLOW_JOBS_INVALID']},
            {'task_id': 't', 'step': 'CLOSED', 'codes': []},
        ])
        label = Mock(return_value={'step': 'WAITING_CI', 'codes': []})
        code, result = self._drive(store, lambda **_kwargs: next(results), label, '--merge')
        label.assert_called_once()
        self.assertEqual(code, 0)
        self.assertEqual(result['step'], 'CLOSED')


if __name__ == '__main__':
    unittest.main()
