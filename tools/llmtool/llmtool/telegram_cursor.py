"""Private Telegram cursor: per-alias update offset and claimed notification keys.

One JSON file at a caller-given absolute, normalized path holds, for each
local chat alias, a monotonic Telegram update offset and a bounded, ordered
set of claimed notification keys, so a restarted process never resends a
notification it already claimed. The file never holds a chat ID, bot token
or message text.

Rules (#1160 WP6; same state-file rules as the WP5 GitHubSync poll ledger):

- Only ``init_state`` creates the file, and only when it is missing, in a
  directory owned by the user and not writable by group or other. No other
  function creates or repairs it.
- Loading fails closed with ``TELEGRAM_STATE_MISSING`` or
  ``TELEGRAM_STATE_CORRUPT`` (``TELEGRAM_STATE_CHANGED`` if the file changes
  while it is read); the cursor and keys are never reset.
- Every write is temp file, fsync, atomic replace and directory fsync, and
  replaces only the file that was loaded (device, inode, size, mtime);
  otherwise ``TELEGRAM_STATE_CHANGED`` and the file is left as it was.
- Every call that may write waits for one exclusive ``flock`` on the state
  directory itself (opened read-only; it is never replaced) and holds it from
  load through the replace and directory fsync, so two writers of this module
  never interleave: the second one loads the first one's result.
- A call that changes nothing does not write.
- Results hold only counts, keys and offsets; an error holds only a fixed
  code, never a path or an input value.

Not wired into any CLI or loop; no network. POSIX only.
"""

import contextlib
import fcntl
import json
import os
import re
import stat
import uuid

from .errors import LLMToolError


__all__ = [
    'ERROR_CODES', 'MAX_ALIASES', 'MAX_KEYS_PER_ALIAS', 'MAX_KEYS_PER_CALL',
    'MAX_STATE_BYTES', 'MAX_UPDATE_ID', 'SCHEMA_VERSION', 'TelegramCursorError',
    'advance_offset', 'claim', 'init_state', 'offset', 'release',
]

SCHEMA_VERSION = 1
MAX_STATE_BYTES = 1024 * 1024
MAX_ALIASES = 8
MAX_KEYS_PER_ALIAS = 1000
MAX_KEYS_PER_CALL = 100
MAX_UPDATE_ID = 2 ** 63 - 1

STATE_MISSING = 'TELEGRAM_STATE_MISSING'
STATE_EXISTS = 'TELEGRAM_STATE_EXISTS'
STATE_CORRUPT = 'TELEGRAM_STATE_CORRUPT'
STATE_CHANGED = 'TELEGRAM_STATE_CHANGED'
STATE_DIR_UNSAFE = 'TELEGRAM_STATE_DIR_UNSAFE'
STATE_WRITE_FAILED = 'TELEGRAM_STATE_WRITE_FAILED'
CHAT_ALIAS_INVALID = 'TELEGRAM_CHAT_ALIAS_INVALID'
CHAT_LIMIT = 'TELEGRAM_CHAT_LIMIT'
ARGUMENT_INVALID = 'TELEGRAM_ARGUMENT_INVALID'

ERROR_CODES = frozenset((
    STATE_MISSING, STATE_EXISTS, STATE_CORRUPT, STATE_CHANGED, STATE_DIR_UNSAFE,
    STATE_WRITE_FAILED, CHAT_ALIAS_INVALID, CHAT_LIMIT, ARGUMENT_INVALID,
))

_ALIAS_RE = re.compile(r'[a-z][a-z0-9_-]{0,31}')
_KEY_RE = re.compile(r'[0-9a-f]{64}')
_TOP_FIELDS = frozenset(('schema_version', 'chats'))
_CHAT_FIELDS = frozenset(('update_offset', 'claimed'))
_TEMP_PREFIX = '.telegram-cursor-'
_READ_CHUNK = 64 * 1024

