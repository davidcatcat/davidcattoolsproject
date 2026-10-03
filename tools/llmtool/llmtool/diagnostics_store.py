"""Opt-in POSIX metadata store. No provider access or implicit CLI persistence.

Failures return None/False and retain one safe warning. Callers own rendering;
they must preserve the primary task result. Unknown store data is never removed.
"""

import errno
import os
import stat
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

from . import diagnostics as d
from .diagnostics_settings import DiagnosticsSettings, resolve_diagnostics_home
from .diagnostics_summary import (RunSummary, SUMMARY_MAX_BYTES,
                                  parse_run_summary, strict_object)

RESERVATION_SCHEMA = 'llmtool.diagnostics.reservation'
RESERVATION_MAX_BYTES = 1024
ABANDONED_GRACE_SECONDS = 3600
SCAN_LIMIT = 10000
RUN_OVERHEAD_BYTES = SUMMARY_MAX_BYTES + RESERVATION_MAX_BYTES
RUN_FILES = frozenset(('.run.lock', '.reservation', 'trace.partial.jsonl',
                       'trace.jsonl', 'summary.json'))


@dataclass(frozen=True)
class StoreWarning:
    code: d.ErrorCode
    message: str = 'diagnostics persistence unavailable; task result unaffected'

    def line(self):
        return 'llmtool: code=' + self.code.value + ' ' + self.message


class _Unsafe(Exception):
    pass


class _Full(Exception):
    pass


class _Collision(Exception):
    pass


def _warning(exc, writing=False):
    code = (d.ErrorCode.DIAGNOSTICS_STORE_UNSAFE if isinstance(exc, _Unsafe) else
            d.ErrorCode.DIAGNOSTICS_STORE_FULL if isinstance(exc, _Full) else
            d.ErrorCode.DIAGNOSTICS_RUN_COLLISION if isinstance(exc, _Collision) else
            d.ErrorCode.DIAGNOSTICS_WRITE_FAILED if writing else
            d.ErrorCode.DIAGNOSTICS_STORE_UNAVAILABLE)
    return StoreWarning(code)


def persistence_supported(platform=None):
    system = sys.platform if platform is None else platform
    return (system == 'darwin' or system.startswith('linux')) and all(
        hasattr(os, name) for name in ('O_NOFOLLOW', 'O_DIRECTORY', 'geteuid'))


def _private(info, directory=False):
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o7077
            or (not directory and info.st_nlink != 1)):
        raise _Unsafe()


def _directory(parent, name, create=False, private=True):
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        if private:
            _private(os.fstat(fd), True)
        return fd
    except Exception:
        os.close(fd)
        raise


def _home(path):
    # Root is the immutable trusted anchor. Traverse every component no-follow.
    if not path.is_absolute() or '..' in path.parts or len(path.parts) < 2:
        raise _Unsafe()
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for index, name in enumerate(path.parts[1:], 1):
            try:
                child = _directory(fd, name, private=index == len(path.parts) - 1)
            except FileNotFoundError:
                child = _directory(fd, name, create=True)
            os.close(fd)
            fd = child
        return fd
    except Exception:
        os.close(fd)
        raise


