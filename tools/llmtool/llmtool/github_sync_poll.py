"""One bounded GitHub sync poll step with a private cursor and request ledger.

The caller injects a read-only GitHub GET function and a message parser. This
module is not wired into any CLI or loop: it creates no Task, posts nothing and
calls no model. Results carry counts, request keys and codes, never comment
bodies or refusal text.

State is one owner-only JSON file at a caller-given absolute path. Only
``init_state`` creates it. Every later write replaces it atomically (temp
file, fsync, rename, directory fsync). A missing, unreadable, corrupt,
wrong-mode, hard-linked or symlinked file fails closed and is never reset.

The cursor and ledger are committed in one write after the whole batch is
handled. A crash before that write replays the same comments next time, and
the ledger rules make the replayed result identical.

Every call holds ``flock`` on the validated state-directory descriptor:
exclusive in ``init_state``, ``poll_once`` and ``link_task`` from before the
load (or existence check) through the write and directory fsync, shared around
the load in ``pending``. A competing call waits, so no update is lost and no
reader sees a replace or the two-name window of ``init_state`` as corruption.
A poll holds the lock across its GitHub reads, so a slow read delays a
competing link; ``github_get`` and ``parse`` must not call back into this
module for the same directory, or they wait on their own lock. The lock is
advisory among callers of this module. A lock
that cannot be taken fails closed with SYNC_STATE_LOCK_FAILED; nothing runs
unlocked. Closing the descriptor releases the lock on every return and
exception.

Page-number pagination is not a snapshot. One page is one response and has no
gap, but a comment deleted between two page reads shifts later pages up, so
the walk skips the comment that moved across the page boundary. A walk that
read more than one page is therefore accepted only when a second walk agrees
on every id the first walk could have seen; otherwise the poll is OFFLINE and
the cursor does not move. ``max_pages`` bounds every page read in one poll,
confirmation walks included: a poll that would read more pages fails with
SYNC_CATCHUP_LIMIT and state unchanged, so a k-page catch-up needs
``max_pages >= 2 * k``. A page whose ids are not strictly newest first is an
invalid response (OFFLINE), so an out-of-order id never ends a walk early.

Failures raise ``LLMToolError`` with a fixed ``sync_code``; a GitHub read
failure is not raised but returned as ``OFFLINE`` with a backoff.
"""

import contextlib
import json
import os
import re
import stat
import tempfile

try:
    import fcntl
except ImportError:  # No flock on this platform: every call fails closed.
    fcntl = None

from .errors import LLMToolError

SCHEMA_VERSION = 1
PAGE_SIZE = 100
MAX_PAGES_LIMIT = 50
MAX_REQUESTS = 2000
MAX_STATE_BYTES = 4 * 1024 * 1024
MAX_FAILURE_COUNT = 1000000
MAX_WALKS = 3
BACKOFF_SECONDS = (30, 60, 120, 240, 480, 900)

_MAX_ID = 2**63 - 1
_MAX_REVISION = 2**31 - 1
_KEY = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z')
_KIND = re.compile(r'[a-z][a-z0-9_]{0,63}\Z')
_CODE = re.compile(r'[A-Z][A-Z0-9_]{0,63}\Z')
_TASK_ID = re.compile(r'[0-9a-f]{32}\Z')
_ENTRY_STATES = ('PENDING_PM', 'LINKED', 'STALE_LINK')
_STATE_KEYS = frozenset(('schema_version', 'cursor', 'failure_count', 'requests'))
_ENTRY_KEYS = frozenset(('state', 'kind', 'revision', 'delivery_id', 'first_comment_id',
                         'comment_id', 'task_id', 'linked_revision'))
_PARSED_KEYS = frozenset(('request_key', 'revision', 'delivery_id', 'kind'))
_COUNT_NAMES = ('non_sync', 'refused', 'pending_pm', 'inert', 'duplicate',
                'revision_pending', 'stale_link')


class _Invalid(Exception):
    """Internal: the state value does not match the schema."""


class _Offline(Exception):
    """Internal: a GitHub read failed or returned an invalid response."""

    def __init__(self, pages_read):
        super().__init__()
        self.pages_read = pages_read


def _fail(code):
    error = LLMToolError(f'GitHub sync step stopped: {code}; details omitted')
    error.sync_code = code
    raise error from None


def _is_int(value, low, high):
    return type(value) is int and low <= value <= high


