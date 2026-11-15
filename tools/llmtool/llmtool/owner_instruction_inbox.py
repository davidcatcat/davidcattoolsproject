"""Private local Owner instruction inbox store (#1160 PR 2).

One JSON file at a caller-given absolute, normalized path holds owner
instructions: schema_version 1, enabled, entries and tombstones. An entry has
exactly id (64 lowercase hex), alias ([a-z][a-z0-9_-]{0,31}), update_id
(int 0..2**63-1), received_at (int >= 0), state (QUEUED, SEEN, ACKED,
REVOKED, EXPIRED, REVOKE_REQUESTED, REVOKE_ACKED) and text (str).

Only init_state creates the file, only when missing, in a directory owned by
the user and not group or other writable. Load fails closed (missing,
corrupt or changed) with strict JSON; duplicate keys, unknown or missing
fields, wrong types (bool is not int), duplicate ids and an id in tombstones
are refused. Every write is temp file, fsync, atomic replace and directory
fsync under one exclusive flock on the directory from load through replace;
it replaces only the loaded identity and is skipped when nothing changed.
now is a caller int and no clock is read here. At most 200 entries, 20
unacked, 1000 tombstones and 1 MiB of state; old terminal entries compact
to tombstones. An error holds one fixed code, never a path, text or input.

Accident guard against same-user agents, not a security boundary. POSIX only;
standard library and .errors only; no network, CLI or TaskStore.
"""

import contextlib
import fcntl
import hashlib
import json
import os
import re
import stat
import uuid

from .errors import LLMToolError

__all__ = [
    'ERROR_CODES', 'EXPIRY_SECONDS', 'MAX_ENTRIES', 'MAX_STATE_BYTES',
    'MAX_STATUS_LIMIT', 'MAX_TEXT', 'MAX_TOMBSTONES', 'MAX_UNACKED',
    'MAX_UPDATE_ID', 'SCHEMA_VERSION', 'STATES', 'OwnerInboxError', 'ack',
    'add', 'init_state', 'instruction_id', 'mark_seen', 'peek', 'revoke',
    'set_enabled', 'status',
]

SCHEMA_VERSION = 1
MAX_STATE_BYTES = 1024 * 1024
MAX_ENTRIES = 200
MAX_TOMBSTONES = 1000
MAX_UNACKED = 20
MAX_TEXT = 2000
MAX_STATUS_LIMIT = 10
MAX_UPDATE_ID = 2 ** 63 - 1
EXPIRY_SECONDS = 86400

QUEUED, SEEN, ACKED, REVOKED, EXPIRED, REVOKE_REQUESTED, REVOKE_ACKED = (
    'QUEUED', 'SEEN', 'ACKED', 'REVOKED', 'EXPIRED', 'REVOKE_REQUESTED',
    'REVOKE_ACKED')
STATES = frozenset((QUEUED, SEEN, ACKED, REVOKED, EXPIRED,
                    REVOKE_REQUESTED, REVOKE_ACKED))
_LIVE = frozenset((QUEUED, SEEN, REVOKE_REQUESTED))
_DONE = frozenset((ACKED, REVOKED, EXPIRED, REVOKE_ACKED))

STATE_MISSING = 'OWNER_INBOX_STATE_MISSING'
STATE_EXISTS = 'OWNER_INBOX_STATE_EXISTS'
STATE_CORRUPT = 'OWNER_INBOX_STATE_CORRUPT'
STATE_CHANGED = 'OWNER_INBOX_STATE_CHANGED'
DIR_UNSAFE = 'OWNER_INBOX_DIR_UNSAFE'
WRITE_FAILED = 'OWNER_INBOX_WRITE_FAILED'
ARGUMENT_INVALID = 'OWNER_INBOX_ARGUMENT_INVALID'
DISABLED = 'OWNER_INBOX_DISABLED'
FULL = 'OWNER_INBOX_FULL'
HISTORY_LIMIT = 'OWNER_INBOX_HISTORY_LIMIT'
NOT_FOUND = 'OWNER_INBOX_NOT_FOUND'
NOT_ACKABLE = 'OWNER_INBOX_NOT_ACKABLE'
ERROR_CODES = frozenset((
    STATE_MISSING, STATE_EXISTS, STATE_CORRUPT, STATE_CHANGED, DIR_UNSAFE,
    WRITE_FAILED, ARGUMENT_INVALID, DISABLED, FULL, HISTORY_LIMIT, NOT_FOUND,
    NOT_ACKABLE))