_O_NOFOLLOW = getattr(os, 'O_NOFOLLOW', 0)
_O_CLOEXEC = getattr(os, 'O_CLOEXEC', 0)
_O_NONBLOCK = getattr(os, 'O_NONBLOCK', 0)
_O_DIRECTORY = getattr(os, 'O_DIRECTORY', 0)
_READ_FLAGS = os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK | _O_CLOEXEC
_CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC
_DIR_FLAGS = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC


class TelegramCursorError(LLMToolError):
    """A cursor failure whose message is exactly one fixed code."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _fail(code):
    return TelegramCursorError(code)


# --- argument validation ---------------------------------------------------

def _state_path(path):
    try:
        if isinstance(path, os.PathLike):
            path = os.fspath(path)
    except Exception:
        raise _fail(ARGUMENT_INVALID) from None
    if (type(path) is not str or '\x00' in path or not os.path.isabs(path) or
            os.path.normpath(path) != path or
            os.path.basename(path) in ('', '.', '..')):
        raise _fail(ARGUMENT_INVALID)
    return path


def _valid_alias(alias):
    return type(alias) is str and _ALIAS_RE.fullmatch(alias) is not None


def _valid_key(item):
    return type(item) is str and _KEY_RE.fullmatch(item) is not None


def _require_alias(alias):
    if not _valid_alias(alias):
        raise _fail(CHAT_ALIAS_INVALID)


def _require_keys(keys):
    """Return the keys deduplicated in input order, or refuse the call."""
    if type(keys) not in (list, tuple) or not 1 <= len(keys) <= MAX_KEYS_PER_CALL:
        raise _fail(ARGUMENT_INVALID)
    unique = []
    seen = set()
    for item in keys:
        if not _valid_key(item):
            raise _fail(ARGUMENT_INVALID)
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _require_update_id(update_id):
    if type(update_id) is not int or not 0 <= update_id <= MAX_UPDATE_ID:
        raise _fail(ARGUMENT_INVALID)


# --- file safety -------------------------------------------------------------

def _check_dir_info(info):
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
            info.st_mode & 0o022):
        raise _fail(STATE_DIR_UNSAFE)


def _check_dir(directory, missing_code):
    try:
        info = os.stat(directory, follow_symlinks=False)
    except FileNotFoundError:
        raise _fail(missing_code) from None
    except OSError:
        raise _fail(STATE_DIR_UNSAFE) from None
    _check_dir_info(info)


def _check_file(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
            info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or
            info.st_size > MAX_STATE_BYTES):
        raise _fail(STATE_CORRUPT)


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _temp_path(directory):
    return os.path.join(directory, _TEMP_PREFIX + uuid.uuid4().hex + '.tmp')


def _write_all(descriptor, payload):
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError('short write')
        view = view[written:]


def _create_temp(temp):
    """Open a new private temp file; it is the caller's to discard from here."""
    return os.open(temp, _CREATE_FLAGS, 0o600)


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


# --- encoding ----------------------------------------------------------------

def _empty_state():
    return {'schema_version': SCHEMA_VERSION, 'chats': {}}


def _encode(state):
    payload = json.dumps(state, sort_keys=True, separators=(',', ':'),
                         allow_nan=False, ensure_ascii=True).encode('ascii')
    if len(payload) > MAX_STATE_BYTES:
        raise _fail(STATE_WRITE_FAILED)
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
    if type(data) is not dict or set(data) != _TOP_FIELDS:
        raise _fail(STATE_CORRUPT)
    version = data['schema_version']
    chats = data['chats']
    if (type(version) is not int or version != SCHEMA_VERSION or
            type(chats) is not dict or len(chats) > MAX_ALIASES):
        raise _fail(STATE_CORRUPT)
    result = {}
    for alias, entry in chats.items():
        if (not _valid_alias(alias) or type(entry) is not dict or
                set(entry) != _CHAT_FIELDS):
            raise _fail(STATE_CORRUPT)
        value = entry['update_offset']
        claimed = entry['claimed']
        if (type(value) is not int or not 0 <= value <= MAX_UPDATE_ID or
                type(claimed) is not list or len(claimed) > MAX_KEYS_PER_ALIAS):
            raise _fail(STATE_CORRUPT)
        if (not all(_valid_key(item) for item in claimed) or
                len(set(claimed)) != len(claimed)):
            raise _fail(STATE_CORRUPT)
        result[alias] = {'update_offset': value, 'claimed': list(claimed)}
    return {'schema_version': SCHEMA_VERSION, 'chats': result}


