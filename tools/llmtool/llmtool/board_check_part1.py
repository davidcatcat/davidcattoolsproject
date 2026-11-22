"""Ledger and row-claim helpers for ``llmtool board-check`` (#763)."""

import contextlib
import fcntl
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
import stat

from . import task_status, worker_dispatch
from .task_store import TaskStore


def _bc():
    name = f'{__package__}.board_check' if __package__ else 'board_check'
    mod = sys.modules.get(name)
    if mod is None:
        from . import board_check as mod
    return mod


def default_ledger_path():
    return Path.home() / '.local' / 'state' / 'llmtool' / 'board-ledger.json'


def _seconds(dt):
    bc = _bc()
    if isinstance(dt, datetime):
        return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    if type(dt) is not str or bc._UTC_SECONDS.fullmatch(dt) is None:
        return None
    try:
        return datetime.fromisoformat(dt.replace('Z', '+00:00'))
    except ValueError:
        return None


def _valid_claim(claim):
    bc = _bc()
    if type(claim) is not dict:
        return False
    if set(claim) != bc._CLAIM_FIELDS:
        return False
    holder = claim.get('holder')
    if type(holder) is not str or bc._HOLDER.fullmatch(holder) is None:
        return False
    row = claim.get('row')
    if type(row) is not str or not (1 <= len(row) <= 512):
        return False
    claimed_at = claim.get('claimed_at')
    if type(claimed_at) is not str or bc._UTC_SECONDS.fullmatch(claimed_at) is None or bc._seconds(claimed_at) is None:
        return False
    task_id = claim.get('task_id')
    bound_at = claim.get('bound_at')
    if task_id is None:
        if bound_at is not None:
            return False
    else:
        if type(task_id) is not str or bc._TASK_ID.fullmatch(task_id) is None:
            return False
        if type(bound_at) is not str or bc._UTC_SECONDS.fullmatch(bound_at) is None or bc._seconds(bound_at) is None:
            return False
    return True


