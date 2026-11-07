"""Owner-only merge-train records beside the TaskStore root (#1182 T4a).

One JSON record per merge train lives in ``<TaskStore root parent>/merge-trains``.
A record is created once with an atomic hard link, read with schema and digest
checks, and changed only by compare-and-update under an exclusive lock with the
allowed state moves. Every doubt fails closed with a typed code; error messages
carry only the code and, when it is a safe identifier, the train_id.
"""

import copy
import fcntl
import hashlib
import json
import os
import re
import stat
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import task_store
from .errors import LLMToolError

SCHEMA_VERSION = 1
DIRECTORY_NAME = 'merge-trains'
MAX_RECORD_BYTES = 256 * 1024
READ_ATTEMPTS = 50
LOCK_TIMEOUT_SECONDS = 10.0
POLL_SECONDS = 0.01

TRAIN_RECORD_INVALID = 'TRAIN_RECORD_INVALID'
TRAIN_RECORD_UNAVAILABLE = 'TRAIN_RECORD_UNAVAILABLE'
TRAIN_RECORD_EXISTS = 'TRAIN_RECORD_EXISTS'
TRAIN_RECORD_CONFLICT = 'TRAIN_RECORD_CONFLICT'
TRAIN_RECORD_IMMUTABLE = 'TRAIN_RECORD_IMMUTABLE'
TRAIN_RECORD_AMBIGUOUS = 'TRAIN_RECORD_AMBIGUOUS'
TRAIN_RECORD_BUSY = 'TRAIN_RECORD_BUSY'
TRAIN_STATE_INVALID = 'TRAIN_STATE_INVALID'

FIELDS = frozenset((
    'schema_version', 'train_id', 'main_sha', 'members', 'excluded', 'train_branch',
    'train_head', 'train_pr', 'state', 'merge_attempt', 'merge_sha', 'created_at',
    'record_sha256'))
MUTABLE_FIELDS = frozenset(('state', 'train_pr', 'merge_attempt', 'merge_sha'))
MEMBER_FIELDS = frozenset((
    'task_id', 'pr', 'head', 'executor_task', 'review_version', 'linux_check_run_id',
    'macos_check_run_id', 'remote_test_receipt_digest'))
EXCLUDED_FIELDS = frozenset(('task_id', 'pr', 'code'))
MERGE_ATTEMPT_FIELDS = frozenset(('pr', 'head_sha', 'state', 'claimed_at'))
MERGE_ATTEMPT_STATES = frozenset(('CLAIMED', 'UNKNOWN', 'MERGED'))

STATES = frozenset((
    'BUILT', 'MERGE_CLAIMED', 'UNKNOWN', 'MERGED', 'SUPERSEDED', 'CLOSED'))
FINAL_STATES = frozenset(('SUPERSEDED', 'CLOSED'))
STATE_MOVES = {
    'BUILT': frozenset(('MERGE_CLAIMED', 'SUPERSEDED')),
    'MERGE_CLAIMED': frozenset(('MERGED', 'UNKNOWN')),
    'UNKNOWN': frozenset(('MERGE_CLAIMED', 'MERGED', 'SUPERSEDED')),
    'MERGED': frozenset(('CLOSED',)),
    'SUPERSEDED': frozenset(),
    'CLOSED': frozenset(),
}

MIN_MEMBERS = 2
MAX_MEMBERS = 64
MAX_EXCLUDED = 64
MAX_PR = 9999999

TRAIN_ID = re.compile(r'[a-z0-9][a-z0-9-]{0,63}')
_RECORD_NAME = re.compile(r'([a-z0-9][a-z0-9-]{0,63})\.json')
_SHA40 = re.compile(r'[0-9a-f]{40}')
_SHA256 = re.compile(r'[0-9a-f]{64}')
_TASK_ID = re.compile(r'[0-9a-f]{32}')
_SHORT_HEX = re.compile(r'[0-9a-f]{8,64}')
_CODE = re.compile(r'[A-Z][A-Z0-9_]{0,63}')
_EXECUTOR_TASK = task_store.D6_TASK_ID
_BRANCH_PREFIX = 'llmtool/train-'


