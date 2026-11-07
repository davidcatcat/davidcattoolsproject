"""PM -> Developer assignment inbox and claim receipts (#367 slice 1). Offline only.

Isolated TaskStore, a fake D6 registration and two temporary Claude
configuration directories (PM account A, Developer account B). The
worker-dispatch gate is replaced by a fixed decision so tests do not depend
on the live Owner policy file; no provider, network, session or model is used.
Matrix numbers refer to the #367 Task Contract offline test matrix.
"""

import fcntl
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
HEAD = '1' * 40
WORKER = 'claude-developer'


def allow(worker_key, owner_assignment_ref=None, task_id=None):
    return PolicyDecision(worker_key, True, 'OWNER_POLICY_ALLOWED', POLICY)


def paused(worker_key, owner_assignment_ref=None, task_id=None):
    return PolicyDecision(worker_key, False, 'OWNER_PAUSED', POLICY)


def owner_only(worker_key, owner_assignment_ref=None, task_id=None):
    return PolicyDecision(worker_key, False, 'OWNER_ASSIGNMENT_REQUIRED', POLICY)


import review_trust_fixture

# Reuse the completion and PM inbox fixtures without re-collecting their tests.
import test_active_pm_inbox as pm_fixture
import test_task_completion as completion_fixture


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


class DeveloperAssignmentInboxTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.store_root = self.base / 'tasks'
        self.store = TaskStore(self.store_root)
        self.account_a = str(self.base / 'claude-account-a')
        self.account_b = str(self.base / 'claude-account-b')
        self.common_dir = self.base / 'repo' / '.git'
        (self.common_dir / 'llmtool-worktrees').mkdir(parents=True, mode=0o700)
        os.chmod(self.common_dir / 'llmtool-worktrees', 0o700)
        env = patch.dict(os.environ, {'HOME': str(self.base / 'home'),
                                      'CLAUDE_CONFIG_DIR': self.account_a})
        env.start()
        self.addCleanup(env.stop)
        self.gate_patch = patch('llmtool.worker_dispatch.evaluate', side_effect=allow)
        self.gate = self.gate_patch.start()
        self.addCleanup(self.gate_patch.stop)
        self.roots = {}
        self.register(D6)

    # -- fixtures ---------------------------------------------------------

    def register(self, task_id, *, head=HEAD, state='ACTIVE'):
        root = self.roots.get(task_id)
        if root is None:
            root = self.base / 'worktrees' / task_id
            root.mkdir(parents=True, mode=0o700)
            os.chmod(root, 0o700)
            self.roots[task_id] = root
        info = root.lstat()
        record = {
            'schema_version': 1, 'task_id': task_id, 'state': state,
            'lock_state': 'LOCKED', 'git_common_dir': str(self.common_dir),
            'trusted_agent_id': 'claude-developer', 'session_ref': 'session-ref',
            'root_realpath': str(root), 'branch': 'feat/' + task_id,
            'base_sha': '0' * 40, 'head_sha': head,
            'physical_identity': {'device': info.st_dev, 'inode': info.st_ino,
                                  'uid': info.st_uid, 'mode': '0o700'},
        }
        path = self.common_dir / 'llmtool-worktrees' / (task_id + '.json')
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(record))
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        return root

    def transcript(self, session, account, task_id=D6):
        path = active_pm_inbox.claude_session_file(
            session, str(self.roots[task_id]), {'CLAUDE_CONFIG_DIR': account})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}\n')
        return path

    def assign(self, *, worker=WORKER, d6=D6, now=None, **kwargs):
        return inbox.assign(self.store, worker=worker, d6_task_id=d6,
                            common_dir=str(self.common_dir), pm_session_key=PM,
                            now=now, **kwargs)

    def claim(self, assignment_id, *, session, account=None, worker=WORKER, d6=D6,
              store=None, now=None):
        return inbox.claim(store or self.store, assignment_id, worker=worker, d6_task_id=d6,
                           session_id=session,
                           environ={'CLAUDE_CONFIG_DIR': account or self.account_b},
                           now=now)

    def assert_refused(self, code, call, *args, **kwargs):
        with self.assertRaises(LLMToolError) as raised:
            call(*args, **kwargs)
        self.assertEqual(inbox.refusal_code(raised.exception), code)

    def stored(self, assignment_id):
        (item,) = [item for item in TaskStore(self.store_root).get_developer_assignments()
                   if item['assignment_id'] == assignment_id]
        return item

    # -- matrix 1 ---------------------------------------------------------

    def test_1_assign_then_cross_account_claim_is_visible_to_pm(self):
        before = datetime.now(timezone.utc).replace(microsecond=0)
        assigned = self.assign()
        self.assertEqual((assigned['state'], assigned['source_sha'], assigned['d6_root']),
                         ('ASSIGNED', HEAD, str(self.roots[D6])))
        expires = (datetime.strptime(assigned['expires_at'], '%Y-%m-%dT%H:%M:%SZ')
                   .replace(tzinfo=timezone.utc))
        self.assertLessEqual(expires - before, timedelta(seconds=601))
        self.assertEqual(assigned['dispatch'], {'decision': 'ALLOWED',
                                                'reason_code': 'OWNER_POLICY_ALLOWED',
                                                'policy_sha256': POLICY})
        self.gate.assert_called_once_with(WORKER, owner_assignment_ref=None, task_id=None)
        listed = inbox.pending_assignments(self.store, WORKER)
        self.assertEqual(listed, [{'assignment_id': assigned['assignment_id'],
                                   'd6_task_id': D6, 'task_id': None, 'source_sha': HEAD,
                                   'expires_at': assigned['expires_at']}])
        self.assertEqual(inbox.pending_assignments(self.store, 'codex-developer'), [])

        session = str(uuid.uuid4())
        self.transcript(session, self.account_b)
        claimed = self.claim(assigned['assignment_id'], session=session,
                             store=TaskStore(self.store_root))
        self.assertEqual(claimed['state'], 'CLAIMED')
        receipt = claimed['claim']
        self.assertEqual(set(receipt), {'session_id', 'claimed_at', 'account_dir_label'})
        self.assertEqual(receipt['session_id'], session)
        self.assertEqual(receipt['account_dir_label'],
                         inbox.account_dir_label({'CLAUDE_CONFIG_DIR': self.account_b}))
        self.assertNotEqual(receipt['account_dir_label'],
                            inbox.account_dir_label({'CLAUDE_CONFIG_DIR': self.account_a}))
        self.assertEqual([entry['action'] for entry in claimed['audit']], ['ASSIGN', 'CLAIM'])
        # The PM (account A, its own process view) sees the receipt.
        view = active_pm_inbox.peek_inbox(TaskStore(self.store_root), PM)
        self.assertEqual(view['unclaimed_assignments'], [])
        (seen,) = view['claimed_assignments']
        self.assertEqual((seen['state'], seen['claim']), ('CLAIMED', receipt))
        self.assertEqual(view['status'], 'OK')
        self.assertEqual(self.store.task_ids(), [])

    def test_claim_without_config_env_uses_worker_account_not_pm_account(self):
        assigned = self.assign()
        session = str(uuid.uuid4())
        home = str(self.base / 'home')
        worker_dir = str(Path(home) / '.claude')
        environ = {'HOME': home}

        def configured_role(role, *, environ, home):
            return {'role': role, 'config_dir': self.account_a if role == 'pm' else None}

        self.transcript(session, worker_dir)
        with patch('llmtool.role_config.resolve_role', side_effect=configured_role):
            claimed = inbox.claim(self.store, assigned['assignment_id'], worker=WORKER,
                                  d6_task_id=D6, session_id=session, environ=environ)
        self.assertEqual(claimed['state'], 'CLAIMED')
        self.assertEqual(claimed['claim']['account_dir_label'],
                         inbox.account_dir_label(environ))

        self.register(D6, head='2' * 40)
        assigned = self.assign()
        pm_only_session = str(uuid.uuid4())
        self.transcript(pm_only_session, self.account_a)
        with patch('llmtool.role_config.resolve_role', side_effect=configured_role):
            self.assert_refused(
                'ASSIGNMENT_SESSION_NOT_FOUND', inbox.claim, self.store,
                assigned['assignment_id'], worker=WORKER, d6_task_id=D6,
                session_id=pm_only_session, environ=environ)

    # -- matrix 2 ---------------------------------------------------------

    def test_2_same_session_reclaim_returns_receipt_other_session_refused(self):
        assigned = self.assign()
        session = str(uuid.uuid4())
        self.transcript(session, self.account_b)
        first = self.claim(assigned['assignment_id'], session=session)
        path = self.store_root / task_store_module.ASSIGNMENTS_NAME
        raw = path.read_bytes()
        later = datetime.now(timezone.utc) + timedelta(seconds=5)
        again = self.claim(assigned['assignment_id'], session=session, now=later)
        self.assertEqual(again['claim'], first['claim'])
        self.assertEqual(path.read_bytes(), raw)
        other = str(uuid.uuid4())
        self.transcript(other, self.account_b)
        self.assert_refused('ASSIGNMENT_ALREADY_CLAIMED', self.claim,
                            assigned['assignment_id'], session=other)
        # Same session id from another account is a different claimer.
        self.transcript(session, self.account_a)
        self.assert_refused('ASSIGNMENT_ALREADY_CLAIMED', self.claim,
                            assigned['assignment_id'], session=session,
                            account=self.account_a)
        self.assertEqual(self.stored(assigned['assignment_id'])['claim'], first['claim'])

    # -- matrix 3 ---------------------------------------------------------

    def test_3_crash_before_claim_expires_to_unclaimed_and_claim_refused(self):
        past = datetime.now(timezone.utc) - timedelta(seconds=700)
        assigned = self.assign(now=past)
        self.assertEqual(inbox.pending_assignments(self.store, WORKER), [])
        view = active_pm_inbox.peek_inbox(self.store, PM)
        self.assertEqual([(item['assignment_id'], item['state'])
                          for item in view['unclaimed_assignments']],
                         [(assigned['assignment_id'], 'UNCLAIMED')])
        self.assertEqual(view['status'], 'ATTENTION')
        session = str(uuid.uuid4())
        self.transcript(session, self.account_b)
        self.assert_refused('ASSIGNMENT_EXPIRED', self.claim,
                            assigned['assignment_id'], session=session)
        self.assertEqual(self.stored(assigned['assignment_id'])['state'], 'EXPIRED')
        self.assert_refused('ASSIGNMENT_EXPIRED', self.claim,
                            assigned['assignment_id'], session=session)
        self.assertEqual(len(active_pm_inbox.peek_inbox(self.store, PM)
                             ['unclaimed_assignments']), 1)
        self.assertEqual(active_pm_inbox.peek_inbox(self.store, 'pm-other')
                         ['unclaimed_assignments'], [])

    # -- matrix 4 ---------------------------------------------------------

    def test_4_crash_after_claim_keeps_claimed_and_wait_does_not_relist(self):
        assigned = self.assign()
        session = str(uuid.uuid4())
        self.transcript(session, self.account_b)
        self.claim(assigned['assignment_id'], session=session)
        restarted = TaskStore(self.store_root)
        clock = iter(range(0, 1000, 5))
        result = inbox.wait_assignments(restarted, WORKER, wait=True, timeout=12,
                                        clock=lambda: next(clock), sleep=lambda _: None)
        self.assertEqual((result['assignments'], result['timed_out']), ([], True))
        self.assertEqual(self.stored(assigned['assignment_id'])['state'], 'CLAIMED')

    # -- matrix 5 ---------------------------------------------------------

    def test_5_d6_head_moved_or_inactive_refuses_claim(self):
        assigned = self.assign()
        session = str(uuid.uuid4())
        self.transcript(session, self.account_b)
        self.register(D6, head='2' * 40)
        self.assert_refused('ASSIGNMENT_SOURCE_STALE', self.claim,
                            assigned['assignment_id'], session=session)
        self.register(D6, head=HEAD, state='RETIRED')
        self.assert_refused('ASSIGNMENT_D6_NOT_ACTIVE', self.claim,
                            assigned['assignment_id'], session=session)
        self.assertEqual(self.stored(assigned['assignment_id'])['state'], 'ASSIGNED')
        # Assign refuses a D6 that is not ACTIVE or not registered, writing nothing.
        self.assert_refused('ASSIGNMENT_D6_NOT_ACTIVE', self.assign, d6='claude-dev-other')
        self.assertEqual(len(self.store.get_developer_assignments()), 1)

    # -- matrix 6 ---------------------------------------------------------

    def test_6_paused_or_owner_only_worker_at_claim_is_refused_and_audited(self):
        assigned = self.assign()
        session = str(uuid.uuid4())
        self.transcript(session, self.account_b)
        for decision, code in ((paused, 'OWNER_PAUSED'),
                               (owner_only, 'OWNER_ASSIGNMENT_REQUIRED')):
            self.gate.side_effect = decision
            self.assert_refused(code, self.claim, assigned['assignment_id'], session=session)
            item = self.stored(assigned['assignment_id'])
            self.assertEqual(item['state'], 'ASSIGNED')
            self.assertEqual({key: item['audit'][-1][key]
                              for key in ('action', 'decision', 'reason_code')},
                             {'action': 'CLAIM', 'decision': 'BLOCKED', 'reason_code': code})
        self.assertEqual([entry['action'] for entry in item['audit']],
                         ['ASSIGN', 'CLAIM', 'CLAIM'])
        # Assign is refused by the gate as well, and writes nothing.
        path = self.store_root / task_store_module.ASSIGNMENTS_NAME
        raw = path.read_bytes()
        self.register('claude-dev-367-b')
        self.gate.side_effect = paused
        self.assert_refused('OWNER_PAUSED', self.assign, d6='claude-dev-367-b')
        self.assertEqual(path.read_bytes(), raw)
        # Re-allowed: the claim now succeeds, with an ALLOWED CLAIM audit entry.
        self.gate.side_effect = allow
        claimed = self.claim(assigned['assignment_id'], session=session)
        self.assertEqual(claimed['audit'][-1]['decision'], 'ALLOWED')

    def test_6_real_gate_refuses_unknown_worker(self):
        self.gate_patch.stop()
        try:
            with self.assertRaises(LLMToolError):
                self.assign(worker='no-such-worker')
            self.assertEqual(self.store.get_developer_assignments(), [])
        finally:
            self.gate = self.gate_patch.start()

    # -- matrix 7 ---------------------------------------------------------

    def test_7_wrong_worker_wrong_d6_or_pm_account_transcript_refused(self):
        assigned = self.assign()
        session = str(uuid.uuid4())
        self.transcript(session, self.account_a)  # only under the PM's account
        self.assert_refused('ASSIGNMENT_WORKER_MISMATCH', self.claim,
                            assigned['assignment_id'], session=session,
                            worker='codex-developer')
        self.assert_refused('ASSIGNMENT_D6_MISMATCH', self.claim,
                            assigned['assignment_id'], session=session,
                            d6='claude-dev-other')
        self.assert_refused('ASSIGNMENT_SESSION_NOT_FOUND', self.claim,
                            assigned['assignment_id'], session=session)
        # A transcript for another cwd under the claimer's account does not count.
        other_root = self.register('claude-dev-367-b')
        self.transcript(session, self.account_b, task_id='claude-dev-367-b')
        self.assertTrue(other_root.is_dir())
        self.assert_refused('ASSIGNMENT_SESSION_NOT_FOUND', self.claim,
                            assigned['assignment_id'], session=session)
        self.assert_refused('ASSIGNMENT_NOT_FOUND', self.claim, uuid.uuid4().hex,
                            session=session)
        self.assert_refused('ASSIGNMENT_SESSION_INVALID', self.claim,
                            assigned['assignment_id'], session='not-a-session')
        self.assertEqual(self.stored(assigned['assignment_id'])['state'], 'ASSIGNED')

    # -- matrix 8 ---------------------------------------------------------

    def test_8_second_live_assignment_for_same_d6_refused(self):
        first = self.assign()
        self.assert_refused('ASSIGNMENT_DUPLICATE', self.assign, worker='codex-developer')
        session = str(uuid.uuid4())
        self.transcript(session, self.account_b)
        self.claim(first['assignment_id'], session=session)
        # Claimed at the same HEAD: the claimer is still the writer.
        self.assert_refused('ASSIGNMENT_DUPLICATE', self.assign)
        self.assertEqual(len(self.store.get_developer_assignments()), 1)
        # After the D6 HEAD moved (e.g. a repair round) a new assignment is allowed.
        self.register(D6, head='3' * 40)
        second = self.assign()
        self.assertEqual(second['source_sha'], '3' * 40)
        # An expired assignment does not block the D6 either.
        self.register('claude-dev-367-b')
        past = datetime.now(timezone.utc) - timedelta(seconds=700)
        self.assign(d6='claude-dev-367-b', now=past)
        self.assign(d6='claude-dev-367-b')

    # -- matrix 9 ---------------------------------------------------------

    def test_9_symlinked_or_missing_transcript_refused(self):
        assigned = self.assign()
        session = str(uuid.uuid4())
        self.assert_refused('ASSIGNMENT_SESSION_NOT_FOUND', self.claim,
                            assigned['assignment_id'], session=session)
        real = self.base / 'elsewhere.jsonl'
        real.write_text('{}\n')
        path = active_pm_inbox.claude_session_file(
            session, str(self.roots[D6]), {'CLAUDE_CONFIG_DIR': self.account_b})
        path.parent.mkdir(parents=True)
        path.symlink_to(real)
        self.assert_refused('ASSIGNMENT_SESSION_NOT_FOUND', self.claim,
                            assigned['assignment_id'], session=session)
        # A symlinked project directory is refused as well.
        path.unlink()
        project = path.parent
        project.rmdir()
        target = self.base / 'project-target'
        target.mkdir()
        (target / path.name).write_text('{}\n')
        project.symlink_to(target, target_is_directory=True)
        self.assert_refused('ASSIGNMENT_SESSION_NOT_FOUND', self.claim,
                            assigned['assignment_id'], session=session)
        self.assertEqual(self.stored(assigned['assignment_id'])['state'], 'ASSIGNED')

    # -- matrix 10 --------------------------------------------------------

    def test_10_wait_returns_within_bound_with_no_events(self):
        ticks = []
        clock = iter(range(0, 1000, 5))

        def fake_clock():
            value = next(clock)
            ticks.append(value)
            return value

        empty = inbox.wait_assignments(self.store, WORKER, wait=True, timeout=12,
                                       clock=fake_clock, sleep=lambda _: None)
        self.assertEqual((empty['assignments'], empty['timed_out']), ([], True))
        self.assertLessEqual(ticks[-1] - ticks[0], 12 + 5)
        self.assert_refused('ASSIGNMENT_WAIT_TIMEOUT_INVALID', inbox.wait_assignments,
                            self.store, WORKER, timeout=601)
        # It wakes as soon as an assignment arrives.
        calls = []

        def sleep(seconds):
            calls.append(seconds)
            if len(calls) == 1:
                self.assign()

        out = StringIO()
        self.assertEqual(run_task_assignments(
            Namespace(worker=WORKER, wait=True, timeout=30),
            store=self.store, stdout=out, sleep=sleep), 0)
        result = json.loads(out.getvalue())
        self.assertEqual((calls, len(result['assignments']), result['timed_out']),
                         ([inbox.POLL_SECONDS], 1, False))

    # -- supporting checks ------------------------------------------------

    def test_expiry_is_bounded_to_fifteen_minutes(self):
        for value in (0, 901, '60'):
            self.assert_refused('ASSIGNMENT_EXPIRY_INVALID', self.assign, expires_in=value)
        self.assertEqual(self.assign(expires_in=900)['state'], 'ASSIGNED')

    def test_lock_wait_is_bounded_with_busy_code(self):
        self.assign()
        lock = os.open(self.store_root / task_store_module.ASSIGNMENTS_LOCK_NAME, os.O_RDWR)
        self.addCleanup(os.close, lock)
        fcntl.flock(lock, fcntl.LOCK_EX)
        with patch.object(task_store_module, 'TASK_UPDATE_LOCK_TIMEOUT_SECONDS', 0.2):
            self.assert_refused('ASSIGNMENTS_BUSY', self.store.get_developer_assignments)
            self.assert_refused('ASSIGNMENTS_BUSY', self.assign, d6='claude-dev-x')
        fcntl.flock(lock, fcntl.LOCK_UN)
        self.assertEqual(len(self.store.get_developer_assignments()), 1)

    def test_pm_wait_wakes_when_an_assignment_becomes_unclaimed(self):
        past = datetime.now(timezone.utc) - timedelta(seconds=700)
        self.assign(now=past)
        self.register('claude-dev-367-b')
        calls = []

        def sleep(seconds):
            calls.append(seconds)
            if len(calls) == 2:
                self.assign(d6='claude-dev-367-b', now=past)

        result = active_pm_inbox.wait_inbox(self.store, PM, wait=True, timeout=60,
                                            sleep=sleep)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(result['unclaimed_assignments']), 2)
        # Woken by the newly UNCLAIMED assignment, not by the bound.
        self.assertIs(result['timed_out'], False)

    def test_pm_wait_with_only_old_unclaimed_reports_timed_out(self):
        past = datetime.now(timezone.utc) - timedelta(seconds=700)
        self.assign(now=past)
        clock = iter(range(0, 1000, 5))
        result = active_pm_inbox.wait_inbox(self.store, PM, wait=True, timeout=12,
                                            clock=lambda: next(clock),
                                            sleep=lambda _: None)
        self.assertEqual(len(result['unclaimed_assignments']), 1)
        self.assertIs(result['timed_out'], True)

    def test_claim_delayed_by_lock_contention_past_expiry_is_expired(self):
        start = datetime.now(timezone.utc).replace(microsecond=0)
        current = [start]
        with patch.object(inbox, '_now', lambda now=None: current[0]):
            assigned = self.assign(expires_in=60)
            session = str(uuid.uuid4())
            self.transcript(session, self.account_b)
            real = TaskStore._with_assignments

            def contended(store, **kwargs):
                # The claimer was ready before expiry, but waits on the sidecar
                # lock until after it; the clock moves on while it waits.
                current[0] = start + timedelta(seconds=120)
                return real(store, **kwargs)

            with patch.object(TaskStore, '_with_assignments', contended):
                self.assert_refused('ASSIGNMENT_EXPIRED', self.claim,
                                    assigned['assignment_id'], session=session)
        item = self.stored(assigned['assignment_id'])
        self.assertEqual((item['state'], item.get('claim')), ('EXPIRED', None))

    def test_damaged_sidecar_fails_closed_without_hiding_pm_inbox(self):
        self.assign()
        path = self.store_root / task_store_module.ASSIGNMENTS_NAME
        value = json.loads(path.read_text())
        value['assignments'][0]['source_sha'] = '9' * 40
        path.write_text(json.dumps(value))
        self.assert_refused('ASSIGNMENTS_INVALID', self.store.get_developer_assignments)
        result = active_pm_inbox.peek_inbox(self.store, PM)
        self.assertEqual(result['assignments_error_code'], 'ASSIGNMENTS_INVALID')
        self.assertEqual(result['status'], 'ATTENTION')

    def test_cli_assign_assignments_claim_round_trip(self):
        native = PM[3:]

        def run(*argv, account=self.account_a):
            out = StringIO()
            with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': account}), redirect_stdout(out):
                code = main(['task', *argv])
            return code, json.loads(out.getvalue())

        code, assigned = run('assign', '--worker', WORKER, '--d6', D6,
                             '--common-dir', str(self.common_dir), '--pm-session', native,
                             '--expires-in', '60')
        self.assertEqual((code, assigned['state']), (0, 'ASSIGNED'))
        self.assertTrue((self.base / 'home' / '.local' / 'state' / 'llmtool' / 'tasks' /
                         task_store_module.ASSIGNMENTS_NAME).exists())
        code, listed = run('assignments', '--worker', WORKER)
        self.assertEqual([item['assignment_id'] for item in listed['assignments']],
                         [assigned['assignment_id']])
        session = str(uuid.uuid4())
        self.transcript(session, self.account_b)
        claim = ('claim', assigned['assignment_id'], '--worker', WORKER, '--d6', D6,
                 '--session', session)
        code, claimed = run(*claim, account=self.account_b)
        self.assertEqual((code, claimed['claim']['session_id']), (0, session))
        code, again = run(*claim, account=self.account_b)
        self.assertEqual((code, again['claim']), (0, claimed['claim']))
        code, refused = run(*claim, account=self.account_a)
        self.assertEqual((code, refused), (1, {'code': 'ASSIGNMENT_ALREADY_CLAIMED'}))
        code, refused = run('assignments', '--worker', WORKER, '--wait', '--timeout', '601')
        self.assertEqual((code, refused), (1, {'code': 'ASSIGNMENT_WAIT_TIMEOUT_INVALID'}))
        self.gate.side_effect = paused
        self.register('claude-dev-367-b')
        code, refused = run('assign', '--worker', WORKER, '--d6', 'claude-dev-367-b',
                            '--common-dir', str(self.common_dir), '--pm-session', native)
        self.assertEqual((code, refused), (1, {'code': 'OWNER_PAUSED'}))
        # run_task_claim honours CLAUDE_CONFIG_DIR for the claimer account.
        self.gate.side_effect = allow
        out = StringIO()
        with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': self.account_b}):
            code = run_task_claim(Namespace(assignment_id=assigned['assignment_id'],
                                            worker=WORKER, d6=D6, session=session),
                                  store=TaskStore(self.base / 'home' / '.local' / 'state' /
                                                  'llmtool' / 'tasks'), stdout=out)
        self.assertEqual(code, 0)

    def test_claim_cli_preserves_specific_role_config_refusal_from_claim_gate(self):
        assigned = self.assign()
        session = str(uuid.uuid4())
        self.transcript(session, self.account_b)
        error = LLMToolError('role configuration is invalid')
        error.role_config_code = 'ROLE_ACCOUNT_DIR_INVALID'
        out = StringIO()
        with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': self.account_b}), \
                patch.object(active_pm_inbox, 'observe_claude_session', side_effect=error):
            code = run_task_claim(Namespace(assignment_id=assigned['assignment_id'],
                                            worker=WORKER, d6=D6, session=session),
                                  store=self.store, stdout=out)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out.getvalue()), {'code': 'ROLE_ACCOUNT_DIR_INVALID'})
        self.assertEqual(self.stored(assigned['assignment_id'])['state'], 'ASSIGNED')


