"""A11b.3 D1 one-use receipt ledger for synthetic fake effects only.

A receipt is ISSUED from an ALLOW PolicyDecision and redeemed at most once:
ISSUED -> CONSUMED -> EFFECT_DONE | EFFECT_UNKNOWN (or DENIED / EXPIRED). The
consume is a conditional UPDATE inside BEGIN IMMEDIATE and is committed with
synchronous=FULL before the sink runs, so a crash, restart, replay or a second
process can never repeat the effect. A crash in either window leaves the row
CONSUMED; recover() turns it into EFFECT_UNKNOWN, which needs a fresh decision
and is never reported as success. This is at-most-once, not exactly-once.

The store must be an existing private file: owned by this euid, mode 0600,
regular, single-link, opened without following symlinks, in a directory not
writable by group/other, with its device/inode rechecked before every
transaction. Corrupt, locked, missing or swapped stores and host clock
rollback below the durable high-water mark fail closed. fullfsync is
requested for macOS, but power-loss durability is NOT_VERIFIED. None of this
protects against malicious code running as the same user: the path checks,
SQLite's own reopen of the path and the auxiliary journal are only
race-free against other users, and ancestor directories are not checked.

A failed create() leaves a file that open() rejects (LEDGER_CORRUPT) and
create() refuses; the host must remove it deliberately. Anyone able to call
redeem() with a receipt's nonce can burn it (DENIED/EXPIRED) by presenting a
mismatch; that is deliberate fail-closed behavior and needs a fresh decision.
recover() is for restart only: run while another process is between consume
and finalization, it makes that redemption report EFFECT_UNKNOWN (never a
repeated effect or a false success).

At most one receipt ever exists per exact binding, in any status: issuing the
same decision again is NOT_ALLOWED. The redeem run id is part of the binding,
so a new run needs a new decide() call.

Host responsibilities / accepted D1 limits: redeem() does not re-check
policy or issuer revocation; it trusts the decision at issue time for at most
the receipt TTL (<= MAX_APPROVAL_SECONDS), so a host that revokes must stop
redeeming itself. The host chooses the store path and must place it outside
any repository root or worktree (where repository tooling could commit, clean
or replace it); D1 does not enforce that.
"""

import abc
import math
import os
import secrets
import sqlite3
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum

from .action_contract import MAX_APPROVAL_SECONDS, MAX_EPOCH, ContractValue, _hex, _typed
from .authorization_policy import Decision, EffectKind, ObservedState, PolicyDecision, ReceiptBinding
from .errors import LLMToolError

SCHEMA_VERSION = 1
BUSY_TIMEOUT_SECONDS = 2.0
_SCHEMA = (
    'CREATE TABLE clock (id INTEGER PRIMARY KEY CHECK (id = 1), high_water INTEGER NOT NULL)',
    'CREATE TABLE receipts (nonce TEXT PRIMARY KEY, binding TEXT NOT NULL UNIQUE, '
    'issued_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, status TEXT NOT NULL CHECK '
    "(status IN ('ISSUED', 'CONSUMED', 'EFFECT_DONE', 'EFFECT_UNKNOWN', 'DENIED', 'EXPIRED')), "
    'redeem_run_id TEXT)',
)
_AUX_SUFFIXES = ('-journal', '-wal', '-shm')


class LedgerCode(str, Enum):
    LEDGER_UNAVAILABLE = 'LEDGER_UNAVAILABLE'
    LEDGER_INSECURE = 'LEDGER_INSECURE'
    LEDGER_CORRUPT = 'LEDGER_CORRUPT'
    LEDGER_LOCKED = 'LEDGER_LOCKED'
    CLOCK_INVALID = 'CLOCK_INVALID'
    CLOCK_ROLLBACK = 'CLOCK_ROLLBACK'
    NOT_ALLOWED = 'NOT_ALLOWED'


class LedgerError(LLMToolError):
    """Fail-closed ledger failure; no receipt was issued or redeemed."""

    def __init__(self, code):
        super().__init__('authorization ledger unavailable: ' + code.value)
        self.code = code


class RedeemCode(str, Enum):
    FAKE_EFFECT_DONE = 'FAKE_EFFECT_DONE'
    EFFECT_UNKNOWN = 'EFFECT_UNKNOWN'
    RECEIPT_UNKNOWN = 'RECEIPT_UNKNOWN'
    RECEIPT_TAMPERED = 'RECEIPT_TAMPERED'
    BINDING_MISMATCH = 'BINDING_MISMATCH'
    RUN_MISMATCH = 'RUN_MISMATCH'
    SINK_MISMATCH = 'SINK_MISMATCH'
    EXPIRED = 'EXPIRED'
    REPLAY = 'REPLAY'


