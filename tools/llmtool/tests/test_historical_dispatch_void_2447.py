"""#2447: an Owner-approved void of a historical DISPATCHING assignment.

llmtool never infers "not started" from missing launch evidence (Owner decision
A on #2413). The void runs only through the explicit command with the canonical
Owner relay URL for that exact repair Task, whose comment explicitly approves
the void, and only when every other check also holds.

Every row runs through a real TaskStore. The D6 registration, Git snapshot and
process probe are injected stand-ins: the void checks are real, only what they
observe is supplied. No test data here is a credential; the identifiers and
URLs are plain labels.
"""
import copy
import hashlib
import inspect
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import direct_reassignment, task_completion, task_submission_cli
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, new_id
from llmtool.task_submission_cli import _direct_result_callback, _record

import review_trust_fixture
import worker_policy_fixture


EXECUTOR = 'executor-void-1'
OTHER_EXECUTOR = 'executor-void-2'
ROOT = '/registered/root'
COMMON = '/repo/.git'
BRANCH = 'task-branch'
HEAD = 'a' * 40
HISTORICAL = '2026-11-20T13:05:00Z'
LATER = '2027-03-01T08:00:00Z'
OWNER_REF = 'https://github.com/davidcatcat/davidcattoolsproject/issues/2413#issuecomment-7058210840'
OTHER_REF = 'https://github.com/davidcatcat/davidcattoolsproject/issues/2413#issuecomment-7058210841'
VOIDED = 'DIRECT_HISTORICAL_DISPATCH_VOIDED'


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def _registration(agent='agy-developer', session_ref='agy-session', native=None,
                  task_id=EXECUTOR):
    return task_completion.D6Registration(
        task_id=task_id, trusted_agent_id=agent, session_ref=session_ref,
        root=ROOT, common_dir=COMMON, branch=BRANCH, source_sha=HEAD, result_sha=HEAD,
        state='ACTIVE', lock_state='HELD', device=1, inode=2, uid=3,
        native_session_id=native, github_login=None, env2_pass_marker=None,
        env2_approved_tooling_sha=HEAD)


def _snapshot(head=HEAD, clean=True):
    return SimpleNamespace(root=ROOT, common_dir=COMMON, branch=BRANCH, head=head,
                           clean=clean)


def _no_process(_root):
    return []


class _Listing:
    """A finished listing child: what Popen returns for lsof or ps."""

    def __init__(self, output, code, pid):
        self.output, self.returncode, self.pid = output, code, pid

    def communicate(self, timeout=None):
        return self.output, b''


def _fake_popen(lsof=b'', ps=b'', lsof_code=0, ps_code=0):
    def popen(argv, **_kwargs):
        if os.path.basename(argv[0]) == 'lsof':
            return _Listing(lsof, lsof_code, 999998)
        return _Listing(ps, ps_code, 999999)
    return popen


def _relay_body(*task_ids, marker=True, approval=True):
    """Synthetic PM relay text: plain labels only, never a credential."""
    actor = 'PM' if marker else 'Owner'
    text = ''
    if approval:
        text = ('<!-- activity: type=owner_decision actor=' + actor +
                ' target=Owner status=APPROVED -->\n')
    text += ('PM relayed the Owner decision: approve the void of the historical '
             'direct assignment.')
    if task_ids:
        text += ' Task ' + ', '.join(task_ids) + '.'
    if marker and not approval:
        text += ' actor=PM'
    return text


def _comment_json(body, *, ref=OWNER_REF, issue=2413):
    """The exact fields llmtool reads from one GitHub issue comment."""
    return json.dumps({
        'body': body, 'html_url': ref,
        'issue_url': ('https://api.github.com/repos/davidcatcat/davidcattoolsproject'
                      f'/issues/{issue}'),
    }).encode('utf-8')


class HistoricalDispatchVoidTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'tasks'
        self.store = TaskStore(self.root)
        self.policy_path = worker_policy_fixture.install(self)
        self.registration = _registration()
        self.snapshot = _snapshot()

    # --- fixtures -------------------------------------------------------

    @staticmethod
    def _task_record():
        request = 'Bounded non-sensitive AGY repair task.'
        task_id, run_id = new_id(), new_id()
        task = _record(
            task_id, run_id, 'pm-' + new_id(), 'pm-profile', request,
            state='PM_RESPONDED', provider_type='openai-compatible', model='pm-model',
            backend='NO_NATIVE_SESSION_ID_EXPOSED',
            request_sha=hashlib.sha256(request.encode()).hexdigest())
        task.update(
            pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                              'base_url': None},
            response='PM returned a bounded task.', error_code=None,
            pm_assignment={'schema_version': 1, 'route': 'agy', 'risk': 'LOW',
                           'goal': 'Repair the recorded failure.',
                           'acceptance': ['the repair is recorded']})
        return task

    @staticmethod
    def _developer(profile, *, created_at=HISTORICAL):
        developer = {
            'assignment_id': new_id(), 'selection_source': 'PM',
            'state': 'DEVELOPER_PENDING', 'provider_profile': profile,
            'provider_type': None, 'model': None,
            'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
            'session_id': 'dev-' + new_id(), 'source_sha': HEAD,
            'source_sha_binding': 'D6_REGISTRY_OBSERVED',
            'read_paths': ['src.py'], 'write_paths': ['src.py'],
            'acceptance': ['the repair is recorded'], 'context_sha256': 'b' * 64,
            'egress_basis': 'Approved non-sensitive test context.', 'input_bytes': 10,
            'max_tokens': None, 'artifact_format': 'unified_diff', 'artifact': None,
            'artifact_sha256': None, 'usage': None, 'error_code': None,
            'execution': None, 'created_at': created_at,
        }
        if profile == 'agy':
            developer['owner_assignment_ref'] = None
        return developer

    @staticmethod
    def _completion(task, registration):
        return {
            'state': 'WAITING_EXECUTOR', 'executor_task_id': registration.task_id,
            'reviewer_task_id': None,
            'evidence': {
                'task_id': task['task_id'], 'run_id': task['run_id'],
                'contract_digest': task['task_binding']['contract_digest'],
                'executor_identity': registration.trusted_agent_id,
                'executor_session_ref': registration.session_ref,
                'executor_native_session_id': registration.native_session_id,
                'executor_native_session': ('VERIFIED' if registration.native_session_id
                                            else 'UNKNOWN'),
                'execution_mode': 'DIRECT_D6', 'root': registration.root,
                'common_dir': registration.common_dir, 'branch': registration.branch,
                'source_sha': HEAD, 'result_head': None},
            'missing_gates': [], 'pm_dispatch': 'NOT_STARTED', 'pm_response': None,
            'pm_recommendation': None, 'pm_usage': None, 'pm_error_code': None,
        }

    def _historical(self, *, created_at=HISTORICAL, boundary=None, registration=None):
        """A direct AGY assignment in DISPATCHING; boundary None is the pre-#2440 shape."""
        registration = registration or self.registration
        task = self._task_record()
        self.store.create(task)
        pending = self._developer('agy', created_at=created_at)
        pending['direct_result_callback'] = _direct_result_callback(
            task, registration, HEAD, test_policy='LOCAL')
        self.store.compare_and_update(
            task['task_id'], expected={'developer': None, 'completion': None},
            developer=pending, completion=self._completion(task, registration))
        dispatching = dict(pending, state='DEVELOPER_DISPATCHING', provider_type='agy-cli',
                           model='gemini-test')
        if boundary is None:
            self.store.update(task['task_id'], developer=dispatching)
        else:
            self.store.prepare_direct_dispatch(
                task['task_id'], expected_developer=pending, developer=dispatching)
            if boundary == 'STARTING':
                current = self.store.get(task['task_id'])['developer']
                self.store.authorize_direct_spawn(
                    task['task_id'], expected_developer=current)
        return task['task_id']

    def _void(self, task_id, *, registration=None, snapshot=None, probe=_no_process,
              task=None, owner_ref=OWNER_REF, owner_task=None, executor_task=EXECUTOR,
              read_comment=None):
        registration = registration or self.registration
        snapshot = snapshot or self.snapshot
        task = self.store.get(task_id) if task is None else task
        owner_task = task_id if owner_task is None else owner_task
        reader = read_comment or (lambda _ref: _relay_body(task_id))
        with patch.object(direct_reassignment, '_read_owner_comment',
                          side_effect=reader), \
                patch.object(task_completion, 'bind_executor_task',
                             return_value=(registration, snapshot)):
            return direct_reassignment.void_historical(
                self.store, task, registration, snapshot, executor_task=executor_task,
                owner_ref=owner_ref, owner_task=owner_task, process_probe=probe)

    def _refused(self, code, task_id, **kwargs):
        before = self.store.get(task_id)
        with self.assertRaises(LLMToolError) as caught:
            self._void(task_id, **kwargs)
        self.assertEqual(str(caught.exception), code)
        self.assertEqual(self.store.get(task_id), before)

    def _delegate(self, task_id, *, read_comment=None, **flags):
        """The real recovery path of `task delegate`, with the D6 and process stubs."""
        args = SimpleNamespace(task_id=task_id, executor_task=EXECUTOR, **flags)
        task = self.store.get(task_id)
        reader = read_comment or (lambda _ref: _relay_body(task_id))
        with patch.object(task_submission_cli, '_delegate_d6_binding',
                          return_value=(self.registration, self.snapshot)), \
                patch.object(task_completion, 'bind_executor_task',
                             return_value=(self.registration, self.snapshot)), \
                patch.object(direct_reassignment, 'd6_processes', return_value=[]), \
                patch.object(direct_reassignment, '_read_owner_comment',
                             side_effect=reader):
            return task_submission_cli._recover_direct_child_dispatch(
                args, task, task['developer'], self.store, expected_agent='agy-developer')

    # --- (a) valid Owner relay and every check -> voided ----------------------

    def test_a_owner_relay_and_all_checks_void_and_the_next_delegate_gets_a_new_assignment(self):
        task_id = self._historical()
        before = self.store.get(task_id)
        old = before['developer']
        self.assertNotIn('dispatch_boundary', old)
        saved = self._void(task_id)
        voided = self.store.get(task_id)['developer']
        self.assertEqual(saved['developer'], voided)
        self.assertEqual(voided['dispatch_boundary'], 'PREPARED')
        self.assertEqual(voided['assignment_id'], old['assignment_id'])
        self.assertEqual(voided['state'], 'DEVELOPER_DISPATCHING')
        self.assertIsNone(voided['execution'])
        self.assertEqual(len(voided['voided_assignments']), 1)
        entry = voided['voided_assignments'][0]
        self.assertEqual(entry['assignment_id'], old['assignment_id'])
        self.assertEqual(entry['provider_profile'], 'agy')
        self.assertEqual(entry['source_sha'], HEAD)
        self.assertEqual(entry['created_at'], HISTORICAL)
        self.assertEqual(entry['task_id'], task_id)
        self.assertEqual(entry['owner_ref'], OWNER_REF)
        self.assertEqual(entry['record_sha256'], before['record_sha256'])
        # The record states the Owner decision and claims no proof of non-launch.
        self.assertEqual(entry['reason'], 'OWNER_APPROVED_VOID')
        self.assertEqual({key: value for key, value in voided.items()
                          if key not in ('dispatch_boundary', 'voided_assignments')}, old)
        self.assertEqual(TaskStore(self.root).get(task_id)['developer'], voided)
        self.assertTrue(direct_reassignment.replacement_candidate(voided))

        # The ordinary #2440 reassignment now creates a new assignment on the
        # same Task and the same D6 claim.
        current = self.store.get(task_id)
        args = SimpleNamespace(
            reassign_from=old['assignment_id'], executor_task=EXECUTOR,
            acceptance=old['acceptance'], egress_basis=old['egress_basis'],
            selection_source='PM', data_class=None)
        plan = direct_reassignment.inspect(
            args, self.store, current, self.registration, self.snapshot, 'opencode',
            old['read_paths'], old['write_paths'])
        self.assertNotEqual(plan.assignment, old['assignment_id'])
        replacement = self._developer('opencode')
        replacement.update(assignment_id=plan.assignment)
        replacement['direct_result_callback'] = _direct_result_callback(
            current, plan.successor, HEAD, test_policy='LOCAL')
        completion = self._completion(current, plan.successor)
        reassigned = self.store.reassign_direct_developer(
            task_id, current=current, developer=replacement, completion=completion,
            verify_binding=lambda: True)
        after = self.store.get(task_id)
        self.assertEqual(after['record_sha256'], reassigned['record_sha256'])
        developer = after['developer']
        self.assertEqual(developer['state'], 'DEVELOPER_PENDING')
        self.assertEqual(developer['provider_profile'], 'opencode')
        self.assertEqual(developer['assignment_id'], plan.assignment)
        handoff = developer['direct_handoffs'][-1]
        self.assertEqual(handoff['old_assignment'], old['assignment_id'])
        self.assertEqual(developer['voided_assignments'], voided['voided_assignments'])
        self.assertEqual(after['completion']['executor_task_id'], EXECUTOR)
        audit = after['dispatch_audit'][-1]
        self.assertEqual((audit['action'], audit['decision']), ('REASSIGN', 'ALLOWED'))

    def test_a_the_explicit_command_voids_and_asks_for_a_new_delegate(self):
        task_id = self._historical()
        with self.assertRaises(LLMToolError) as caught:
            self._delegate(task_id, void_historical_ref=OWNER_REF,
                           void_historical_task=task_id)
        self.assertTrue(str(caught.exception).startswith(VOIDED))
        voided = self.store.get(task_id)['developer']
        self.assertEqual(voided['dispatch_boundary'], 'PREPARED')
        self.assertEqual(voided['voided_assignments'][0]['owner_ref'], OWNER_REF)
        # The same command again finds the PREPARED boundary, not a void candidate.
        with self.assertRaises(LLMToolError) as caught:
            self._delegate(task_id, void_historical_ref=OWNER_REF,
                           void_historical_task=task_id)
        self.assertEqual(str(caught.exception), 'DIRECT_CHILD_SPAWN_NOT_AUTHORIZED')

    # --- (b) no URL -> refused as on main --------------------------------------

    def test_b_no_owner_ref_is_refused_as_on_main(self):
        task_id = self._historical()
        before = self.store.get(task_id)
        with self.assertRaises(LLMToolError) as caught:
            self._delegate(task_id)
        self.assertEqual(str(caught.exception), 'DIRECT_CHILD_LAUNCH_EVIDENCE_MISSING')
        with self.assertRaises(LLMToolError) as caught:
            self._delegate(task_id, void_historical_ref=None, void_historical_task=None)
        self.assertEqual(str(caught.exception), 'DIRECT_CHILD_LAUNCH_EVIDENCE_MISSING')
        self.assertEqual(self.store.get(task_id), before)

    def test_b_the_void_itself_needs_both_an_owner_ref_and_a_task(self):
        task_id = self._historical()
        self._refused('DIRECT_HISTORICAL_VOID_OWNER_REF_INVALID', task_id, owner_ref=None)
        with self.assertRaises(LLMToolError) as caught:
            direct_reassignment.void_historical(
                self.store, self.store.get(task_id), self.registration, self.snapshot,
                executor_task=EXECUTOR, owner_ref=OWNER_REF, owner_task=None,
                process_probe=_no_process)
        self.assertEqual(str(caught.exception), 'DIRECT_HISTORICAL_VOID_OWNER_TASK_MISMATCH')

    def test_b_the_delegate_refuses_one_flag_without_the_other_or_a_wrong_route(self):
        task_id = self._historical()
        before = self.store.get(task_id)
        base = dict(task_id=task_id, selection_source='PM', executor_task=EXECUTOR,
                    background=False, reassign_from=None)
        for name, flags in {
                'ref only': dict(provider='agy', void_historical_ref=OWNER_REF,
                                 void_historical_task=None),
                'task only': dict(provider='agy', void_historical_ref=None,
                                  void_historical_task=task_id),
                'claude route': dict(provider='claude', void_historical_ref=OWNER_REF,
                                     void_historical_task=task_id),
                'with reassign': dict(provider='agy', void_historical_ref=OWNER_REF,
                                      void_historical_task=task_id,
                                      reassign_from='c' * 32)}.items():
            with self.subTest(name=name):
                args = SimpleNamespace(**dict(base, **flags))
                with self.assertRaises(LLMToolError) as caught:
                    task_submission_cli.run_task_delegate(args, store=self.store)
                message = str(caught.exception)
                self.assertTrue(
                    message.startswith('DIRECT_HISTORICAL_VOID_ARGUMENTS_REFUSED') or
                    message == 'DIRECT_REASSIGN_ROUTE_REFUSED', message)
        self.assertEqual(self.store.get(task_id), before)

    # --- (c) URL for another Task or malformed -> refused ----------------------

    def test_c_a_url_naming_another_task_is_refused(self):
        task_id = self._historical()
        self._refused('DIRECT_HISTORICAL_VOID_OWNER_TASK_MISMATCH', task_id,
                      owner_task='d' * 32)
        self._refused('DIRECT_HISTORICAL_VOID_OWNER_TASK_MISMATCH', task_id,
                      owner_task='not-a-task-id')
        with self.assertRaises(LLMToolError) as caught:
            self._delegate(task_id, void_historical_ref=OWNER_REF,
                           void_historical_task='d' * 32)
        self.assertEqual(str(caught.exception), 'DIRECT_HISTORICAL_VOID_OWNER_TASK_MISMATCH')
        self.assertNotIn('voided_assignments', self.store.get(task_id)['developer'])

    def test_c_a_url_already_used_for_another_tasks_void_is_refused(self):
        first = self._historical()
        second = self._historical(registration=_registration(task_id=OTHER_EXECUTOR))
        self._void(first)
        self._refused('DIRECT_HISTORICAL_VOID_OWNER_TASK_MISMATCH', second,
                      registration=_registration(task_id=OTHER_EXECUTOR),
                      executor_task=OTHER_EXECUTOR)
        # A different Owner comment for the second Task is accepted.
        self._void(second, registration=_registration(task_id=OTHER_EXECUTOR),
                   executor_task=OTHER_EXECUTOR, owner_ref=OTHER_REF)
        self.assertEqual(
            self.store.get(second)['developer']['voided_assignments'][0]['owner_ref'],
            OTHER_REF)

    def test_c_a_malformed_url_is_refused(self):
        task_id = self._historical()
        slug = 'davidcatcat/davidcattoolsproject'
        for name, ref in {
                'plain text': 'the owner agreed',
                'empty': '',
                'http': f'http://github.com/{slug}/issues/2413#issuecomment-7058210840',
                'other repository': 'https://github.com/other/repo/issues/2413#issuecomment-1',
                'no comment': f'https://github.com/{slug}/issues/2413',
                'pull request': f'https://github.com/{slug}/pull/2413#issuecomment-1',
                'comment zero': f'https://github.com/{slug}/issues/2413#issuecomment-0',
                'issue zero': f'https://github.com/{slug}/issues/0#issuecomment-1',
                'trailing text': f'https://github.com/{slug}/issues/2413#issuecomment-1 extra',
                'query': f'https://github.com/{slug}/issues/2413?x=1#issuecomment-1',
                'prefix text': f'see https://github.com/{slug}/issues/2413#issuecomment-1',
                'number': 7058210840}.items():
            with self.subTest(name=name):
                self._refused('DIRECT_HISTORICAL_VOID_OWNER_REF_INVALID', task_id,
                              owner_ref=ref)
        with self.assertRaises(LLMToolError) as caught:
            self._delegate(task_id, void_historical_ref='the owner agreed',
                           void_historical_task=task_id)
        self.assertEqual(str(caught.exception), 'DIRECT_HISTORICAL_VOID_OWNER_REF_INVALID')

    # --- (d) execution, artifact, result or session evidence present ----------

    def test_d_execution_present_is_refused(self):
        task_id = self._historical()
        stored = self.store.get(task_id)
        launched = copy.deepcopy(stored)
        launched['developer']['execution'] = {
            'status': 'RUNNING', 'native_session_id': None, 'process_exit_code': None,
            'stdout_sha256': None, 'elapsed_ms': None, 'cleanup_status': 'UNKNOWN',
            'usage_source': 'UNKNOWN', 'source_bytes': None, 'source_file_count': None,
            'source_manifest_sha256': None, 'child_pid': 123,
            'child_start_time': '123456', 'child_pgid': 123}
        self.assertFalse(direct_reassignment.historical_candidate(launched['developer']))
        self._refused('DIRECT_HISTORICAL_VOID_NOT_HISTORICAL', task_id, task=launched)

    def test_d_artifact_or_result_present_is_refused(self):
        task_id = self._historical()
        stored = self.store.get(task_id)

        def with_change(change):
            task = copy.deepcopy(stored)
            change(task)
            return task

        cases = {
            'artifact': lambda t: t['developer'].update(artifact='a diff'),
            'artifact_sha': lambda t: t['developer'].update(artifact_sha256='c' * 64),
            'usage': lambda t: t['developer'].update(usage={'prompt_tokens': 1}),
            'error_code': lambda t: t['developer'].update(error_code='SOME_ERROR'),
            'reported': lambda t: t['developer'].update(reported={'summary': 'done'}),
            'result_head': lambda t: t['completion']['evidence'].update(result_head='d' * 40),
            'completion_state': lambda t: t['completion'].update(state='VERIFYING_EXECUTOR'),
            'completion_events': lambda t: t.update(completion_events=[{'kind': 'result'}]),
            'callback_result': lambda t: t['developer']['direct_result_callback'][
                'evidence'].update(result_head='d' * 40),
            'callback_state': lambda t: t['developer']['direct_result_callback'].update(
                state='WAITING_PM'),
            'callback_verification': lambda t: t['developer']['direct_result_callback'][
                'evidence'].update(verification_state='READY'),
        }
        for name, change in cases.items():
            with self.subTest(name=name):
                before = self.store.get(task_id)
                with self.assertRaises(LLMToolError) as caught:
                    self._void(task_id, task=with_change(change))
                self.assertEqual(str(caught.exception),
                                 'DIRECT_HISTORICAL_VOID_RESULT_PRESENT')
                self.assertEqual(self.store.get(task_id), before)

    def test_d_provider_session_evidence_is_refused(self):
        task_id = self._historical()
        stored = self.store.get(task_id)
        with_session = copy.deepcopy(stored)
        with_session['completion']['evidence'].update(
            executor_native_session_id='provider-session-one',
            executor_native_session='VERIFIED')
        with self.assertRaises(LLMToolError) as caught:
            self._void(task_id, task=with_session)
        self.assertEqual(str(caught.exception), 'DIRECT_HISTORICAL_VOID_SESSION_EVIDENCE')
        with self.assertRaises(LLMToolError) as caught:
            self._void(task_id, registration=_registration(native='provider-session-one'))
        self.assertEqual(str(caught.exception), 'DIRECT_HISTORICAL_VOID_SESSION_EVIDENCE')
        self.assertEqual(self.store.get(task_id), stored)

    def test_d_a_valid_owner_relay_does_not_override_a_failed_check(self):
        task_id = self._historical()
        stored = self.store.get(task_id)
        with_artifact = copy.deepcopy(stored)
        with_artifact['developer'].update(artifact='a diff')
        with self.assertRaises(LLMToolError):
            self._void(task_id, task=with_artifact)
        self.assertEqual(self.store.get(task_id), stored)

    # --- (e) D6 dirty or HEAD moved --------------------------------------------

    def test_e_dirty_or_moved_d6_is_refused(self):
        task_id = self._historical()
        self._refused('DIRECT_HISTORICAL_VOID_D6_UNVERIFIED', task_id,
                      snapshot=_snapshot(clean=False))
        self._refused('DIRECT_HISTORICAL_VOID_D6_UNVERIFIED', task_id,
                      snapshot=_snapshot(head='f' * 40))
        self._refused('DIRECT_HISTORICAL_VOID_D6_UNVERIFIED', task_id,
                      registration=_registration(agent='opencode-developer'))

    def test_e_d6_changed_under_the_lock_is_refused(self):
        task_id = self._historical()
        before = self.store.get(task_id)
        moved = _snapshot(head='f' * 40)
        with patch.object(direct_reassignment, '_read_owner_comment',
                          side_effect=lambda _ref: _relay_body(task_id)), \
                patch.object(task_completion, 'bind_executor_task',
                             return_value=(self.registration, moved)):
            with self.assertRaises(LLMToolError) as caught:
                direct_reassignment.void_historical(
                    self.store, before, self.registration, self.snapshot,
                    executor_task=EXECUTOR, owner_ref=OWNER_REF, owner_task=task_id,
                    process_probe=_no_process)
        self.assertEqual(str(caught.exception), 'DIRECT_HISTORICAL_VOID_D6_UNVERIFIED')
        self.assertEqual(self.store.get(task_id), before)

    # --- (f) a live process bound to the D6 ------------------------------------

    def test_f_live_process_is_refused(self):
        task_id = self._historical()
        self._refused('DIRECT_HISTORICAL_VOID_PROCESS_LIVE', task_id,
                      probe=lambda _root: [4242])

    def test_f_unknown_process_state_is_refused(self):
        task_id = self._historical()

        def broken(_root):
            raise OSError('listing unavailable')

        self._refused('DIRECT_HISTORICAL_VOID_PROCESS_UNVERIFIED', task_id, probe=broken)
        self._refused('DIRECT_HISTORICAL_VOID_PROCESS_UNVERIFIED', task_id,
                      probe=lambda _root: None)

    def test_f_process_started_after_the_first_check_is_refused_under_the_lock(self):
        task_id = self._historical()
        answers = iter(([], [4242]))
        self._refused('DIRECT_HISTORICAL_VOID_PROCESS_LIVE', task_id,
                      probe=lambda _root: next(answers))

    def test_f_the_default_probe_is_the_full_d6_binding_check(self):
        task_id = self._historical()
        before = self.store.get(task_id)
        # A provider with another cwd that holds a D6 file open, listed by lsof.
        listing = b'p31\nfcwd\nn/elsewhere\nf9\nn' + ROOT.encode() + b'/src.py\n'
        with patch.object(direct_reassignment.os, 'access', return_value=True), \
                patch('subprocess.Popen', side_effect=_fake_popen(lsof=listing)):
            with self.assertRaises(LLMToolError) as caught:
                self._void(task_id, probe=None)
        self.assertEqual(str(caught.exception), 'DIRECT_HISTORICAL_VOID_PROCESS_LIVE')
        self.assertEqual(self.store.get(task_id), before)
        # A provider started against the D6 from elsewhere, listed by ps.
        command = b'  32 provider run --dir=' + ROOT.encode() + b'\n'
        with patch.object(direct_reassignment.os, 'access', return_value=True), \
                patch('subprocess.Popen', side_effect=_fake_popen(ps=command)):
            with self.assertRaises(LLMToolError) as caught:
                self._void(task_id, probe=None)
        self.assertEqual(str(caught.exception), 'DIRECT_HISTORICAL_VOID_PROCESS_LIVE')
        self.assertEqual(self.store.get(task_id), before)
        # Nothing bound to the D6: the same real probe allows the void.
        with patch.object(direct_reassignment.os, 'access', return_value=True), \
                patch('subprocess.Popen', side_effect=_fake_popen(
                    lsof=b'p33\nfcwd\nn/elsewhere\n', ps=b'  33 sleep 1\n')):
            self._void(task_id, probe=None)
        self.assertEqual(self.store.get(task_id)['developer']['dispatch_boundary'],
                         'PREPARED')

    def test_f_process_listing_counts_cwd_open_files_and_command_lines(self):
        lsof = (b'p10\nfcwd\nn/work/d6\n'
                b'p11\nftxt\nn/work/d6/bin/tool\n'
                b'p12\nfcwd\nn/elsewhere\nf7\nn/work/d6/src/file.py\n'
                b'p13\nfcwd\nn/work/d6-other\n'
                b'p14\nfcwd\nn/elsewhere\nf3\nn*:8080\n'
                + b'p' + str(os.getpid()).encode() + b'\nfcwd\nn/work/d6\n')
        ps = (b'   20 provider --dir /work/d6 run\n'
              b'   21 provider --dir=/work/d6/sub\n'
              b'   22 tool /work/d6-other/x\n'
              b'   23 tool /other/work/d6\n'
              b'   24 tool /work/d6.bak\n'
              b'   25 tool "/work/d6"\n'
              b'   26 sleep 1\n'
              b'   ' + str(os.getpid()).encode() + b' runner --dir /work/d6\n'
              b'   999999 ps-child /work/d6\n'
              b'garbled line without a pid\n')
        with patch.object(direct_reassignment.os, 'access', return_value=True), \
                patch('subprocess.Popen', side_effect=_fake_popen(lsof=lsof, ps=ps)):
            self.assertEqual(direct_reassignment.d6_processes('/work/d6'),
                             [10, 11, 12, 20, 21, 25])

    def test_f_failed_or_missing_listing_refuses(self):
        for name, popen, access in (
                ('lsof failed', _fake_popen(lsof_code=1), True),
                ('ps failed', _fake_popen(ps_code=1), True),
                ('no tool', _fake_popen(), False)):
            with self.subTest(name=name):
                with patch.object(direct_reassignment.os, 'access', return_value=access), \
                        patch('subprocess.Popen', side_effect=popen):
                    with self.assertRaises(LLMToolError) as caught:
                        direct_reassignment.d6_processes('/work/d6')
                self.assertEqual(str(caught.exception),
                                 'DIRECT_HISTORICAL_VOID_PROCESS_UNVERIFIED')
        with patch.object(direct_reassignment.os, 'access', return_value=True):
            with self.assertRaises(LLMToolError) as caught:
                direct_reassignment.d6_processes('relative/path')
        self.assertEqual(str(caught.exception), 'DIRECT_HISTORICAL_VOID_PROCESS_UNVERIFIED')

    # --- (g) second or concurrent void -----------------------------------------

    def test_g_a_second_void_is_refused(self):
        task_id = self._historical()
        stale = self.store.get(task_id)
        self._void(task_id, task=stale)
        voided = self.store.get(task_id)
        # A request made from the record as it was before the first void.
        with self.assertRaises(LLMToolError) as caught:
            self._void(task_id, task=stale)
        self.assertEqual(str(caught.exception), 'DIRECT_HISTORICAL_VOID_CONFLICT')
        # A request made from the record as it is now.
        with self.assertRaises(LLMToolError) as caught:
            self._void(task_id, task=voided)
        self.assertEqual(str(caught.exception), 'DIRECT_HISTORICAL_VOID_NOT_HISTORICAL')
        self.assertEqual(self.store.get(task_id), voided)
        self.assertEqual(len(voided['developer']['voided_assignments']), 1)

    def test_g_concurrent_voids_exactly_one_succeeds(self):
        task_id = self._historical()
        stale = self.store.get(task_id)
        start = threading.Barrier(2)
        outcomes = []

        def request():
            start.wait(timeout=10)
            try:
                direct_reassignment.void_historical(
                    TaskStore(self.root), copy.deepcopy(stale), self.registration,
                    self.snapshot, executor_task=EXECUTOR, owner_ref=OWNER_REF,
                    owner_task=task_id, process_probe=_no_process)
                outcomes.append('voided')
            except LLMToolError as exc:
                outcomes.append(str(exc))

        with patch.object(direct_reassignment, '_read_owner_comment',
                          side_effect=lambda _ref: _relay_body(task_id)), \
                patch.object(task_completion, 'bind_executor_task',
                             return_value=(self.registration, self.snapshot)):
            runners = [threading.Thread(target=request) for _ in range(2)]
            for runner in runners:
                runner.start()
            for runner in runners:
                runner.join(timeout=30)
        self.assertCountEqual(outcomes, ['voided', 'DIRECT_HISTORICAL_VOID_CONFLICT'])
        self.assertEqual(
            len(self.store.get(task_id)['developer']['voided_assignments']), 1)

    # --- (h) counts, claim and budgets unchanged -------------------------------

    def test_h_void_changes_no_count_claim_or_budget(self):
        task_id = self._historical()
        before = self.store.get(task_id)
        self._void(task_id)
        after = self.store.get(task_id)
        untouched = lambda record: {  # noqa: E731
            key: value for key, value in record.items()
            if key not in ('developer', 'updated_at', 'record_sha256')}
        self.assertEqual(untouched(after), untouched(before))
        for key in ('review_repair_claims', 'high_debug', 'dispatch_audit',
                    'start_failures', 'repair_packet', 'completion'):
            self.assertEqual(after.get(key), before.get(key), key)
        self.assertEqual(
            {key: value for key, value in after['developer'].items()
             if key not in ('dispatch_boundary', 'voided_assignments')},
            before['developer'])

    def test_h_a_repair_claim_that_no_longer_matches_blocks_the_void(self):
        task_id = self._historical()
        stored = self.store.get(task_id)
        claimed = copy.deepcopy(stored)
        claimed['request'] = 'REPAIR the recorded failure'
        # A repair Task whose counted claim cannot be found is never voided.
        self._refused('DIRECT_REASSIGN_REPAIR_CLAIM_UNVERIFIED', task_id, task=claimed)

    # --- (i) PREPARED, STARTING and STOPPED keep the #2440/#2450 rules ---------

    def test_i_boundary_records_are_never_voided(self):
        for boundary in ('PREPARED', 'STARTING'):
            with self.subTest(boundary=boundary):
                # One registered D6 has one live holder, so each case gets its
                # own store rather than a second Task on the same D6.
                scratch = tempfile.TemporaryDirectory()
                self.addCleanup(scratch.cleanup)
                self.store = TaskStore(Path(scratch.name).resolve() / 'tasks')
                task_id = self._historical(boundary=boundary)
                developer = self.store.get(task_id)['developer']
                self.assertEqual(developer['dispatch_boundary'], boundary)
                self.assertFalse(direct_reassignment.historical_candidate(developer))
                self.assertEqual(direct_reassignment.replacement_candidate(developer),
                                 boundary == 'PREPARED')
                self._refused('DIRECT_HISTORICAL_VOID_NOT_HISTORICAL', task_id)
                expected = ('DIRECT_CHILD_SPAWN_NOT_AUTHORIZED' if boundary == 'PREPARED'
                            else 'DIRECT_CHILD_SPAWN_OUTCOME_UNVERIFIED')
                with self.assertRaises(LLMToolError) as caught:
                    self._delegate(task_id, void_historical_ref=OWNER_REF,
                                   void_historical_task=task_id)
                self.assertEqual(str(caught.exception), expected)

    def test_i_stopped_record_is_not_a_void_candidate(self):
        stopped = {'provider_profile': 'opencode', 'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                   'dispatch_boundary': 'STOPPED', 'execution': {'status': 'UNKNOWN'}}
        self.assertFalse(direct_reassignment.historical_candidate(stopped))
        self.assertTrue(direct_reassignment.replacement_candidate(stopped))

    def test_i_the_store_accepts_no_void_outside_the_void_path(self):
        task_id = self._historical()
        before = self.store.get(task_id)
        developer = before['developer']
        forged = dict(developer, dispatch_boundary='PREPARED', voided_assignments=[{
            'assignment_id': developer['assignment_id'], 'provider_profile': 'agy',
            'source_sha': HEAD, 'created_at': HISTORICAL, 'task_id': task_id,
            'owner_ref': OWNER_REF, 'record_sha256': before['record_sha256'],
            'reason': 'OWNER_APPROVED_VOID', 'recorded_at': HISTORICAL}])
        with self.assertRaises(LLMToolError) as caught:
            self.store.update(task_id, developer=forged)
        self.assertEqual(caught.exception.task_store_error_code,
                         'DIRECT_HISTORICAL_VOID_HISTORY_FORBIDDEN')
        with self.assertRaises(LLMToolError) as caught:
            self.store.update(task_id, developer=dict(developer,
                                                     dispatch_boundary='PREPARED'))
        self.assertEqual(caught.exception.task_store_error_code,
                         'DIRECT_DISPATCH_BOUNDARY_FORBIDDEN')
        self.assertEqual(self.store.get(task_id), before)

    def test_i_a_void_the_checks_did_not_confirm_is_refused_by_the_store(self):
        task_id = self._historical()
        before = self.store.get(task_id)
        with self.assertRaises(LLMToolError) as caught:
            self.store.void_historical_direct_dispatch(
                task_id, current=before, owner_ref=OWNER_REF, verify=lambda: False)
        self.assertEqual(caught.exception.task_store_error_code,
                         'DIRECT_DISPATCH_BOUNDARY_FORBIDDEN')
        self.assertEqual(self.store.get(task_id), before)

    def test_i_the_store_refuses_a_void_without_a_valid_owner_ref(self):
        task_id = self._historical()
        before = self.store.get(task_id)
        for ref in (None, 'the owner agreed', OWNER_REF + ' extra'):
            with self.subTest(ref=ref):
                with self.assertRaises(LLMToolError):
                    self.store.void_historical_direct_dispatch(
                        task_id, current=before, owner_ref=ref, verify=lambda: True)
                self.assertEqual(self.store.get(task_id), before)

    # --- (k)-(n) the linked Owner relay is read and authenticated --------------

    def test_k_a_missing_comment_is_refused(self):
        task_id = self._historical()

        def missing(_ref):
            raise LLMToolError('DIRECT_HISTORICAL_VOID_OWNER_COMMENT_MISSING')

        self._refused('DIRECT_HISTORICAL_VOID_OWNER_COMMENT_MISSING', task_id,
                      read_comment=missing)
        with self.assertRaises(LLMToolError) as caught:
            self._delegate(task_id, void_historical_ref=OWNER_REF,
                           void_historical_task=task_id, read_comment=missing)
        self.assertEqual(str(caught.exception),
                         'DIRECT_HISTORICAL_VOID_OWNER_COMMENT_MISSING')

    def test_l_a_comment_that_does_not_name_this_task_is_refused(self):
        task_id = self._historical()
        self._refused('DIRECT_HISTORICAL_VOID_OWNER_TASK_MISMATCH', task_id,
                      read_comment=lambda _ref: _relay_body('d' * 32))
        # A longer hex string containing the Task id is not this exact Task id.
        self._refused('DIRECT_HISTORICAL_VOID_OWNER_TASK_MISMATCH', task_id,
                      read_comment=lambda _ref: _relay_body('e' + task_id))

    def test_m_a_comment_without_the_owner_relay_marker_is_refused(self):
        task_id = self._historical()
        self._refused('DIRECT_HISTORICAL_VOID_OWNER_MARKER_MISSING', task_id,
                      read_comment=lambda _ref: _relay_body(task_id, marker=False))
        # The marker is a standalone PM relay token, not a prefix.
        self._refused('DIRECT_HISTORICAL_VOID_OWNER_MARKER_MISSING', task_id,
                      read_comment=lambda _ref: _relay_body(task_id).replace(
                          'actor=PM', 'actor=PM-not'))

    def test_n_a_failed_github_read_is_refused(self):
        task_id = self._historical()

        def failed(_ref):
            raise LLMToolError('DIRECT_HISTORICAL_VOID_OWNER_READ_FAILED')

        self._refused('DIRECT_HISTORICAL_VOID_OWNER_READ_FAILED', task_id,
                      read_comment=failed)

    def test_n_the_real_reader_refuses_a_failed_or_missing_comment(self):
        for name, completed, code in (
                ('read failed', SimpleNamespace(
                    returncode=1, stdout=b'', stderr=b'HTTP 500'),
                 'DIRECT_HISTORICAL_VOID_OWNER_READ_FAILED'),
                ('comment missing', SimpleNamespace(
                    returncode=1, stdout=b'', stderr=b'Not Found (HTTP 404)'),
                 'DIRECT_HISTORICAL_VOID_OWNER_COMMENT_MISSING')):
            with self.subTest(name=name):
                with patch('subprocess.run', return_value=completed):
                    with self.assertRaises(LLMToolError) as caught:
                        direct_reassignment._read_owner_comment(OWNER_REF)
                self.assertEqual(str(caught.exception), code)
        succeeded = SimpleNamespace(
            returncode=0, stdout=_comment_json('actor=PM body'), stderr=b'')
        with patch('subprocess.run', return_value=succeeded):
            self.assertEqual(direct_reassignment._read_owner_comment(OWNER_REF),
                             'actor=PM body')

    # --- (q)-(s) only an explicit Owner void approval authorises the void ------

    def test_q_a_relay_without_an_explicit_void_approval_is_refused(self):
        task_id = self._historical()
        bodies = {
            'pm progress': (
                '<!-- activity: type=pm_progress actor=PM target=Owner status=NOTICE -->\n'
                f'PM progress: Task {task_id}; void is not approved.'),
            'owner notice': (
                '<!-- activity: type=owner_decision actor=PM target=Owner status=NOTICE -->\n'
                f'Owner decision for Task {task_id}; void approval is pending.'),
            'owner request': (
                '<!-- activity: type=owner_request actor=PM target=Owner status=QUESTION -->\n'
                f'Owner asks whether to void Task {task_id}.'),
            'no void directive': (
                '<!-- activity: type=owner_decision actor=PM target=Owner status=APPROVED -->\n'
                f'Owner decision for Task {task_id}.'),
        }
        for name, body in bodies.items():
            with self.subTest(name=name):
                self._refused('DIRECT_HISTORICAL_VOID_OWNER_APPROVAL_MISSING', task_id,
                              read_comment=lambda _ref, body=body: body)
        # The explicit reason token is itself an approval and admits the void.
        self._void(task_id, read_comment=lambda _ref: (
            f'Owner approved OWNER_APPROVED_VOID for Task {task_id}. actor=PM'))
        self.assertEqual(
            self.store.get(task_id)['developer']['dispatch_boundary'], 'PREPARED')

    def test_r_an_owner_approval_activity_marker_admits_the_void(self):
        task_id = self._historical()
        body = (
            '<!-- activity: type=owner_approval actor=PM target=Owner status=ANSWERED -->\n'
            f'Owner approved the void of Task {task_id}.')
        self._void(task_id, read_comment=lambda _ref: body)
        self.assertEqual(
            self.store.get(task_id)['developer']['voided_assignments'][0]['owner_ref'],
            OWNER_REF)

    # --- (s) the comment URL must be its canonical comment identity ------------

    def test_s_an_aliased_comment_url_is_refused_by_the_real_reader(self):
        task_id = self._historical()
        canonical = SimpleNamespace(
            returncode=0, stdout=_comment_json(_relay_body(task_id)), stderr=b'')
        alias = ('https://github.com/davidcatcat/davidcattoolsproject/issues/9999'
                 '#issuecomment-7058210840')
        # The alias names a different issue but the same comment id, so gh
        # returns the same body; the returned canonical URL must reject it.
        with patch('subprocess.run', return_value=canonical):
            with self.assertRaises(LLMToolError) as caught:
                direct_reassignment._read_owner_comment(alias)
        self.assertEqual(str(caught.exception),
                         'DIRECT_HISTORICAL_VOID_OWNER_REF_INVALID')
        # The canonical URL reads the same comment and accepts its body.
        with patch('subprocess.run', return_value=canonical):
            self.assertEqual(direct_reassignment._read_owner_comment(OWNER_REF),
                             _relay_body(task_id))
        # The API issue URL must also name the same issue as the comment URL.
        wrong_issue = SimpleNamespace(
            returncode=0, stdout=_comment_json(_relay_body(task_id), issue=9999),
            stderr=b'')
        with patch('subprocess.run', return_value=wrong_issue):
            with self.assertRaises(LLMToolError) as caught:
                direct_reassignment._read_owner_comment(OWNER_REF)
        self.assertEqual(str(caught.exception),
                         'DIRECT_HISTORICAL_VOID_OWNER_REF_INVALID')
        # A malformed API answer fails closed, never authorising a void.
        malformed = SimpleNamespace(returncode=0, stdout=b'not-json', stderr=b'')
        with patch('subprocess.run', return_value=malformed):
            with self.assertRaises(LLMToolError) as caught:
                direct_reassignment._read_owner_comment(OWNER_REF)
        self.assertEqual(str(caught.exception),
                         'DIRECT_HISTORICAL_VOID_OWNER_READ_FAILED')

    # --- (o) one relay URL reserves exactly one Task ---------------------------

    def test_o_two_concurrent_voids_for_different_tasks_void_exactly_one(self):
        first = self._historical()
        second = self._historical(registration=_registration(task_id=OTHER_EXECUTOR))
        first_stale = self.store.get(first)
        second_stale = self.store.get(second)
        # One relay body satisfies the per-Task name check for both requests;
        # only the store-wide reservation may decide the winner.
        body = _relay_body(first, second)
        start = threading.Barrier(2)
        outcomes = []

        def request(task_id, record, registration, executor):
            start.wait(timeout=10)
            try:
                direct_reassignment.void_historical(
                    TaskStore(self.root), copy.deepcopy(record), registration,
                    self.snapshot, executor_task=executor, owner_ref=OWNER_REF,
                    owner_task=task_id, process_probe=_no_process)
                outcomes.append((task_id, 'voided'))
            except LLMToolError as exc:
                outcomes.append((task_id, str(exc)))

        def bind(task_id, **_kwargs):
            return _registration(task_id=task_id), self.snapshot

        with patch.object(direct_reassignment, '_read_owner_comment',
                          side_effect=lambda _ref: body), \
                patch.object(task_completion, 'bind_executor_task', side_effect=bind):
            runners = [
                threading.Thread(target=request, args=(
                    first, first_stale, self.registration, EXECUTOR)),
                threading.Thread(target=request, args=(
                    second, second_stale,
                    _registration(task_id=OTHER_EXECUTOR), OTHER_EXECUTOR)),
            ]
            for runner in runners:
                runner.start()
            for runner in runners:
                runner.join(timeout=30)
        self.assertEqual(sum(1 for _task, outcome in outcomes if outcome == 'voided'), 1)
        reserved = [task_id for task_id in (first, second)
                    if self.store.get(task_id)['developer'].get('voided_assignments')]
        self.assertEqual(reserved, [next(task_id for task_id, outcome in outcomes
                                         if outcome == 'voided')])

    # --- (j) no date cutoff ----------------------------------------------------

    def test_j_no_date_cutoff_is_used(self):
        self.assertFalse(hasattr(direct_reassignment, 'HISTORICAL_BOUNDARY_CUTOFF'))
        self.assertNotIn('cutoff', inspect.signature(
            direct_reassignment.void_historical).parameters)
        # Neither an old nor a far-later creation time changes the outcome:
        # only the Owner relay and the checks decide.
        for created_at in (HISTORICAL, LATER):
            with self.subTest(created_at=created_at):
                scratch = tempfile.TemporaryDirectory()
                self.addCleanup(scratch.cleanup)
                self.store = TaskStore(Path(scratch.name).resolve() / 'tasks')
                task_id = self._historical(created_at=created_at)
                self._refused('DIRECT_HISTORICAL_VOID_OWNER_REF_INVALID', task_id,
                              owner_ref=None)
                self._void(task_id)
                self.assertEqual(
                    self.store.get(task_id)['developer']['dispatch_boundary'], 'PREPARED')


if __name__ == '__main__':
    unittest.main()