class AssignmentChainAcrossAccountsTests(unittest.TestCase):
    """One Task through assign, claim, complete and PM inbox on two Claude accounts.

    The PM runs on account A and the Developer on account B; each account is
    only a temporary CLAUDE_CONFIG_DIR. Fixtures are borrowed, never inherited,
    so no existing test is collected twice.
    """

    setUp_fixture = completion_fixture.TaskCompletionTests.setUp
    _git = completion_fixture.TaskCompletionTests._git
    _trusted_main_ref = completion_fixture.TaskCompletionTests._trusted_main_ref
    _write_registration = completion_fixture.TaskCompletionTests._write_registration
    _write_env2_marker = completion_fixture.TaskCompletionTests._write_env2_marker
    _task = completion_fixture.TaskCompletionTests._task
    _mock_complete_evidence = completion_fixture.TaskCompletionTests._mock_complete_evidence
    _claude_session = pm_fixture.ActivePMInboxTests._claude_session
    _record_pm = pm_fixture.ActivePMInboxTests._record_pm
    _ready_task = pm_fixture.ActivePMInboxTests._ready_task
    _complete = pm_fixture.ActivePMInboxTests._complete
    assert_refused = DeveloperAssignmentInboxTests.assert_refused

    def setUp(self):
        self.setUp_fixture()
        self.account_a = str(self.base / 'claude-account-a')
        self.account_b = str(self.base / 'claude-account-b')
        self.pm_cwd = self.base / 'pm-root'
        self.pm_cwd.mkdir()
        env = patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': self.account_a,
                                      'HOME': str(self.base / 'home')})
        env.start()
        self.addCleanup(env.stop)
        gate = patch('llmtool.worker_dispatch.evaluate', side_effect=allow)
        gate.start()
        self.addCleanup(gate.stop)
        self.session = self._claude_session()
        self.key = 'pm-' + self.session
        self.store = TaskStore(self.base / 'tasks')

    def fresh_store(self):
        return TaskStore(self.base / 'tasks')

    def assign(self, task, **kwargs):
        return inbox.assign(self.store, worker=WORKER, d6_task_id=self.task_id,
                            common_dir=self.common, pm_session_key=self.key,
                            task_id=task['task_id'], **kwargs)

    def d6_transcript(self, session, account):
        path = active_pm_inbox.claude_session_file(session, str(self.root),
                                                   {'CLAUDE_CONFIG_DIR': account})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}\n')

    def claim_as_b(self, assignment_id, session):
        return inbox.claim(self.fresh_store(), assignment_id, worker=WORKER,
                           d6_task_id=self.task_id, session_id=session,
                           environ={'CLAUDE_CONFIG_DIR': self.account_b})

    def test_one_task_crosses_both_accounts_from_assign_to_pm_ack(self):
        task = self._ready_task()
        assigned = self.assign(task)
        assignment_id = assigned['assignment_id']
        self.assertEqual((assigned['state'], assigned['task_id'], assigned['source_sha']),
                         ('ASSIGNED', task['task_id'], self.source_sha))

        # A session known only to the PM's account cannot claim as account B.
        pm_only = str(uuid.uuid4())
        self.d6_transcript(pm_only, self.account_a)
        self.assert_refused('ASSIGNMENT_SESSION_NOT_FOUND', self.claim_as_b,
                            assignment_id, pm_only)

        session = str(uuid.uuid4())
        self.d6_transcript(session, self.account_b)
        claimed = self.claim_as_b(assignment_id, session)
        receipt = claimed['claim']
        self.assertEqual((claimed['state'], receipt['session_id']), ('CLAIMED', session))
        self.assertEqual(receipt['account_dir_label'],
                         inbox.account_dir_label({'CLAUDE_CONFIG_DIR': self.account_b}))
        self.assertNotEqual(receipt['account_dir_label'],
                            inbox.account_dir_label({'CLAUDE_CONFIG_DIR': self.account_a}))

        # The Developer completes on account B, where the PM has no transcript.
        with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': self.account_b}):
            code, completed = self._complete(task)
        self.assertEqual((code, completed['completion']['state']), (0, 'PM_EVIDENCE_READY'))
        self.assertFalse(active_pm_inbox.claude_session_file(
            self.session, str(self.pm_cwd), {'CLAUDE_CONFIG_DIR': self.account_b}).exists())

        other = str(uuid.uuid4())
        self.d6_transcript(other, self.account_b)
        self.assert_refused('ASSIGNMENT_ALREADY_CLAIMED', self.claim_as_b,
                            assignment_id, other)
        self.assertEqual(self.claim_as_b(assignment_id, session)['claim'], receipt)

        # The PM reads on account A through its own store view.
        view = active_pm_inbox.read_inbox(self.fresh_store(), self.key)
        (event,) = view['events']
        self.assertEqual(event['task_id'], task['task_id'])
        self.assertIs(event['current'], True)
        self.assertEqual(event['source'], 'DIRECT_D6_DEVELOPER')
        self.assertEqual(event['active_pm_handoff']['state'], 'PM_EVIDENCE_READY')
        self.assertEqual(event['pm_recovery']['resume_argv'],
                         ['claude', '--resume', self.session])
        self.assertEqual(view['status'], 'OK')
        self.assertEqual(view['unclaimed_assignments'], [])
        (seen,) = view['claimed_assignments']
        self.assertEqual((seen['assignment_id'], seen['task_id'], seen['claim']),
                         (assignment_id, task['task_id'], receipt))

        store = self.fresh_store()
        active_pm_inbox.ack(store, event['event_id'], self.key)
        after = active_pm_inbox.read_inbox(store, self.key)
        self.assertEqual(after['events'], [])
        self.assertEqual([item['claim'] for item in after['claimed_assignments']], [receipt])

        # A claimed assignment never turns UNCLAIMED once its expiry time passes.
        later = (datetime.strptime(assigned['expires_at'], '%Y-%m-%dT%H:%M:%SZ')
                 .replace(tzinfo=timezone.utc) + timedelta(hours=1))
        unclaimed, still_claimed = inbox.pm_assignments(self.fresh_store(), self.key, now=later)
        self.assertEqual(unclaimed, [])
        self.assertEqual([(item['assignment_id'], item['state'], item['claim'])
                          for item in still_claimed],
                         [(assignment_id, 'CLAIMED', receipt)])

    def test_task_bound_assignment_past_expiry_is_unclaimed_and_late_claim_refused(self):
        task = self._ready_task()
        past = datetime.now(timezone.utc) - timedelta(seconds=700)
        assigned = self.assign(task, now=past)
        expected = [(assigned['assignment_id'], task['task_id'], 'UNCLAIMED')]

        def pm_unclaimed():
            view = active_pm_inbox.peek_inbox(self.fresh_store(), self.key)
            self.assertEqual(view['status'], 'ATTENTION')
            return [(item['assignment_id'], item['task_id'], item['state'])
                    for item in view['unclaimed_assignments']]

        self.assertEqual(pm_unclaimed(), expected)
        session = str(uuid.uuid4())
        self.d6_transcript(session, self.account_b)
        self.assert_refused('ASSIGNMENT_EXPIRED', self.claim_as_b,
                            assigned['assignment_id'], session)
        (stored,) = self.fresh_store().get_developer_assignments()
        self.assertEqual(stored['state'], 'EXPIRED')
        self.assertEqual(pm_unclaimed(), expected)


if __name__ == '__main__':
    unittest.main()
