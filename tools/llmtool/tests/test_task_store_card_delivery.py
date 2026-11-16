"""Unit tests for TaskStore card_delivery schema, validation, and commit.

#1502 WP-F PR 2 (design section 2.7):
- Strict card_delivery check
- commit_card_delivery() compare-and-update from PREPARED to DELIVERED
- Binding preservation under the same assignment_id
- No downgrade after commit
- PREPARED and exemption shapes pass
- Uncertain claim commit (F2) directory fsync failure recovery
"""

import errno
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from llmtool import task_store
from llmtool.task_store import TaskStore, TaskStoreConflict
from llmtool.errors import LLMToolError
from llmtool.task_verification import AcceptanceCriterion, CriterionKind, TaskContract


def make_contract(task_id, run_id, request='Implement a small tested feature'):
    request_sha = hashlib.sha256(request.encode('utf-8')).hexdigest()
    contract = TaskContract(
        schema_version=1,
        task_id=task_id,
        contract_version=1,
        previous_digest=None,
        goal='PM intake request sha256:' + request_sha,
        authorized_scope=('pm-planning',),
        forbidden_scope=(),
        criteria=(
            AcceptanceCriterion(
                'pm_response',
                CriterionKind.ARTIFACT,
                'Record the PM reply or unavailable status.',
            ),
        ),
    )
    return contract.to_dict(), contract.binding(run_id).to_dict()


def make_task_record(task_id, run_id, *, developer=None, request='Implement a small tested feature'):
    request_sha = hashlib.sha256(request.encode('utf-8')).hexdigest()
    contract_raw, binding_dict = make_contract(task_id, run_id, request=request)
    return {
        'schema_version': 2,
        'task_id': task_id,
        'run_id': run_id,
        'pm_session_id': 'pm-test-session-1',
        'pm_provider_profile': 'pm',
        'pm_provider_type': 'codex-cli',
        'pm_model': 'gpt-6-luna',
        'backend_session_semantics': 'EPHEMERAL_SINGLE_CALL',
        'native_session_id': None,
        'state': 'PM_PENDING',
        'created_at': '2026-11-16T00:00:00Z',
        'updated_at': '2026-11-16T00:00:00Z',
        'task_contract': contract_raw,
        'task_binding': binding_dict,
        'request': request,
        'request_sha256': request_sha,
        'response': None,
        'usage': None,
        'error_code': None,
        'developer': developer,
        'pm_assignment': None,
        'record_sha256': None,
    }


def make_card_delivery(
    *,
    route='opencode',
    pin_commit='a' * 40,
    prefix_sha256='b' * 64,
    session_id='dev-test-session-1',
    prepared_id='c' * 32,
    state='PREPARED',
):
    return {
        'route': route,
        'pin_commit': pin_commit,
        'prefix_sha256': prefix_sha256,
        'session_id': session_id,
        'prepared_id': prepared_id,
        'state': state,
    }


def make_developer(
    assignment_id='d' * 32,
    *,
    provider_profile='opencode',
    provider_type='opencode-cli',
    session_id='dev-test-session-1',
    state='DEVELOPER_DISPATCHING',
    card_delivery=None,
    execution=None,
    model=None,
    max_tokens=1000,
    owner_assignment_ref=None,
    artifact=None,
    artifact_sha256=None,
    error_code=None,
):
    dev = {
        'assignment_id': assignment_id,
        'selection_source': 'PM',
        'state': state,
        'provider_profile': provider_profile,
        'provider_type': provider_type,
        'model': model,
        'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
        'session_id': session_id,
        'source_sha': '1' * 40,
        'source_sha_binding': 'D6_REGISTRY_OBSERVED',
        'read_paths': ['tools/llmtool/llmtool/task_store.py'],
        'write_paths': ['tools/llmtool/llmtool/task_store.py'],
        'acceptance': ['acceptance criterion 1'],
        'context_sha256': '2' * 64,
        'egress_basis': 'safe non-sensitive egress basis',
        'input_bytes': 100,
        'max_tokens': max_tokens,
        'artifact_format': 'unified_diff',
        'artifact': artifact,
        'artifact_sha256': artifact_sha256,
        'usage': None,
        'error_code': error_code,
        'created_at': '2026-11-16T00:00:00Z',
        'execution': execution,
    }
    if provider_profile in ('claude', 'agy'):
        dev['owner_assignment_ref'] = owner_assignment_ref
    if provider_profile == 'claude':
        dev['executor_task_id'] = 'd6-exec-test-1'
        dev['direct_result_callback'] = {'executor_task_id': 'd6-exec-test-1'}
        dev['max_tokens'] = None
    elif provider_profile in ('agy', 'gw-agent', 'codex'):
        dev['max_tokens'] = None
    if card_delivery is not None:
        dev['card_delivery'] = card_delivery
    return dev