def _is_text(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


# --- state file -------------------------------------------------------------

def _split_path(path):
    if (type(path) is not str or not path or '\0' in path or not os.path.isabs(path) or
            os.path.normpath(path) != path):
        _fail('SYNC_ARGUMENT_INVALID')
    parent, name = os.path.split(path)
    if not name or name in ('.', '..'):
        _fail('SYNC_ARGUMENT_INVALID')
    return parent, name


def _safe_dir_info(info):
    return (stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and
            stat.S_IMODE(info.st_mode) & 0o022 == 0)


def _dir_safe(parent):
    """Owner's directory, not writable by group or other. Raises OSError."""
    return _safe_dir_info(os.stat(parent))


@contextlib.contextmanager
def _state_lock(parent, exclusive, missing_code, unsafe_code):
    """Hold flock on the validated state directory for the whole block.

    The descriptor is checked to be a safe directory before the lock, and the
    path is checked to still name it after the lock. A lock that cannot be
    taken raises SYNC_STATE_LOCK_FAILED. The finally clause unlocks and closes
    on every return and exception.
    """
    if fcntl is None:
        _fail('SYNC_STATE_LOCK_FAILED')
    try:
        fd = os.open(parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) |
                     getattr(os, 'O_CLOEXEC', 0))
    except FileNotFoundError:
        _fail(missing_code)
    except OSError:
        _fail(unsafe_code)
    locked = False
    try:
        try:
            opened = os.fstat(fd)
        except OSError:
            _fail(unsafe_code)
        if not _safe_dir_info(opened):
            _fail(unsafe_code)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        except OSError:
            _fail('SYNC_STATE_LOCK_FAILED')
        locked = True
        try:
            named = os.stat(parent)
        except FileNotFoundError:
            _fail(missing_code)
        except OSError:
            _fail(unsafe_code)
        if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
            # The path no longer names the locked directory.
            _fail(unsafe_code)
        yield
    finally:
        if locked:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass  # Closing the only descriptor below releases it as well.
        try:
            os.close(fd)
        except OSError:
            pass


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _Invalid()
        result[key] = value
    return result


def _reject_constant(_):
    raise _Invalid()


def _validate_entry(entry, cursor):
    if type(entry) is not dict or set(entry) != _ENTRY_KEYS:
        raise _Invalid()
    state = entry['state']
    revision = entry['revision']
    if (type(state) is not str or state not in _ENTRY_STATES or
            not _is_text(entry['kind'], _KIND) or
            not _is_int(revision, 0, _MAX_REVISION) or
            not _is_text(entry['delivery_id'], _KEY) or
            not _is_int(entry['first_comment_id'], 1, cursor) or
            not _is_int(entry['comment_id'], entry['first_comment_id'], cursor)):
        raise _Invalid()
    task_id, linked = entry['task_id'], entry['linked_revision']
    if state == 'PENDING_PM':
        if task_id is not None or linked is not None:
            raise _Invalid()
    elif (not _is_text(task_id, _TASK_ID) or not _is_int(linked, 0, revision) or
            (state == 'LINKED') != (linked == revision)):
        raise _Invalid()


def _validate_state(value):
    if type(value) is not dict or set(value) != _STATE_KEYS:
        raise _Invalid()
    cursor, requests = value['cursor'], value['requests']
    if (type(value['schema_version']) is not int or value['schema_version'] != SCHEMA_VERSION or
            not _is_int(cursor, 0, _MAX_ID) or
            not _is_int(value['failure_count'], 0, MAX_FAILURE_COUNT) or
            type(requests) is not dict or len(requests) > MAX_REQUESTS):
        raise _Invalid()
    for key, entry in requests.items():
        if not _is_text(key, _KEY):
            raise _Invalid()
        _validate_entry(entry, cursor)
    return value


