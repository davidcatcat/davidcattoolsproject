"""Private local ledger for observed skill-manifest delivery.

The hash chain detects edited, removed, or reordered lines. Same-user tampering
can be detected only up to rewriting the whole chain.
"""

import fcntl
import hashlib
import json
import os
import secrets
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .errors import LLMToolError

SCHEMA_VERSION = 1
EVIDENCE_LEVELS = frozenset((
    'CONTROLLER_DELIVERED', 'HOOK_DELIVERED', 'OBSERVED_READ', 'UNVERIFIED'))
REASONS = frozenset((
    'EXPIRED', 'REVOKED', 'WRONG_TASK', 'WRONG_RUN', 'WRONG_AGENT',
    'WRONG_ROLE', 'WRONG_PROVIDER', 'WRONG_MODEL', 'WRONG_REPO',
    'WRONG_HEAD', 'MANIFEST_CHANGED', 'NOT_FOUND',
    'CHAIN_BROKEN', 'LEDGER_UNREADABLE'))
_BINDING_FIELDS = ('task_id', 'run_id', 'session_id', 'agent_id', 'role',
                   'provider', 'model', 'repo', 'head', 'manifest_sha256')
_GENESIS = '0' * 64
_MAX_BYTES = 16 * 1024 * 1024


class SkillLedgerError(LLMToolError):
    """Failure with a stable, non-sensitive ledger code."""

    def __init__(self, code):
        super().__init__('skill evidence ledger operation failed')
        self.skill_ledger_code = code


def _fail(code):
    raise SkillLedgerError(code)


def _path():
    return Path.home() / '.local' / 'state' / 'llmtool' / 'skill-ledger.jsonl'


def _stamp(value):
    if isinstance(value, datetime):
        dt = value
    elif type(value) is str:
        try:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def _now_text():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _entry_hash(entry):
    value = {k: v for k, v in entry.items() if k != 'entry_sha256'}
    return hashlib.sha256(_canonical(value)).hexdigest()