@dataclass(frozen=True)
class Receipt(ContractValue):
    """A handle only: authority lives in the ledger row, not in this object."""
    binding: ReceiptBinding
    issued_at: int
    expires_at: int
    nonce: str

    def __post_init__(self):
        _typed(self.binding, ReceiptBinding)
        _hex(self.nonce)
        for name in ('issued_at', 'expires_at'):
            if type(getattr(self, name)) is not int:
                raise LLMToolError('receipt invalid; details omitted')


@dataclass(frozen=True)
class RedeemResult:
    code: RedeemCode

    @property
    def effect_done(self):
        """True only for a recorded fake sink effect; never a real APPLIED."""
        return self.code is RedeemCode.FAKE_EFFECT_DONE


class EffectSink(abc.ABC):
    """Host-provided synthetic sink; D1 ships no implementation."""

    @property
    @abc.abstractmethod
    def effect_kind(self):
        """An EffectKind member."""

    @abc.abstractmethod
    def apply(self, binding):
        """Perform the fake effect for this exact binding."""


def _fail(code):
    raise LedgerError(code)


def _secure_directory(path):
    try:
        st = os.lstat(path)
    except OSError:
        _fail(LedgerCode.LEDGER_UNAVAILABLE)
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or
            stat.S_IMODE(st.st_mode) & 0o022):
        _fail(LedgerCode.LEDGER_INSECURE)


def _secure_file(st):
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or
            stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1):
        _fail(LedgerCode.LEDGER_INSECURE)


def _sqlite_failure(error):
    text = str(error).lower()
    if 'locked' in text or 'busy' in text:
        return LedgerCode.LEDGER_LOCKED
    if isinstance(error, sqlite3.OperationalError) and 'malformed' not in text:
        return LedgerCode.LEDGER_UNAVAILABLE
    return LedgerCode.LEDGER_CORRUPT


