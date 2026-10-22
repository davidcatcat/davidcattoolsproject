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
from llmtool.task_store import TaskStore, new_id
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
