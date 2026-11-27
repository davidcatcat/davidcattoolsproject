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
writable by group/other. The file's device/inode and the directory's type,
owner, mode and device/inode are rechecked before and after the write lock is
taken for every transaction, and the host clock is read only once the lock is
held. Corrupt, locked, missing or swapped stores and host clock
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

from .action_contract import MAX_APPROVAL_SECONDS, MAX_EPOCH, ContractValue, _hex
from .authorization_policy import (Decision, EffectKind, ObservedState, PolicyDecision, ReceiptBinding,
                                   ProviderReviewBinding,ProviderPolicyDecision,decide_provider_review,
                                   NotificationBinding,NotificationDecision,decide_notification)
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
_PROVIDER_SCHEMA = (
    _SCHEMA[0],
    "CREATE TABLE ledger_meta (kind TEXT NOT NULL CHECK (kind = 'PROVIDER_REVIEW'), creation_nonce TEXT NOT NULL)",
    'CREATE TABLE receipts (nonce TEXT PRIMARY KEY, binding TEXT NOT NULL UNIQUE, '
    'issued_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, status TEXT NOT NULL CHECK '
    "(status IN ('ISSUED', 'CONSUMED', 'EFFECT_DONE', 'EFFECT_UNKNOWN', 'DENIED', 'EXPIRED')), "
    'redeem_run_id TEXT, consumed_at INTEGER NULL, consumed_day INTEGER NULL, '
    'route_digest TEXT NOT NULL, object_key TEXT NOT NULL UNIQUE, '
    "CHECK ((status IN ('CONSUMED','EFFECT_DONE','EFFECT_UNKNOWN') AND "
    "typeof(consumed_at) = 'integer' AND consumed_at >= 0 AND "
    "typeof(consumed_day) = 'integer' AND consumed_day = consumed_at / 86400) OR "
    "(status IN ('ISSUED','DENIED','EXPIRED') AND consumed_at IS NULL AND consumed_day IS NULL)))",
)

# Provider layout 2 (#192-A): explicit separate new-file layout. Layout 1 is
# never migrated. object_key is unique only among rows that hold it (ISSUED or
# an effect status); DENIED and EXPIRED rows stay as evidence but release it.
_PROVIDER_SCHEMA_V2 = (
    _SCHEMA[0],
    "CREATE TABLE ledger_meta (kind TEXT NOT NULL CHECK (kind = 'PROVIDER_REVIEW'), "
    "layout_version INTEGER NOT NULL CHECK (layout_version = 2), creation_nonce TEXT NOT NULL)",
    _PROVIDER_SCHEMA[2].replace('object_key TEXT NOT NULL UNIQUE', 'object_key TEXT NOT NULL'),
)
_PROVIDER_V2_INDEXES = (
    'CREATE UNIQUE INDEX receipts_live_object_key ON receipts(object_key) '
    "WHERE status IN ('ISSUED','CONSUMED','EFFECT_DONE','EFFECT_UNKNOWN')",
)


NOTIFICATION_SCHEMA_VERSION = 1
_NOTIFICATION_SCHEMA = (
    _SCHEMA[0],
    "CREATE TABLE ledger_meta (kind TEXT NOT NULL CHECK (kind = 'NOTIFICATION'), "
    "layout_version INTEGER NOT NULL CHECK (layout_version = 1), creation_nonce TEXT NOT NULL)",
    'CREATE TABLE receipts (nonce TEXT PRIMARY KEY, binding TEXT NOT NULL UNIQUE, '
    'issued_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, status TEXT NOT NULL CHECK '
    "(status IN ('ISSUED','CONSUMED','EFFECT_DONE','EFFECT_UNKNOWN','ZERO_EFFECT','WITHDRAWN','CANCELLED','DENIED','EXPIRED')), "
    'redeem_run_id TEXT, object_key TEXT NOT NULL, session_key TEXT NOT NULL, grant_digest TEXT NOT NULL, '
    'dispatch_started INTEGER NOT NULL CHECK (dispatch_started IN (0,1)), '
    'message_id TEXT, turn_id TEXT, pending INTEGER NOT NULL CHECK (pending IN (0,1)), '
    "review_status TEXT CHECK (review_status IN ('APPROVE','CHANGES_REQUIRED','BLOCKED')), verdict_digest TEXT)",
)


