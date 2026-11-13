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
from llmtool import pm_runtime
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

    def _remote_test(self, task_id, *extra, claude=''):
        argv = ['task', 'remote-test', task_id, '--executor-task', 'executor-1',
                '--sha', 'a' * 40, '--ref', 'refs/heads/feat/x', *extra]
        args = parser_for_cli().parse_args(argv)
        output = io.StringIO()
        with _environment(claude=claude), \
                patch('llmtool.remote_test.run_remote_test',
                      return_value={'result': 'PASS'}) as run:
            code = task_submission_cli.run_task_remote_test_checked(
                args, store=self.store, stdout=output)
        return code, output.getvalue(), run

    def test_remote_test_refuses_missing_identity(self):
        task_id = self._task(OWNER)
        code, output, run = self._remote_test(task_id)
        run.assert_not_called()
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output), {
            'task_id': task_id, 'step': 'REFUSED', 'result': None, 'next': 'REFUSED',
            'codes': ['PM_CALLER_SESSION_MISSING'], 'acked': []})

    def test_remote_test_refuses_non_owner(self):
        task_id = self._task(OWNER)
        for extra, claude in (((), OTHER), (('--pm-session', OTHER), '')):
            with self.subTest(extra=extra, claude=claude):
                code, output, run = self._remote_test(task_id, *extra, claude=claude)
                run.assert_not_called()
                self.assertEqual(code, 1)
                self.assertEqual(json.loads(output)['codes'], ['PM_CALLER_NOT_TASK_OWNER'])

    def test_remote_test_refuses_conflicting_explicit_session(self):
        task_id = self._task(OWNER)
        code, output, run = self._remote_test(task_id, '--pm-session', OWNER, claude=OTHER)
        run.assert_not_called()
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)['codes'], ['PM_CALLER_SESSION_CONFLICT'])

    def test_remote_test_refuses_fenced_owner(self):
        task_id = self._task(OWNER)
        pm_runtime.write_session_fence(OWNER, 'pm-' + OTHER, '2026-11-12T00:00:00Z')
        code, output, run = self._remote_test(task_id, claude=OWNER)
        run.assert_not_called()
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)['codes'], ['PM_CALLER_SESSION_FENCED'])

    def test_remote_test_runs_for_owner(self):
        task_id = self._task(OWNER)
        for extra, claude in ((('--pm-session', OWNER), ''), ((), OWNER)):
            with self.subTest(extra=extra, claude=claude):
                code, output, run = self._remote_test(task_id, *extra, claude=claude)
                self.assertIsNone(code)
                run.assert_called_once()
                self.assertEqual(run.call_args.args, (task_id,))
                self.assertEqual(run.call_args.kwargs['executor_task'], 'executor-1')
                self.assertEqual(json.loads(output), {'result': 'PASS'})

    def test_advance_internal_remote_test_is_not_caller_checked(self):
        from llmtool import task_advance
        task_id = self._task(OWNER)
        with _environment(), patch('llmtool.remote_test.run_remote_test',
                                   return_value={'result': 'PASS'}) as run, \
                redirect_stdout(io.StringIO()):
            result = task_advance._run_remote_test(
                self.store, task_id, 'executor-1', 'a' * 40, 'refs/pull/1/head', 1)
        run.assert_called_once()
        self.assertEqual(result, {'result': 'PASS'})

    def _review_dispatch(self, task_id, *extra, claude=''):
        argv = ['task', 'review-dispatch', task_id, '--executor-task', 'executor-1',
                '--pr', '17', *extra]
        args = parser_for_cli().parse_args(argv)
        output = io.StringIO()
        with _environment(claude=claude), \
                patch('llmtool.review_dispatch.dispatch_review',
                      return_value={'state': 'DISPATCHED'}) as dispatch, \
                redirect_stdout(output):
            code = task_submission_cli.run_task_review_dispatch(args, store=self.store)
        return code, output.getvalue(), dispatch

    def test_review_dispatch_refuses_missing_identity(self):
        task_id = self._task(OWNER)
        code, output, dispatch = self._review_dispatch(task_id)
        dispatch.assert_not_called()
        self.assertEqual(code, 1)
        lines = output.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0]), {
            'task_id': task_id, 'step': 'REFUSED', 'result': None, 'next': 'REFUSED',
            'codes': ['PM_CALLER_SESSION_MISSING'], 'acked': []})

    def test_review_dispatch_refuses_non_owner(self):
        task_id = self._task(OWNER)
        for extra, claude in (((), OTHER), (('--pm-session', OTHER), '')):
            with self.subTest(extra=extra, claude=claude):
                code, output, dispatch = self._review_dispatch(task_id, *extra,
                                                               claude=claude)
                dispatch.assert_not_called()
                self.assertEqual(code, 1)
                self.assertEqual(json.loads(output)['codes'], ['PM_CALLER_NOT_TASK_OWNER'])

    def test_review_dispatch_refuses_conflicting_explicit_session(self):
        task_id = self._task(OWNER)
        code, output, dispatch = self._review_dispatch(task_id, '--pm-session', OWNER,
                                                       claude=OTHER)
        dispatch.assert_not_called()
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)['codes'], ['PM_CALLER_SESSION_CONFLICT'])

    def test_review_dispatch_refuses_fenced_owner(self):
        task_id = self._task(OWNER)
        pm_runtime.write_session_fence(OWNER, 'pm-' + OTHER, '2026-11-12T00:00:00Z')
        code, output, dispatch = self._review_dispatch(task_id, claude=OWNER)
        dispatch.assert_not_called()
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)['codes'], ['PM_CALLER_SESSION_FENCED'])

    def test_review_dispatch_runs_for_owner(self):
        task_id = self._task(OWNER)
        for extra, claude in ((('--pm-session', OWNER), ''), ((), OWNER)):
            with self.subTest(extra=extra, claude=claude):
                code, output, dispatch = self._review_dispatch(task_id, *extra,
                                                               claude=claude)
                self.assertIsNone(code)
                dispatch.assert_called_once_with(
                    store=self.store, task_id=task_id, executor_task='executor-1',
                    pr_number=17, version=1, reviewer=None)
                self.assertEqual(json.loads(output), {'state': 'DISPATCHED'})

    def test_review_dispatch_passes_version_and_reviewer_for_owner(self):
        task_id = self._task(OWNER)
        code, _, dispatch = self._review_dispatch(
            task_id, '--version', '3', '--reviewer', 'codex-reviewer',
            '--pm-session', 'pm-' + OWNER)
        self.assertIsNone(code)
        dispatch.assert_called_once_with(
            store=self.store, task_id=task_id, executor_task='executor-1',
            pr_number=17, version=3, reviewer='codex-reviewer')

    def test_advance_internal_review_dispatch_is_not_caller_checked(self):
        from llmtool import task_advance
        head = 'a' * 40
        store = _MutableStore({'task_id': 't', 'pm_session_id': 'pm-' + OWNER,
                               'completion': {'executor_task_id': 'executor-1'}})
        d6 = {'root': str(self.home), 'branch': 'feat/x', 'dirty': False,
              'result_head': head, 'recorded_result_head': head}
        with _environment(), \
                patch.object(task_advance, '_closed_train_member_head', return_value=None), \
                patch.object(task_advance, '_d6_snapshot', return_value=d6), \
                patch.object(task_advance, '_merged_review_target', return_value=None), \
                patch.object(task_advance, 'decide', return_value='DISPATCH_REVIEW'), \
                patch.object(task_advance, '_review_ci_gate', return_value=(None, None)), \
                patch.object(pm_caller, 'check_caller',
                             wraps=pm_caller.check_caller) as check, \
                patch('llmtool.review_dispatch.dispatch_review',
                      return_value={'state': 'DISPATCHED'}) as dispatch, \
                redirect_stdout(io.StringIO()):
            result = task_advance.advance(task_id='t', store=store, pr_number=17,
                                          github_get=lambda _path: {})
        check.assert_not_called()
        dispatch.assert_called_once_with(store=store, task_id='t',
                                         executor_task='executor-1',
                                         pr_number=17, version=1)
        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        self.assertEqual(result['result'], {'state': 'DISPATCHED'})

    def _repair(self, task_id, *extra, claude=''):
        argv = ['task', 'repair', task_id, '--executor-task', 'executor-1',
                '--pr', '17', *extra]
        args = parser_for_cli().parse_args(argv)
        output = io.StringIO()
        with _environment(claude=claude), \
                patch('llmtool.review_repair.repair_review',
                      return_value={'state': 'REPAIR_DISPATCHED'}) as repair, \
                redirect_stdout(output):
            code = task_submission_cli.run_task_repair(args, store=self.store)
        return code, output.getvalue(), repair

    def test_repair_refuses_missing_identity(self):
        task_id = self._task(OWNER)
        code, output, repair = self._repair(task_id)
        repair.assert_not_called()
        self.assertEqual(code, 1)
        lines = output.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0]), {
            'task_id': task_id, 'step': 'REFUSED', 'result': None, 'next': 'REFUSED',
            'codes': ['PM_CALLER_SESSION_MISSING'], 'acked': []})

    def test_repair_refuses_non_owner(self):
        task_id = self._task(OWNER)
        for extra, claude in (((), OTHER), (('--pm-session', OTHER), '')):
            with self.subTest(extra=extra, claude=claude):
                code, output, repair = self._repair(task_id, *extra, claude=claude)
                repair.assert_not_called()
                self.assertEqual(code, 1)
                lines = output.splitlines()
                self.assertEqual(len(lines), 1)
                self.assertEqual(json.loads(lines[0])['codes'],
                                 ['PM_CALLER_NOT_TASK_OWNER'])

    def test_repair_refuses_conflicting_explicit_session(self):
        task_id = self._task(OWNER)
        code, output, repair = self._repair(task_id, '--pm-session', OWNER, claude=OTHER)
        repair.assert_not_called()
        self.assertEqual(code, 1)
        lines = output.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])['codes'], ['PM_CALLER_SESSION_CONFLICT'])

    def test_repair_refuses_fenced_owner(self):
        task_id = self._task(OWNER)
        pm_runtime.write_session_fence(OWNER, 'pm-' + OTHER, '2026-11-12T00:00:00Z')
        code, output, repair = self._repair(task_id, claude=OWNER)
        repair.assert_not_called()
        self.assertEqual(code, 1)
        lines = output.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])['codes'], ['PM_CALLER_SESSION_FENCED'])

    def test_repair_runs_for_owner(self):
        task_id = self._task(OWNER)
        for extra, claude in ((('--pm-session', OWNER), ''), ((), OWNER)):
            with self.subTest(extra=extra, claude=claude):
                code, output, repair = self._repair(task_id, *extra, claude=claude)
                self.assertIsNone(code)
                repair.assert_called_once_with(
                    store=self.store, task_id=task_id, executor_task='executor-1',
                    pr_number=17, family=None, github_get=None, dispatch_adapter=None,
                    current_head=None, auto_review=False, review_dispatcher=None,
                    delegate_callable=None, repair_task_recorder=None)
                self.assertEqual(json.loads(output), {'state': 'REPAIR_DISPATCHED'})

    def test_repair_passes_family_and_auto_review_for_owner(self):
        task_id = self._task(OWNER)
        code, _, repair = self._repair(task_id, '--family', 'pr-17-root',
                                       '--auto-review', '--pm-session', 'pm-' + OWNER)
        self.assertIsNone(code)
        repair.assert_called_once_with(
            store=self.store, task_id=task_id, executor_task='executor-1',
            pr_number=17, family='pr-17-root', github_get=None, dispatch_adapter=None,
            current_head=None, auto_review=True, review_dispatcher=None,
            delegate_callable=None, repair_task_recorder=None)

    def test_advance_internal_repair_is_not_caller_checked(self):
        from llmtool import task_advance
        head = 'a' * 40
        store = _MutableStore({'task_id': 't', 'pm_session_id': 'pm-' + OWNER,
                               'completion': {'executor_task_id': 'executor-1'}})
        d6 = {'root': str(self.home), 'branch': 'feat/x', 'dirty': False,
              'result_head': head, 'recorded_result_head': head}

        def github_get(_path):
            return {}

        repair_call = Mock(return_value={'state': 'REPAIR_DISPATCHED'})
        with _environment(), \
                patch.object(task_advance, '_closed_train_member_head', return_value=None), \
                patch.object(task_advance, '_d6_snapshot', return_value=d6), \
                patch.object(task_advance, '_merged_review_target', return_value=None), \
                patch.object(task_advance, 'decide', return_value='REPAIR_REVIEW'), \
                patch.object(task_advance, '_remote_test_status',
                             return_value=(None, None, None)), \
                patch.object(pm_caller, 'check_caller',
                             wraps=pm_caller.check_caller) as check, \
                patch('llmtool.review_repair.repair_review') as module_repair, \
                redirect_stdout(io.StringIO()):
            result = task_advance.advance(task_id='t', store=store, pr_number=17,
                                          github_get=github_get,
                                          repair_review_call=repair_call)
        check.assert_not_called()
        module_repair.assert_not_called()
        repair_call.assert_called_once_with(
            store=store, task_id='t', executor_task='executor-1', pr_number=17,
            auto_review=False, github_get=github_get, retry_claim_id=None)
        self.assertEqual(result['step'], 'REPAIR_REVIEW')
        self.assertEqual(result['result'], {'state': 'REPAIR_DISPATCHED'})

    def _delegate(self, task_id, *extra, claude='', returned=0):
        argv = ['task', 'delegate', task_id, '--provider', 'claude',
                '--selection-source', 'PM', '--executor-task', 'executor-1',
                '--read-path', 'a.txt', '--write-path', 'a.txt',
                '--acceptance', 'done', '--egress-basis', 'fixture', *extra]
        args = parser_for_cli().parse_args(argv)
        output = io.StringIO()
        with _environment(claude=claude), \
                patch('llmtool.task_submission_cli.run_task_delegate',
                      return_value=returned) as run, \
                redirect_stdout(output):
            code = task_submission_cli.run_task_delegate_checked(args, store=self.store)
        return code, output.getvalue(), run, args

    def _assert_delegate_refused(self, task_id, code, output, run, expected):
        run.assert_not_called()
        self.assertEqual(code, 1)
        lines = output.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0]), {
            'task_id': task_id, 'step': 'REFUSED', 'result': None, 'next': 'REFUSED',
            'codes': [expected], 'acked': []})

    def test_delegate_refuses_missing_identity(self):
        task_id = self._task(OWNER)
        code, output, run, _ = self._delegate(task_id)
        self._assert_delegate_refused(task_id, code, output, run,
                                      'PM_CALLER_SESSION_MISSING')

    def test_delegate_refuses_non_owner(self):
        task_id = self._task(OWNER)
        for extra, claude in (((), OTHER), (('--pm-session', OTHER), '')):
            with self.subTest(extra=extra, claude=claude):
                code, output, run, _ = self._delegate(task_id, *extra, claude=claude)
                self._assert_delegate_refused(task_id, code, output, run,
                                              'PM_CALLER_NOT_TASK_OWNER')

    def test_delegate_refuses_conflicting_explicit_session(self):
        task_id = self._task(OWNER)
        code, output, run, _ = self._delegate(task_id, '--pm-session', OWNER, claude=OTHER)
        self._assert_delegate_refused(task_id, code, output, run,
                                      'PM_CALLER_SESSION_CONFLICT')

    def test_delegate_refuses_fenced_owner(self):
        task_id = self._task(OWNER)
        pm_runtime.write_session_fence(OWNER, 'pm-' + OTHER, '2026-11-12T00:00:00Z')
        code, output, run, _ = self._delegate(task_id, claude=OWNER)
        self._assert_delegate_refused(task_id, code, output, run,
                                      'PM_CALLER_SESSION_FENCED')

    def test_delegate_runs_for_owner(self):
        task_id = self._task(OWNER)
        for extra, claude in ((('--pm-session', OWNER), ''), ((), OWNER),
                              (('--pm-session', 'pm-' + OWNER), OWNER)):
            with self.subTest(extra=extra, claude=claude):
                code, output, run, args = self._delegate(task_id, *extra, claude=claude,
                                                         returned=7)
                self.assertEqual(code, 7)
                run.assert_called_once()
                self.assertIs(run.call_args.args[0], args)
                self.assertEqual(len(run.call_args.args), 1)
                self.assertEqual(run.call_args.kwargs, {'store': self.store})
                self.assertEqual(output, '')

    def test_review_repair_internal_delegate_is_not_caller_checked(self):
        from llmtool import review_repair
        store = _MutableStore({'task_id': 'original', 'pm_session_id': 'pm-' + OTHER})
        task = {'task_id': 't', 'pm_assignment': {'acceptance': ['done']}}
        developer = {'provider_profile': 'claude', 'read_paths': ['a.txt'],
                     'write_paths': ['a.txt'], 'egress_basis': 'fixture',
                     'owner_assignment_ref': None, 'data_class': None}
        registration = Mock(root=str(self.home))
        with _environment(), \
                patch.object(review_repair, '_registered_d6',
                             return_value=(registration, None)), \
                patch.object(pm_caller, 'check_caller',
                             wraps=pm_caller.check_caller) as check, \
                patch('llmtool.task_submission_cli.run_task_delegate_checked') as checked, \
                patch('llmtool.task_submission_cli.run_task_delegate',
                      return_value=0) as run, \
                redirect_stdout(io.StringIO()):
            result = review_repair._delegate_repair(
                task=task, developer=developer, packet={'task': 'original'},
                executor_task='executor-1', store=store)
        check.assert_not_called()
        checked.assert_not_called()
        run.assert_called_once()
        self.assertEqual(result, 0)
        self.assertIs(run.call_args.kwargs['store'], store)
        delegated = run.call_args.args[0]
        self.assertEqual(delegated.task_id, 't')
        self.assertEqual(delegated.executor_task, 'executor-1')
        self.assertFalse(hasattr(delegated, 'pm_session'))

    def test_cli_delegate_action_names_checked_adapter(self):
        from llmtool.cli import TASK_ACTIONS
        self.assertEqual(TASK_ACTIONS['delegate'], ('run_task_delegate_checked', True))

    def _claude_bind(self, task_id, *extra, claude='', returned=0):
        argv = ['task', 'claude-bind', task_id, '--executor-task', 'executor-1',
                '--owner-ref', 'https://github.com/example/repo/issues/1#issuecomment-1',
                '--read-path', 'a.txt', '--write-path', 'a.txt', *extra]
        args = parser_for_cli().parse_args(argv)
        output = io.StringIO()
        before = self.store.get(task_id)
        with _environment(claude=claude), \
                patch('llmtool.task_submission_cli.run_task_claude_bind',
                      return_value=returned) as run, \
                patch.object(self.store, 'claude_bind_compare_and_update') as bind, \
                patch.object(task_submission_cli.task_completion,
                             'read_d6_registration') as registration, \
                redirect_stdout(io.StringIO()) as printed:
            code = task_submission_cli.run_task_claude_bind_checked(
                args, store=self.store, stdout=output)
        self.assertEqual(printed.getvalue(), '')
        return code, output.getvalue(), run, args, (before, bind, registration)

    def _assert_claude_bind_refused(self, task_id, code, output, run, side, expected):
        run.assert_not_called()
        before, bind, registration = side
        bind.assert_not_called()
        registration.assert_not_called()
        self.assertEqual(self.store.get(task_id), before)
        self.assertEqual(code, 1)
        lines = output.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0]), {
            'task_id': task_id, 'step': 'REFUSED', 'result': None, 'next': 'REFUSED',
            'codes': [expected], 'acked': []})

    def test_claude_bind_refuses_missing_identity(self):
        task_id = self._task(OWNER)
        code, output, run, _, side = self._claude_bind(task_id)
        self._assert_claude_bind_refused(task_id, code, output, run, side,
                                         'PM_CALLER_SESSION_MISSING')

    def test_claude_bind_refuses_invalid_identity(self):
        task_id = self._task(OWNER)
        for extra, claude in ((('--pm-session', 'not a session'), ''),
                              ((), 'not a session')):
            with self.subTest(extra=extra, claude=claude):
                code, output, run, _, side = self._claude_bind(task_id, *extra,
                                                               claude=claude)
                self._assert_claude_bind_refused(task_id, code, output, run, side,
                                                 'PM_CALLER_SESSION_INVALID')

    def test_claude_bind_refuses_non_owner(self):
        task_id = self._task(OWNER)
        for extra, claude in (((), OTHER), (('--pm-session', OTHER), ''),
                              (('--pm-session', 'pm-' + OTHER), ''))):
            with self.subTest(extra=extra, claude=claude):
                code, output, run, _, side = self._claude_bind(task_id, *extra,
                                                               claude=claude)
                self._assert_claude_bind_refused(task_id, code, output, run, side,
                                                 'PM_CALLER_NOT_TASK_OWNER')

    def test_claude_bind_refuses_conflicting_explicit_session(self):
        task_id = self._task(OWNER)
        code, output, run, _, side = self._claude_bind(task_id, '--pm-session', OWNER,
                                                       claude=OTHER)
        self._assert_claude_bind_refused(task_id, code, output, run, side,
                                         'PM_CALLER_SESSION_CONFLICT')

    def test_claude_bind_refuses_fenced_owner(self):
        task_id = self._task(OWNER)
        pm_runtime.write_session_fence(OWNER, 'pm-' + OTHER, '2026-11-12T00:00:00Z')
        code, output, run, _, side = self._claude_bind(task_id, claude=OWNER)
        self._assert_claude_bind_refused(task_id, code, output, run, side,
                                         'PM_CALLER_SESSION_FENCED')

    def test_claude_bind_runs_for_owner(self):
        task_id = self._task(OWNER)
        for extra, claude in ((('--pm-session', OWNER), ''), ((), OWNER),
                              (('--pm-session', 'pm-' + OWNER), OWNER)):
            with self.subTest(extra=extra, claude=claude):
                code, output, run, args, _ = self._claude_bind(
                    task_id, *extra, claude=claude, returned=7)
                self.assertEqual(code, 7)
                run.assert_called_once()
                self.assertIs(run.call_args.args[0], args)
                self.assertEqual(len(run.call_args.args), 1)
                self.assertEqual(set(run.call_args.kwargs), {'store', 'stdout'})
                self.assertIs(run.call_args.kwargs['store'], self.store)
                self.assertIsNotNone(run.call_args.kwargs['stdout'])
                self.assertEqual(output, '')

    def test_claude_bind_pm_session_is_optional_in_parser(self):
        args = parser_for_cli().parse_args([
            'task', 'claude-bind', 't', '--executor-task', 'executor-1',
            '--owner-ref', 'https://github.com/example/repo/issues/1#issuecomment-1',
            '--read-path', 'a.txt', '--write-path', 'a.txt'])
        self.assertIsNone(args.pm_session)

    def test_direct_claude_bind_is_not_caller_checked(self):
        # The fixture Task is not a Claude Task, so the unchecked function
        # refuses on its own eligibility rule, never on the caller.
        from llmtool.errors import LLMToolError
        task_id = self._task(OWNER)
        args = parser_for_cli().parse_args([
            'task', 'claude-bind', task_id, '--executor-task', 'executor-1',
            '--owner-ref', 'https://github.com/example/repo/issues/1#issuecomment-1',
            '--read-path', 'a.txt', '--write-path', 'a.txt'])
        with _environment(), \
                patch.object(pm_caller, 'check_caller',
                             wraps=pm_caller.check_caller) as check:
            with self.assertRaisesRegex(LLMToolError, 'low-risk active-PM Claude Task'):
                task_submission_cli.run_task_claude_bind(
                    args, store=self.store, stdout=io.StringIO())
        check.assert_not_called()

    def test_cli_claude_bind_action_names_checked_adapter(self):
        from llmtool.cli import TASK_ACTIONS
        self.assertEqual(TASK_ACTIONS['claude-bind'],
                         ('run_task_claude_bind_checked', True))

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
                advance_call=advance, label_mac_ci_call=label,
                quota_read=lambda: None)
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
                advance_call=advance, label_mac_ci_call=label,
                quota_read=lambda: None)
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


class PMCallerFenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state_root = Path(temp.name).resolve()

    def test_fenced_owner_refused_before_reading_store(self):
        pm_runtime.write_session_fence(
            OWNER, 'pm-' + OTHER, '2026-11-12T00:00:00Z', self.state_root)
        store = Mock()
        with _environment(claude=OWNER):
            with self.assertRaises(pm_caller.PMCallerRefused) as caught:
                pm_caller.check_caller(store, 't', state_root=self.state_root)
            self.assertEqual(caught.exception.code, pm_caller.SESSION_FENCED)
        store.get.assert_not_called()

    def test_unfenced_owner_allowed(self):
        store = _MutableStore({'task_id': 't', 'pm_session_id': 'pm-' + OWNER})
        with _environment(claude=OWNER):
            caller_key = pm_caller.check_caller(
                store, 't', state_root=self.state_root)
        self.assertEqual(caller_key, 'pm-' + OWNER)

    def test_garbage_fence_file_refused(self):
        fence_file = self.state_root / ('pm-' + OWNER + '.fence.json')
        fence_file.write_text('{garbage\n', encoding='utf-8')
        store = Mock()
        with _environment(claude=OWNER):
            with self.assertRaises(pm_caller.PMCallerRefused) as caught:
                pm_caller.check_caller(store, 't', state_root=self.state_root)
            self.assertEqual(caught.exception.code, pm_caller.SESSION_FENCED)
        store.get.assert_not_called()

    def test_guarded_refuses_when_fence_is_written_after_check_caller(self):
        store = _MutableStore({'task_id': 't', 'pm_session_id': 'pm-' + OWNER})
        with _environment(claude=OWNER):
            caller_key = pm_caller.check_caller(
                store, 't', state_root=self.state_root)
        self.assertEqual(caller_key, 'pm-' + OWNER)

        call = Mock()
        invoke = pm_caller.guarded(
            call, store, 't', caller_key, state_root=self.state_root)

        pm_runtime.write_session_fence(
            OWNER, 'pm-' + OTHER, '2026-11-12T00:00:00Z', self.state_root)

        with self.assertRaises(pm_caller.PMCallerRefused) as caught:
            invoke()
        self.assertEqual(caught.exception.code, pm_caller.SESSION_FENCED)
        call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
