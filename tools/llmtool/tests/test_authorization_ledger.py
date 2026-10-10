"""A11b.3 D1 one-use receipt ledger: at-most-once fake effects, fail-closed store."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from llmtool import authorization_ledger as l
from llmtool import authorization_policy as p
from test_authorization_policy import REDEEM_RUN, allow, make_create, make_observed

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
        return (ledger or self.ledger).issue(allow(**changes), ttl_seconds=ttl)

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
        count = sqlite3.connect(self.path).execute('SELECT count(*) FROM receipts').fetchone()[0]
        self.assertEqual(count, 0)

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

    def test_sink_failure_is_effect_unknown_never_success(self):
        receipt = self.issue()
        result, sink = self.redeem(receipt, sink=FakeSink(error=RuntimeError('fake sink failed')))
        self.assertIs(result.code, l.RedeemCode.EFFECT_UNKNOWN)
        self.assertFalse(result.effect_done)
        self.assertEqual(len(sink.calls), 1)
        self.assertEqual(self.ledger.status(receipt.nonce), 'EFFECT_UNKNOWN')
        self.assertIs(self.redeem(receipt)[0].code, l.RedeemCode.REPLAY)

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
                result, sink = self.redeem(receipt)
                self.assertIs(result.code, l.RedeemCode.REPLAY)
                self.assertEqual(sink.calls, [])
                self.assertEqual(len(self.effects()), effects)

    def test_two_processes_racing_one_receipt_produce_one_effect(self):
        for attempt in range(3):
            with self.subTest(attempt=attempt):
                log = self.dir / 'effects.log'
                if log.exists():
                    log.write_text('')
                receipt = self.issue()
                children = [self.child(receipt, wait=True) for _ in range(2)]
                for child in children:
                    child.stdin.write('go\n')
                    child.stdin.flush()
                outputs = []
                for child in children:
                    out, err = child.communicate(timeout=60)
                    self.assertEqual(child.returncode, 0, err)
                    outputs.append(out.strip())
                self.assertEqual(sorted(outputs), ['FAKE_EFFECT_DONE', 'REPLAY'])
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

    def test_locked_store_times_out_closed_without_issuing(self):
        holder = sqlite3.connect(self.path, isolation_level=None)
        holder.execute('BEGIN EXCLUSIVE')
        try:
            with patch.object(l, 'BUSY_TIMEOUT_SECONDS', 0.1):
                self.assertLedgerError(l.LedgerCode.LEDGER_LOCKED, self.open)
            self.ledger._db.execute('PRAGMA busy_timeout = 100')
            self.assertLedgerError(l.LedgerCode.LEDGER_LOCKED, self.issue)
        finally:
            holder.execute('ROLLBACK')
            holder.close()
        count = sqlite3.connect(self.path).execute('SELECT count(*) FROM receipts').fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == '__main__':
    unittest.main()
