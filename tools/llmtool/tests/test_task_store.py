import hashlib
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool.action_contract import TaskBinding
from llmtool.errors import LLMToolError
from llmtool import worker_dispatch
from llmtool.task_store import TaskStore, new_id
from llmtool.task_submission_cli import _record
from llmtool.task_verification import AcceptanceCriterion, CriterionKind, TaskContract


def record(request='Implement a small tested feature'):
    task_id, run_id = new_id(), new_id()
    request_sha = hashlib.sha256(request.encode()).hexdigest()
    contract = TaskContract(
        schema_version=1, task_id=task_id, contract_version=1,
        previous_digest=None, goal='PM intake request sha256:' + request_sha,
        authorized_scope=('pm-planning',), forbidden_scope=(),
        criteria=(AcceptanceCriterion('pm_response', CriterionKind.ARTIFACT,
                                      'Record the PM reply or unavailable status.'),),
    )
    return {
        'schema_version': 1, 'task_id': task_id, 'run_id': run_id,
        'pm_session_id': 'pm-' + new_id(), 'pm_provider_profile': 'pm',
        'pm_provider_type': 'codex-cli', 'pm_model': 'gpt-6-luna',
        'backend_session_semantics': 'EPHEMERAL_SINGLE_CALL', 'native_session_id': None,
        'state': 'PM_PENDING', 'created_at': '2026-10-21T00:00:00Z',
        'updated_at': '2026-10-21T00:00:00Z', 'task_contract': contract.to_dict(),
        'task_binding': contract.binding(run_id).to_dict(), 'request': request,
        'request_sha256': request_sha, 'response': None, 'usage': None,
        'error_code': None, 'record_sha256': None,
    }


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'tasks'
        self.store = TaskStore(self.root)

    def _codex_dispatch_task(self):
        request = 'Bounded non-sensitive Codex proposal task.'
        task_id, run_id = new_id(), new_id()
        request_sha = hashlib.sha256(request.encode()).hexdigest()
        task = _record(
            task_id, run_id, 'pm-' + new_id(), 'pm-profile', request,
            state='PM_RESPONDED', provider_type='openai-compatible', model='pm-model',
            backend='NO_NATIVE_SESSION_ID_EXPOSED', request_sha=request_sha)
        task.update(
            pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                              'base_url': None},
            response='PM returned a bounded task.', error_code=None,
            pm_assignment={'schema_version': 1, 'route': 'codex', 'risk': 'LOW',
                           'goal': 'Produce a proposal diff.',
                           'acceptance': ['the proposal is recorded']},
        )
        return task

    @staticmethod
    def _developer_assignment(*, provider='codex', max_tokens=None):
        return {
            'assignment_id': new_id(), 'selection_source': 'PM',
            'state': 'DEVELOPER_PENDING', 'provider_profile': provider,
            'provider_type': None, 'model': None,
            'backend_session_semantics': 'NO_NATIVE_SESSION_ID_EXPOSED',
            'session_id': 'dev-' + new_id(), 'source_sha': 'a' * 40,
            'source_sha_binding': 'D6_REGISTRY_OBSERVED',
            'read_paths': ['src.py'], 'write_paths': ['src.py'],
            'acceptance': ['the proposal is recorded'],
            'context_sha256': 'b' * 64,
            'egress_basis': 'Approved non-sensitive test context.', 'input_bytes': 10,
            'max_tokens': max_tokens, 'artifact_format': 'unified_diff',
            'artifact': None, 'artifact_sha256': None, 'usage': None,
            'error_code': None, 'created_at': '2026-10-23T00:00:00Z',
        }

    def _allow_codex_dispatch(self):
        policy_path = Path(worker_dispatch._POLICY_PATH)
        policy = json.loads(policy_path.read_text(encoding='utf-8'))
        policy['workers']['codex-developer']['dispatch'] = 'ALLOWED'
        temporary_policy = Path(self.temp.name) / 'worker-dispatch.json'
        temporary_policy.write_text(json.dumps(policy), encoding='utf-8')
        patcher = patch.object(worker_dispatch, '_POLICY_PATH', temporary_policy)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_codex_cli_dispatch_and_response_allow_unknown_token_cap(self):
        self._allow_codex_dispatch()
        task = self._codex_dispatch_task()
        self.store.create(task)
        pending = self._developer_assignment(provider='codex', max_tokens=None)
        self.store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=pending)

        dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                       'provider_type': 'codex-cli', 'model': 'gpt-6-luna'}
        self.store.update(task['task_id'], developer=dispatching)
        artifact = ('diff --git a/src.py b/src.py\n'
                    '--- a/src.py\n+++ b/src.py\n@@ -1 +1 @@\n-x=1\n+x=2\n')
        responded = {
            **dispatching, 'state': 'DEVELOPER_RESPONDED', 'artifact': artifact,
            'artifact_sha256': hashlib.sha256(artifact.encode()).hexdigest(),
        }
        saved = self.store.update(task['task_id'], developer=responded)
        self.assertEqual(saved['developer']['provider_profile'], 'codex')
        self.assertEqual(saved['developer']['provider_type'], 'codex-cli')
        self.assertIsNone(saved['developer']['max_tokens'])
        self.assertIsNone(saved['developer']['usage'])
        before_clear = saved
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            self.store.update(task['task_id'], developer=None)
        self.assertEqual(caught.exception.dispatch_code,
                         'WORKER_ASSIGNMENT_CLEAR_FORBIDDEN')
        after_clear = self.store.get(task['task_id'])
        self.assertEqual(after_clear['developer'], before_clear['developer'])
        self.assertEqual(len(after_clear['dispatch_audit']),
                         len(before_clear['dispatch_audit']) + 1)
        refusal = after_clear['dispatch_audit'][-1]
        self.assertEqual(refusal['worker_key'], 'codex-developer')
        self.assertEqual(refusal['action'], 'REASSIGN')
        self.assertEqual(refusal['decision'], 'BLOCKED')
        self.assertEqual(refusal['reason_code'], 'WORKER_ASSIGNMENT_CLEAR_FORBIDDEN')
        self.assertRegex(refusal['policy_sha256'], r'^[0-9a-f]{64}$')

    def test_unknown_token_cap_is_rejected_for_other_routes_and_codex_cannot_claim_one(self):
        self._allow_codex_dispatch()
        task = self._codex_dispatch_task()
        self.store.create(task)
        pending = self._developer_assignment(provider='company-gw', max_tokens=None)
        self.store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=pending)
        dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                       'provider_type': 'openai-compatible', 'model': 'gw-model'}
        with self.assertRaises(LLMToolError):
            self.store.update(task['task_id'], developer=dispatching)

        other = self._codex_dispatch_task()
        claimed_cap = self._developer_assignment(provider='codex', max_tokens=64)
        other['developer'] = claimed_cap
        with self.assertRaises(LLMToolError):
            self.store.create(other)

    def test_owner_private_creation_and_second_store_read(self):
        value = record()
        created = self.store.create(value)
        loaded = TaskStore(self.root).get(value['task_id'])
        self.assertEqual(created, loaded)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        path = self.root / (value['task_id'] + '.json')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(path.stat().st_nlink, 1)
        self.assertEqual(loaded['task_binding']['task_id'], loaded['task_id'])

    def test_atomic_update_preserves_integrity_and_removes_lock(self):
        value = record()
        self.store.create(value)
        changed = self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                    error_code='PROVIDER_TIMEOUT')
        self.assertEqual(changed['state'], 'PM_UNAVAILABLE')
        self.assertEqual(changed['error_code'], 'PROVIDER_TIMEOUT')
        self.assertEqual(self.store.get(value['task_id']), changed)
        self.assertFalse(any(path.name.endswith('.lock') for path in self.root.iterdir()))

    def test_failed_atomic_replace_keeps_previous_record_and_releases_lock(self):
        value = record()
        self.store.create(value)
        previous = self.store.get(value['task_id'])
        with patch('llmtool.task_store.os.replace', side_effect=OSError('private detail')):
            with self.assertRaises(LLMToolError):
                self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                  error_code='PROVIDER_TIMEOUT')
        self.assertEqual(self.store.get(value['task_id']), previous)
        self.assertFalse(any(path.name.endswith('.lock') for path in self.root.iterdir()))
        self.assertFalse(any(path.name.endswith('.tmp') for path in self.root.iterdir()))

    def test_unknown_malformed_corrupt_symlink_and_wrong_mode_reject(self):
        with self.assertRaises(LLMToolError):
            self.store.get('../outside')
        with self.assertRaises(LLMToolError):
            self.store.get('0' * 32)
        value = record()
        self.store.create(value)
        path = self.root / (value['task_id'] + '.json')
        raw = json.loads(path.read_text())
        raw['state'] = 'PM_RESPONDED'
        path.write_text(json.dumps(raw))
        with self.assertRaises(LLMToolError):
            self.store.get(value['task_id'])
        path.unlink()
        target = self.root / 'target'
        target.write_text('{}')
        path.symlink_to(target)
        with self.assertRaises(LLMToolError):
            self.store.get(value['task_id'])
        path.unlink()
        self.store.create(value)
        path.chmod(0o644)
        with self.assertRaises(LLMToolError):
            self.store.get(value['task_id'])

    def test_secret_like_request_or_response_never_persists(self):
        for key, secret in (('request', 'api_key=sk-abcdefghijklmnopqrstuvwxyz'),
                            ('response', 'Bearer abcdefghijklmnop')):
            value = record()
            value[key] = secret
            if key == 'request':
                value['request_sha256'] = hashlib.sha256(secret.encode()).hexdigest()
                contract = TaskContract(
                    schema_version=1, task_id=value['task_id'], contract_version=1,
                    previous_digest=None,
                    goal='PM intake request sha256:' + value['request_sha256'],
                    authorized_scope=('pm-planning',), forbidden_scope=(),
                    criteria=(AcceptanceCriterion('pm_response', CriterionKind.ARTIFACT,
                                                  'Record PM outcome.'),),
                )
                value['task_contract'] = contract.to_dict()
                value['task_binding'] = contract.binding(value['run_id']).to_dict()
            else:
                value['state'] = 'PM_RESPONDED'
                value['error_code'] = None
            with self.subTest(key=key), self.assertRaises(LLMToolError):
                self.store.create(value)
        self.assertFalse(self.root.exists())

    def test_state_requires_matching_request_provider_and_outcome(self):
        value = record()
        value.update(state='PM_RESPONDED', request=None,
                     response='A recorded PM reply.', error_code=None)
        with self.assertRaises(LLMToolError):
            self.store.create(value)

        value = record()
        value.update(state='INPUT_REJECTED', backend_session_semantics='EPHEMERAL_SINGLE_CALL',
                     error_code='PM_INPUT_REJECTED')
        with self.assertRaises(LLMToolError):
            self.store.create(value)

    def _active_pm_record(self):
        value = record()
        value.update(
            schema_version=2,
            pm_provider_profile='active-pm-runtime',
            pm_provider_type='codex-cli',
            pm_model=None,
            pm_route_binding={'type': 'codex-cli', 'model': None, 'base_url': None},
            backend_session_semantics='ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL',
            state='PM_RESPONDED',
            response='{"schema_version":1,"summary":"Ready.","developer_assignment":'
                     '{"route":"opencode","risk":"LOW","goal":"Do task.",'
                     '"acceptance":["A test passes."]}}',
            error_code=None,
            developer=None,
            pm_assignment={
                'schema_version': 1, 'route': 'opencode', 'risk': 'LOW',
                'goal': 'Do task.', 'acceptance': ['A test passes.'],
            },
            completion=None,
            pm_provenance={
                'source': 'ACTIVE_PM_RUNTIME', 'provider_invoked': False,
                'native_session_id_status': 'UNAVAILABLE', 'usage_status': 'UNKNOWN',
                'session_restored': False, 'automatic_callback': False,
            },
        )
        return value

    def test_active_pm_provenance_is_validated_and_round_trips(self):
        value = self._active_pm_record()
        created = self.store.create(value)
        self.assertEqual(self.store.get(value['task_id']), created)
        self.assertEqual(created['pm_provenance']['usage_status'], 'UNKNOWN')
        self.assertEqual(created['backend_session_semantics'],
                         'ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL')

    def test_active_pm_provenance_rejects_false_or_incomplete_claims(self):
        mutations = (
            lambda record: record['pm_provenance'].__setitem__('provider_invoked', True),
            lambda record: record['pm_provenance'].__setitem__('session_restored', True),
            lambda record: record['pm_provenance'].__setitem__('automatic_callback', True),
            lambda record: record.__setitem__('usage', {
                'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}),
            lambda record: record.__setitem__('pm_assignment', {
                **record['pm_assignment'], 'risk': 'OTHER'}),
            lambda record: record.pop('pm_provenance'),
        )
        for mutate in mutations:
            value = self._active_pm_record()
            mutate(value)
            with self.subTest(mutate=mutate), self.assertRaises(LLMToolError):
                self.store.create(value)

    def test_legacy_task_shapes_remain_compatible_without_provenance(self):
        legacy = record()
        self.assertEqual(self.store.create(legacy)['schema_version'], 1)
        self.assertEqual(self.store.get(legacy['task_id'])['task_id'], legacy['task_id'])

        previous_v2 = record()
        previous_v2.update(
            schema_version=2,
            pm_route_binding={'type': 'codex-cli', 'model': 'gpt-6-luna',
                              'base_url': None},
            developer=None,
            pm_assignment=None,
            completion=None,
        )
        saved = self.store.create(previous_v2)
        self.assertEqual(self.store.get(previous_v2['task_id']), saved)
        self.assertNotIn('pm_provenance', saved)


if __name__ == '__main__':
    unittest.main()
