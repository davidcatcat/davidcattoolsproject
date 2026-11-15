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
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import tempfile

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


@dataclass(frozen=True)
class Reservation:
    """Handle for one RESERVED advice slot pair; pass it to ``finish``."""
    wp: str
    week: str
    week_slot: int
    directory: Path


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


def ledger_directory(root=None):
    """The ledger directory beside Task store ``root``; None means the default store."""
    if root is None:
        from . import task_store
        root = task_store.default_root()
    return Path(root).parent / LEDGER_DIR


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


def _wp_path(directory, wp):
    return directory / ('wp-' + wp + '.json')


def _week_path(directory, week, n):
    return directory / ('week-%s-%d.json' % (week, n))


def _ledger_ready(directory, *, create):
    """Create (when ``create``) and check the owner-only, non-symlink ledger directory."""
    try:
        if create:
            directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                os.mkdir(directory, 0o700)
            except FileExistsError:
                pass
            else:
                os.chmod(directory, 0o700)
        info = os.lstat(directory)
    except OSError:
        _refuse(LEDGER_INVALID, 'ledger directory unusable')
    if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or
            info.st_uid != os.getuid()):
        _refuse(LEDGER_INVALID, 'ledger directory not private')


def _private_file(info):
    # The link count is not checked: create-once briefly holds a second name.
    return (stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and
            info.st_uid == os.getuid() and info.st_size <= _RECORD_LIMIT)