_ALIAS_RE = re.compile(r'[a-z][a-z0-9_-]{0,31}')
_ID_RE = re.compile(r'[0-9a-f]{64}')
_CODE_RE = re.compile(r'[0-9a-f]{12}')
_TOP = frozenset(('schema_version', 'enabled', 'entries', 'tombstones'))
_FIELDS = frozenset(('id', 'alias', 'update_id', 'received_at', 'state', 'text'))
_TEMP = '.owner-inbox-'
_READ_CHUNK = 64 * 1024
_DOMAIN = b'llmtool.owner_instruction_inbox.v1\n'

_NOFOLLOW = getattr(os, 'O_NOFOLLOW', 0)
_CLOEXEC = getattr(os, 'O_CLOEXEC', 0)
_NONBLOCK = getattr(os, 'O_NONBLOCK', 0)
_DIRECTORY = getattr(os, 'O_DIRECTORY', 0)
_READ_FLAGS = os.O_RDONLY | _NOFOLLOW | _NONBLOCK | _CLOEXEC
_CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC
_DIR_FLAGS = os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC


class OwnerInboxError(LLMToolError):
    """An inbox failure whose message is exactly one fixed code."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _fail(code):
    return OwnerInboxError(code)


def _require(ok, code=ARGUMENT_INVALID):
    if not ok:
        raise _fail(code)


def _state_path(path):
    try:
        if isinstance(path, os.PathLike):
            path = os.fspath(path)
    except Exception:
        raise _fail(ARGUMENT_INVALID) from None
    _require(type(path) is str and '\x00' not in path and os.path.isabs(path)
             and os.path.normpath(path) == path
             and os.path.basename(path) not in ('', '.', '..'))
    return path


def _is_alias(value):
    return type(value) is str and _ALIAS_RE.fullmatch(value) is not None


def _is_id(value):
    return type(value) is str and _ID_RE.fullmatch(value) is not None


def _is_code(value):
    return type(value) is str and _CODE_RE.fullmatch(value) is not None


def _is_update(value):
    return type(value) is int and 0 <= value <= MAX_UPDATE_ID


def _is_now(value):
    return type(value) is int and value >= 0


def _require_text(value):
    _require(type(value) is str and 1 <= len(value) <= MAX_TEXT
             and '\x00' not in value)


def instruction_id(alias, update_id):
    """Return the 64-hex instruction id for an alias and update id."""
    _require(_is_alias(alias) and _is_update(update_id))
    payload = (_DOMAIN + alias.encode('ascii') + b'\n' +
               str(update_id).encode('ascii'))
    return hashlib.sha256(payload).hexdigest()


def _check_dir_info(info):
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
             and not info.st_mode & 0o022, DIR_UNSAFE)


def _check_dir(directory, missing_code):
    try:
        info = os.stat(directory, follow_symlinks=False)
    except FileNotFoundError:
        raise _fail(missing_code) from None
    except OSError:
        raise _fail(DIR_UNSAFE) from None
    _check_dir_info(info)


def _check_file(info):
    _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
             and info.st_uid == os.getuid()
             and stat.S_IMODE(info.st_mode) == 0o600
             and info.st_size <= MAX_STATE_BYTES, STATE_CORRUPT)


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _temp_path(directory):
    return os.path.join(directory, _TEMP + uuid.uuid4().hex + '.tmp')


def _write_all(descriptor, payload):
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError('short write')
        view = view[written:]


def _fill_temp(descriptor, payload):
    try:
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_dir(directory):
    descriptor = os.open(directory, _DIR_FLAGS)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _discard(temp):
    try:
        os.unlink(temp)
    except OSError:
        pass


def _encode(state):
    payload = json.dumps(state, sort_keys=True, separators=(',', ':'),
                         allow_nan=False, ensure_ascii=True).encode('ascii')
    _require(len(payload) <= MAX_STATE_BYTES, WRITE_FAILED)
    return payload


def _unique_pairs(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError('duplicate key')
        result[name] = value
    return result


def _reject_constant(_name):
    raise ValueError('non-finite number')


def _validate(data):
    """Return a fresh normalized state, or refuse anything off-schema."""
    if type(data) is not dict or set(data) != _TOP:
        raise _fail(STATE_CORRUPT)
    version = data['schema_version']
    enabled = data['enabled']
    entries = data['entries']
    tombstones = data['tombstones']
    _require(type(version) is int and version == SCHEMA_VERSION
             and type(enabled) is bool and type(entries) is list
             and len(entries) <= MAX_ENTRIES and type(tombstones) is list
             and len(tombstones) <= MAX_TOMBSTONES, STATE_CORRUPT)
    known = set()
    for item in tombstones:
        _require(_is_id(item) and item not in known, STATE_CORRUPT)
        known.add(item)
    result = []
    for entry in entries:
        _require(type(entry) is dict and set(entry) == _FIELDS, STATE_CORRUPT)
        ident = entry['id']
        _require(_is_id(ident) and _is_alias(entry['alias'])
                 and _is_update(entry['update_id'])
                 and type(entry['received_at']) is int
                 and entry['received_at'] >= 0
                 and type(entry['state']) is str and entry['state'] in STATES
                 and type(entry['text']) is str
                 and len(entry['text']) <= MAX_TEXT
                 and '\x00' not in entry['text']
                 and ident == instruction_id(entry['alias'], entry['update_id'])
                 and ident not in known,
                 STATE_CORRUPT)
        known.add(ident)
        result.append(dict(entry))
    # Codex security v91 (CWE-20): the caps that add() enforces also bind on load.
    _require(sum(1 for item in result if item['state'] in _LIVE) <= MAX_UNACKED,
             STATE_CORRUPT)
    return {'schema_version': SCHEMA_VERSION, 'enabled': enabled,
            'entries': result, 'tombstones': list(tombstones)}


def _parse(raw):
    try:
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs,
                          parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise _fail(STATE_CORRUPT) from None
    return _validate(data)


def _read_bounded(descriptor):
    chunks = []
    total = 0
    while total <= MAX_STATE_BYTES:
        chunk = os.read(descriptor,
                        min(_READ_CHUNK, MAX_STATE_BYTES + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b''.join(chunks)


def _load(path):
    """Return (state, identity) for the current file, failing closed."""
    _check_dir(os.path.dirname(path), STATE_MISSING)
    try:
        info = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        raise _fail(STATE_MISSING) from None
    except OSError:
        raise _fail(STATE_CORRUPT) from None
    _check_file(info)
    try:
        descriptor = os.open(path, _READ_FLAGS)
    except FileNotFoundError:
        raise _fail(STATE_MISSING) from None
    except OSError:
        raise _fail(STATE_CORRUPT) from None
    try:
        opened = os.fstat(descriptor)
        _check_file(opened)
        raw = _read_bounded(descriptor)
        after = os.fstat(descriptor)
    except OSError:
        raise _fail(STATE_CORRUPT) from None
    finally:
        os.close(descriptor)
    _require(len(raw) <= MAX_STATE_BYTES, STATE_CORRUPT)
    identity = _identity(opened)
    _require(_identity(after) == identity and len(raw) == opened.st_size,
             STATE_CHANGED)
    return _parse(raw), identity


@contextlib.contextmanager
def _writer_lock(path, missing_code):
    """Hold the exclusive writer lock on the state directory for the block."""
    directory = os.path.dirname(path)
    _check_dir(directory, missing_code)
    try:
        descriptor = os.open(directory, _DIR_FLAGS)
    except FileNotFoundError:
        raise _fail(missing_code) from None
    except OSError:
        raise _fail(DIR_UNSAFE) from None
    locked = False
    try:
        try:
            _check_dir_info(os.fstat(descriptor))
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            locked = True
        except OSError:
            raise _fail(DIR_UNSAFE) from None
        yield descriptor
    finally:
        if locked:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            os.close(descriptor)
        except OSError:
            pass


def _same_dir(directory, lock):
    held = os.fstat(lock)
    try:
        current = os.stat(directory, follow_symlinks=False)
    except OSError:
        raise _fail(STATE_CHANGED) from None
    _require((held.st_dev, held.st_ino) == (current.st_dev, current.st_ino),
             STATE_CHANGED)


def _unchanged(path, identity):
    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError:
        raise _fail(STATE_CHANGED) from None
    _require(stat.S_ISREG(current.st_mode) and _identity(current) == identity,
             STATE_CHANGED)


def _store(path, identity, state, lock):
    """Atomically replace the loaded file, only if it is still that file."""
    directory = os.path.dirname(path)
    _check_dir(directory, STATE_CHANGED)
    payload = _encode(state)
    temp = _temp_path(directory)
    created = False
    try:
        _same_dir(directory, lock)
        _unchanged(path, identity)
        descriptor = os.open(temp, _CREATE_FLAGS, 0o600)
        created = True
        _fill_temp(descriptor, payload)
        _same_dir(directory, lock)
        _unchanged(path, identity)
        os.replace(temp, path)
        created = False
        _fsync_dir(directory)
    except OwnerInboxError:
        raise
    except OSError:
        raise _fail(WRITE_FAILED) from None
    finally:
        if created:
            _discard(temp)


def _expire(state, now):
    """Mark QUEUED or SEEN entries at least 24 h old EXPIRED in memory."""
    for entry in state['entries']:
        if (entry['state'] in (QUEUED, SEEN)
                and now - entry['received_at'] >= EXPIRY_SECONDS):
            entry['state'] = EXPIRED


def _ordered(entries):
    return sorted(enumerate(entries),
                  key=lambda pair: (pair[1]['received_at'], pair[0]))


def _find_code(state, code):
    return next((entry for entry in state['entries']
                 if entry['id'][:12] == code), None)


def _result(code, entry):
    return {'code': code, 'state': entry['state']}


def init_state(path):
    """Create an empty, private, enabled state file; refuse if it exists."""
    path = _state_path(path)
    with _writer_lock(path, DIR_UNSAFE):
        directory = os.path.dirname(path)
        if os.path.lexists(path):
            raise _fail(STATE_EXISTS)
        payload = _encode({'schema_version': SCHEMA_VERSION, 'enabled': True,
                           'entries': [], 'tombstones': []})
        temp = _temp_path(directory)
        created = False
        try:
            descriptor = os.open(temp, _CREATE_FLAGS, 0o600)
            created = True
            _fill_temp(descriptor, payload)
            try:
                os.link(temp, path)
            except FileExistsError:
                raise _fail(STATE_EXISTS) from None
            os.unlink(temp)
            created = False
            _fsync_dir(directory)
        except OwnerInboxError:
            raise
        except OSError:
            raise _fail(WRITE_FAILED) from None
        finally:
            if created:
                _discard(temp)


def add(path, alias, update_id, text, now):
    """Queue one instruction; refuse when disabled, full or out of history."""
    path = _state_path(path)
    _require(_is_alias(alias) and _is_update(update_id) and _is_now(now))
    _require_text(text)
    ident = instruction_id(alias, update_id)
    code = ident[:12]
    with _writer_lock(path, STATE_MISSING) as lock:
        state, identity = _load(path)
        _expire(state, now)
        _require(state['enabled'], DISABLED)
        current = next((entry for entry in state['entries']
                        if entry['id'] == ident), None)
        if current is not None:
            return {'code': code, 'state': current['state'], 'created': False}
        if ident in state['tombstones']:
            return {'code': code, 'state': ACKED, 'created': False}
        live = sum(1 for entry in state['entries'] if entry['state'] in _LIVE)
        _require(live < MAX_UNACKED, FULL)
        entries = state['entries']
        if len(entries) >= MAX_ENTRIES:
            victim = next((entry for _index, entry in _ordered(entries)
                           if entry['state'] in _DONE), None)
            _require(victim is not None, FULL)
            _require(len(state['tombstones']) < MAX_TOMBSTONES,
                     HISTORY_LIMIT)
            entries.remove(victim)
            state['tombstones'].append(victim['id'])
        entries.append({'id': ident, 'alias': alias, 'update_id': update_id,
                        'received_at': now, 'state': QUEUED, 'text': text})
        _store(path, identity, state, lock)
    return {'code': code, 'state': QUEUED, 'created': True}


def peek(path, now):
    """Return live entries oldest first; never writes and never includes ids."""
    path = _state_path(path)
    _require(_is_now(now))
    state, _unused = _load(path)
    _expire(state, now)
    return [{
        'code': entry['id'][:12],
        'alias': entry['alias'],
        'received_at': entry['received_at'],
        'state': entry['state'],
        'text': '' if entry['state'] == REVOKE_REQUESTED else entry['text'],
    } for _index, entry in _ordered(state['entries'])
        if entry['state'] in _LIVE]


def mark_seen(path, code, now):
    """Move a QUEUED entry to SEEN; SEEN is inert and other states refuse."""
    path = _state_path(path)
    _require(_is_code(code) and _is_now(now))
    with _writer_lock(path, STATE_MISSING) as lock:
        state, identity = _load(path)
        _expire(state, now)
        entry = _find_code(state, code)
        _require(entry is not None, NOT_FOUND)
        if entry['state'] == SEEN:
            return _result(code, entry)
        _require(entry['state'] == QUEUED, NOT_ACKABLE)
        entry['state'] = SEEN
        _store(path, identity, state, lock)
    return {'code': code, 'state': SEEN}


def ack(path, code, now):
    """Acknowledge a live entry; ACKED and REVOKE_ACKED are inert."""
    path = _state_path(path)
    _require(_is_code(code) and _is_now(now))
    with _writer_lock(path, STATE_MISSING) as lock:
        state, identity = _load(path)
        _expire(state, now)
        entry = _find_code(state, code)
        _require(entry is not None, NOT_FOUND)
        if entry['state'] in (ACKED, REVOKE_ACKED):
            return _result(code, entry)
        if entry['state'] in (QUEUED, SEEN):
            entry['state'] = ACKED
        elif entry['state'] == REVOKE_REQUESTED:
            entry['state'] = REVOKE_ACKED
        else:
            raise _fail(NOT_ACKABLE)
        _store(path, identity, state, lock)
    return _result(code, entry)


def revoke(path, alias, code, now):
    """Revoke this alias's entry; an ACKED entry becomes REVOKE_REQUESTED."""
    path = _state_path(path)
    _require(_is_alias(alias) and _is_code(code) and _is_now(now))
    with _writer_lock(path, STATE_MISSING) as lock:
        state, identity = _load(path)
        _expire(state, now)
        entry = _find_code(state, code)
        _require(entry is not None and entry['alias'] == alias, NOT_FOUND)
        if entry['state'] in (QUEUED, SEEN, EXPIRED):
            entry['state'] = REVOKED
        elif entry['state'] == ACKED:
            entry['state'] = REVOKE_REQUESTED
        else:
            return _result(code, entry)
        _store(path, identity, state, lock)
    return _result(code, entry)


def status(path, alias, now, code=None, limit=10):
    """Return up to ``limit`` newest alias states, or one code's state."""
    path = _state_path(path)
    _require(_is_alias(alias) and _is_now(now))
    _require(type(limit) is int and 1 <= limit <= MAX_STATUS_LIMIT)
    if code is not None:
        _require(_is_code(code))
    state, _unused = _load(path)
    _expire(state, now)
    mine = [(index, entry) for index, entry in _ordered(state['entries'])
            if entry['alias'] == alias]
    if code is not None:
        for _index, entry in mine:
            if entry['id'][:12] == code:
                return [{'code': code, 'state': entry['state']}]
        raise _fail(NOT_FOUND)
    return [{'code': entry['id'][:12], 'state': entry['state']}
            for _index, entry in list(reversed(mine))[:limit]]


def set_enabled(path, enabled):
    """Set the intake flag; the same value is inert and writes nothing."""
    path = _state_path(path)
    _require(type(enabled) is bool)
    with _writer_lock(path, STATE_MISSING) as lock:
        state, identity = _load(path)
        if state['enabled'] == enabled:
            return enabled
        state['enabled'] = enabled
        _store(path, identity, state, lock)
    return enabled
