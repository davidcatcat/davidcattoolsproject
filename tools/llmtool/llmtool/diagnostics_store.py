"""Opt-in POSIX metadata store. No provider access or implicit CLI persistence.

Failures return None/False and retain one safe warning. Callers own rendering;
they must preserve the primary task result. Unknown store data is never removed;
guarded cleanup may remove empty, private initialization stages.
"""

import errno
import json
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
EVICTION_SCHEMA = 'llmtool.diagnostics.eviction'
EVICTION_MAX_BYTES = 4096


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
    code = (d.ErrorCode.DIAGNOSTICS_STORE_UNSAFE if (isinstance(exc, _Unsafe) or
            isinstance(exc, OSError) and exc.errno in (errno.ELOOP, errno.ENOTDIR)) else
            d.ErrorCode.DIAGNOSTICS_STORE_FULL if isinstance(exc, _Full) else
            d.ErrorCode.DIAGNOSTICS_RUN_COLLISION if isinstance(exc, _Collision) else
            d.ErrorCode.DIAGNOSTICS_WRITE_FAILED if writing else
            d.ErrorCode.DIAGNOSTICS_STORE_UNAVAILABLE)
    return StoreWarning(code)


def persistence_supported(platform=None):
    system = sys.platform if platform is None else platform
    return (system == 'darwin' or system.startswith('linux')) and all(
        hasattr(os, name) for name in ('O_NOFOLLOW', 'O_DIRECTORY', 'geteuid'))


def _inode(info):
    return info.st_dev, info.st_ino


def _fingerprint(info):
    return (_inode(info), info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _eviction_fingerprint(info):
    return list(_inode(info)) + list(_fingerprint(info)[1:])


def _eviction_intent(raw):
    """A closed internal deletion receipt, never an inference from damaged runs."""
    value = strict_object(raw, EVICTION_MAX_BYTES)
    if (set(value) != {'schema', 'schema_version', 'run_id', 'directory', 'files'} or
            value['schema'] != EVICTION_SCHEMA or type(value['schema_version']) is not int or
            value['schema_version'] != 1):
        raise _Unsafe()
    d.validate_run_id(value['run_id'])
    files = value['files']
    if type(files) is not dict or not set(files) <= RUN_FILES or '.run.lock' not in files:
        raise _Unsafe()
    for fingerprint in [value['directory'], *files.values()]:
        if (type(fingerprint) is not list or len(fingerprint) != 8 or
                any(type(n) is not int or not -(2 ** 63) <= n < 2 ** 64 for n in fingerprint)):
            raise _Unsafe()
    if not stat.S_ISDIR(value['directory'][2]):
        raise _Unsafe()
    for fingerprint in files.values():
        if not stat.S_ISREG(fingerprint[2]) or fingerprint[4] != 1:
            raise _Unsafe()
    return value


def _private(info, directory=False):
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o7077
            or (not directory and info.st_nlink != 1)):
        raise _Unsafe()


def _ancestor(info):
    # #137 R26: owned by root or this user; not group/other-writable unless sticky.
    mode = stat.S_IMODE(info.st_mode)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid())
            or (mode & 0o022 and not mode & stat.S_ISVTX)):
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
        _ancestor(os.fstat(fd))
        for index, name in enumerate(path.parts[1:], 1):
            try:
                child = _directory(fd, name, private=index == len(path.parts) - 1)
            except FileNotFoundError:
                child = _directory(fd, name, create=True)
            parent, fd = fd, child
            os.close(parent)
            if index < len(path.parts) - 1:
                _ancestor(os.fstat(fd))
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


def _publish_run_directory(parent, source, runs, target):
    """Atomic no-replace directory publication on the supported POSIX backends.

    No portable rename fallback: os.rename can replace an existing empty run.
    Missing libc/kernel support disables this save without touching the target.
    """
    import ctypes
    library = ctypes.CDLL(None, use_errno=True)
    symbol, flag = ('renameatx_np', 0x4) if sys.platform == 'darwin' else ('renameat2', 1)
    try:
        rename = getattr(library, symbol)
    except AttributeError:
        raise OSError(errno.ENOTSUP, 'atomic diagnostics publication unavailable') from None
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                       ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(parent, os.fsencode(source), runs, os.fsencode(target), flag) != 0:
        code = ctypes.get_errno()
        raise OSError(code, 'atomic diagnostics publication failed')


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