def _safe_train_id(value):
    return type(value) is str and TRAIN_ID.fullmatch(value) is not None


def _refuse(code, train_id=None):
    """Raise the typed refusal; the message is the code plus a safe train_id only."""
    message = code
    if _safe_train_id(train_id):
        message = code + ': train ' + train_id
    error = LLMToolError(message)
    error.merge_train_code = code
    raise error from None


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def record_digest(record):
    """SHA-256 of the canonical JSON of ``record`` without ``record_sha256``."""
    body = {key: item for key, item in record.items() if key != 'record_sha256'}
    return hashlib.sha256(_canonical(body)).hexdigest()


def _hex(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def _pr(value):
    return type(value) is int and 1 <= value <= MAX_PR


def _optional_run_id(value):
    return value is None or (type(value) is int and value >= 1)


def _utc_iso(value):
    if type(value) is not str or not 0 < len(value) <= 64 or not value.isascii():
        return False
    text = value[:-1] + '+00:00' if value.endswith('Z') else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)


def _claimed_at(value):
    return (type(value) is str and 0 < len(value) <= 64 and
            not any(ord(char) < 0x20 or 0x7f <= ord(char) <= 0x9f for char in value))


def _valid_member(member):
    return (type(member) is dict and set(member) == MEMBER_FIELDS and
            _hex(member['task_id'], _TASK_ID) and
            _pr(member['pr']) and
            _hex(member['head'], _SHA40) and
            type(member['executor_task']) is str and
            _EXECUTOR_TASK.fullmatch(member['executor_task']) is not None and
            type(member['review_version']) is int and member['review_version'] >= 1 and
            _optional_run_id(member['linux_check_run_id']) and
            _optional_run_id(member['macos_check_run_id']) and
            _hex(member['remote_test_receipt_digest'], _SHORT_HEX))


def _valid_excluded(item):
    return (type(item) is dict and set(item) == EXCLUDED_FIELDS and
            _hex(item['task_id'], _TASK_ID) and
            _pr(item['pr']) and
            _hex(item['code'], _CODE))


def _valid_merge_attempt(attempt):
    return attempt is None or (
        type(attempt) is dict and set(attempt) == MERGE_ATTEMPT_FIELDS and
        _pr(attempt['pr']) and
        _hex(attempt['head_sha'], _SHA40) and
        type(attempt['state']) is str and attempt['state'] in MERGE_ATTEMPT_STATES and
        _claimed_at(attempt['claimed_at']))


def _merged_shape(record):
    attempt = record['merge_attempt']
    return (_hex(record['merge_sha'], _SHA40) and
            type(attempt) is dict and
            attempt['state'] == 'MERGED' and
            record['train_pr'] is not None and
            attempt['pr'] == record['train_pr'] and
            attempt['head_sha'] == record['train_head'])


def _valid_body(record):
    """Check every field except ``record_sha256``; return False on any doubt."""
    if type(record) is not dict or set(record) != FIELDS - {'record_sha256'}:
        return False
    if (type(record['schema_version']) is not int or
            record['schema_version'] != SCHEMA_VERSION or
            not _safe_train_id(record['train_id']) or
            not _hex(record['main_sha'], _SHA40) or
            not _hex(record['train_head'], _SHA40) or
            type(record['train_branch']) is not str or
            record['train_branch'] != _BRANCH_PREFIX + record['train_id'] or
            not (record['train_pr'] is None or _pr(record['train_pr'])) or
            not (record['merge_sha'] is None or _hex(record['merge_sha'], _SHA40)) or
            not _utc_iso(record['created_at']) or
            type(record['state']) is not str or record['state'] not in STATES or
            not _valid_merge_attempt(record['merge_attempt'])):
        return False
    members = record['members']
    if (type(members) is not list or not MIN_MEMBERS <= len(members) <= MAX_MEMBERS or
            not all(_valid_member(member) for member in members)):
        return False
    for key in ('task_id', 'pr', 'head'):
        if len({member[key] for member in members}) != len(members):
            return False
    excluded = record['excluded']
    if (type(excluded) is not list or len(excluded) > MAX_EXCLUDED or
            not all(_valid_excluded(item) for item in excluded)):
        return False
    excluded_ids = {item['task_id'] for item in excluded}
    if (len(excluded_ids) != len(excluded) or
            excluded_ids & {member['task_id'] for member in members}):
        return False
    if record['state'] == 'BUILT' and (record['merge_attempt'] is not None or
                                       record['merge_sha'] is not None):
        return False
    # CLOSED is reachable only from MERGED and keeps the merged evidence.
    if record['state'] in ('MERGED', 'CLOSED') and not _merged_shape(record):
        return False
    return True


