"""End and remove gw-agent session homes whose Task has ended (#120 G1c).

Each home is ``Path(store.root).parent / 'gw-agent-sessions' / <executor task id>``
(mode 0700). It holds ``binding.json``, a ``lock`` file and the gateway state
(``sessions/<session_id>/meta.json`` and ``history.json``). History is sensitive:
this module never reads it, prints it or records it anywhere.

A home is ended and removed only when its Task has ended, or when it is an orphan
(missing or invalid binding, or no root Task record) older than the TTL. Any other
state, including UNKNOWN or an unreadable record, is open and the home is never
touched. Every filesystem step works through directory descriptors opened with
``O_NOFOLLOW``: a symlink is unlinked as a link and never resolved, and nothing
outside the ``gw-agent-sessions`` directory is touched.

The gateway works by path names, so it is run only on a plain home: every entry
under it is a real directory or a single-link regular file owned by this user.
A home holding a symlink, hard link, FIFO, socket or device anywhere is never
handed to the gateway; it is cleaned here, by descriptor, as an end failure.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

try:
    import fcntl
except ImportError:  # pragma: no cover - llmtool runs on macOS and Linux
    fcntl = None

from . import task_completion
from . import task_status

SESSIONS_DIR_NAME = 'gw-agent-sessions'
ORPHAN_TTL_SECONDS = 7 * 24 * 60 * 60
END_TIMEOUT_SECONDS = 30
# The D6 lifecycle state of a retired registration. A missing registry record
# or a missing registered root also counts as an ended D6.
_D6_RETIRED_STATES = frozenset(('RETIRED',))

ENDED = 'ENDED'
REMOVED_AFTER_END_FAILURE = 'REMOVED_AFTER_END_FAILURE'
ORPHAN_REMOVED = 'ORPHAN_REMOVED'
SKIPPED_OPEN = 'SKIPPED_OPEN'
SKIPPED_LOCKED = 'SKIPPED_LOCKED'

_HOME_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')
_TASK_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')
_SESSION_ID = re.compile(r'[0-9a-f]{32}\Z')
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')
_BINDING_LIMIT = 64 * 1024
_MAX_DEPTH = 8
_MAX_ENTRIES = 10000
_CLOEXEC = getattr(os, 'O_CLOEXEC', 0)
_DIR_FLAGS = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | os.O_NOFOLLOW | _CLOEXEC
_GATEWAY_ENV_KEYS = ('PATH', 'HOME', 'LANG')
_UNRESOLVED = object()  # the gateway package is resolved once per sweep, on first need


class _Kept(Exception):
    """Internal: the home must stay as it is."""


def sweep(store, now=None, *, runner=None, package_path=None):
    """End and remove session homes whose Task has ended; return content-free outcomes.

    ``runner(argv, *, env, timeout, cwd)`` runs the gateway and returns its exit
    code; the default runs it as a subprocess with no captured output.
    ``package_path`` overrides the configured gateway package (tests only).
    This function never raises: any failure leaves the affected home in place.
    """
    results = []
    try:
        _sweep(store, time.time() if now is None else now, runner or _run_gateway,
               package_path, results)
    except Exception:
        pass
    return results


def _sweep(store, now, runner, package_path, results):
    base = Path(store.root).parent / SESSIONS_DIR_NAME
    try:
        base_fd = os.open(base, _DIR_FLAGS)
    except OSError:
        return
    try:
        info = os.fstat(base_fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            return
        try:
            task_ids = frozenset(store.task_ids())
        except Exception:
            task_ids = None  # existence is unknown, so no binding counts as an orphan
        package = _UNRESOLVED
        for name in sorted(os.listdir(base_fd)):
            if _HOME_NAME.fullmatch(name) is None:
                continue
            try:
                outcome, package = _sweep_home(store, base, base_fd, name, now, task_ids,
                                               runner, package_path, package)
            except Exception:
                continue
            if outcome is not None:
                results.append({'executor_task_id': name, 'outcome': outcome})
    finally:
        os.close(base_fd)


def _sweep_home(store, base, base_fd, name, now, task_ids, runner, package_path, package):
    info = os.lstat(name, dir_fd=base_fd)
    if stat.S_ISLNK(info.st_mode):
        # Never a real home: its binding cannot be read without following it.
        if now - info.st_mtime > ORPHAN_TTL_SECONDS:
            os.unlink(name, dir_fd=base_fd)
            return ORPHAN_REMOVED, package
        return SKIPPED_OPEN, package
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        return None, package
    home_fd = os.open(name, _DIR_FLAGS, dir_fd=base_fd)
    try:
        opened = os.fstat(home_fd)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            return None, package
        binding = _read_binding(home_fd, name)
        if binding is None:
            state = 'ORPHAN'
        else:
            state = _task_state(store, binding, task_ids)
        if state == 'OPEN':
            return SKIPPED_OPEN, package
        if state == 'ORPHAN':
            try:
                newest = _newest_mtime(base_fd, name, info, 0, [0])
            except (OSError, _Kept):
                return SKIPPED_OPEN, package
            if now - newest <= ORPHAN_TTL_SECONDS:
                return SKIPPED_OPEN, package
        lock_fd = _try_lock(home_fd)
        if lock_fd is None:
            return SKIPPED_LOCKED, package
        try:
            # A valid binding always holds a valid session_id; an invalid one is None.
            session_id = binding['session_id'] if binding is not None else None
            ended = True
            if state == 'ENDED' and session_id is not None:
                # The gateway follows path names: hand it only a plain home that is
                # still the entry checked above. Anything else is an end failure.
                ended = False
                if _state_is_plain(home_fd) and _still_entry(base_fd, name, info):
                    if package is _UNRESOLVED:
                        package = _gateway_package(package_path)
                    ended = _end_session(base / name, session_id, package, runner)
            if session_id is not None:
                _unlink_session_files(home_fd, session_id)
            if not _remove_entry(base_fd, name, 0):
                return None, package  # retried by the next sweep
        finally:
            _release(lock_fd)
        if state == 'ORPHAN':
            return ORPHAN_REMOVED, package
        return (ENDED if ended else REMOVED_AFTER_END_FAILURE), package
    finally:
        os.close(home_fd)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _read_binding(home_fd, name):
    """Return a validated binding, or None when it is missing or invalid."""
    try:
        fd = os.open('binding.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | _CLOEXEC,
                     dir_fd=home_fd)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                info.st_size > _BINDING_LIMIT):
            return None
        chunks, size = [], 0
        while size <= _BINDING_LIMIT:
            chunk = os.read(fd, _BINDING_LIMIT + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    except OSError:
        return None
    finally:
        os.close(fd)
    if size > _BINDING_LIMIT:
        return None
    try:
        value = json.loads(b''.join(chunks).decode('utf-8'), object_pairs_hook=_unique_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, RecursionError):
        return None
    if type(value) is not dict:
        return None
    # A missing, null or malformed session_id makes the binding invalid, so the
    # home is an orphan: removed only after the TTL and never ended early.
    if (type(value.get('schema_version')) is not int or value['schema_version'] != 1 or
            type(value.get('root_task_id')) is not str or
            _TASK_ID.fullmatch(value['root_task_id']) is None or
            value.get('executor_task_id') != name or
            type(value.get('session_id')) is not str or
            _SESSION_ID.fullmatch(value['session_id']) is None or
            type(value.get('data_class')) is not str or not value['data_class'] or
            type(value.get('scope_digest')) is not str or
            _DIGEST.fullmatch(value['scope_digest']) is None):
        return None
    return value


def _task_state(store, binding, task_ids):
    """Return ENDED, ORPHAN or OPEN. Anything not proven ended or missing is OPEN."""
    root_task_id = binding['root_task_id']
    if task_ids is not None and root_task_id not in task_ids:
        return 'ORPHAN'
    try:
        record = store.get(root_task_id)
    except Exception:
        return 'OPEN'
    try:
        derived = task_status.derive_state(record)
    except Exception:
        derived = 'UNKNOWN'
    if derived in ('COMPLETED', 'ABANDONED'):
        return 'ENDED'
    if type(record) is dict and _d6_ended(binding['executor_task_id'], record):
        return 'ENDED'
    return 'OPEN'


def _d6_ended(executor_task_id, record):
    completion = record.get('completion')
    evidence = completion.get('evidence') if type(completion) is dict else None
    common_dir = evidence.get('common_dir') if type(evidence) is dict else None
    if type(common_dir) is not str:
        return False
    try:
        registration = task_completion.read_d6_registration(
            executor_task_id, common_dir,
            allowed_states=tuple(sorted(task_completion._D6_EVIDENCE_STATES |
                                        _D6_RETIRED_STATES)))
    except Exception as exc:
        code = getattr(exc, 'completion_code', None)
        if code == 'D6_ROOT_MISSING':
            return True
        if code == 'D6_REGISTRY_UNAVAILABLE':
            return _registry_record_missing(executor_task_id, common_dir)
        return False
    return getattr(registration, 'state', None) in _D6_RETIRED_STATES


def _registry_record_missing(executor_task_id, common_dir):
    """True only when the registry is present and this Task's record is absent."""
    try:
        if (not os.path.isabs(common_dir) or os.path.realpath(common_dir) != common_dir or
                not common_dir.endswith('/.git')):
            return False
        registry = Path(common_dir) / 'llmtool-worktrees'
        info = registry.lstat()
    except (OSError, ValueError):
        return False
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        return False
    try:
        (registry / (executor_task_id + '.json')).lstat()
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False
    return False