def _file(parent, name, flags=0, create=False):
    base = os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW | flags
    if create and not flags & os.O_EXCL:
        # Exclusive create then no-follow open avoids a concurrent O_CREAT /
        # O_NOFOLLOW lookup race on macOS. Never replace an existing lock inode.
        try:
            fd = os.open(name, base | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
        except FileExistsError:
            fd = os.open(name, base, dir_fd=parent)
    else:
        fd = os.open(name, base | (os.O_CREAT if create else 0), 0o600, dir_fd=parent)
    try:
        _private(os.fstat(fd))
        return fd
    except Exception:
        os.close(fd)
        raise


def _read(parent, name, limit):
    fd = _file(parent, name)
    try:
        if os.fstat(fd).st_size > limit:
            raise _Unsafe()
        raw = bytearray()
        while len(raw) <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        if len(raw) > limit:
            raise _Unsafe()
        return bytes(raw)
    finally:
        os.close(fd)


def _write(fd, raw):
    view = memoryview(raw)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            raise OSError('diagnostics write failed')
        view = view[count:]


def _sync_directory(fd):
    try:
        os.fsync(fd)
    except OSError as exc:
        if exc.errno not in (errno.EINVAL, errno.ENOTSUP):
            raise


@contextmanager
def _store_lock(fd):
    import fcntl
    deadline = time.monotonic() + 2
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise OSError('diagnostics lock unavailable') from None
            time.sleep(0.01)
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _publish(parent, source, target):
    # Atomic no-clobber publication, even if target is a dangling symlink.
    os.link(source, target, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
    os.unlink(source, dir_fd=parent)


def _reservation(raw, run_id):
    value = strict_object(raw, RESERVATION_MAX_BYTES)
    if (set(value) != {'schema', 'schema_version', 'run_id', 'max_run_bytes', 'created_at'}
            or value['schema'] != RESERVATION_SCHEMA
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['run_id'] != run_id
            or type(value['max_run_bytes']) is not int
            or not 65536 <= value['max_run_bytes'] <= 8 * 1024 * 1024):
        raise _Unsafe()
    d.validate_timestamp(value['created_at'])
    return value


def _events(raw, run_id):
    result = []
    if raw and not raw.endswith(b'\n'):
        raise _Unsafe()
    for line in raw.splitlines():
        if len(result) >= d.MAX_RUN_EVENTS:
            raise _Unsafe()
        value = strict_object(line, d.EVENT_MAX_BYTES)
        value['type'] = d.EventType(value['type'])
        value['severity'] = d.DiagnosticSeverity(value['severity'])
        obj = d.DiagnosticEvent(**value)
        if obj.run_id != run_id or obj.event_id != len(result) + 1:
            raise _Unsafe()
        result.append(obj)
    return result


def _bytes(parent, budget, skip=()):
    """Account unknown data without following links; bounded traversal fails closed."""
    total = 0
    names = os.listdir(parent)
    budget[0] -= len(names)
    if budget[0] < 0:
        raise _Full()
    for name in names:
        if name in skip:
            continue
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child = _directory(parent, name, private=False)
            try:
                total += _bytes(child, budget)
            finally:
                os.close(child)
        else:
            # Includes symlink text and unrelated regular files, never their targets.
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
                raise _Full()
            total += info.st_size
    return total


class DiagnosticsStore:
    """Construction is pure; start() alone creates/cleans an explicitly enabled store."""

    def __init__(self, settings=None, *, home=None, platform=None):
        self.settings = DiagnosticsSettings() if settings is None else settings
        if type(self.settings) is not DiagnosticsSettings:
            raise ValueError('diagnostics store requires validated settings')
        self.home = home
        self.platform = platform
        self.warning = None

    def _warn(self, exc, writing=False):
        if self.warning is None:
            self.warning = _warning(exc, writing)

    def start(self, run_id=None):
        self.warning = None
        if not self.settings.persist_metadata:
            return None  # Do not inspect home/env/platform, or perform any I/O.
        fds = []
        try:
            if not persistence_supported(self.platform):
                raise OSError('unsupported diagnostics backend')
            from pathlib import Path
            path = (resolve_diagnostics_home(platform=self.platform) if self.home is None else
                    resolve_diagnostics_home(environ={'LLMTOOL_HOME': str(self.home)},
                                             platform=self.platform))
            root = _home(Path(path))
            fds.append(root)
            lock = _file(root, '.store.lock', create=True)
            fds.append(lock)
            with _store_lock(lock):
                runs = _directory(root, 'runs', create=True)
                fds.append(runs)
                self._capacity(root, runs)
                identity = d.new_run_id() if run_id is None else d.validate_run_id(run_id)
                for attempt in range(3):
                    try:
                        os.mkdir(identity, 0o700, dir_fd=runs)
                        break
                    except FileExistsError:
                        if attempt == 2:
                            raise _Collision() from None
                        identity = d.new_run_id()
                directory = _directory(runs, identity)
                fds.append(directory)
                run_lock = _file(directory, '.run.lock', os.O_EXCL, create=True)
                fds.append(run_lock)
                import fcntl
                fcntl.flock(run_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                reservation = _file(directory, '.reservation', os.O_EXCL, create=True)
                try:
                    raw = d.diagnostic_json({
                        'schema': RESERVATION_SCHEMA, 'schema_version': 1,
                        'run_id': identity, 'max_run_bytes': self.settings.max_run_bytes,
                        'created_at': d.utc_timestamp(),
                    }, RESERVATION_MAX_BYTES).encode('utf-8')
                    _write(reservation, raw)
                    os.fsync(reservation)
                finally:
                    os.close(reservation)
                trace = _file(directory, 'trace.partial.jsonl', os.O_EXCL, create=True)
                fds.append(trace)
                _sync_directory(directory)
                _sync_directory(runs)
            writer = StoredRun(self, identity, root, runs, lock, directory, run_lock, trace)
            fds = []  # Writer owns all descriptors, including root lock for completion.
            return writer
        except Exception as exc:
            self._warn(exc)
            return None
        finally:
            for fd in reversed(fds):
                os.close(fd)

    def _candidate(self, runs, name, now):
        """Hold candidate run lock through deletion. Unknown/active entries are kept."""
        directory = lock = None
        try:
            d.validate_run_id(name)
            directory = _directory(runs, name)
            lock = _file(directory, '.run.lock')
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            names = set(os.listdir(directory))
            if not names <= RUN_FILES:
                raise _Unsafe()
            for filename in names:
                _private(os.stat(filename, dir_fd=directory, follow_symlinks=False))
            summary = None
            reservation = None
            if '.reservation' in names:
                reservation = _reservation(_read(directory, '.reservation', RESERVATION_MAX_BYTES), name)
            if 'summary.json' in names:
                summary = parse_run_summary(_read(directory, 'summary.json', SUMMARY_MAX_BYTES))
                if summary.run_id != name or names - {'.run.lock', '.reservation', 'trace.jsonl', 'summary.json'}:
                    raise _Unsafe()
                raw = _read(directory, 'trace.jsonl', 8 * 1024 * 1024)
                events = _events(raw, name)
                if (not events or events[0].type is not d.EventType.RUN_START
                        or events[-1].type is not d.EventType.RUN_END
                        or summary.trace_bytes != len(raw) or summary.event_count != len(events)
                        or summary.started_at != events[0].timestamp
                        or summary.ended_at != events[-1].timestamp
                        or summary.mode.value != events[0].data.get('mode')
                        or summary.status != events[-1].data.get('status')
                        or summary.duration_ms != events[-1].data.get('duration_ms')):
                    raise _Unsafe()
                stamp = datetime.strptime(summary.ended_at, '%Y-%m-%dT%H:%M:%S.%fZ').replace(
                    tzinfo=timezone.utc).timestamp()
                eligible = True
            else:
                if reservation is None or ('trace.partial.jsonl' in names and 'trace.jsonl' in names):
                    raise _Unsafe()
                for filename in ('trace.partial.jsonl', 'trace.jsonl'):
                    if filename in names:
                        _events(_read(directory, filename, reservation['max_run_bytes']), name)
                # File mtimes prevent deletion of recently updated abandoned work.
                stamp = max([os.fstat(directory).st_mtime] + [
                    os.stat(n, dir_fd=directory, follow_symlinks=False).st_mtime for n in names])
                eligible = now - stamp >= ABANDONED_GRACE_SECONDS
            candidate = (name, directory, lock, names, stamp, summary is not None, eligible)
            directory = lock = None
            return candidate
        except (OSError, ValueError, TypeError, KeyError, _Unsafe):
            return None
        finally:
            for fd in (lock, directory):
                if fd is not None:
                    os.close(fd)

    def _remove(self, runs, candidate):
        name, directory, _, names, *_ = candidate
        original = os.fstat(directory)
        current = os.stat(name, dir_fd=runs, follow_symlinks=False)
        if (original.st_dev, original.st_ino) != (current.st_dev, current.st_ino):
            raise _Unsafe()
        if set(os.listdir(directory)) != names:
            raise _Unsafe()
        for filename in names:
            _private(os.stat(filename, dir_fd=directory, follow_symlinks=False))
        for filename in names:
            os.unlink(filename, dir_fd=directory)
        os.rmdir(name, dir_fd=runs)
        _sync_directory(runs)

    def _capacity(self, root, runs):
        settings, now = self.settings, time.time()
        names = os.listdir(runs)
        if len(names) > SCAN_LIMIT:
            raise _Full()
        candidates = []
        try:
            for name in names:
                item = self._candidate(runs, name, now)
                if item is not None:
                    candidates.append(item)
            candidates.sort(key=lambda item: (item[4], item[0]))
            # Age cleanup and abandoned recovery precede count/byte pressure.
            deleted = set()
            for item in candidates:
                if item[6] and (not item[5] or now - item[4] > settings.max_age_days * 86400):
                    self._remove(runs, item)
                    deleted.add(item[0])
            def usage():
                budget = [SCAN_LIMIT]
                total = _bytes(root, budget, skip=('runs',))
                current = os.listdir(runs)
                budget[0] -= len(current)
                if budget[0] < 0:
                    raise _Full()
                for name in current:
                    directory = None
                    try:
                        info = os.stat(name, dir_fd=runs, follow_symlinks=False)
                        if not stat.S_ISDIR(info.st_mode):
                            if not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
                                raise _Full()
                            total += info.st_size
                            continue
                        directory = _directory(runs, name, private=False)
                        actual = _bytes(directory, budget)
                        if '.reservation' in os.listdir(directory):
                            d.validate_run_id(name)
                            _private(os.fstat(directory), True)
                            reservation = _reservation(_read(directory, '.reservation', RESERVATION_MAX_BYTES), name)
                            # Charge the whole reservation once. Sampling root bytes
                            # then subtracting a later run size would undercount
                            # a concurrently growing active trace.
                            actual = max(actual, reservation['max_run_bytes'])
                        total += actual
                    except (OSError, ValueError, TypeError, KeyError, _Unsafe):
                        # Invalid reservations cannot guarantee a capacity bound.
                        raise _Full() from None
                    finally:
                        if directory is not None:
                            os.close(directory)
                return len(current), total
            count, total = usage()
            for item in candidates:
                if item[0] in deleted:
                    continue
                if count < settings.max_runs and total + settings.max_run_bytes <= settings.max_total_bytes:
                    break
                if item[5] and item[6]:
                    self._remove(runs, item)
                    count, total = usage()
            if count >= settings.max_runs or total + settings.max_run_bytes > settings.max_total_bytes:
                raise _Full()
        finally:
            # Includes deleted directories: their still-open lock descriptors are valid.
            for item in candidates:
                os.close(item[2])
                os.close(item[1])


class StoredRun:
    """One writer; explicit append/finish/close. Closing without finish is a crash record."""

    def __init__(self, store, run_id, *fds):
        self.store, self.run_id = store, run_id
        self.root, self.runs, self.store_lock, self.directory, self.run_lock, self.trace = fds
        self._fds = fds
        self._lock = threading.RLock()
        self.closed = self.persisted = False
        self.warning = None
        self.trace_bytes = 0
        self._events = []

    @property
    def events(self):
        return tuple(self._events)

    def _warn(self, exc):
        if self.warning is None:
            self.warning = _warning(exc, writing=True)

    def append(self, event):
        with self._lock:
            if self.closed:
                return False
            try:
                if (type(event) is not d.DiagnosticEvent or event.run_id != self.run_id
                        or event.event_id != len(self._events) + 1
                        or len(self._events) >= d.MAX_RUN_EVENTS
                        or (not self._events and event.type is not d.EventType.RUN_START)
                        or (self._events and (event.type is d.EventType.RUN_START
                                             or self._events[-1].type is d.EventType.RUN_END))):
                    raise ValueError('invalid stored diagnostics sequence')
                raw = (event.to_json() + '\n').encode('utf-8')
                if self.trace_bytes + len(raw) + RUN_OVERHEAD_BYTES > self.store.settings.max_run_bytes:
                    raise _Full()
                _write(self.trace, raw)
                self.trace_bytes += len(raw)
                self._events.append(event)
                return True
            except Exception as exc:
                self._warn(exc)
                self.close()
                return False

    def finish(self, summary):
        with self._lock:
            if self.closed:
                return False
            try:
                if (type(summary) is not RunSummary or summary.run_id != self.run_id
                        or summary.trace_bytes != self.trace_bytes
                        or summary.event_count != len(self._events) or not self._events
                        or self._events[-1].type is not d.EventType.RUN_END
                        or summary.started_at != self._events[0].timestamp
                        or summary.ended_at != self._events[-1].timestamp
                        or summary.mode.value != self._events[0].data.get('mode')
                        or summary.status != self._events[-1].data.get('status')
                        or summary.duration_ms != self._events[-1].data.get('duration_ms')):
                    raise ValueError('summary does not match trace')
                raw = (summary.to_json() + '\n').encode('utf-8')
                os.fsync(self.trace)
                _publish(self.directory, 'trace.partial.jsonl', 'trace.jsonl')
                temporary = '.summary-' + d.new_run_id()
                fd = _file(self.directory, temporary, os.O_EXCL, create=True)
                try:
                    _write(fd, raw)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                _publish(self.directory, temporary, 'summary.json')
                _sync_directory(self.directory)
                with _store_lock(self.store_lock):
                    os.unlink('.reservation', dir_fd=self.directory)
                    _sync_directory(self.directory)
                self.persisted = True
                return True
            except Exception as exc:
                self._warn(exc)
                return False
            finally:
                # Temp artifacts after a failure are deliberately unknown to cleanup.
                self.close()

    def close(self):
        with self._lock:
            if not self.closed:
                self.closed = True
                for fd in reversed(self._fds):
                    os.close(fd)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
