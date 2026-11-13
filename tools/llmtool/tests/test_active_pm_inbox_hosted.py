"""Active-PM inbox delivery for hosted remote test receipts (#1257 R2c-2b).

Synthetic test data only; no credential-shaped strings are used.
"""

import hashlib
import json
import os
import unittest
from unittest.mock import patch

from llmtool import active_pm_inbox
from llmtool.task_store import TaskStore
import test_active_pm_inbox as pm_inbox_fixture
import test_task_completion as completion_fixture
from review_trust_fixture import start_module as _start_trust_fixture
from review_trust_fixture import stop_module as _stop_trust_fixture
import worker_policy_fixture


def setUpModule():
    _start_trust_fixture()


def tearDownModule():
    _stop_trust_fixture()


LEGACY_LOG_PATH = '/tmp/llmtool-remote-log.Abc123/remote-test.log'


def actions_block(**overrides):
    block = {
        'run_id': 1001, 'run_attempt': 1, 'job_id': 2002, 'check_run_id': 3003,
        'workflow_path': '.github/workflows/llmtool-remote-test.yml',
        'workflow_sha': 'e' * 40, 'labels': ['ubuntu-24.04'],
        'runner_group': 'GitHub Actions', 'nonce': 'f' * 32,
    }
    block.update(overrides)
    return block


def receipt_digest(receipt):
    body = {key: value for key, value in receipt.items() if key != 'receipt_digest'}
    return hashlib.sha256(json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode() + b'\n').hexdigest()


def receipt(task, *, failed=False, excerpt=None, actions=None, **overrides):
    """One synthetic receipt; ``actions`` and ``excerpt`` add optional keys."""
    value = {
        'result': 'PASS', 'exit_code': 0, 'source_sha': 'a' * 40,
        'tree': 'b' * 40, 'fetch_head': 'a' * 40,
        'modules': ['test_remote_test'], 'identity': {'uid': 65534},
        'started_at': '2026-11-09T00:00:00Z', 'ended_at': '2026-11-09T00:00:01Z',
        'elapsed_s': 1.0, 'ran_total': 1, 'final': ['OK'], 'failures': [],
        'log_bytes': 0, 'log_sha256': 'c' * 64, 'remote_log_path': None,
        'task_id': task['task_id'], 'run_id': task['run_id'],
        'executor_task': 'd6-task', 'pm_session': task['pm_session_id'],
        'command_digest': 'd' * 64,
    }
    if failed:
        value.update(result='FAIL', exit_code=1, final=['FAILED (failures=1)'],
                     failures=['test_remote_test'], log_bytes=1)
    if excerpt is not None:
        value['failure_excerpt'] = excerpt
    if actions is not None:
        value['actions'] = actions
    value.update(overrides)
    value['receipt_digest'] = receipt_digest(value)
    return value