def _cleanup_empty_stages(root, names):
    """Remove only exact-name, private, same-user empty init stages.

    The caller holds the exclusive store lock, serializing this pass with init.
    Unknown, non-empty, or unsafe stages remain untouched.
    """
    prefix = '.run-init-'
    for name in names:
        if len(name) != len(prefix) + 32 or not name.startswith(prefix):
            continue
        try:
            d.validate_run_id(name[len(prefix):])
            directory = _directory(root, name)
            try:
                opened = os.fstat(directory)
                current = os.stat(name, dir_fd=root, follow_symlinks=False)
                _private(current, True)
                if _inode(opened) != _inode(current) or os.listdir(directory):
                    continue
                os.rmdir(name, dir_fd=root)
            finally:
                os.close(directory)
        except (OSError, ValueError, _Unsafe):
            # Cleanup is opportunistic; never make a diagnostics run fail.
            continue


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
                        os.stat(identity, dir_fd=runs, follow_symlinks=False)
                    except FileNotFoundError:
                        break
                    if attempt == 2:
                        raise _Collision()
                    identity = d.new_run_id()
                # mkdir cannot return a creation-bound inode. Initialize outside
                # runs; incomplete or unprovable stages remain for guarded cleanup.
                # Root byte/scan accounting still includes every retained stage.
                import secrets
                stage = '.run-init-' + secrets.token_hex(16)
                os.mkdir(stage, 0o700, dir_fd=root)
                directory = None
                created = {}
                run_locked = published = False
                try:
                    directory = _directory(root, stage)
                    fds.append(directory)

                    def create_file(name):
                        fd = os.open(name, os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW |
                                     os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
                        fds.append(fd)
                        created[name] = os.fstat(fd)
                        _private(created[name])
                        return fd

                    run_lock = create_file('.run.lock')
                    import fcntl
                    fcntl.flock(run_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    run_locked = True
                    reservation = create_file('.reservation')
                    raw = d.diagnostic_json({
                        'schema': RESERVATION_SCHEMA, 'schema_version': 1,
                        'run_id': identity, 'max_run_bytes': self.settings.max_run_bytes,
                        'created_at': d.utc_timestamp(),
                    }, RESERVATION_MAX_BYTES).encode('utf-8')
                    _write(reservation, raw)
                    os.fsync(reservation)
                    os.close(reservation)
                    fds.remove(reservation)
                    trace = create_file('trace.partial.jsonl')
                    os.fsync(trace)
                    _sync_directory(directory)
                    _sync_directory(root)
                    _sync_directory(runs)
                    # Finish all construction before the namespace commit point.
                    writer = StoredRun(self, identity, root, runs, lock, directory, run_lock, trace)
                    snapshot = self._snapshot(directory, set(created))
                    self._check_initialization(root, stage, directory, created)
                    _reservation(_read(directory, '.reservation', RESERVATION_MAX_BYTES), identity)
                    _events(_read(directory, 'trace.partial.jsonl', self.settings.max_run_bytes), identity)
                    if self._snapshot(directory, set(created)) != snapshot:
                        raise _Unsafe()
                    _publish_run_directory(root, stage, runs, identity)
                    published = True
                    # Directory rename has no portable fd-source form. Recheck
                    # the namespace after publication; an unmanaged same-UID race
                    # may move a replacement, but we never delete that unknown data.
                    self._check_initialization(runs, identity, directory, created)
                    _sync_directory(runs)
                    _sync_directory(root)
                except Exception:
                    if not published and run_locked:
                        self._rollback_start(root, stage, directory, created)
                    # Postpublication errors retain valid/ambiguous run data;
                    # no rollback deletes a final run directory.
                    raise
            fds = []  # Writer owns all descriptors, including root lock for completion.
            return writer
        except Exception as exc:
            self._warn(exc)
            return None
        finally:
            for fd in reversed(fds):
                try:
                    os.close(fd)
                except Exception as exc:
                    self._warn(exc)

    def _check_initialization(self, parent, name, directory, created):
        original = os.fstat(directory)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        _private(original, True)
        _private(current, True)
        if _inode(original) != _inode(current) or set(os.listdir(directory)) != set(created):
            raise _Unsafe()
        for filename, info in created.items():
            current = os.stat(filename, dir_fd=directory, follow_symlinks=False)
            _private(current)
            if _inode(current) != _inode(info):
                raise _Unsafe()

    def _rollback_start(self, root, stage, directory, created):
        try:
            self._check_initialization(root, stage, directory, created)
            for filename, info in created.items():
                current = os.stat(filename, dir_fd=directory, follow_symlinks=False)
                _private(current)
                if (_inode(current) != _inode(info) or
                        _inode(os.fstat(directory)) != _inode(os.stat(stage, dir_fd=root, follow_symlinks=False))):
                    return
                os.unlink(filename, dir_fd=directory)
            _sync_directory(directory)
            # Incomplete stages remain; guarded cleanup removes only empty,
            # private stages while holding the exclusive store lock.
        except (OSError, _Unsafe):
            pass  # Preserve unprovable data and the original warning.

    def _candidate(self, runs, name, now):
        """Validate one locked run; callers must release it before scanning another."""
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
            snapshot = self._snapshot(directory, names)
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
            if self._snapshot(directory, names) != snapshot:
                raise _Unsafe()
            candidate = (name, directory, lock, names, stamp, summary is not None, eligible, snapshot)
            directory = lock = None
            return candidate
        except (OSError, ValueError, TypeError, KeyError, _Unsafe):
            return None
        finally:
            for fd in (lock, directory):
                if fd is not None:
                    os.close(fd)

    @staticmethod
    def _snapshot(directory, names):
        return (_fingerprint(os.fstat(directory)), {
            name: _fingerprint(os.stat(name, dir_fd=directory, follow_symlinks=False))
            for name in names})

    @staticmethod
    def _close_candidate(candidate):
        os.close(candidate[2])
        os.close(candidate[1])

    def _reopen_candidate(self, runs, saved, now):
        candidate = self._candidate(runs, saved[0], now)
        if candidate is not None and candidate[7] != saved[7]:
            self._close_candidate(candidate)
            return None
        return candidate

    def _check_candidate(self, runs, candidate):
        name, directory, lock, names, *_ = candidate
        original = os.fstat(directory)
        current = os.stat(name, dir_fd=runs, follow_symlinks=False)
        _private(current, True)
        if _inode(original) != _inode(current):
            raise _Unsafe()
        if set(os.listdir(directory)) != names or self._snapshot(directory, names) != candidate[7]:
            raise _Unsafe()
        if _fingerprint(os.fstat(lock)) != candidate[7][1]['.run.lock']:
            raise _Unsafe()
        for filename in names:
            _private(os.stat(filename, dir_fd=directory, follow_symlinks=False))

    def _begin_eviction(self, root, runs, candidate):
        """Durably authorize only this fully validated, eligible locked candidate."""
        self._check_candidate(runs, candidate)
        if not candidate[6]:
            raise _Unsafe()
        import secrets
        token = secrets.token_hex(16)
        temporary, journal = '.eviction-init-' + token, '.eviction-' + token + '.json'
        raw = json.dumps({'schema': EVICTION_SCHEMA, 'schema_version': 1,
            'run_id': candidate[0], 'directory': _eviction_fingerprint(os.fstat(candidate[1])),
            'files': {name: _eviction_fingerprint(os.stat(name, dir_fd=candidate[1], follow_symlinks=False))
                      for name in candidate[3]}}, sort_keys=True, separators=(',', ':')).encode('ascii')
        _eviction_intent(raw)
        fd = _file(root, temporary, os.O_EXCL, create=True)
        created, published = _inode(os.fstat(fd)), False
        try:
            _write(fd, raw)
            os.fsync(fd)
            self._check_candidate(runs, candidate)
            if _fingerprint(os.stat(temporary, dir_fd=root, follow_symlinks=False)) != _fingerprint(os.fstat(fd)):
                raise _Unsafe()
            # Native no-replace rename also publishes regular files atomically,
            # avoiding the link/unlink nlink=2 interruption window.
            _publish_run_directory(root, temporary, root, journal)
            published = True
            if _fingerprint(os.stat(journal, dir_fd=root, follow_symlinks=False)) != _fingerprint(os.fstat(fd)):
                raise _Unsafe()
            _sync_directory(root)
            return journal
        except Exception:
            if not published:
                try:
                    current = os.stat(temporary, dir_fd=root, follow_symlinks=False)
                    _private(current)
                    if (_inode(current) == created and
                            _fingerprint(current) == _fingerprint(os.fstat(fd))):
                        os.unlink(temporary, dir_fd=root)
                        _sync_directory(root)
                except (OSError, _Unsafe):
                    pass  # Preserve replacements and the original failure.
            raise
        finally:
            os.close(fd)
            # SIGKILL leaves unpublished/ambiguous files unknown; no guessed repair.

    def _evict(self, root, runs, journal, candidate=None):
        """Resume a committed eviction, checking every remaining identity before unlink."""
        directory = lock = None
        owned = candidate is None
        receipt = _file(root, journal)
        try:
            receipt_info = os.fstat(receipt)
            intent = _eviction_intent(_read(root, journal, EVICTION_MAX_BYTES))
            name, files = intent['run_id'], intent['files']
            quarantine = '.evicted-' + journal[len('.eviction-'):-len('.json')]

            def check_receipt():
                info = os.stat(journal, dir_fd=root, follow_symlinks=False)
                _private(info)
                if _fingerprint(info) != _fingerprint(receipt_info):
                    raise _Unsafe()

            check_receipt()
            try:
                info = os.stat(quarantine, dir_fd=root, follow_symlinks=False)
            except FileNotFoundError:
                info = None
            if info is None:
                try:
                    original = os.stat(name, dir_fd=runs, follow_symlinks=False)
                except FileNotFoundError:
                    original = None
                if original is not None:
                    _private(original, True)
                    if _eviction_fingerprint(original) != intent['directory']:
                        raise _Unsafe()
                    if candidate is None:
                        candidate = self._candidate(runs, name, time.time())
                        if candidate is None:
                            raise _Unsafe()
                    directory, lock = candidate[1:3]
                    self._check_candidate(runs, candidate)
                    if (not candidate[6] or candidate[3] != set(files) or any(
                            _eviction_fingerprint(os.stat(filename, dir_fd=directory,
                                                         follow_symlinks=False)) != files[filename]
                            for filename in files)):
                        raise _Unsafe()
                    check_receipt()
                    # Move the verified, locked run out of count accounting before
                    # any unlink. Root byte/scan accounting still covers it.
                    _publish_run_directory(runs, name, root, quarantine)
                    _sync_directory(root)
                    _sync_directory(runs)
                    info = os.stat(quarantine, dir_fd=root, follow_symlinks=False)
            if info is not None:
                _private(info, True)
                if _eviction_fingerprint(info)[:4] != intent['directory'][:4]:
                    raise _Unsafe()
                if directory is not None:
                    pass  # Recovery already acquired the original run lock above.
                elif candidate is not None:
                    directory, lock = candidate[1:3]
                else:
                    directory = _directory(root, quarantine)
                    if '.run.lock' in os.listdir(directory):
                        lock = _file(directory, '.run.lock')
                        import fcntl
                        try:
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            return False  # Active data is never examined/deleted.

                def check_remaining():
                    check_receipt()
                    current = os.stat(quarantine, dir_fd=root, follow_symlinks=False)
                    _private(current, True)
                    # APFS changes directory nlink as files are removed; size and
                    # timestamps also change. Inventory/file fingerprints below
                    # authorize those mutations, never a replacement inode/mode/uid.
                    if (_eviction_fingerprint(current)[:4] != intent['directory'][:4] or
                            _inode(current) != _inode(os.fstat(directory))):
                        raise _Unsafe()
                    remaining = set(os.listdir(directory))
                    if not remaining <= set(files) or (remaining and '.run.lock' not in remaining):
                        raise _Unsafe()
                    for filename in remaining:
                        current = os.stat(filename, dir_fd=directory, follow_symlinks=False)
                        _private(current)
                        if _eviction_fingerprint(current) != files[filename]:
                            raise _Unsafe()
                    if '.run.lock' in remaining and (lock is None or
                            _eviction_fingerprint(os.fstat(lock)) != files['.run.lock']):
                        raise _Unsafe()
                    return remaining

                remaining = check_remaining()
                # Retain the verified lock through every data-file deletion.
                for filename in sorted(remaining - {'.run.lock'}) + (['.run.lock'] if '.run.lock' in remaining else []):
                    if filename not in check_remaining():
                        raise _Unsafe()
                    os.unlink(filename, dir_fd=directory)
                if check_remaining():
                    raise _Unsafe()
                if lock is not None:
                    # The verified open lock/directory pin this incarnation.
                    os.rmdir(quarantine, dir_fd=root)
                # A lockless empty quarantine after restart cannot prove its
                # incarnation. Preserve it, never infer permission to rmdir from
                # a filename/inode/owner/mode. It uses zero run slots/bytes and
                # remains charged to the root scan budget (N1).
            # Receipt survives through rename/rmdir and durability boundaries.
            _sync_directory(runs)
            _sync_directory(root)
            check_receipt()
            os.unlink(journal, dir_fd=root)
            _sync_directory(root)
            return True
        finally:
            failure = None
            for fd in ((lock, directory, receipt) if owned else (receipt,)):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError as exc:
                        failure = exc
            if failure is not None:
                raise failure

    def _remove(self, root, runs, candidate):
        self._check_candidate(runs, candidate)
        journal = self._begin_eviction(root, runs, candidate)
        self._evict(root, runs, journal, candidate)

    def _capacity(self, root, runs):
        settings, now = self.settings, time.time()
        # Bound descriptor use: recover one durable receipt at a time under root EX.
        root_names = os.listdir(root)
        if len(root_names) > SCAN_LIMIT:
            raise _Full()
        for name in sorted(root_names):
            if name.startswith('.eviction-') and name.endswith('.json'):
                token = name[len('.eviction-'):-len('.json')]
                try:
                    d.validate_run_id(token)
                except ValueError:
                    continue  # Unknown names are capacity-accounted, never deleted.
                if not self._evict(root, runs, name):
                    raise _Full()
        _cleanup_empty_stages(root, root_names)
        names = os.listdir(runs)
        if len(names) > SCAN_LIMIT:
            raise _Full()
        candidates = []
        for name in names:
            item = self._candidate(runs, name, now)
            if item is not None:
                # Keep only metadata, never descriptors for the whole inventory.
                saved = (item[0], None, None, set(item[3]), *item[4:])
                self._close_candidate(item)
                if saved[5] and '.reservation' in saved[3]:
                    item = self._reopen_candidate(runs, saved, now)
                    if item is not None:
                        try:
                            self._check_candidate(runs, item)
                            os.unlink('.reservation', dir_fd=item[1])
                            item[3].remove('.reservation')
                            _sync_directory(item[1])
                            saved = (item[0], None, None, set(item[3]), *item[4:7],
                                     self._snapshot(item[1], item[3]))
                        finally:
                            self._close_candidate(item)
                candidates.append(saved)
        candidates.sort(key=lambda item: (item[4], item[0]))

        def remove(saved):
            item = self._reopen_candidate(runs, saved, now)
            if item is None:
                return False
            try:
                self._remove(root, runs, item)
                return True
            finally:
                self._close_candidate(item)

        # Age cleanup and abandoned recovery precede count/byte pressure.
        deleted = set()
        for item in candidates:
            if item[6] and (not item[5] or now - item[4] > settings.max_age_days * 86400):
                if remove(item):
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
                if remove(item):
                    count, total = usage()
        if count >= settings.max_runs or total + settings.max_run_bytes > settings.max_total_bytes:
            raise _Full()


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
                # Durable publication is the commit point. Reservation cleanup
                # is maintenance; a later capacity pass reconciles it safely.
                self.persisted = True
                with _store_lock(self.store_lock):
                    os.unlink('.reservation', dir_fd=self.directory)
                    _sync_directory(self.directory)
                return True
            except Exception as exc:
                if not self.persisted:
                    self._warn(exc)
                return self.persisted
            finally:
                # Temp artifacts after a failure are deliberately unknown to cleanup.
                self.close()

    def close(self):
        with self._lock:
            if not self.closed:
                self.closed = True
                for fd in reversed(self._fds):
                    try:
                        os.close(fd)
                    except Exception as exc:
                        # Attempt every remaining release even if one close fails.
                        self._warn(exc)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
