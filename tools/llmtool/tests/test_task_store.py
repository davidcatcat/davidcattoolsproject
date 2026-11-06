import fcntl
import hashlib
import io
import json
import os
import errno
import select
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool.action_contract import TaskBinding
from llmtool.errors import LLMToolError
from llmtool import worker_dispatch
from llmtool import task_submission_cli as task_submission_cli_module
from llmtool import codex_direct_developer
from llmtool.task_store import TaskStore, TaskStoreConflict, new_id
from llmtool import task_store as task_store_module
from llmtool.task_submission_cli import _record, _task_view
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


import review_trust_fixture


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'tasks'
        self.store = TaskStore(self.root)

    def test_merge_attempt_unknown_can_be_reclaimed_for_same_pr_and_head(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        claimed = {'pr': 17, 'head_sha': 'a' * 40, 'state': 'CLAIMED',
                   'claimed_at': '2026-11-06T00:00:00Z'}
        current = self.store.get(task['task_id'])
        self.store.compare_and_update(task['task_id'],
            expected={'record_sha256': current['record_sha256']}, merge_attempt=claimed)
        unknown = dict(claimed, state='UNKNOWN')
        current = self.store.get(task['task_id'])
        self.store.compare_and_update(task['task_id'],
            expected={'record_sha256': current['record_sha256']}, merge_attempt=unknown)
        retry = dict(claimed, claimed_at='2026-11-06T00:00:00.000001Z')
        current = self.store.get(task['task_id'])
        saved = self.store.compare_and_update(task['task_id'],
            expected={'record_sha256': current['record_sha256'], 'merge_attempt': unknown},
            merge_attempt=retry)
        self.assertEqual(saved['merge_attempt'], retry)

    def test_merge_attempt_transition_refusals_remain_strict(self):
        cases = (
            ('MERGED to CLAIMED', 'MERGED', 17, 'a' * 40,
             {'pr': 17, 'head_sha': 'a' * 40, 'state': 'CLAIMED',
              'claimed_at': '2026-11-06T00:00:01Z'}),
            ('CLAIMED to CLAIMED', 'CLAIMED', 17, 'a' * 40,
             {'pr': 17, 'head_sha': 'a' * 40, 'state': 'CLAIMED',
              'claimed_at': '2026-11-06T00:00:01Z'}),
            ('UNKNOWN to another PR', 'UNKNOWN', 17, 'a' * 40,
             {'pr': 18, 'head_sha': 'a' * 40, 'state': 'CLAIMED',
              'claimed_at': '2026-11-06T00:00:01Z'}),
            ('UNKNOWN to another head', 'UNKNOWN', 17, 'a' * 40,
             {'pr': 17, 'head_sha': 'b' * 40, 'state': 'CLAIMED',
              'claimed_at': '2026-11-06T00:00:01Z'}),
            ('UNKNOWN without a new claim time', 'UNKNOWN', 17, 'a' * 40,
             {'pr': 17, 'head_sha': 'a' * 40, 'state': 'CLAIMED',
              'claimed_at': '2026-11-06T00:00:00Z'}),
        )
        for label, old_state, pr, head, new_attempt in cases:
            with self.subTest(label=label):
                task = self._codex_dispatch_task()
                self.store.create(task)
                old = {'pr': pr, 'head_sha': head, 'state': 'CLAIMED',
                       'claimed_at': '2026-11-06T00:00:00Z'}
                current = self.store.get(task['task_id'])
                self.store.compare_and_update(task['task_id'],
                    expected={'record_sha256': current['record_sha256']}, merge_attempt=old)
                if old_state != 'CLAIMED':
                    old = dict(old, state=old_state)
                    current = self.store.get(task['task_id'])
                    self.store.compare_and_update(task['task_id'],
                        expected={'record_sha256': current['record_sha256']}, merge_attempt=old)
                current = self.store.get(task['task_id'])
                with self.assertRaises(LLMToolError) as caught:
                    self.store.compare_and_update(task['task_id'],
                        expected={'record_sha256': current['record_sha256']},
                        merge_attempt=new_attempt)
                self.assertEqual(caught.exception.task_store_error_code,
                                 'TASK_MERGE_ATTEMPT_TRANSITION_INVALID')

    def test_claimed_merge_attempt_still_allows_unknown_and_merged(self):
        for next_state in ('UNKNOWN', 'MERGED'):
            with self.subTest(next_state=next_state):
                task = self._codex_dispatch_task()
                self.store.create(task)
                claim = {'pr': 17, 'head_sha': 'a' * 40, 'state': 'CLAIMED',
                         'claimed_at': '2026-11-06T00:00:00Z'}
                current = self.store.get(task['task_id'])
                self.store.compare_and_update(task['task_id'],
                    expected={'record_sha256': current['record_sha256']}, merge_attempt=claim)
                updated = dict(claim, state=next_state)
                current = self.store.get(task['task_id'])
                saved = self.store.compare_and_update(task['task_id'],
                    expected={'record_sha256': current['record_sha256']},
                    merge_attempt=updated)
                self.assertEqual(saved['merge_attempt'], updated)

    def test_high_debug_entries_are_append_only(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        entry = {
            'family': 'test-family', 'failed_corrections': 2, 'early_reason': None,
            'examined_sha': 'a' * 40, 'd6': 'executor-hd1-aaaaaaa',
            'model_requested': 'gpt-6.1-sol', 'model_observed': 'UNKNOWN',
            'effort_observed': 'UNKNOWN', 'status': 'REPORT_INVALID',
            'report_sha256': hashlib.sha256(b'').hexdigest(), 'report': '', 'usage': 'UNKNOWN',
            'recorded_at': '2026-10-27T00:00:00Z',
        }
        saved = self.store.append_high_debug(task['task_id'], entry)
        changed = dict(entry, status='RCA_CONFIRMED')
        with self.assertRaises(LLMToolError):
            self.store.update(task['task_id'], high_debug=[changed])
        self.assertEqual(self.store.get(task['task_id'])['high_debug'], [entry])

    def test_repair_cap_override_validates_and_records_optional_shape(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        saved = self.store.record_repair_cap_override(
            task['task_id'],
            ref='https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-123',
            rounds=3, recorded_at='2026-11-04T00:00:00Z')
        self.assertEqual(saved['repair_cap_override'], {
            'ref': 'https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-123',
            'rounds': 3, 'recorded_at': '2026-11-04T00:00:00Z'})

    def test_repair_cap_override_rejects_bad_shapes(self):
        cases = (
            {'ref': 'http://github.com/owner/repo/issues/1/comments/2',
             'rounds': 3, 'recorded_at': '2026-11-04T00:00:00Z'},
            {'ref': 'https://github.com/owner/repo/issues/1',
             'rounds': 3, 'recorded_at': '2026-11-04T00:00:00Z'},
            {'ref': 'https://github.com/owner/repo/issues/1/comments/2',
             'rounds': 2, 'recorded_at': '2026-11-04T00:00:00Z'},
            {'ref': 'https://github.com/owner/repo/issues/1/comments/2',
             'rounds': 3, 'recorded_at': 'invalid'},
            {'ref': 'https://github.com/owner/repo/issues/1/comments/2',
             'rounds': True, 'recorded_at': '2026-11-04T00:00:00Z'},
        )
        for override in cases:
            with self.subTest(override=override):
                task = self._codex_dispatch_task()
                self.store.create(task)
                with self.assertRaises(LLMToolError) as caught:
                    self.store.update(task['task_id'], repair_cap_override=override)
                self.assertEqual(caught.exception.task_store_error_code,
                                 'TASK_REPAIR_CAP_OVERRIDE_INVALID')

    def test_repair_cap_override_is_optional_for_existing_records(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        self.assertNotIn('repair_cap_override', self.store.get(task['task_id']))

    def test_run_failed_high_debug_entries_from_high_debug_are_valid(self):
        from llmtool import high_debug
        task = self._codex_dispatch_task()
        self.store.create(task)
        reasons = ({'failed_corrections': 2, 'early_reason': None},
                   {'failed_corrections': None, 'early_reason': 'early'})
        codes = ('HIGH_DEBUG_RUN_FAILED', 'HIGH_DEBUG_WROTE_FILES',
                 'HIGH_DEBUG_REPORT_INVALID')
        expected = []
        for index, (reason, code) in enumerate(
                [(reason, code) for reason in reasons for code in codes], 1):
            entry = high_debug._failed_entry(
                family='test-family', head='a' * 40, d6=f'executor-hd{index}-aaaaaaa',
                code=code, observed='UNKNOWN', effort='UNKNOWN',
                usage={'input': 1, 'cached': 0, 'output': 1, 'reasoning': 0},
                recorded_at='2026-10-27T00:00:00Z', **reason)
            self.store.append_high_debug(task['task_id'], entry)
            expected.append(entry)
        self.assertEqual(self.store.get(task['task_id'])['high_debug'], expected)
        with self.assertRaises(LLMToolError):
            self.store.append_high_debug(task['task_id'], dict(expected[0], status='FAILED'))

    def test_review_repair_claim_can_fail_only_after_dispatch(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        claim = {
            'executor_task': 'executor-1', 'pr': 17, 'head_sha': 'a' * 40,
            'version': 1, 'attempt': 1, 'family': 'pr-17', 'state': 'CLAIMED',
            'repair_task_id': new_id(), 'comment_id': 9, 'packet_sha256': 'b' * 64,
            'claimed_at': '2026-10-27T00:00:00Z',
        }
        saved = self.store.get(task['task_id'])
        self.store.compare_and_update(task['task_id'],
            expected={'record_sha256': saved['record_sha256']},
            review_repair_claims=[claim])
        claimed_record = self.store.get(task['task_id'])
        with self.assertRaises(LLMToolError) as caught:
            self.store.compare_and_update(task['task_id'],
                expected={'record_sha256': claimed_record['record_sha256']},
                review_repair_claims=[dict(claim, state='FAILED')])
        self.assertEqual(caught.exception.task_store_error_code,
                         'REVIEW_REPAIR_CLAIM_TRANSITION_INVALID')
        self.assertEqual(self.store.get(task['task_id']), claimed_record)
        dispatched = dict(claim, state='DISPATCHED')
        self.store.compare_and_update(task['task_id'],
            expected={'record_sha256': claimed_record['record_sha256']},
            review_repair_claims=[dispatched])
        failed = dict(dispatched, state='FAILED')
        dispatched_record = self.store.get(task['task_id'])
        saved = self.store.compare_and_update(task['task_id'],
            expected={'record_sha256': dispatched_record['record_sha256']},
            review_repair_claims=[failed])
        self.assertEqual(saved['review_repair_claims'], [failed])
        with self.assertRaises(LLMToolError) as caught:
            self.store.compare_and_update(task['task_id'],
                expected={'record_sha256': saved['record_sha256']},
                review_repair_claims=[dict(failed, state='DISPATCHED')])
        self.assertEqual(caught.exception.task_store_error_code,
                         'REVIEW_REPAIR_CLAIM_TRANSITION_INVALID')
        self.assertEqual(self.store.get(task['task_id']), saved)

    def test_retry_claim_transition_is_atomic_with_next_attempt(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        first = {
            'executor_task': 'executor-1', 'pr': 17, 'head_sha': 'a' * 40,
            'version': 1, 'attempt': 1, 'family': 'pr-17', 'state': 'CLAIMED',
            'repair_task_id': new_id(), 'comment_id': 9, 'packet_sha256': 'b' * 64,
            'claimed_at': '2026-10-27T00:00:00Z',
        }
        saved = self.store.get(task['task_id'])
        self.store.compare_and_update(task['task_id'],
            expected={'record_sha256': saved['record_sha256']},
            review_repair_claims=[first])
        saved = self.store.get(task['task_id'])
        first_dispatched = dict(first, state='DISPATCHED')
        self.store.compare_and_update(task['task_id'],
            expected={'record_sha256': saved['record_sha256']},
            review_repair_claims=[first_dispatched])
        second = dict(first, attempt=2, state='CLAIMED', repair_task_id=new_id(),
                      claimed_at='2026-10-27T00:01:00Z')
        saved = self.store.get(task['task_id'])
        updated = self.store.compare_and_update(task['task_id'],
            expected={'record_sha256': saved['record_sha256']},
            review_repair_claims=[dict(first_dispatched, state='FAILED'), second])
        self.assertEqual([claim['state'] for claim in updated['review_repair_claims']],
                         ['FAILED', 'CLAIMED'])

    def test_remote_test_receipt_is_appended_idempotently_to_same_task(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        receipt = {
            'result': 'PASS', 'exit_code': 0, 'source_sha': 'a' * 40,
            'tree': 'b' * 40, 'fetch_head': 'a' * 40,
            'modules': ['test_task_store'], 'identity': {'uid': 65534},
            'started_at': '2026-10-27T00:00:00Z', 'ended_at': '2026-10-27T00:00:01Z',
            'elapsed_s': 1.0, 'ran_total': 1,
            'final': ['OK (skipped=1)'], 'failures': [],
            'log_bytes': 0, 'log_sha256': 'c' * 64,
            'remote_log_path': '/tmp/llmtool-remote-log.Abc123/remote-test.log',
            'task_id': task['task_id'],
            'run_id': task['run_id'], 'executor_task': 'd6-task',
            'pm_session': task['pm_session_id'], 'command_digest': 'd' * 64,
            'receipt_digest': 'e' * 64,
        }
        receipt['receipt_digest'] = hashlib.sha256(json.dumps(
            {key: value for key, value in receipt.items() if key != 'receipt_digest'},
            ensure_ascii=False, sort_keys=True, separators=(',', ':'),
            allow_nan=False).encode() + b'\n').hexdigest()
        self.assertEqual(self.store.append_remote_test_receipt(task['task_id'], receipt), receipt)
        self.assertEqual(self.store.append_remote_test_receipt(task['task_id'], receipt), receipt)
        self.assertEqual(self.store.get(task['task_id'])['remote_test_receipts'], [receipt])

        invalid = {**receipt, 'final': ['OK (expected failures=1)']}
        invalid['receipt_digest'] = hashlib.sha256(json.dumps(
            {key: value for key, value in invalid.items() if key != 'receipt_digest'},
            ensure_ascii=False, sort_keys=True, separators=(',', ':'),
            allow_nan=False).encode() + b'\n').hexdigest()
        with self.assertRaises(LLMToolError):
            self.store.append_remote_test_receipt(task['task_id'], invalid)

    def test_published_pr_is_idempotent_replaceable_and_validated(self):
        task = self._codex_dispatch_task()
        self.store.create(task)

        first = self.store.record_published_pr(task['task_id'], 425, 'a' * 40)
        self.assertEqual(first, self.store.record_published_pr(
            task['task_id'], 425, 'a' * 40))
        self.assertEqual(self.store.get(task['task_id'])['published_pr'], first)
        replacement = self.store.record_published_pr(
            task['task_id'], 426, 'b' * 40)
        self.assertEqual(replacement['pr'], 426)
        self.assertEqual(replacement['head_sha'], 'b' * 40)

        claim = {
            'executor_task': 'executor-d6', 'pr': 426, 'head_sha': 'b' * 40,
            'version': 1, 'state': 'CLAIMED', 'reviewer_task': 'reviewer-d6',
            'native_session_id': None, 'account_mode': 'ROLES',
            'claimed_at': '2026-10-27T00:00:00Z',
            'updated_at': '2026-10-27T00:00:00Z',
        }
        current = self.store.get(task['task_id'])
        saved = self.store.compare_and_update(
            task['task_id'], expected={'record_sha256': current['record_sha256']},
            review_dispatch_claim=[claim], remote_test_receipts=[])
        task_store_module._validate(saved)
        with_published = dict(saved, published_pr=replacement)
        with_published['record_sha256'] = task_store_module._digest(with_published)
        task_store_module._validate(with_published)
        without_published = dict(saved)
        without_published.pop('published_pr')
        without_published['record_sha256'] = task_store_module._digest(without_published)
        task_store_module._validate(without_published)

        for invalid in (
                {'pr': True, 'head_sha': 'a' * 40,
                 'recorded_at': '2026-10-27T00:00:00Z'},
                {'pr': 0, 'head_sha': 'a' * 40,
                 'recorded_at': '2026-10-27T00:00:00Z'},
                {'pr': 425, 'head_sha': 'A' * 40,
                 'recorded_at': '2026-10-27T00:00:00Z'},
                {'pr': 425, 'head_sha': 'a' * 40,
                 'recorded_at': 'bad'},
                {'pr': 425, 'head_sha': 'a' * 40,
                 'recorded_at': '2026-10-27T00:00:00Z', 'extra': True},
        ):
            malformed = dict(saved, published_pr=invalid)
            malformed['record_sha256'] = task_store_module._digest(malformed)
            with self.subTest(invalid=invalid), self.assertRaises(LLMToolError) as caught:
                task_store_module._validate(malformed)
            self.assertEqual(caught.exception.task_store_error_code,
                             'TASK_PUBLISHED_PR_INVALID')

        for pr, head in ((True, 'a' * 40), (0, 'a' * 40), (425, 'bad')):
            with self.subTest(pr=pr, head=head), self.assertRaises(LLMToolError) as caught:
                self.store.record_published_pr(task['task_id'], pr, head)
            self.assertEqual(caught.exception.task_store_error_code,
                             'TASK_PUBLISHED_PR_INVALID')

    def test_remote_test_receipt_accepts_missing_log_and_rejects_other_paths(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        base = {
            'result': 'FAIL', 'exit_code': 1, 'source_sha': 'a' * 40,
            'tree': 'b' * 40, 'fetch_head': 'a' * 40,
            'modules': ['test_task_store'], 'identity': {'uid': 65534},
            'started_at': '2026-10-27T00:00:00Z', 'ended_at': '2026-10-27T00:00:01Z',
            'elapsed_s': 1.0, 'ran_total': 1, 'final': ['FAILED (failures=1)'],
            'failures': ['test_task_store'], 'log_bytes': 1, 'log_sha256': 'c' * 64,
            'remote_log_path': None, 'task_id': task['task_id'],
            'run_id': task['run_id'], 'executor_task': 'd6-task',
            'pm_session': task['pm_session_id'], 'command_digest': 'd' * 64,
            'receipt_digest': 'e' * 64,
        }
        for path in (None, '/tmp/llmtool-remote-log.Abc123/remote-test.log'):
            receipt = {**base, 'remote_log_path': path}
            receipt['receipt_digest'] = hashlib.sha256(json.dumps(
                {key: value for key, value in receipt.items() if key != 'receipt_digest'},
                ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                allow_nan=False).encode() + b'\n').hexdigest()
            self.assertEqual(self.store.append_remote_test_receipt(task['task_id'], receipt),
                             receipt)
        invalid = {**base, 'remote_log_path': '/tmp/other.log'}
        invalid['receipt_digest'] = hashlib.sha256(json.dumps(
            {key: value for key, value in invalid.items() if key != 'receipt_digest'},
            ensure_ascii=False, sort_keys=True, separators=(',', ':'),
            allow_nan=False).encode() + b'\n').hexdigest()
        with self.assertRaises(LLMToolError):
            self.store.append_remote_test_receipt(task['task_id'], invalid)

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

    def test_codex_delegate_developer_account_is_stored_as_name(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        developer = self._developer_assignment()
        developer['account'] = 'codex-developer'
        saved = self.store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=developer)
        self.assertEqual(saved['developer']['account'], 'codex-developer')
        self.assertNotIn('/private', repr(saved['developer']))

    def test_developer_account_rejects_path_like_and_invalid_names(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        for account in ('/private/codex-account', 'codex.account'):
            with self.subTest(account=account):
                developer = self._developer_assignment()
                developer['account'] = account
                with self.assertRaises(LLMToolError):
                    self.store.compare_and_update(
                        task['task_id'], expected={'developer': None},
                        developer=developer)

    def test_real_store_accepts_reviewer_accounts_and_preserves_them_on_transition(self):
        for account in ('claude-reviewer', 'codex-reviewer'):
            with self.subTest(account=account):
                task = self._codex_dispatch_task()
                self.store.create(task)
                claimed = {
                    'executor_task': 'executor-d6', 'pr': 42,
                    'head_sha': 'a' * 40, 'version': 1, 'state': 'CLAIMED',
                    'reviewer_task': 'reviewer-d6', 'native_session_id': None,
                    'account_mode': 'ROLES', 'account': account,
                    'claimed_at': '2026-10-27T00:00:00Z',
                    'updated_at': '2026-10-27T00:00:00Z',
                }
                initial = self.store.get(task['task_id'])
                self.store.compare_and_update(
                    task['task_id'], expected={'record_sha256': initial['record_sha256']},
                    review_dispatch_claim=[claimed])
                current = self.store.get(task['task_id'])
                dispatched = dict(claimed, state='DISPATCHED',
                                  native_session_id='native-' + account)
                changed_account = dict(dispatched, account='other-reviewer')
                with self.assertRaises(LLMToolError):
                    self.store.compare_and_update(
                        task['task_id'],
                        expected={'record_sha256': current['record_sha256']},
                        review_dispatch_claim=[changed_account])
                self.store.compare_and_update(
                    task['task_id'], expected={'record_sha256': current['record_sha256']},
                    review_dispatch_claim=[dispatched])
                self.assertEqual(
                    self.store.get(task['task_id'])['review_dispatch_claim'], [dispatched])

    def test_task_store_rejects_invalid_account_names(self):
        for account in ('/private/codex-account', 'codex.account'):
            with self.subTest(account=account):
                task = self._codex_dispatch_task()
                self.store.create(task)
                initial = self.store.get(task['task_id'])
                claim = {
                    'executor_task': 'executor-d6', 'pr': 42,
                    'head_sha': 'a' * 40, 'version': 1, 'state': 'CLAIMED',
                    'reviewer_task': 'reviewer-d6', 'native_session_id': None,
                    'account_mode': 'ROLES', 'account': account,
                    'claimed_at': '2026-10-27T00:00:00Z',
                    'updated_at': '2026-10-27T00:00:00Z',
                }
                with self.assertRaises(LLMToolError):
                    self.store.compare_and_update(
                        task['task_id'],
                        expected={'record_sha256': initial['record_sha256']},
                        review_dispatch_claim=[claim])

    def _claude_result_task(self, pm_session_id=None):
        task = self._codex_dispatch_task()
        if pm_session_id is not None:
            task['pm_session_id'] = pm_session_id
        self.store.create(task)
        developer = self._developer_assignment(provider='claude')
        developer['owner_assignment_ref'] = (
            'https://github.com/davidcatcat/davidcattoolsproject/issues/289'
            '#issuecomment-1')
        developer['executor_task_id'] = 'executor-1'
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            self.store.compare_and_update(
                task['task_id'], expected={'developer': None}, developer=developer)
        return task

    def _claude_cli_task(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        registration = SimpleNamespace(
            task_id='executor-claude', root='/registered/root', common_dir='/repo/.git',
            branch='task-branch', result_sha='a' * 40, trusted_agent_id='claude-developer',
            session_ref='session-ref', native_session_id=None)
        developer = self._developer_assignment(provider='claude')
        developer.update(
            backend_session_semantics='CLI_REPORTED_SESSION_ID',
            owner_assignment_ref=('https://github.com/davidcatcat/davidcattoolsproject/issues/289'
                                  '#issuecomment-1'),
            executor_task_id=registration.task_id,
            execution=None,
            direct_result_callback=task_submission_cli_module._direct_result_callback(
                task, registration, 'a' * 40, test_policy='LOCAL'))
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            self.store.compare_and_update(
                task['task_id'], expected={'developer': None}, developer=developer)
        return task

    def test_claude_cli_record_lifecycle_and_record_only_result_refusal(self):
        task = self._claude_cli_task()
        developer = self.store.get(task['task_id'])['developer']
        self.assertEqual(developer['backend_session_semantics'], 'CLI_REPORTED_SESSION_ID')
        with self.assertRaises(LLMToolError) as caught:
            self.store.record_claude_result(
                task['task_id'], pm_session_id=task['pm_session_id'],
                executor_task_id='executor-claude', head_sha='a' * 40)
        self.assertEqual(caught.exception.task_store_error_code, 'CLAUDE_RESULT_STATE_INVALID')

        dispatching = dict(developer, state='DEVELOPER_DISPATCHING',
                           provider_type='claude-cli', model='claude-test-1',
                           execution=None)
        initial = self.store.get(task['task_id'])
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            self.store.compare_and_update(task['task_id'],
                expected={'record_sha256': initial['record_sha256']}, developer=dispatching)
        responded = dict(dispatching, state='DEVELOPER_RESPONDED',
            artifact='diff --git a/src.py b/src.py\n',
            artifact_sha256=hashlib.sha256(b'diff --git a/src.py b/src.py\n').hexdigest(),
            execution={'status': 'DONE', 'native_session_id': '12345678-1234-4234-8234-123456789abc',
                'process_exit_code': 0, 'stdout_sha256': 'c' * 64, 'elapsed_ms': 1,
                'cleanup_status': 'CONFIRMED', 'usage_source': 'UNKNOWN',
                'source_bytes': None, 'source_file_count': None,
                'source_manifest_sha256': None})
        current = self.store.get(task['task_id'])
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            self.store.compare_and_update(task['task_id'],
                expected={'record_sha256': current['record_sha256']}, developer=responded)
        self.assertEqual(self.store.get(task['task_id'])['developer']['state'],
                         'DEVELOPER_RESPONDED')

    def test_claude_cli_accepts_blocked_isolation_record(self):
        task = self._claude_cli_task()
        developer = self.store.get(task['task_id'])['developer']
        blocked = dict(developer, state='BLOCKED', provider_type='claude-cli',
                       model='claude-test-1', error_code='BLOCKED_WRITE_ISOLATION',
                       blocked_reason='BLOCKED_WRITE_ISOLATION', execution=None)
        current = self.store.get(task['task_id'])
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            saved = self.store.compare_and_update(task['task_id'],
                expected={'record_sha256': current['record_sha256']}, developer=blocked)
        self.assertEqual(saved['developer']['state'], 'BLOCKED')

    def test_claude_cli_rejects_mismatched_callback_executor(self):
        task = self._claude_cli_task()
        current = self.store.get(task['task_id'])
        developer = dict(current['developer'])
        developer['direct_result_callback'] = dict(
            developer['direct_result_callback'], executor_task_id='other-executor')
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            with self.assertRaises(LLMToolError):
                self.store.compare_and_update(task['task_id'],
                    expected={'record_sha256': current['record_sha256']}, developer=developer)

    def test_claude_cli_rejects_record_only_result_field(self):
        task = self._claude_cli_task()
        current = self.store.get(task['task_id'])
        developer = dict(current['developer'], claude_result={})
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            with self.assertRaises(LLMToolError):
                self.store.compare_and_update(task['task_id'],
                    expected={'record_sha256': current['record_sha256']}, developer=developer)

    def test_record_only_claude_rejects_cli_provider_type(self):
        task = self._claude_result_task()
        current = self.store.get(task['task_id'])
        developer = dict(current['developer'], provider_type='claude-cli')
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            with self.assertRaises(LLMToolError):
                self.store.compare_and_update(task['task_id'],
                    expected={'record_sha256': current['record_sha256']}, developer=developer)

    def test_claude_assignment_rejects_invalid_repository_config(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        developer = self._developer_assignment(provider='claude')
        developer['owner_assignment_ref'] = (
            'https://github.com/davidcatcat/davidcattoolsproject/issues/289'
            '#issuecomment-1')
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None), \
                patch.object(worker_dispatch, 'owner_comment_ref',
                             side_effect=LLMToolError('repository config is invalid')):
            with self.assertRaises(LLMToolError) as caught:
                self.store.compare_and_update(
                    task['task_id'], expected={'developer': None}, developer=developer)
        self.assertEqual(caught.exception.task_store_error_code,
                         'TASK_STORE_REPOSITORY_CONFIG_INVALID')

    def test_claude_result_is_pm_bound_single_pending_transition(self):
        task = self._claude_result_task()
        pm_session = task['pm_session_id']
        with self.assertRaises(LLMToolError):
            self.store.record_claude_result(
                task['task_id'], pm_session_id='pm-' + new_id(),
                executor_task_id='executor-1', head_sha='a' * 40)
        with self.assertRaises(LLMToolError):
            self.store.record_claude_result(
                task['task_id'], pm_session_id=pm_session,
                executor_task_id='executor-1', head_sha='bad')
        with self.assertRaises(LLMToolError):
            self.store.record_claude_result(
                task['task_id'], pm_session_id=pm_session,
                executor_task_id='different-executor', head_sha='a' * 40)
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            saved = self.store.record_claude_result(
                task['task_id'], pm_session_id=pm_session,
                executor_task_id='executor-1', head_sha='a' * 40)
        result = saved['developer']['claude_result']
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_RESPONDED')
        self.assertEqual(result['executor_task_id'], 'executor-1')
        self.assertEqual(result['head_sha'], 'a' * 40)
        self.assertRegex(result['recorded_at'], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
        with self.assertRaises(LLMToolError):
            self.store.record_claude_result(
                task['task_id'], pm_session_id=pm_session,
                executor_task_id='executor-1', head_sha='a' * 40)

    def test_claude_result_cli_requires_invoking_pm_session(self):
        task = self._claude_result_task()
        args = SimpleNamespace(task_id=task['task_id'], executor_task='executor-1',
                               head='a' * 40)
        with self.assertRaises(LLMToolError):
            task_submission_cli_module.run_task_claude_result(args, store=self.store)
        args.pm_session = 'pm-not-the-original'
        with self.assertRaises(LLMToolError):
            task_submission_cli_module.run_task_claude_result(args, store=self.store)
        args.pm_session = 'not a session'
        with self.assertRaises(LLMToolError):
            task_submission_cli_module.run_task_claude_result(args, store=self.store)
        self.assertEqual(self.store.get(task['task_id'])['developer']['state'],
                         'DEVELOPER_PENDING')
        args.pm_session = task['pm_session_id']
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            self.assertEqual(task_submission_cli_module.run_task_claude_result(
                args, store=self.store, stdout=io.StringIO()), 0)
        self.assertEqual(self.store.get(task['task_id'])['developer']['state'],
                         'DEVELOPER_RESPONDED')

        native_pm_session_id = str(uuid.uuid4())
        uuid_task = self._claude_result_task(pm_session_id='pm-' + native_pm_session_id)
        args.task_id = uuid_task['task_id']
        args.pm_session = native_pm_session_id
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            self.assertEqual(task_submission_cli_module.run_task_claude_result(
                args, store=self.store, stdout=io.StringIO()), 0)
        self.assertEqual(self.store.get(uuid_task['task_id'])['developer']['state'],
                         'DEVELOPER_RESPONDED')

    def test_claude_result_rejects_wrong_state(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        with self.assertRaises(LLMToolError):
            self.store.record_claude_result(
                task['task_id'], pm_session_id=task['pm_session_id'],
                executor_task_id='executor-1', head_sha='a' * 40)

    def _allow_codex_dispatch(self):
        policy_path = Path(worker_dispatch._POLICY_PATH)
        policy = json.loads(policy_path.read_text(encoding='utf-8'))
        policy['workers']['codex-developer']['dispatch'] = 'ALLOWED'
        policy['workers']['opencode-developer']['dispatch'] = 'ALLOWED'
        temporary_policy = Path(self.temp.name) / 'worker-dispatch.json'
        temporary_policy.write_text(json.dumps(policy), encoding='utf-8')
        patcher = patch.object(worker_dispatch, '_POLICY_PATH', temporary_policy)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _allow_agy_assignment(self, task_id, owner_assignment_ref):
        policy_path = Path(worker_dispatch._POLICY_PATH)
        policy = json.loads(policy_path.read_text(encoding='utf-8'))
        policy['workers']['agy']['owner_assignments'] = [{
            'task_id': task_id, 'owner_assignment_ref': owner_assignment_ref,
        }]
        temporary_policy = Path(self.temp.name) / 'worker-dispatch.json'
        temporary_policy.write_text(json.dumps(policy), encoding='utf-8')
        patcher = patch.object(worker_dispatch, '_POLICY_PATH', temporary_policy)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_developer_progress_is_optional_extension_for_all_direct_routes(self):
        artifact = ('diff --git a/src.py b/src.py\n'
                    '--- a/src.py\n+++ b/src.py\n@@ -1 +1 @@\n-x=1\n+x=2\n')
        execution = {
            'status': 'DONE', 'native_session_id': '12345678-1234-4234-8234-123456789abc',
            'process_exit_code': 0, 'stdout_sha256': 'c' * 64, 'elapsed_ms': 12,
            'cleanup_status': 'CONFIRMED', 'usage_source': 'UNKNOWN',
            'source_bytes': None, 'source_file_count': None,
            'source_manifest_sha256': None,
        }
        cases = (
            ('codex', 'DEVELOPER_PENDING', False, False),
            ('codex', 'DEVELOPER_RESPONDED', True, False),
            ('agy', 'DEVELOPER_RESPONDED', True, False),
            ('agy', 'BLOCKED', False, True),
        )
        for provider, state, with_execution, with_progress in cases:
            with self.subTest(provider=provider, state=state, progress=with_progress):
                task = self._codex_dispatch_task()
                self.store.create(task)
                developer = self._developer_assignment(provider=provider, max_tokens=None)
                developer.update(state=state,
                    provider_type=('agy-cli' if provider == 'agy' and state != 'DEVELOPER_PENDING'
                                   else 'codex-cli' if provider == 'codex' and state != 'DEVELOPER_PENDING'
                                   else None),
                    model=('gemini-test' if provider == 'agy' and state != 'DEVELOPER_PENDING'
                           else 'gpt-6-luna' if provider == 'codex' and state != 'DEVELOPER_PENDING'
                           else None),
                    backend_session_semantics=('CLI_REPORTED_SESSION_ID' if provider == 'agy'
                                               else 'NO_NATIVE_SESSION_ID_EXPOSED'),
                    error_code='AGY_QUOTA_EXHAUSTED' if with_progress else None)
                if provider == 'agy':
                    developer['owner_assignment_ref'] = (
                        'https://github.com/davidcatcat/davidcattoolsproject/issues/289'
                        '#issuecomment-1')
                    self._allow_agy_assignment(task['task_id'],
                                               developer['owner_assignment_ref'])
                developer['execution'] = execution if with_execution else None
                if with_progress:
                    developer['progress'] = {
                        'state': 'QUOTA_EXHAUSTED', 'head_sha': 'a' * 40,
                        'changed_files': ['src.py'], 'unfinished_checks': ['UNKNOWN'],
                        'session_id': 'conversation_12345678',
                    }
                if state == 'DEVELOPER_RESPONDED':
                    developer.update(artifact=artifact,
                        artifact_sha256=hashlib.sha256(artifact.encode()).hexdigest())
                saved = self.store.compare_and_update(
                    task['task_id'], expected={'developer': None}, developer=developer)
                self.assertEqual(saved['developer'].get('progress'),
                                 developer.get('progress'))
                self.assertEqual(saved['developer'].get('execution'),
                                 developer.get('execution'))

    def test_non_claude_d6_binding_refuses_second_live_task_for_each_route(self):
        store = TaskStore(Path(self.temp.name).resolve())
        routes = ('codex', 'company-gw', 'opencode', 'agy', 'gw-agent')

        def create_task(provider, *, bind=False):
            task = self._codex_dispatch_task()
            store.create(task)
            if not bind:
                return task['task_id']
            developer = self._developer_assignment(provider=provider, max_tokens=None)
            if provider == 'opencode':
                # OpenCode assignments always carry an execution record, even
                # before a run has started.
                developer['execution'] = None
            if provider in ('opencode', 'agy', 'gw-agent'):
                developer['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
            if provider == 'agy':
                ref = ('https://github.com/davidcatcat/davidcattoolsproject/issues/289'
                       '#issuecomment-1')
                developer['owner_assignment_ref'] = ref
                self._allow_agy_assignment(task['task_id'], ref)
            completion = {
                'state': 'WAITING_EXECUTOR',
                'executor_task_id': executor_task_id,
                'reviewer_task_id': None, 'evidence': None, 'missing_gates': [],
                'pm_dispatch': 'NOT_STARTED', 'pm_response': None,
                'pm_recommendation': None, 'pm_usage': None, 'pm_error_code': None,
            }
            developer['executor_task_id'] = executor_task_id
            store.compare_and_update(
                task['task_id'], expected={'developer': None, 'completion': None},
                developer=developer, completion=completion)
            return task['task_id']

        for route in routes:
            with self.subTest(route=route):
                executor_task_id = 'executor-shared-' + route
                live_id = create_task(route, bind=True)
                candidate_id = create_task(route)
                pending = self._developer_assignment(provider=route, max_tokens=None)
                if route == 'opencode':
                    pending['execution'] = None
                if route in ('opencode', 'agy', 'gw-agent'):
                    pending['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
                if route == 'agy':
                    ref = ('https://github.com/davidcatcat/davidcattoolsproject/issues/289'
                           '#issuecomment-1')
                    pending['owner_assignment_ref'] = ref
                    self._allow_agy_assignment(candidate_id, ref)
                completion = {
                    'state': 'WAITING_EXECUTOR', 'executor_task_id': executor_task_id,
                    'reviewer_task_id': None, 'evidence': None, 'missing_gates': [],
                    'pm_dispatch': 'NOT_STARTED', 'pm_response': None,
                    'pm_recommendation': None, 'pm_usage': None, 'pm_error_code': None,
                }
                with self.assertRaises(LLMToolError) as caught:
                    store.d6_bind_compare_and_update(
                        candidate_id, executor_task_id=executor_task_id,
                        expected={'developer': None, 'completion': None},
                        developer=pending, completion=completion)
                self.assertEqual(caught.exception.task_store_error_code,
                                 'TASK_D6_ALREADY_BOUND')
                self.assertEqual(store.get(live_id)['developer']['state'],
                                 'DEVELOPER_PENDING')

    def test_non_claude_d6_binding_allows_finished_and_abandoned_tasks(self):
        self._allow_codex_dispatch()
        store = TaskStore(Path(self.temp.name).resolve())
        executor_task_id = 'executor-repair-1'
        finished = self._codex_dispatch_task()
        store.create(finished)
        developer = self._developer_assignment(provider='codex', max_tokens=None)
        developer['executor_task_id'] = executor_task_id
        completion = {
            'state': 'WAITING_EXECUTOR', 'executor_task_id': executor_task_id,
            'reviewer_task_id': None, 'evidence': None, 'missing_gates': [],
            'pm_dispatch': 'NOT_STARTED', 'pm_response': None,
            'pm_recommendation': None, 'pm_usage': None, 'pm_error_code': None,
        }
        store.compare_and_update(finished['task_id'],
            expected={'developer': None, 'completion': None},
            developer=developer, completion=completion)
        dispatching = dict(developer, state='DEVELOPER_DISPATCHING',
                           provider_type='codex-cli', model='gpt-6-luna')
        store.update(finished['task_id'], developer=dispatching)
        artifact = ('diff --git a/src.py b/src.py\n--- a/src.py\n+++ b/src.py\n'
                    '@@ -1 +1 @@\n-x=1\n+x=2\n')
        responded = dict(dispatching, state='DEVELOPER_RESPONDED',
                         artifact=artifact,
                         artifact_sha256=hashlib.sha256(artifact.encode()).hexdigest())
        store.update(finished['task_id'], developer=responded)

        candidate = self._codex_dispatch_task()
        store.create(candidate)
        candidate_developer = self._developer_assignment(provider='codex', max_tokens=None)
        candidate_completion = dict(completion)
        saved = store.d6_bind_compare_and_update(
            candidate['task_id'], executor_task_id=executor_task_id,
            expected={'developer': None, 'completion': None},
            developer=candidate_developer, completion=candidate_completion)
        self.assertEqual(saved['completion']['executor_task_id'], executor_task_id)

        abandoned = self._codex_dispatch_task()
        store.create(abandoned)
        abandoned_developer = self._developer_assignment(provider='codex', max_tokens=None)
        abandoned_developer['executor_task_id'] = 'executor-abandoned-1'
        abandoned_completion = dict(completion, executor_task_id='executor-abandoned-1')
        store.compare_and_update(abandoned['task_id'],
            expected={'developer': None, 'completion': None},
            developer=abandoned_developer, completion=abandoned_completion)
        store.abandon(abandoned['task_id'], pm_session_id=abandoned['pm_session_id'],
                      reason='Task stopped before D6 work')
        after_abandonment = self._codex_dispatch_task()
        store.create(after_abandonment)
        allowed_developer = self._developer_assignment(provider='codex', max_tokens=None)
        allowed_completion = dict(completion, executor_task_id='executor-abandoned-1')
        saved = store.d6_bind_compare_and_update(
            after_abandonment['task_id'], executor_task_id='executor-abandoned-1',
            expected={'developer': None, 'completion': None},
            developer=allowed_developer, completion=allowed_completion)
        self.assertEqual(saved['completion']['executor_task_id'], 'executor-abandoned-1')

    def test_non_claude_concurrent_d6_binds_allow_exactly_one_winner(self):
        store = TaskStore(Path(self.temp.name).resolve())
        executor_task_id = 'executor-concurrent-1'
        candidates = [self._codex_dispatch_task() for _ in range(2)]
        for task in candidates:
            store.create(task)
        barrier = threading.Barrier(2)
        outcomes = []

        def bind(task):
            developer = self._developer_assignment(provider='codex', max_tokens=None)
            completion = {
                'state': 'WAITING_EXECUTOR', 'executor_task_id': executor_task_id,
                'reviewer_task_id': None, 'evidence': None, 'missing_gates': [],
                'pm_dispatch': 'NOT_STARTED', 'pm_response': None,
                'pm_recommendation': None, 'pm_usage': None, 'pm_error_code': None,
            }
            barrier.wait()
            try:
                store.d6_bind_compare_and_update(
                    task['task_id'], executor_task_id=executor_task_id,
                    expected={'developer': None, 'completion': None},
                    developer=developer, completion=completion)
                outcomes.append('BOUND')
            except LLMToolError as exc:
                outcomes.append(exc.task_store_error_code)

        threads = [threading.Thread(target=bind, args=(task,)) for task in candidates]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(outcomes.count('BOUND'), 1)
        self.assertEqual(outcomes.count('TASK_D6_ALREADY_BOUND'), 1)

    def test_developer_reported_claims_are_bounded_and_strict(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        pending = self._developer_assignment(provider='codex', max_tokens=None)
        self.store.compare_and_update(task['task_id'], expected={'developer': None},
                                      developer=pending)
        valid_report = {'tests': ['claimed test'], 'summary': 'claimed summary',
                        'self_review': 'claimed review'}
        valid = {**pending, 'reported': valid_report}
        saved = self.store.update(task['task_id'], developer=valid)
        self.assertEqual(saved['developer']['reported'], valid_report)
        self.assertIsNone(saved.get('completion'))

        for reported in (
                {'other': 'field'}, {'summary': 4},
                {'summary': 'x' * (8 * 1024 + 1)},
                {'summary': 'x' * (4 * 1024), 'self_review': 'y' * (4 * 1024)},
                {'tests': ['x' * 1025]},
                {'tests': [''.join(('api', '_', 'key', '=', 'secret'))]},
                {'tests': ['ok'] * 33}, {}):
            with self.subTest(reported=repr(reported)[:32]):
                with self.assertRaises(LLMToolError):
                    self.store.update(task['task_id'],
                                      developer={**valid, 'reported': reported})

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

    def test_codex_child_identity_is_written_only_by_trusted_launch_path(self):
        self._allow_codex_dispatch()
        task = self._codex_dispatch_task()
        self.store.create(task)
        pending = self._developer_assignment(provider='codex', max_tokens=None)
        self.store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=pending)
        dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                       'provider_type': 'codex-cli', 'model': 'gpt-6-luna'}
        self.store.update(task['task_id'], developer=dispatching)
        launch = {**dispatching, 'execution': {
            'status': 'RUNNING', 'native_session_id': None,
            'process_exit_code': None, 'stdout_sha256': None, 'elapsed_ms': None,
            'cleanup_status': 'UNKNOWN', 'usage_source': 'UNKNOWN',
            'source_bytes': None, 'source_file_count': None,
            'source_manifest_sha256': None, 'child_pid': 123,
            'child_start_time': '123456', 'child_pgid': 123}}
        with self.assertRaises(LLMToolError):
            self.store.update(task['task_id'], developer=launch)
        saved = self.store.record_codex_launch(
            task['task_id'], assignment_id=pending['assignment_id'], pid=123,
            start_time='123456', pgid=123)
        self.assertEqual(saved['developer']['execution'], launch['execution'])

    def test_opencode_child_identity_is_profile_checked_and_trusted(self):
        self._allow_codex_dispatch()
        task = self._codex_dispatch_task()
        task['pm_assignment']['route'] = 'opencode'
        store = TaskStore(Path(self.temp.name).resolve() / 'opencode-tasks')
        store.create(task)
        pending = self._developer_assignment(provider='opencode', max_tokens=4096)
        pending['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
        pending['execution'] = None
        store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=pending)
        dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                       'provider_type': 'opencode-cli', 'model': 'opencode/test-free'}
        store.update(task['task_id'], developer=dispatching)
        launch = {**dispatching, 'execution': {
            'status': 'RUNNING', 'native_session_id': None,
            'process_exit_code': None, 'stdout_sha256': None, 'elapsed_ms': None,
            'cleanup_status': 'UNKNOWN', 'usage_source': 'UNKNOWN',
            'source_bytes': None, 'source_file_count': None,
            'source_manifest_sha256': None, 'child_pid': 123,
            'child_start_time': '123456', 'child_pgid': 123}}
        with self.assertRaises(LLMToolError):
            store.update(task['task_id'], developer=launch)
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            saved = store.record_opencode_launch(
                task['task_id'], assignment_id=pending['assignment_id'], pid=123,
                start_time='123456', pgid=123)
        self.assertEqual(saved['developer']['execution'], launch['execution'])
        with self.assertRaises(LLMToolError):
            store.record_opencode_launch(
                task['task_id'], assignment_id=pending['assignment_id'], pid=124,
                start_time='123457', pgid=124)

    def test_codex_direct_success_persists_native_thread_and_usage(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        pending = self._developer_assignment(provider='codex', max_tokens=None)
        pending['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
        self.store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=pending)
        dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                       'provider_type': 'codex-cli', 'model': 'gpt-6-luna'}
        self.store.update(task['task_id'], developer=dispatching)
        artifact = ('diff --git a/src.py b/src.py\n'
                    '--- a/src.py\n+++ b/src.py\n@@ -1 +1 @@\n-x=1\n+x=2\n')
        execution = {'status': 'DONE',
            'native_session_id': '12345678-1234-4234-8234-123456789abc',
            'process_exit_code': 0, 'stdout_sha256': 'c' * 64, 'elapsed_ms': 12,
            'cleanup_status': 'CONFIRMED', 'usage_source': 'CLI_REPORTED',
            'source_bytes': None, 'source_file_count': None,
            'source_manifest_sha256': None, 'native_session_binding': 'BOUND'}
        responded = {**dispatching, 'state': 'DEVELOPER_RESPONDED',
            'artifact': artifact, 'artifact_sha256': hashlib.sha256(artifact.encode()).hexdigest(),
            'execution': execution,
            'usage': {'prompt_tokens': 13, 'completion_tokens': 8, 'total_tokens': 21}}
        saved = self.store.update(task['task_id'], developer=responded)
        self.assertEqual(saved['developer']['execution']['status'], 'DONE')
        self.assertEqual(saved['developer']['execution']['native_session_id'],
                         execution['native_session_id'])
        self.assertEqual(saved['developer']['execution']['native_session_binding'], 'BOUND')
        self.assertEqual(saved['developer']['usage']['total_tokens'], 21)

    def test_native_session_binding_rejected_for_opencode_and_invalid_values(self):
        self._allow_codex_dispatch()  # also allows OpenCode; the live policy may pause it
        artifact = ('diff --git a/src.py b/src.py\n'
                    '--- a/src.py\n+++ b/src.py\n@@ -1 +1 @@\n-x=1\n+x=2\n')
        opencode_execution = {
            'status': 'SUCCEEDED', 'native_session_id': 'ses_abcdefgh12345678',
            'process_exit_code': 0, 'stdout_sha256': 'c' * 64,
            'elapsed_ms': 12, 'cleanup_status': 'CONFIRMED',
            'usage_source': 'UNKNOWN', 'source_bytes': 10,
            'source_file_count': 1, 'source_manifest_sha256': 'd' * 64,
        }
        task = self._codex_dispatch_task()
        self.store.create(task)
        pending = self._developer_assignment(provider='opencode', max_tokens=128)
        pending['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
        pending['execution'] = None
        self.store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=pending)
        dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                       'provider_type': 'opencode-cli', 'model': None}
        self.store.update(task['task_id'], developer=dispatching)
        responded = {
            **dispatching, 'state': 'DEVELOPER_RESPONDED', 'artifact': artifact,
            'artifact_sha256': hashlib.sha256(artifact.encode()).hexdigest(),
            'execution': {**opencode_execution, 'native_session_binding': 'BOUND'},
        }
        with self.assertRaises(LLMToolError):
            self.store.update(task['task_id'], developer=responded)

        for invalid_binding in ('bound', 'B' * 65, 7):
            with self.subTest(invalid_binding=repr(invalid_binding)):
                task = self._codex_dispatch_task()
                self.store.create(task)
                pending = self._developer_assignment(provider='codex', max_tokens=None)
                pending['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
                self.store.compare_and_update(
                    task['task_id'], expected={'developer': None}, developer=pending)
                dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                               'provider_type': 'codex-cli', 'model': 'gpt-6-luna'}
                self.store.update(task['task_id'], developer=dispatching)
                invalid = {
                    **dispatching, 'state': 'DEVELOPER_RESPONDED', 'artifact': artifact,
                    'artifact_sha256': hashlib.sha256(artifact.encode()).hexdigest(),
                    'execution': {
                        'status': 'DONE',
                        'native_session_id': '12345678-1234-4234-8234-123456789abc',
                        'process_exit_code': 0, 'stdout_sha256': 'c' * 64,
                        'elapsed_ms': 12, 'cleanup_status': 'CONFIRMED',
                        'usage_source': 'CLI_REPORTED', 'source_bytes': None,
                        'source_file_count': None, 'source_manifest_sha256': None,
                        'native_session_binding': invalid_binding,
                    },
                }
                with self.assertRaises(LLMToolError):
                    self.store.update(task['task_id'], developer=invalid)

        task = self._codex_dispatch_task()
        self.store.create(task)
        pending = self._developer_assignment(provider='codex', max_tokens=None)
        pending['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
        self.store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=pending)
        dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                       'provider_type': 'codex-cli', 'model': 'gpt-6-luna'}
        self.store.update(task['task_id'], developer=dispatching)
        invalid = {
            **dispatching, 'state': 'DEVELOPER_RESPONDED', 'artifact': artifact,
            'artifact_sha256': hashlib.sha256(artifact.encode()).hexdigest(),
            'execution': {
                'status': 'DONE',
                'native_session_id': '12345678-1234-4234-8234-123456789abc',
                'process_exit_code': 0, 'stdout_sha256': 'c' * 64,
                'elapsed_ms': 12, 'cleanup_status': 'CONFIRMED',
                'usage_source': 'CLI_REPORTED', 'source_bytes': None,
                'source_file_count': None, 'source_manifest_sha256': None,
                'unexpected_execution_key': 'rejected',
            },
        }
        with self.assertRaises(LLMToolError):
            self.store.update(task['task_id'], developer=invalid)

    def test_codex_blocked_and_timeout_outcomes_persist_reported_usage(self):
        usage = {'prompt_tokens': 13, 'completion_tokens': 8, 'total_tokens': 21}
        for status, error_code, process_exit in (
                ('BLOCKED', 'CODEX_BLOCKED', 0), ('TIMEOUT', 'CODEX_TIMEOUT', None)):
            with self.subTest(status=status):
                task = self._codex_dispatch_task()
                self.store.create(task)
                pending = self._developer_assignment(provider='codex', max_tokens=None)
                pending['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
                self.store.compare_and_update(
                    task['task_id'], expected={'developer': None}, developer=pending)
                dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                    'provider_type': 'codex-cli', 'model': 'gpt-6-luna'}
                self.store.update(task['task_id'], developer=dispatching)
                execution = {'status': status,
                    'native_session_id': '12345678-1234-4234-8234-123456789abc',
                    'process_exit_code': process_exit, 'stdout_sha256': 'd' * 64,
                    'elapsed_ms': 25, 'cleanup_status': 'CONFIRMED',
                    'usage_source': 'CLI_REPORTED', 'source_bytes': None,
                    'source_file_count': None, 'source_manifest_sha256': None}
                outcome = {**dispatching, 'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                    'error_code': error_code, 'execution': execution, 'usage': usage}
                if status == 'BLOCKED':
                    outcome['blocked_reason'] = 'The requested change cannot be made safely.'
                saved = self.store.update(task['task_id'], developer=outcome)
                self.assertEqual(saved['developer']['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
                self.assertEqual(saved['developer']['execution']['status'], status)
                self.assertEqual(saved['developer']['usage'], usage)
                if status == 'BLOCKED':
                    self.assertEqual(saved['developer']['blocked_reason'],
                                     'The requested change cannot be made safely.')
                    self.assertEqual(_task_view(saved)['developer']['blocked_reason'],
                                     saved['developer']['blocked_reason'])

    def test_blocked_reason_must_be_bounded_screened_and_bound_to_codex_blocked(self):
        for reason in ('x' * 2049, 'api_key=secret-value', 'bad\x01reason'):
            with self.subTest(reason=reason[:20]):
                task = self._codex_dispatch_task()
                self.store.create(task)
                pending = self._developer_assignment(provider='codex', max_tokens=None)
                pending['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
                self.store.compare_and_update(
                    task['task_id'], expected={'developer': None}, developer=pending)
                dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                    'provider_type': 'codex-cli', 'model': 'gpt-6-luna'}
                self.store.update(task['task_id'], developer=dispatching)
                invalid = {**dispatching, 'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                    'provider_type': 'codex-cli', 'error_code': 'CODEX_BLOCKED',
                    'execution': {'status': 'BLOCKED',
                        'native_session_id': '12345678-1234-4234-8234-123456789abc',
                        'process_exit_code': 0, 'stdout_sha256': 'd' * 64,
                        'elapsed_ms': 25, 'cleanup_status': 'CONFIRMED',
                        'usage_source': 'UNKNOWN', 'source_bytes': None,
                        'source_file_count': None, 'source_manifest_sha256': None},
                    'blocked_reason': reason}
                with self.assertRaises(LLMToolError):
                    self.store.update(task['task_id'], developer=invalid)

        task = self._codex_dispatch_task()
        self.store.create(task)
        pending = self._developer_assignment(provider='codex', max_tokens=None)
        pending['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
        self.store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=pending)
        dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
            'provider_type': 'codex-cli', 'model': 'gpt-6-luna'}
        self.store.update(task['task_id'], developer=dispatching)
        not_blocked = {**dispatching, 'state': 'DEVELOPER_OUTCOME_UNKNOWN',
            'provider_type': 'codex-cli', 'error_code': 'CODEX_TIMEOUT',
            'execution': {'status': 'TIMEOUT',
                'native_session_id': '12345678-1234-4234-8234-123456789abc',
                'process_exit_code': None, 'stdout_sha256': 'd' * 64,
                'elapsed_ms': 25, 'cleanup_status': 'CONFIRMED',
                'usage_source': 'UNKNOWN', 'source_bytes': None,
                'source_file_count': None, 'source_manifest_sha256': None},
            'blocked_reason': 'must not be attached to an unknown outcome'}
        with self.assertRaises(LLMToolError):
            self.store.update(task['task_id'], developer=not_blocked)

    def test_blocked_reason_is_rejected_for_every_non_blocked_codex_outcome(self):
        artifact = ('diff --git a/src.py b/src.py\n'
                    '--- a/src.py\n+++ b/src.py\n@@ -1 +1 @@\n-x=1\n+x=2\n')
        outcomes = (
            ('DEVELOPER_RESPONDED', None, None, artifact, None),
            ('DEVELOPER_OUTCOME_UNKNOWN', 'CODEX_RESULT_EVIDENCE_UNKNOWN',
             'UNKNOWN', None, None),
            ('DEVELOPER_OUTCOME_UNKNOWN', 'CODEX_RESULT_PERSISTENCE_REJECTED',
             'UNKNOWN', None, None),
            ('DEVELOPER_OUTPUT_REJECTED', 'DEVELOPER_OUTPUT_REJECTED',
             None, None, None),
        )
        for state, error_code, execution_status, result_artifact, usage in outcomes:
            with self.subTest(state=state, error_code=error_code):
                task = self._codex_dispatch_task()
                self.store.create(task)
                pending = self._developer_assignment(provider='codex', max_tokens=None)
                pending['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
                self.store.compare_and_update(
                    task['task_id'], expected={'developer': None}, developer=pending)
                dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                    'provider_type': 'codex-cli', 'model': 'gpt-6-luna'}
                self.store.update(task['task_id'], developer=dispatching)
                outcome = {**dispatching, 'state': state,
                    'error_code': error_code, 'artifact': result_artifact,
                    'artifact_sha256': (hashlib.sha256(result_artifact.encode()).hexdigest()
                                        if result_artifact is not None else None),
                    'usage': usage,
                    'blocked_reason': 'A bounded reason belongs only to CODEX_BLOCKED.'}
                if execution_status is not None:
                    outcome['execution'] = {'status': execution_status,
                        'native_session_id': '12345678-1234-4234-8234-123456789abc',
                        'process_exit_code': None,
                        'stdout_sha256': 'd' * 64, 'elapsed_ms': 25,
                        'cleanup_status': 'CONFIRMED', 'usage_source': 'UNKNOWN',
                        'source_bytes': None, 'source_file_count': None,
                        'source_manifest_sha256': None}
                with self.assertRaises(LLMToolError):
                    self.store.update(task['task_id'], developer=outcome)

    def test_rejection_detail_is_restricted_to_rejected_output_and_known_shape(self):
        def persist(detail):
            task = self._codex_dispatch_task()
            self.store.create(task)
            pending = self._developer_assignment(provider='codex', max_tokens=None)
            pending['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
            self.store.compare_and_update(
                task['task_id'], expected={'developer': None}, developer=pending)
            dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                'provider_type': 'codex-cli', 'model': 'gpt-6-luna'}
            self.store.update(task['task_id'], developer=dispatching)
            outcome = {**dispatching, 'state': 'DEVELOPER_OUTPUT_REJECTED',
                'error_code': 'DEVELOPER_OUTPUT_INVALID_DIFF', 'artifact': None,
                'artifact_sha256': None, 'usage': None,
                'rejection_detail': detail}
            return task, outcome

        task, outcome = persist({'reason': 'HUNK_HEADER_INVALID', 'hunk': 0})
        self.store.update(task['task_id'], developer=outcome)
        self.assertEqual(self.store.get(task['task_id'])['developer']['rejection_detail'],
                         {'reason': 'HUNK_HEADER_INVALID', 'hunk': 0})

        invalid_details = (
            {'reason': 'NOT_A_REASON', 'hunk': 0},
            {'reason': 'HUNK_COUNTS_RECOUNTED', 'hunk': 0},
            {'reason': 'STRICT_PARSE', 'hunk': -1},
            {'reason': 'STRICT_PARSE', 'hunk': True},
            {'reason': 'STRICT_PARSE', 'hunk': None, 'extra': 'value'},
        )
        for detail in invalid_details:
            task, outcome = persist(detail)
            with self.subTest(detail=detail), self.assertRaises(LLMToolError):
                self.store.update(task['task_id'], developer=outcome)

        task, outcome = persist({'reason': 'STRICT_PARSE', 'hunk': None})
        outcome['state'] = 'DEVELOPER_OUTCOME_UNKNOWN'
        outcome['error_code'] = 'CODEX_TIMEOUT'
        outcome['execution'] = {'status': 'UNKNOWN',
            'native_session_id': '12345678-1234-4234-8234-123456789abc',
            'process_exit_code': None, 'stdout_sha256': 'd' * 64,
            'elapsed_ms': 25, 'cleanup_status': 'CONFIRMED',
            'usage_source': 'UNKNOWN', 'source_bytes': None,
            'source_file_count': None, 'source_manifest_sha256': None}
        with self.assertRaises(LLMToolError):
            self.store.update(task['task_id'], developer=outcome)

    def test_codex_delegate_screens_blocked_reason_before_persisting(self):
        self._allow_codex_dispatch()
        root = str(Path.cwd())
        snapshot = SimpleNamespace(root=root, common_dir='/registered/common',
                                   branch='task-branch', head='a' * 40)
        registration = SimpleNamespace(
            task_id='executor-task', trusted_agent_id='agent',
            session_ref='session-ref', native_session_id=None, root=root,
            common_dir=snapshot.common_dir, branch=snapshot.branch,
            env2_pass_marker='pass')
        args = SimpleNamespace(
            task_id=None, selection_source='USER', context_stdin=False,
            executor_task='executor-task', read_path=['src.py'],
            write_path=['src.py'], acceptance=['the outcome is recorded'],
            egress_basis='Approved non-sensitive test context.')

        cases = (
            ('bad\x01reason', 'CODEX_BLOCKED', None, 'BLOCKED', False),
            ('api_key=secret-value', 'CODEX_BLOCKED', None, 'BLOCKED', False),
            ('The requested change cannot be made safely.', 'CODEX_BLOCKED',
             'The requested change cannot be made safely.', 'BLOCKED', False),
            ('The requested change cannot be made safely.',
             'CODEX_RESULT_EVIDENCE_UNKNOWN', None, 'UNKNOWN', True),
        )
        for reason, expected_code, expected_reason, expected_execution, evidence_failure in cases:
            with self.subTest(reason=reason[:20]):
                task = self._codex_dispatch_task()
                self.store.create(task)
                args.task_id = task['task_id']
                result = SimpleNamespace(
                    status='BLOCKED', blocked_reason=reason,
                    thread_id='12345678-1234-4234-8234-123456789abc',
                    exit_code=0, stdout_sha256='d' * 64, elapsed_ms=25,
                    cleanup_status='CONFIRMED', usage=None)
                with patch.object(task_submission_cli_module.task_completion,
                                  'validate_task_binding'), \
                        patch.object(task_submission_cli_module.task_completion,
                                     'collect_git_snapshot', return_value=snapshot), \
                        patch.object(task_submission_cli_module.task_completion,
                                     'bind_executor_task', return_value=(registration, snapshot)), \
                        patch.object(task_submission_cli_module.task_completion,
                                     'validate_env2_pass_marker'), \
                        patch.object(task_submission_cli_module.task_completion,
                                     'read_d6_registration',
                                     side_effect=(RuntimeError('evidence unavailable')
                                                  if evidence_failure else None),
                                     return_value=registration), \
                        patch.object(task_submission_cli_module.task_completion,
                                     '_git', return_value=b''), \
                        patch.object(task_submission_cli_module,
                                     '_bootstrap_registered_d6'), \
                        patch.object(task_submission_cli_module,
                                     '_ignored_file_fingerprints', return_value={}), \
                        patch.object(task_submission_cli_module,
                                     '_codex_developer_profile',
                                     return_value=({'model': 'gpt-6-luna'}, 'high')), \
                        patch.object(task_submission_cli_module,
                                     '_ignored_output_rejection', return_value=None), \
                        patch.object(codex_direct_developer, 'run', return_value=result), \
                        patch.object(task_submission_cli_module.dataclasses, 'replace',
                                     side_effect=lambda value, **changes: value):
                    task_submission_cli_module.run_task_codex_delegate(args, store=self.store)

                saved = self.store.get(task['task_id'])['developer']
                self.assertEqual(saved['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
                self.assertEqual(saved['error_code'], expected_code)
                self.assertEqual(saved['execution']['status'], expected_execution)
                self.assertEqual(saved['execution']['process_exit_code'], 0)
                self.assertEqual(saved['execution']['native_session_id'], result.thread_id)
                self.assertIsNone(saved['usage'])
                self.assertEqual(saved.get('blocked_reason'), expected_reason)

    def test_codex_blocked_result_persistence_rejection_drops_blocked_reason(self):
        self._allow_codex_dispatch()
        root = str(Path.cwd())
        snapshot = SimpleNamespace(root=root, common_dir='/registered/common',
                                   branch='task-branch', head='a' * 40)
        registration = SimpleNamespace(
            task_id='executor-task', trusted_agent_id='agent',
            session_ref='session-ref', native_session_id=None, root=root,
            common_dir=snapshot.common_dir, branch=snapshot.branch,
            env2_pass_marker='pass')
        task = self._codex_dispatch_task()
        self.store.create(task)
        args = SimpleNamespace(
            task_id=task['task_id'], selection_source='USER', context_stdin=False,
            executor_task='executor-task', read_path=['src.py'],
            write_path=['src.py'], acceptance=['the outcome is recorded'],
            egress_basis='Approved non-sensitive test context.')
        result = SimpleNamespace(
            status='BLOCKED', blocked_reason='The requested change cannot be made safely.',
            thread_id='12345678-1234-4234-8234-123456789abc',
            exit_code=0, stdout_sha256='d' * 64, elapsed_ms=25,
            cleanup_status='CONFIRMED', usage=None)
        original_update = self.store.update
        rejected = False

        def reject_first_blocked_result(task_id, **changes):
            nonlocal rejected
            developer = changes.get('developer')
            if (not rejected and type(developer) is dict and
                    developer.get('error_code') == 'CODEX_BLOCKED'):
                rejected = True
                raise TaskStoreConflict('simulated first result persistence rejection')
            return original_update(task_id, **changes)

        with patch.object(task_submission_cli_module.task_completion,
                          'validate_task_binding'), \
                patch.object(task_submission_cli_module.task_completion,
                             'collect_git_snapshot', return_value=snapshot), \
                patch.object(task_submission_cli_module.task_completion,
                             'bind_executor_task', return_value=(registration, snapshot)), \
                patch.object(task_submission_cli_module.task_completion,
                             'validate_env2_pass_marker'), \
                patch.object(task_submission_cli_module.task_completion,
                             'read_d6_registration', return_value=registration), \
                patch.object(task_submission_cli_module.task_completion,
                             '_git', return_value=b''), \
                patch.object(task_submission_cli_module, '_bootstrap_registered_d6'), \
                patch.object(task_submission_cli_module,
                             '_ignored_file_fingerprints', return_value={}), \
                patch.object(task_submission_cli_module, '_codex_developer_profile',
                             return_value=({'model': 'gpt-6-luna'}, 'high')), \
                patch.object(task_submission_cli_module,
                             '_ignored_output_rejection', return_value=None), \
                patch.object(codex_direct_developer, 'run', return_value=result), \
                patch.object(task_submission_cli_module.dataclasses, 'replace',
                             side_effect=lambda value, **changes: value), \
                patch.object(self.store, 'update', side_effect=reject_first_blocked_result):
            task_submission_cli_module.run_task_codex_delegate(args, store=self.store)

        saved = self.store.get(task['task_id'])['developer']
        self.assertTrue(rejected)
        self.assertEqual(saved['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(saved['error_code'], 'CODEX_RESULT_PERSISTENCE_REJECTED')
        self.assertNotIn('blocked_reason', saved)

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

    def test_get_keeps_record_descriptor_open_for_path_stat(self):
        value = record()
        self.store.create(value)
        expected = self.store.get(value['task_id'])
        name = value['task_id'] + '.json'
        original_open, original_stat, original_fstat = os.open, os.stat, os.fstat
        record_fds = []
        checked = []

        def remember_open(path, flags, *args, **kwargs):
            fd = original_open(path, flags, *args, **kwargs)
            if path == name:
                record_fds.append(fd)
            return fd

        def check_open_during_stat(path, *args, **kwargs):
            if path == name:
                self.assertTrue(record_fds)
                original_fstat(record_fds[-1])
                checked.append(True)
            return original_stat(path, *args, **kwargs)

        with patch.object(task_store_module.os, 'open', side_effect=remember_open), \
                patch.object(task_store_module.os, 'stat', side_effect=check_open_during_stat):
            self.assertEqual(self.store.get(value['task_id']), expected)

        self.assertEqual(checked, [True])
        with self.assertRaises(OSError):
            original_fstat(record_fds[-1])

    def test_get_survives_inode_reuse_during_path_stat(self):
        value = record()
        self.store.create(value)
        other_store = TaskStore(self.root)
        name = value['task_id'] + '.json'
        original_stat = os.stat
        injected = [False]
        updates = []

        def replace_twice_before_path_stat(path, *args, **kwargs):
            if path == name and not injected[0]:
                injected[0] = True
                updates.append(other_store.update(
                    value['task_id'], state='PM_UNAVAILABLE',
                    error_code='PROVIDER_TIMEOUT'))
                updates.append(other_store.update(
                    value['task_id'], error_code='PROVIDER_TIMEOUT'))
            return original_stat(path, *args, **kwargs)

        with patch.object(task_store_module.os, 'stat',
                          side_effect=replace_twice_before_path_stat):
            loaded = self.store.get(value['task_id'])

        self.assertEqual(len(updates), 2)
        self.assertEqual(loaded, updates[-1])

    def test_get_retries_when_atomic_replace_lands_after_open(self):
        value = record()
        self.store.create(value)
        original_stat = os.stat
        replacing = [False]
        replaced = [False]
        updated = []

        def replace_before_path_stat(path, *args, **kwargs):
            if (path == value['task_id'] + '.json' and not replacing[0] and
                    not replaced[0]):
                replaced[0] = True
                replacing[0] = True
                try:
                    updated.append(self.store.update(
                        value['task_id'], state='PM_UNAVAILABLE',
                        error_code='PROVIDER_TIMEOUT'))
                finally:
                    replacing[0] = False
            return original_stat(path, *args, **kwargs)

        with patch.object(task_store_module.os, 'stat', side_effect=replace_before_path_stat):
            loaded = self.store.get(value['task_id'])

        self.assertTrue(replaced[0])
        self.assertEqual(loaded, updated[0])

    def test_get_reports_busy_when_path_keeps_changing(self):
        value = record()
        self.store.create(value)
        original_stat = os.stat
        replacing = [False]
        replacements = []

        def replace_before_path_stat(path, *args, **kwargs):
            if path == value['task_id'] + '.json' and not replacing[0]:
                replacing[0] = True
                try:
                    replacements.append(self.store.update(
                        value['task_id'], state='PM_UNAVAILABLE',
                        error_code='PROVIDER_TIMEOUT'))
                finally:
                    replacing[0] = False
            return original_stat(path, *args, **kwargs)

        with patch.object(task_store_module.os, 'stat', side_effect=replace_before_path_stat):
            with self.assertRaises(LLMToolError) as caught:
                self.store.get(value['task_id'])

        self.assertEqual(len(replacements), 3)
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_BUSY')

    def test_get_rejects_self_inconsistent_open_descriptor(self):
        value = record()
        self.store.create(value)
        path = self.root / (value['task_id'] + '.json')
        target_inode = path.stat().st_ino
        original_fstat = os.fstat
        task_fd_calls = [0]

        def inconsistent_fstat(fd):
            info = original_fstat(fd)
            if info.st_ino == target_inode:
                task_fd_calls[0] += 1
                if task_fd_calls[0] == 2:
                    return SimpleNamespace(
                        st_dev=info.st_dev, st_ino=info.st_ino, st_mode=info.st_mode,
                        st_uid=info.st_uid, st_gid=info.st_gid, st_size=info.st_size + 1,
                        st_mtime_ns=info.st_mtime_ns, st_nlink=info.st_nlink)
            return info

        with patch.object(task_store_module.os, 'fstat', side_effect=inconsistent_fstat):
            with self.assertRaises(LLMToolError) as caught:
                self.store.get(value['task_id'])

        self.assertEqual(task_fd_calls[0], 2)
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_INVALID')

    def test_atomic_update_preserves_integrity_and_retains_lock_inode(self):
        value = record()
        self.store.create(value)
        changed = self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                    error_code='PROVIDER_TIMEOUT')
        self.assertEqual(changed['state'], 'PM_UNAVAILABLE')
        self.assertEqual(changed['error_code'], 'PROVIDER_TIMEOUT')
        self.assertEqual(self.store.get(value['task_id']), changed)
        lock = self.root / ('.' + value['task_id'] + '.json.lock')
        self.assertTrue(lock.is_file())
        self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)
        self.assertEqual(lock.stat().st_nlink, 1)

    def test_failed_atomic_replace_keeps_previous_record_and_releases_lock(self):
        value = record()
        self.store.create(value)
        previous = self.store.get(value['task_id'])
        with patch('llmtool.task_store.os.replace', side_effect=OSError('private detail')):
            with self.assertRaises(LLMToolError) as caught:
                self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                  error_code='PROVIDER_TIMEOUT')
        self.assertEqual(self.store.get(value['task_id']), previous)
        self.assertEqual(caught.exception.task_store_code, 'UNAVAILABLE')
        lock = self.root / ('.' + value['task_id'] + '.json.lock')
        self.assertTrue(lock.is_file())
        self.assertFalse(any(path.name.endswith('.tmp') for path in self.root.iterdir()))

    def test_transient_atomic_replace_contention_retries_update(self):
        value = record()
        self.store.create(value)
        original_replace = os.replace
        attempts = []

        def contend_once(source, destination, **kwargs):
            attempts.append((source, destination))
            if len(attempts) == 1:
                raise OSError(errno.EBUSY, 'transient filesystem contention')
            return original_replace(source, destination, **kwargs)

        with patch.object(task_store_module.os, 'replace', side_effect=contend_once):
            updated = self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                        error_code='PROVIDER_TIMEOUT')

        self.assertEqual(len(attempts), 2)
        self.assertEqual(updated['state'], 'PM_UNAVAILABLE')
        self.assertEqual(self.store.get(value['task_id']), updated)

    def _start_lock_holder(self, lock_path):
        script = (
            'import fcntl, os, sys; '
            'fd=os.open(sys.argv[1], os.O_RDWR|os.O_CREAT, 0o600); '
            'fcntl.flock(fd, fcntl.LOCK_EX); print("LOCKED", flush=True); '
            'sys.stdin.readline(); fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)'
        )
        process = subprocess.Popen(
            [sys.executable, '-B', '-c', script, str(lock_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True)
        self.assertEqual(process.stdout.readline().strip(), 'LOCKED')
        self.addCleanup(self._stop_process, process)
        return process

    @staticmethod
    def _stop_process(process):
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()

    def _writer_env(self):
        env = os.environ.copy()
        package_root = str(Path(__file__).resolve().parents[1])
        env['PYTHONPATH'] = package_root + os.pathsep + env.get('PYTHONPATH', '')
        return env

    @staticmethod
    def _readline_timeout(process, timeout=5):
        ready, _, _ = select.select([process.stdout], [], [], timeout)
        if not ready:
            return None
        return process.stdout.readline().strip()

    def test_sigkill_releases_lock_and_next_update_succeeds(self):
        value = record()
        self.store.create(value)
        lock = self.root / ('.' + value['task_id'] + '.json.lock')
        holder = self._start_lock_holder(lock)
        holder.kill()
        self.assertEqual(holder.wait(timeout=5), -signal.SIGKILL)

        changed = self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                    error_code='PROVIDER_TIMEOUT')
        self.assertEqual(changed['state'], 'PM_UNAVAILABLE')
        self.assertEqual(changed['error_code'], 'PROVIDER_TIMEOUT')
        self.assertTrue(lock.is_file())

    def test_two_process_updates_wait_for_lock_and_both_succeed(self):
        value = record()
        self.store.create(value)
        lock = self.root / ('.' + value['task_id'] + '.json.lock')
        holder = self._start_lock_holder(lock)
        script = (
            'import fcntl, sys\n'
            'from llmtool import task_store\n'
            'from llmtool.task_store import TaskStore\n'
            'real_flock = fcntl.flock\n'
            'def observed_flock(fd, operation):\n'
            '    try:\n'
            '        return real_flock(fd, operation)\n'
            '    except BlockingIOError:\n'
            '        print("WAITING", flush=True)\n'
            '        raise\n'
            'task_store.fcntl.flock = observed_flock\n'
            # A stalled parent must not turn the waiter into BUSY; that path
            # has its own test.
            'task_store.TASK_UPDATE_LOCK_TIMEOUT_SECONDS = 30\n'
            'print("READY", flush=True)\n'
            'TaskStore(sys.argv[1]).update(sys.argv[2], state="PM_UNAVAILABLE", '
            'error_code="PROVIDER_TIMEOUT")\n'
            'print("OK", flush=True)\n'
        )
        writers = [subprocess.Popen(
            [sys.executable, '-B', '-c', script, str(self.root), value['task_id']],
            env=self._writer_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True) for _ in range(2)]
        for writer in writers:
            self.addCleanup(self._stop_process, writer)
        try:
            for writer in writers:
                self.assertEqual(self._readline_timeout(writer), 'READY')
            for writer in writers:
                self.assertEqual(self._readline_timeout(writer), 'WAITING')
            self.assertTrue(all(writer.poll() is None for writer in writers))
        finally:
            holder.stdin.write('release\n')
            holder.stdin.flush()
            self.assertEqual(holder.wait(timeout=5), 0)
        results = [writer.communicate(timeout=5) for writer in writers]
        self.assertTrue(all(writer.returncode == 0 for writer in writers), results)
        self.assertTrue(all(stdout.strip().endswith('OK') for stdout, _ in results), results)
        saved = self.store.get(value['task_id'])
        self.assertEqual(saved['state'], 'PM_UNAVAILABLE')
        self.assertEqual(saved['error_code'], 'PROVIDER_TIMEOUT')

    def test_concurrent_process_cas_keeps_real_conflict(self):
        value = record()
        self.store.create(value)
        lock_name = '.' + value['task_id'] + '.json.lock'
        lock_path = self.root / lock_name
        self.assertFalse(lock_path.exists())
        barrier = Path(self.temp.name) / 'first-lock-barrier'
        barrier.mkdir(mode=0o700)
        start = barrier / 'start'
        open_release = barrier / 'open-release'
        owner_release = barrier / 'owner-release'
        script = (
            'import fcntl, os, sys, time\n'
            'from llmtool import task_store\n'
            'from llmtool.task_store import TaskStore, TaskStoreConflict\n'
            'root, task_id, lock_name, start, open_release, owner_release = sys.argv[1:]\n'
            'print("READY", flush=True)\n'
            'while not os.path.exists(start): time.sleep(0.005)\n'
            'real_open = os.open\n'
            'def synchronized_open(path, flags, *args, **kwargs):\n'
            '    fd = real_open(path, flags, *args, **kwargs)\n'
            '    if path == lock_name:\n'
            '        inode = os.fstat(fd).st_ino\n'
            '        print("OPENED", inode, flush=True)\n'
            '        while not os.path.exists(open_release): time.sleep(0.005)\n'
            '    return fd\n'
            'os.open = synchronized_open\n'
            'real_flock = fcntl.flock\n'
            'first_acquisition = [True]\n'
            'def observed_flock(fd, operation):\n'
            '    try:\n'
            '        result = real_flock(fd, operation)\n'
            '    except BlockingIOError:\n'
            '        print("WAITING", flush=True)\n'
            '        raise\n'
            '    if first_acquisition[0]:\n'
            '        first_acquisition[0] = False\n'
            '        print("ACQUIRED", flush=True)\n'
            '        while not os.path.exists(owner_release): time.sleep(0.005)\n'
            '    return result\n'
            'task_store.fcntl.flock = observed_flock\n'
            # A stalled parent must not turn the waiter into BUSY; that path
            # has its own test.
            'task_store.TASK_UPDATE_LOCK_TIMEOUT_SECONDS = 30\n'
            'try:\n'
            '    TaskStore(root).compare_and_update(task_id, expected={"state": "PM_PENDING"}, '
            'state="PM_UNAVAILABLE", error_code="PROVIDER_TIMEOUT")\n'
            'except TaskStoreConflict as exc:\n'
            '    print("CONFLICT", exc.task_store_code, flush=True)\n'
            '    sys.exit(3)\n'
            'print("UPDATED", flush=True)\n'
        )
        writers = [subprocess.Popen(
            [sys.executable, '-B', '-c', script, str(self.root), value['task_id'],
             lock_name, str(start), str(open_release), str(owner_release)],
            env=self._writer_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True) for _ in range(2)]
        for writer in writers:
            self.addCleanup(self._stop_process, writer)
        try:
            for writer in writers:
                self.assertEqual(self._readline_timeout(writer), 'READY')
            start.touch(mode=0o600)
            inodes = []
            for writer in writers:
                line = self._readline_timeout(writer)
                self.assertIsNotNone(line)
                fields = line.split()
                self.assertEqual(fields[0], 'OPENED', line)
                inodes.append(int(fields[1]))
            self.assertEqual(inodes[0], inodes[1])
            self.assertEqual(lock_path.stat().st_ino, inodes[0])
            open_release.touch(mode=0o600)
            states = [self._readline_timeout(writer) for writer in writers]
            self.assertEqual(sorted(states), ['ACQUIRED', 'WAITING'], states)
        finally:
            open_release.touch(mode=0o600)
            owner_release.touch(mode=0o600)
        results = [writer.communicate(timeout=5) for writer in writers]
        self.assertEqual(sorted(writer.returncode for writer in writers), [0, 3], results)
        self.assertTrue(any('CONFLICT' in stdout for stdout, _ in results), results)
        saved = self.store.get(value['task_id'])
        self.assertEqual(saved['state'], 'PM_UNAVAILABLE')

    def test_lock_wait_timeout_is_bounded_and_classified_busy(self):
        value = record()
        self.store.create(value)
        lock = self.root / ('.' + value['task_id'] + '.json.lock')
        holder = self._start_lock_holder(lock)
        started = time.monotonic()
        with patch.object(task_store_module, 'TASK_UPDATE_LOCK_TIMEOUT_SECONDS', 0.1):
            with self.assertRaises(LLMToolError) as caught:
                self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                  error_code='PROVIDER_TIMEOUT')
        elapsed = time.monotonic() - started
        self.assertEqual(caught.exception.task_store_code, 'BUSY')
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_BUSY')
        self.assertGreaterEqual(elapsed, 0.08)
        self.assertLess(elapsed, 1.0)
        holder.stdin.write('release\n')
        holder.stdin.flush()
        self.assertEqual(holder.wait(timeout=5), 0)

    def test_transient_flock_enolck_retries_update(self):
        value = record()
        self.store.create(value)
        original_flock = fcntl.flock
        attempts = []

        def contend_once(fd, operation):
            if operation == fcntl.LOCK_EX | fcntl.LOCK_NB:
                attempts.append(operation)
                if len(attempts) == 1:
                    raise OSError(errno.ENOLCK, 'transient lock table contention')
            return original_flock(fd, operation)

        with patch.object(task_store_module.fcntl, 'flock', side_effect=contend_once):
            updated = self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                        error_code='PROVIDER_TIMEOUT')

        self.assertEqual(len(attempts), 2)
        self.assertEqual(updated['state'], 'PM_UNAVAILABLE')
        self.assertEqual(updated['error_code'], 'PROVIDER_TIMEOUT')
        self.assertEqual(self.store.get(value['task_id']), updated)

    def test_concurrent_lock_create_enoent_is_retried(self):
        # macOS openat(O_CREAT) may report ENOENT while another process
        # creates the same lock name; the next attempt opens that file.
        value = record()
        self.store.create(value)
        lock_name = '.' + value['task_id'] + '.json.lock'
        original_open = os.open
        attempts = []

        def lose_first_create(path, flags, *args, **kwargs):
            if path == lock_name:
                attempts.append(flags)
                if len(attempts) == 1:
                    raise FileNotFoundError(errno.ENOENT, 'private filesystem detail')
            return original_open(path, flags, *args, **kwargs)

        with patch.object(task_store_module.os, 'open', side_effect=lose_first_create):
            changed = self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                        error_code='PROVIDER_TIMEOUT')
        self.assertEqual(changed['state'], 'PM_UNAVAILABLE')
        self.assertEqual(len(attempts), 2)
        self.assertTrue(all(flags & os.O_CREAT and flags & os.O_NOFOLLOW for flags in attempts))

    def test_persistent_lock_create_enoent_is_bounded_and_unavailable(self):
        value = record()
        self.store.create(value)
        lock_name = '.' + value['task_id'] + '.json.lock'
        original_open = os.open
        attempts = []

        def always_enoent(path, flags, *args, **kwargs):
            if path == lock_name:
                attempts.append(path)
                raise FileNotFoundError(errno.ENOENT, 'private filesystem detail')
            return original_open(path, flags, *args, **kwargs)

        started = time.monotonic()
        with patch.object(task_store_module, 'TASK_UPDATE_LOCK_TIMEOUT_SECONDS', 0.1), \
                patch.object(task_store_module.os, 'open', side_effect=always_enoent):
            with self.assertRaises(LLMToolError) as caught:
                self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                  error_code='PROVIDER_TIMEOUT')
        elapsed = time.monotonic() - started
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_UNAVAILABLE')
        self.assertNotIn('private filesystem detail', str(caught.exception))
        self.assertGreater(len(attempts), 1)
        self.assertGreaterEqual(elapsed, 0.08)
        self.assertLess(elapsed, 1.0)
        self.assertEqual(self.store.get(value['task_id'])['state'], value['state'])

    def test_planted_lock_paths_are_refused_without_touching_them(self):
        value = record()
        self.store.create(value)
        lock = self.root / ('.' + value['task_id'] + '.json.lock')
        outside = Path(self.temp.name) / 'outside-target'

        def symlink():
            outside.write_bytes(b'outside')
            outside.chmod(0o600)
            lock.symlink_to(outside)

        def dangling():
            lock.symlink_to(Path(self.temp.name) / 'missing-target')

        def hardlink():
            outside.write_bytes(b'outside')
            outside.chmod(0o600)
            os.link(outside, lock)

        def loose_mode():
            lock.write_bytes(b'')
            lock.chmod(0o644)

        def fifo():
            os.mkfifo(lock, 0o600)

        def directory():
            lock.mkdir(mode=0o700)

        cases = (('symlink', symlink), ('dangling symlink', dangling), ('hardlink', hardlink),
                 ('mode 0644', loose_mode), ('fifo', fifo), ('directory', directory))
        for label, plant in cases:
            with self.subTest(label):
                plant()
                before = self.store.get(value['task_id'])
                started = time.monotonic()
                with self.assertRaises(LLMToolError) as caught:
                    self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                      error_code='PROVIDER_TIMEOUT')
                self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_INVALID')
                self.assertLess(time.monotonic() - started, 1.0)
                self.assertEqual(self.store.get(value['task_id']), before)
                self.assertFalse((Path(self.temp.name) / 'missing-target').exists())
                if outside.exists():
                    self.assertEqual(outside.read_bytes(), b'outside')
                    self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o600)
                if lock.is_dir() and not lock.is_symlink():
                    lock.rmdir()
                else:
                    lock.unlink()
                if outside.exists():
                    outside.unlink()

    def test_lock_owned_by_another_user_is_refused(self):
        value = record()
        self.store.create(value)
        lock_name = '.' + value['task_id'] + '.json.lock'
        original_open, original_fstat = os.open, os.fstat
        lock_fd = []

        def remember(path, flags, *args, **kwargs):
            fd = original_open(path, flags, *args, **kwargs)
            if path == lock_name:
                lock_fd.append(fd)
            return fd

        def foreign_owner(fd):
            info = original_fstat(fd)
            if lock_fd and fd == lock_fd[0]:
                fields = list(info)
                fields[stat.ST_UID] = os.getuid() + 1
                return os.stat_result(fields)
            return info

        with patch.object(task_store_module.os, 'open', side_effect=remember), \
                patch.object(task_store_module.os, 'fstat', side_effect=foreign_owner):
            with self.assertRaises(LLMToolError) as caught:
                self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                  error_code='PROVIDER_TIMEOUT')
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_INVALID')
        self.assertNotEqual(self.store.get(value['task_id'])['state'], 'PM_UNAVAILABLE')

    def test_lock_open_and_fstat_errors_are_safe_and_close_descriptors(self):
        value = record()
        self.store.create(value)
        lock_name = '.' + value['task_id'] + '.json.lock'
        original_open = os.open
        original_fstat = os.fstat

        def fail_lock_open(path, flags, *args, **kwargs):
            if path == lock_name:
                raise OSError(errno.EIO, 'private filesystem detail')
            return original_open(path, flags, *args, **kwargs)

        with patch.object(task_store_module.os, 'open', side_effect=fail_lock_open):
            with self.assertRaises(LLMToolError) as caught:
                self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                  error_code='PROVIDER_TIMEOUT')
        self.assertEqual(caught.exception.task_store_code, 'UNAVAILABLE')
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_UNAVAILABLE')
        self.assertNotIn('private filesystem detail', str(caught.exception))

        lock_fd = []

        def remember_lock_open(path, flags, *args, **kwargs):
            fd = original_open(path, flags, *args, **kwargs)
            if path == lock_name:
                lock_fd.append(fd)
            return fd

        def fail_lock_fstat(fd):
            if lock_fd and fd == lock_fd[0]:
                raise OSError(errno.EIO, 'private stat detail')
            return original_fstat(fd)

        with patch.object(task_store_module.os, 'open', side_effect=remember_lock_open), \
                patch.object(task_store_module.os, 'fstat', side_effect=fail_lock_fstat):
            with self.assertRaises(LLMToolError) as caught:
                self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                  error_code='PROVIDER_TIMEOUT')
        self.assertEqual(caught.exception.task_store_code, 'UNAVAILABLE')
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_UNAVAILABLE')
        self.assertNotIn('private stat detail', str(caught.exception))
        self.assertEqual(len(lock_fd), 1)
        with self.assertRaises(OSError) as closed:
            original_fstat(lock_fd[0])
        self.assertEqual(closed.exception.errno, errno.EBADF)

    def test_task_store_failures_expose_safe_distinct_categories(self):
        value = record()
        self.store.create(value)
        self.store.compare_and_update(
            value['task_id'], expected={'state': 'PM_PENDING'},
            state='PM_UNAVAILABLE', error_code='PROVIDER_TIMEOUT')
        with self.assertRaises(TaskStoreConflict) as caught:
            self.store.compare_and_update(
                value['task_id'], expected={'state': 'PM_PENDING'},
                state='PM_UNAVAILABLE', error_code='PROVIDER_TIMEOUT')
        self.assertEqual(caught.exception.task_store_code, 'CONFLICT')
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_CONFLICT')

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

    def _active_pm_record(self, route='opencode'):
        value = record()
        response = json.dumps({
            'schema_version': 1, 'summary': 'Ready.',
            'developer_assignment': {
                'route': route, 'risk': 'LOW', 'goal': 'Do task.',
                'acceptance': ['A test passes.'],
            },
        }, separators=(',', ':'))
        value.update(
            schema_version=2,
            pm_provider_profile='active-pm-runtime',
            pm_provider_type='active-pm-runtime',
            pm_model=None,
            pm_route_binding={'type': 'active-pm-runtime', 'model': None, 'base_url': None},
            backend_session_semantics='ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL',
            state='PM_RESPONDED',
            response=response,
            error_code=None,
            developer=None,
            pm_assignment={
                'schema_version': 1, 'route': route, 'risk': 'LOW',
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

    def test_active_codex_pm_session_binding_is_validated_and_round_trips(self):
        value = self._active_pm_record()
        native = str(uuid.uuid4())
        value['pm_session_id'] = 'pm-' + native
        value['pm_provenance'].update(
            pm_runtime='codex', pm_native_session_id=native,
            pm_cwd='/tmp/codex-pm-task',
            native_session_id_status='LOCAL_SESSION_FILE_OBSERVED')
        created = self.store.create(value)
        self.assertEqual(self.store.get(value['task_id']), created)
        self.assertEqual(created['pm_provenance']['pm_runtime'], 'codex')
        invalid = self._active_pm_record()
        invalid['pm_session_id'] = 'pm-' + native
        invalid['pm_provenance'].update(
            pm_runtime='codex', pm_native_session_id=native,
            pm_cwd='relative/path',
            native_session_id_status='LOCAL_SESSION_FILE_OBSERVED')
        with self.assertRaises(LLMToolError):
            TaskStore(self.root / 'invalid-codex-binding').create(invalid)

    def test_active_pm_company_gateway_assignment_is_validated_and_round_trips(self):
        value = self._active_pm_record(route='company-gw')
        created = self.store.create(value)
        self.assertEqual(self.store.get(value['task_id']), created)
        self.assertEqual(created['pm_assignment']['route'], 'company-gw')

    def test_legacy_codex_pm_runtime_records_remain_readable(self):
        value = self._active_pm_record()
        value['pm_provider_type'] = 'codex-cli'
        value['pm_route_binding'] = {
            'type': 'codex-cli', 'model': None, 'base_url': None}
        created = self.store.create(value)
        self.assertEqual(self.store.get(value['task_id']), created)
        self.assertEqual(created['pm_provider_type'], 'codex-cli')

    def test_active_pm_provenance_rejects_false_or_incomplete_claims(self):
        mutations = (
            lambda record: record['pm_provenance'].__setitem__('provider_invoked', True),
            lambda record: record['pm_provenance'].__setitem__('session_restored', True),
            lambda record: record['pm_provenance'].__setitem__('automatic_callback', True),
            lambda record: record.__setitem__('usage', {
                'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}),
            lambda record: record.__setitem__('pm_assignment', {
                **record['pm_assignment'], 'risk': 'OTHER'}),
            lambda record: record.__setitem__('pm_assignment', {
                **record['pm_assignment'], 'route': 'none'}),
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

    @staticmethod
    def _completion_event(value, *, event_id=None, sequence=0):
        return dict(event_id=event_id or new_id(), run_id=value['run_id'],
                    session_id='ses_Test123', source='DIRECT_D6_DEVELOPER',
                    source_sha='a' * 40, sequence=sequence,
                    payload_sha256='b' * 64)

    def test_completion_event_persists_before_wake_and_replays_idempotently(self):
        value = self._active_pm_record()
        self.store.create(value)
        args = self._completion_event(value)
        first = self.store.record_completion_event(value['task_id'], **args)
        self.assertTrue(first['created'])
        self.assertEqual(first['event']['state'], 'PENDING')
        with self.assertRaises(LLMToolError) as generic_mutation:
            self.store.update(value['task_id'], completion_events=[])
        self.assertEqual(generic_mutation.exception.task_store_error_code,
                         'COMPLETION_EVENT_MUTATION_FORBIDDEN')
        self.assertEqual(TaskStore(self.root).unacked_completion_events(
            value['task_id']), [first['event']])
        replay = TaskStore(self.root).record_completion_event(value['task_id'], **args)
        self.assertFalse(replay['created'])
        self.assertEqual(replay['event'], first['event'])
        self.assertEqual(len(self.store.get(value['task_id'])['completion_events']), 1)
        with self.assertRaises(LLMToolError) as changed_payload:
            self.store.record_completion_event(value['task_id'],
                **{**args, 'payload_sha256': 'c' * 64})
        self.assertEqual(changed_payload.exception.task_store_error_code,
                         'COMPLETION_EVENT_CONFLICT')
        with self.assertRaises(LLMToolError) as changed_id_same_sequence:
            self.store.record_completion_event(value['task_id'],
                **{**args, 'event_id': new_id()})
        self.assertEqual(changed_id_same_sequence.exception.task_store_error_code,
                         'COMPLETION_EVENT_CONFLICT')
        second = self._completion_event(value, sequence=2)
        self.store.record_completion_event(value['task_id'], **second)
        with self.assertRaises(LLMToolError) as out_of_order:
            self.store.record_completion_event(value['task_id'],
                **self._completion_event(value, sequence=1))
        self.assertEqual(out_of_order.exception.task_store_error_code,
                         'COMPLETION_EVENT_SEQUENCE_INVALID')

    def test_completion_ack_requires_persisted_pm_consumption_and_confirmed_intent(self):
        value = self._active_pm_record()
        self.store.create(value)
        args = self._completion_event(value)
        event_id = args['event_id']
        pm = value['pm_session_id']
        self.store.record_completion_event(value['task_id'], **args)
        with self.assertRaises(LLMToolError) as wrong_pm:
            self.store.consume_completion_event(value['task_id'], event_id,
                pm_session_id='pm-not-the-original', pm_decision_sha256='c' * 64)
        self.assertEqual(wrong_pm.exception.task_store_error_code,
                         'COMPLETION_EVENT_BINDING_MISMATCH')
        with self.assertRaises(LLMToolError):
            self.store.ack_completion_event(value['task_id'], event_id,
                                            pm_session_id=pm, pm_decision_sha256='c' * 64)
        consumed = self.store.consume_completion_event(
            value['task_id'], event_id, pm_session_id=pm, pm_decision_sha256='c' * 64)
        self.assertEqual(consumed['event']['state'], 'PM_CONSUMED')
        self.assertFalse(TaskStore(self.root).consume_completion_event(
            value['task_id'], event_id, pm_session_id=pm,
            pm_decision_sha256='c' * 64)['created'])
        intent_id = new_id()
        intent = self.store.prepare_completion_dispatch_intent(
            value['task_id'], event_id, intent_id=intent_id,
            intent_sha256='d' * 64, pm_session_id=pm)
        self.assertTrue(intent['created'])
        self.assertEqual(intent['event']['dispatch_intent']['state'], 'UNKNOWN')
        with self.assertRaises(LLMToolError) as refused_replay:
            self.store.prepare_completion_dispatch_intent(
                value['task_id'], event_id, intent_id=new_id(),
                intent_sha256='e' * 64, pm_session_id=pm)
        self.assertEqual(refused_replay.exception.task_store_error_code,
                         'COMPLETION_EVENT_CONFLICT')
        self.assertFalse(TaskStore(self.root).prepare_completion_dispatch_intent(
            value['task_id'], event_id, intent_id=intent_id,
            intent_sha256='d' * 64, pm_session_id=pm)['created'])
        with self.assertRaises(LLMToolError):
            self.store.ack_completion_event(value['task_id'], event_id,
                                            pm_session_id=pm, pm_decision_sha256='c' * 64)
        self.assertEqual(TaskStore(self.root).unacked_completion_events(
            value['task_id'])[0]['dispatch_intent']['state'], 'UNKNOWN')
        confirmed = self.store.confirm_completion_dispatch(
            value['task_id'], event_id, intent_id=intent_id,
            receipt_sha256='f' * 64, pm_session_id=pm)
        self.assertEqual(confirmed['event']['dispatch_intent']['state'], 'CONFIRMED')
        self.assertFalse(self.store.confirm_completion_dispatch(
            value['task_id'], event_id, intent_id=intent_id,
            receipt_sha256='f' * 64, pm_session_id=pm)['created'])
        acked = self.store.ack_completion_event(value['task_id'], event_id,
                                                pm_session_id=pm,
                                                pm_decision_sha256='c' * 64)
        self.assertEqual(acked['event']['state'], 'ACKED')
        self.assertEqual(TaskStore(self.root).unacked_completion_events(
            value['task_id']), [])
        self.assertFalse(self.store.ack_completion_event(
            value['task_id'], event_id, pm_session_id=pm,
            pm_decision_sha256='c' * 64)['created'])

    def test_completion_persist_failures_preserve_previous_transition(self):
        value = self._active_pm_record()
        self.store.create(value)
        args = self._completion_event(value)
        pm = value['pm_session_id']
        prior = self.store.get(value['task_id'])
        with patch('llmtool.task_store.os.replace', side_effect=OSError('private detail')):
            with self.assertRaises(LLMToolError):
                self.store.record_completion_event(value['task_id'], **args)
        self.assertEqual(self.store.get(value['task_id']), prior)
        self.store.record_completion_event(value['task_id'], **args)
        self.store.consume_completion_event(value['task_id'], args['event_id'],
                                            pm_session_id=pm,
                                            pm_decision_sha256='c' * 64)
        consumed = self.store.get(value['task_id'])
        with patch('llmtool.task_store.os.replace', side_effect=OSError('private detail')):
            with self.assertRaises(LLMToolError):
                self.store.prepare_completion_dispatch_intent(
                    value['task_id'], args['event_id'],
                    intent_id=new_id(), intent_sha256='d' * 64, pm_session_id=pm)
        self.assertEqual(TaskStore(self.root).get(value['task_id']), consumed)
        intent_id = new_id()
        self.store.prepare_completion_dispatch_intent(
            value['task_id'], args['event_id'],
            intent_id=intent_id, intent_sha256='d' * 64, pm_session_id=pm)
        intent_pending = self.store.get(value['task_id'])
        with patch('llmtool.task_store.os.replace', side_effect=OSError('private detail')):
            with self.assertRaises(LLMToolError):
                self.store.confirm_completion_dispatch(
                    value['task_id'], args['event_id'], intent_id=intent_id,
                    receipt_sha256='e' * 64, pm_session_id=pm)
        self.assertEqual(TaskStore(self.root).get(value['task_id']), intent_pending)
        self.store.confirm_completion_dispatch(
            value['task_id'], args['event_id'], intent_id=intent_id,
            receipt_sha256='e' * 64, pm_session_id=pm)
        confirmed = self.store.get(value['task_id'])
        with patch('llmtool.task_store.os.replace', side_effect=OSError('private detail')):
            with self.assertRaises(LLMToolError):
                self.store.ack_completion_event(value['task_id'], args['event_id'],
                    pm_session_id=pm, pm_decision_sha256='c' * 64)
        self.assertEqual(TaskStore(self.root).get(value['task_id']), confirmed)

    def test_completion_concurrent_duplicate_reconciles_without_second_event(self):
        value = self._active_pm_record()
        self.store.create(value)
        args = self._completion_event(value)
        other = TaskStore(self.root)
        original = self.store._update
        first = True
        def concurrent_writer(*call_args, **call_kwargs):
            nonlocal first
            if first:
                first = False
                other.record_completion_event(value['task_id'], **args)
                raise TaskStoreConflict()
            return original(*call_args, **call_kwargs)
        with patch.object(self.store, '_update', side_effect=concurrent_writer):
            result = self.store.record_completion_event(value['task_id'], **args)
        self.assertFalse(result['created'])
        self.assertEqual(len(self.store.get(value['task_id'])['completion_events']), 1)

    def test_completion_identity_and_legacy_schema_fail_closed(self):
        legacy = record()
        self.store.create(legacy)
        with self.assertRaises(LLMToolError):
            self.store.record_completion_event(legacy['task_id'],
                **self._completion_event(legacy))
        value = self._active_pm_record()
        self.store.create(value)
        args = self._completion_event(value)
        with self.assertRaises(LLMToolError) as mismatch:
            self.store.record_completion_event(value['task_id'],
                **{**args, 'run_id': new_id()})
        self.assertEqual(mismatch.exception.task_store_error_code,
                         'COMPLETION_EVENT_BINDING_MISMATCH')
        with self.assertRaises(LLMToolError):
            self.store.record_completion_event(value['task_id'],
                **{**args, 'session_id': '../other'})

    def test_abandonment_is_pm_bound_validated_and_idempotent(self):
        value = self._codex_dispatch_task()
        self.store.create(value)
        pm = value['pm_session_id']
        with self.assertRaises(LLMToolError) as denied:
            self.store.abandon(value['task_id'], pm_session_id='pm-wrong', reason='Stopped')
        self.assertEqual(denied.exception.task_store_error_code, 'TASK_PM_SESSION_DENIED')
        self.assertNotIn('abandonment', self.store.get(value['task_id']))

        for reason in ('', 'x' * 501, 'line\nfeed'):
            with self.subTest(reason=reason[:20]):
                with self.assertRaises(LLMToolError) as invalid:
                    self.store.abandon(value['task_id'], pm_session_id=pm, reason=reason)
                self.assertEqual(invalid.exception.task_store_error_code,
                                 'TASK_ABANDONMENT_INVALID')
        with patch.object(task_store_module, '_emitted_sensitive', return_value=True):
            with self.assertRaises(LLMToolError) as sensitive:
                self.store.abandon(value['task_id'], pm_session_id=pm,
                                   reason='Rejected by sensitive screen')
            self.assertEqual(sensitive.exception.task_store_error_code,
                             'TASK_ABANDONMENT_INVALID')
        saved = self.store.abandon(value['task_id'], pm_session_id=pm,
                                   reason='PM stopped this Task')
        self.assertEqual(saved['abandonment']['reason'], 'PM stopped this Task')
        self.assertTrue(saved['abandonment']['abandoned_at'].endswith('Z'))
        self.assertEqual(self.store.abandon(value['task_id'], pm_session_id=pm,
                                            reason='PM stopped this Task'), saved)
        with self.assertRaises(LLMToolError) as conflict:
            self.store.abandon(value['task_id'], pm_session_id=pm,
                               reason='Different reason')
        self.assertEqual(conflict.exception.task_store_error_code,
                         'TASK_ABANDONMENT_CONFLICT')
        self.assertEqual(self.store.get(value['task_id']), saved)

    def test_create_rejects_abandonment_without_writing_record(self):
        value = record()
        value['abandonment'] = {
            'reason': 'PM stopped this Task',
            'abandoned_at': '2026-10-29T00:00:00Z',
            'pm_session_id': value['pm_session_id'],
        }

        with self.assertRaises(LLMToolError) as refused:
            self.store.create(value)

        self.assertEqual(refused.exception.task_store_error_code,
                         'TASK_ABANDONMENT_MUTATION_FORBIDDEN')
        self.assertFalse((self.root / (value['task_id'] + '.json')).exists())

    def test_abandonment_schema_requires_exact_shape(self):
        value = self._codex_dispatch_task()
        self.store.create(value)
        with self.assertRaises(LLMToolError) as invalid:
            self.store.update(value['task_id'], abandonment={
                'reason': 'Stopped', 'abandoned_at': '2026-10-29T00:00:00Z',
                'pm_session_id': value['pm_session_id'], 'extra': True,
            })
        self.assertEqual(invalid.exception.task_store_error_code,
                         'TASK_ABANDONMENT_MUTATION_FORBIDDEN')

    def test_abandonment_changes_are_restricted_to_pm_bound_path(self):
        value = self._codex_dispatch_task()
        self.store.create(value)
        original = self.store.get(value['task_id'])
        stamped = {
            'reason': 'PM stopped this Task',
            'abandoned_at': '2026-10-29T00:00:00Z',
            'pm_session_id': value['pm_session_id'],
        }
        for mutate in (
                lambda: self.store.update(value['task_id'], abandonment=stamped),
                lambda: self.store.compare_and_update(value['task_id'],
                    expected={'record_sha256': original['record_sha256']},
                    abandonment=stamped)):
            with self.assertRaises(LLMToolError) as refused:
                mutate()
            self.assertEqual(refused.exception.task_store_error_code,
                             'TASK_ABANDONMENT_MUTATION_FORBIDDEN')
            self.assertEqual(self.store.get(value['task_id']), original)

        saved = self.store.abandon(value['task_id'],
                                   pm_session_id=value['pm_session_id'],
                                   reason=stamped['reason'])
        changed = dict(saved['abandonment'], reason='Changed outside abandon')
        for mutate in (
                lambda: self.store.update(value['task_id'], abandonment=changed),
                lambda: self.store.compare_and_update(value['task_id'],
                    expected={'record_sha256': saved['record_sha256']},
                    abandonment=changed),
                lambda: self.store.update(value['task_id'], abandonment=None),
                lambda: self.store.compare_and_update(value['task_id'],
                    expected={'record_sha256': saved['record_sha256']},
                    abandonment=None)):
            with self.assertRaises(LLMToolError) as refused:
                mutate()
            self.assertEqual(refused.exception.task_store_error_code,
                             'TASK_ABANDONMENT_MUTATION_FORBIDDEN')
            self.assertEqual(self.store.get(value['task_id']), saved)

    def test_abandonment_pm_session_must_match_task_session(self):
        value = self._codex_dispatch_task()
        self.store.create(value)
        saved = self.store.abandon(value['task_id'],
                                   pm_session_id=value['pm_session_id'],
                                   reason='PM stopped this Task')
        mismatched = dict(saved)
        mismatched['abandonment'] = dict(saved['abandonment'],
                                         pm_session_id='pm-' + new_id())
        with self.assertRaises(LLMToolError) as invalid:
            task_store_module._validate(mismatched)
        self.assertEqual(invalid.exception.task_store_error_code,
                         'TASK_ABANDONMENT_INVALID')

    def test_abandonment_supports_v1_record_shape(self):
        value = record()
        self.assertEqual(value['schema_version'], 1)
        self.store.create(value)

        saved = self.store.abandon(value['task_id'],
                                   pm_session_id=value['pm_session_id'],
                                   reason='PM stopped this Task')

        self.assertEqual(saved['abandonment']['reason'], 'PM stopped this Task')
        self.assertEqual(saved['schema_version'], 1)

    def test_pm_session_adoption_fields_are_not_writable_through_generic_updates(self):
        value = self._codex_dispatch_task()
        self.store.create(value)
        original = self.store.get(value['task_id'])
        changes = {
            'pm_provenance': {'forged': True},
            'pm_session_adoptions': [{'old_session_key': original['pm_session_id'],
                'new_session_key': 'pm-' + new_id(), 'at': '2026-10-29T00:00:00Z'}],
        }
        for field, field_value in changes.items():
            for mutate in (
                    lambda field=field, field_value=field_value:
                        self.store.update(value['task_id'], **{field: field_value}),
                    lambda field=field, field_value=field_value:
                        self.store.compare_and_update(value['task_id'],
                            expected={'record_sha256': original['record_sha256']},
                            **{field: field_value})):
                with self.subTest(field=field), self.assertRaises(LLMToolError) as caught:
                    mutate()
                self.assertEqual(caught.exception.task_store_error_code,
                                 'PM_SESSION_ADOPTION_INVALID')
                self.assertEqual(self.store.get(value['task_id']), original)

    def test_pm_session_adoption_refuses_abandoned_task_under_update_lock(self):
        value = record()
        self.store.create(value)
        self.store.abandon(value['task_id'], pm_session_id=value['pm_session_id'],
                           reason='Stopped before adoption')
        current = self.store.get(value['task_id'])
        with self.assertRaises(LLMToolError) as caught:
            self.store.adopt_pm_session(
                value['task_id'], old_session_key=current['pm_session_id'],
                new_session_key='pm-' + new_id(), at='2026-10-29T00:00:00Z',
                expected_record_sha256=current['record_sha256'])
        self.assertEqual(caught.exception.task_store_error_code,
                         'PM_SESSION_ADOPTION_ABANDONED')
        self.assertEqual(self.store.get(value['task_id']), current)


if __name__ == '__main__':
    unittest.main()
