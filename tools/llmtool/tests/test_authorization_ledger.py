"""A11b.3 D1 one-use receipt ledger: at-most-once fake effects, fail-closed store."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from llmtool import authorization_ledger as l
from llmtool import authorization_policy as p
from test_authorization_policy import REDEEM_RUN, allow, make_create, make_observed, make_policy

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent


class FakeSink(l.EffectSink):
    """Synthetic sink: records bindings in memory; touches nothing else."""

    def __init__(self, kind=p.EffectKind.FAKE_FILE_CREATE, error=None):
        self.kind, self.error, self.calls = kind, error, []

    @property
    def effect_kind(self):
        return self.kind

    def apply(self, binding):
        self.calls.append(binding)
        if self.error is not None:
            raise self.error


class Clock:
    def __init__(self, now=1000):
        self.now = now

    def __call__(self):
        return self.now


# Child process for crash windows and the two-process race. Its sink appends
# one line to a fixture file in the test's own temporary directory.
CHILD = r'''
import json, os, sys
from llmtool import authorization_ledger as l, authorization_policy as p
from test_authorization_policy import make_observed
spec = json.loads(sys.argv[1])
raw = dict(spec['binding'], action_paths=tuple(spec['binding']['action_paths']))
receipt = l.Receipt(p.ReceiptBinding(**raw), spec['issued_at'], spec['expires_at'], spec['nonce'])

class Sink(l.EffectSink):
    effect_kind = p.EffectKind.FAKE_FILE_CREATE

    def apply(self, binding):
        if spec['crash'] == 'before_effect':
            os._exit(17)
        with open(spec['effects'], 'a') as handle:
            handle.write(binding.action_digest + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        if spec['crash'] == 'after_effect':
            os._exit(17)

if spec['crash'] == 'before_commit':
    # Die after the CONSUMED update but before its COMMIT (hot journal left).
    import sqlite3

    class CrashBeforeCommit(sqlite3.Connection):
        consumed = False

        def execute(self, sql, *args):
            if sql.startswith("UPDATE receipts SET status = 'CONSUMED'"):
                CrashBeforeCommit.consumed = True
            elif sql == 'COMMIT' and CrashBeforeCommit.consumed:
                os._exit(17)
            return super().execute(sql, *args)
    real_connect = sqlite3.connect
    sqlite3.connect = lambda *a, **k: real_connect(*a, factory=CrashBeforeCommit, **k)

if spec['wait']:
    sys.stdin.readline()
try:
    ledger = l.ReceiptLedger.open(spec['path'], clock=lambda: spec['now'])
    result = ledger.redeem(receipt, redeem_run_id=spec['run'], observed=make_observed(), sink=Sink())
    print(result.code.value)
except l.LedgerError as error:
    print(error.code.value)
'''


class LedgerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='d1-ledger-')
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        os.chmod(self.dir, 0o700)
        self.path = self.dir / 'receipts.sqlite3'
        self.clock = Clock()
        self.ledger = l.ReceiptLedger.create(self.path, clock=self.clock)
        self.addCleanup(lambda: self.ledger.close())

    def issue(self, ledger=None, ttl=60, **changes):
        # One receipt per exact binding: each helper issue gets its own decision
        # (a distinct reviewed policy version) unless a policy is given.
        if 'policy' not in changes:
            self.versions = getattr(self, 'versions', 100) + 1
            changes['policy'] = make_policy(version=self.versions)
        return (ledger or self.ledger).issue(allow(**changes), ttl_seconds=ttl)

    def count_receipts(self):
        db = sqlite3.connect(self.path)
        try:
            return db.execute('SELECT count(*) FROM receipts').fetchone()[0]
        finally:
            db.close()

    def redeem(self, receipt, ledger=None, sink=None, run=REDEEM_RUN, observed=None):
        sink = sink or FakeSink()
        result = (ledger or self.ledger).redeem(receipt, redeem_run_id=run,
            observed=observed or make_observed(), sink=sink)
        return result, sink

    def assertLedgerError(self, code, function, *args, **kwargs):
        with self.assertRaises(l.LedgerError) as caught:
            function(*args, **kwargs)
        self.assertIs(caught.exception.code, code)

    def child(self, receipt, crash='none', wait=False, now=None):
        spec = dict(binding=json.loads(receipt.binding.to_json()), issued_at=receipt.issued_at,
                    expires_at=receipt.expires_at, nonce=receipt.nonce, crash=crash, wait=wait,
                    path=str(self.path), run=REDEEM_RUN, now=now or self.clock.now,
                    effects=str(self.dir / 'effects.log'))
        env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(ROOT), str(TESTS))))
        return subprocess.Popen([sys.executable, '-c', CHILD, json.dumps(spec)], env=env,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)

    def effects(self):
        path = self.dir / 'effects.log'
        return path.read_text().splitlines() if path.exists() else []


class ReceiptLifecycleTests(LedgerCase):
    def test_one_host_grant_yields_one_fake_effect_then_replay_is_denied(self):
        receipt = self.issue()
        self.assertEqual(self.ledger.status(receipt.nonce), 'ISSUED')
        self.assertEqual((receipt.issued_at, receipt.expires_at), (1000, 1060))
        result, sink = self.redeem(receipt)
        self.assertIs(result.code, l.RedeemCode.FAKE_EFFECT_DONE)
        self.assertTrue(result.effect_done)
        self.assertEqual(sink.calls, [receipt.binding])
        self.assertEqual(self.ledger.status(receipt.nonce), 'EFFECT_DONE')
        again, second = self.redeem(receipt)
        self.assertIs(again.code, l.RedeemCode.REPLAY)
        self.assertFalse(again.effect_done)
        self.assertEqual(second.calls, [])
        self.assertEqual(sorted(os.listdir(self.dir)), ['receipts.sqlite3'])

    def test_replay_after_restart_is_denied(self):
        receipt = self.issue()
        self.redeem(receipt)
        self.ledger.close()
        with l.ReceiptLedger.open(self.path, clock=self.clock) as reopened:
            result, sink = self.redeem(receipt, reopened)
            self.assertIs(result.code, l.RedeemCode.REPLAY)
            self.assertEqual(sink.calls, [])

    def test_only_an_allow_decision_from_decide_can_issue(self):
        for decision in (allow(requirements=frozenset((p.Requirement.NEW_COST,))),
                         allow(requirements=frozenset((p.Requirement.OWNER_P0_ADMIN_PRIVILEGE,))),
                         allow().binding, json.loads(allow().binding.to_json()), None):
            with self.subTest(decision=type(decision).__name__):
                self.assertLedgerError(l.LedgerCode.NOT_ALLOWED, self.ledger.issue, decision,
                                       ttl_seconds=60)
        for ttl in (0, -1, 3601, 60.0, True):
            with self.subTest(ttl=ttl):
                self.assertLedgerError(l.LedgerCode.NOT_ALLOWED, self.ledger.issue, allow(),
                                       ttl_seconds=ttl)
        self.assertEqual(self.count_receipts(), 0)

    def test_forged_or_tampered_receipt_never_reaches_the_sink(self):
        receipt = self.issue()
        unknown = replace(receipt, nonce='f'*64)
        result, sink = self.redeem(unknown)
        self.assertIs(result.code, l.RedeemCode.RECEIPT_UNKNOWN)
        self.assertEqual(sink.calls, [])
        for forged in (None, receipt.to_dict(), json.loads(receipt.binding.to_json())):
            result, sink = self.redeem(forged)
            self.assertIs(result.code, l.RedeemCode.RECEIPT_UNKNOWN)
            self.assertEqual(sink.calls, [])
        self.assertEqual(self.ledger.status(receipt.nonce), 'ISSUED')
        changes = dict(action_digest='c'*64, policy_digest='c'*64, capability_id='other-host',
                       index_sha256='c'*64, content_sha256='c'*64, contract_digest='c'*64,
                       task_run_id='c'*32, policy_version=2, parent_inode=31)
        for field, value in changes.items():
            with self.subTest(field=field):
                fresh = self.issue()
                tampered = replace(fresh, binding=replace(fresh.binding, **{field: value}))
                result, sink = self.redeem(tampered, observed=None)
                self.assertIs(result.code, l.RedeemCode.RECEIPT_TAMPERED)
                self.assertEqual(sink.calls, [])
                self.assertEqual(self.ledger.status(fresh.nonce), 'DENIED')
                self.assertIs(self.redeem(fresh)[0].code, l.RedeemCode.REPLAY)
        for field in ('issued_at', 'expires_at'):
            with self.subTest(field=field):
                fresh = self.issue()
                result, sink = self.redeem(replace(fresh, **{field: getattr(fresh, field) + 1}))
                self.assertIs(result.code, l.RedeemCode.RECEIPT_TAMPERED)
                self.assertEqual(sink.calls, [])

    def test_fresh_redemption_state_run_and_sink_must_match(self):
        observed = make_observed()
        repository = observed.repository
        mismatches = {
            'repository id': dict(repository=replace(repository, repository_id='c'*64)),
            'canonical root': dict(repository=replace(repository, canonical_root='/synthetic/other')),
            'root device': dict(repository=replace(repository, root_device=11)),
            'root inode': dict(repository=replace(repository, root_inode=21)),
            'expected head': dict(repository=replace(repository, expected_head='c'*40)),
            'observed head': dict(head='c'*40),
            'index': dict(index_sha256='c'*64),
            'worktree': dict(worktree_sha256='c'*64),
            'target path': dict(target_path='other.txt'),
            'target now present': dict(target_absent=False),
            'parent device': dict(parent_device=11),
            'parent inode': dict(parent_inode=31),
        }
        for label, change in mismatches.items():
            with self.subTest(label=label):
                receipt = self.issue()
                result, sink = self.redeem(receipt, observed=replace(observed, **change))
                self.assertIs(result.code, l.RedeemCode.BINDING_MISMATCH)
                self.assertEqual(sink.calls, [])
                self.assertEqual(self.ledger.status(receipt.nonce), 'DENIED')
        receipt = self.issue()
        result, sink = self.redeem(receipt, run='b'*32)
        self.assertIs(result.code, l.RedeemCode.RUN_MISMATCH)
        for bad_sink in (FakeSink(kind='FAKE_FILE_CREATE'), FakeSink(kind=None), object()):
            with self.subTest(sink=type(bad_sink).__name__):
                receipt = self.issue()
                result = self.ledger.redeem(receipt, redeem_run_id=REDEEM_RUN,
                                            observed=observed, sink=bad_sink)
                self.assertIs(result.code, l.RedeemCode.SINK_MISMATCH)
        receipt = self.issue()
        self.assertIs(self.redeem(receipt, observed=observed.to_dict())[0].code,
                      l.RedeemCode.BINDING_MISMATCH)

    def test_one_decision_yields_at_most_one_receipt_in_any_status(self):
        decision = allow()
        receipt = self.ledger.issue(decision, ttl_seconds=60)
        self.assertLedgerError(l.LedgerCode.NOT_ALLOWED, self.ledger.issue, decision,
                               ttl_seconds=60)
        self.assertEqual(self.count_receipts(), 1)
        result, sink = self.redeem(receipt)
        self.assertIs(result.code, l.RedeemCode.FAKE_EFFECT_DONE)
        calls = list(sink.calls)
        for _ in range(3):
            self.assertLedgerError(l.LedgerCode.NOT_ALLOWED, self.ledger.issue, decision,
                                   ttl_seconds=60)
            # A fresh decide() for the same action, run and policy is the same binding.
            self.assertLedgerError(l.LedgerCode.NOT_ALLOWED, self.ledger.issue, allow(),
                                   ttl_seconds=30)
            self.assertIs(self.redeem(receipt)[0].code, l.RedeemCode.REPLAY)
        self.assertEqual(calls, [receipt.binding])
        self.assertEqual(self.count_receipts(), 1)
        self.assertEqual(self.ledger.status(receipt.nonce), 'EFFECT_DONE')
        # A new redeem run is a new decision and binding.
        other = self.ledger.issue(allow(redeem_run_id='b'*32), ttl_seconds=60)
        self.assertIs(self.redeem(other, run='b'*32)[0].code, l.RedeemCode.FAKE_EFFECT_DONE)
        self.assertEqual(self.count_receipts(), 2)

    def test_run_id_must_be_the_exact_bound_string(self):
        class AlwaysEqual(str):
            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False
            __hash__ = str.__hash__
        for run in (AlwaysEqual('b'*32), AlwaysEqual(REDEEM_RUN), None, b'a'*32):
            with self.subTest(run=type(run).__name__):
                receipt = self.issue()
                result, sink = self.redeem(receipt, run=run)
                self.assertIs(result.code, l.RedeemCode.RUN_MISMATCH)
                self.assertEqual(sink.calls, [])
                self.assertEqual(self.ledger.status(receipt.nonce), 'DENIED')

    def test_last_valid_second_redeems_and_expiry_boundary_is_exclusive(self):
        receipt = self.issue(ttl=60)
        self.clock.now = 1059.9
        self.assertIs(self.redeem(receipt)[0].code, l.RedeemCode.FAKE_EFFECT_DONE)
        receipt = self.issue(ttl=1)
        self.assertEqual((receipt.issued_at, receipt.expires_at), (1059, 1060))
        self.clock.now = 1060
        self.assertIs(self.redeem(receipt)[0].code, l.RedeemCode.EXPIRED)

    def test_expiry_is_host_clock_based_and_final(self):
        receipt = self.issue(ttl=60)
        self.clock.now = 1060
        result, sink = self.redeem(receipt)
        self.assertIs(result.code, l.RedeemCode.EXPIRED)
        self.assertEqual(sink.calls, [])
        self.assertEqual(self.ledger.status(receipt.nonce), 'EXPIRED')
        self.assertIs(self.redeem(receipt)[0].code, l.RedeemCode.REPLAY)

    def test_clock_rollback_and_invalid_clock_fail_closed_durably(self):
        receipt = self.issue()
        self.clock.now = 999
        self.assertLedgerError(l.LedgerCode.CLOCK_ROLLBACK, self.redeem, receipt)
        self.assertLedgerError(l.LedgerCode.CLOCK_ROLLBACK, self.issue)
        self.ledger.close()
        with l.ReceiptLedger.open(self.path, clock=self.clock) as reopened:
            self.assertLedgerError(l.LedgerCode.CLOCK_ROLLBACK, self.redeem, receipt, reopened)
        self.clock.now = 1000
        with l.ReceiptLedger.open(self.path, clock=self.clock) as reopened:
            self.assertEqual(reopened.status(receipt.nonce), 'ISSUED')
            for bad in ('1000', None, -1, True, 2**40, float('nan'), float('inf')):
                with self.subTest(clock=bad):
                    reopened._clock = lambda: bad
                    self.assertLedgerError(l.LedgerCode.CLOCK_INVALID, self.redeem, receipt, reopened)

            def broken():
                raise OSError('clock unavailable')
            reopened._clock = broken
            self.assertLedgerError(l.LedgerCode.CLOCK_INVALID, self.redeem, receipt, reopened)
        self.assertLedgerError(l.LedgerCode.CLOCK_INVALID, l.ReceiptLedger.open, self.path, clock=1000)

    def test_negative_clock_is_invalid_even_on_a_fresh_store(self):
        path = self.dir / 'fresh.sqlite3'
        for bad in (-0.5, -0.0 - 1e-9, -1):
            with self.subTest(clock=bad):
                with l.ReceiptLedger.create(path, clock=lambda: bad) as fresh:
                    self.assertLedgerError(l.LedgerCode.CLOCK_INVALID, fresh.status, 'f'*64)
                    self.assertLedgerError(l.LedgerCode.CLOCK_INVALID, fresh.issue, allow(),
                                           ttl_seconds=60)
                    self.assertLedgerError(l.LedgerCode.CLOCK_INVALID, fresh.recover)
                os.replace(path, self.dir / ('used-%d.sqlite3' % len(os.listdir(self.dir))))

    def test_sink_failure_is_effect_unknown_never_success(self):
        receipt = self.issue()
        result, sink = self.redeem(receipt, sink=FakeSink(error=RuntimeError('fake sink failed')))
        self.assertIs(result.code, l.RedeemCode.EFFECT_UNKNOWN)
        self.assertFalse(result.effect_done)
        self.assertEqual(len(sink.calls), 1)
        self.assertEqual(self.ledger.status(receipt.nonce), 'EFFECT_UNKNOWN')
        self.assertIs(self.redeem(receipt)[0].code, l.RedeemCode.REPLAY)

    def test_sink_base_exception_is_recorded_unknown_then_reraised(self):
        for error in (KeyboardInterrupt(), SystemExit(3)):
            with self.subTest(error=type(error).__name__):
                receipt = self.issue()
                sink = FakeSink(error=error)
                with self.assertRaises(type(error)):
                    self.redeem(receipt, sink=sink)
                self.assertEqual(len(sink.calls), 1)
                self.assertEqual(self.ledger.status(receipt.nonce), 'EFFECT_UNKNOWN')
                result, again = self.redeem(receipt)
                self.assertIs(result.code, l.RedeemCode.REPLAY)
                self.assertEqual(again.calls, [])

    def test_finalization_failure_reports_effect_unknown(self):
        receipt = self.issue()
        sink = FakeSink()
        original = sink.apply

        def apply_then_roll_clock_back(binding):
            original(binding)
            self.clock.now = 1
        sink.apply = apply_then_roll_clock_back
        result, _ = self.redeem(receipt, sink=sink)
        self.assertIs(result.code, l.RedeemCode.EFFECT_UNKNOWN)
        self.clock.now = 1000
        self.assertEqual(self.ledger.status(receipt.nonce), 'CONSUMED')
        self.assertEqual(self.ledger.recover(), 1)
        self.assertEqual(self.ledger.status(receipt.nonce), 'EFFECT_UNKNOWN')


class CrashAndRaceTests(LedgerCase):
    def test_crash_before_consume_commit_rolls_back_to_one_usable_receipt(self):
        receipt = self.issue()
        child = self.child(receipt, crash='before_commit')
        out, err = child.communicate(timeout=60)
        self.assertEqual(child.returncode, 17, err)
        self.assertEqual(out, '')
        self.assertTrue(Path(str(self.path) + '-journal').exists())
        self.assertEqual(self.effects(), [])
        # The hot journal is rolled back: nothing was consumed, nothing to recover.
        self.assertEqual(self.ledger.status(receipt.nonce), 'ISSUED')
        self.assertEqual(self.ledger.recover(), 0)
        result, sink = self.redeem(receipt)
        self.assertIs(result.code, l.RedeemCode.FAKE_EFFECT_DONE)
        self.assertEqual(sink.calls, [receipt.binding])
        self.assertIs(self.redeem(receipt)[0].code, l.RedeemCode.REPLAY)
        self.assertFalse(Path(str(self.path) + '-journal').exists())

    def test_crash_in_either_window_never_repeats_or_reports_success(self):
        for window, effects in (('before_effect', 0), ('after_effect', 1)):
            with self.subTest(window=window):
                log = self.dir / 'effects.log'
                if log.exists():
                    log.write_text('')
                receipt = self.issue()
                child = self.child(receipt, crash=window)
                out, err = child.communicate(timeout=60)
                self.assertEqual(child.returncode, 17, err)
                self.assertEqual(out, '')
                self.assertEqual(self.ledger.status(receipt.nonce), 'CONSUMED')
                self.assertEqual(len(self.effects()), effects)
                self.ledger.close()
                self.ledger = l.ReceiptLedger.open(self.path, clock=self.clock)
                self.assertEqual(self.ledger.recover(), 1)
                self.assertEqual(self.ledger.status(receipt.nonce), 'EFFECT_UNKNOWN')
                self.assertEqual(self.ledger.recover(), 0)
                self.assertEqual(self.ledger.status(receipt.nonce), 'EFFECT_UNKNOWN')
                result, sink = self.redeem(receipt)
                self.assertIs(result.code, l.RedeemCode.REPLAY)
                self.assertEqual(sink.calls, [])
                self.assertEqual(len(self.effects()), effects)

    def test_two_processes_racing_one_receipt_produce_one_effect(self):
        for attempt in range(5):
            with self.subTest(attempt=attempt):
                log = self.dir / 'effects.log'
                if log.exists():
                    log.write_text('')
                receipt = self.issue()
                # Children import first and block; all are released together.
                children = [self.child(receipt, wait=True) for _ in range(4)]
                for child in children:
                    child.stdin.write('go\n')
                    child.stdin.flush()
                outputs = []
                for child in children:
                    out, err = child.communicate(timeout=60)
                    self.assertEqual(child.returncode, 0, err)
                    outputs.append(out.strip())
                self.assertEqual(sorted(outputs), ['FAKE_EFFECT_DONE'] + ['REPLAY'] * 3)
                self.assertEqual(self.effects(), [receipt.binding.action_digest])
                self.assertEqual(self.ledger.status(receipt.nonce), 'EFFECT_DONE')


class StoreSecurityTests(LedgerCase):
    def open(self, path=None, **kwargs):
        return l.ReceiptLedger.open(path or self.path, clock=self.clock, **kwargs)

    def test_store_is_private_and_durable_by_configuration(self):
        st = os.lstat(self.path)
        self.assertEqual(st.st_mode & 0o777, 0o600)
        self.assertEqual(st.st_uid, os.geteuid())
        db = self.ledger._db
        self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
        self.assertEqual(db.execute('PRAGMA synchronous').fetchone()[0], 2)
        self.assertEqual(db.execute('PRAGMA fullfsync').fetchone()[0], 1)
        self.assertLedgerError(l.LedgerCode.LEDGER_INSECURE, l.ReceiptLedger.create, self.path,
                               clock=self.clock)

    def test_missing_store_fails_closed(self):
        self.assertLedgerError(l.LedgerCode.LEDGER_UNAVAILABLE, self.open, self.dir / 'missing.sqlite3')
        self.assertLedgerError(l.LedgerCode.LEDGER_UNAVAILABLE, self.open,
                               self.dir / 'no-dir' / 'receipts.sqlite3')

    def test_symlinked_store_or_directory_is_rejected(self):
        link = self.dir / 'link.sqlite3'
        link.symlink_to(self.path)
        self.assertLedgerError(l.LedgerCode.LEDGER_INSECURE, self.open, link)
        alias = self.dir / 'alias'
        alias.symlink_to(self.dir, target_is_directory=True)
        self.assertLedgerError(l.LedgerCode.LEDGER_INSECURE, self.open, alias / 'receipts.sqlite3')
        journal = Path(str(self.path) + '-journal')
        journal.symlink_to(self.dir / 'elsewhere')
        self.assertLedgerError(l.LedgerCode.LEDGER_INSECURE, self.issue)
        journal.unlink()
        self.issue()

    def test_wrong_mode_owner_links_or_shared_directory_are_rejected(self):
        os.chmod(self.path, 0o644)
        self.assertLedgerError(l.LedgerCode.LEDGER_INSECURE, self.open)
        self.assertLedgerError(l.LedgerCode.LEDGER_INSECURE, self.issue)
        os.chmod(self.path, 0o600)
        with patch('llmtool.authorization_ledger.os.geteuid', return_value=os.geteuid() + 1):
            self.assertLedgerError(l.LedgerCode.LEDGER_INSECURE, self.open)
        hardlink = self.dir / 'hardlink.sqlite3'
        os.link(self.path, hardlink)
        self.assertLedgerError(l.LedgerCode.LEDGER_INSECURE, self.open)
        hardlink.unlink()
        os.chmod(self.dir, 0o777)
        try:
            self.assertLedgerError(l.LedgerCode.LEDGER_INSECURE, self.open)
        finally:
            os.chmod(self.dir, 0o700)
        self.open().close()

    def test_store_swapped_after_open_is_rejected(self):
        receipt = self.issue()
        other_dir = self.dir / 'other'
        other_dir.mkdir(mode=0o700)
        l.ReceiptLedger.create(other_dir / 'receipts.sqlite3', clock=self.clock).close()
        os.replace(other_dir / 'receipts.sqlite3', self.path)
        result = None
        with self.assertRaises(l.LedgerError) as caught:
            result = self.redeem(receipt)
        self.assertIs(caught.exception.code, l.LedgerCode.LEDGER_INSECURE)
        self.assertIsNone(result)

    def test_corrupt_or_altered_store_fails_closed(self):
        self.ledger.close()
        alterations = {
            'extra trigger': "CREATE TRIGGER t AFTER UPDATE ON receipts BEGIN SELECT 1; END",
            'extra table': 'CREATE TABLE extra (x)',
            'dropped clock row': 'DELETE FROM clock',
            'schema version': 'PRAGMA user_version = 2',
        }
        for label, statement in alterations.items():
            with self.subTest(label=label):
                path = self.dir / (label.replace(' ', '-') + '.sqlite3')
                l.ReceiptLedger.create(path, clock=self.clock).close()
                db = sqlite3.connect(path)
                db.execute(statement)
                db.commit()
                db.close()
                self.assertLedgerError(l.LedgerCode.LEDGER_CORRUPT, self.open, path)
        garbage = self.dir / 'garbage.sqlite3'
        fd = os.open(garbage, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, b'not a sqlite database' * 200)
        os.close(fd)
        self.assertLedgerError(l.LedgerCode.LEDGER_CORRUPT, self.open, garbage)
        damaged = self.dir / 'damaged.sqlite3'
        l.ReceiptLedger.create(damaged, clock=self.clock).close()
        with open(damaged, 'r+b') as handle:
            handle.seek(100)
            handle.write(b'\xff' * 400)
        self.assertLedgerError(l.LedgerCode.LEDGER_CORRUPT, self.open, damaged)

    def test_os_and_sqlite_failures_surface_only_as_ledger_errors(self):
        broken = sqlite3.OperationalError('unable to open database file')
        with patch.object(l.sqlite3, 'connect', side_effect=broken):
            self.assertLedgerError(l.LedgerCode.LEDGER_UNAVAILABLE, self.open)
        with patch.object(l.sqlite3, 'connect', side_effect=sqlite3.DatabaseError('x')):
            self.assertLedgerError(l.LedgerCode.LEDGER_CORRUPT, self.open)
        with patch.object(l.os, 'fstat', side_effect=OSError(5, 'EIO')):
            self.assertLedgerError(l.LedgerCode.LEDGER_UNAVAILABLE, self.open)
        with patch.object(l.os, 'fsync', side_effect=OSError(5, 'EIO')):
            self.assertLedgerError(l.LedgerCode.LEDGER_UNAVAILABLE, l.ReceiptLedger.create,
                                   self.dir / 'fsync.sqlite3', clock=self.clock)
        with patch.object(l.os, 'fchmod', side_effect=OSError(1, 'EPERM')):
            self.assertLedgerError(l.LedgerCode.LEDGER_UNAVAILABLE, l.ReceiptLedger.create,
                                   self.dir / 'fchmod.sqlite3', clock=self.clock)
        self.open().close()

    def test_failed_create_leaves_a_store_that_never_opens_or_recreates(self):
        path = self.dir / 'half.sqlite3'
        with patch.object(l, '_SCHEMA', l._SCHEMA + ('CREATE TABLE broken (',)):
            self.assertLedgerError(l.LedgerCode.LEDGER_UNAVAILABLE, l.ReceiptLedger.create, path,
                                   clock=self.clock)
        self.assertEqual(os.lstat(path).st_mode & 0o777, 0o600)
        self.assertLedgerError(l.LedgerCode.LEDGER_CORRUPT, self.open, path)
        self.assertLedgerError(l.LedgerCode.LEDGER_INSECURE, l.ReceiptLedger.create, path,
                               clock=self.clock)

    def test_locked_store_times_out_closed_without_issuing(self):
        holder = sqlite3.connect(self.path, isolation_level=None)
        holder.execute('BEGIN EXCLUSIVE')
        try:
            with patch.object(l, 'BUSY_TIMEOUT_SECONDS', 0.1):
                self.assertLedgerError(l.LedgerCode.LEDGER_LOCKED, self.open)
            self.ledger._db.execute('PRAGMA busy_timeout = 100')
            self.assertLedgerError(l.LedgerCode.LEDGER_LOCKED, self.issue)
            self.assertLedgerError(l.LedgerCode.LEDGER_LOCKED, self.ledger.status, 'f'*64)
            self.assertLedgerError(l.LedgerCode.LEDGER_LOCKED, self.ledger.recover)
        finally:
            holder.execute('ROLLBACK')
            holder.close()
        self.assertEqual(self.count_receipts(), 0)


class AdmissionAfterLockTests(LedgerCase):
    """Codex PR #103 D1-R2-01/02: admit time and store only once the lock is held."""

    def test_contention_observes_sqlite_begin_without_sleep(self):
        receipt = self.issue(ttl=2)
        with patch.object(time, 'sleep', side_effect=AssertionError('blind contention sleep')):
            outcome, sink = self.contended_redeem(receipt, release_at=1001)
        self.assertTrue(outcome['begin_immediate_attempted'])
        self.assertIs(outcome['result'].code, l.RedeemCode.FAKE_EFFECT_DONE)
        self.assertEqual(sink.calls, [receipt.binding])

    def test_contention_surfaces_worker_close_failure(self):
        receipt = self.issue(ttl=2)
        original_open = l.ReceiptLedger.open
        failure = RuntimeError('synthetic close failure')

        def open_with_close_failure(*args, **kwargs):
            ledger = original_open(*args, **kwargs)
            close = ledger.close
            def failing_close():
                close()
                raise failure
            ledger.close = failing_close
            return ledger

        with patch.object(l.ReceiptLedger, 'open', side_effect=open_with_close_failure):
            with self.assertRaises(RuntimeError) as caught:
                self.contended_redeem(receipt, release_at=1001)
        self.assertIs(caught.exception, failure)

    def contended_redeem(self, receipt, release_at=None, busy=5.0):
        # Real SQLite writer contention: another connection holds the write
        # lock while the redeem waits in BEGIN IMMEDIATE.
        holder = sqlite3.connect(self.path, isolation_level=None)
        holder.execute('BEGIN IMMEDIATE')
        reached, outcome, sink = threading.Event(), {}, FakeSink()

        def trace(statement):
            if statement == 'BEGIN IMMEDIATE':
                # SQLite itself has started this statement, not merely a
                # Python thread or redeem(). The held writer prevents its
                # admission until rollback (or the real busy timeout).
                outcome['begin_immediate_attempted'] = True
                reached.set()

        def worker():
            ledger = None
            try:
                ledger = l.ReceiptLedger.open(self.path, clock=self.clock)
                ledger._db.set_trace_callback(trace)
                outcome['result'] = ledger.redeem(receipt, redeem_run_id=REDEEM_RUN,
                                                  observed=make_observed(), sink=sink)
            except l.LedgerError as error:
                outcome['error'] = error.code
            except BaseException as error:
                outcome['unexpected'] = error
            finally:
                try:
                    if ledger is not None:
                        ledger.close()
                except BaseException as error:
                    outcome.setdefault('unexpected', error)
                finally:
                    reached.set()  # Surface startup/hook/close errors promptly.
        thread = threading.Thread(target=worker)
        try:
            with patch.object(l, 'BUSY_TIMEOUT_SECONDS', busy):
                try:
                    thread.start()
                    self.assertTrue(reached.wait(10), 'SQLite BEGIN was not observed')
                    if 'unexpected' in outcome:
                        raise outcome['unexpected']
                    self.assertTrue(outcome.get('begin_immediate_attempted'))
                    self.assertTrue(holder.in_transaction)
                    if release_at is not None:
                        self.clock.now = release_at
                        holder.execute('ROLLBACK')
                    thread.join(15)
                finally:
                    if holder.in_transaction:
                        holder.execute('ROLLBACK')
                    if thread.ident is not None:
                        thread.join(15)
                self.assertFalse(thread.is_alive(), 'owned SQLite worker did not stop')
        finally:
            if holder.in_transaction:
                holder.execute('ROLLBACK')
            holder.close()
        if 'unexpected' in outcome:
            raise outcome['unexpected']
        return outcome, sink

    def test_lock_released_after_expiry_is_expired_without_effect(self):
        receipt = self.issue(ttl=1)  # issued 1000, expires 1001
        outcome, sink = self.contended_redeem(receipt, release_at=1002)
        self.assertIs(outcome['result'].code, l.RedeemCode.EXPIRED)
        self.assertEqual(sink.calls, [])
        self.assertEqual(self.ledger.status(receipt.nonce), 'EXPIRED')

    def test_lock_released_before_expiry_redeems_once(self):
        receipt = self.issue(ttl=2)  # issued 1000, expires 1002
        outcome, sink = self.contended_redeem(receipt, release_at=1001)
        self.assertIs(outcome['result'].code, l.RedeemCode.FAKE_EFFECT_DONE)
        self.assertEqual(sink.calls, [receipt.binding])
        self.assertEqual(self.ledger.status(receipt.nonce), 'EFFECT_DONE')

    def test_lock_never_released_times_out_closed(self):
        receipt = self.issue(ttl=60)
        outcome, sink = self.contended_redeem(receipt, busy=0.2)
        self.assertIs(outcome['error'], l.LedgerCode.LEDGER_LOCKED)
        self.assertEqual(sink.calls, [])
        self.assertEqual(self.ledger.status(receipt.nonce), 'ISSUED')

    def make_inner(self):
        inner = self.dir / 'inner'
        inner.mkdir(mode=0o700)
        path = inner / 'receipts.sqlite3'
        ledger = l.ReceiptLedger.create(path, clock=self.clock)
        self.addCleanup(ledger.close)
        return inner, path, ledger

    def assertAllRefused(self, ledger, receipt, code=l.LedgerCode.LEDGER_INSECURE):
        sink = FakeSink()
        self.assertLedgerError(code, self.issue, ledger)
        self.assertLedgerError(code, self.redeem, receipt, ledger, sink)
        self.assertLedgerError(code, ledger.recover)
        self.assertLedgerError(code, ledger.status, receipt.nonce)
        self.assertEqual(sink.calls, [])

    def test_open_ledger_refuses_after_directory_becomes_shared(self):
        inner, path, ledger = self.make_inner()
        receipt = self.issue(ledger)
        for mode in (0o777, 0o770, 0o702):
            with self.subTest(mode=oct(mode)):
                os.chmod(inner, mode)
                try:
                    self.assertAllRefused(ledger, receipt)
                finally:
                    os.chmod(inner, 0o700)
        result, sink = self.redeem(receipt, ledger)
        self.assertIs(result.code, l.RedeemCode.FAKE_EFFECT_DONE)
        self.assertEqual(len(sink.calls), 1)

    def test_open_ledger_refuses_replaced_or_symlinked_directory(self):
        inner, path, ledger = self.make_inner()
        receipt = self.issue(ledger)
        moved = self.dir / 'inner-moved'
        os.rename(inner, moved)
        # A different directory now holds the very same database inode.
        inner.mkdir(mode=0o700)
        os.rename(moved / 'receipts.sqlite3', path)
        self.assertEqual(os.lstat(path).st_nlink, 1)
        self.assertAllRefused(ledger, receipt)
        # The original directory back behind a symlink is refused as well.
        os.rename(path, moved / 'receipts.sqlite3')
        inner.rmdir()
        inner.symlink_to(moved, target_is_directory=True)
        self.assertAllRefused(ledger, receipt)
        self.assertEqual(self.count_receipts_at(moved / 'receipts.sqlite3'), ['ISSUED'])

    def count_receipts_at(self, path):
        db = sqlite3.connect(path)
        try:
            return [row[0] for row in db.execute('SELECT status FROM receipts')]
        finally:
            db.close()


