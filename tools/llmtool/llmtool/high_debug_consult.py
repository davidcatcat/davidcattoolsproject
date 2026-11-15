"""Private, metadata-only O6 advice ledger for no-PR high-debug consults (#1943 WP-5).

Owner O6 (#1943 c6879415056): design advice at most once per Work Package and
at most 10 times per ISO week (UTC). The ledger sits beside the high-debug
screen records (``high_debug._screen_location``) and holds one slot file per
Work Package and one per used week slot:

    <state root>/high-debug-consult/wp-<wp>.json
    <state root>/high-debug-consult/week-<YYYY>-W<ww>-<n>.json    n = 1..10

``reserve`` creates the WP slot, then one week slot, each exactly once by an
atomic hard link (the technique of ``high_debug._create_private``), in state
RESERVED and before any launch. ``finish`` moves a reservation to DONE or
FAILED with the report digest. Slots are never deleted, so a crash fails
closed. Records hold metadata only: no question, evidence or report text and
no path. Design: docs/design/wp5-no-pr-high-debug-entry-1943.md §3.7-3.8.
Nothing calls this module yet (WP-5 PR1).

Confinement (security repair 1, formal finding F1): a root must be absolute
with no empty, ``.`` or ``..`` component, judged on the text as given and
never after abspath, normpath or realpath. The state root (the Task store
root's parent) is then reached from ``/`` one component at a time, each one
lstat'ed and opened with ``O_NOFOLLOW`` and ``O_DIRECTORY``, so every
component must exist and none may be a symlink; it must be owned by the
user and not group- or world-writable. A supplied Task store root is opened
the same way and must be owned by the user and exactly 0700. Every later step
(create, link, replace, read) works relative to the opened ledger directory
descriptor, so a path swapped after the check is never followed.

Hard links: an existing slot must hold exactly one name, else it refuses
LEDGER_INVALID. Create-once briefly holds its own temporary second name
(``.consult-*`` in the ledger) and checks for a single name after removing
it. A reader that meets a second name waits a bounded time only when that
name is such an in-ledger temporary on the same inode; any other second name
is a planted hard link and refuses at once.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time
from typing import Optional

from .errors import LLMToolError

WP_USED = 'HIGH_DEBUG_CONSULT_WP_USED'
WEEK_CAP = 'HIGH_DEBUG_CONSULT_WEEK_CAP'
LEDGER_INVALID = 'HIGH_DEBUG_CONSULT_LEDGER_INVALID'
ARGUMENT_INVALID = 'HIGH_DEBUG_CONSULT_ARGUMENT_INVALID'

RESERVED = 'RESERVED'
DONE = 'DONE'
FAILED = 'FAILED'
WEEK_LIMIT = 10
LEDGER_DIR = 'high-debug-consult'

_STATES = (RESERVED, DONE, FAILED)
_WP = re.compile(r'[A-Za-z0-9._-]{1,64}\Z')
_WEEK = re.compile(r'[0-9]{4}-W[0-9]{2}\Z')
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')
_RECORD_LIMIT = 4096
_WP_SCHEMA = 'high-debug-consult-wp.v1'
_WEEK_SCHEMA = 'high-debug-consult-week.v1'
_WP_KEYS = frozenset(('schema', 'wp', 'state', 'week', 'week_slot', 'reserved_at',
                      'finished_at', 'report_sha256'))
_WEEK_KEYS = frozenset(('schema', 'week', 'slot', 'wp', 'state', 'reserved_at',
                        'finished_at', 'report_sha256'))
_TEMP_PREFIX = '.consult-'
# Bounded wait (about one second) for another caller's create-once or replace
# to finish; an in-ledger temporary name that outlasts it refuses too.
_SETTLE_TRIES = 40
_SETTLE_DELAY = 0.025

_NOFOLLOW = getattr(os, 'O_NOFOLLOW', 0)
_DIRECTORY = getattr(os, 'O_DIRECTORY', 0)
_CLOEXEC = getattr(os, 'O_CLOEXEC', 0)
_NONBLOCK = getattr(os, 'O_NONBLOCK', 0)
# Ancestors are only walked through, so O_PATH (Linux) avoids needing read
# permission on them; elsewhere it is a plain read-only directory open.
_WALK_FLAGS = getattr(os, 'O_PATH', os.O_RDONLY) | _DIRECTORY | _NOFOLLOW | _CLOEXEC
_LEDGER_FLAGS = os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC


class _Settling(Exception):
    """A slot briefly holds two names (create-once) or none (an open replaced inode)."""


@dataclass(frozen=True)
class Reservation:
    """Handle for one RESERVED advice slot pair; pass it to ``finish``.

    ``root`` is the absolute Task store root given to ``reserve`` (None for the
    default store); ``finish`` derives the ledger from it again under the same
    confinement and requires it to equal ``directory``.
    """
    wp: str
    week: str
    week_slot: int
    directory: Path
    root: Optional[Path]


def _refuse(code, detail=None):
    """Raise the coded refusal; ``detail`` is metadata only (states, digests, counts)."""
    message = 'High-debug consult refused: ' + code
    if detail is not None:
        message += ' (' + detail + ')'
    error = LLMToolError(message)
    error.high_debug_code = code
    if detail is not None:
        error.high_debug_detail = detail
    raise error from None


def _root_text(value):
    """The text of a root argument; anything that is not a usable path refuses ARGUMENT_INVALID."""
    try:
        text = os.fspath(value)
    except TypeError:
        _refuse(ARGUMENT_INVALID, 'root must be a path')
    if type(text) is not str or not text or '\x00' in text:
        _refuse(ARGUMENT_INVALID, 'root must be a path')
    return text


def _clean_absolute(text):
    """True when ``text`` is absolute and has no empty, ``.`` or ``..`` component.

    Judged on the text exactly as given. A root is never passed through
    abspath, normpath or realpath first: a lexical ``..`` would hide a symlink
    (``base/link/../tasks``) or a missing directory (``base/missing/../tasks``)
    from the component walk, since the kernel resolves ``..`` after ``link``.
    """
    if not text.startswith('/'):
        return False
    return all(part not in ('', '.', '..') for part in text[1:].split('/'))


def _checked_root(value):
    """A Task store root as an absolute Path, shape-checked before any normalisation.

    A non-path refuses ARGUMENT_INVALID; a relative path or one with an empty,
    ``.`` or ``..`` component refuses LEDGER_INVALID. The text is then used
    unchanged, so the component walk sees every name the kernel would.
    """
    text = _root_text(value)
    if not _clean_absolute(text):
        _refuse(LEDGER_INVALID, 'root must be absolute with no empty, . or .. component')
    return Path(text)


def _platform_ready():
    """True when every descriptor-relative call the ledger needs is available."""
    needed = (os.open, os.mkdir, os.link, os.rename, os.unlink, os.stat)
    return bool(_NOFOLLOW and _DIRECTORY and
                all(call in os.supports_dir_fd for call in needed) and
                os.stat in os.supports_follow_symlinks and
                os.listdir in os.supports_fd)


# Taken once at import, against the real os functions.
_PLATFORM_READY = _platform_ready()


def _open_walk(name, dir_fd):
    """lstat, then open, one directory component, never through a symlink; returns (fd, fstat).

    The component must exist and be a real directory (not a symlink) by
    lstat relative to its opened parent; the opened descriptor must be that
    same directory.
    """
    try:
        seen = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        _refuse(LEDGER_INVALID, 'path component missing')
    if not stat.S_ISDIR(seen.st_mode):
        _refuse(LEDGER_INVALID, 'path component not a real directory')
    try:
        fd = os.open(name, _WALK_FLAGS, dir_fd=dir_fd)
    except OSError:
        _refuse(LEDGER_INVALID, 'path component not a real directory')
    try:
        info = os.fstat(fd)
    except OSError:
        os.close(fd)
        _refuse(LEDGER_INVALID, 'path component unusable')
    if (not stat.S_ISDIR(info.st_mode) or info.st_ino != seen.st_ino or
            info.st_dev != seen.st_dev):
        os.close(fd)
        _refuse(LEDGER_INVALID, 'path component not a real directory')
    return fd, info


def _open_chain(path):
    """Open absolute ``path`` from the anchor down, one non-symlink directory at a time."""
    fd, info = _open_walk(path.anchor, None)
    for part in path.parts[1:]:
        try:
            child, info = _open_walk(part, fd)
        finally:
            os.close(fd)
        fd = child
    return fd, info


def _confine(store):
    """Open and check the state root for Task store ``store`` (None: default store).

    Returns (state root path, open state root fd); the caller closes the fd.
    """
    if not _PLATFORM_READY:
        _refuse(LEDGER_INVALID, 'platform cannot confine the ledger')
    supplied = store is not None
    if supplied:
        store = _checked_root(store)
    else:
        from . import task_store
        store = _checked_root(task_store.default_root())
    if not store.name:
        _refuse(LEDGER_INVALID, 'Task store root unusable')
    state = store.parent
    state_fd, info = _open_chain(state)
    try:
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
            _refuse(LEDGER_INVALID, 'state root not private')
        if supplied:
            store_fd, store_info = _open_walk(store.name, state_fd)
            os.close(store_fd)
            if (store_info.st_uid != os.getuid() or
                    stat.S_IMODE(store_info.st_mode) != 0o700):
                _refuse(LEDGER_INVALID, 'Task store root not private')
    except BaseException:
        os.close(state_fd)
        raise
    return state, state_fd


def ledger_directory(root=None):
    """The ledger directory beside Task store ``root``; None means the default store.

    The ledger sits in the private llmtool state root that holds high_debug's
    ``high-debug-screen`` records (the Task store root's parent). A root (the
    supplied one or the default) must be absolute with no empty, ``.`` or
    ``..`` component, judged before any normalisation. A supplied ``root``
    must be an existing real directory, owned by the user, exactly 0700 and
    with no symlink in any component of its path. In every case the
    state root must be symlink-free, owned by the user and not group- or
    world-writable. Anything else refuses LEDGER_INVALID. Nothing is created.
    """
    state, state_fd = _confine(root)
    os.close(state_fd)
    return state / LEDGER_DIR


def _open_ledger(state_fd, *, create):
    """Create (when ``create``) and open the owner-only, non-symlink ledger directory."""
    created = False
    if create:
        try:
            os.mkdir(LEDGER_DIR, 0o700, dir_fd=state_fd)
            created = True
        except FileExistsError:
            pass
        except OSError:
            _refuse(LEDGER_INVALID, 'ledger directory unusable')
    try:
        fd = os.open(LEDGER_DIR, _LEDGER_FLAGS, dir_fd=state_fd)
    except OSError:
        _refuse(LEDGER_INVALID, 'ledger directory unusable')
    try:
        if created:
            os.fchmod(fd, 0o700)
        info = os.fstat(fd)
    except OSError:
        os.close(fd)
        _refuse(LEDGER_INVALID, 'ledger directory unusable')
    if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or
            info.st_uid != os.getuid()):
        os.close(fd)
        _refuse(LEDGER_INVALID, 'ledger directory not private')
    return fd


@contextmanager
def _ledger(store, *, create):
    """Yield (ledger path, ledger fd) after every confinement check; closes the fd."""
    state, state_fd = _confine(store)
    try:
        ledger_fd = _open_ledger(state_fd, create=create)
    finally:
        os.close(state_fd)
    try:
        yield state / LEDGER_DIR, ledger_fd
    finally:
        os.close(ledger_fd)


def iso_week(moment):
    """ISO week label ``YYYY-Www`` of an aware datetime, taken in UTC."""
    year, week, _ = moment.astimezone(timezone.utc).isocalendar()
    return '%04d-W%02d' % (year, week)


def _utc(now):
    if now is None:
        return datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        _refuse(ARGUMENT_INVALID, 'now must be an aware datetime')
    return now.astimezone(timezone.utc)


def _valid_wp(wp):
    return type(wp) is str and _WP.fullmatch(wp) is not None


def _valid_slot_number(value):
    return type(value) is int and 1 <= value <= WEEK_LIMIT


def _valid_time(value):
    if type(value) is not str:
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)


def _wp_name(wp):
    return 'wp-' + wp + '.json'


def _week_name(week, n):
    return 'week-%s-%d.json' % (week, n)


def _private_shape(info):
    """Regular, 0600, owned by the user and bounded; the link count is checked apart."""
    return (stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and
            info.st_uid == os.getuid() and info.st_size <= _RECORD_LIMIT)


def _private_file(info):
    """An existing slot: private, regular, bounded and with exactly one name.

    A second name is a hard link that aliases the slot outside the ledger's
    control, so it is never accepted as a settled slot.
    """
    return _private_shape(info) and info.st_nlink == 1


def _in_flight(dfd, name, info):
    """True when the slot's second name may be another caller's create-once temporary.

    Only a ``.consult-`` name inside the ledger on the same inode counts. If
    none is found but the slot has meanwhile settled to one name (or was
    atomically replaced), the temporary was just removed. Any other second
    name is a planted hard link.
    """
    try:
        for entry in os.listdir(dfd):
            if not entry.startswith(_TEMP_PREFIX):
                continue
            try:
                other = os.stat(entry, dir_fd=dfd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if other.st_ino == info.st_ino and other.st_dev == info.st_dev:
                return True
        again = os.stat(name, dir_fd=dfd, follow_symlinks=False)
    except OSError:
        return False
    return again.st_ino != info.st_ino or again.st_nlink == 1


def _check_slot_file(dfd, name, info):
    """Accept a settled slot; signal a transient link count; refuse anything else."""
    if _private_file(info):
        return
    if _private_shape(info):
        # No name: an opened inode that an atomic replace just swapped out.
        if info.st_nlink == 0:
            raise _Settling()
        # Two names: only another caller's in-ledger create-once temporary.
        if info.st_nlink == 2 and _in_flight(dfd, name, info):
            raise _Settling()
    _refuse(LEDGER_INVALID, 'slot not a private single-link regular file')


def _settle_pause():
    time.sleep(_SETTLE_DELAY)


def _read_slot(dfd, name):
    """Return one slot's parsed JSON, or None when the name does not exist.

    A slot whose in-ledger temporary second name outlasts the bounded wait
    refuses LEDGER_INVALID.
    """
    for _ in range(_SETTLE_TRIES):
        try:
            return _read_slot_once(dfd, name)
        except _Settling:
            _settle_pause()
    _refuse(LEDGER_INVALID, 'slot did not settle to one link')


def _read_slot_once(dfd, name):
    try:
        info = os.stat(name, dir_fd=dfd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError:
        _refuse(LEDGER_INVALID, 'slot unreadable')
    _check_slot_file(dfd, name, info)
    try:
        fd = os.open(name, os.O_RDONLY | _NOFOLLOW | _NONBLOCK | _CLOEXEC, dir_fd=dfd)
    except OSError:
        _refuse(LEDGER_INVALID, 'slot unreadable')
    try:
        # The inode may differ from the stat one: the owner's atomic replace
        # can land in between. O_NOFOLLOW and these checks still bind the file.
        _check_slot_file(dfd, name, os.fstat(fd))
        chunks, remaining = [], _RECORD_LIMIT + 1
        while remaining:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError:
        _refuse(LEDGER_INVALID, 'slot unreadable')
    finally:
        os.close(fd)
    raw = b''.join(chunks)
    if len(raw) > _RECORD_LIMIT:
        _refuse(LEDGER_INVALID, 'slot too large')
    try:
        return json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        _refuse(LEDGER_INVALID, 'slot not JSON')


def _valid_tail(record):
    """State, timestamps and digest shared by both slot kinds."""
    state = record['state']
    digest = record['report_sha256']
    if state not in _STATES or not _valid_time(record['reserved_at']):
        return False
    if digest is not None and (type(digest) is not str or _DIGEST.fullmatch(digest) is None):
        return False
    if state == RESERVED:
        return record['finished_at'] is None and digest is None
    if not _valid_time(record['finished_at']):
        return False
    return state != DONE or digest is not None


def _check_wp_record(record, wp):
    if (type(record) is not dict or set(record) != _WP_KEYS or
            record['schema'] != _WP_SCHEMA or type(record['wp']) is not str or
            record['wp'] != wp or type(record['week']) is not str or
            _WEEK.fullmatch(record['week']) is None or
            not (record['week_slot'] is None or _valid_slot_number(record['week_slot'])) or
            not _valid_tail(record)):
        _refuse(LEDGER_INVALID, 'WP slot record invalid')
    return record


def _check_week_record(record, week, n):
    if (type(record) is not dict or set(record) != _WEEK_KEYS or
            record['schema'] != _WEEK_SCHEMA or type(record['week']) is not str or
            record['week'] != week or not _valid_slot_number(record['slot']) or
            record['slot'] != n or not _valid_wp(record['wp']) or
            not _valid_tail(record)):
        _refuse(LEDGER_INVALID, 'week slot record invalid')
    return record


def _refuse_used(record):
    """Refuse a used WP with its recorded state and digest; never relaunch."""
    slot = record['week_slot']
    _refuse(WP_USED, 'state=%s week=%s week_slot=%s report_sha256=%s' % (
        record['state'], record['week'], 'none' if slot is None else slot,
        record['report_sha256'] or 'none'))


def _record_bytes(record):
    return (json.dumps(record, sort_keys=True) + '\n').encode('ascii')


def _sync_directory(dfd):
    """Best effort: make a created or replaced name durable before any launch."""
    try:
        os.fsync(dfd)
    except OSError:
        pass


def _unlink_quietly(dfd, name):
    try:
        os.unlink(name, dir_fd=dfd)
    except OSError:
        pass


def _write_temporary(dfd, record):
    """Write ``record`` to a fresh private ``.consult-*`` name; returns (name, fstat)."""
    for _ in range(16):
        name = _TEMP_PREFIX + secrets.token_hex(12)
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC,
                         0o600, dir_fd=dfd)
        except FileExistsError:
            continue
        break
    else:
        # A plain OSError, never FileExistsError: a slot-exists signal must
        # only ever come from the create-once link itself.
        raise OSError('no free temporary name')
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(_record_bytes(record))
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
        info = os.fstat(fd)
    except BaseException:
        os.close(fd)
        _unlink_quietly(dfd, name)
        raise
    os.close(fd)
    return name, info


def _create_once(dfd, name, record):
    """Create slot ``name`` holding ``record`` exactly once; FileExistsError if it exists.

    The temporary name is the only second link ever tolerated: once it is
    removed the new slot must be our inode with exactly one name, else the
    slot stays (fail closed) and the call refuses LEDGER_INVALID.
    """
    temporary, created = _write_temporary(dfd, record)
    try:
        # A hard link is atomic and fails when the name already exists.
        os.link(temporary, name, src_dir_fd=dfd, dst_dir_fd=dfd)
    finally:
        _unlink_quietly(dfd, temporary)
    try:
        info = os.stat(name, dir_fd=dfd, follow_symlinks=False)
    except OSError:
        _refuse(LEDGER_INVALID, 'created slot unreadable')
    if (not _private_file(info) or info.st_ino != created.st_ino or
            info.st_dev != created.st_dev):
        _refuse(LEDGER_INVALID, 'created slot not a single-link private file')


def _replace(dfd, name, record):
    """Atomically replace slot ``name``; the name itself is never removed."""
    temporary, _ = _write_temporary(dfd, record)
    try:
        os.replace(temporary, name, src_dir_fd=dfd, dst_dir_fd=dfd)
    except BaseException:
        _unlink_quietly(dfd, temporary)
        raise


def _week_has_free_slot(dfd, week):
    """Check every existing slot of ``week``; True when at least one n is unused."""
    free = False
    for n in range(1, WEEK_LIMIT + 1):
        record = _read_slot(dfd, _week_name(week, n))
        if record is None:
            free = True
        else:
            _check_week_record(record, week, n)
    return free


def _claim_week_slot(dfd, week, wp, stamp):
    """Create the first free week slot; a lost race tries the next n. None when full."""
    for n in range(1, WEEK_LIMIT + 1):
        name = _week_name(week, n)
        record = {'schema': _WEEK_SCHEMA, 'week': week, 'slot': n, 'wp': wp,
                  'state': RESERVED, 'reserved_at': stamp, 'finished_at': None,
                  'report_sha256': None}
        try:
            _create_once(dfd, name, record)
        except FileExistsError:
            taken = _read_slot(dfd, name)
            if taken is None:
                _refuse(LEDGER_INVALID, 'week slot vanished')
            _check_week_record(taken, week, n)
            continue
        except OSError:
            _refuse(LEDGER_INVALID, 'week slot not created')
        return n
    return None


def reserve(wp, *, now=None, root=None):
    """Reserve the one advice slot of ``wp`` and one slot of the current ISO week.

    Call after every local check and just before launch. Returns a
    ``Reservation``. Refuses WP_USED (with the slot state and report digest)
    when the WP was ever reserved (also when another caller reserved it while
    this call found the week full), WEEK_CAP when the week has no free slot,
    LEDGER_INVALID for a bad ledger or root and ARGUMENT_INVALID for a bad
    ``wp``, ``now`` or ``root`` type. ``root`` is the Task store root, as for
    the screen records; see ``ledger_directory`` for its confinement.
    """
    if not _valid_wp(wp):
        _refuse(ARGUMENT_INVALID, 'wp must match [A-Za-z0-9._-]{1,64}')
    moment = _utc(now)
    store = None if root is None else _checked_root(root)
    week = iso_week(moment)
    stamp = moment.isoformat()
    with _ledger(store, create=True) as (directory, dfd):
        wp_name = _wp_name(wp)
        existing = _read_slot(dfd, wp_name)
        if existing is not None:
            _refuse_used(_check_wp_record(existing, wp))
        # A full or bad week refuses here, before the WP slot is taken.
        if not _week_has_free_slot(dfd, week):
            # Another caller for this WP may have reserved it (taking the last
            # week slot) after the read above: report that WP as used, with
            # its state and digest, rather than as a week cap.
            existing = _read_slot(dfd, wp_name)
            if existing is not None:
                _refuse_used(_check_wp_record(existing, wp))
            _refuse(WEEK_CAP, 'week=%s limit=%d' % (week, WEEK_LIMIT))
        record = {'schema': _WP_SCHEMA, 'wp': wp, 'state': RESERVED, 'week': week,
                  'week_slot': None, 'reserved_at': stamp, 'finished_at': None,
                  'report_sha256': None}
        try:
            _create_once(dfd, wp_name, record)
        except FileExistsError:
            # Lost the race for this WP: report the winner's slot, never launch.
            taken = _read_slot(dfd, wp_name)
            if taken is None:
                _refuse(LEDGER_INVALID, 'WP slot vanished')
            _refuse_used(_check_wp_record(taken, wp))
        except OSError:
            _refuse(LEDGER_INVALID, 'WP slot not created')
        _sync_directory(dfd)
        # The WP slot now exists and is never removed: every refusal below fails closed.
        n = _claim_week_slot(dfd, week, wp, stamp)
        if n is None:
            # Concurrent callers took the last week slots. Mark the WP slot
            # FAILED (no launch, no report); if that write fails it stays
            # RESERVED, which is closed too.
            try:
                _replace(dfd, wp_name, dict(record, state=FAILED, finished_at=stamp))
            except OSError:
                pass
            _refuse(WEEK_CAP, 'week=%s limit=%d' % (week, WEEK_LIMIT))
        try:
            _replace(dfd, wp_name, dict(record, week_slot=n))
        except OSError:
            _refuse(LEDGER_INVALID, 'WP slot not updated')
        _sync_directory(dfd)
    return Reservation(wp=wp, week=week, week_slot=n, directory=directory, root=store)


def _valid_handle(handle):
    if type(handle) is not Reservation:
        return False
    root = handle.root
    if root is not None and (not isinstance(root, Path) or
                             not _clean_absolute(os.fspath(root))):
        return False
    return (_valid_wp(handle.wp) and type(handle.week) is str and
            _WEEK.fullmatch(handle.week) is not None and
            _valid_slot_number(handle.week_slot) and isinstance(handle.directory, Path))


def finish(handle, status, report_sha256, *, now=None):
    """Move a reservation's WP and week slots from RESERVED to DONE or FAILED.

    DONE needs the 64-hex report digest; FAILED takes a digest or None. A slot
    already finished with the same status and digest is left as is, so a
    repeated or interrupted finish completes; any other finished state refuses
    ARGUMENT_INVALID. The ledger is derived again from ``handle.root`` under
    the same confinement as ``reserve`` and must equal ``handle.directory``.
    Slots are never deleted.
    """
    if not _valid_handle(handle):
        _refuse(ARGUMENT_INVALID, 'handle invalid')
    if status not in (DONE, FAILED):
        _refuse(ARGUMENT_INVALID, 'status must be DONE or FAILED')
    if report_sha256 is None:
        if status == DONE:
            _refuse(ARGUMENT_INVALID, 'DONE needs a report digest')
    elif type(report_sha256) is not str or _DIGEST.fullmatch(report_sha256) is None:
        _refuse(ARGUMENT_INVALID, 'report digest must be 64 lowercase hex')
    stamp = _utc(now).isoformat()
    with _ledger(handle.root, create=False) as (directory, dfd):
        if handle.directory != directory:
            _refuse(ARGUMENT_INVALID, 'handle directory does not match its root')
        wp_name = _wp_name(handle.wp)
        week_name = _week_name(handle.week, handle.week_slot)
        wp_record = _read_slot(dfd, wp_name)
        week_record = _read_slot(dfd, week_name)
        if wp_record is None or week_record is None:
            _refuse(LEDGER_INVALID, 'reserved slot missing')
        _check_wp_record(wp_record, handle.wp)
        _check_week_record(week_record, handle.week, handle.week_slot)
        if (wp_record['week'] != handle.week or wp_record['week_slot'] != handle.week_slot or
                week_record['wp'] != handle.wp):
            _refuse(LEDGER_INVALID, 'slots do not match the reservation')
        # Check both slots before writing either, so a conflict changes nothing.
        pending = []
        for name, record in ((wp_name, wp_record), (week_name, week_record)):
            if record['state'] == RESERVED:
                pending.append((name, dict(record, state=status, finished_at=stamp,
                                           report_sha256=report_sha256)))
            elif record['state'] != status or record['report_sha256'] != report_sha256:
                _refuse(ARGUMENT_INVALID, 'slot already finished: state=%s report_sha256=%s' % (
                    record['state'], record['report_sha256'] or 'none'))
        for name, record in pending:
            try:
                _replace(dfd, name, record)
            except OSError:
                _refuse(LEDGER_INVALID, 'slot not updated')
        if pending:
            _sync_directory(dfd)