def _parse(raw):
    try:
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs,
                          parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise _fail(STATE_CORRUPT) from None
    return _validate(data)


# --- load and store ------------------------------------------------------------

def _read_bounded(descriptor):
    chunks = []
    total = 0
    while total <= MAX_STATE_BYTES:
        chunk = os.read(descriptor, min(_READ_CHUNK, MAX_STATE_BYTES + 1 - total))
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
    if len(raw) > MAX_STATE_BYTES:
        raise _fail(STATE_CORRUPT)
    identity = _identity(opened)
    if _identity(after) != identity or len(raw) != opened.st_size:
        raise _fail(STATE_CHANGED)
    return _parse(raw), identity


def _lock_exclusive(descriptor):
    """Wait for the exclusive writer lock (a seam for the contention tests)."""
    fcntl.flock(descriptor, fcntl.LOCK_EX)


@contextlib.contextmanager
def _writer_lock(path, missing_code):
    """Hold the exclusive writer lock on the state directory for the block.

    The lock is an flock on the directory itself, opened read-only. The
    directory is never replaced by this module, so every writer locks the
    same object for the whole load, check, replace and directory fsync. A
    second writer waits here and then loads the first writer's result.
    """
    directory = os.path.dirname(path)
    _check_dir(directory, missing_code)
    try:
        descriptor = os.open(directory, _DIR_FLAGS)
    except FileNotFoundError:
        raise _fail(missing_code) from None
    except OSError:
        raise _fail(STATE_DIR_UNSAFE) from None
    locked = False
    try:
        try:
            _check_dir_info(os.fstat(descriptor))
            _lock_exclusive(descriptor)
            locked = True
        except OSError:
            raise _fail(STATE_DIR_UNSAFE) from None
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


def _require_same_dir(directory, lock):
    held = os.fstat(lock)
    try:
        current = os.stat(directory, follow_symlinks=False)
    except OSError:
        raise _fail(STATE_CHANGED) from None
    if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
        raise _fail(STATE_CHANGED)


def _require_unchanged(path, identity):
    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError:
        raise _fail(STATE_CHANGED) from None
    if not stat.S_ISREG(current.st_mode) or _identity(current) != identity:
        raise _fail(STATE_CHANGED)


def _store(path, identity, state, lock):
    """Atomically replace the loaded file, only if it is still that file.

    The caller holds ``_writer_lock`` (``lock``) from before the load until
    this returns, so no writer of this module can replace the file between
    the identity check and the replace. The identity check still refuses a
    change made outside this module. If the directory fsync fails after the
    replace, the new state is in place but TELEGRAM_STATE_WRITE_FAILED is
    raised.
    """
    directory = os.path.dirname(path)
    _check_dir(directory, STATE_CHANGED)
    payload = _encode(state)
    temp = _temp_path(directory)
    created = False
    try:
        _require_same_dir(directory, lock)
        _require_unchanged(path, identity)
        descriptor = _create_temp(temp)
        created = True
        _fill_temp(descriptor, payload)
        _require_same_dir(directory, lock)
        _require_unchanged(path, identity)
        os.replace(temp, path)
        created = False
        _fsync_dir(directory)
    except TelegramCursorError:
        raise
    except OSError:
        raise _fail(STATE_WRITE_FAILED) from None
    finally:
        if created:
            _discard(temp)


