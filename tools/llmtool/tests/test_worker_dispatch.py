import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from llmtool import worker_dispatch
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, new_id
from llmtool.task_submission_cli import _record


class WorkerDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.policy_path = self.root / 'worker-dispatch.json'
        self.policy = json.loads(Path(worker_dispatch._POLICY_PATH).read_text(encoding='utf-8'))
        self.policy_path.write_text(json.dumps(self.policy), encoding='utf-8')
        self.policy_patch = patch.object(worker_dispatch, '_POLICY_PATH', self.policy_path)
        self.policy_patch.start()
        self.addCleanup(self.policy_patch.stop)
        self.store = TaskStore(self.root / 'tasks')
        self.task = self._task()
        self.store.create(self.task)

    def _task(self):
        request = 'Bounded non-sensitive Developer task.'
        task_id, run_id = new_id(), new_id()
        request_sha = hashlib.sha256(request.encode()).hexdigest()
        value = _record(task_id, run_id, 'pm-' + new_id(), 'pm-profile', request,
                        state='PM_RESPONDED', provider_type='openai-compatible',
                        model='pm-model', backend='NO_NATIVE_SESSION_ID_EXPOSED',
                        request_sha=request_sha)
        value.update(
            pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                              'base_url': None},
            response='PM accepted a bounded request.', error_code=None,
            pm_assignment={'schema_version': 1, 'route': 'company-gw', 'risk': 'LOW',
                           'goal': 'Add a focused test.', 'acceptance': ['the test passes']},
        )
        return value

    @staticmethod
    def developer(*, state='DEVELOPER_PENDING', provider='company-gw'):
        return {
            'assignment_id': new_id(), 'selection_source': 'PM', 'state': state,
            'provider_profile': provider, 'provider_type': None, 'model': None,
            'backend_session_semantics': 'NO_NATIVE_SESSION_ID_EXPOSED',
            'session_id': 'dev-' + new_id(), 'source_sha': 'a' * 40,
            'source_sha_binding': 'D6_REGISTRY_OBSERVED',
            'read_paths': ['src.py'], 'write_paths': ['src.py'],
            'acceptance': ['the test passes'], 'context_sha256': 'b' * 64,
            'egress_basis': 'Approved non-sensitive test context.', 'input_bytes': 10,
            'max_tokens': 64, 'artifact_format': 'unified_diff', 'artifact': None,
            'artifact_sha256': None, 'usage': None, 'error_code': None,
            'created_at': datetime.now(timezone.utc).replace(microsecond=0).strftime(
                '%Y-%m-%dT%H:%M:%SZ'),
        }

    def _set_state(self, worker, state):
        self.policy['workers'][worker]['dispatch'] = state
        self.policy['workers'][worker]['reason'] = 'test policy state'
        self.policy_path.write_text(json.dumps(self.policy), encoding='utf-8')

    def test_owner_policy_states_are_read_from_the_exact_schema(self):
        self.assertTrue(worker_dispatch.evaluate('codex-developer').allowed)
        self.assertTrue(worker_dispatch.evaluate('company-gw').allowed)
        self.assertTrue(worker_dispatch.evaluate('claude-reviewer').allowed)
        self.assertEqual(worker_dispatch.evaluate('claude-developer').reason_code,
                         'OWNER_ASSIGNMENT_REQUIRED')
        self.assertEqual(worker_dispatch.evaluate('opencode-developer').reason_code,
                         'OWNER_PAUSED')
        self.assertFalse(worker_dispatch.evaluate('unknown-worker').allowed)

    def test_policy_is_reloaded_after_each_change(self):
        self.assertTrue(worker_dispatch.evaluate('opencode-developer').allowed is False)
        self._set_state('opencode-developer', 'ALLOWED')
        self.assertTrue(worker_dispatch.evaluate('opencode-developer').allowed)
        self._set_state('opencode-developer', 'PAUSED')
        self.assertFalse(worker_dispatch.evaluate('opencode-developer').allowed)

    def test_missing_malformed_duplicate_and_unknown_policy_fail_closed(self):
        self.policy_path.unlink()
        self.assertEqual(worker_dispatch.evaluate('company-gw').reason_code,
                         'WORKER_POLICY_MISSING')
        self.policy_path.write_text('{broken', encoding='utf-8')
        self.assertFalse(worker_dispatch.evaluate('company-gw').allowed)
        self.policy_path.write_text(
            '{"schema_version":1,"schema_version":1,"workers":{}}', encoding='utf-8')
        self.assertEqual(worker_dispatch.evaluate('company-gw').reason_code,
                         'WORKER_POLICY_INVALID')
        self.policy['workers']['extra-worker'] = self.policy['workers']['company-gw']
        self.policy_path.write_text(json.dumps(self.policy), encoding='utf-8')
        self.assertEqual(worker_dispatch.evaluate('company-gw').reason_code,
                         'WORKER_POLICY_INVALID')

    def test_taskstore_denies_direct_assignment_and_persists_blocked_audit(self):
        self._set_state('opencode-developer', 'PAUSED')
        developer = self.developer(provider='opencode')
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied):
            self.store.compare_and_update(
                self.task['task_id'],
                expected={'developer': None, 'state': 'PM_RESPONDED'},
                developer=developer)
        saved = self.store.get(self.task['task_id'])
        self.assertIsNone(saved['developer'])
        self.assertEqual(saved['dispatch_audit'][-1]['worker_key'], 'opencode-developer')
        self.assertEqual(saved['dispatch_audit'][-1]['decision'], 'BLOCKED')
        self.assertEqual(saved['dispatch_audit'][-1]['reason_code'], 'OWNER_PAUSED')

    def test_allowed_taskstore_assignment_and_adapter_attempt_are_both_audited(self):
        self._set_state('company-gw', 'ALLOWED')
        developer = self.developer()
        saved = self.store.compare_and_update(
            self.task['task_id'], expected={'developer': None, 'state': 'PM_RESPONDED'},
            developer=developer)
        dispatching = {**developer, 'state': 'DEVELOPER_DISPATCHING',
                       'provider_type': 'openai-compatible', 'model': 'gw-model'}
        saved = self.store.update(self.task['task_id'], developer=dispatching)
        self.assertEqual([entry['decision'] for entry in saved['dispatch_audit']],
                         ['ALLOWED', 'ALLOWED'])
        self._set_state('company-gw', 'PAUSED')
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            worker_dispatch.authorize_dispatch(
                self.store, self.task['task_id'], 'company-gw', 'DISPATCH')
        self.assertEqual(caught.exception.dispatch_code, 'OWNER_PAUSED')
        saved = self.store.get(self.task['task_id'])
        self.assertEqual(saved['dispatch_audit'][-1]['decision'], 'BLOCKED')
        self.assertEqual(saved['dispatch_audit'][-1]['reason_code'], 'OWNER_PAUSED')

    def test_adapter_requires_a_durable_taskstore_dispatching_claim(self):
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            worker_dispatch.authorize_dispatch(
                self.store, self.task['task_id'], 'company-gw', 'DISPATCH')
        self.assertEqual(caught.exception.dispatch_code,
                         'WORKER_ASSIGNMENT_NOT_DISPATCHING')
        saved = self.store.get(self.task['task_id'])
        self.assertEqual(saved['dispatch_audit'][-1]['decision'], 'BLOCKED')

    def test_ambiguous_provider_outcome_cannot_be_replayed_through_taskstore(self):
        developer = self.developer()
        self.store.compare_and_update(
            self.task['task_id'], expected={'developer': None}, developer=developer)
        unknown = {**developer, 'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                   'provider_type': 'openai-compatible', 'model': 'gw-model',
                   'error_code': 'DEVELOPER_TIMEOUT'}
        self.store.update(self.task['task_id'], developer=unknown)
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied):
            self.store.update(self.task['task_id'],
                developer={**unknown, 'state': 'DEVELOPER_DISPATCHING', 'error_code': None})
        saved = self.store.get(self.task['task_id'])
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(saved['dispatch_audit'][-1]['reason_code'],
                         'WORKER_REPLAY_FORBIDDEN')

    def test_ambiguous_provider_outcome_cannot_reset_to_pending(self):
        developer = self.developer()
        self.store.compare_and_update(
            self.task['task_id'], expected={'developer': None}, developer=developer)
        unknown = {**developer, 'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                   'provider_type': 'openai-compatible', 'model': 'gw-model',
                   'error_code': 'DEVELOPER_TIMEOUT'}
        self.store.update(self.task['task_id'], developer=unknown)
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied):
            self.store.update(self.task['task_id'],
                              developer={**unknown, 'state': 'DEVELOPER_PENDING'})
        saved = self.store.get(self.task['task_id'])
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(saved['dispatch_audit'][-1]['reason_code'],
                         'WORKER_REPLAY_FORBIDDEN')

    def test_same_id_provider_route_change_is_a_gated_reassignment(self):
        developer = self.developer()
        self.store.compare_and_update(
            self.task['task_id'], expected={'developer': None}, developer=developer)
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            self.store.update(self.task['task_id'],
                              developer={**developer, 'provider_profile': 'opencode'})
        self.assertEqual(caught.exception.dispatch_code, 'OWNER_PAUSED')
        saved = self.store.get(self.task['task_id'])
        self.assertEqual(saved['developer']['provider_profile'], 'company-gw')
        self.assertEqual(saved['dispatch_audit'][-1]['worker_key'], 'opencode-developer')
        self.assertEqual(saved['dispatch_audit'][-1]['action'], 'REASSIGN')

    def test_audit_cannot_be_forged_by_generic_taskstore_update(self):
        with self.assertRaises(LLMToolError):
            self.store.update(self.task['task_id'], dispatch_audit=[])


if __name__ == '__main__':
    unittest.main()