def _load(path):
    """Return (state, identity) or fail closed; never creates or repairs the file."""
    parent, _ = _split_path(path)
    try:
        if not _dir_safe(parent):
            _fail('SYNC_STATE_CORRUPT')
        before = os.lstat(path)
    except FileNotFoundError:
        _fail('SYNC_STATE_MISSING')
    except OSError:
        _fail('SYNC_STATE_CORRUPT')
    if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or
            stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1 or
            before.st_size > MAX_STATE_BYTES):
        _fail('SYNC_STATE_CORRUPT')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK |
                     getattr(os, 'O_CLOEXEC', 0))
    except FileNotFoundError:
        _fail('SYNC_STATE_MISSING')
    except OSError:
        _fail('SYNC_STATE_CORRUPT')
    try:
        opened = os.fstat(fd)
        if (not stat.S_ISREG(opened.st_mode) or
                (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino) or
                stat.S_IMODE(opened.st_mode) != 0o600 or opened.st_nlink != 1):
            _fail('SYNC_STATE_CORRUPT')
        raw = bytearray()
        while len(raw) <= MAX_STATE_BYTES:
            chunk = os.read(fd, MAX_STATE_BYTES + 1 - len(raw))
            if not chunk:
                break
            raw.extend(chunk)
    except OSError:
        _fail('SYNC_STATE_CORRUPT')
    finally:
        os.close(fd)
    if len(raw) > MAX_STATE_BYTES:
        _fail('SYNC_STATE_CORRUPT')
    try:
        value = json.loads(bytes(raw).decode('utf-8'), object_pairs_hook=_unique_pairs,
                           parse_constant=_reject_constant)
        state = _validate_state(value)
    except (_Invalid, ValueError, RecursionError):
        _fail('SYNC_STATE_CORRUPT')
    identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
    return state, identity


def _serialize(state):
    return (json.dumps(state, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                       allow_nan=False) + '\n').encode('ascii')