def _read_slot(path):
    """Return one slot's parsed JSON, or None when the name does not exist."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        _refuse(LEDGER_INVALID, 'slot unreadable')
    if not _private_file(info):
        _refuse(LEDGER_INVALID, 'slot not a private regular file')
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) |
                     getattr(os, 'O_NONBLOCK', 0))
    except OSError:
        _refuse(LEDGER_INVALID, 'slot unreadable')
    try:
        # The inode may differ from the lstat one: the owner's atomic replace
        # can land in between. O_NOFOLLOW and these checks still bind the file.
        if not _private_file(os.fstat(fd)):
            _refuse(LEDGER_INVALID, 'slot not a private regular file')
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


def _sync_directory(directory):
    """Best effort: make a created or replaced name durable before any launch."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _create_once(path, record):
    """Create ``path`` holding ``record`` exactly once; FileExistsError if the name exists."""
    fd, temporary = tempfile.mkstemp(prefix='.consult-', dir=str(path.parent))
    try:
        try:
            os.fchmod(fd, 0o600)
            stream = os.fdopen(fd, 'wb')
        except Exception:
            os.close(fd)
            raise
        with stream:
            stream.write(_record_bytes(record))
            stream.flush()
            os.fsync(stream.fileno())
        # A hard link is atomic and fails when the name already exists.
        os.link(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass


def _replace(path, record):
    """Atomically replace the slot at ``path``; the name itself is never removed."""
    fd, temporary = tempfile.mkstemp(prefix='.consult-', dir=str(path.parent))
    try:
        try:
            os.fchmod(fd, 0o600)
            stream = os.fdopen(fd, 'wb')
        except Exception:
            os.close(fd)
            raise
        with stream:
            stream.write(_record_bytes(record))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _week_has_free_slot(directory, week):
    """Check every existing slot of ``week``; True when at least one n is unused."""
    free = False
    for n in range(1, WEEK_LIMIT + 1):
        record = _read_slot(_week_path(directory, week, n))
        if record is None:
            free = True
        else:
            _check_week_record(record, week, n)
    return free


def _claim_week_slot(directory, week, wp, stamp):
    """Create the first free week slot; a lost race tries the next n. None when full."""
    for n in range(1, WEEK_LIMIT + 1):
        path = _week_path(directory, week, n)
        record = {'schema': _WEEK_SCHEMA, 'week': week, 'slot': n, 'wp': wp,
                  'state': RESERVED, 'reserved_at': stamp, 'finished_at': None,
                  'report_sha256': None}
        try:
            _create_once(path, record)
        except FileExistsError:
            taken = _read_slot(path)
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
    when the WP was ever reserved, WEEK_CAP when the week has no free slot,
    LEDGER_INVALID for a bad ledger and ARGUMENT_INVALID for a bad ``wp`` or
    ``now``. ``root`` is the Task store root, as for the screen records.
    """
    if not _valid_wp(wp):
        _refuse(ARGUMENT_INVALID, 'wp must match [A-Za-z0-9._-]{1,64}')
    moment = _utc(now)
    week = iso_week(moment)
    stamp = moment.isoformat()
    directory = ledger_directory(root)
    _ledger_ready(directory, create=True)
    wp_path = _wp_path(directory, wp)
    existing = _read_slot(wp_path)
    if existing is not None:
        _refuse_used(_check_wp_record(existing, wp))
    # A full or bad week refuses here, before the WP slot is taken.
    if not _week_has_free_slot(directory, week):
        _refuse(WEEK_CAP, 'week=%s limit=%d' % (week, WEEK_LIMIT))
    record = {'schema': _WP_SCHEMA, 'wp': wp, 'state': RESERVED, 'week': week,
              'week_slot': None, 'reserved_at': stamp, 'finished_at': None,
              'report_sha256': None}
    try:
        _create_once(wp_path, record)
    except FileExistsError:
        # Lost the race for this WP: report the winner's slot, never launch.
        taken = _read_slot(wp_path)
        if taken is None:
            _refuse(LEDGER_INVALID, 'WP slot vanished')
        _refuse_used(_check_wp_record(taken, wp))
    except OSError:
        _refuse(LEDGER_INVALID, 'WP slot not created')
    _sync_directory(directory)
    # The WP slot now exists and is never removed: every refusal below fails closed.
    n = _claim_week_slot(directory, week, wp, stamp)
    if n is None:
        # Concurrent callers took the last week slots. Mark the WP slot FAILED
        # (no launch, no report); if that write fails it stays RESERVED, which
        # is closed too.
        try:
            _replace(wp_path, dict(record, state=FAILED, finished_at=stamp))
        except OSError:
            pass
        _refuse(WEEK_CAP, 'week=%s limit=%d' % (week, WEEK_LIMIT))
    try:
        _replace(wp_path, dict(record, week_slot=n))
    except OSError:
        _refuse(LEDGER_INVALID, 'WP slot not updated')
    _sync_directory(directory)
    return Reservation(wp=wp, week=week, week_slot=n, directory=directory)


def finish(handle, status, report_sha256, *, now=None):
    """Move a reservation's WP and week slots from RESERVED to DONE or FAILED.

    DONE needs the 64-hex report digest; FAILED takes a digest or None. A slot
    already finished with the same status and digest is left as is, so a
    repeated or interrupted finish completes; any other finished state refuses
    ARGUMENT_INVALID. Slots are never deleted.
    """
    if (type(handle) is not Reservation or not _valid_wp(handle.wp) or
            type(handle.week) is not str or _WEEK.fullmatch(handle.week) is None or
            not _valid_slot_number(handle.week_slot) or
            not isinstance(handle.directory, Path)):
        _refuse(ARGUMENT_INVALID, 'handle invalid')
    if status not in (DONE, FAILED):
        _refuse(ARGUMENT_INVALID, 'status must be DONE or FAILED')
    if report_sha256 is None:
        if status == DONE:
            _refuse(ARGUMENT_INVALID, 'DONE needs a report digest')
    elif type(report_sha256) is not str or _DIGEST.fullmatch(report_sha256) is None:
        _refuse(ARGUMENT_INVALID, 'report digest must be 64 lowercase hex')
    stamp = _utc(now).isoformat()
    directory = handle.directory
    _ledger_ready(directory, create=False)
    wp_path = _wp_path(directory, handle.wp)
    week_path = _week_path(directory, handle.week, handle.week_slot)
    wp_record = _read_slot(wp_path)
    week_record = _read_slot(week_path)
    if wp_record is None or week_record is None:
        _refuse(LEDGER_INVALID, 'reserved slot missing')
    _check_wp_record(wp_record, handle.wp)
    _check_week_record(week_record, handle.week, handle.week_slot)
    if (wp_record['week'] != handle.week or wp_record['week_slot'] != handle.week_slot or
            week_record['wp'] != handle.wp):
        _refuse(LEDGER_INVALID, 'slots do not match the reservation')
    # Check both slots before writing either, so a conflict changes nothing.
    pending = []
    for path, record in ((wp_path, wp_record), (week_path, week_record)):
        if record['state'] == RESERVED:
            pending.append((path, dict(record, state=status, finished_at=stamp,
                                       report_sha256=report_sha256)))
        elif record['state'] != status or record['report_sha256'] != report_sha256:
            _refuse(ARGUMENT_INVALID, 'slot already finished: state=%s report_sha256=%s' % (
                record['state'], record['report_sha256'] or 'none'))
    for path, record in pending:
        try:
            _replace(path, record)
        except OSError:
            _refuse(LEDGER_INVALID, 'slot not updated')
    if pending:
        _sync_directory(directory)