def _validate(record, train_id=None):
    """Return ``record`` when its shape and digest are valid, else refuse INVALID."""
    if (type(record) is not dict or set(record) != FIELDS or
            not _hex(record.get('record_sha256'), _SHA256)):
        _refuse(TRAIN_RECORD_INVALID, train_id)
    body = {key: item for key, item in record.items() if key != 'record_sha256'}
    if not _valid_body(body):
        _refuse(TRAIN_RECORD_INVALID, train_id)
    try:
        digest = record_digest(record)
    except (TypeError, ValueError):
        _refuse(TRAIN_RECORD_INVALID, train_id)
    if digest != record['record_sha256']:
        _refuse(TRAIN_RECORD_INVALID, train_id)
    return record


def _sealed(body, train_id):
    """Return a detached, validated record with ``record_sha256`` set."""
    if not _valid_body(body):
        _refuse(TRAIN_RECORD_INVALID, train_id)
    try:
        record = json.loads(_canonical(body).decode('utf-8'))
    except (TypeError, ValueError):
        _refuse(TRAIN_RECORD_INVALID, train_id)
    record['record_sha256'] = record_digest(record)
    return _validate(record, train_id)


def _directory_path(root):
    base = Path(task_store.default_root() if root is None else root).absolute()
    return base.parent / DIRECTORY_NAME


def _open_directory(root):
    """Create or open the owner-only record directory; a symlink is refused."""
    if os.name != 'posix' or not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY'):
        _refuse(TRAIN_RECORD_UNAVAILABLE)
    directory = _directory_path(root)
    try:
        directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        _refuse(TRAIN_RECORD_UNAVAILABLE)
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError:
        _refuse(TRAIN_RECORD_UNAVAILABLE)
    try:
        named = os.lstat(directory)
    except OSError:
        _refuse(TRAIN_RECORD_UNAVAILABLE)
    if (stat.S_ISLNK(named.st_mode) or not stat.S_ISDIR(named.st_mode) or
            named.st_uid != os.getuid()):
        _refuse(TRAIN_RECORD_INVALID)
    try:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        _refuse(TRAIN_RECORD_INVALID)
    try:
        opened = os.fstat(fd)
        if (not stat.S_ISDIR(opened.st_mode) or opened.st_uid != os.getuid() or
                (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
            _refuse(TRAIN_RECORD_INVALID)
        if stat.S_IMODE(opened.st_mode) != 0o700:
            os.fchmod(fd, 0o700)
            if stat.S_IMODE(os.fstat(fd).st_mode) != 0o700:
                _refuse(TRAIN_RECORD_UNAVAILABLE)
    except LLMToolError:
        os.close(fd)
        raise
    except OSError:
        os.close(fd)
        _refuse(TRAIN_RECORD_UNAVAILABLE)
    return fd


def _record_name(train_id):
    if not _safe_train_id(train_id):
        _refuse(TRAIN_RECORD_INVALID)
    return train_id + '.json'


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _reject_constant(_):
    raise ValueError('non-finite number')


def _read_at(directory, train_id):
    """Read and validate one record through the directory descriptor."""
    name = _record_name(train_id)
    for _ in range(READ_ATTEMPTS):
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            _refuse(TRAIN_RECORD_UNAVAILABLE, train_id)
        except OSError:
            _refuse(TRAIN_RECORD_INVALID, train_id)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                    stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > MAX_RECORD_BYTES):
                _refuse(TRAIN_RECORD_INVALID, train_id)
            if info.st_nlink != 1:
                # A replace unlinked this inode, or a create is between its
                # link and temporary unlink; a planted extra link persists.
                time.sleep(POLL_SECONDS)
                continue
            chunks, remaining = [], MAX_RECORD_BYTES + 1
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        except LLMToolError:
            raise
        except OSError:
            _refuse(TRAIN_RECORD_INVALID, train_id)
        finally:
            os.close(fd)
        raw = b''.join(chunks)
        if len(raw) > MAX_RECORD_BYTES:
            _refuse(TRAIN_RECORD_INVALID, train_id)
        try:
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs,
                               parse_constant=_reject_constant)
        except (UnicodeError, ValueError, RecursionError):
            _refuse(TRAIN_RECORD_INVALID, train_id)
        _validate(value, train_id)
        if value['train_id'] != train_id:
            _refuse(TRAIN_RECORD_INVALID, train_id)
        return value
    _refuse(TRAIN_RECORD_INVALID, train_id)