def _ensure_parent(path):
    if os.name != 'posix' or not hasattr(os, 'O_NOFOLLOW'):
        _fail('LEDGER_UNREADABLE')
    parent = path.parent
    try:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = parent.lstat()
        if (stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or
                info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
            _fail('LEDGER_UNREADABLE')
    except SkillLedgerError:
        raise
    except OSError:
        _fail('LEDGER_UNREADABLE')


def _locked(path):
    _ensure_parent(path)
    lock_path = str(path) + '.lock'
    try:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            os.close(fd)
            _fail('LEDGER_UNREADABLE')
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd
    except SkillLedgerError:
        raise
    except OSError:
        _fail('LEDGER_UNREADABLE')


def _read(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return []
    except OSError:
        _fail('LEDGER_UNREADABLE')
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or
                info.st_size > _MAX_BYTES):
            _fail('LEDGER_UNREADABLE')
        with os.fdopen(fd, 'rb') as stream:
            fd = None
            raw = stream.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES or (raw and not raw.endswith(b'\n')):
            _fail('LEDGER_UNREADABLE')
        if not raw:
            return []
        records = [json.loads(line.decode('utf-8')) for line in raw.splitlines()]
        return records
    except SkillLedgerError:
        raise
    except Exception:
        _fail('LEDGER_UNREADABLE')
    finally:
        if fd is not None:
            os.close(fd)


def _chain(records):
    try:
        previous = _GENESIS
        for entry in records:
            if (type(entry) is not dict or entry.get('schema_version') != SCHEMA_VERSION or
                    entry.get('prev_sha256') != previous or
                    type(entry.get('entry_sha256')) is not str or
                    entry['entry_sha256'] != _entry_hash(entry)):
                return False
            previous = entry['entry_sha256']
        return True
    except (TypeError, ValueError, UnicodeError, OverflowError):
        return False


def _anchor_path(path):
    return Path(str(path) + '.head')


def _anchor_matches(path, records):
    """Check the independently stored end marker against the ledger."""
    anchor = _anchor_path(path)
    try:
        fd = os.open(anchor, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        try:
            os.lstat(path)
        except FileNotFoundError:
            return not records
        except OSError:
            return False
        return False
    except OSError:
        return False
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or
                info.st_size > 4096):
            return False
        with os.fdopen(fd, 'rb') as stream:
            fd = None
            value = json.loads(stream.read(4097).decode('utf-8'))
        expected = {'count': len(records),
                    'last_sha256': records[-1]['entry_sha256'] if records else _GENESIS}
        return (type(value) is dict and set(value) == {'count', 'last_sha256'} and
                type(value.get('count')) is int and type(value.get('last_sha256')) is str and
                value == expected)
    except (OSError, ValueError, TypeError, UnicodeError, OverflowError):
        return False
    finally:
        if fd is not None:
            os.close(fd)


def _write_anchor(path, records):
    anchor = _anchor_path(path)
    temp = Path(str(anchor) + '.' + secrets.token_hex(8) + '.tmp')
    payload = _canonical({'count': len(records),
                          'last_sha256': records[-1]['entry_sha256'] if records else _GENESIS})
    fd = None
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        view = memoryview(payload)
        while view:
            count = os.write(fd, view)
            view = view[count:]
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(temp, anchor)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        _fail('LEDGER_UNREADABLE')
    finally:
        if fd is not None:
            os.close(fd)
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def verify_chain():
    """Return (valid, code); whole-chain rewriting by the same user is undetectable."""
    path = _path()
    lock_fd = _locked(path)
    try:
        try:
            records = _read(path)
        except SkillLedgerError:
            return False, 'LEDGER_UNREADABLE'
        return ((True, None) if _chain(records) and _anchor_matches(path, records)
                else (False, 'CHAIN_BROKEN'))
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _append(path, records, entry):
    entry['prev_sha256'] = records[-1]['entry_sha256'] if records else _GENESIS
    entry['entry_sha256'] = _entry_hash(entry)
    payload = _canonical(entry) + b'\n'
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            _fail('LEDGER_UNREADABLE')
        view = memoryview(payload)
        while view:
            count = os.write(fd, view)
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    records = [*records, entry]
    _write_anchor(path, records)
    return entry['receipt_id']


def _append_locked(path, lock_fd, records, entry):
    try:
        return _append(path, records, entry)
    except SkillLedgerError:
        raise
    except (OSError, TypeError, ValueError, UnicodeError, OverflowError):
        _fail('LEDGER_UNREADABLE')


def record(entry, *, ttl_seconds=86400):
    """Append an observed delivery record; evidence level must be explicit."""
    if type(entry) is not dict or type(ttl_seconds) is not int or ttl_seconds <= 0:
        _fail('INVALID_ENTRY')
    if entry.get('evidence_level') not in EVIDENCE_LEVELS:
        _fail('INVALID_EVIDENCE_LEVEL')
    if any(type(entry.get(key)) is not str or not entry[key] for key in _BINDING_FIELDS):
        _fail('INVALID_ENTRY')
    try:
        created = datetime.now(timezone.utc)
        value = {key: entry[key] for key in _BINDING_FIELDS}
        value.update({
            'schema_version': SCHEMA_VERSION,
            'receipt_id': secrets.token_hex(16),
            'evidence_level': entry['evidence_level'],
            'status': 'ACTIVE',
            'created_at': created.isoformat(timespec='seconds').replace('+00:00', 'Z'),
            'expires_at': (created + timedelta(seconds=ttl_seconds)).isoformat(
                timespec='seconds').replace('+00:00', 'Z'),
        })
    except (TypeError, ValueError, UnicodeError, OverflowError):
        _fail('INVALID_ENTRY')
    path = _path()
    lock_fd = _locked(path)
    try:
        records = _read(path)
        if not _chain(records) or not _anchor_matches(path, records):
            _fail('CHAIN_BROKEN')
        return _append_locked(path, lock_fd, records, value)
    except (TypeError, ValueError, UnicodeError, OverflowError):
        _fail('INVALID_ENTRY')
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _revoked(records, receipt_id):
    return any(item.get('entry_type') == 'REVOCATION' and
               item.get('revokes_receipt_id') == receipt_id for item in records)


def check(task_id, run_id, session_id, agent_id, role, provider, model, repo,
          head, manifest_sha256, now=None):
    """Check exact bindings and expiry; callers must supply trusted time."""
    try:
        path = _path()
        lock_fd = _locked(path)
    except SkillLedgerError:
        return {'status': 'UNVERIFIED', 'reason': 'LEDGER_UNREADABLE'}
    try:
        try:
            records = _read(path)
        except SkillLedgerError:
            return {'status': 'UNVERIFIED', 'reason': 'LEDGER_UNREADABLE'}
        if not _chain(records) or not _anchor_matches(path, records):
            return {'status': 'UNVERIFIED', 'reason': 'CHAIN_BROKEN'}
        by_id = [item for item in records if item.get('receipt_id') and
                 item.get('entry_type') != 'REVOCATION']
        supplied = (task_id, run_id, session_id, agent_id, role, provider,
                    model, repo, head, manifest_sha256)
        fields = _BINDING_FIELDS
        valid_supplied = all(type(value) is str and value for value in supplied)
        for item in reversed(by_id):
            if valid_supplied and all(item.get(k) == v for k, v in zip(fields, supplied)):
                if _revoked(records, item['receipt_id']):
                    return {'status': 'UNVERIFIED', 'reason': 'REVOKED'}
                current = _stamp(now) if now is not None else datetime.now(timezone.utc)
                expiry = _stamp(item.get('expires_at'))
                if current is None or expiry is None:
                    return {'status': 'UNVERIFIED', 'reason': 'LEDGER_UNREADABLE'}
                if current >= expiry:
                    return {'status': 'UNVERIFIED', 'reason': 'EXPIRED'}
                if item.get('evidence_level') == 'UNVERIFIED':
                    return {'status': 'UNVERIFIED', 'reason': 'NOT_FOUND'}
                return {'status': 'VERIFIED', 'reason': None,
                        'receipt_id': item['receipt_id'],
                        'evidence_level': item['evidence_level']}
        # Classify the receipt matching the greatest number of supplied fields.
        if by_id:
            item = max(by_id, key=lambda row: sum(
                type(value) is str and bool(value) and row.get(key) == value
                for key, value in zip(fields, supplied)))
            for reason, key, value in (
                    ('WRONG_TASK', 'task_id', task_id), ('WRONG_RUN', 'run_id', run_id),
                    ('WRONG_AGENT', 'agent_id', agent_id), ('WRONG_ROLE', 'role', role),
                    ('WRONG_PROVIDER', 'provider', provider),
                    ('WRONG_MODEL', 'model', model), ('WRONG_REPO', 'repo', repo),
                    ('WRONG_HEAD', 'head', head),
                    ('MANIFEST_CHANGED', 'manifest_sha256', manifest_sha256)):
                if item.get(key) != value:
                    return {'status': 'UNVERIFIED', 'reason': reason}
        return {'status': 'UNVERIFIED', 'reason': 'NOT_FOUND'}
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def revoke(receipt_id, reason):
    """Append a revocation event for an existing receipt."""
    if type(receipt_id) is not str or not receipt_id or type(reason) is not str or not reason:
        _fail('INVALID_ENTRY')
    path = _path()
    lock_fd = _locked(path)
    try:
        records = _read(path)
        if not _chain(records):
            _fail('CHAIN_BROKEN')
        target = next((item for item in records if item.get('receipt_id') == receipt_id
                       and item.get('entry_type') != 'REVOCATION'), None)
        if target is None:
            _fail('NOT_FOUND')
        if _revoked(records, receipt_id):
            _fail('REVOKED')
        event = dict(target)
        event.update(entry_type='REVOCATION', revokes_receipt_id=receipt_id,
                     revocation_reason=reason, status='REVOKED',
                     receipt_id=secrets.token_hex(16), created_at=_now_text())
        if not _anchor_matches(path, records):
            _fail('CHAIN_BROKEN')
        return _append_locked(path, lock_fd, records, event)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