def _newest_mtime(parent_fd, name, info, depth, count):
    """Newest mtime in a home, by lstat only. Too deep or too large keeps it."""
    count[0] += 1
    if count[0] > _MAX_ENTRIES:
        raise _Kept()
    newest = info.st_mtime
    if not stat.S_ISDIR(info.st_mode):
        return newest
    if depth >= _MAX_DEPTH:
        raise _Kept()
    fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    try:
        for child in os.listdir(fd):
            child_info = os.lstat(child, dir_fd=fd)
            newest = max(newest, _newest_mtime(fd, child, child_info, depth + 1, count))
    finally:
        os.close(fd)
    return newest


def _state_is_plain(home_fd):
    """True only when the gateway cannot follow or block on anything in the home.

    Every entry, at any depth, must be a real directory or a regular file with a
    single link, owned by this user. A symlink, hard link, FIFO, socket or device,
    an unreadable entry, or a tree too deep or too large makes the home unsafe.
    """
    try:
        return _plain_tree(home_fd, 0, [0])
    except (OSError, _Kept):
        return False


def _plain_tree(dir_fd, depth, count):
    if depth >= _MAX_DEPTH:
        raise _Kept()
    uid = os.getuid()
    for child in os.listdir(dir_fd):
        count[0] += 1
        if count[0] > _MAX_ENTRIES:
            raise _Kept()
        info = os.lstat(child, dir_fd=dir_fd)
        if info.st_uid != uid:
            return False
        if stat.S_ISREG(info.st_mode):
            if info.st_nlink != 1:
                return False
            continue
        if not stat.S_ISDIR(info.st_mode):
            return False
        fd = os.open(child, _DIR_FLAGS, dir_fd=dir_fd)
        try:
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                return False
            if not _plain_tree(fd, depth + 1, count):
                return False
        finally:
            os.close(fd)
    return True


