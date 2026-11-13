"""Private local ledger for observed skill-manifest delivery.

The hash chain detects edited, removed, or reordered lines. Same-user tampering
can be detected only up to rewriting the whole chain: removing entries and
rewriting the separate .head anchor is undetected, and deleting both files
reads as an empty ledger. A pending append intent plus one removed committed
line reads as not appended and goes undetected. check(), record(), and revoke()
may truncate the ledger while recovering an interrupted append; check() may
also write .head. record() can raise after its line persists,
so callers must treat that result as outcome unknown. The ledger is limited to
16 MiB and uses an untimed exclusive flock.

Role-card delivery (#1502 WP-F design 3.7): record_card_delivery() appends a
CONTROLLER_DELIVERED receipt that also binds route, base commit and per-card
sha256; check() verifies it like any other receipt. record_not_delivered()
appends a NOT_DELIVERED event with a fixed reason code; it is never verified
evidence, check() ignores it and task_summary() reads it as UNVERIFIED.
"""

import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .errors import LLMToolError

SCHEMA_VERSION = 1
EVIDENCE_LEVELS = frozenset((
    'CONTROLLER_DELIVERED', 'HOOK_DELIVERED', 'OBSERVED_READ', 'UNVERIFIED'))
_BINDING_FIELDS = ('task_id', 'run_id', 'session_id', 'agent_id', 'role',
                   'provider', 'model', 'repo', 'head', 'manifest_sha256')
_GENESIS = '0' * 64
_MAX_BYTES = 16 * 1024 * 1024

CONTROLLER_DELIVERED = 'CONTROLLER_DELIVERED'
NOT_DELIVERED = 'NOT_DELIVERED'
# Fixed reason codes for a route run that did not receive its role cards.
NOT_DELIVERED_REASONS = frozenset((
    'ROUTE_NOT_SUPPORTED',  # the route has no rule-delivery field yet (gw-agent)
    'ROUTE_UNKNOWN',        # no role-card table entry for the route
    'CARD_READ_FAILED',     # a card could not be read from the base commit
    'PREFIX_TOO_LARGE',     # the Developer prefix exceeds its size budget
))
_MAX_CARDS = 16
_MAX_CARD_PATH = 512
_ROUTE_RE = re.compile(r'[a-z0-9][a-z0-9-]{0,63}')
_COMMIT_RE = re.compile(r'[0-9a-fA-F]{40}')
_SHA256_RE = re.compile(r'[0-9a-f]{64}')


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
    try:
        return dt.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None


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
        # Remove only stale temporary anchors, while holding the ledger lock.
        for temp in path.parent.glob(path.name + '.head.*.tmp'):
            try:
                temp.unlink()
            except OSError:
                # Stale cleanup is opportunistic: a directory, permission
                # change, or racing removal must not leak the acquired lock.
                pass
        return fd
    except SkillLedgerError:
        raise
    except OSError:
        _fail('LEDGER_UNREADABLE')


def _read(path, *, recover_torn=False):
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
        if len(raw) > _MAX_BYTES:
            _fail('LEDGER_UNREADABLE')
        if raw and not raw.endswith(b'\n'):
            if not recover_torn:
                _fail('LEDGER_UNREADABLE')
            complete_bytes = raw.rfind(b'\n') + 1
            complete = raw[:complete_bytes]
            try:
                records = [json.loads(line.decode('utf-8'))
                           for line in complete.splitlines()]
                anchor = _read_anchor(path)
                intent = anchor.get('intent') if type(anchor) is dict else None
                actual_hash = records[-1]['entry_sha256'] if records else _GENESIS
                if (not _chain(records) or type(intent) is not dict or
                        anchor.get('count') != len(records) or
                        anchor.get('last_sha256') != actual_hash or
                        set(intent) != {'count', 'last_sha256'} or
                        type(intent.get('count')) is not int or
                        type(intent.get('last_sha256')) is not str):
                    _fail('LEDGER_UNREADABLE')
                repair_fd = os.open(path, os.O_WRONLY | os.O_NOFOLLOW)
                try:
                    repair_info = os.fstat(repair_fd)
                    if (not stat.S_ISREG(repair_info.st_mode) or
                            repair_info.st_dev != info.st_dev or
                            repair_info.st_ino != info.st_ino or
                            repair_info.st_uid != os.getuid() or
                            stat.S_IMODE(repair_info.st_mode) != 0o600 or
                            repair_info.st_nlink != 1):
                        _fail('LEDGER_UNREADABLE')
                    os.ftruncate(repair_fd, complete_bytes)
                    os.fsync(repair_fd)
                finally:
                    os.close(repair_fd)
            except SkillLedgerError:
                raise
            except Exception:
                _fail('LEDGER_UNREADABLE')
            raw = complete
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


