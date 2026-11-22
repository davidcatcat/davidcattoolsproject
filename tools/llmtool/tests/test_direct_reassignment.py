"""Direct tests for llmtool.direct_reassignment helpers (issue #2492)."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llmtool.direct_reassignment as dr
from llmtool import worker_dispatch
from llmtool.direct_reassignment import (effective_writer_admission,
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
        for claim, task in (
                (dict(self._claim(), head_sha='f' * 40), self._repair_task()),
                (self._claim(), broken)):
            with self.assertRaises(LLMToolError) as ctx:
                verified_repair_result(self._root(), claim, task)
            self.assertIn(CODE, _error_codes(ctx.exception))

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
        store = FakeStore({'root-2492': self._root(), 'repair-2492': task})
        with self.assertRaises(LLMToolError) as ctx:
            effective_writer_admission(store, store.tasks['root-2492'],
                                       dict(self.evidence), TASK_ID)
        self.assertIn(CODE, _error_codes(ctx.exception))


if __name__ == '__main__':
    unittest.main()
