"""#2447: void a historical DISPATCHING assignment that provably never launched.

Every row runs through a real TaskStore. The D6 registration, Git snapshot and
process probe are injected stand-ins: the void checks are real, only what they
observe is supplied. No test data here is a credential; the identifiers are
plain labels.
"""
import copy
import hashlib
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import direct_reassignment, task_completion
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, new_id
from llmtool.task_submission_cli import _direct_result_callback, _record

import review_trust_fixture
import worker_policy_fixture


EXECUTOR = 'executor-void-1'
ROOT = '/registered/root'
COMMON = '/repo/.git'
BRANCH = 'task-branch'
HEAD = 'a' * 40
HISTORICAL = '2026-11-20T13:05:00Z'
AFTER_BOUNDARY = '2026-11-21T08:00:00Z'


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def _registration(agent='agy-developer', session_ref='agy-session', native=None):
    return task_completion.D6Registration(
        task_id=EXECUTOR, trusted_agent_id=agent, session_ref=session_ref,
        root=ROOT, common_dir=COMMON, branch=BRANCH, source_sha=HEAD, result_sha=HEAD,
        state='ACTIVE', lock_state='HELD', device=1, inode=2, uid=3,
        native_session_id=native, github_login=None, env2_pass_marker=None,
        env2_approved_tooling_sha=HEAD)


def _snapshot(head=HEAD, clean=True):
    return SimpleNamespace(root=ROOT, common_dir=COMMON, branch=BRANCH, head=head,
                           clean=clean)