class ProviderLedgerTests(unittest.TestCase):
    """Provider mechanics with synthetic issuer; not Host attestation proof."""
    def setUp(self):
        from test_review_authority import Harness
        from llmtool.review_authority import ProductionReviewIssuer
        self.h=Harness(cap=1);self.addCleanup(self.h.close)
        class MechanicsIssuer(ProductionReviewIssuer):
            def matches(inner,call,observed): return observed.attested
        self.issuer=MechanicsIssuer(self.h.pin,self.h.clock,self.h.observer,self.h.context)
        self.base,_=self.h.binding()

    def issue(self,tag='1',ttl=120):
        call=replace(self.base,packet_digest=tag*64,deadline='2026-10-14T00:05:00Z')
        binding=p.ProviderReviewBinding(call)
        observed=p.ProviderObservedState(binding.digest,call.source_sha,call.observation_digest,True)
        decision=p.decide_provider_review(binding,issuer=self.issuer,observed=observed)
        self.assertIs(decision.decision,p.Decision.ALLOW_WITH_TRUSTED_RECEIPT)
        return self.h.ledger.issue_provider(decision,ttl_seconds=ttl),observed

    def consume(self,receipt,observed):
        return self.h.ledger.consume_provider(receipt,issuer=self.issuer,observed=observed)

    def test_distinct_layout_cross_open_and_creation_nonce(self):
        with self.assertRaises(l.LedgerError):l.ReceiptLedger.open(self.h.ledger_path,clock=self.h.clock.utc)
        with self.assertRaises(l.LedgerError):l.ReceiptLedger.open(self.h.ledger_path,clock=self.h.clock.utc,
            kind='PROVIDER_REVIEW',creation_nonce='0'*64)
        path=self.h.hostdir/'d1.sqlite3'
        with l.ReceiptLedger.create(path,clock=self.h.clock.utc):pass
        with self.assertRaises(l.LedgerError):l.ReceiptLedger.open(path,clock=self.h.clock.utc,
            kind='PROVIDER_REVIEW',creation_nonce=self.h.ledger.creation_nonce)

    def test_same_object_new_redeem_run_never_reissued(self):
        receipt,observed=self.issue()
        call=replace(receipt.binding.call,redeem_run_id='e'*32,reviewer_launch_nonce='d'*32)
        b=p.ProviderReviewBinding(call);o=p.ProviderObservedState(b.digest,call.source_sha,call.observation_digest,True)
        decision=p.decide_provider_review(b,issuer=self.issuer,observed=o)
        with self.assertRaises(l.LedgerError):self.h.ledger.issue_provider(decision,ttl_seconds=120)
        self.assertTrue(self.consume(receipt,observed))
        with self.assertRaises(l.LedgerError):self.consume(receipt,observed)

    def test_atomic_cap_keeps_unconsumed_row_issued(self):
        first,o1=self.issue('1');second,o2=self.issue('2')
        self.assertTrue(self.consume(first,o1));self.assertFalse(self.consume(second,o2))
        expected='DENIED' if self.h.ledger.provider_layout == 2 else 'ISSUED'
        self.assertEqual(self.h.ledger.status(second.nonce),expected)
        self.assertTrue(self.h.ledger.finalize_provider(first,'EFFECT_UNKNOWN'))
        if expected == 'ISSUED': self.assertFalse(self.consume(second,o2))
        else:
            with self.assertRaises(l.LedgerError): self.consume(second,o2)

    def test_day_is_consume_day_not_issue_or_finalize_day(self):
        self.h.fake.utc='2026-10-13T23:59:59Z'
        receipt,o=self.issue('1')
        self.h.fake.utc='2026-10-14T00:00:00Z'
        self.assertTrue(self.consume(receipt,o))
        row=self.h.ledger._db.execute('SELECT issued_at,consumed_at,consumed_day FROM receipts').fetchone()
        self.assertNotEqual(row[0]//86400,row[2]);self.assertEqual(row[1]//86400,row[2])
        self.h.fake.utc='2026-10-14T00:00:01Z'
        self.h.ledger.finalize_provider(receipt,'EFFECT_DONE')
        self.assertEqual(row,self.h.ledger._db.execute('SELECT issued_at,consumed_at,consumed_day FROM receipts').fetchone())

    def test_recovery_unknown_remains_charged_and_write_once(self):
        receipt,o=self.issue();self.assertTrue(self.consume(receipt,o))
        charge=self.h.ledger._db.execute('SELECT consumed_at,consumed_day FROM receipts').fetchone()
        self.assertEqual(self.h.ledger.recover(),1)
        self.assertEqual(self.h.ledger.status(receipt.nonce),'EFFECT_UNKNOWN')
        self.assertEqual(charge,self.h.ledger._db.execute('SELECT consumed_at,consumed_day FROM receipts').fetchone())
        second,o2=self.issue('2');self.assertFalse(self.consume(second,o2))
        self.assertFalse(self.h.ledger.finalize_provider(receipt,'EFFECT_DONE'))

    def test_clock_rollback_denied_before_consume(self):
        receipt,o=self.issue();self.h.fake.utc='2026-10-13T00:00:09Z'
        with self.assertRaises(l.LedgerError) as caught:self.consume(receipt,o)
        self.assertIs(caught.exception.code,l.LedgerCode.CLOCK_ROLLBACK)
        self.h.fake.utc='2026-10-13T00:00:10Z'
        self.assertEqual(self.h.ledger.status(receipt.nonce),'ISSUED')

    def test_expiry_is_durable_zero_effect(self):
        receipt,o=self.issue(ttl=1);self.h.fake.utc='2026-10-13T00:00:11Z'
        self.assertFalse(self.consume(receipt,o));self.assertEqual(self.h.ledger.status(receipt.nonce),'EXPIRED')

    def test_corrupt_consume_day_rejected(self):
        receipt,o=self.issue();self.consume(receipt,o)
        db=sqlite3.connect(self.h.ledger_path)
        db.execute('PRAGMA ignore_check_constraints=ON');db.execute('UPDATE receipts SET consumed_day=0');db.commit();db.close()
        with self.assertRaises(l.LedgerError) as caught:self.h.ledger.status(receipt.nonce)
        self.assertIs(caught.exception.code,l.LedgerCode.LEDGER_CORRUPT)

    def test_revocation_at_consume_denies_cached_admission(self):
        receipt,o=self.issue()
        self.h.hostdata['route_policies'][0]['revoked']=True;self.h.write_host()
        self.assertFalse(self.consume(receipt,o));self.assertEqual(self.h.ledger.status(receipt.nonce),'DENIED')

    def test_wrong_binding_burns_without_consuming_or_charging(self):
        receipt,o=self.issue()
        tampered=replace(receipt,binding=p.ProviderReviewBinding(replace(receipt.binding.call,packet_digest='9'*64)))
        self.assertFalse(self.consume(tampered,o));self.assertEqual(self.h.ledger.status(receipt.nonce),'DENIED')
        self.assertEqual(self.h.ledger._db.execute('SELECT consumed_at,consumed_day FROM receipts').fetchone(),(None,None))


if __name__ == '__main__':
    unittest.main()


class NotificationLedgerTests(unittest.TestCase):
    def setUp(self):
        from notification_fixture import NotificationFixture
        self.t=NotificationFixture();self.addCleanup(self.t.close)
    def observed(self):
        return p.NotificationObserved(self.t.binding.digest,self.t.binding.host_pin,True,True,True,True,True,True,True)
    def issue(self):
        return self.t.ledger.issue_notification(p.decide_notification(self.t.binding,issuer=self.t.issuer,
            observed=self.observed()),ttl_seconds=240)
    def consume(self, receipt):
        return self.t.ledger.consume_notification(receipt,issuer=self.t.issuer,observed=self.observed())
    def test_l3_independent_meta_version_nonce_cross_kind_and_no_provider_counts(self):
        notification=self.t.ledger
        self.assertEqual(notification._db.execute('PRAGMA user_version').fetchone()[0],l.SCHEMA_VERSION)
        self.assertEqual(notification._db.execute('SELECT kind,layout_version,creation_nonce FROM ledger_meta').fetchall(),
                         [('NOTIFICATION',l.NOTIFICATION_SCHEMA_VERSION,notification.creation_nonce)])
        for wrong in ('D1','PROVIDER_REVIEW'):
            with self.assertRaises(l.LedgerError):l.ReceiptLedger.open(self.t.path,clock=self.t.clock,
                kind=wrong,creation_nonce=notification.creation_nonce)
        for kind in ('D1','PROVIDER_REVIEW'):
            path=str(Path(self.t.tmp.name)/('other-'+kind))
            with l.ReceiptLedger.create(path,clock=self.t.clock,kind=kind) as other:
                before=other._db.execute('SELECT sql FROM sqlite_master ORDER BY name').fetchall()
                with self.assertRaises(l.LedgerError):l.ReceiptLedger.open(path,clock=self.t.clock,
                    kind='NOTIFICATION',creation_nonce=notification.creation_nonce)
                self.assertEqual(other._db.execute('SELECT sql FROM sqlite_master ORDER BY name').fetchall(),before)
                self.assertEqual(other._db.execute('SELECT count(*) FROM receipts').fetchone()[0],0)
        columns=[x[1] for x in notification._db.execute('PRAGMA table_info(receipts)')]
        self.assertNotIn('consumed_day',columns);self.assertNotIn('route_digest',columns)
    def test_atomic_consume_replay_pending_and_restart_recovery(self):
        receipt=self.issue();self.assertTrue(self.consume(receipt));self.assertFalse(self.consume(receipt))
        self.assertTrue(self.t.ledger.notification_pending(self.t.binding.reviewer_session))
        self.t.ledger.close()
        self.t.ledger=l.ReceiptLedger.open(self.t.path,clock=self.t.clock,kind='NOTIFICATION',
                                        creation_nonce=self.t.binding.ledger_nonce)
        self.assertEqual(self.t.ledger.recover(),1)
        self.assertEqual(self.t.ledger.status(receipt.nonce),'EFFECT_UNKNOWN')
        self.assertFalse(self.consume(receipt));self.assertTrue(self.t.ledger.notification_pending(self.t.binding.reviewer_session))
    def test_known_zero_rearm_needs_fresh_external_grant_and_original_receipt(self):
        receipt=self.issue();self.assertTrue(self.consume(receipt))
        self.assertTrue(self.t.ledger.finalize_notification(receipt,'ZERO_EFFECT'))
        with self.assertRaises(l.LedgerError):self.issue()
        self.t.binding=replace(self.t.binding,rearm_from=receipt.nonce,grant_digest='c'*64,attempt_id='b'*32)
        self.t.issuer.binding=self.t.binding
        fresh=self.issue();self.assertTrue(self.consume(fresh))
        self.assertTrue(self.t.ledger.begin_notification(fresh))
        with self.assertRaises(l.LedgerError):self.t.ledger.finalize_notification(fresh,'ZERO_EFFECT')
        self.t.ledger.finalize_notification(fresh,'EFFECT_UNKNOWN')
        self.t.binding=replace(self.t.binding,rearm_from=fresh.nonce,grant_digest='d'*64,attempt_id='c'*32)
        self.t.issuer.binding=self.t.binding
        with self.assertRaises(l.LedgerError):self.issue()
    def test_cross_file_nonce_and_provider_d1_methods_reject_notification(self):
        receipt=self.issue()
        with l.ReceiptLedger.create(str(Path(self.t.tmp.name)/'another-notification'),clock=self.t.clock,
                                    kind='NOTIFICATION') as other:
            with self.assertRaises(l.LedgerError):other.consume_notification(receipt,issuer=self.t.issuer,observed=self.observed())
        with self.assertRaises(l.LedgerError):self.t.ledger.issue(p.decide_notification(self.t.binding,
            issuer=self.t.issuer,observed=self.observed()),ttl_seconds=10)
        with self.assertRaises(l.LedgerError):self.t.ledger.issue_provider(p.decide_notification(self.t.binding,
            issuer=self.t.issuer,observed=self.observed()),ttl_seconds=10)
    def test_notification_expiry_revocation_ids_and_blocked_result(self):
        receipt=self.issue();self.t.issuer.revoked=True;self.assertFalse(self.consume(receipt))
        self.assertEqual(self.t.ledger.status(receipt.nonce),'DENIED')
        # A different object permits a separately vouched test grant.
        self.t.issuer.revoked=False;self.t.binding=replace(self.t.binding,source_sha='9'*40,attempt_id='e'*32)
        self.t.issuer.binding=self.t.binding
        receipt=self.issue();self.assertTrue(self.consume(receipt));self.assertTrue(self.t.ledger.begin_notification(receipt))
        self.t.ledger.record_notification_ids(receipt,'m','t')
        with self.assertRaises(l.LedgerError):self.t.ledger.record_notification_ids(receipt,'other','t')
        self.t.ledger.finalize_notification(receipt,'EFFECT_DONE',review_status='BLOCKED',verdict_digest='b'*64)
        self.assertEqual(self.t.ledger.notification_record(self.t.binding.object_key)['review_status'],'BLOCKED')
        self.t.binding=replace(self.t.binding,rearm_from=receipt.nonce,grant_digest='e'*64,attempt_id='f'*32)
        self.t.issuer.binding=self.t.binding
        self.assertIsNotNone(self.issue())


class NativeDeltaLedgerTests(unittest.TestCase):
    def setUp(self):
        from notification_fixture import NotificationFixture
        self.t=NotificationFixture(audit_only=True)
    def tearDown(self):self.t.close()
    def consumed(self):
        t=self.t;o=p.NotificationObserved(t.binding.digest,t.binding.host_pin,True,True,True,True,False,True,True,True,t.binding.risk_acceptance_digest)
        receipt=t.ledger.issue_notification(p.decide_notification(t.binding,issuer=t.issuer,observed=o),ttl_seconds=200)
        self.assertTrue(t.ledger.consume_notification(receipt,issuer=t.issuer,observed=o));self.assertTrue(t.ledger.begin_notification(receipt));t.ledger.record_notification_ids(receipt,'m','t');return receipt
    def test_layout_v2_never_migrates_v1_and_cross_kind_rejects(self):
        t=self.t;before=Path(t.path).read_bytes()
        with self.assertRaises(l.LedgerError):l.ReceiptLedger.open(t.path,clock=t.clock,kind='NOTIFICATION',creation_nonce=t.binding.ledger_nonce)
        self.assertEqual(Path(t.path).read_bytes(),before)
        self.assertEqual(t.ledger._db.execute('SELECT layout_version FROM ledger_meta').fetchone()[0],2)
        self.assertEqual(t.ledger._db.execute('PRAGMA user_version').fetchone()[0],l.SCHEMA_VERSION)
    def test_review_completion_receipt_and_late_unknown_are_forward_only(self):
        receipt=self.consumed();t=self.t
        self.assertTrue(t.ledger.finish_notification_turn(receipt,terminal_status='Completed',coverage_digest='c'*64,child_exited=True,review_status='APPROVE',verdict_digest='d'*64))
        state=t.ledger.notification_completion(receipt);self.assertEqual(state['author_receipt_status'],'NOT_VERIFIED');self.assertFalse(state['pending'])
        self.assertTrue(t.ledger.record_author_receipt(receipt,verdict_digest='d'*64,receipt_digest='e'*64))
        self.assertFalse(t.ledger.record_author_receipt(receipt,verdict_digest='d'*64,receipt_digest='e'*64))
        self.assertTrue(t.ledger.invalidate_notification_completion(receipt));self.assertTrue(t.ledger.notification_completion(receipt)['pending'])
        self.assertFalse(t.ledger.finish_notification_turn(receipt,terminal_status='Completed',coverage_digest='c'*64,child_exited=True,review_status='APPROVE',verdict_digest='d'*64))
    def test_n3_terminal_and_no_false_completion_or_exit(self):
        receipt=self.consumed();t=self.t
        for status,exit in (('InProgress',True),('Failed',False),('Completed',True)):
            with self.assertRaises(l.LedgerError):t.ledger.finish_notification_turn(receipt,terminal_status=status,coverage_digest='c'*64,child_exited=exit)
        self.assertTrue(t.ledger.notification_pending(t.binding.reviewer_session))
        self.assertTrue(t.ledger.finish_notification_turn(receipt,terminal_status='Failed',coverage_digest='c'*64,child_exited=True))
        state=t.ledger.notification_completion(receipt);self.assertEqual(state['terminal_status'],'Failed');self.assertIsNone(state['review_status']);self.assertFalse(state['pending'])
    def test_p2_p4_never_consumed_or_unknown_no_rearm(self):
        receipt=self.consumed();t=self.t;t.ledger.finalize_notification(receipt,'EFFECT_UNKNOWN')
        t.binding=replace(t.binding,rearm_from=receipt.nonce,grant_digest='e'*64,attempt_id='e'*32);t.issuer.binding=t.binding
        o=p.NotificationObserved(t.binding.digest,t.binding.host_pin,True,True,True,True,False,True,True,True,t.binding.risk_acceptance_digest)
        with self.assertRaises(l.LedgerError):t.ledger.issue_notification(p.decide_notification(t.binding,issuer=t.issuer,observed=o),ttl_seconds=100)


class ProviderLayout2Tests(ProviderLedgerTests):
    """#192-A: provider layout 2 reruns every provider mechanic, then adds key release."""

    def setUp(self):
        super().setUp()
        self.layout1, self.path1 = self.h.ledger, self.h.ledger_path
        self.path2 = self.h.hostdir / 'provider-v2.sqlite3'
        self.h.ledger_path = self.path2  # inherited tests tamper with this file
        self.h.ledger = l.ReceiptLedger.create(self.path2, clock=self.h.clock.utc,
                                               kind='PROVIDER_REVIEW', provider_layout=2)
        self.addCleanup(self.layout1.close)
        self.addCleanup(lambda: self.h.ledger.close())

    def again(self, receipt, run='e', ttl=120, ledger=None):
        call = replace(receipt.binding.call, redeem_run_id=run * 32, reviewer_launch_nonce=run * 32)
        b = p.ProviderReviewBinding(call)
        o = p.ProviderObservedState(b.digest, call.source_sha, call.observation_digest, True)
        decision = p.decide_provider_review(b, issuer=self.issuer, observed=o)
        return (ledger or self.h.ledger).issue_provider(decision, ttl_seconds=ttl), o

    def deny(self, receipt, observed):
        self.assertFalse(self.consume(receipt, replace(observed, attested=False)))
        self.assertEqual(self.h.ledger.status(receipt.nonce), 'DENIED')

    def statuses(self, ledger=None):
        return [r[0] for r in (ledger or self.h.ledger)._db.execute(
            'SELECT status FROM receipts ORDER BY issued_at, rowid')]

    def test_layout1_still_holds_key_after_denied_and_expired(self):
        self.h.ledger.close()
        self.h.ledger = self.layout1
        receipt, o = self.issue('1')
        self.deny(receipt, o)
        with self.assertRaises(l.LedgerError) as caught:
            self.again(receipt)
        self.assertIs(caught.exception.code, l.LedgerCode.NOT_ALLOWED)
        expired, o2 = self.issue('2', ttl=1)
        self.h.fake.utc = '2026-10-13T00:00:11Z'
        self.assertFalse(self.consume(expired, o2))
        self.assertEqual(self.h.ledger.status(expired.nonce), 'EXPIRED')
        with self.assertRaises(l.LedgerError):
            self.again(expired)

    def test_denied_and_expired_release_the_key(self):
        receipt, o = self.issue('1')
        self.deny(receipt, o)
        again, o_again = self.again(receipt)
        self.assertTrue(self.consume(again, o_again))
        expired, o2 = self.issue('2', ttl=1)
        self.h.fake.utc = '2026-10-13T00:00:11Z'
        self.assertFalse(self.consume(expired, o2))
        self.assertEqual(self.h.ledger.status(expired.nonce), 'EXPIRED')
        self.again(expired)
        self.assertEqual(self.statuses(), ['DENIED', 'CONSUMED', 'EXPIRED', 'ISSUED'])

    def test_cap_refusal_releases_key_for_same_object_retry(self):
        first, o1 = self.issue('1')
        second, o2 = self.issue('2', ttl=1)
        self.assertTrue(self.consume(first, o1))
        self.assertFalse(self.consume(second, o2))  # daily cap 1: zero effect
        self.assertEqual(self.h.ledger.status(second.nonce), 'DENIED')
        row = self.h.ledger._db.execute('SELECT consumed_at, consumed_day, redeem_run_id FROM receipts '
                                        'WHERE nonce=?', (second.nonce,)).fetchone()
        self.assertEqual(row, (None, None, None))
        self.h.fake.utc = '2026-10-14T00:00:00Z'
        third, o3 = self.again(second, run='f')
        self.assertEqual(self.h.ledger.status(second.nonce), 'DENIED')
        self.assertEqual(self.h.ledger.status(third.nonce), 'ISSUED')
        self.assertTrue(self.consume(third, o3))

    def test_same_reviewed_object_is_rejected_across_observations(self):
        first, _ = self.issue('1')
        call = replace(first.binding.call, packet_digest='2'*64, sequence=first.binding.call.sequence+1,
                       observation_digest='3'*64, redeem_run_id='e'*32, reviewer_launch_nonce='e'*32)
        b = p.ProviderReviewBinding(call)
        observed = p.ProviderObservedState(b.digest, call.source_sha, call.observation_digest, True)
        decision = p.decide_provider_review(b, issuer=self.issuer, observed=observed)
        self.assertEqual(b.object_key, first.binding.object_key)
        with self.assertRaises(l.LedgerError):
            self.h.ledger.issue_provider(decision, ttl_seconds=120)

    def test_effect_rows_and_live_issue_hold_the_key(self):
        for status in ('ISSUED', 'CONSUMED', 'EFFECT_DONE', 'EFFECT_UNKNOWN'):
            with self.subTest(status=status):
                self.h.ledger.close()
                self.path2.unlink()
                self.h.ledger = l.ReceiptLedger.create(self.path2, clock=self.h.clock.utc,
                                                       kind='PROVIDER_REVIEW', provider_layout=2)
                receipt, o = self.issue('1', ttl=1)
                if status != 'ISSUED':
                    self.assertTrue(self.consume(receipt, o))
                if status in ('EFFECT_DONE', 'EFFECT_UNKNOWN'):
                    self.assertTrue(self.h.ledger.finalize_provider(receipt, status))
                if status != 'ISSUED':
                    self.h.fake.utc = '2026-10-13T00:00:11Z'  # expiry never frees an effect row
                with self.assertRaises(l.LedgerError) as caught:
                    self.again(receipt)
                self.assertIs(caught.exception.code, l.LedgerCode.NOT_ALLOWED)
                self.assertEqual(self.h.ledger.status(receipt.nonce), status)
                self.h.fake.utc = '2026-10-13T00:00:10Z'

    def test_released_rows_do_not_count_toward_daily_cap(self):
        receipt, o = self.issue('1')
        self.deny(receipt, o)
        expired, o2 = self.issue('2', ttl=1)
        self.h.fake.utc = '2026-10-13T00:00:11Z'
        self.consume(expired, o2)
        fresh, o3 = self.issue('3')
        self.assertTrue(self.consume(fresh, o3))  # cap 1, still available
        fourth, o4 = self.issue('4')
        self.assertFalse(self.consume(fourth, o4))

    def test_layout_parameter_and_cross_open_fail_closed(self):
        nonce = self.h.ledger.creation_nonce
        with self.assertRaises(l.LedgerError) as caught:
            l.ReceiptLedger.open(self.path2, clock=self.h.clock.utc, kind='PROVIDER_REVIEW',
                                 creation_nonce=nonce)
        self.assertIs(caught.exception.code, l.LedgerCode.LEDGER_CORRUPT)
        with self.assertRaises(l.LedgerError) as caught:
            l.ReceiptLedger.open(self.path1, clock=self.h.clock.utc, kind='PROVIDER_REVIEW',
                                 creation_nonce=self.layout1.creation_nonce, provider_layout=2)
        self.assertIs(caught.exception.code, l.LedgerCode.LEDGER_CORRUPT)
        reopened = l.ReceiptLedger.open(self.path2, clock=self.h.clock.utc, kind='PROVIDER_REVIEW',
                                        creation_nonce=nonce, provider_layout=2)
        reopened.close()
        for kind, layout in (('PROVIDER_REVIEW', 3), ('PROVIDER_REVIEW', True), ('D1', 2),
                             ('NOTIFICATION', 2)):
            with self.subTest(kind=kind, layout=layout):
                with self.assertRaises(l.LedgerError) as caught:
                    l.ReceiptLedger.create(self.h.hostdir / 'bad.sqlite3', clock=self.h.clock.utc,
                                           kind=kind, provider_layout=layout)
                self.assertIs(caught.exception.code, l.LedgerCode.NOT_ALLOWED)
                self.assertFalse((self.h.hostdir / 'bad.sqlite3').exists())

    def test_schema_tampering_is_corrupt(self):
        nonce = self.h.ledger.creation_nonce
        self.h.ledger.close()
        pristine = self.path2.read_bytes()
        tampers = (
            'DROP INDEX receipts_live_object_key',
            'CREATE INDEX extra_status ON receipts(status)',
            'CREATE TRIGGER t AFTER INSERT ON receipts BEGIN SELECT 1; END',
            'CREATE VIEW v AS SELECT nonce FROM receipts',
            "UPDATE ledger_meta SET creation_nonce='" + '0' * 64 + "'",
            # The CHECK (layout_version = 2) would refuse these rows; bypass it
            # so the reader's own meta check is what must catch them.
            ('PRAGMA ignore_check_constraints=ON', 'UPDATE ledger_meta SET layout_version=1'),
            ('PRAGMA ignore_check_constraints=ON', 'UPDATE ledger_meta SET layout_version=3'),
        )
        for statement in tampers:
            with self.subTest(statement=statement):
                self.path2.write_bytes(pristine)
                os.chmod(self.path2, 0o600)
                db = sqlite3.connect(self.path2)
                for sql in (statement,) if type(statement) is str else statement:
                    db.execute(sql)
                db.commit()
                db.close()
                with self.assertRaises(l.LedgerError) as caught:
                    l.ReceiptLedger.open(self.path2, clock=self.h.clock.utc, kind='PROVIDER_REVIEW',
                                         creation_nonce=nonce, provider_layout=2)
                self.assertIs(caught.exception.code, l.LedgerCode.LEDGER_CORRUPT)
        self.path2.write_bytes(pristine)
        self.h.ledger = l.ReceiptLedger.open(self.path2, clock=self.h.clock.utc, kind='PROVIDER_REVIEW',
                                             creation_nonce=nonce, provider_layout=2)

    def test_two_handles_racing_leave_one_live_row(self):
        receipt, o = self.issue('1', ttl=1)
        self.h.fake.utc = '2026-10-13T00:00:11Z'
        nonce = self.h.ledger.creation_nonce
        results = []
        barrier = threading.Barrier(2)

        def attempt(run):
            # One connection per thread, as two host processes would have.
            ledger = l.ReceiptLedger.open(self.path2, clock=self.h.clock.utc, kind='PROVIDER_REVIEW',
                                          creation_nonce=nonce, provider_layout=2)
            try:
                barrier.wait()
                self.again(receipt, run=run, ledger=ledger)
                results.append('ISSUED')
            except l.LedgerError as error:
                results.append(error.code.value)
            finally:
                ledger.close()

        threads = [threading.Thread(target=attempt, args=(run,)) for run in ('e', 'f')]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(sorted(results), ['ISSUED', 'NOT_ALLOWED'])
        live = self.h.ledger._db.execute(
            "SELECT count(*) FROM receipts WHERE status IN ('ISSUED','CONSUMED','EFFECT_DONE','EFFECT_UNKNOWN')"
        ).fetchone()[0]
        self.assertEqual(live, 1)
