"""TaskStore card delivery binding (#1502 WP-F gate PR 2).

The optional per-assignment ``developer.card_delivery`` reservation is strictly
validated, preserved by every ordinary write, and moved from PREPARED to
DELIVERED once by ``commit_card_delivery()``. File-system faults are injected
with unittest.mock; a temporary store is used. Test data rule (Owner #763 A):
every value here is a fixed, non-secret test label. No Git, network or provider
process is used. Stdlib unittest only.
"""

import errno
import hashlib
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool.errors import LLMToolError
from llmtool import task_store as task_store_module
from llmtool import worker_dispatch
from llmtool.task_store import TaskStore, TaskStoreConflict, new_id
from llmtool.task_verification import AcceptanceCriterion, CriterionKind, TaskContract


def _task_record():
    task_id, run_id = new_id(), new_id()
    request = 'Bounded non-sensitive card delivery task.'
    request_sha = hashlib.sha256(request.encode()).hexdigest()
    contract = TaskContract(
        schema_version=1, task_id=task_id, contract_version=1,
        previous_digest=None, goal='PM intake request sha256:' + request_sha,
        authorized_scope=('pm-planning',), forbidden_scope=(),
        criteria=(AcceptanceCriterion('pm_response', CriterionKind.ARTIFACT,
                                      'Record the PM reply or unavailable status.'),),
    )
    record = {
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
    record.update(
        schema_version=2,
        pm_route_binding={'type': 'codex-cli', 'model': 'gpt-6-luna',
                          'base_url': None},
        developer=None, pm_assignment=None, completion=None,
    )
    return record


def _developer(provider='opencode', **changes):
    developer = {
        'assignment_id': new_id(), 'selection_source': 'PM',
        'state': 'DEVELOPER_PENDING', 'provider_profile': provider,
        'provider_type': None, 'model': None,
        'backend_session_semantics': 'NO_NATIVE_SESSION_ID_EXPOSED',
        'session_id': 'dev-' + new_id(), 'source_sha': 'a' * 40,
        'source_sha_binding': 'D6_REGISTRY_OBSERVED',
        'read_paths': ['src.py'], 'write_paths': ['src.py'],
        'acceptance': ['the card delivery is recorded'],
        'context_sha256': 'b' * 64,
        'egress_basis': 'Approved non-sensitive test context.', 'input_bytes': 10,
        'max_tokens': None, 'artifact_format': 'unified_diff',
        'artifact': None, 'artifact_sha256': None, 'usage': None,
        'error_code': None, 'created_at': '2026-10-23T00:00:00Z',
    }
    if provider == 'opencode':
        developer.update(backend_session_semantics='CLI_REPORTED_SESSION_ID',
                         max_tokens=128, execution=None)
    elif provider == 'gw-agent':
        developer.update(backend_session_semantics='CLI_REPORTED_SESSION_ID')
    developer.update(changes)
    return developer


def _prepared(developer, **changes):
    binding = {
        'route': developer['provider_profile'],
        'pin_commit': 'c' * 40,
        'prefix_sha256': 'd' * 64,
        'session_id': developer['session_id'],
        'prepared_id': new_id(),
        'state': 'PREPARED',
    }
    binding.update(changes)
    return dict(developer, card_delivery=binding)


def _exemption(developer, **changes):
    binding = {'route': 'gw-agent', 'state': 'NOT_DELIVERED'}
    binding.update(changes)
    return dict(developer, card_delivery=binding)


def _completion():
    return {
        'state': 'WAITING_EXECUTOR', 'executor_task_id': None,
        'reviewer_task_id': None, 'evidence': None, 'missing_gates': [],
        'pm_dispatch': 'NOT_STARTED', 'pm_response': None,
        'pm_recommendation': None, 'pm_usage': None, 'pm_error_code': None,
    }


def _opencode_failure():
    return {
        'status': 'UNKNOWN', 'native_session_id': None,
        'process_exit_code': 1, 'stdout_sha256': None, 'elapsed_ms': None,
        'cleanup_status': 'CONFIRMED', 'usage_source': 'UNKNOWN',
        'source_bytes': None, 'source_file_count': None,
        'source_manifest_sha256': None,
    }


def _code(error):
    return getattr(error, 'task_store_error_code', None)


class CardDeliveryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'tasks'
        self.store = TaskStore(self.root)
        # The dispatch gate is not the behaviour under test; refuse nothing.
        gate = patch.object(worker_dispatch, 'authorize_task_store_change',
                            return_value=None)
        gate.start()
        self.addCleanup(gate.stop)

    def _create(self):
        task = _task_record()
        self.store.create(task)
        return task['task_id']

    def _assign(self, developer):
        task_id = self._create()
        self.store.compare_and_update(task_id, expected={'developer': None},
                                      developer=developer)
        return task_id

    def _prepare(self, provider='opencode'):
        developer = _prepared(_developer(provider))
        task_id = self._assign(developer)
        return task_id, developer, developer['card_delivery']

    def _delivered(self, provider='opencode'):
        task_id, _developer_value, binding = self._prepare(provider)
        self.store.commit_card_delivery(task_id, binding['prepared_id'])
        return task_id, binding

    def _assert_unchanged(self, task_id, before, code, call):
        with self.assertRaises(LLMToolError) as caught:
            call()
        self.assertEqual(_code(caught.exception), code)
        self.assertEqual(self.store.get(task_id), before)

    def test_prepared_is_accepted_on_a_new_assignment_and_commit_moves_it_once(self):
        task_id, developer, binding = self._prepare()
        stored = self.store.get(task_id)
        self.assertEqual(stored['developer']['card_delivery'], binding)
        self.assertEqual(binding['state'], 'PREPARED')

        saved = self.store.commit_card_delivery(task_id, binding['prepared_id'])
        delivered = saved['developer']['card_delivery']
        self.assertEqual(delivered, dict(binding, state='DELIVERED'))
        self.assertEqual({key: value for key, value in saved['developer'].items()
                          if key != 'card_delivery'},
                         {key: value for key, value in developer.items()
                          if key != 'card_delivery'})
        self.assertEqual({key: value for key, value in saved.items()
                          if key not in ('developer', 'updated_at', 'record_sha256')},
                         {key: value for key, value in stored.items()
                          if key not in ('developer', 'updated_at', 'record_sha256')})
        self.assertEqual(self.store.get(task_id), saved)

        with self.assertRaises(TaskStoreConflict):
            self.store.commit_card_delivery(task_id, binding['prepared_id'])
        self.assertEqual(self.store.get(task_id), saved)

    def test_binding_shape_is_strict(self):
        base = _prepared(_developer('opencode'))
        binding = base['card_delivery']
        structural = (
            'not-a-binding',
            {key: value for key, value in binding.items() if key != 'prefix_sha256'},
            dict(binding, extra='value'),
            dict(binding, state='UNKNOWN'),
            dict(binding, route='codex'),
            dict(binding, pin_commit=123),
            dict(binding, pin_commit='z' * 40),
            dict(binding, pin_commit='C' * 40),
            dict(binding, prefix_sha256='x' * 64),
            dict(binding, session_id='dev-' + new_id()),
            dict(binding, prepared_id='not-a-reservation'),
        )
        for candidate in structural:
            with self.subTest(candidate=candidate):
                task_id = self._create()
                before = self.store.get(task_id)
                bad = dict(base, card_delivery=candidate)
                self._assert_unchanged(task_id, before, 'CARD_DELIVERY_INVALID',
                                       lambda: self.store.compare_and_update(
                                           task_id, expected={'developer': None},
                                           developer=bad))
        delivered = dict(base, card_delivery=dict(binding, state='DELIVERED'))
        task_id = self._create()
        before = self.store.get(task_id)
        self._assert_unchanged(task_id, before, 'CARD_DELIVERY_TRANSITION_INVALID',
                               lambda: self.store.compare_and_update(
                                   task_id, expected={'developer': None},
                                   developer=delivered))
        created = _task_record()
        created['developer'] = delivered
        with self.assertRaises(LLMToolError) as caught:
            self.store.create(created)
        self.assertEqual(_code(caught.exception),
                         'CARD_DELIVERY_TRANSITION_INVALID')
        self.assertNotIn(created['task_id'], self.store.task_ids())

    def test_gw_agent_exemption_is_accepted_and_kept(self):
        developer = _exemption(_developer('gw-agent'))
        task_id = self._assign(developer)
        stored = self.store.get(task_id)['developer']
        self.assertEqual(stored['card_delivery'],
                         {'route': 'gw-agent', 'state': 'NOT_DELIVERED'})
        changed = dict(stored, context_sha256='e' * 64)
        saved = self.store.update(task_id, developer=changed)
        self.assertEqual(saved['developer']['card_delivery'],
                         stored['card_delivery'])
        before = self.store.get(task_id)
        reservation = dict(stored, card_delivery={
            'route': 'gw-agent', 'pin_commit': 'c' * 40,
            'prefix_sha256': 'd' * 64, 'session_id': stored['session_id'],
            'prepared_id': new_id(), 'state': 'PREPARED'})
        self._assert_unchanged(task_id, before, 'CARD_DELIVERY_TRANSITION_INVALID',
                               lambda: self.store.update(
                                   task_id, developer=reservation))

    def test_ordinary_writes_keep_the_delivered_binding(self):
        task_id, binding = self._delivered()
        delivered = self.store.get(task_id)
        stale = dict(delivered['developer'],
                     card_delivery=dict(binding, state='PREPARED'))
        self._assert_unchanged(task_id, delivered, 'CARD_DELIVERY_TRANSITION_INVALID',
                               lambda: self.store.update(task_id, developer=stale))

        changed = json.loads(json.dumps(delivered['developer']))
        changed['card_delivery']['prefix_sha256'] = 'f' * 64
        self._assert_unchanged(task_id, delivered, 'CARD_DELIVERY_TRANSITION_INVALID',
                               lambda: self.store.update(task_id, developer=changed))

        removed = json.loads(json.dumps(delivered['developer']))
        removed.pop('card_delivery')
        self._assert_unchanged(task_id, delivered, 'CARD_DELIVERY_TRANSITION_INVALID',
                               lambda: self.store.update(task_id, developer=removed))

        result = dict(delivered['developer'], state='DEVELOPER_DISPATCHING',
                      provider_type='opencode-cli', model='opencode/test-free')
        saved = self.store.update(task_id, developer=result)
        self.assertEqual(saved['developer']['card_delivery'],
                         dict(binding, state='DELIVERED'))
        saved = self.store.update(task_id, completion=_completion())
        self.assertEqual(saved['developer']['card_delivery'],
                         dict(binding, state='DELIVERED'))

        failure = dict(saved['developer'], state='DEVELOPER_OUTCOME_UNKNOWN',
                       error_code='OPENCODE_EXECUTION_FAILED',
                       execution=_opencode_failure())
        saved = self.store.update(task_id, developer=failure)
        self.assertEqual(saved['developer']['card_delivery'],
                         dict(binding, state='DELIVERED'))

    def test_opencode_launch_write_keeps_the_binding(self):
        task_id, binding = self._delivered()
        stored = self.store.get(task_id)['developer']
        dispatching = dict(stored, state='DEVELOPER_DISPATCHING',
                           provider_type='opencode-cli',
                           model='opencode/test-free')
        self.store.update(task_id, developer=dispatching)
        saved = self.store.record_opencode_launch(
            task_id, assignment_id=stored['assignment_id'], pid=123,
            start_time='123456', pgid=123)
        self.assertEqual(saved['developer']['card_delivery'],
                         dict(binding, state='DELIVERED'))
        self.assertEqual(saved['developer']['execution']['child_pid'], 123)

    def test_commit_after_replace_is_uncertain_but_persisted(self):
        task_id, _developer_value, binding = self._prepare()
        before = self.store.get(task_id)
        real_fsync = task_store_module.os.fsync

        def fail_directory_fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError(errno.EIO, 'injected directory sync fault')
            return real_fsync(fd)

        with patch.object(task_store_module.os, 'fsync',
                          side_effect=fail_directory_fsync):
            with self.assertRaises(LLMToolError) as caught:
                self.store.commit_card_delivery(task_id, binding['prepared_id'])
        self.assertEqual(_code(caught.exception), 'TASK_STORE_UNAVAILABLE')

        fresh = TaskStore(self.root).get(task_id)
        self.assertEqual(fresh['developer']['card_delivery'],
                         dict(binding, state='DELIVERED'))
        with self.assertRaises(TaskStoreConflict):
            self.store.commit_card_delivery(task_id, binding['prepared_id'])
        self.assertEqual(self.store.get(task_id), fresh)

        stale = dict(before['developer'])
        self._assert_unchanged(task_id, fresh, 'CARD_DELIVERY_TRANSITION_INVALID',
                               lambda: self.store.update(task_id, developer=stale))

        failure = dict(fresh['developer'], state='DEVELOPER_OUTCOME_UNKNOWN',
                       provider_type='opencode-cli',
                       error_code='OPENCODE_EXECUTION_FAILED',
                       execution=_opencode_failure())
        saved = self.store.update(task_id, developer=failure)
        self.assertEqual(saved['developer']['card_delivery'],
                         dict(binding, state='DELIVERED'))

    def test_commit_before_replace_leaves_prepared(self):
        task_id, _developer_value, binding = self._prepare()
        before = self.store.get(task_id)
        with patch.object(task_store_module.os, 'replace',
                          side_effect=OSError(errno.EIO, 'injected replace fault')):
            with self.assertRaises(LLMToolError) as caught:
                self.store.commit_card_delivery(task_id, binding['prepared_id'])
        self.assertEqual(_code(caught.exception), 'TASK_STORE_UNAVAILABLE')
        self.assertEqual(TaskStore(self.root).get(task_id), before)
        self.assertEqual(self.store.get(task_id)['developer']['card_delivery']['state'],
                         'PREPARED')

        saved = self.store.commit_card_delivery(task_id, binding['prepared_id'])
        self.assertEqual(saved['developer']['card_delivery']['state'], 'DELIVERED')

    def test_legacy_record_without_the_binding_round_trips_unchanged(self):
        # A v1 record predates developer, pm_assignment, completion and the
        # optional card_delivery field entirely; create it as written.
        legacy = _task_record()
        legacy['schema_version'] = 1
        for field in ('developer', 'pm_assignment', 'completion', 'pm_route_binding'):
            legacy.pop(field)
        task_id = legacy['task_id']
        self.store.create(legacy)
        path = self.root / (task_id + '.json')
        before = path.read_bytes()
        loaded = self.store.get(task_id)
        self.assertNotIn('developer', loaded)
        task_store_module._validate(dict(loaded))
        self.assertEqual(path.read_bytes(), before)

        task_id = self._assign(_developer('gw-agent'))
        path = self.root / (task_id + '.json')
        before = path.read_bytes()
        loaded = self.store.get(task_id)
        self.assertNotIn('card_delivery', loaded['developer'])
        task_store_module._validate(dict(loaded))
        self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