def _anchor_value(records, intent=None):
    value = {'count': len(records),
             'last_sha256': records[-1]['entry_sha256'] if records else _GENESIS}
    if intent is not None:
        value['intent'] = intent
    return value


def _read_anchor(path):
    anchor = _anchor_path(path)
    try:
        fd = os.open(anchor, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        try:
            os.lstat(path)
        except FileNotFoundError:
            return None if not os.path.lexists(path) else False
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
        if type(value) is not dict:
            return False
        if set(value) not in ({'count', 'last_sha256'}, {'count', 'last_sha256', 'intent'}):
            return False
        if type(value.get('count')) is not int or type(value.get('last_sha256')) is not str:
            return False
        return value
    except (OSError, ValueError, TypeError, UnicodeError, OverflowError):
        return False
    finally:
        if fd is not None:
            os.close(fd)


def _anchor_matches(path, records):
    value = _read_anchor(path)
    return value is None and not records or value == _anchor_value(records)


def _recover_anchor(path, records):
    """Complete or clear a pending anchor intent after a ledger interruption."""
    value = _read_anchor(path)
    if value is None:
        return not records
    if value is False:
        return False
    intent = value.get('intent')
    if intent is None:
        return _anchor_matches(path, records)
    if (type(intent) is not dict or set(intent) != {'count', 'last_sha256'} or
            type(intent.get('count')) is not int or
            type(intent.get('last_sha256')) is not str):
        return False
    base_count = value['count']
    base_hash = value['last_sha256']
    actual_hash = records[-1]['entry_sha256'] if records else _GENESIS
    if base_count == len(records) and base_hash == actual_hash:
        # Intent was durable but its append was not.
        _write_anchor(path, records)
        return True
    # A complete, valid line matching the pending intent was committed.
    if (base_count + 1 == len(records) and
            intent['count'] == len(records) and
            base_hash == (records[-2]['entry_sha256'] if len(records) > 1 else _GENESIS) and
            intent['last_sha256'] == actual_hash):
        _write_anchor(path, records)
        return True
    return False


def _write_anchor(path, records, intent=None):
    anchor = _anchor_path(path)
    temp = Path(str(anchor) + '.' + secrets.token_hex(8) + '.tmp')
    payload = _canonical(_anchor_value(records, intent))
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
    """Return (valid, code).

    Same-user tampering is detectable only up to rewriting the whole chain:
    removing entries and rewriting .head is undetected, and deleting both
    files reads empty. A torn final line is refused as LEDGER_UNREADABLE and
    is never repaired by verification.
    """
    path = _path()
    try:
        lock_fd = _locked(path)
    except SkillLedgerError:
        return False, 'LEDGER_UNREADABLE'
    try:
        try:
            records = _read(path)
        except SkillLedgerError:
            return False, 'LEDGER_UNREADABLE'
        if not _chain(records):
            return False, 'CHAIN_BROKEN'
        try:
            recovered = _recover_anchor(path, records)
        except SkillLedgerError:
            return False, 'LEDGER_UNREADABLE'
        return (True, None) if recovered else (False, 'CHAIN_BROKEN')
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _append(path, records, entry):
    entry['prev_sha256'] = records[-1]['entry_sha256'] if records else _GENESIS
    entry['entry_sha256'] = _entry_hash(entry)
    payload = _canonical(entry) + b'\n'
    intent = {'count': len(records) + 1, 'last_sha256': entry['entry_sha256']}
    _write_anchor(path, records, intent)
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


def _append_locked(path, records, entry):
    try:
        return _append(path, records, entry)
    except SkillLedgerError:
        raise
    except (OSError, TypeError, ValueError, UnicodeError, OverflowError):
        _fail('LEDGER_UNREADABLE')


def _binding_value(entry, ttl_seconds):
    """Validate a receipt entry and return its binding fields and level."""
    if type(entry) is not dict or type(ttl_seconds) is not int or ttl_seconds <= 0:
        _fail('INVALID_ENTRY')
    evidence_level = entry.get('evidence_level')
    if type(evidence_level) is not str or evidence_level not in EVIDENCE_LEVELS:
        _fail('INVALID_EVIDENCE_LEVEL')
    if any(type(entry.get(key)) is not str or not entry[key] for key in _BINDING_FIELDS):
        _fail('INVALID_ENTRY')
    value = {key: entry[key] for key in _BINDING_FIELDS}
    value['evidence_level'] = evidence_level
    return value


def _stamped(value, ttl_seconds):
    """Add receipt metadata and prove the value encodes canonically."""
    try:
        created = datetime.now(timezone.utc)
        value.update({
            'schema_version': SCHEMA_VERSION,
            'receipt_id': secrets.token_hex(16),
            'status': 'ACTIVE',
            'created_at': created.isoformat(timespec='seconds').replace('+00:00', 'Z'),
            'expires_at': (created + timedelta(seconds=ttl_seconds)).isoformat(
                timespec='seconds').replace('+00:00', 'Z'),
        })
        # Reject strings that cannot be encoded as canonical UTF-8 before any
        # filesystem access, rather than misclassifying them as ledger damage.
        _canonical(value)
    except (TypeError, ValueError, UnicodeError, OverflowError):
        _fail('INVALID_ENTRY')
    return value


def _valid_route(route):
    return type(route) is str and _ROUTE_RE.fullmatch(route) is not None


def _card_digests(cards):
    """Return a copy of [{path, sha256}] card digests, or fail INVALID_ENTRY."""
    if type(cards) is not list or not 1 <= len(cards) <= _MAX_CARDS:
        _fail('INVALID_ENTRY')
    result = []
    seen = set()
    for card in cards:
        if type(card) is not dict or set(card) != {'path', 'sha256'}:
            _fail('INVALID_ENTRY')
        path = card['path']
        digest = card['sha256']
        if (type(path) is not str or not path or len(path) > _MAX_CARD_PATH or
                path in seen or type(digest) is not str or
                _SHA256_RE.fullmatch(digest) is None):
            _fail('INVALID_ENTRY')
        seen.add(path)
        result.append({'path': path, 'sha256': digest})
    return result


def record(entry, *, ttl_seconds=86400):
    """Append an observed delivery record; evidence level must be explicit."""
    value = _stamped(_binding_value(entry, ttl_seconds), ttl_seconds)
    return _append_new(value)


def record_card_delivery(entry, *, ttl_seconds=86400):
    """Append a CONTROLLER_DELIVERED receipt bound to role-card digests.

    ``entry`` holds every binding field plus ``route``, ``base_commit`` (the
    40-hex commit the cards were read from) and ``cards`` (a list of
    ``{'path', 'sha256'}``). ``evidence_level`` may be omitted; any value
    other than CONTROLLER_DELIVERED is refused.
    """
    if type(entry) is not dict:
        _fail('INVALID_ENTRY')
    level = entry.get('evidence_level', CONTROLLER_DELIVERED)
    if level != CONTROLLER_DELIVERED:
        _fail('INVALID_EVIDENCE_LEVEL')
    value = _binding_value({**entry, 'evidence_level': level}, ttl_seconds)
    route = entry.get('route')
    base_commit = entry.get('base_commit')
    if (not _valid_route(route) or type(base_commit) is not str or
            _COMMIT_RE.fullmatch(base_commit) is None):
        _fail('INVALID_ENTRY')
    value.update(route=route, base_commit=base_commit.lower(),
                 cards=_card_digests(entry.get('cards')))
    return _append_new(_stamped(value, ttl_seconds))


def record_not_delivered(entry, *, ttl_seconds=86400):
    """Append a NOT_DELIVERED event for one route run with a fixed reason.

    ``entry`` holds ``task_id``, ``run_id``, ``role``, ``route`` and
    ``reason_code`` (one of NOT_DELIVERED_REASONS). The event is stored with
    evidence level UNVERIFIED and never verifies.
    """
    if type(entry) is not dict or type(ttl_seconds) is not int or ttl_seconds <= 0:
        _fail('INVALID_ENTRY')
    if any(type(entry.get(key)) is not str or not entry[key]
           for key in ('task_id', 'run_id', 'role')):
        _fail('INVALID_ENTRY')
    if not _valid_route(entry.get('route')):
        _fail('INVALID_ENTRY')
    reason = entry.get('reason_code')
    if type(reason) is not str or reason not in NOT_DELIVERED_REASONS:
        _fail('INVALID_REASON')
    value = {
        'entry_type': NOT_DELIVERED,
        'task_id': entry['task_id'],
        'run_id': entry['run_id'],
        'role': entry['role'],
        'route': entry['route'],
        'reason_code': reason,
        'evidence_level': 'UNVERIFIED',
    }
    return _append_new(_stamped(value, ttl_seconds))


def _append_new(value):
    path = _path()
    lock_fd = _locked(path)
    try:
        records = _read(path, recover_torn=True)
        if not _chain(records) or not _recover_anchor(path, records):
            _fail('CHAIN_BROKEN')
        return _append_locked(path, records, value)
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
            records = _read(path, recover_torn=True)
        except SkillLedgerError:
            return {'status': 'UNVERIFIED', 'reason': 'LEDGER_UNREADABLE'}
        if not _chain(records):
            return {'status': 'UNVERIFIED', 'reason': 'CHAIN_BROKEN'}
        try:
            recovered = _recover_anchor(path, records)
        except SkillLedgerError:
            return {'status': 'UNVERIFIED', 'reason': 'LEDGER_UNREADABLE'}
        if not recovered:
            return {'status': 'UNVERIFIED', 'reason': 'CHAIN_BROKEN'}
        # NOT_DELIVERED events are never receipts, so they never verify and
        # never steer the mismatch reason.
        by_id = [item for item in records if item.get('receipt_id') and
                 item.get('entry_type') not in ('REVOCATION', NOT_DELIVERED)]
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
                if current is None:
                    return {'status': 'UNVERIFIED', 'reason': 'INVALID_NOW'}
                if expiry is None:
                    return {'status': 'UNVERIFIED', 'reason': 'LEDGER_UNREADABLE'}
                if current >= expiry:
                    return {'status': 'UNVERIFIED', 'reason': 'EXPIRED'}
                if item.get('evidence_level') == 'UNVERIFIED':
                    return {'status': 'UNVERIFIED', 'reason': 'NOT_FOUND'}
                return {'status': 'VERIFIED', 'reason': None,
                        'receipt_id': item['receipt_id'],
                        'evidence_level': item['evidence_level']}
        # Prefer a receipt with matching identity fields before considering its
        # revision bindings. This makes a wrong HEAD classify consistently even
        # when another run happens to match more of the trailing fields.
        if by_id:
            identity_fields = fields[:-2]
            identity_values = supplied[:-2]
            item = max(by_id, key=lambda row: (
                sum(type(value) is str and bool(value) and row.get(key) == value
                    for key, value in zip(identity_fields, identity_values)),
                sum(type(value) is str and bool(value) and row.get(key) == value
                    for key, value in zip(fields, supplied))))
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
    try:
        _canonical({'reason': reason})
    except (TypeError, ValueError, UnicodeError, OverflowError):
        _fail('INVALID_ENTRY')
    path = _path()
    lock_fd = _locked(path)
    try:
        records = _read(path, recover_torn=True)
        if not _chain(records) or not _recover_anchor(path, records):
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
        return _append_locked(path, records, event)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def task_summary(task_id, roles, now=None):
    """Return a mapping of roles to their newest active evidence level."""
    try:
        summary = {role: 'UNVERIFIED' for role in roles}
    except TypeError:
        return {}
    if type(task_id) is not str or not task_id or not summary:
        return summary
    path = _path()
    try:
        lock_fd = _locked(path)
    except SkillLedgerError:
        return summary
    try:
        try:
            records = _read(path)
        except SkillLedgerError:
            return summary
        if not _chain(records):
            return summary
        current = _stamp(now) if now is not None else datetime.now(timezone.utc)
        if current is None:
            return summary
        revoked_receipts = {
            item['revokes_receipt_id']
            for item in records
            if item.get('entry_type') == 'REVOCATION' and item.get('revokes_receipt_id')
        }
        remaining_roles = set(summary.keys())
        for item in reversed(records):
            if not remaining_roles:
                break
            if type(item) is not dict:
                continue
            if item.get('entry_type') == 'REVOCATION':
                continue
            if item.get('task_id') != task_id:
                continue
            role = item.get('role')
            if role not in remaining_roles:
                continue
            if item.get('status') != 'ACTIVE':
                continue
            receipt_id = item.get('receipt_id')
            if not receipt_id or receipt_id in revoked_receipts:
                continue
            expiry = _stamp(item.get('expires_at'))
            if expiry is None or current >= expiry:
                continue
            level = item.get('evidence_level')
            if level in EVIDENCE_LEVELS:
                summary[role] = level
            else:
                summary[role] = 'UNVERIFIED'
            remaining_roles.remove(role)
        return summary
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