def _write_temporary(directory, train_id, record):
    """Write the fsynced owner-only temporary; return its name."""
    raw = _canonical(record) + b'\n'
    if len(raw) > MAX_RECORD_BYTES:
        _refuse(TRAIN_RECORD_INVALID, train_id)
    temporary = '.' + train_id + '.' + uuid.uuid4().hex + '.tmp'
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                 dir_fd=directory)
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    return temporary


def create(record, root=None):
    """Validate and write one new BUILT record exactly once; return it."""
    if type(record) is not dict:
        _refuse(TRAIN_RECORD_INVALID)
    train_id = record.get('train_id')
    body = {key: item for key, item in record.items() if key != 'record_sha256'}
    if set(body) != FIELDS - {'record_sha256'}:
        _refuse(TRAIN_RECORD_INVALID, train_id)
    # Shape first: an unknown state is INVALID; a valid non-BUILT state is a bad move.
    sealed = _sealed(body, train_id)
    if sealed['state'] != 'BUILT':
        _refuse(TRAIN_STATE_INVALID, train_id)
    name = _record_name(sealed['train_id'])
    directory = _open_directory(root)
    temporary = None
    try:
        temporary = _write_temporary(directory, train_id, sealed)
        # A hard link is atomic and fails when the name already exists.
        os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory,
                follow_symlinks=False)
        os.unlink(temporary, dir_fd=directory)
        temporary = None
        os.fsync(directory)
    except FileExistsError:
        _refuse(TRAIN_RECORD_EXISTS, train_id)
    except LLMToolError:
        raise
    except OSError:
        _refuse(TRAIN_RECORD_UNAVAILABLE, train_id)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary, dir_fd=directory)
            except OSError:
                pass
        os.close(directory)
    return copy.deepcopy(sealed)


def get(train_id, root=None):
    """Return the validated record for ``train_id``."""
    _record_name(train_id)
    directory = _open_directory(root)
    try:
        return _read_at(directory, train_id)
    finally:
        os.close(directory)


def _acquire_lock(directory, train_id):
    lock = '.' + train_id + '.lock'
    try:
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600,
                     dir_fd=directory)
    except OSError:
        _refuse(TRAIN_RECORD_INVALID, train_id)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                info.st_nlink != 1):
            _refuse(TRAIN_RECORD_INVALID, train_id)
        if stat.S_IMODE(info.st_mode) != 0o600:
            os.fchmod(fd, 0o600)
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    _refuse(TRAIN_RECORD_BUSY, train_id)
                time.sleep(POLL_SECONDS)
    except LLMToolError:
        os.close(fd)
        raise
    except OSError:
        os.close(fd)
        _refuse(TRAIN_RECORD_UNAVAILABLE, train_id)