class BoardLedger:
    """Cooperative process-level claim ledger for pm-board reconciliation."""

    def __init__(self, path=None):
        bc = _bc()
        self.path = Path(path) if path is not None else bc.default_ledger_path()
        self.lock_path = self.path.with_name(self.path.name + '.lock')

    def read(self):
        bc = _bc()
        try:
            st = os.lstat(self.path)
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise bc.LedgerUnreadable('ledger unreadable') from exc

        if stat.S_ISLNK(st.st_mode):
            raise bc.LedgerUnreadable('ledger is a symlink')
        if not stat.S_ISREG(st.st_mode):
            raise bc.LedgerUnreadable('ledger is not a regular file')
        if stat.S_IMODE(st.st_mode) != 0o600:
            raise bc.LedgerUnreadable('ledger permissions must be 0600')
        if st.st_size > bc.MAX_LEDGER_BYTES:
            raise bc.LedgerUnreadable('ledger exceeds size limit')

        try:
            flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
            fd = os.open(self.path, flags)
            try:
                info = os.fstat(fd)
                if stat.S_ISLNK(info.st_mode):
                    raise bc.LedgerUnreadable('ledger is a symlink')
                if not stat.S_ISREG(info.st_mode):
                    raise bc.LedgerUnreadable('ledger is not a regular file')
                if info.st_uid != os.getuid() or info.st_nlink != 1:
                    raise bc.LedgerUnreadable('ledger ownership or link count invalid')
                if stat.S_IMODE(info.st_mode) != 0o600:
                    raise bc.LedgerUnreadable('ledger permissions must be 0600')
                if info.st_size > bc.MAX_LEDGER_BYTES:
                    raise bc.LedgerUnreadable('ledger exceeds size limit')
                chunks, remaining = [], bc.MAX_LEDGER_BYTES + 1
                while remaining:
                    chunk = os.read(fd, min(65536, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
            finally:
                os.close(fd)
            raw = b''.join(chunks)
            if len(raw) > bc.MAX_LEDGER_BYTES:
                raise bc.LedgerUnreadable('ledger exceeds size limit')
            value = json.loads(raw.decode('utf-8'),
                               object_pairs_hook=worker_dispatch._unique_pairs,
                               parse_constant=bc._reject_constant)
        except bc.LedgerUnreadable:
            raise
        except FileNotFoundError:
            return []
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise bc.LedgerUnreadable('ledger unreadable') from exc

        if (type(value) is not dict or type(value.get('schema_version')) is not int or
                value['schema_version'] != bc.LEDGER_SCHEMA_VERSION or
                type(value.get('claims')) is not list):
            raise bc.LedgerUnreadable('ledger is not schema_version 1 with a claims list')

        claims = value['claims']
        if len(claims) > bc.MAX_LEDGER_CLAIMS:
            raise bc.LedgerUnreadable('ledger claims exceed limit')
        for claim in claims:
            if not bc._valid_claim(claim):
                raise bc.LedgerUnreadable('invalid claim in ledger')
        return claims

    @contextlib.contextmanager
    def locked(self):
        bc = _bc()
        parent = self.path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            st = os.lstat(parent)
            if stat.S_ISLNK(st.st_mode) or st.st_uid != os.getuid() or (st.st_mode & 0o022):
                raise bc.ClaimRefused('LEDGER_UNAVAILABLE')
        except bc.ClaimRefused:
            raise
        except OSError:
            raise bc.ClaimRefused('LEDGER_UNAVAILABLE')

        timeout = bc.LEDGER_LOCK_TIMEOUT_SECONDS
        deadline = time.time() + timeout
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            fd = os.open(self.lock_path, flags, 0o600)
        except OSError:
            raise bc.ClaimRefused('LEDGER_UNAVAILABLE')
        try:
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
                    raise bc.ClaimRefused('LEDGER_UNAVAILABLE')
            except bc.ClaimRefused:
                raise
            except OSError:
                raise bc.ClaimRefused('LEDGER_UNAVAILABLE')
            try:
                os.chmod(self.lock_path, 0o600)
            except OSError:
                pass
            acquired = False
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except (BlockingIOError, OSError):
                    if time.time() >= deadline:
                        break
                    time.sleep(0.02)
            if not acquired:
                raise bc.ClaimRefused('LEDGER_BUSY')
            try:
                yield self
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            os.close(fd)

    def write(self, claims):
        bc = _bc()
        if len(claims) > bc.MAX_LEDGER_CLAIMS:
            raise bc.ClaimRefused('LEDGER_FULL')
        for claim in claims:
            if not bc._valid_claim(claim):
                raise bc.ClaimRefused('LEDGER_CLAIM_INVALID')

        parent = self.path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            st = os.lstat(parent)
            if stat.S_ISLNK(st.st_mode) or st.st_uid != os.getuid() or (st.st_mode & 0o022):
                raise bc.ClaimRefused('LEDGER_UNAVAILABLE')
        except bc.ClaimRefused:
            raise
        except OSError:
            raise bc.ClaimRefused('LEDGER_UNAVAILABLE')

        payload = {
            'schema_version': bc.LEDGER_SCHEMA_VERSION,
            'claims': list(claims),
        }
        data = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True).encode('utf-8')
        if len(data) > bc.MAX_LEDGER_BYTES:
            raise bc.ClaimRefused('LEDGER_FULL')

        tmp_path = self.path.with_name(f'{self.path.name}.tmp.{uuid.uuid4().hex}')
        try:
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, 'O_NOFOLLOW', 0)
            )
            fd = os.open(tmp_path, flags, 0o600)
            try:
                os.fchmod(fd, 0o600)
                offset = 0
                total = len(data)
                while offset < total:
                    written = os.write(fd, data[offset:])
                    if written <= 0:
                        raise bc.ClaimRefused('LEDGER_WRITE_FAILED')
                    offset += written
                os.fsync(fd)
            finally:
                os.close(fd)

            os.replace(tmp_path, self.path)

            dir_flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0)
            dir_fd = os.open(parent, dir_flags)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except bc.ClaimRefused:
            raise
        except OSError as exc:
            raise bc.ClaimRefused('LEDGER_WRITE_FAILED') from exc
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def claim_live(claim, store, now=None):
    """Return whether a claim is currently live.

    A reservation (no task_id) is live while now - claimed_at < RESERVATION_TTL.
    A bound claim (with task_id) is live only if store.get(task_id) returns a
    record whose derived state is not terminal.
    """
    bc = _bc()
    if not isinstance(claim, dict):
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    elif isinstance(now, datetime) and now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    task_id = claim.get('task_id')
    if task_id is None:
        claimed_at = claim.get('claimed_at')
        if isinstance(claimed_at, datetime):
            dt_claimed = claimed_at
        elif isinstance(claimed_at, str):
            dt_claimed = bc._seconds(claimed_at)
        else:
            return False
        if dt_claimed is None:
            return False
        if dt_claimed.tzinfo is None:
            dt_claimed = dt_claimed.replace(tzinfo=timezone.utc)
        return (now - dt_claimed) < bc.RESERVATION_TTL

    if not isinstance(task_id, str) or bc._TASK_ID.fullmatch(task_id) is None:
        return False
    if store is None:
        return False
    try:
        record = store.get(task_id)
    except Exception:
        return False
    if not isinstance(record, dict):
        return False
    try:
        state = task_status.derive_state(record)
    except Exception:
        state = record.get('state')
    if state in bc._TERMINAL_TASK_STATES or record.get('state') in bc._TERMINAL_TASK_STATES:
        return False
    return state is not None