def _fsync_dir(parent):
    fd = os.open(parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write(path, state, identity):
    """Write state atomically.

    identity None: create the file; never replaces an existing path.
    Otherwise replace the file only if it is still the one that was loaded.
    The caller holds the exclusive state lock; this never takes it again.
    """
    parent, name = _split_path(path)
    data = _serialize(state)
    fd = temp = None
    try:
        fd, temp = tempfile.mkstemp(prefix='.' + name + '.', suffix='.tmp', dir=parent)
        os.fchmod(fd, 0o600)
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
        os.close(fd)
        fd = None
        if identity is None:
            try:
                os.link(temp, path)
            except FileExistsError:
                _fail('SYNC_STATE_EXISTS')
            os.unlink(temp)
        else:
            try:
                current = os.lstat(path)
            except FileNotFoundError:
                _fail('SYNC_STATE_CHANGED')
            if (stat.S_ISLNK(current.st_mode) or
                    (current.st_dev, current.st_ino, current.st_size,
                     current.st_mtime_ns) != identity):
                _fail('SYNC_STATE_CHANGED')
            os.replace(temp, path)
        temp = None
        _fsync_dir(parent)
    except OSError:
        _fail('SYNC_STATE_WRITE_FAILED')
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if temp is not None:
            try:
                os.unlink(temp)
            except OSError:
                pass


# --- public API -------------------------------------------------------------

def init_state(path, start_after_comment_id):
    """Create the private state file; comments at or below the id are never handled."""
    parent, _ = _split_path(path)
    if not _is_int(start_after_comment_id, 0, _MAX_ID):
        _fail('SYNC_ARGUMENT_INVALID')
    with _state_lock(parent, True, 'SYNC_STATE_DIR_UNSAFE', 'SYNC_STATE_DIR_UNSAFE'):
        try:
            os.lstat(path)
        except FileNotFoundError:
            pass
        except OSError:
            _fail('SYNC_STATE_DIR_UNSAFE')
        else:
            _fail('SYNC_STATE_EXISTS')
        state = {'schema_version': SCHEMA_VERSION, 'cursor': start_after_comment_id,
                 'failure_count': 0, 'requests': {}}
        _write(path, state, None)
    return {'status': 'INITIALIZED', 'cursor': start_after_comment_id}


def _walk(github_get, cursor, max_pages, reads):
    """One newest-first walk: return ({id: comment} newer than cursor, pages walked).

    ``reads`` is a one-item list counting every page read in this poll; a read
    beyond ``max_pages`` in total fails with SYNC_CATCHUP_LIMIT before it is made.
    """
    fresh = {}
    page = 0
    while True:
        page += 1
        if reads[0] >= max_pages:
            # The poll would read more than max_pages pages; nothing is written.
            _fail('SYNC_CATCHUP_LIMIT')
        reads[0] += 1
        try:
            batch = github_get('issues/comments?sort=created&direction=desc'
                               f'&per_page={PAGE_SIZE}&page={page}')
        except LLMToolError:
            raise _Offline(reads[0]) from None
        if (type(batch) is not list or len(batch) > PAGE_SIZE or
                any(type(item) is not dict or not _is_int(item.get('id'), 1, _MAX_ID)
                    for item in batch)):
            raise _Offline(reads[0])
        ids = [item['id'] for item in batch]
        if any(newer <= older for newer, older in zip(ids, ids[1:])):
            # Not strictly newest first: stopping at a low id could skip a later,
            # higher one, so the response is invalid and the cursor stays.
            raise _Offline(reads[0])
        for item in batch:
            if item['id'] <= cursor:
                return fresh, page
            # A comment created during the walk shifts later pages down and repeats
            # an id; keep the first copy.
            fresh.setdefault(item['id'], item)
        if len(batch) < PAGE_SIZE:
            return fresh, page


def _read_new(github_get, cursor, max_pages):
    """Return ({id: comment} newer than cursor with no gap, pages_read).

    A one-page walk is one response and is accepted as is. A multi-page walk can
    miss a comment only when a comment it already read was deleted before a later
    page was read. That deleted comment cannot appear in a later walk, so the walk
    is accepted only when the next walk, limited to ids up to the walk's newest id,
    returns exactly the same ids. New comments above that id are left for the next
    poll. No agreement within MAX_WALKS walks is an unstable listing: OFFLINE.
    All walks share one budget of ``max_pages`` reads (SYNC_CATCHUP_LIMIT).
    """
    reads = [0]
    walk, pages = _walk(github_get, cursor, max_pages, reads)
    for _ in range(MAX_WALKS - 1):
        if pages == 1:
            return walk, reads[0]
        # A full first page newer than the cursor means the walk is not empty.
        newest = max(walk)
        check, pages = _walk(github_get, cursor, max_pages, reads)
        if {comment_id for comment_id in check if comment_id <= newest} == set(walk):
            return walk, reads[0]
        walk = check
    if pages == 1:
        return walk, reads[0]
    raise _Offline(reads[0])


def _parse_one(parse, item):
    """Return None, a refusal code, or (request_key, revision, delivery_id, kind)."""
    try:
        value = parse(item)
    except Exception:
        # A parser failure must not stall the cursor forever; count it, keep no text.
        return 'SYNC_PARSE_ERROR'
    if value is None:
        return None
    if type(value) is str:
        return value if _CODE.fullmatch(value) else 'SYNC_PARSE_INVALID'
    if (type(value) is not dict or set(value) != _PARSED_KEYS or
            not _is_text(value['request_key'], _KEY) or
            not _is_int(value['revision'], 0, _MAX_REVISION) or
            not _is_text(value['delivery_id'], _KEY) or
            not _is_text(value['kind'], _KIND)):
        return 'SYNC_PARSE_INVALID'
    return (value['request_key'], value['revision'], value['delivery_id'], value['kind'])


def _add_once(items, key):
    if key not in items:
        items.append(key)


def _result(status, previous, state, pages_read, *, new_comments=0, counts=None,
            refusals=None, new_keys=(), revised_keys=(), stale_keys=()):
    return {'status': status, 'previous_cursor': previous, 'cursor': state['cursor'],
            'pages_read': pages_read, 'new_comments': new_comments,
            'counts': dict(counts or dict.fromkeys(_COUNT_NAMES, 0)),
            'refusals': dict(sorted((refusals or {}).items())),
            'new_request_keys': list(new_keys), 'revised_request_keys': list(revised_keys),
            'stale_link_request_keys': list(stale_keys),
            'failure_count': 0, 'backoff_seconds': 0}


def poll_once(path, github_get, parse, max_pages=10):
    """Run one poll step: read new comments, update the ledger, commit once.

    At most ``max_pages`` pages are read in this call, confirmation walks included.
    The exclusive state lock is held from before the load through the write.
    """
    if (not callable(github_get) or not callable(parse) or
            not _is_int(max_pages, 1, MAX_PAGES_LIMIT)):
        _fail('SYNC_ARGUMENT_INVALID')
    parent, _ = _split_path(path)
    with _state_lock(parent, True, 'SYNC_STATE_MISSING', 'SYNC_STATE_CORRUPT'):
        return _poll_locked(path, github_get, parse, max_pages)


def _poll_locked(path, github_get, parse, max_pages):
    state, identity = _load(path)
    previous = state['cursor']
    try:
        fresh, pages_read = _read_new(github_get, previous, max_pages)
    except _Offline as offline:
        count = min(state['failure_count'] + 1, MAX_FAILURE_COUNT)
        state['failure_count'] = count
        _write(path, state, identity)
        result = _result('OFFLINE', previous, state, offline.pages_read)
        result['failure_count'] = count
        result['backoff_seconds'] = BACKOFF_SECONDS[min(count, len(BACKOFF_SECONDS)) - 1]
        return result
    if not fresh:
        if state['failure_count']:
            state['failure_count'] = 0
            _write(path, state, identity)
        return _result('IDLE', previous, state, pages_read)

    requests = state['requests']
    counts = dict.fromkeys(_COUNT_NAMES, 0)
    refusals = {}
    new_keys, revised_keys, stale_keys = [], [], []
    for comment_id in sorted(fresh):
        parsed = _parse_one(parse, fresh[comment_id])
        if parsed is None:
            counts['non_sync'] += 1
            continue
        if type(parsed) is str:
            # Every code keeps its own count. Codes are short fixed-format names,
            # never stored, and bounded by the comments read (max_pages * 100).
            refusals[parsed] = refusals.get(parsed, 0) + 1
            counts['refused'] += 1
            continue
        key, revision, delivery_id, kind = parsed
        entry = requests.get(key)
        if entry is None:
            if len(requests) >= MAX_REQUESTS:
                _fail('SYNC_LEDGER_FULL')
            requests[key] = {'state': 'PENDING_PM', 'kind': kind, 'revision': revision,
                             'delivery_id': delivery_id, 'first_comment_id': comment_id,
                             'comment_id': comment_id, 'task_id': None,
                             'linked_revision': None}
            counts['pending_pm'] += 1
            _add_once(new_keys, key)
        elif delivery_id == entry['delivery_id']:
            counts['inert'] += 1
        elif revision <= entry['revision']:
            counts['duplicate'] += 1
        else:
            entry.update(kind=kind, revision=revision, delivery_id=delivery_id,
                         comment_id=comment_id)
            counts['revision_pending'] += 1
            _add_once(revised_keys, key)
            if entry['task_id'] is not None:
                # Never relink: the PM decides what the linked Task does next.
                entry['state'] = 'STALE_LINK'
                counts['stale_link'] += 1
                _add_once(stale_keys, key)
    state['cursor'] = max(fresh)
    state['failure_count'] = 0
    _write(path, state, identity)
    return _result('SYNCED', previous, state, pages_read, new_comments=len(fresh),
                   counts=counts, refusals=refusals, new_keys=new_keys,
                   revised_keys=revised_keys, stale_keys=stale_keys)


def link_task(path, request_key, task_id):
    """Bind an unlinked request to one 32-hex Task id, once."""
    if not _is_text(request_key, _KEY) or not _is_text(task_id, _TASK_ID):
        _fail('SYNC_ARGUMENT_INVALID')
    parent, _ = _split_path(path)
    with _state_lock(parent, True, 'SYNC_STATE_MISSING', 'SYNC_STATE_CORRUPT'):
        return _link_locked(path, request_key, task_id)


def _link_locked(path, request_key, task_id):
    state, identity = _load(path)
    entry = state['requests'].get(request_key)
    if entry is None:
        _fail('SYNC_REQUEST_UNKNOWN')
    if entry['task_id'] == task_id:
        return {'status': 'INERT', 'request_key': request_key, 'task_id': task_id,
                'state': entry['state']}
    if entry['task_id'] is not None:
        _fail('SYNC_REQUEST_ALREADY_LINKED')
    entry.update(state='LINKED', task_id=task_id, linked_revision=entry['revision'])
    _write(path, state, identity)
    return {'status': 'LINKED', 'request_key': request_key, 'task_id': task_id,
            'state': 'LINKED'}


def pending(path):
    """List unlinked requests, oldest first, without writing (shared lock)."""
    parent, _ = _split_path(path)
    with _state_lock(parent, False, 'SYNC_STATE_MISSING', 'SYNC_STATE_CORRUPT'):
        state, _ = _load(path)
    items = [{'request_key': key, 'kind': entry['kind'], 'revision': entry['revision'],
              'first_comment_id': entry['first_comment_id'],
              'comment_id': entry['comment_id']}
             for key, entry in state['requests'].items() if entry['task_id'] is None]
    items.sort(key=lambda item: (item['first_comment_id'], item['request_key']))
    return {'cursor': state['cursor'], 'requests': items}