def _still_entry(base_fd, name, info):
    """True when ``name`` in the sessions dir is still the real directory checked."""
    try:
        current = os.lstat(name, dir_fd=base_fd)
    except OSError:
        return False
    return (stat.S_ISDIR(current.st_mode) and
            (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino))


def _try_lock(home_fd):
    """Take the home lock without blocking; None means a run holds it."""
    if fcntl is None:
        return None
    try:
        if stat.S_ISLNK(os.lstat('lock', dir_fd=home_fd).st_mode):
            os.unlink('lock', dir_fd=home_fd)  # a link is never a real lock file
    except FileNotFoundError:
        pass
    except OSError:
        return None
    try:
        fd = os.open('lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | _CLOEXEC, 0o600,
                     dir_fd=home_fd)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _release(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    os.close(fd)


def _gateway_package(package_path=None):
    """Return the configured gateway package directory, or None when unsafe."""
    try:
        if package_path is None:
            from .developer_routes import load_route_config
            package_path = load_route_config()['routes']['gw-agent']['package_path']
        if type(package_path) is not str:
            package_path = os.fspath(package_path)
        if package_path.startswith('~/'):
            package_path = os.path.expanduser(package_path)
        if not os.path.isabs(package_path):
            return None
        resolved = Path(package_path).resolve(strict=True)
        for directory in (resolved, resolved / 'api_agent_gateway'):
            info = directory.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
                    stat.S_IMODE(info.st_mode) & 0o022):
                return None
        return str(resolved)
    except Exception:
        return None