# Explicit separate new-file layout. Legacy v1 is never migrated.
_NOTIFICATION_SCHEMA_V2 = (
    _SCHEMA[0],
    _NOTIFICATION_SCHEMA[1].replace('layout_version = 1','layout_version = 2'),
    _NOTIFICATION_SCHEMA[2][:-1] + ", author_receipt_status TEXT NOT NULL DEFAULT 'NOT_VERIFIED' "
    "CHECK (author_receipt_status IN ('NOT_VERIFIED','VERIFIED')), author_receipt_digest TEXT, "
    "terminal_status TEXT CHECK (terminal_status IN ('Completed','Interrupted','Failed')), coverage_digest TEXT)",
)


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
        if type(self.binding) not in (ReceiptBinding,ProviderReviewBinding,NotificationBinding):
            raise LLMToolError('receipt invalid; details omitted')
        _hex(self.nonce)
        for name in ('issued_at', 'expires_at'):
            if type(getattr(self, name)) is not int:
                raise LLMToolError('receipt invalid; details omitted')

    def to_dict(self):
        values=super().to_dict()
        if type(self.binding) is ProviderReviewBinding:
            values['binding']=self.binding.to_dict()
        return values


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
    return st.st_dev, st.st_ino


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


def _check_provider_layout(kind, provider_layout):
    if (type(provider_layout) is not int or provider_layout not in (1, 2) or
            (kind != 'PROVIDER_REVIEW' and provider_layout != 1)):
        _fail(LedgerCode.NOT_ALLOWED)


def _layout_schema(kind, notification_layout, provider_layout):
    """(tables in name order, explicit indexes) for exactly one layout."""
    if kind == 'D1':
        return _SCHEMA, ()
    if kind == 'PROVIDER_REVIEW':
        return (_PROVIDER_SCHEMA, ()) if provider_layout == 1 else (_PROVIDER_SCHEMA_V2, _PROVIDER_V2_INDEXES)
    return (_NOTIFICATION_SCHEMA if notification_layout == 1 else _NOTIFICATION_SCHEMA_V2), ()