class ActivePMInboxHostedReceiptTests(unittest.TestCase):
    setUp_fixture = completion_fixture.TaskCompletionTests.setUp
    _git = completion_fixture.TaskCompletionTests._git
    _trusted_main_ref = completion_fixture.TaskCompletionTests._trusted_main_ref
    _write_registration = completion_fixture.TaskCompletionTests._write_registration
    _write_env2_marker = completion_fixture.TaskCompletionTests._write_env2_marker
    _task = completion_fixture.TaskCompletionTests._task
    _mock_complete_evidence = completion_fixture.TaskCompletionTests._mock_complete_evidence

    def setUp(self):
        self.setUp_fixture()
        self.claude_home = self.base / 'claude-config'
        self.codex_home = self.base / 'codex-config'
        self.pm_cwd = self.base / 'pm-root'
        self.pm_cwd.mkdir()
        env = patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(self.claude_home),
                                      'CODEX_HOME': str(self.codex_home),
                                      'HOME': str(self.base / 'home')})
        env.start()
        self.addCleanup(env.stop)
        # #1692: the shared all-ALLOWED test policy, never the live file.
        self.policy_path = worker_policy_fixture.install(self)
        self.session = self._claude_session()
        self.store = TaskStore(self.base / 'tasks')

    _claude_session = pm_inbox_fixture.ActivePMInboxTests._claude_session
    _record_pm = pm_inbox_fixture.ActivePMInboxTests._record_pm
    _ready_task = pm_inbox_fixture.ActivePMInboxTests._ready_task
    _inbox = pm_inbox_fixture.ActivePMInboxTests._inbox

    def test_hosted_receipt_event_has_github_source_and_reaches_pm_inbox(self):
        task = self._ready_task()
        hosted_receipt = receipt(task, actions=actions_block(), executor_task=self.task_id)
        self.store.append_remote_test_receipt(task['task_id'], hosted_receipt)
        event = active_pm_inbox.record_remote_test_event(self.store, task['task_id'], hosted_receipt)
        self.assertEqual(event['source'], 'GITHUB_REMOTE_TEST')
        self.assertEqual(event['task_id'], task['task_id'])
        self.assertEqual(event['source_sha'], hosted_receipt['source_sha'])
        self.assertEqual(event['sequence'], 0)

        events = self._inbox()['events']
        remote_events = [e for e in events if e['source'] == 'GITHUB_REMOTE_TEST']
        self.assertEqual(len(remote_events), 1)
        self.assertEqual(remote_events[0]['event_id'], event['event_id'])
        self.assertEqual(remote_events[0]['task_id'], task['task_id'])
        self.assertEqual(remote_events[0]['source_sha'], hosted_receipt['source_sha'])

    def test_ec2_receipt_event_keeps_ec2_remote_test_source(self):
        task = self._ready_task()
        ec2_receipt = receipt(task, remote_log_path=LEGACY_LOG_PATH, executor_task=self.task_id)
        self.assertNotIn('actions', ec2_receipt)
        self.store.append_remote_test_receipt(task['task_id'], ec2_receipt)
        event = active_pm_inbox.record_remote_test_event(self.store, task['task_id'], ec2_receipt)
        self.assertEqual(event['source'], 'EC2_REMOTE_TEST')
        self.assertEqual(event['task_id'], task['task_id'])
        self.assertEqual(event['source_sha'], ec2_receipt['source_sha'])
        self.assertEqual(event['sequence'], 0)

        events = self._inbox()['events']
        remote_events = [e for e in events if e['source'] == 'EC2_REMOTE_TEST']
        self.assertEqual(len(remote_events), 1)
        self.assertEqual(remote_events[0]['event_id'], event['event_id'])
        self.assertEqual(remote_events[0]['task_id'], task['task_id'])
        self.assertEqual(remote_events[0]['source_sha'], ec2_receipt['source_sha'])

    def test_replaying_same_hosted_receipt_returns_same_event_idempotently(self):
        task = self._ready_task()
        hosted_receipt = receipt(task, actions=actions_block(), executor_task=self.task_id)
        self.store.append_remote_test_receipt(task['task_id'], hosted_receipt)
        first = active_pm_inbox.record_remote_test_event(self.store, task['task_id'], hosted_receipt)
        replay = active_pm_inbox.record_remote_test_event(self.store, task['task_id'], hosted_receipt)
        self.assertEqual(first['event_id'], replay['event_id'])
        self.assertEqual(first['sequence'], replay['sequence'])
        self.assertEqual(replay['source'], 'GITHUB_REMOTE_TEST')

        saved = self.store.get(task['task_id'])
        self.assertEqual(len(saved['completion_events']), 1)
        inbox_events = self._inbox()['events']
        self.assertEqual(len(inbox_events), 1)
        self.assertEqual(inbox_events[0]['event_id'], first['event_id'])

    def test_hosted_and_ec2_receipts_have_independent_sequence_streams(self):
        task = self._ready_task()
        ec2_receipt = receipt(task, remote_log_path=LEGACY_LOG_PATH, executor_task=self.task_id)
        self.store.append_remote_test_receipt(task['task_id'], ec2_receipt)
        ec2_event = active_pm_inbox.record_remote_test_event(self.store, task['task_id'], ec2_receipt)
        self.assertEqual(ec2_event['source'], 'EC2_REMOTE_TEST')
        self.assertEqual(ec2_event['sequence'], 0)

        hosted_receipt = receipt(task, actions=actions_block(), executor_task=self.task_id)
        self.store.append_remote_test_receipt(task['task_id'], hosted_receipt)
        hosted_event = active_pm_inbox.record_remote_test_event(self.store, task['task_id'], hosted_receipt)
        self.assertEqual(hosted_event['source'], 'GITHUB_REMOTE_TEST')
        self.assertEqual(hosted_event['sequence'], 0)

        events = self._inbox()['events']
        self.assertEqual(len(events), 2)
        sources = {e['source'] for e in events}
        self.assertEqual(sources, {'EC2_REMOTE_TEST', 'GITHUB_REMOTE_TEST'})