# --- public API ----------------------------------------------------------------

def init_state(path):
    """Create an empty state file; refuse if it exists or the directory is unsafe.

    The file appears complete and private: a fsynced temp file is hard-linked
    into place (which never overwrites), the temp name is removed, and the
    directory is fsynced, all under the writer lock.
    """
    path = _state_path(path)
    with _writer_lock(path, STATE_DIR_UNSAFE):
        _create_state(path)


def _create_state(path):
    directory = os.path.dirname(path)
    if os.path.lexists(path):
        raise _fail(STATE_EXISTS)
    payload = _encode(_empty_state())
    temp = _temp_path(directory)
    created = False
    try:
        descriptor = _create_temp(temp)
        created = True
        _fill_temp(descriptor, payload)
        try:
            os.link(temp, path)
        except FileExistsError:
            raise _fail(STATE_EXISTS) from None
        os.unlink(temp)
        created = False
        _fsync_dir(directory)
    except TelegramCursorError:
        raise
    except OSError:
        raise _fail(STATE_WRITE_FAILED) from None
    finally:
        if created:
            _discard(temp)


def claim(path, alias, keys):
    """Claim 1..100 keys; return those not already claimed, in input order.

    Writes once, and only when at least one key is new. Each alias keeps at
    most MAX_KEYS_PER_ALIAS keys; the oldest are dropped first.
    """
    path = _state_path(path)
    _require_alias(alias)
    wanted = _require_keys(keys)
    with _writer_lock(path, STATE_MISSING) as lock:
        state, identity = _load(path)
        chats = state['chats']
        entry = chats.get(alias)
        present = set(entry['claimed']) if entry is not None else set()
        fresh = [item for item in wanted if item not in present]
        if not fresh:
            return []
        if entry is None:
            if len(chats) >= MAX_ALIASES:
                raise _fail(CHAT_LIMIT)
            entry = {'update_offset': 0, 'claimed': []}
            chats[alias] = entry
        entry['claimed'] = (entry['claimed'] + fresh)[-MAX_KEYS_PER_ALIAS:]
        _store(path, identity, state, lock)
    return fresh


def release(path, alias, keys):
    """Unclaim listed keys that are present; return them in input order.

    Unknown keys and unknown aliases are inert; nothing removed means no write.
    """
    path = _state_path(path)
    _require_alias(alias)
    wanted = _require_keys(keys)
    with _writer_lock(path, STATE_MISSING) as lock:
        state, identity = _load(path)
        entry = state['chats'].get(alias)
        if entry is None:
            return []
        present = set(entry['claimed'])
        removed = [item for item in wanted if item in present]
        if not removed:
            return []
        gone = set(removed)
        entry['claimed'] = [item for item in entry['claimed'] if item not in gone]
        _store(path, identity, state, lock)
    return removed


def advance_offset(path, alias, update_id):
    """Raise the alias offset to update_id if higher; return the current offset.

    An equal or lower value is inert and does not write.
    """
    path = _state_path(path)
    _require_alias(alias)
    _require_update_id(update_id)
    with _writer_lock(path, STATE_MISSING) as lock:
        state, identity = _load(path)
        chats = state['chats']
        entry = chats.get(alias)
        current = entry['update_offset'] if entry is not None else 0
        if update_id <= current:
            return current
        if entry is None:
            if len(chats) >= MAX_ALIASES:
                raise _fail(CHAT_LIMIT)
            entry = {'update_offset': 0, 'claimed': []}
            chats[alias] = entry
        entry['update_offset'] = update_id
        _store(path, identity, state, lock)
    return update_id


def offset(path, alias):
    """Return the alias offset without writing; 0 for an alias not yet seen."""
    path = _state_path(path)
    _require_alias(alias)
    state, _identity_unused = _load(path)
    entry = state['chats'].get(alias)
    return entry['update_offset'] if entry is not None else 0
