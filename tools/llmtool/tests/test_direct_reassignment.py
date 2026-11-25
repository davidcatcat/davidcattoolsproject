"""Direct tests for llmtool.direct_reassignment helpers (issue #2492)."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llmtool.direct_reassignment as dr
from llmtool import worker_dispatch
from llmtool.direct_reassignment import (authoring_route,
                                         effective_writer_admission,
                                         replacement_candidate,
                                         verified_repair_result)
from llmtool.errors import LLMToolError

SHA = 'c' * 40
ASSIGNMENT = 'a' * 32
OLD_ASSIGNMENT = 'b' * 32
TASK_ID = 'd6task-2492'
SESSION = 'sess-2492'
AGENT = 'opencode-developer'
CODE = 'DIRECT_REASSIGN_RESULT_UNVERIFIED'
ROOT_INVALID = 'DIRECT_REASSIGN_ROOT_INVALID'
EVIDENCE_INVALID = 'DIRECT_REASSIGN_RESULT_EVIDENCE_INVALID'
EVIDENCE_KEYS = ('executor_identity', 'executor_session_ref',
                 'executor_native_session_id', 'executor_native_session')


class FakeStore:
    """Minimal in-test stand-in for the Task store: plain dicts, no disk."""

    def __init__(self, tasks):
        self.tasks = tasks

    def get(self, task_id):
        if task_id not in self.tasks:
            raise KeyError(task_id)
        return self.tasks[task_id]


class FailingStore(FakeStore):
    """Store whose get raises a fixed LLMToolError for one task id."""

    def __init__(self, tasks, failed_task_id):
        super().__init__(tasks)
        self.failed_task_id = failed_task_id

    def get(self, task_id):
        if task_id == self.failed_task_id:
            raise LLMToolError('STORE_UNAVAILABLE')
        return super().get(task_id)


def _error_codes(exc):
    values = {str(exc), str(getattr(exc, 'code', ''))}
    values.update(map(str, getattr(exc, 'args', ())))
    return values


class DirectReassignmentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.common = Path(tmp.name) / 'common'
        self.common.mkdir()
        self.registration = SimpleNamespace(
            task_id=TASK_ID, root=str(Path(tmp.name) / 'root'),
            common_dir=str(self.common), branch='feature-2492',
            trusted_agent_id=AGENT, session_ref='handoff-' + ASSIGNMENT,
            native_session_id=SESSION)
        self.evidence = {
            'root': self.registration.root,
            'common_dir': self.registration.common_dir,
            'branch': self.registration.branch,
            'executor_identity': AGENT,
            'executor_session_ref': self.registration.session_ref,
            'executor_native_session_id': SESSION,
            'executor_native_session': 'native-2492'}
        # Screen functions only: the D6 registry read and the audit-binding
        # helpers. The verification condition under test runs unmodified.
        self._patches = [
            mock.patch.object(worker_dispatch, 'worker_key_for',
                              lambda profile: AGENT if profile == 'opencode'
                              else 'other-agent'),
            mock.patch.object(worker_dispatch, '_assignment_audit_binding',
                              lambda task, developer: 'bind-2492'),
            mock.patch.object(worker_dispatch, '_matches_assignment_binding',
                              lambda entry, binding: entry.get('binding') == binding),
            mock.patch.object(dr.task_completion, 'read_d6_registration',
                              self._read_registration)]
        for patch in self._patches:
            patch.start()
        self.addCleanup(self._stop_patches)

    def _stop_patches(self):
        for patch in reversed(self._patches):
            patch.stop()

    def _read_registration(self, *args, **kwargs):
        self.assertEqual((args[0], args[1]),
                         (TASK_ID, self.registration.common_dir))
        return self.registration

    def _repair_task(self):
        return {
            'task_id': 'repair-2492',
            'developer': {
                'provider_profile': 'opencode',
                'assignment_id': ASSIGNMENT,
                'source_sha': SHA,
                'state': 'DEVELOPER_RESPONDED',
                'dispatch_boundary': 'STARTING',
                'direct_handoffs': [{'old_assignment': OLD_ASSIGNMENT,
                                      'new_assignment': ASSIGNMENT}],
                'execution': {'status': 'DONE', 'process_exit_code': 0,
                              'cleanup_status': 'CONFIRMED',
                              'native_session_id': SESSION}},
            'completion': {'executor_task_id': TASK_ID,
                           'evidence': dict(self.evidence)},
            'dispatch_audit': [
                {'worker_key': AGENT, 'decision': 'ALLOWED',
                 'action': 'REASSIGN', 'binding': 'bind-2492'},
                {'worker_key': AGENT, 'decision': 'ALLOWED',
                 'action': 'DISPATCH', 'binding': 'bind-2492'}]}

    def _claim(self):
        return {'repair_task_id': 'repair-2492', 'executor_task': TASK_ID,
                'state': 'DISPATCHED', 'head_sha': SHA, 'pr': 2492}

    def _root(self):
        return {'task_id': 'root-2492', 'review_repair_claims': [self._claim()],
                'completion': {'executor_task_id': TASK_ID,
                               'evidence': dict(self.evidence)}}

    def test_replacement_candidate_accepts_eligible_developers(self):
        for developer in (
                {'provider_profile': 'opencode', 'state': 'DEVELOPER_PENDING'},
                {'provider_profile': 'agy', 'state': 'DEVELOPER_DISPATCHING',
                 'dispatch_boundary': 'PREPARED'},
                {'provider_profile': 'opencode',
                 'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                 'dispatch_boundary': 'STOPPED'}):
            self.assertTrue(replacement_candidate(developer))

    def test_replacement_candidate_refuses_ineligible_developers(self):
        for developer in (
                {'provider_profile': 'opencode', 'state': 'DEVELOPER_RESPONDED'},
                {'provider_profile': 'github-copilot', 'state': 'DEVELOPER_PENDING'},
                {'provider_profile': 'agy', 'state': 'DEVELOPER_DISPATCHING',
                 'dispatch_boundary': 'LAUNCHING'},
                {}, None):
            self.assertFalse(replacement_candidate(developer))

    def test_verified_repair_result_accepts_verified_result(self):
        self.assertIsNone(verified_repair_result(self._root(), self._claim(),
                                                 self._repair_task()))

    def test_verified_repair_result_refuses_unverified_result(self):
        broken = self._repair_task()
        broken['developer']['execution']['status'] = 'FAILED'
        root = self._root()
        for claim, task in (
                (dict(self._claim(), head_sha='f' * 40), self._repair_task()),
                (self._claim(), broken)):
            before = copy.deepcopy((root, claim, task))
            with self.assertRaises(LLMToolError) as ctx:
                verified_repair_result(root, claim, task)
            self.assertIn(CODE, _error_codes(ctx.exception))
            self.assertEqual((root, claim, task), before)

    def test_effective_writer_admission_returns_verified_writer(self):
        root = self._root()
        root['review_repair_claims'] = [
            {'repair_task_id': 'missing-2492', 'executor_task': TASK_ID,
             'state': 'DISPATCHED'},
            {'repair_task_id': 'repair-2492', 'executor_task': 'other-task',
             'state': 'DISPATCHED'},
            self._claim()]
        store = FakeStore({'root-2492': root, 'repair-2492': self._repair_task()})
        initial = dict(self.evidence, executor_identity='prior-' + AGENT)
        result = effective_writer_admission(store, root, initial, TASK_ID)
        expected = dict(initial)
        expected.update({key: self.evidence[key] for key in EVIDENCE_KEYS})
        self.assertEqual(result, expected)

    def test_effective_writer_admission_refuses_unverified_repair(self):
        task = self._repair_task()
        task['developer']['source_sha'] = 'f' * 40
        root = self._root()
        store = FakeStore({'root-2492': root, 'repair-2492': task})
        initial = dict(self.evidence)
        before = copy.deepcopy((store.tasks, initial))
        with self.assertRaises(LLMToolError) as ctx:
            effective_writer_admission(store, root, initial, TASK_ID)
        self.assertIn(CODE, _error_codes(ctx.exception))
        self.assertEqual((store.tasks, initial), before)

    def test_authoring_route_a1_returns_developer_without_claims(self):
        developer = {'provider_profile': 'agy',
                     'owner_assignment_ref': 'ref-2492'}
        for claims in (None, []):
            root = {'task_id': 'root-2492',
                    'completion': {'evidence': dict(self.evidence)}}
            if claims is not None:
                root['review_repair_claims'] = claims
            self.assertIs(authoring_route(FakeStore({}), root, developer,
                                          TASK_ID), developer)

    def test_authoring_route_a2_returns_developer_without_common_dir(self):
        developer = {'provider_profile': 'agy',
                     'owner_assignment_ref': 'ref-2492'}
        for evidence in ({}, {'common_dir': ''}):
            root = {'task_id': 'root-2492',
                    'review_repair_claims': [self._claim()],
                    'completion': {'evidence': evidence}}
            self.assertIs(authoring_route(FakeStore({}), root, developer,
                                          TASK_ID), developer)
        root = {'task_id': 'root-2492',
                'review_repair_claims': [self._claim()]}
        self.assertIs(authoring_route(FakeStore({}), root, developer, TASK_ID),
                      developer)

    def test_authoring_route_b_returns_developer_when_projection_matches(self):
        developer = {'provider_profile': 'agy',
                     'owner_assignment_ref': 'ref-2492'}
        evidence = dict(self.evidence, executor_session_ref='sess-2492')
        root = {'task_id': 'root-2492',
                'review_repair_claims': [dict(self._claim(), state='CLAIMED')],
                'completion': {'evidence': evidence}}
        self.assertIs(authoring_route(FakeStore({}), root, developer, TASK_ID),
                      developer)

    def test_authoring_route_c_returns_developer_when_profile_matches(self):
        root = self._root()
        store = FakeStore({'root-2492': root,
                           'repair-2492': self._repair_task()})
        developer = {'provider_profile': 'opencode',
                     'owner_assignment_ref': 'ref-2492'}
        self.assertIs(authoring_route(store, root, developer, TASK_ID),
                      developer)

    def test_authoring_route_d_returns_new_record_for_changed_profile(self):
        root = self._root()
        store = FakeStore({'root-2492': root,
                           'repair-2492': self._repair_task()})
        developer = {'provider_profile': 'agy',
                     'owner_assignment_ref': 'ref-2492',
                     'goal': 'goal-2492',
                     'acceptance': ['acceptance-2492'],
                     'read_paths': ['read-2492'],
                     'write_paths': ['write-2492']}
        result = authoring_route(store, root, developer, TASK_ID)
        self.assertIsNot(result, developer)
        self.assertEqual(result, dict(developer, provider_profile='opencode',
                                      owner_assignment_ref=None))
        self.assertEqual(developer['provider_profile'], 'agy')
        self.assertEqual(developer['owner_assignment_ref'], 'ref-2492')

    def test_authoring_route_e_refuses_unknown_projected_identity(self):
        task = self._repair_task()
        task['completion']['evidence']['executor_identity'] = 'unknown-agent'
        root = self._root()
        store = FakeStore({'root-2492': root, 'repair-2492': task})
        developer = {'provider_profile': 'opencode'}
        before = copy.deepcopy((store.tasks, developer))
        with self.assertRaises(LLMToolError) as ctx:
            authoring_route(store, root, developer, TASK_ID)
        self.assertIn('DIRECT_REASSIGN_D6_BINDING_MISMATCH',
                      _error_codes(ctx.exception))
        self.assertEqual((store.tasks, developer), before)

    def test_authoring_route_f_refuses_unverified_repair(self):
        task = self._repair_task()
        task['developer']['source_sha'] = 'f' * 40
        root = self._root()
        store = FakeStore({'root-2492': root, 'repair-2492': task})
        developer = {'provider_profile': 'opencode'}
        before = copy.deepcopy((store.tasks, developer))
        with self.assertRaises(LLMToolError) as ctx:
            authoring_route(store, root, developer, TASK_ID)
        self.assertIn(CODE, _error_codes(ctx.exception))
        self.assertEqual((store.tasks, developer), before)

    def test_effective_writer_admission_skips_bad_claims(self):
        initial = dict(self.evidence)
        claims_values = ('claims-2492', {'repair_task_id': 'repair-2492'},
                         [self._claim(), 'claim-2492'])
        for claims in claims_values:
            root = {'review_repair_claims': claims,
                    'completion': {'evidence': dict(initial)}}
            self.assertIs(effective_writer_admission(
                FakeStore({}), root, initial, TASK_ID), initial)

    def test_effective_writer_admission_skips_other_state_and_executor(self):
        initial = dict(self.evidence)
        claims = (dict(self._claim(), state='CLAIMED'),
                  dict(self._claim(), executor_task='other-task'))
        for claim in claims:
            root = {'review_repair_claims': [claim],
                    'completion': {'evidence': dict(initial)}}
            self.assertIs(effective_writer_admission(
                FakeStore({}), root, initial, TASK_ID), initial)

    def test_effective_writer_admission_skips_missing_repair(self):
        root = self._root()
        initial = dict(self.evidence)
        self.assertIs(effective_writer_admission(
            FakeStore({'root-2492': root}), root, initial, TASK_ID), initial)

    def test_effective_writer_admission_skips_store_error(self):
        root = self._root()
        store = FailingStore({'root-2492': root}, 'repair-2492')
        initial = dict(self.evidence)
        self.assertIs(effective_writer_admission(store, root, initial, TASK_ID),
                      initial)

    def test_effective_writer_admission_skips_unresponded_repair(self):
        initial = dict(self.evidence)
        developers = ({'direct_handoffs': [],
                       'state': 'DEVELOPER_RESPONDED'},
                      {'direct_handoffs': [{'new_assignment': ASSIGNMENT}],
                       'state': 'DEVELOPER_PENDING'})
        for developer in developers:
            task = self._repair_task()
            task['developer'].update(developer)
            root = self._root()
            store = FakeStore({'root-2492': root, 'repair-2492': task})
            self.assertIs(effective_writer_admission(
                store, root, initial, TASK_ID), initial)

    def test_effective_writer_admission_returns_initial_unverified(self):
        initial = dict(self.evidence)
        root = {'review_repair_claims': [],
                'completion': {'evidence': dict(initial)}}
        self.assertIs(effective_writer_admission(
            FakeStore({}), root, initial, TASK_ID), initial)

    def test_authoring_route_refuses_malformed_root(self):
        developer = {'provider_profile': 'opencode'}
        roots = (None, [], 'root-2492', 2492,
                 {'review_repair_claims': [self._claim()],
                  'completion': 'bad'},
                 {'review_repair_claims': [self._claim()],
                  'completion': {'evidence': 'bad'}})
        for root in roots:
            before = copy.deepcopy((root, developer))
            with self.assertRaises(LLMToolError) as ctx:
                authoring_route(FakeStore({}), root, developer, TASK_ID)
            self.assertIn(ROOT_INVALID, _error_codes(ctx.exception))
            self.assertEqual((root, developer), before)

    def test_effective_writer_admission_refuses_missing_repair_evidence(self):
        removals = ('completion', 'evidence', 'executor_identity',
                    'executor_session_ref', 'executor_native_session_id',
                    'executor_native_session')
        for removal in removals:
            task = self._repair_task()
            if removal == 'completion':
                del task['completion']
            elif removal == 'evidence':
                del task['completion']['evidence']
            else:
                del task['completion']['evidence'][removal]
            root = self._root()
            store = FakeStore({'root-2492': root, 'repair-2492': task})
            initial = dict(self.evidence)
            before = copy.deepcopy((store.tasks, initial))
            with self.assertRaises(LLMToolError) as ctx:
                effective_writer_admission(store, root, initial, TASK_ID)
            self.assertIn(EVIDENCE_INVALID, _error_codes(ctx.exception))
            self.assertEqual((store.tasks, initial), before)


if __name__ == '__main__':
    unittest.main()