def claim_row(ledger, rows, row, holder, task_id=None, store=None, now=None):
    """Claim or bind a pm-board.json row under the ledger lock.

    Without task_id, reserves the row for holder if no live claim exists.
    With task_id, binds that Task to the holder's live reservation on the row.
    """
    bc = _bc()
    if now is None:
        now = datetime.now(timezone.utc)
    elif isinstance(now, datetime) and now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    if store is None:
        store = TaskStore()

    if isinstance(rows, dict):
        row_ids = set(rows.keys())
    elif isinstance(rows, (list, tuple)):
        row_ids = {r.get('id') for r in rows if isinstance(r, dict) and 'id' in r}
    else:
        row_ids = set()

    with ledger.locked():
        claims = ledger.read()

        if row not in row_ids:
            raise bc.ClaimRefused('CLAIM_ROW_UNKNOWN')

        if task_id is None:
            for c in claims:
                if c.get('row') == row and bc.claim_live(c, store, now):
                    raise bc.ClaimRefused('CLAIM_HELD')

            new_claim = {
                'holder': holder,
                'row': row,
                'claimed_at': bc._seconds(now),
                'task_id': None,
                'bound_at': None,
            }
            claims.append(new_claim)
            ledger.write(claims)
            return new_claim

        if not isinstance(task_id, str) or bc._TASK_ID.fullmatch(task_id) is None:
            raise bc.ClaimRefused('CLAIM_TASK_UNKNOWN')

        try:
            record = store.get(task_id)
        except Exception:
            raise bc.ClaimRefused('CLAIM_TASK_UNKNOWN') from None

        if not isinstance(record, dict):
            raise bc.ClaimRefused('CLAIM_TASK_UNKNOWN')

        try:
            state = task_status.derive_state(record)
        except Exception:
            state = record.get('state')

        if (state in bc._TERMINAL_TASK_STATES or
                record.get('state') in bc._TERMINAL_TASK_STATES or
                state is None):
            raise bc.ClaimRefused('CLAIM_TASK_UNKNOWN')

        for c in claims:
            if c.get('task_id') == task_id and bc.claim_live(c, store, now):
                raise bc.ClaimRefused('CLAIM_TASK_BOUND')

        matching_reservation = None
        for c in claims:
            if (c.get('row') == row and
                    c.get('holder') == holder and
                    c.get('task_id') is None and
                    bc.claim_live(c, store, now)):
                matching_reservation = c
                break

        if matching_reservation is None:
            raise bc.ClaimRefused('CLAIM_NOT_RESERVED')

        matching_reservation['task_id'] = task_id
        matching_reservation['bound_at'] = bc._seconds(now)
        ledger.write(claims)
        return matching_reservation