class ReceiptLedger:
    """Use create() once, then open(); there is no default path."""

    @classmethod
    def create(cls, path, *, clock, kind='D1', notification_layout=1, provider_layout=1):
        if kind not in ('D1','PROVIDER_REVIEW','NOTIFICATION'): _fail(LedgerCode.NOT_ALLOWED)
        if type(notification_layout) is not int or notification_layout not in (1,2) or (kind != 'NOTIFICATION' and notification_layout != 1): _fail(LedgerCode.NOT_ALLOWED)
        _check_provider_layout(kind, provider_layout)
        creation_nonce = secrets.token_hex(32) if kind != 'D1' else None
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
                tables, indexes = _layout_schema(kind, notification_layout, provider_layout)
                for statement in tables + indexes:
                    db.execute(statement)
                if creation_nonce is not None:
                    if kind == 'NOTIFICATION':
                        db.execute('INSERT INTO ledger_meta VALUES (?, ?, ?)',(kind,notification_layout,creation_nonce))
                    elif provider_layout == 2:
                        db.execute('INSERT INTO ledger_meta VALUES (?, ?, ?)',(kind,provider_layout,creation_nonce))
                    else:
                        db.execute('INSERT INTO ledger_meta VALUES (?, ?)',(kind,creation_nonce))
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
        return cls.open(path,clock=clock,kind=kind,creation_nonce=creation_nonce,
                        notification_layout=notification_layout,provider_layout=provider_layout)

    @classmethod
    def open(cls, path, *, clock, kind='D1', creation_nonce=None, notification_layout=1, provider_layout=1):
        """clock is the host's time source; callers never pass request times."""
        if not callable(clock):
            _fail(LedgerCode.CLOCK_INVALID)
        if kind not in ('D1','PROVIDER_REVIEW','NOTIFICATION'): _fail(LedgerCode.NOT_ALLOWED)
        if kind != 'D1':
            try: _hex(creation_nonce)
            except LLMToolError: _fail(LedgerCode.LEDGER_CORRUPT)
        if type(notification_layout) is not int or notification_layout not in (1,2) or (kind != "NOTIFICATION" and notification_layout != 1): _fail(LedgerCode.NOT_ALLOWED)
        _check_provider_layout(kind, provider_layout)
        return cls(os.path.abspath(path),clock,kind,creation_nonce,notification_layout,provider_layout)

    def __init__(self, path, clock, kind='D1', creation_nonce=None, notification_layout=1, provider_layout=1):
        self.kind,self.creation_nonce = kind,creation_nonce
        self.notification_layout = notification_layout
        self.provider_layout = provider_layout
        self._path, self._clock, self._db, self._fd = path, clock, None, None
        self._directory = _secure_directory(os.path.dirname(path))
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
                "SELECT count(*) FROM sqlite_master WHERE type NOT IN ('table', 'index')").fetchone()[0]
            explicit = self._db.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL "
                "ORDER BY name").fetchall()
            version = self._db.execute('PRAGMA user_version').fetchone()[0]
            # A schema-less (e.g. half-created) store is corrupt, not unavailable.
            schema, indexes = _layout_schema(self.kind, self.notification_layout, self.provider_layout)
            if (tables != [(s,) for s in schema] or others or version != SCHEMA_VERSION or
                    explicit != [(s,) for s in indexes]):
                _fail(LedgerCode.LEDGER_CORRUPT)
            if self.kind == 'PROVIDER_REVIEW' and self.provider_layout == 1 and self._db.execute(
                    'SELECT kind,creation_nonce FROM ledger_meta').fetchall() != [(self.kind,self.creation_nonce)]:
                _fail(LedgerCode.LEDGER_CORRUPT)
            if self.kind == 'PROVIDER_REVIEW' and self.provider_layout == 2 and self._db.execute(
                    'SELECT kind,layout_version,creation_nonce FROM ledger_meta').fetchall() != [(self.kind,2,self.creation_nonce)]:
                _fail(LedgerCode.LEDGER_CORRUPT)
            if self.kind == 'NOTIFICATION' and self._db.execute(
                    'SELECT kind,layout_version,creation_nonce FROM ledger_meta').fetchall() != [(self.kind,self.notification_layout,self.creation_nonce)]:
                _fail(LedgerCode.LEDGER_CORRUPT)
            rows = self._db.execute('SELECT id FROM clock').fetchall()
        except sqlite3.Error as error:
            _fail(_sqlite_failure(error))
        if rows != [(1,)]:
            _fail(LedgerCode.LEDGER_CORRUPT)

    def _check_identity(self):
        # The containing directory is rechecked too: an open ledger whose
        # directory became shared, a symlink or a different directory is refused.
        if _secure_directory(os.path.dirname(self._path)) != self._directory:
            _fail(LedgerCode.LEDGER_INSECURE)
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
        """BEGIN IMMEDIATE; yields the host time after the durable clock check.

        Store admission and the host clock are both checked only after the
        write lock is held: BEGIN can wait up to the busy timeout, and a time
        sampled before that wait could authorize an already expired receipt.
        """
        if self._db is None:
            _fail(LedgerCode.LEDGER_UNAVAILABLE)
        self._check_identity()
        try:
            # Inside the try so an interrupt right after BEGIN still rolls back.
            self._db.execute('BEGIN IMMEDIATE')
            self._check_identity()
            if self.kind != 'D1':
                self._verify_schema()
            now = self._now()
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

    def _now(self):
        try:
            now = self._clock()
        except Exception:
            _fail(LedgerCode.CLOCK_INVALID)
        if type(now) is float and math.isfinite(now) and now >= 0:
            now = int(now)
        if type(now) is not int or not 0 <= now <= MAX_EPOCH:
            _fail(LedgerCode.CLOCK_INVALID)
        return now

    def issue(self, decision, *, ttl_seconds):
        """Record one receipt for an ALLOW decision made by decide()."""
        if (self.kind != 'D1' or type(decision) is not PolicyDecision or
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
        if self.kind != 'D1' or type(receipt) is not Receipt or type(receipt.binding) is not ReceiptBinding:
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

    def issue_provider(self, decision, *, ttl_seconds):
        """Provider-only issuance, no launch.

        Layout 1: one object forever per route. Layout 2: an object is held by
        an unexpired ISSUED row or any effect row; DENIED/EXPIRED rows release it.
        """
        if (self.kind != 'PROVIDER_REVIEW' or type(decision) is not ProviderPolicyDecision or
                decision.decision is not Decision.ALLOW_WITH_TRUSTED_RECEIPT or
                type(ttl_seconds) is not int or not 1 <= ttl_seconds <= MAX_APPROVAL_SECONDS):
            _fail(LedgerCode.NOT_ALLOWED)
        b=decision.binding
        object_key = b.object_key if self.provider_layout == 2 else b.legacy_object_key
        from .task_lifecycle import utc_time
        deadline=int(utc_time(b.call.deadline).timestamp())
        nonce=secrets.token_hex(32)
        with self._transaction() as now:
            expires=min(now+ttl_seconds,deadline)
            if not now < expires <= MAX_EPOCH: _fail(LedgerCode.NOT_ALLOWED)
            if self.provider_layout == 2:
                # Zero effect: an ISSUED row past expires_at was never consumed.
                self._db.execute("UPDATE receipts SET status='EXPIRED' WHERE object_key=? "
                                 "AND status='ISSUED' AND expires_at <= ?",(object_key,now))
            try:
                self._db.execute('INSERT INTO receipts VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)',
                    (nonce,b.to_json(),now,expires,'ISSUED',b.call.route_digest,object_key))
            except sqlite3.IntegrityError: _fail(LedgerCode.NOT_ALLOWED)
        return Receipt(b,now,expires,nonce)

    def consume_provider(self, receipt, *, issuer, observed):
        """Atomic CURRENT-policy recheck/cap count/consume; effect runs later."""
        if self.kind != 'PROVIDER_REVIEW' or type(receipt) is not Receipt or type(receipt.binding) is not ProviderReviewBinding:
            _fail(LedgerCode.NOT_ALLOWED)
        b=receipt.binding
        object_key = b.object_key if self.provider_layout == 2 else b.legacy_object_key
        with self._transaction() as now:
            row=self._db.execute('SELECT binding,issued_at,expires_at,status,route_digest,object_key '
                                 'FROM receipts WHERE nonce=?',(receipt.nonce,)).fetchone()
            if row is None or row[3] != 'ISSUED': _fail(LedgerCode.NOT_ALLOWED)
            if row != (b.to_json(),receipt.issued_at,receipt.expires_at,'ISSUED',b.call.route_digest,object_key):
                self._set(receipt.nonce,'DENIED','ISSUED'); return False
            if not row[1] <= now < row[2]:
                self._set(receipt.nonce,'EXPIRED','ISSUED'); return False
            decision=decide_provider_review(b,issuer=issuer,observed=observed)
            if decision.decision is not Decision.ALLOW_WITH_TRUSTED_RECEIPT:
                self._set(receipt.nonce,'DENIED','ISSUED'); return False
            try: cap=issuer.daily_cap(b.call.route_digest)
            except Exception: _fail(LedgerCode.NOT_ALLOWED)
            if type(cap) is not int or not 1 <= cap <= 1000: _fail(LedgerCode.NOT_ALLOWED)
            day=now//86400
            count=self._db.execute("SELECT count(*) FROM receipts WHERE route_digest=? AND consumed_day=? "
                "AND status IN ('CONSUMED','EFFECT_DONE','EFFECT_UNKNOWN')",(b.call.route_digest,day)).fetchone()[0]
            if count >= cap:
                # Layout 2 releases the reviewed-object key for a refused,
                # zero-effect attempt in this transaction. Preserve layout 1
                # semantics exactly: its unique object key remains held.
                if self.provider_layout == 2:
                    self._set(receipt.nonce,'DENIED','ISSUED')
                return False
            updated=self._db.execute("UPDATE receipts SET status='CONSUMED',redeem_run_id=?,consumed_at=?,consumed_day=? "
                "WHERE nonce=? AND status='ISSUED'",(b.call.redeem_run_id,now,day,receipt.nonce))
            return updated.rowcount == 1

    def finalize_provider(self, receipt, status):
        if (self.kind != 'PROVIDER_REVIEW' or type(receipt) is not Receipt or
                type(receipt.binding) is not ProviderReviewBinding or status not in ('EFFECT_DONE','EFFECT_UNKNOWN')):
            _fail(LedgerCode.NOT_ALLOWED)
        with self._transaction():
            row=self._db.execute('SELECT binding FROM receipts WHERE nonce=?',(receipt.nonce,)).fetchone()
            if row != (receipt.binding.to_json(),): _fail(LedgerCode.NOT_ALLOWED)
            # Charge time/day are never updated by finalize/recover.
            return self._set(receipt.nonce,status,'CONSUMED')


    def _notification_row(self, receipt):
        if (self.kind != 'NOTIFICATION' or type(receipt) is not Receipt or
                type(receipt.binding) is not NotificationBinding or
                receipt.binding.ledger_nonce != self.creation_nonce):
            _fail(LedgerCode.NOT_ALLOWED)
        row = self._db.execute('SELECT binding,issued_at,expires_at,status,dispatch_started,message_id,turn_id '
                               'FROM receipts WHERE nonce=?',(receipt.nonce,)).fetchone()
        if row is None or row[:3] != (receipt.binding.to_json(),receipt.issued_at,receipt.expires_at):
            _fail(LedgerCode.NOT_ALLOWED)
        return row

    def notification_record(self, object_key):
        if self.kind != 'NOTIFICATION': _fail(LedgerCode.NOT_ALLOWED)
        _hex(object_key)
        with self._transaction():
            row = self._db.execute('SELECT nonce,status,review_status,verdict_digest,pending,grant_digest '
                'FROM receipts WHERE object_key=? ORDER BY rowid DESC LIMIT 1',(object_key,)).fetchone()
            return None if row is None else dict(zip(
                ('nonce','status','review_status','verdict_digest','pending','grant_digest'),row))

    def notification_pending(self, session):
        if self.kind != 'NOTIFICATION': _fail(LedgerCode.NOT_ALLOWED)
        with self._transaction():
            return self._db.execute('SELECT count(*) FROM receipts WHERE session_key=? AND pending=1',
                                    (session,)).fetchone()[0] != 0

    def issue_notification(self, decision, *, ttl_seconds):
        if (self.kind != 'NOTIFICATION' or type(decision) is not NotificationDecision or
                decision.decision is not Decision.ALLOW_WITH_TRUSTED_RECEIPT or
                type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 600):
            _fail(LedgerCode.NOT_ALLOWED)
        b = decision.binding
        if type(b) is not NotificationBinding or b.ledger_nonce != self.creation_nonce:
            _fail(LedgerCode.NOT_ALLOWED)
        nonce = secrets.token_hex(32)
        with self._transaction() as now:
            if not b.not_before <= now < b.expires_at: _fail(LedgerCode.NOT_ALLOWED)
            if self._db.execute('SELECT count(*) FROM receipts WHERE session_key=? AND pending=1',
                                (b.reviewer_session,)).fetchone()[0]: _fail(LedgerCode.NOT_ALLOWED)
            previous = self._db.execute('SELECT nonce,status,review_status,grant_digest FROM receipts '
                'WHERE object_key=? ORDER BY rowid DESC LIMIT 1',(b.object_key,)).fetchone()
            if self.notification_layout == 2 and previous is not None: _fail(LedgerCode.NOT_ALLOWED)
            if previous is None:
                if b.rearm_from: _fail(LedgerCode.NOT_ALLOWED)
            elif (b.rearm_from != previous[0] or b.grant_digest == previous[3] or
                  not (previous[1] in ('ZERO_EFFECT','WITHDRAWN','CANCELLED') or
                       (previous[1] == 'EFFECT_DONE' and previous[2] == 'BLOCKED'))):
                _fail(LedgerCode.NOT_ALLOWED)
            expires = min(now+ttl_seconds,b.expires_at)
            self._db.execute('INSERT INTO receipts (nonce,binding,issued_at,expires_at,status,redeem_run_id,object_key,session_key,grant_digest,dispatch_started,message_id,turn_id,pending,review_status,verdict_digest) VALUES (?,?,?,?,?,NULL,?,?,?,0,NULL,NULL,0,NULL,NULL)',
                (nonce,b.to_json(),now,expires,'ISSUED',b.object_key,b.reviewer_session,b.grant_digest))
        return Receipt(b,now,expires,nonce)

    def consume_notification(self, receipt, *, issuer, observed):
        with self._transaction() as now:
            row = self._notification_row(receipt)
            if row[3] != 'ISSUED': return False
            if not receipt.issued_at <= now < receipt.expires_at:
                self._set(receipt.nonce,'EXPIRED','ISSUED'); return False
            decision = decide_notification(receipt.binding,issuer=issuer,observed=observed)
            if decision.decision is not Decision.ALLOW_WITH_TRUSTED_RECEIPT:
                self._set(receipt.nonce,'DENIED','ISSUED'); return False
            if self._db.execute('SELECT count(*) FROM receipts WHERE session_key=? AND pending=1',
                                (receipt.binding.reviewer_session,)).fetchone()[0]: return False
            return self._db.execute("UPDATE receipts SET status='CONSUMED',pending=1,redeem_run_id=? "
                "WHERE nonce=? AND status='ISSUED'",(receipt.binding.task.run_id,receipt.nonce)).rowcount == 1

    def begin_notification(self, receipt):
        # Commit ambiguous dispatch intent BEFORE entering the external callback.
        with self._transaction() as now:
            row = self._notification_row(receipt)
            if row[3] != 'CONSUMED' or row[4] or now >= receipt.expires_at: return False
            return self._db.execute('UPDATE receipts SET dispatch_started=1 WHERE nonce=? AND dispatch_started=0',
                                    (receipt.nonce,)).rowcount == 1

    def record_notification_ids(self, receipt, message_id, turn_id=None):
        from .authorization_policy import notification_id
        notification_id(message_id)
        if turn_id is not None: notification_id(turn_id)
        with self._transaction():
            row = self._notification_row(receipt)
            if row[3] != 'CONSUMED' or row[4] != 1 or row[5] not in (None,message_id) or row[6] not in (None,turn_id):
                _fail(LedgerCode.NOT_ALLOWED)
            self._db.execute('UPDATE receipts SET message_id=?,turn_id=? WHERE nonce=?',
                             (message_id,turn_id,receipt.nonce))

    def finalize_notification(self, receipt, status, *, message_id=None, turn_id=None, no_turn=False,
                              review_status=None, verdict_digest=None):
        # Trusted host-only completion API. Constructor data is not attestation;
        # the transport composition must first correlate original host events.
        with self._transaction():
            row = self._notification_row(receipt)
            if row[3] != 'CONSUMED': return False
            if status == 'ZERO_EFFECT':
                if row[4] or row[5] is not None: _fail(LedgerCode.NOT_ALLOWED)
                pending = 0
            elif status == 'WITHDRAWN':
                if (row[5] is None or row[5] != message_id or row[6] is not None or no_turn is not True):
                    _fail(LedgerCode.NOT_ALLOWED)
                pending = 0
            elif status == 'CANCELLED':
                if row[5] != message_id or row[6] is None or row[6] != turn_id: _fail(LedgerCode.NOT_ALLOWED)
                pending = 0
            elif status == 'EFFECT_DONE':
                if row[5] is None or row[6] is None or review_status not in ('APPROVE','CHANGES_REQUIRED','BLOCKED'):
                    _fail(LedgerCode.NOT_ALLOWED)
                _hex(verdict_digest); pending = 0
            elif status == 'EFFECT_UNKNOWN':
                pending = 1
            else: _fail(LedgerCode.NOT_ALLOWED)
            self._db.execute('UPDATE receipts SET status=?,pending=?,review_status=?,verdict_digest=? WHERE nonce=?',
                             (status,pending,review_status,verdict_digest,receipt.nonce))
            return True


    def finish_notification_turn(self, receipt, *, terminal_status, coverage_digest, child_exited,
                                 review_status=None, verdict_digest=None):
        """Host-only forward completion; author receipt is separately verified.

        The adapter must establish original terminal/coverage/owned exit first.
        Constructor fields do not authenticate those facts. No UNKNOWN recovery.
        """
        if self.kind != 'NOTIFICATION' or self.notification_layout != 2: _fail(LedgerCode.NOT_ALLOWED)
        _hex(coverage_digest)
        if terminal_status not in ('Completed','Interrupted','Failed') or child_exited is not True: _fail(LedgerCode.NOT_ALLOWED)
        if terminal_status == 'Completed':
            if review_status not in ('APPROVE','CHANGES_REQUIRED','BLOCKED'): _fail(LedgerCode.NOT_ALLOWED)
            _hex(verdict_digest)
        elif review_status is not None or verdict_digest is not None: _fail(LedgerCode.NOT_ALLOWED)
        with self._transaction():
            row=self._notification_row(receipt)
            if row[3] != 'CONSUMED' or row[4] != 1 or row[5] is None or row[6] is None: return False
            self._db.execute("UPDATE receipts SET status='EFFECT_DONE',pending=0,review_status=?,verdict_digest=?,terminal_status=?,coverage_digest=? WHERE nonce=?",
                             (review_status,verdict_digest,terminal_status,coverage_digest,receipt.nonce))
            return True

    def record_author_receipt(self, receipt, *, verdict_digest, receipt_digest):
        if self.kind != 'NOTIFICATION' or self.notification_layout != 2: _fail(LedgerCode.NOT_ALLOWED)
        _hex(verdict_digest); _hex(receipt_digest)
        with self._transaction():
            self._notification_row(receipt)
            return self._db.execute("UPDATE receipts SET author_receipt_status='VERIFIED',author_receipt_digest=? WHERE nonce=? AND status='EFFECT_DONE' AND terminal_status='Completed' AND verdict_digest=? AND author_receipt_status='NOT_VERIFIED'",
                                    (receipt_digest,receipt.nonce,verdict_digest)).rowcount == 1

    def notification_completion(self, receipt):
        if self.kind != 'NOTIFICATION' or self.notification_layout != 2: _fail(LedgerCode.NOT_ALLOWED)
        with self._transaction():
            self._notification_row(receipt)
            row=self._db.execute('SELECT status,pending,review_status,verdict_digest,terminal_status,coverage_digest,author_receipt_status,author_receipt_digest FROM receipts WHERE nonce=?',(receipt.nonce,)).fetchone()
            return dict(zip(('status','pending','review_status','verdict_digest','terminal_status','coverage_digest','author_receipt_status','author_receipt_digest'),row))

    def invalidate_notification_completion(self, receipt):
        """Same-attempt late contradiction only; never reconciles UNKNOWN."""
        if self.kind != 'NOTIFICATION' or self.notification_layout != 2: _fail(LedgerCode.NOT_ALLOWED)
        with self._transaction():
            self._notification_row(receipt)
            return self._db.execute("UPDATE receipts SET status='EFFECT_UNKNOWN',pending=1 WHERE nonce=? AND status='EFFECT_DONE'",(receipt.nonce,)).rowcount == 1


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
