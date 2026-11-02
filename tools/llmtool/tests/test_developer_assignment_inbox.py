"""PM -> Developer assignment inbox and claim receipts (#367). Offline only.

The worker-dispatch gate is replaced by a fixed decision so tests do not
depend on the live Owner policy file; no provider, network or model is used.
"""

import json
import os
import tempfile
import unittest
import uuid
from argparse import Namespace
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from llmtool import active_pm_inbox
from llmtool import developer_assignment_inbox as inbox
from llmtool import task_store as task_store_module
from llmtool.cli import main
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore
from llmtool.task_submission_cli import run_task_assignments, run_task_claim
from llmtool.worker_dispatch import PolicyDecision

POLICY = 'a' * 64
PM = 'pm-' + '11111111-2222-4333-8444-555555555555'
D6 = 'claude-dev-367-assignment-inbox-20261103'


def allow(worker_key, owner_assignment_ref=None, task_id=None):
    return PolicyDecision(worker_key, True, 'OWNER_POLICY_ALLOWED', POLICY)


def deny(worker_key, owner_assignment_ref=None, task_id=None):
    return PolicyDecision(worker_key, False, 'OWNER_PAUSED', POLICY)


class DeveloperAssignmentInboxTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / 'tasks'
        self.store = TaskStore(self.root)
        self.account_a = str(self.base / 'claude-account-a')
        self.account_b = str(self.base / 'claude-account-b')
        env = patch.dict(os.environ, {'HOME': str(self.base / 'home'),
                                      'CLAUDE_CONFIG_DIR': self.account_a})
        env.start()
        self.addCleanup(env.stop)
        self.gate_patch = patch('llmtool.worker_dispatch.evaluate', side_effect=allow)
        self.gate = self.gate_patch.start()
        self.addCleanup(self.gate_patch.stop)

    def _assign(self, *, worker='claude-developer', d6=D6, now=None, **kwargs):
        return inbox.assign(self.store, worker=worker, d6_task_id=d6, pm_session_key=PM,
                            now=now, **kwargs)

    def _claim(self, assignment_id, *, account=None, session=None, store=None, worker=None):
        environ = {'CLAUDE_CONFIG_DIR': account or self.account_a}
        return inbox.claim(store or self.store, assignment_id,
                           claimer_session_id=session or str(uuid.uuid4()),
                           worker=worker, environ=environ)

    def test_assign_lists_pending_for_the_named_worker_only(self):
        assigned = self._assign()
        self.assertEqual(assigned['event'], 'ASSIGNED')
        self.assertEqual(assigned['state'], 'ASSIGNED')
        self.assertEqual((assigned['worker'], assigned['d6_task_id'], assigned['pm_session_id']),
                         ('claude-developer', D6, PM))
        self.assertEqual(assigned['dispatch'], {'decision': 'ALLOWED',
                                                'reason_code': 'OWNER_POLICY_ALLOWED',
                                                'policy_sha256': POLICY})
        self.assertEqual(assigned['claim_timeout_seconds'], 120)
        self.gate.assert_called_once_with('claude-developer', owner_assignment_ref=None,
                                          task_id=None)
        pending = inbox.pending_assignments(self.store, 'claude-developer')
        self.assertEqual([item['assignment_id'] for item in pending],
                         [assigned['assignment_id']])
        self.assertEqual(inbox.pending_assignments(self.store, 'codex-developer'), [])
        # The sidecar never appears as a Task.
        self.assertEqual(self.store.task_ids(), [])

    def test_claim_once_writes_receipt_and_second_claim_is_refused(self):
        assigned = self._assign()
        session = str(uuid.uuid4())
        claimed = self._claim(assigned['assignment_id'], session=session,
                              worker='claude-developer')
        self.assertEqual(claimed['state'], 'CLAIMED')
        receipt = claimed['claim']
        self.assertEqual(receipt['claimer_session_id'], session)
        self.assertEqual(receipt['claimer_account'], inbox.claimer_account(
            {'CLAUDE_CONFIG_DIR': self.account_a}))
        self.assertTrue(receipt['claimed_at'].endswith('Z'))
        self.assertIs(receipt['late'], False)
        self.assertEqual(inbox.pending_assignments(self.store, 'claude-developer'), [])

        with self.assertRaises(LLMToolError) as raised:
            self._claim(assigned['assignment_id'])
        self.assertEqual(raised.exception.task_store_error_code, 'ASSIGNMENT_ALREADY_CLAIMED')
        # The first receipt is unchanged.
        (stored,) = self.store.get_developer_assignments()
        self.assertEqual(stored['claim'], receipt)

    def test_claim_refuses_unknown_id_wrong_worker_and_bad_session(self):
        assigned = self._assign()
        with self.assertRaises(LLMToolError) as raised:
            self._claim(uuid.uuid4().hex)
        self.assertEqual(raised.exception.task_store_error_code, 'ASSIGNMENT_NOT_FOUND')
        with self.assertRaises(LLMToolError) as raised:
            self._claim(assigned['assignment_id'], worker='codex-developer')
        self.assertEqual(raised.exception.task_store_error_code, 'ASSIGNMENT_WORKER_MISMATCH')
        with self.assertRaises(LLMToolError):
            self._claim(assigned['assignment_id'], session='not-a-session')
        self.assertEqual(self.store.get_developer_assignments()[0]['state'], 'ASSIGNED')

    def test_cross_account_claims_share_the_store_with_distinct_identities(self):
        first = self._assign(d6='claude-dev-367-a')
        second = self._assign(d6='claude-dev-367-b')
        # Each account uses its own process view of the same shared store root.
        receipt_a = self._claim(first['assignment_id'], account=self.account_a,
                                store=TaskStore(self.root))['claim']
        receipt_b = self._claim(second['assignment_id'], account=self.account_b,
                                store=TaskStore(self.root))['claim']
        self.assertNotEqual(receipt_a['claimer_account'], receipt_b['claimer_account'])
        with self.assertRaises(LLMToolError) as raised:
            self._claim(first['assignment_id'], account=self.account_b,
                        store=TaskStore(self.root))
        self.assertEqual(raised.exception.task_store_error_code, 'ASSIGNMENT_ALREADY_CLAIMED')
        stored = {item['assignment_id']: item['claim']
                  for item in TaskStore(self.root).get_developer_assignments()}
        self.assertEqual(stored, {first['assignment_id']: receipt_a,
                                  second['assignment_id']: receipt_b})
        # Through the CLI handler, CLAUDE_CONFIG_DIR selects the claimer account.
        third = self._assign(d6='claude-dev-367-c')
        out = StringIO()
        with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': self.account_b}):
            code = run_task_claim(Namespace(assignment_id=third['assignment_id'],
                                            session=str(uuid.uuid4()), worker=None),
                                  store=self.store, stdout=out)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())['claim']['claimer_account'],
                         receipt_b['claimer_account'])

    def test_unclaimed_after_timeout_is_listed_in_pm_inbox(self):
        past = datetime.now(timezone.utc) - timedelta(seconds=300)
        overdue = self._assign(d6='claude-dev-367-old', now=past)
        fresh = self._assign(d6='claude-dev-367-new')
        custom = self._assign(d6='claude-dev-367-long', now=past, claim_timeout=3600)
        claimed = self._assign(d6='claude-dev-367-done', now=past)
        late = self._claim(claimed['assignment_id'])
        self.assertIs(late['claim']['late'], True)

        result = active_pm_inbox.peek_inbox(self.store, PM)
        self.assertEqual([item['assignment_id'] for item in result['unclaimed_assignments']],
                         [overdue['assignment_id']])
        self.assertEqual(result['unclaimed_assignments'][0]['state'], 'UNCLAIMED')
        self.assertEqual(result['status'], 'ATTENTION')
        self.assertEqual(active_pm_inbox.peek_inbox(self.store, 'pm-other')
                         ['unclaimed_assignments'], [])
        # Still claimable by the Developer, and flagged overdue for it.
        pending = {item['assignment_id']: item['overdue']
                   for item in inbox.pending_assignments(self.store, 'claude-developer')}
        self.assertEqual(pending, {overdue['assignment_id']: True,
                                   fresh['assignment_id']: False,
                                   custom['assignment_id']: False})
        later = datetime.now(timezone.utc) + timedelta(seconds=121)
        self.assertEqual({item['assignment_id'] for item in
                          inbox.unclaimed_for_pm(self.store, PM, now=later)},
                         {overdue['assignment_id'], fresh['assignment_id']})

    def test_worker_dispatch_refusal_writes_no_event(self):
        self.gate.side_effect = deny
        with self.assertRaises(LLMToolError) as raised:
            self._assign()
        self.assertEqual(raised.exception.dispatch_code, 'OWNER_PAUSED')
        self.assertFalse((self.root / task_store_module.ASSIGNMENTS_NAME).exists())
        self.assertEqual(self.store.get_developer_assignments(), [])
        # An existing sidecar is left byte-identical by a later refusal.
        self.gate.side_effect = allow
        self._assign()
        before = (self.root / task_store_module.ASSIGNMENTS_NAME).read_bytes()
        self.gate.side_effect = deny
        with self.assertRaises(LLMToolError):
            self._assign(d6='claude-dev-367-other')
        self.assertEqual((self.root / task_store_module.ASSIGNMENTS_NAME).read_bytes(), before)

    def test_real_gate_refuses_unknown_worker(self):
        self.gate_patch.stop()
        try:
            with self.assertRaises(LLMToolError):
                self._assign(worker='no-such-worker')
            self.assertEqual(self.store.get_developer_assignments(), [])
        finally:
            self.gate = self.gate_patch.start()

    def test_duplicate_open_assignment_is_refused(self):
        self._assign()
        with self.assertRaises(LLMToolError) as raised:
            self._assign()
        self.assertEqual(raised.exception.task_store_error_code, 'ASSIGNMENT_DUPLICATE')
        self.assertEqual(len(self.store.get_developer_assignments()), 1)

    def test_wait_sleeps_until_assignment_arrives_and_times_out(self):
        calls = []

        def sleep(seconds):
            calls.append(seconds)
            if len(calls) == 1:
                self._assign()

        out = StringIO()
        self.assertEqual(run_task_assignments(
            Namespace(worker='claude-developer', wait=True, timeout=30),
            store=self.store, stdout=out, sleep=sleep), 0)
        result = json.loads(out.getvalue())
        self.assertEqual(calls, [inbox.POLL_SECONDS])
        self.assertEqual(len(result['assignments']), 1)
        self.assertIs(result['timed_out'], False)

        clock = iter(range(0, 1000, 5))
        empty = inbox.wait_assignments(self.store, 'codex-developer', wait=True, timeout=12,
                                       clock=lambda: next(clock), sleep=lambda _: None)
        self.assertEqual(empty['assignments'], [])
        self.assertIs(empty['timed_out'], True)
        with self.assertRaises(LLMToolError):
            inbox.wait_assignments(self.store, 'codex-developer', timeout=3601)

    def test_pm_wait_wakes_when_an_assignment_becomes_unclaimed(self):
        # Already-unclaimed assignments do not end the wait; a new one does.
        past = datetime.now(timezone.utc) - timedelta(seconds=300)
        self._assign(d6='claude-dev-367-old', now=past)
        calls = []

        def sleep(seconds):
            calls.append(seconds)
            if len(calls) == 2:
                self._assign(d6='claude-dev-367-new', now=past)

        result = active_pm_inbox.wait_inbox(self.store, PM, wait=True, timeout=60,
                                            sleep=sleep)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(result['unclaimed_assignments']), 2)

    def test_damaged_sidecar_fails_closed_without_hiding_pm_inbox(self):
        self._assign()
        path = self.root / task_store_module.ASSIGNMENTS_NAME
        value = json.loads(path.read_text())
        value['assignments'][0]['worker'] = 'codex-developer'
        path.write_text(json.dumps(value))
        with self.assertRaises(LLMToolError) as raised:
            self.store.get_developer_assignments()
        self.assertEqual(raised.exception.task_store_error_code, 'ASSIGNMENTS_INVALID')
        result = active_pm_inbox.peek_inbox(self.store, PM)
        self.assertEqual(result['assignments_error_code'], 'ASSIGNMENTS_INVALID')
        self.assertEqual(result['status'], 'ATTENTION')

    def test_cli_assign_claim_round_trip_and_refusal_code(self):
        home = self.base / 'home'
        native = PM[3:]

        def run(*argv):
            out = StringIO()
            with redirect_stdout(out):
                code = main(['task', *argv])
            return code, json.loads(out.getvalue())

        code, assigned = run('assign', '--worker', 'claude-developer', '--d6-task', D6,
                             '--pm-session', native, '--claim-timeout', '60')
        self.assertEqual(code, 0)
        self.assertEqual(assigned['claim_timeout_seconds'], 60)
        self.assertTrue((home / '.local' / 'state' / 'llmtool' / 'tasks' /
                         task_store_module.ASSIGNMENTS_NAME).exists())
        code, listed = run('assignments', '--worker', 'claude-developer')
        self.assertEqual([item['assignment_id'] for item in listed['assignments']],
                         [assigned['assignment_id']])
        session = str(uuid.uuid4())
        code, claimed = run('claim', assigned['assignment_id'], '--session', session)
        self.assertEqual((code, claimed['claim']['claimer_session_id']), (0, session))
        code, refused = run('claim', assigned['assignment_id'], '--session', session)
        self.assertEqual((code, refused), (1, {'code': 'ASSIGNMENT_ALREADY_CLAIMED'}))
        self.gate.side_effect = deny
        code, refused = run('assign', '--worker', 'claude-developer', '--d6-task', D6,
                            '--pm-session', native)
        self.assertEqual((code, refused), (1, {'code': 'OWNER_PAUSED'}))


if __name__ == '__main__':
    unittest.main()
