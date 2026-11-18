"""Tests for Issue #2325: test-failure and review repair unknown outcome recovery."""

import hashlib
import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from llmtool import task_advance
from llmtool.task_store import TaskStore, _json_bytes

HEAD = '1111111111111111111111111111111111111111'
HEAD2 = '2222222222222222222222222222222222222222'


def _save_task(store, record):
    target_dir = getattr(store, 'directory', getattr(store, 'root', None))
    if target_dir is None:
        target_dir = getattr(store, 'tasks_dir', None)
    if target_dir is None and hasattr(store, '_dir'):
        target_dir = getattr(store, '_dir')
    os.makedirs(target_dir, exist_ok=True)
    task_id = record['task_id']
    path = os.path.join(target_dir, f'{task_id}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(record, f, sort_keys=True)
    # Return fresh record with record_sha256 loaded by store
    return store.get(task_id)


def _make_receipt(*, task_id, run_id, executor_task, source_sha, result='FAIL'):
    receipt = {
        'task_id': task_id,
        'run_id': run_id,
        'executor_task': executor_task,
        'source_sha': source_sha,
        'result': result,
        'modules': ['tests.sample'],
        'ran_total': 1,
        'failures': ['tests.sample.test_fail'],
        'failure_excerpt': 'AssertionError: failure in sample test',
    }
    digest = hashlib.sha256(_json_bytes(receipt)).hexdigest()
    receipt['receipt_digest'] = digest
    return receipt


def _make_d6(*, root, common_dir, executor_task, head=HEAD, dirty=False):
    return {
        'root': root,
        'common_dir': common_dir,
        'task_id': executor_task,
        'branch': 'fix-2325',
        'result_head': head,
        'recorded_result_head': head,
        'registered_result_head': head,
        'dirty': dirty,
        'state': 'DIRTY' if dirty else 'CLEAN',
        'registration_state': 'ACTIVE',
        'trusted_agent_id': 'pm-deterministic',
    }


class RepairUnknownRecovery2325Tests(unittest.TestCase):
    def setUp(self):
        self.tmp_store_dir = tempfile.TemporaryDirectory()
        self.store = TaskStore(self.tmp_store_dir.name)
        self.tmp_repo_dir = tempfile.TemporaryDirectory()
        self.root = self.tmp_repo_dir.name
        self.common_dir = os.path.join(self.root, '.git')
        os.makedirs(self.common_dir, exist_ok=True)

    def tearDown(self):
        self.tmp_repo_dir.cleanup()
        self.tmp_store_dir.cleanup()

    def test_evidence_1_test_failure_repair_unknown_recovered(self):
        """PR #2323 Task 07c7358d: test-failure repair ended DEVELOPER_OUTCOME_UNKNOWN.

        When D6 is clean, head unchanged, and cleanup confirmed, task advance
        retries the repair without getting stuck indefinitely.
        """
        task_id = '07c7358d' + '0' * 24
        run_id = 'a' * 32
        child_id = '88b3c440' + '0' * 24
        executor_task = 'executor-01'

        receipt = _make_receipt(
            task_id=task_id, run_id=run_id, executor_task=executor_task, source_sha=HEAD)

        parent_record = {
            'task_id': task_id,
            'run_id': run_id,
            'pm_provenance': {'pm_native_session_id': 'pm-sess-01', 'pm_cwd': self.root},
            'pm_assignment': {'goal': 'fix remote test failure'},
            'developer': {
                'provider_profile': 'opencode',
                'state': 'DEVELOPER_RESPONDED',
                'acceptance': ['resolve failure'],
                'read_paths': ['tools/sample.py'],
                'write_paths': ['tools/sample.py'],
                'model': 'opencode/deepseek-v4.1-flash-free#high',
            },
            'completion': {
                'executor_task_id': executor_task,
                'evidence': {'common_dir': self.common_dir},
            },
            'remote_test_receipts': [receipt],
            'review_repair_claims': [{
                'executor_task': executor_task,
                'pr': 2323,
                'head_sha': HEAD,
                'version': 1,
                'attempt': 1,
                'family': 'pr-2323-ec2',
                'state': 'DISPATCHED',
                'repair_task_id': child_id,
                'comment_id': None,
                'packet_sha256': 'f' * 64,
                'claimed_at': '2026-11-18T00:00:00Z',
            }],
        }
        _save_task(self.store, parent_record)

        child_record = {
            'task_id': child_id,
            'run_id': 'b' * 32,
            'state': 'DEVELOPER_OUTCOME_UNKNOWN',
            'developer': {
                'provider_profile': 'opencode',
                'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                'error_code': 'OPENCODE_OUTPUT_TRUNCATED',
                'execution': {
                    'status': 'UNKNOWN',
                    'cleanup_status': 'CONFIRMED',
                },
            },
        }
        _save_task(self.store, child_record)

        d6 = _make_d6(root=self.root, common_dir=self.common_dir,
                      executor_task=executor_task, head=HEAD, dirty=False)

        mock_repair_result = {
            'result': {'state': 'DISPATCHED'},
            'attempt': 2,
            'repair_task_id': '99c4d551' + '0' * 24,
            'claim': {'state': 'DISPATCHED'},
        }
        mock_repair = Mock(return_value=mock_repair_result)

        with patch.object(task_advance, '_d6_snapshot', return_value=d6):
            result = task_advance.advance(
                task_id=task_id, store=self.store, repair_review_call=mock_repair)

        self.assertEqual(result['step'], 'REPAIR_RETRY')
        self.assertEqual(result['codes'], [])
        mock_repair.assert_called_once()
        _, kwargs = mock_repair.call_args
        self.assertEqual(kwargs['retry_claim_id'], child_id)
        self.assertEqual(kwargs['remote_test_receipt']['source_sha'], HEAD)

    def test_evidence_1_d6_dirty_stays_refused(self):
        """A2: when D6 is dirty after repair ended UNKNOWN, advance stays refused."""
        task_id = '07c7358d' + '0' * 24
        run_id = 'a' * 32
        child_id = '88b3c440' + '0' * 24
        executor_task = 'executor-01'

        receipt = _make_receipt(
            task_id=task_id, run_id=run_id, executor_task=executor_task, source_sha=HEAD)

        parent_record = {
            'task_id': task_id,
            'run_id': run_id,
            'pm_provenance': {'pm_native_session_id': 'pm-sess-01', 'pm_cwd': self.root},
            'pm_assignment': {'goal': 'fix remote test failure'},
            'developer': {
                'provider_profile': 'opencode',
                'state': 'DEVELOPER_RESPONDED',
                'acceptance': ['resolve failure'],
                'read_paths': ['tools/sample.py'],
                'write_paths': ['tools/sample.py'],
                'model': 'opencode/deepseek-v4.1-flash-free#high',
            },
            'completion': {
                'executor_task_id': executor_task,
                'evidence': {'common_dir': self.common_dir},
            },
            'remote_test_receipts': [receipt],
            'review_repair_claims': [{
                'executor_task': executor_task,
                'pr': 2323,
                'head_sha': HEAD,
                'version': 1,
                'attempt': 1,
                'family': 'pr-2323-ec2',
                'state': 'DISPATCHED',
                'repair_task_id': child_id,
                'comment_id': None,
                'packet_sha256': 'f' * 64,
                'claimed_at': '2026-11-18T00:00:00Z',
            }],
        }
        _save_task(self.store, parent_record)

        child_record = {
            'task_id': child_id,
            'run_id': 'b' * 32,
            'state': 'DEVELOPER_OUTCOME_UNKNOWN',
            'developer': {
                'provider_profile': 'opencode',
                'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                'error_code': 'OPENCODE_OUTPUT_TRUNCATED',
                'execution': {
                    'status': 'UNKNOWN',
                    'cleanup_status': 'CONFIRMED',
                },
            },
        }
        _save_task(self.store, child_record)

        # D6 is dirty!
        d6 = _make_d6(root=self.root, common_dir=self.common_dir,
                      executor_task=executor_task, head=HEAD, dirty=True)

        mock_repair = Mock()
        with patch.object(task_advance, '_d6_snapshot', return_value=d6):
            result = task_advance.advance(
                task_id=task_id, store=self.store, repair_review_call=mock_repair)

        self.assertEqual(result['step'], 'REPAIR_OUTCOME_UNKNOWN')
        self.assertIn('REPAIR_OUTCOME_UNKNOWN', result['codes'])
        mock_repair.assert_not_called()

    def test_evidence_1_head_moved_stays_refused(self):
        """A2: when HEAD moved after repair ended UNKNOWN, advance stays refused."""
        task_id = '07c7358d' + '0' * 24
        run_id = 'a' * 32
        child_id = '88b3c440' + '0' * 24
        executor_task = 'executor-01'

        receipt = _make_receipt(
            task_id=task_id, run_id=run_id, executor_task=executor_task, source_sha=HEAD)

        parent_record = {
            'task_id': task_id,
            'run_id': run_id,
            'pm_provenance': {'pm_native_session_id': 'pm-sess-01', 'pm_cwd': self.root},
            'pm_assignment': {'goal': 'fix remote test failure'},
            'developer': {
                'provider_profile': 'opencode',
                'state': 'DEVELOPER_RESPONDED',
                'acceptance': ['resolve failure'],
                'read_paths': ['tools/sample.py'],
                'write_paths': ['tools/sample.py'],
                'model': 'opencode/deepseek-v4.1-flash-free#high',
            },
            'completion': {
                'executor_task_id': executor_task,
                'evidence': {'common_dir': self.common_dir},
            },
            'remote_test_receipts': [receipt],
            'review_repair_claims': [{
                'executor_task': executor_task,
                'pr': 2323,
                'head_sha': HEAD,
                'version': 1,
                'attempt': 1,
                'family': 'pr-2323-ec2',
                'state': 'DISPATCHED',
                'repair_task_id': child_id,
                'comment_id': None,
                'packet_sha256': 'f' * 64,
                'claimed_at': '2026-11-18T00:00:00Z',
            }],
        }
        _save_task(self.store, parent_record)

        child_record = {
            'task_id': child_id,
            'run_id': 'b' * 32,
            'state': 'DEVELOPER_OUTCOME_UNKNOWN',
            'developer': {
                'provider_profile': 'opencode',
                'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                'error_code': 'OPENCODE_OUTPUT_TRUNCATED',
                'execution': {
                    'status': 'UNKNOWN',
                    'cleanup_status': 'CONFIRMED',
                },
            },
        }
        _save_task(self.store, child_record)

        # HEAD moved to HEAD2
        d6 = _make_d6(root=self.root, common_dir=self.common_dir,
                      executor_task=executor_task, head=HEAD2, dirty=False)

        mock_repair = Mock()
        with patch.object(task_advance, '_d6_snapshot', return_value=d6):
            result = task_advance.advance(
                task_id=task_id, store=self.store, repair_review_call=mock_repair)

        # Head mismatch: does not retry the stale repair
        self.assertNotEqual(result['step'], 'REPAIR_RETRY')
        mock_repair.assert_not_called()

    def test_evidence_1_cleanup_unconfirmed_stays_refused(self):
        """Cleanup status UNKNOWN holds advancement for safety."""
        task_id = '07c7358d' + '0' * 24
        run_id = 'a' * 32
        child_id = '88b3c440' + '0' * 24
        executor_task = 'executor-01'

        receipt = _make_receipt(
            task_id=task_id, run_id=run_id, executor_task=executor_task, source_sha=HEAD)

        parent_record = {
            'task_id': task_id,
            'run_id': run_id,
            'pm_provenance': {'pm_native_session_id': 'pm-sess-01', 'pm_cwd': self.root},
            'pm_assignment': {'goal': 'fix remote test failure'},
            'developer': {
                'provider_profile': 'opencode',
                'state': 'DEVELOPER_RESPONDED',
                'acceptance': ['resolve failure'],
                'read_paths': ['tools/sample.py'],
                'write_paths': ['tools/sample.py'],
                'model': 'opencode/deepseek-v4.1-flash-free#high',
            },
            'completion': {
                'executor_task_id': executor_task,
                'evidence': {'common_dir': self.common_dir},
            },
            'remote_test_receipts': [receipt],
            'review_repair_claims': [{
                'executor_task': executor_task,
                'pr': 2323,
                'head_sha': HEAD,
                'version': 1,
                'attempt': 1,
                'family': 'pr-2323-ec2',
                'state': 'DISPATCHED',
                'repair_task_id': child_id,
                'comment_id': None,
                'packet_sha256': 'f' * 64,
                'claimed_at': '2026-11-18T00:00:00Z',
            }],
        }
        _save_task(self.store, parent_record)

        child_record = {
            'task_id': child_id,
            'run_id': 'b' * 32,
            'state': 'DEVELOPER_OUTCOME_UNKNOWN',
            'developer': {
                'provider_profile': 'opencode',
                'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                'error_code': 'OPENCODE_CLEANUP_UNKNOWN',
                'execution': {
                    'status': 'UNKNOWN',
                    'cleanup_status': 'UNKNOWN',  # unconfirmed!
                },
            },
        }
        _save_task(self.store, child_record)

        d6 = _make_d6(root=self.root, common_dir=self.common_dir,
                      executor_task=executor_task, head=HEAD, dirty=False)

        mock_repair = Mock()
        with patch.object(task_advance, '_d6_snapshot', return_value=d6):
            result = task_advance.advance(
                task_id=task_id, store=self.store, repair_review_call=mock_repair)

        self.assertEqual(result['step'], 'REPAIR_OUTCOME_UNKNOWN')
        self.assertIn('REPAIR_OUTCOME_UNKNOWN', result['codes'])
        mock_repair.assert_not_called()

    def test_evidence_2_review_repair_unknown_claim_delegated(self):
        """Evidence 2: review repair claim in state UNKNOWN, never delegated.

        When D6 is clean and head unchanged, advance or review_repair delegates the
        repair task and sets claim to DISPATCHED with attempt 1 (counted once).
        """
        task_id = 'e71e850c' + '0' * 24
        run_id = 'c' * 32
        child_id = '02bb9833' + '0' * 24
        executor_task = 'executor-02'

        parent_record = {
            'task_id': task_id,
            'run_id': run_id,
            'pm_provenance': {'pm_native_session_id': 'pm-sess-02', 'pm_cwd': self.root},
            'pm_assignment': {'goal': 'repair review finding'},
            'developer': {
                'provider_profile': 'opencode',
                'state': 'DEVELOPER_RESPONDED',
                'acceptance': ['fix finding'],
                'read_paths': ['tools/sample.py'],
                'write_paths': ['tools/sample.py'],
                'model': 'opencode/deepseek-v4.1-flash-free#high',
            },
            'completion': {
                'executor_task_id': executor_task,
                'evidence': {'common_dir': self.common_dir},
            },
            'completion_events': [{
                'source': 'REVIEWER',
                'source_sha': HEAD,
                'version': 1,
                'outcome': 'REVIEW_FAIL',
                'findings_count': 1,
                'state': 'PENDING',
                'event_id': 'evt001',
                'received_at': '2026-11-18T01:00:00Z',
                'session_id': 'review-evt001',
            }],
            'review_dispatch_claim': [{
                'executor_task': executor_task,
                'pr': 2340,
                'head_sha': HEAD,
                'version': 1,
                'account_mode': 'DEFAULT_SECOND_ACCOUNT',
            }],
            'review_repair_claims': [{
                'executor_task': executor_task,
                'pr': 2340,
                'head_sha': HEAD,
                'version': 1,
                'attempt': 1,
                'family': 'pr-2340',
                'state': 'UNKNOWN',  # claim in state UNKNOWN
                'repair_task_id': child_id,
                'comment_id': 1001,
                'packet_sha256': 'f' * 64,
                'claimed_at': '2026-11-18T01:05:00Z',
            }],
        }
        _save_task(self.store, parent_record)

        # Child repair task created by record-pm, but never delegated (no developer record)
        child_record = {
            'task_id': child_id,
            'run_id': 'd' * 32,
            'state': 'PM_PENDING',
            'pm_assignment': {'goal': 'repair review finding', 'acceptance': ['fix finding']},
        }
        _save_task(self.store, child_record)

        d6 = _make_d6(root=self.root, common_dir=self.common_dir,
                      executor_task=executor_task, head=HEAD, dirty=False)

        mock_repair_result = {
            'result': {'state': 'DISPATCHED'},
            'attempt': 1,
            'repair_task_id': child_id,
            'claim': {'state': 'DISPATCHED'},
        }
        mock_repair = Mock(return_value=mock_repair_result)

        with patch.object(task_advance, '_d6_snapshot', return_value=d6):
            result = task_advance.advance(
                task_id=task_id, store=self.store, repair_review_call=mock_repair)

        self.assertEqual(result['step'], 'REPAIR_REVIEW')
        mock_repair.assert_called_once()
        # Verify attempt 1 was maintained (counted once)
        self.assertEqual(result['result']['attempt'], 1)

    def test_evidence_2_d6_dirty_stays_refused(self):
        """Evidence 2 with dirty D6 stays refused."""
        task_id = 'e71e850c' + '0' * 24
        run_id = 'c' * 32
        child_id = '02bb9833' + '0' * 24
        executor_task = 'executor-02'

        parent_record = {
            'task_id': task_id,
            'run_id': run_id,
            'pm_provenance': {'pm_native_session_id': 'pm-sess-02', 'pm_cwd': self.root},
            'pm_assignment': {'goal': 'repair review finding'},
            'developer': {
                'provider_profile': 'opencode',
                'state': 'DEVELOPER_RESPONDED',
                'acceptance': ['fix finding'],
                'read_paths': ['tools/sample.py'],
                'write_paths': ['tools/sample.py'],
                'model': 'opencode/deepseek-v4.1-flash-free#high',
            },
            'completion': {
                'executor_task_id': executor_task,
                'evidence': {'common_dir': self.common_dir},
            },
            'completion_events': [{
                'source': 'REVIEWER',
                'source_sha': HEAD,
                'version': 1,
                'outcome': 'REVIEW_FAIL',
                'findings_count': 1,
                'state': 'PENDING',
                'event_id': 'evt001',
                'received_at': '2026-11-18T01:00:00Z',
                'session_id': 'review-evt001',
            }],
            'review_dispatch_claim': [{
                'executor_task': executor_task,
                'pr': 2340,
                'head_sha': HEAD,
                'version': 1,
                'account_mode': 'DEFAULT_SECOND_ACCOUNT',
            }],
            'review_repair_claims': [{
                'executor_task': executor_task,
                'pr': 2340,
                'head_sha': HEAD,
                'version': 1,
                'attempt': 1,
                'family': 'pr-2340',
                'state': 'UNKNOWN',
                'repair_task_id': child_id,
                'comment_id': 1001,
                'packet_sha256': 'f' * 64,
                'claimed_at': '2026-11-18T01:05:00Z',
            }],
        }
        _save_task(self.store, parent_record)

        child_record = {
            'task_id': child_id,
            'run_id': 'd' * 32,
            'state': 'PM_PENDING',
            'pm_assignment': {'goal': 'repair review finding', 'acceptance': ['fix finding']},
        }
        _save_task(self.store, child_record)

        # D6 is dirty!
        d6 = _make_d6(root=self.root, common_dir=self.common_dir,
                      executor_task=executor_task, head=HEAD, dirty=True)

        mock_repair = Mock()
        with patch.object(task_advance, '_d6_snapshot', return_value=d6):
            result = task_advance.advance(
                task_id=task_id, store=self.store, repair_review_call=mock_repair)

        # Stays refused, does not call repair_review
        mock_repair.assert_not_called()
        self.assertNotEqual(result.get('step'), 'REPAIR_REVIEW')


if __name__ == '__main__':
    unittest.main()