def compare_and_update(train_id, expected_sha256, root=None, **changes):
    """Apply ``changes`` when the stored digest equals ``expected_sha256``."""
    _record_name(train_id)
    if set(changes) - MUTABLE_FIELDS:
        _refuse(TRAIN_RECORD_IMMUTABLE, train_id)
    directory = _open_directory(root)
    try:
        lock_fd = _acquire_lock(directory, train_id)
        try:
            current = _read_at(directory, train_id)
            if (type(expected_sha256) is not str or
                    current['record_sha256'] != expected_sha256):
                _refuse(TRAIN_RECORD_CONFLICT, train_id)
            old_state = current['state']
            if old_state in FINAL_STATES:
                _refuse(TRAIN_STATE_INVALID, train_id)
            new_state = changes.get('state', old_state)
            if new_state != old_state and (
                    type(new_state) is not str or
                    new_state not in STATE_MOVES[old_state]):
                _refuse(TRAIN_STATE_INVALID, train_id)
            body = {key: item for key, item in current.items() if key != 'record_sha256'}
            body.update(changes)
            updated = _sealed(body, train_id)
            name = _record_name(train_id)
            temporary = None
            try:
                temporary = _write_temporary(directory, train_id, updated)
                os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                temporary = None
                os.fsync(directory)
            except OSError:
                _refuse(TRAIN_RECORD_UNAVAILABLE, train_id)
            finally:
                if temporary is not None:
                    try:
                        os.unlink(temporary, dir_fd=directory)
                    except OSError:
                        pass
            return copy.deepcopy(updated)
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
    finally:
        os.close(directory)


def find_by_pr(train_pr, root=None):
    """Return the one record whose train_pr matches, or None; refuse ambiguity."""
    if not _pr(train_pr):
        _refuse(TRAIN_RECORD_INVALID)
    directory = _open_directory(root)
    try:
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            _refuse(TRAIN_RECORD_UNAVAILABLE)
        found = []
        for name in names:
            if name.startswith('.'):
                continue  # lock files and in-flight temporaries
            match = _RECORD_NAME.fullmatch(name)
            if match is None:
                _refuse(TRAIN_RECORD_INVALID)
            train_id = match.group(1)
            try:
                record = _read_at(directory, train_id)
            except LLMToolError:
                _refuse(TRAIN_RECORD_INVALID, train_id)
            if record['train_pr'] == train_pr:
                found.append(record)
        if len(found) > 1:
            _refuse(TRAIN_RECORD_AMBIGUOUS)
        return found[0] if found else None
    finally:
        os.close(directory)


def list_records(root=None):
    """Return every record, validated as ``get`` does, sorted by train_id.

    Read-only: a missing directory returns [] and is not created. Any
    unreadable or invalid record, or an unsafe directory, refuses INVALID.
    """
    if os.name != 'posix' or not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY'):
        _refuse(TRAIN_RECORD_UNAVAILABLE)
    path = _directory_path(root)
    try:
        named = os.lstat(path)
    except FileNotFoundError:
        return []
    except OSError:
        _refuse(TRAIN_RECORD_UNAVAILABLE)
    if (stat.S_ISLNK(named.st_mode) or not stat.S_ISDIR(named.st_mode) or
            named.st_uid != os.getuid()):
        _refuse(TRAIN_RECORD_INVALID)
    try:
        directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        _refuse(TRAIN_RECORD_INVALID)
    try:
        try:
            opened = os.fstat(directory)
            names = sorted(os.listdir(directory))
        except OSError:
            _refuse(TRAIN_RECORD_UNAVAILABLE)
        if (not stat.S_ISDIR(opened.st_mode) or opened.st_uid != os.getuid() or
                (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino) or
                stat.S_IMODE(opened.st_mode) != 0o700):
            _refuse(TRAIN_RECORD_INVALID)
        records = []
        for name in names:
            if name.startswith('.'):
                continue  # lock files and in-flight temporaries
            match = _RECORD_NAME.fullmatch(name)
            if match is None:
                _refuse(TRAIN_RECORD_INVALID)
            train_id = match.group(1)
            try:
                records.append(_read_at(directory, train_id))
            except LLMToolError:
                _refuse(TRAIN_RECORD_INVALID, train_id)
        return sorted(records, key=lambda record: record['train_id'])
    finally:
        os.close(directory)