def gateway_env(package):
    """The gateway end environment: exactly PATH, HOME, LANG and PYTHONPATH.

    PATH, HOME and LANG come from this process, with a fixed default when one is
    unset, so the key set never changes. PYTHONPATH names the configured package
    so ``python -P -m`` can import it; it is a directory path, never a credential.
    Nothing else from this process's environment is passed on.
    """
    env = {}
    for key in _GATEWAY_ENV_KEYS:
        value = os.environ.get(key)
        if type(value) is not str or not value or '\x00' in value:
            value = _gateway_env_default(key)
        env[key] = value
    env['PYTHONPATH'] = package
    return env


def _gateway_env_default(key):
    if key == 'PATH':
        return '/usr/bin:/bin'
    if key == 'LANG':
        return 'C'
    try:
        home = os.path.expanduser('~')
    except Exception:
        home = None
    if type(home) is str and os.path.isabs(home) and '\x00' not in home:
        return home
    return '/'


def _end_session(home, session_id, package, runner):
    if package is None:
        return False
    argv = [sys.executable, '-B', '-P', '-m', 'api_agent_gateway', 'end', session_id,
            '--reason', 'task_terminal', '--state-dir', str(home)]
    try:
        code = runner(argv, env=gateway_env(package), timeout=END_TIMEOUT_SECONDS,
                      cwd=str(home))
    except Exception:
        return False
    return type(code) is int and code == 0


def _run_gateway(argv, *, env, timeout, cwd):
    # Output is discarded: it never reaches llmtool's stdout, logs or evidence.
    completed = subprocess.run(argv, env=env, cwd=cwd, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=timeout, check=False)
    return completed.returncode


def _unlink_session_files(home_fd, session_id):
    """Unlink the session history and meta first; history is sensitive."""
    try:
        sessions_fd = os.open('sessions', _DIR_FLAGS, dir_fd=home_fd)
    except OSError:
        return
    try:
        try:
            session_fd = os.open(session_id, _DIR_FLAGS, dir_fd=sessions_fd)
        except OSError:
            return
        try:
            for file_name in ('history.json', 'meta.json'):
                try:
                    if not stat.S_ISDIR(os.lstat(file_name, dir_fd=session_fd).st_mode):
                        os.unlink(file_name, dir_fd=session_fd)
                except OSError:
                    pass
        finally:
            os.close(session_fd)
    finally:
        os.close(sessions_fd)


def _remove_entry(parent_fd, name, depth):
    """Remove one entry without following links; return True when it is gone."""
    try:
        info = os.lstat(name, dir_fd=parent_fd)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if not stat.S_ISDIR(info.st_mode):
        try:
            os.unlink(name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        except OSError:
            return False
        return True
    if depth >= _MAX_DEPTH:
        return False
    try:
        fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except OSError:
        return False
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            return False
        removed = True
        for child in os.listdir(fd):
            removed = _remove_entry(fd, child, depth + 1) and removed
    except OSError:
        return False
    finally:
        os.close(fd)
    if not removed:
        return False
    try:
        os.rmdir(name, dir_fd=parent_fd)
    except FileNotFoundError:
        pass
    except OSError:
        return False
    return True