class ReceiptLedger:
    """Use create() once, then open(); there is no default path."""

    @classmethod
    def create(cls, path, *, clock):
        path = os.path.abspath(path)
        _secure_directory(os.path.dirname(path))
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW |
                         getattr(os, 'O_CLOEXEC', 0), 0o600)
        except OSError:
            _fail(LedgerCode.LEDGER_INSECURE)
        try:
            os.fchmod(fd, 0o600)
            db = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
            try:
                cls._configure(db)
                db.execute('BEGIN IMMEDIATE')
                for statement in _SCHEMA:
                    db.execute(statement)
                db.execute('INSERT INTO clock VALUES (1, 0)')
                db.execute('PRAGMA user_version = %d' % SCHEMA_VERSION)
                db.execute('COMMIT')
            finally:
                db.close()
            directory = os.open(os.path.dirname(path), os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except sqlite3.Error as error:
            _fail(_sqlite_failure(error))
        except OSError:
            _fail(LedgerCode.LEDGER_UNAVAILABLE)
        finally:
            os.close(fd)
        return cls.open(path, clock=clock)

    @classmethod
    def open(cls, path, *, clock):
        """clock is the host's time source; callers never pass request times."""
        if not callable(clock):
            _fail(LedgerCode.CLOCK_INVALID)
        return cls(os.path.abspath(path), clock)

    def __init__(self, path, clock):
        self._path, self._clock, self._db, self._fd = path, clock, None, None
        _secure_directory(os.path.dirname(path))
        try:
            self._fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0))
        except FileNotFoundError:
            _fail(LedgerCode.LEDGER_UNAVAILABLE)
        except OSError:
            _fail(LedgerCode.LEDGER_INSECURE)
        try:
            try:
                st = os.fstat(self._fd)
                _secure_file(st)
                self._identity = (st.st_dev, st.st_ino)
                self._check_identity()
                self._db = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS,
                                           isolation_level=None)
                self._check_identity()
                self._configure(self._db)
                self._verify_schema()
            except sqlite3.Error as error:
                _fail(_sqlite_failure(error))
            except OSError:
                _fail(LedgerCode.LEDGER_UNAVAILABLE)
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _configure(db):
        try:
            # Rollback journal (no persistent WAL/SHM) and full syncs per commit.
            # fullfsync asks macOS for F_FULLFSYNC; ignored elsewhere.
            for pragma in ('journal_mode = DELETE', 'synchronous = FULL', 'fullfsync = ON',
                           'checkpoint_fullfsync = ON', 'trusted_schema = OFF',
                           'cell_size_check = ON'):
                db.execute('PRAGMA ' + pragma).fetchall()
            if (db.execute('PRAGMA journal_mode').fetchone()[0] != 'delete' or
                    db.execute('PRAGMA synchronous').fetchone()[0] != 2):
                _fail(LedgerCode.LEDGER_UNAVAILABLE)
        except sqlite3.Error as error:
            _fail(_sqlite_failure(error))

    def _verify_schema(self):
        try:
            if self._db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                _fail(LedgerCode.LEDGER_CORRUPT)
            tables = self._db.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall()
            others = self._db.execute(
                "SELECT count(*) FROM sqlite_master WHERE type NOT IN ('table', 'index') "
                "OR (type = 'index' AND sql IS NOT NULL)").fetchone()[0]
            version = self._db.execute('PRAGMA user_version').fetchone()[0]
            # A schema-less (e.g. half-created) store is corrupt, not unavailable.
            if tables != [(s,) for s in _SCHEMA] or others or version != SCHEMA_VERSION:
                _fail(LedgerCode.LEDGER_CORRUPT)
            rows = self._db.execute('SELECT id FROM clock').fetchall()
        except sqlite3.Error as error:
            _fail(_sqlite_failure(error))
        if rows != [(1,)]:
            _fail(LedgerCode.LEDGER_CORRUPT)

    def _check_identity(self):
        try:
            st = os.lstat(self._path)
        except OSError:
            _fail(LedgerCode.LEDGER_INSECURE)
        _secure_file(st)
        if (st.st_dev, st.st_ino) != self._identity:
            _fail(LedgerCode.LEDGER_INSECURE)
        for suffix in _AUX_SUFFIXES:
            try:
                aux = os.lstat(self._path + suffix)
            except FileNotFoundError:
                continue
            except OSError:
                _fail(LedgerCode.LEDGER_INSECURE)
            if (not stat.S_ISREG(aux.st_mode) or aux.st_uid != os.geteuid() or
                    stat.S_IMODE(aux.st_mode) & 0o077):
                _fail(LedgerCode.LEDGER_INSECURE)

    def close(self):
        db, fd, self._db, self._fd = self._db, self._fd, None, None
        try:
            if db is not None:
                db.close()
        finally:
            if fd is not None:
                os.close(fd)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @contextmanager
    def _transaction(self):
        """BEGIN IMMEDIATE; yields the host time after the durable clock check."""
        if self._db is None:
            _fail(LedgerCode.LEDGER_UNAVAILABLE)
        self._check_identity()
        try:
            now = self._clock()
        except Exception:
            _fail(LedgerCode.CLOCK_INVALID)
        if type(now) is float and math.isfinite(now) and now >= 0:
            now = int(now)
        if type(now) is not int or not 0 <= now <= MAX_EPOCH:
            _fail(LedgerCode.CLOCK_INVALID)
        try:
            # Inside the try so an interrupt right after BEGIN still rolls back.
            self._db.execute('BEGIN IMMEDIATE')
            high_water = self._db.execute('SELECT high_water FROM clock WHERE id = 1').fetchone()
            if high_water is None or type(high_water[0]) is not int:
                _fail(LedgerCode.LEDGER_CORRUPT)
            if now < high_water[0]:
                _fail(LedgerCode.CLOCK_ROLLBACK)
            self._db.execute('UPDATE clock SET high_water = ? WHERE id = 1', (now,))
            yield now
            self._db.execute('COMMIT')
        except BaseException as error:
            try:
                self._db.execute('ROLLBACK')
            except sqlite3.Error:
                pass
            if isinstance(error, sqlite3.Error):
                _fail(_sqlite_failure(error))
            raise

    def issue(self, decision, *, ttl_seconds):
        """Record one receipt for an ALLOW decision made by decide()."""
        if (type(decision) is not PolicyDecision or
                decision.decision is not Decision.ALLOW_WITH_TRUSTED_RECEIPT):
            _fail(LedgerCode.NOT_ALLOWED)
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= MAX_APPROVAL_SECONDS:
            _fail(LedgerCode.NOT_ALLOWED)
        nonce = secrets.token_hex(32)
        with self._transaction() as now:
            if now + ttl_seconds > MAX_EPOCH:
                _fail(LedgerCode.CLOCK_INVALID)
            try:
                self._db.execute('INSERT INTO receipts VALUES (?, ?, ?, ?, ?, NULL)',
                                 (nonce, decision.binding.to_json(), now, now + ttl_seconds,
                                  'ISSUED'))
            except sqlite3.IntegrityError:
                # This exact binding already has a receipt (any status).
                _fail(LedgerCode.NOT_ALLOWED)
        return Receipt(decision.binding, now, now + ttl_seconds, nonce)

    def _set(self, nonce, status, expected):
        cursor = self._db.execute('UPDATE receipts SET status = ? WHERE nonce = ? AND status = ?',
                                  (status, nonce, expected))
        return cursor.rowcount == 1

    def redeem(self, receipt, *, redeem_run_id, observed, sink):
        """Consume once, then run the fake sink; never repeats an effect."""
        if type(receipt) is not Receipt:
            return RedeemResult(RedeemCode.RECEIPT_UNKNOWN)
        binding = receipt.binding
        try:
            effect_kind = sink.effect_kind if isinstance(sink, EffectSink) else None
        except Exception:
            effect_kind = None
        with self._transaction() as now:
            row = self._db.execute('SELECT binding, issued_at, expires_at, status FROM receipts '
                                   'WHERE nonce = ?', (receipt.nonce,)).fetchone()
            if row is None:
                return RedeemResult(RedeemCode.RECEIPT_UNKNOWN)
            stored, issued_at, expires_at, status = row
            if status != 'ISSUED':
                return RedeemResult(RedeemCode.REPLAY)
            denial = None
            if (stored != binding.to_json() or issued_at != receipt.issued_at or
                    expires_at != receipt.expires_at):
                denial = RedeemCode.RECEIPT_TAMPERED
            elif type(redeem_run_id) is not str or redeem_run_id != binding.redeem_run_id:
                denial = RedeemCode.RUN_MISMATCH
            elif effect_kind is not EffectKind.FAKE_FILE_CREATE or effect_kind.value != binding.effect_kind:
                denial = RedeemCode.SINK_MISMATCH
            elif type(observed) is not ObservedState or not _observed_matches(observed, binding):
                denial = RedeemCode.BINDING_MISMATCH
            if denial is not None:
                self._set(receipt.nonce, 'DENIED', 'ISSUED')
                return RedeemResult(denial)
            if not issued_at <= now < expires_at:
                self._set(receipt.nonce, 'EXPIRED', 'ISSUED')
                return RedeemResult(RedeemCode.EXPIRED)
            consumed = self._db.execute(
                "UPDATE receipts SET status = 'CONSUMED', redeem_run_id = ? "
                "WHERE nonce = ? AND status = 'ISSUED'", (redeem_run_id, receipt.nonce))
            if consumed.rowcount != 1:
                return RedeemResult(RedeemCode.REPLAY)
        # Committed CONSUMED. Crash window 1 (before the effect) and window 2
        # (after it, before finalization) both leave CONSUMED -> EFFECT_UNKNOWN.
        try:
            sink.apply(binding)
        except BaseException as error:
            self._finish(receipt.nonce, 'EFFECT_UNKNOWN')
            if not isinstance(error, Exception):
                raise
            return RedeemResult(RedeemCode.EFFECT_UNKNOWN)
        if not self._finish(receipt.nonce, 'EFFECT_DONE'):
            return RedeemResult(RedeemCode.EFFECT_UNKNOWN)
        return RedeemResult(RedeemCode.FAKE_EFFECT_DONE)

    def _finish(self, nonce, status):
        try:
            with self._transaction():
                return self._set(nonce, status, 'CONSUMED')
        except LedgerError:
            return False

    def recover(self):
        """After a restart: every CONSUMED receipt becomes EFFECT_UNKNOWN."""
        with self._transaction():
            return self._db.execute("UPDATE receipts SET status = 'EFFECT_UNKNOWN' "
                                    "WHERE status = 'CONSUMED'").rowcount

    def status(self, nonce):
        _hex(nonce)
        with self._transaction():
            row = self._db.execute('SELECT status FROM receipts WHERE nonce = ?',
                                   (nonce,)).fetchone()
        return None if row is None else row[0]


def _observed_matches(observed, binding):
    repository = observed.repository
    return (repository.repository_id == binding.repository_id and
            repository.canonical_root == binding.canonical_root and
            repository.root_device == binding.root_device and
            repository.root_inode == binding.root_inode and
            repository.expected_head == binding.head and observed.head == binding.head and
            observed.index_sha256 == binding.index_sha256 and
            observed.worktree_sha256 == binding.worktree_sha256 and
            observed.target_path == binding.target_path and observed.target_absent is True and
            observed.parent_device == binding.parent_device and
            observed.parent_inode == binding.parent_inode)
