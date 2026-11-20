"""#2415: a DEVELOPER_DISPATCHING record with no recorded child is recovered as not started.

One test per row of the case table: (a) agy, (b) opencode, (c) recorded live
child, (d) recorded child with unknown liveness, (e) other providers or states,
(f) no repair claim or count, (g) the store's own refusals.
"""
import io
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

from llmtool import opencode_direct_developer
from llmtool import task_store
from llmtool import task_submission_cli as submission_cli
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, new_id
from llmtool.task_submission_cli import run_task_record_pm

AGENTS = {'agy': 'agy-developer', 'opencode': 'opencode-developer'}


class NotStartedRecoveryTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(submission_cli.worker_dispatch,
                               'authorize_task_store_change', return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _store(self, profile, *, launched=False, state='DEVELOPER_DISPATCHING'):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = TaskStore(Path(tmp.name).resolve() / 'tasks')
        response = json.dumps({
            'schema_version': 1, 'summary': 'Bounded not-started test.',
            'developer_assignment': {
                'route': profile, 'risk': 'LOW', 'goal': 'Recover a dispatch.',
                'acceptance': ['The record returns to its post-PM state.'],
            },
        })
        task = run_task_record_pm(
            Namespace(stdin_json=True), store=store,
            input_stream=io.StringIO(json.dumps({
                'request': 'Recover a never-launched dispatch.', 'response': response,
            })), return_record=True)
        task_id = task['task_id']
        developer = {
            'assignment_id': new_id(), 'selection_source': 'PM',
            'executor_task_id': 'executor-one', 'state': 'DEVELOPER_PENDING',
            'provider_profile': profile, 'provider_type': None, 'model': None,
            'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
            'session_id': 'dev-' + new_id(), 'source_sha': 'a' * 40,
            'source_sha_binding': 'D6_REGISTRY_OBSERVED', 'read_paths': ['src.py'],
            'write_paths': ['src.py'], 'acceptance': ['The record returns to its post-PM state.'],
            'context_sha256': 'b' * 64, 'egress_basis': 'Approved test fixture.',
            'input_bytes': 1, 'max_tokens': None, 'artifact_format': 'unified_diff',
            'artifact': None, 'artifact_sha256': None, 'usage': None, 'error_code': None,
            'execution': None, 'created_at': '2026-11-02T00:00:00Z',
        }
        store.compare_and_update(task_id, expected={'developer': None}, developer=developer)
        if state == 'DEVELOPER_DISPATCHING':
            developer.update(state=state, provider_type=profile + '-cli',
                             model=profile + '/test')
            if profile == 'opencode':
                developer['max_tokens'] = opencode_direct_developer.MAX_OUTPUT_TOKENS
            store.update(task_id, developer=developer)
        if launched:
            store.record_opencode_launch(task_id, assignment_id=developer['assignment_id'],
                                         pid=321, start_time='123456', pgid=321)
        return store, task_id

    def _recover(self, store, task_id, profile):
        args = Namespace(task_id=task_id, executor_task='executor-one')
        snapshot = type('Snapshot', (), {'common_dir': '/repo/.git', 'root': '/repo'})()
        registration = type('Registration', (), {
            'task_id': 'executor-one', 'trusted_agent_id': AGENTS[profile]})()
        developer = store.get(task_id)['developer']
        with patch.object(submission_cli.task_completion, 'collect_git_snapshot',
                          return_value=snapshot), \
             patch.object(submission_cli.task_completion, 'bind_executor_task',
                          return_value=(registration, snapshot)):
            return submission_cli._recover_direct_child_dispatch(
                args, store.get(task_id), developer, store, expected_agent=AGENTS[profile])

    def _assert_post_pm(self, store, task_id):
        saved = store.get(task_id)
        self.assertIsNone(saved['developer'])
        self.assertIsNone(saved['completion'])
        self.assertEqual(saved['state'], 'PM_RESPONDED')

    def test_a_agy_without_child_is_recovered_as_never_launched(self):
        store, task_id = self._store('agy')
        self.assertIsNone(self._recover(store, task_id, 'agy'))
        self._assert_post_pm(store, task_id)

    def test_b_opencode_without_child_is_recovered_the_same_way(self):
        store, task_id = self._store('opencode')
        self.assertIsNone(self._recover(store, task_id, 'opencode'))
        self._assert_post_pm(store, task_id)

    def test_c_recorded_live_child_is_refused(self):
        store, task_id = self._store('opencode', launched=True)
        before = store.get(task_id)
        with patch.object(submission_cli.os, 'kill', return_value=None), \
             patch.object(submission_cli.codex_direct_developer, 'process_start_time',
                          return_value='123456'), \
             patch.object(submission_cli.os, 'getpgid', return_value=321):
            with self.assertRaisesRegex(LLMToolError, 'DIRECT_CHILD_STILL_RUNNING'):
                self._recover(store, task_id, 'opencode')
        self.assertEqual(store.get(task_id), before)

    def test_c_agy_with_a_recorded_child_is_refused_without_a_write(self):
        store = MagicMock()
        developer = {'provider_profile': 'agy', 'state': 'DEVELOPER_DISPATCHING',
                     'executor_task_id': 'executor-one',
                     'execution': {'child_pid': 321, 'child_start_time': '123456',
                                   'child_pgid': 321}}
        snapshot = type('Snapshot', (), {'common_dir': '/repo/.git', 'root': '/repo'})()
        registration = type('Registration', (), {
            'task_id': 'executor-one', 'trusted_agent_id': 'agy-developer'})()
        with patch.object(submission_cli.task_completion, 'collect_git_snapshot',
                          return_value=snapshot), \
             patch.object(submission_cli.task_completion, 'bind_executor_task',
                          return_value=(registration, snapshot)):
            with self.assertRaisesRegex(LLMToolError, 'DIRECT_CHILD_LIVENESS_UNKNOWN'):
                submission_cli._recover_direct_child_dispatch(
                    Namespace(task_id='t', executor_task='executor-one'), {}, developer,
                    store, expected_agent='agy-developer')
        self.assertEqual(store.mock_calls, [])

    def test_d_recorded_child_with_unknown_liveness_is_refused(self):
        store, task_id = self._store('opencode', launched=True)
        before = store.get(task_id)
        with patch.object(submission_cli.os, 'kill', return_value=None), \
             patch.object(submission_cli.codex_direct_developer, 'process_start_time',
                          return_value=None):
            with self.assertRaisesRegex(LLMToolError, 'DIRECT_CHILD_LIVENESS_UNKNOWN'):
                self._recover(store, task_id, 'opencode')
        self.assertEqual(store.get(task_id), before)

    def test_e_other_providers_and_states_are_unchanged(self):
        for profile, state in (('codex', 'DEVELOPER_DISPATCHING'),
                               ('agy', 'DEVELOPER_PENDING'),
                               ('opencode', 'DEVELOPER_OUTCOME_UNKNOWN')):
            with self.subTest(profile=profile, state=state):
                store = MagicMock()
                developer = {'provider_profile': profile, 'state': state, 'execution': None,
                             'executor_task_id': 'executor-one'}
                with self.assertRaisesRegex(LLMToolError, 'DIRECT_CHILD_LIVENESS_UNKNOWN'):
                    submission_cli._recover_direct_child_dispatch(
                        Namespace(task_id='t', executor_task='executor-one'), {}, developer,
                        store, expected_agent='opencode-developer')
                self.assertEqual(store.mock_calls, [])

    def test_f_recovery_adds_no_repair_claim_and_no_count(self):
        for profile in ('agy', 'opencode'):
            with self.subTest(profile=profile):
                store, task_id = self._store(profile)
                before = store.get(task_id)
                self._recover(store, task_id, profile)
                after = store.get(task_id)
                self.assertEqual(after.get('dispatch_audit', []),
                                 before.get('dispatch_audit', []))
                self.assertEqual(after.get('review_repair_claims', []),
                                 before.get('review_repair_claims', []))
                self.assertEqual(after.get('start_failures', []),
                                 before.get('start_failures', []))
                changed = {key for key in set(before) | set(after)
                           if before.get(key) != after.get(key)}
                self.assertEqual(changed - {'record_sha256', 'updated_at'}, {'developer'})

    def test_g_store_refuses_a_record_with_a_child(self):
        store, task_id = self._store('opencode', launched=True)
        before = store.get(task_id)
        with self.assertRaises(LLMToolError):
            store.clear_unstarted_direct_dispatch(
                task_id, expected_developer=before['developer'])
        self.assertEqual(store.get(task_id), before)

    def test_g_store_refuses_a_stale_expected_developer(self):
        store, task_id = self._store('agy')
        before = store.get(task_id)
        stale = dict(before['developer'], assignment_id=new_id())
        with self.assertRaises(task_store.TaskStoreConflict):
            store.clear_unstarted_direct_dispatch(task_id, expected_developer=stale)
        self.assertEqual(store.get(task_id), before)

    def test_g_store_refuses_a_record_that_is_not_dispatching(self):
        store, task_id = self._store('agy', state='DEVELOPER_PENDING')
        before = store.get(task_id)
        with self.assertRaises(LLMToolError):
            store.clear_unstarted_direct_dispatch(
                task_id, expected_developer=before['developer'])
        self.assertEqual(store.get(task_id), before)

    def test_g_guard_refuses_a_completion_or_result(self):
        store, task_id = self._store('agy')
        record = store.get(task_id)
        self.assertTrue(task_store._unstarted_direct_dispatch(record))
        claim = {'state': 'WAITING_EXECUTOR', 'pm_dispatch': 'NOT_STARTED',
                 'reviewer_task_id': None, 'executor_task_id': 'executor-one',
                 'evidence': {'result_head': None}}
        self.assertTrue(task_store._unstarted_direct_dispatch(dict(record, completion=claim)))
        refused = {
            'advanced completion': dict(record, completion=dict(claim, state='WAITING_PM')),
            'result head': dict(record, completion=dict(
                claim, evidence={'result_head': 'c' * 40})),
            'pm dispatched': dict(record, completion=dict(claim, pm_dispatch='CLAIMED')),
            'artifact': dict(record, developer=dict(record['developer'], artifact='diff')),
            'usage': dict(record, developer=dict(record['developer'], usage={'total_tokens': 1})),
            'callback advanced': dict(record, developer=dict(
                record['developer'],
                direct_result_callback={'state': 'WAITING_PM', 'pm_dispatch': 'NOT_STARTED'})),
            'child recorded': dict(record, developer=dict(
                record['developer'], execution={'child_pid': 321})),
            'process result': dict(record, developer=dict(
                record['developer'], execution={'process_exit_code': 0})),
            'completion events': dict(record, completion_events=[{'source': 'EXECUTOR'}]),
            'wrong provider': dict(record, developer=dict(
                record['developer'], provider_profile='codex')),
            'not PM_RESPONDED': dict(record, state='PM_FAILED'),
        }
        for name, candidate in refused.items():
            with self.subTest(name):
                self.assertFalse(task_store._unstarted_direct_dispatch(candidate))


if __name__ == '__main__':
    unittest.main()