def _no_process(_root):
    return []


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

    def _historical(self, *, created_at=HISTORICAL, boundary=None):
        """A direct AGY assignment in DISPATCHING; boundary None is the pre-#2440 shape."""
        task = self._task_record()
        self.store.create(task)
        pending = self._developer('agy', created_at=created_at)
        pending['direct_result_callback'] = _direct_result_callback(
            task, self.registration, HEAD, test_policy='LOCAL')
        self.store.compare_and_update(
            task['task_id'], expected={'developer': None, 'completion': None},
            developer=pending, completion=self._completion(task, self.registration))
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
              task=None, **options):
        registration = registration or self.registration
        snapshot = snapshot or self.snapshot
        task = self.store.get(task_id) if task is None else task
        with patch.object(task_completion, 'bind_executor_task',
                          return_value=(registration, snapshot)):
            return direct_reassignment.void_historical(
                self.store, task, registration, snapshot, executor_task=EXECUTOR,
                process_probe=probe, **options)

    def _refused(self, code, task_id, **kwargs):
        before = self.store.get(task_id)
        with self.assertRaises(LLMToolError) as caught:
            self._void(task_id, **kwargs)
        self.assertEqual(str(caught.exception), code)
        self.assertEqual(self.store.get(task_id), before)

    # --- (a) every condition holds -------------------------------------

    def test_a_all_conditions_hold_voids_and_the_next_delegate_gets_a_new_assignment(self):
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
        self.assertEqual(entry['record_sha256'], before['record_sha256'])
        self.assertEqual(entry['reason'], 'HISTORICAL_NO_LAUNCH_EVIDENCE')
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
        self.assertEqual(handoff['reason'], 'NOT_STARTED_CONFIRMED')
        self.assertEqual(developer['voided_assignments'], voided['voided_assignments'])
        self.assertEqual(after['completion']['executor_task_id'], EXECUTOR)
        audit = after['dispatch_audit'][-1]
        self.assertEqual((audit['action'], audit['decision']), ('REASSIGN', 'ALLOWED'))

    # --- (b) execution present ------------------------------------------

    def test_b_execution_present_is_refused(self):
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

    # --- (c) artifact or completion result present ----------------------

    def test_c_artifact_or_result_present_is_refused(self):
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

    def test_c_provider_session_evidence_is_refused(self):
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

    # --- (d) D6 dirty or HEAD moved --------------------------------------

    def test_d_dirty_or_moved_d6_is_refused(self):
        task_id = self._historical()
        self._refused('DIRECT_HISTORICAL_VOID_D6_UNVERIFIED', task_id,
                      snapshot=_snapshot(clean=False))
        self._refused('DIRECT_HISTORICAL_VOID_D6_UNVERIFIED', task_id,
                      snapshot=_snapshot(head='f' * 40))
        self._refused('DIRECT_HISTORICAL_VOID_D6_UNVERIFIED', task_id,
                      registration=_registration(agent='opencode-developer'))

    def test_d_d6_changed_under_the_lock_is_refused(self):
        task_id = self._historical()
        before = self.store.get(task_id)
        moved = _snapshot(head='f' * 40)
        with patch.object(task_completion, 'bind_executor_task',
                          return_value=(self.registration, moved)):
            with self.assertRaises(LLMToolError) as caught:
                direct_reassignment.void_historical(
                    self.store, before, self.registration, self.snapshot,
                    executor_task=EXECUTOR, process_probe=_no_process)
        self.assertEqual(str(caught.exception), 'DIRECT_HISTORICAL_VOID_D6_UNVERIFIED')
        self.assertEqual(self.store.get(task_id), before)

    # --- (e) live process bound to the D6 --------------------------------

    def test_e_live_process_is_refused(self):
        task_id = self._historical()
        self._refused('DIRECT_HISTORICAL_VOID_PROCESS_LIVE', task_id,
                      probe=lambda _root: [4242])

    def test_e_unknown_process_state_is_refused(self):
        task_id = self._historical()

        def broken(_root):
            raise OSError('lsof unavailable')

        self._refused('DIRECT_HISTORICAL_VOID_PROCESS_UNVERIFIED', task_id, probe=broken)
        self._refused('DIRECT_HISTORICAL_VOID_PROCESS_UNVERIFIED', task_id,
                      probe=lambda _root: None)

    def test_e_process_started_after_the_first_check_is_refused_under_the_lock(self):
        task_id = self._historical()
        answers = iter(([], [4242]))
        self._refused('DIRECT_HISTORICAL_VOID_PROCESS_LIVE', task_id,
                      probe=lambda _root: next(answers))

    def test_e_process_listing_counts_only_working_directories_inside_the_d6(self):
        class Listing:
            pid = 999999
            returncode = 0

            def communicate(self, timeout=None):
                return (b'p10\nfcwd\nn/work/d6\np11\nfcwd\nn/work/d6/sub\n'
                        b'p12\nfcwd\nn/work/d6-other\np13\nfcwd\nn/elsewhere\n'), b''

        with patch.object(direct_reassignment.os, 'access', return_value=True), \
                patch('subprocess.Popen', return_value=Listing()):
            self.assertEqual(direct_reassignment.d6_processes('/work/d6'), [10, 11])

    def test_e_failed_process_listing_refuses(self):
        class Failed:
            pid = 999999
            returncode = 1

            def communicate(self, timeout=None):
                return b'', b''

        with patch.object(direct_reassignment.os, 'access', return_value=True), \
                patch('subprocess.Popen', return_value=Failed()):
            with self.assertRaises(LLMToolError) as caught:
                direct_reassignment.d6_processes('/work/d6')
        self.assertEqual(str(caught.exception), 'DIRECT_HISTORICAL_VOID_PROCESS_UNVERIFIED')
        with patch.object(direct_reassignment.os, 'access', return_value=False):
            with self.assertRaises(LLMToolError) as caught:
                direct_reassignment.d6_processes('/work/d6')
        self.assertEqual(str(caught.exception), 'DIRECT_HISTORICAL_VOID_PROCESS_UNVERIFIED')

    # --- (f) recorded after the boundary deployment ----------------------

    def test_f_record_after_the_boundary_deployment_is_refused(self):
        task_id = self._historical(created_at=AFTER_BOUNDARY)
        self._refused('DIRECT_HISTORICAL_VOID_NOT_HISTORICAL', task_id)

    def test_f_cutoff_is_the_only_time_bound(self):
        task_id = self._historical()
        self._refused('DIRECT_HISTORICAL_VOID_NOT_HISTORICAL', task_id,
                      cutoff='2026-11-20T13:05:00Z')
        self._void(task_id, cutoff='2026-11-20T13:05:01Z')
        self.assertEqual(self.store.get(task_id)['developer']['dispatch_boundary'],
                         'PREPARED')

    # --- (g) boundary records keep the #2440 rules -----------------------

    def test_g_boundary_records_are_never_voided(self):
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

    def test_g_stopped_record_is_not_a_void_candidate(self):
        stopped = {'provider_profile': 'opencode', 'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                   'dispatch_boundary': 'STOPPED', 'execution': {'status': 'UNKNOWN'}}
        self.assertFalse(direct_reassignment.historical_candidate(stopped))
        self.assertTrue(direct_reassignment.replacement_candidate(stopped))

    def test_g_the_store_accepts_no_void_outside_the_void_path(self):
        task_id = self._historical()
        before = self.store.get(task_id)
        developer = before['developer']
        forged = dict(developer, dispatch_boundary='PREPARED', voided_assignments=[{
            'assignment_id': developer['assignment_id'], 'provider_profile': 'agy',
            'source_sha': HEAD, 'created_at': HISTORICAL,
            'record_sha256': before['record_sha256'],
            'reason': 'HISTORICAL_NO_LAUNCH_EVIDENCE', 'recorded_at': HISTORICAL}])
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

    def test_g_a_void_the_checks_did_not_confirm_is_refused_by_the_store(self):
        task_id = self._historical()
        before = self.store.get(task_id)
        with self.assertRaises(LLMToolError) as caught:
            self.store.void_historical_direct_dispatch(
                task_id, current=before, verify=lambda: False)
        self.assertEqual(caught.exception.task_store_error_code,
                         'DIRECT_DISPATCH_BOUNDARY_FORBIDDEN')
        self.assertEqual(self.store.get(task_id), before)

    # --- (h) second or concurrent void -----------------------------------

    def test_h_a_second_void_is_refused(self):
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

    def test_h_concurrent_voids_exactly_one_succeeds(self):
        task_id = self._historical()
        stale = self.store.get(task_id)
        start = threading.Barrier(2)
        outcomes = []

        def request():
            start.wait(timeout=10)
            try:
                direct_reassignment.void_historical(
                    TaskStore(self.root), copy.deepcopy(stale), self.registration,
                    self.snapshot, executor_task=EXECUTOR, process_probe=_no_process)
                outcomes.append('voided')
            except LLMToolError as exc:
                outcomes.append(str(exc))

        with patch.object(task_completion, 'bind_executor_task',
                          return_value=(self.registration, self.snapshot)):
            runners = [threading.Thread(target=request) for _ in range(2)]
            for runner in runners:
                runner.start()
            for runner in runners:
                runner.join(timeout=30)
        self.assertCountEqual(outcomes, ['voided', 'DIRECT_HISTORICAL_VOID_CONFLICT'])
        self.assertEqual(
            len(self.store.get(task_id)['developer']['voided_assignments']), 1)

    # --- (i) counts and budgets unchanged --------------------------------

    def test_i_void_changes_no_count_claim_or_budget(self):
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

    def test_i_a_repair_claim_that_no_longer_matches_blocks_the_void(self):
        task_id = self._historical()
        stored = self.store.get(task_id)
        claimed = copy.deepcopy(stored)
        claimed['request'] = 'REPAIR the recorded failure'
        # A repair Task whose counted claim cannot be found is never voided.
        self._refused('DIRECT_REASSIGN_REPAIR_CLAIM_UNVERIFIED', task_id, task=claimed)


if __name__ == '__main__':
    unittest.main()