import review_trust_fixture
import worker_policy_fixture


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


class TestTaskStoreCardDelivery(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'tasks'
        self.store = TaskStore(self.root)
        self.policy_path = worker_policy_fixture.install(self)

    def test_card_delivery_prepared_and_exemption_shapes_pass(self):
        # OpenCode PREPARED
        cd_opencode = make_card_delivery(route='opencode')
        dev_opencode = make_developer(
            '1' * 32, provider_profile='opencode', provider_type='opencode-cli',
            card_delivery=cd_opencode
        )
        task_1 = make_task_record('a' * 32, 'b' * 32, developer=dev_opencode)
        saved_1 = self.store.create(task_1)
        self.assertEqual(saved_1['developer']['card_delivery']['state'], 'PREPARED')
        self.assertEqual(saved_1['developer']['card_delivery']['route'], 'opencode')
        fetched_1 = self.store.get('a' * 32)
        self.assertEqual(fetched_1['developer']['card_delivery'], cd_opencode)

        # Claude PREPARED
        cd_claude = make_card_delivery(route='claude')
        dev_claude = make_developer(
            '2' * 32, provider_profile='claude', provider_type='claude-cli',
            card_delivery=cd_claude
        )
        task_2 = make_task_record('c' * 32, 'd' * 32, developer=dev_claude)
        saved_2 = self.store.create(task_2)
        self.assertEqual(saved_2['developer']['card_delivery']['state'], 'PREPARED')
        self.assertEqual(saved_2['developer']['card_delivery']['route'], 'claude')

        # AGY PREPARED
        cd_agy = make_card_delivery(route='agy')
        dev_agy = make_developer(
            '3' * 32, provider_profile='agy', provider_type='agy-cli',
            card_delivery=cd_agy
        )
        task_3 = make_task_record('e' * 32, 'f' * 32, developer=dev_agy)
        saved_3 = self.store.create(task_3)
        self.assertEqual(saved_3['developer']['card_delivery']['state'], 'PREPARED')
        self.assertEqual(saved_3['developer']['card_delivery']['route'], 'agy')

        # gw-agent exemption shape
        cd_exempt = {'route': 'gw-agent', 'state': 'NOT_DELIVERED'}
        dev_exempt = make_developer(
            '4' * 32, provider_profile='gw-agent', provider_type='gw-agent-cli',
            card_delivery=cd_exempt
        )
        task_4 = make_task_record('1' * 32, '2' * 32, developer=dev_exempt)
        saved_4 = self.store.create(task_4)
        self.assertEqual(saved_4['developer']['card_delivery'], cd_exempt)
        fetched_4 = self.store.get('1' * 32)
        self.assertEqual(fetched_4['developer']['card_delivery'], cd_exempt)

    def test_legacy_record_round_trips_unchanged(self):
        dev = make_developer('1' * 32, provider_profile='opencode', card_delivery=None)
        task = make_task_record('a' * 32, 'b' * 32, developer=dev)
        saved = self.store.create(task)
        self.assertNotIn('card_delivery', saved['developer'])

        fetched = self.store.get('a' * 32)
        self.assertNotIn('card_delivery', fetched['developer'])

        # Update without card_delivery succeeds
        updated_dev = dict(dev, timeout_seconds=120)
        updated = self.store.compare_and_update(
            'a' * 32,
            expected={'record_sha256': fetched['record_sha256']},
            developer=updated_dev,
        )
        self.assertNotIn('card_delivery', updated['developer'])
        self.assertEqual(updated['developer']['timeout_seconds'], 120)

    def test_refused_unknown_key(self):
        cd = make_card_delivery()
        cd['unknown_extra_field'] = 'invalid'
        dev = make_developer('1' * 32, card_delivery=cd)
        task = make_task_record('a' * 32, 'b' * 32, developer=dev)
        with self.assertRaises(LLMToolError):
            self.store.create(task)

        # Unknown key on exemption shape
        cd_exempt = {'route': 'gw-agent', 'state': 'NOT_DELIVERED', 'unknown': 'field'}
        dev_exempt = make_developer('2' * 32, provider_profile='gw-agent',
                                    provider_type='gw-agent-cli', card_delivery=cd_exempt)
        task_exempt = make_task_record('c' * 32, 'd' * 32, developer=dev_exempt)
        with self.assertRaises(LLMToolError):
            self.store.create(task_exempt)

    def test_refused_wrong_type(self):
        # card_delivery not a dict
        for bad_val in ('PREPARED', 12345, ['list'], True):
            dev = make_developer('1' * 32, card_delivery=bad_val)
            task = make_task_record('a' * 32, 'b' * 32, developer=dev)
            with self.assertRaises(LLMToolError):
                self.store.create(task)

        # pin_commit wrong type
        cd = make_card_delivery(pin_commit=12345)
        dev = make_developer('1' * 32, card_delivery=cd)
        with self.assertRaises(LLMToolError):
            self.store.create(make_task_record('a' * 32, 'b' * 32, developer=dev))

        # prefix_sha256 wrong type
        cd = make_card_delivery(prefix_sha256=12345)
        dev = make_developer('1' * 32, card_delivery=cd)
        with self.assertRaises(LLMToolError):
            self.store.create(make_task_record('a' * 32, 'b' * 32, developer=dev))

        # prepared_id wrong type
        cd = make_card_delivery(prepared_id=12345)
        dev = make_developer('1' * 32, card_delivery=cd)
        with self.assertRaises(LLMToolError):
            self.store.create(make_task_record('a' * 32, 'b' * 32, developer=dev))

        # route mismatch with profile
        cd = make_card_delivery(route='claude')
        dev = make_developer('1' * 32, provider_profile='opencode', card_delivery=cd)
        with self.assertRaises(LLMToolError):
            self.store.create(make_task_record('a' * 32, 'b' * 32, developer=dev))

        # invalid state
        cd = make_card_delivery(state='READY')
        dev = make_developer('1' * 32, card_delivery=cd)
        with self.assertRaises(LLMToolError):
            self.store.create(make_task_record('a' * 32, 'b' * 32, developer=dev))

        # exemption shape on non-gw-agent
        cd_exempt = {'route': 'gw-agent', 'state': 'NOT_DELIVERED'}
        dev_non_gw = make_developer('1' * 32, provider_profile='opencode', card_delivery=cd_exempt)
        with self.assertRaises(LLMToolError):
            self.store.create(make_task_record('a' * 32, 'b' * 32, developer=dev_non_gw))

        # standard shape on gw-agent
        cd_std = make_card_delivery(route='gw-agent')
        dev_gw = make_developer('1' * 32, provider_profile='gw-agent',
                                provider_type='gw-agent-cli', card_delivery=cd_std)
        with self.assertRaises(LLMToolError):
            self.store.create(make_task_record('a' * 32, 'b' * 32, developer=dev_gw))

        # card_delivery on codex
        cd_codex = make_card_delivery(route='codex')
        dev_codex = make_developer('1' * 32, provider_profile='codex',
                                   provider_type='codex-cli', card_delivery=cd_codex)
        with self.assertRaises(LLMToolError):
            self.store.create(make_task_record('a' * 32, 'b' * 32, developer=dev_codex))

    def test_refused_non_hex_digest_or_pin(self):
        # non-hex digest
        cd = make_card_delivery(prefix_sha256='g' * 64)
        dev = make_developer('1' * 32, card_delivery=cd)
        with self.assertRaises(LLMToolError):
            self.store.create(make_task_record('a' * 32, 'b' * 32, developer=dev))

        # short digest
        cd = make_card_delivery(prefix_sha256='b' * 63)
        dev = make_developer('1' * 32, card_delivery=cd)
        with self.assertRaises(LLMToolError):
            self.store.create(make_task_record('a' * 32, 'b' * 32, developer=dev))

        # non-hex pin
        cd = make_card_delivery(pin_commit='g' * 40)
        dev = make_developer('1' * 32, card_delivery=cd)
        with self.assertRaises(LLMToolError):
            self.store.create(make_task_record('a' * 32, 'b' * 32, developer=dev))

        # short pin
        cd = make_card_delivery(pin_commit='a' * 39)
        dev = make_developer('1' * 32, card_delivery=cd)
        with self.assertRaises(LLMToolError):
            self.store.create(make_task_record('a' * 32, 'b' * 32, developer=dev))

        # non-hex prepared_id
        cd = make_card_delivery(prepared_id='g' * 32)
        dev = make_developer('1' * 32, card_delivery=cd)
        with self.assertRaises(LLMToolError):
            self.store.create(make_task_record('a' * 32, 'b' * 32, developer=dev))

    def test_refused_session_id_mismatch(self):
        cd = make_card_delivery(session_id='dev-other-session')
        dev = make_developer('1' * 32, session_id='dev-test-session-1', card_delivery=cd)
        task = make_task_record('a' * 32, 'b' * 32, developer=dev)
        with self.assertRaises(LLMToolError):
            self.store.create(task)

    def test_refused_delivered_on_new_assignment_or_plain_update(self):
        # DELIVERED on create (new assignment)
        cd_delivered = make_card_delivery(state='DELIVERED')
        dev_delivered = make_developer('1' * 32, card_delivery=cd_delivered)
        task = make_task_record('a' * 32, 'b' * 32, developer=dev_delivered)
        with self.assertRaises(LLMToolError):
            self.store.create(task)

        # DELIVERED on assignment transition from developer: None
        task_no_dev = make_task_record('c' * 32, 'd' * 32, developer=None)
        created = self.store.create(task_no_dev)
        with self.assertRaises(LLMToolError):
            self.store.compare_and_update(
                'c' * 32,
                expected={'record_sha256': created['record_sha256']},
                developer=dev_delivered,
            )

        # DELIVERED on new assignment_id
        cd_prepared = make_card_delivery(state='PREPARED')
        dev_init = make_developer('1' * 32, card_delivery=cd_prepared)
        created_init = self.store.create(make_task_record('e' * 32, 'f' * 32, developer=dev_init))
        dev_new_assign = make_developer('2' * 32, card_delivery=cd_delivered)
        with self.assertRaises(LLMToolError):
            self.store.compare_and_update(
                'e' * 32,
                expected={'record_sha256': created_init['record_sha256']},
                developer=dev_new_assign,
            )

        # DELIVERED on plain update under the same assignment_id
        dev_same_assign = make_developer('1' * 32, card_delivery=cd_delivered)
        with self.assertRaises(LLMToolError):
            self.store.compare_and_update(
                'e' * 32,
                expected={'record_sha256': created_init['record_sha256']},
                developer=dev_same_assign,
            )

    def test_refused_binding_change_under_same_assignment(self):
        cd = make_card_delivery(state='PREPARED')
        dev = make_developer('1' * 32, card_delivery=cd)
        created = self.store.create(make_task_record('a' * 32, 'b' * 32, developer=dev))

        # Changed pin_commit
        dev_bad_pin = make_developer('1' * 32, card_delivery=dict(cd, pin_commit='f' * 40))
        with self.assertRaises(LLMToolError):
            self.store.compare_and_update(
                'a' * 32,
                expected={'record_sha256': created['record_sha256']},
                developer=dev_bad_pin,
            )

        # Changed prefix_sha256
        dev_bad_digest = make_developer('1' * 32, card_delivery=dict(cd, prefix_sha256='f' * 64))
        with self.assertRaises(LLMToolError):
            self.store.compare_and_update(
                'a' * 32,
                expected={'record_sha256': created['record_sha256']},
                developer=dev_bad_digest,
            )

        # Changed prepared_id
        dev_bad_prep = make_developer('1' * 32, card_delivery=dict(cd, prepared_id='f' * 32))
        with self.assertRaises(LLMToolError):
            self.store.compare_and_update(
                'a' * 32,
                expected={'record_sha256': created['record_sha256']},
                developer=dev_bad_prep,
            )

        # Removed card_delivery under same assignment
        dev_no_cd = make_developer('1' * 32, card_delivery=None)
        with self.assertRaises(LLMToolError):
            self.store.compare_and_update(
                'a' * 32,
                expected={'record_sha256': created['record_sha256']},
                developer=dev_no_cd,
            )

        # Added card_delivery when initially absent under same assignment
        task_legacy = make_task_record('c' * 32, 'd' * 32, developer=dev_no_cd)
        created_legacy = self.store.create(task_legacy)
        dev_with_cd = make_developer('1' * 32, card_delivery=cd)
        with self.assertRaises(LLMToolError):
            self.store.compare_and_update(
                'c' * 32,
                expected={'record_sha256': created_legacy['record_sha256']},
                developer=dev_with_cd,
            )

    def test_commit_card_delivery_moves_prepared_to_delivered(self):
        cd = make_card_delivery(state='PREPARED', prepared_id='c' * 32)
        dev = make_developer('1' * 32, card_delivery=cd)
        self.store.create(make_task_record('a' * 32, 'b' * 32, developer=dev))

        # Commit with matching prepared_id succeeds
        committed = self.store.commit_card_delivery('a' * 32, prepared_id='c' * 32)
        self.assertEqual(committed['developer']['card_delivery']['state'], 'DELIVERED')
        self.assertEqual(committed['developer']['card_delivery']['prepared_id'], 'c' * 32)

        fetched = self.store.get('a' * 32)
        self.assertEqual(fetched['developer']['card_delivery']['state'], 'DELIVERED')

        # Repeat commit conflicts
        with self.assertRaises(TaskStoreConflict):
            self.store.commit_card_delivery('a' * 32, prepared_id='c' * 32)

        # Commit non-existent task raises not found
        with self.assertRaises(task_store._TaskNotFound):
            self.store.commit_card_delivery('f' * 32)

        # Commit with mismatched prepared_id conflicts
        task_2 = make_task_record('1' * 32, '2' * 32, developer=dev)
        self.store.create(task_2)
        with self.assertRaises(TaskStoreConflict):
            self.store.commit_card_delivery('1' * 32, prepared_id='9' * 32)

    def test_refused_stale_prepared_after_commit(self):
        cd = make_card_delivery(state='PREPARED')
        dev = make_developer('1' * 32, card_delivery=cd)
        self.store.create(make_task_record('a' * 32, 'b' * 32, developer=dev))

        self.store.commit_card_delivery('a' * 32)
        fresh = self.store.get('a' * 32)
        self.assertEqual(fresh['developer']['card_delivery']['state'], 'DELIVERED')

        # Attempt to write stale PREPARED under the same assignment
        stale_dev = dict(dev, timeout_seconds=60, card_delivery=cd)
        with self.assertRaises(LLMToolError):
            self.store.compare_and_update(
                'a' * 32,
                expected={'record_sha256': fresh['record_sha256']},
                developer=stale_dev,
            )

    def test_commit_then_result_and_completion_updates_pass_with_delivered_kept(self):
        cd = make_card_delivery(state='PREPARED')
        dev = make_developer('1' * 32, card_delivery=cd)
        self.store.create(make_task_record('a' * 32, 'b' * 32, developer=dev))

        self.store.commit_card_delivery('a' * 32)
        after_commit = self.store.get('a' * 32)
        delivered_cd = after_commit['developer']['card_delivery']
        self.assertEqual(delivered_cd['state'], 'DELIVERED')

        # Result update: developer responded with unified diff
        diff_text = 'diff --git a/file b/file\n--- a/file\n+++ b/file\n@@ -1 +1 @@\n-old\n+new\n'
        diff_sha = task_store.hashlib.sha256(diff_text.encode('utf-8')).hexdigest()
        execution = {
            'status': 'SUCCEEDED',
            'native_session_id': 'ses_12345678',
            'process_exit_code': 0,
            'stdout_sha256': '4' * 64,
            'elapsed_ms': 500,
            'cleanup_status': 'CONFIRMED',
            'usage_source': 'UNKNOWN',
            'source_bytes': 100,
            'source_file_count': 1,
            'source_manifest_sha256': '5' * 64,
        }
        responded_dev = make_developer(
            '1' * 32,
            state='DEVELOPER_RESPONDED',
            card_delivery=delivered_cd,
            artifact=diff_text,
            artifact_sha256=diff_sha,
            execution=execution,
        )
        result_updated = self.store.compare_and_update(
            'a' * 32,
            expected={'record_sha256': after_commit['record_sha256']},
            developer=responded_dev,
        )
        self.assertEqual(result_updated['developer']['card_delivery']['state'], 'DELIVERED')
        self.assertEqual(result_updated['developer']['state'], 'DEVELOPER_RESPONDED')

        # Completion update: completion record added, developer stays untouched
        completion_record = {
            'state': 'WAITING_EXECUTOR',
            'executor_task_id': 'd6-exec-test-1',
            'reviewer_task_id': None,
            'evidence': None,
            'missing_gates': [],
            'pm_dispatch': 'NOT_STARTED',
            'pm_response': None,
            'pm_recommendation': None,
            'pm_usage': None,
            'pm_error_code': None,
        }
        completion_updated = self.store.compare_and_update(
            'a' * 32,
            expected={'record_sha256': result_updated['record_sha256']},
            completion=completion_record,
        )
        self.assertEqual(completion_updated['developer']['card_delivery']['state'], 'DELIVERED')
        self.assertEqual(completion_updated['completion']['state'], 'WAITING_EXECUTOR')

        final_get = self.store.get('a' * 32)
        self.assertEqual(final_get['developer']['card_delivery']['state'], 'DELIVERED')
        self.assertEqual(final_get['completion']['state'], 'WAITING_EXECUTOR')

    def test_record_opencode_launch_keeps_binding(self):
        cd = make_card_delivery(route='opencode', state='PREPARED')
        dev = make_developer('1' * 32, provider_profile='opencode', card_delivery=cd)
        self.store.create(make_task_record('a' * 32, 'b' * 32, developer=dev))

        self.store.record_opencode_launch(
            'a' * 32,
            assignment_id='1' * 32,
            pid=54321,
            start_time='2026-11-16 00:00:00',
            pgid=54321,
        )
        launched = self.store.get('a' * 32)
        self.assertEqual(launched['developer']['card_delivery'], cd)
        self.assertEqual(launched['developer']['execution']['status'], 'RUNNING')
        self.assertEqual(launched['developer']['execution']['child_pid'], 54321)

        # Now commit moves PREPARED to DELIVERED
        committed = self.store.commit_card_delivery('a' * 32)
        self.assertEqual(committed['developer']['card_delivery']['state'], 'DELIVERED')
        self.assertEqual(committed['developer']['execution']['status'], 'RUNNING')

    def test_commit_raises_before_replace_leaves_prepared(self):
        cd = make_card_delivery(state='PREPARED')
        dev = make_developer('1' * 32, card_delivery=cd)
        self.store.create(make_task_record('a' * 32, 'b' * 32, developer=dev))

        real_open = os.open
        def fake_open(path, flags, *args, **kwargs):
            if isinstance(path, str) and path.endswith('.tmp'):
                raise OSError(errno.EIO, 'simulated temporary file open failure')
            return real_open(path, flags, *args, **kwargs)

        with mock.patch('os.open', side_effect=fake_open):
            with self.assertRaises(LLMToolError) as ctx:
                self.store.commit_card_delivery('a' * 32)
            self.assertEqual(ctx.exception.task_store_error_code, 'TASK_STORE_UNAVAILABLE')

        # Record on disk was never replaced: remains PREPARED
        fetched = self.store.get('a' * 32)
        self.assertEqual(fetched['developer']['card_delivery']['state'], 'PREPARED')

    def test_f2_uncertain_commit_fsync_failure(self):
        """F2 test: monkeypatch directory os.fsync after replace to raise OSError.

        - commit raises TASK_STORE_UNAVAILABLE;
        - fresh get() shows DELIVERED;
        - second commit conflicts and writes nothing;
        - stale PREPARED refused;
        - failure-state write from the re-read developer passes with DELIVERED kept.
        """
        cd = make_card_delivery(state='PREPARED', prepared_id='c' * 32)
        dev = make_developer('1' * 32, card_delivery=cd)
        self.store.create(make_task_record('a' * 32, 'b' * 32, developer=dev))

        replace_called = False
        real_replace = TaskStore._replace_at

        def fake_replace(directory, temporary, name):
            nonlocal replace_called
            real_replace(directory, temporary, name)
            replace_called = True

        real_fsync = os.fsync

        def fake_fsync(fd):
            if replace_called:
                raise OSError(errno.EIO, 'simulated directory fsync failure after replace')
            real_fsync(fd)

        with mock.patch.object(TaskStore, '_replace_at', side_effect=fake_replace), \
             mock.patch('os.fsync', side_effect=fake_fsync):
            with self.assertRaises(LLMToolError) as ctx:
                self.store.commit_card_delivery('a' * 32)
            self.assertEqual(ctx.exception.task_store_error_code, 'TASK_STORE_UNAVAILABLE')

        # Fresh get() shows DELIVERED
        fresh = self.store.get('a' * 32)
        self.assertEqual(fresh['developer']['card_delivery']['state'], 'DELIVERED')
        self.assertEqual(fresh['developer']['card_delivery']['prepared_id'], 'c' * 32)

        # Second commit conflicts and writes nothing
        with self.assertRaises(TaskStoreConflict):
            self.store.commit_card_delivery('a' * 32)

        # Stale PREPARED refused
        stale_dev = dict(fresh['developer'], card_delivery=cd)
        with self.assertRaises(LLMToolError):
            self.store.compare_and_update(
                'a' * 32,
                expected={'record_sha256': fresh['record_sha256']},
                developer=stale_dev,
            )

        # Failure-state write from the re-read developer passes with DELIVERED kept
        re_read_dev = self.store.get('a' * 32)['developer']
        failure_dev = dict(
            re_read_dev,
            state='BLOCKED',
            error_code='SKILL_DELIVERY_COMMIT_UNCERTAIN',
        )
        updated = self.store.compare_and_update(
            'a' * 32,
            expected={'record_sha256': self.store.get('a' * 32)['record_sha256']},
            developer=failure_dev,
        )
        self.assertEqual(updated['developer']['card_delivery']['state'], 'DELIVERED')
        self.assertEqual(updated['developer']['state'], 'BLOCKED')
        self.assertEqual(updated['developer']['error_code'], 'SKILL_DELIVERY_COMMIT_UNCERTAIN')

        # Verified in store
        final = self.store.get('a' * 32)
        self.assertEqual(final['developer']['card_delivery']['state'], 'DELIVERED')
        self.assertEqual(final['developer']['state'], 'BLOCKED')
        self.assertEqual(final['developer']['error_code'], 'SKILL_DELIVERY_COMMIT_UNCERTAIN')


if __name__ == '__main__':
    unittest.main()
