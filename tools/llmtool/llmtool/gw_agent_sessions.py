"""Classification and sweeping of gw-agent-sessions homes."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import stat
import subprocess
import sys

from .developer_routes import RouteError
from .developer_routes.gw_agent import (
    GwAgentSessionLock,
    _HAS_SAFE_PATH_FLAG,
    _SAFE_PATH_LAUNCHER,
    _SESSION_HOME_PARENT_DIR,
    _SESSION_LOCK_FILE,
    _TASK_ID,
    _is_private_dir,
    read_session_binding,
)
from .task_status import derive_state

ORPHAN_TTL_SECONDS = 7 * 24 * 60 * 60
_MAX_TRAVERSAL_LEVELS = 8
_MAX_TRAVERSAL_ENTRIES = 10000


def _orphan_newest_mtime(home: Path, home_mtime: float | None = None) -> float:
    """Return the newest mtime among home and its files without following symlinks."""
    if home_mtime is not None:
        newest = float(home_mtime)
    else:
        newest = home.lstat().st_mtime
    entries_visited = 1
    stack = [(home, 0)]
    while stack and entries_visited < _MAX_TRAVERSAL_ENTRIES:
        current_dir, depth = stack.pop()
        if depth >= _MAX_TRAVERSAL_LEVELS:
            continue
        try:
            with os.scandir(current_dir) as scanner:
                for entry in scanner:
                    if entries_visited >= _MAX_TRAVERSAL_ENTRIES:
                        break
                    entries_visited += 1
                    try:
                        # Lock file never counts toward orphan age.
                        if depth == 0 and entry.name == _SESSION_LOCK_FILE:
                            continue
                        stat_res = entry.stat(follow_symlinks=False)
                        if stat_res.st_mtime > newest:
                            newest = stat_res.st_mtime
                        if entry.is_dir(follow_symlinks=False):
                            stack.append((Path(entry.path), depth + 1))
                    except OSError:
                        continue
        except OSError:
            continue
    return newest


def _classify_single_home(
    entry: Path,
    store,
    effective_now: float,
    *,
    check_lock: bool = True,
    pre_lock_home_mtime: float | None = None,
) -> str:
    """Classify a single session home directory."""
    executor_task_id = entry.name
    # 1. Safety check: name, symlink, directory, and private permissions.
    if _TASK_ID.fullmatch(executor_task_id) is None:
        return 'SKIPPED_UNSAFE'
    if not _is_private_dir(entry):
        return 'SKIPPED_UNSAFE'

    # 2. Lock check: try lock without creating lock file if missing.
    if check_lock:
        lock_file = entry / _SESSION_LOCK_FILE
        is_locked = False
        if os.path.lexists(lock_file):
            lock = GwAgentSessionLock(entry)
            try:
                lock.acquire()
            except RouteError as exc:
                if exc.code == 'GW_AGENT_SESSION_LOCKED':
                    is_locked = True
            finally:
                lock.release()

        if is_locked:
            return 'LOCKED'

    # 3. Binding check: missing, invalid, or missing root task makes home an orphan.
    is_orphan = False
    binding = None
    try:
        binding = read_session_binding(entry, executor_task_id=executor_task_id)
    except Exception:
        is_orphan = True

    task_record = None
    read_error = False
    if not is_orphan and binding is not None:
        root_task_id = binding.get('root_task_id')
        task_exists = False

        if hasattr(store, 'task_ids'):
            try:
                task_ids = set(store.task_ids())
                if root_task_id not in task_ids:
                    is_orphan = True
                else:
                    task_exists = True
            except Exception:
                pass

        if not is_orphan:
            try:
                task_record = store.get(root_task_id)
                if task_record is None:
                    if not task_exists:
                        is_orphan = True
                    else:
                        read_error = True
            except (KeyError, FileNotFoundError):
                is_orphan = True
            except Exception:
                read_error = True

    # 4. State resolution: orphan age or task lifecycle state.
    if is_orphan:
        try:
            newest_mtime = _orphan_newest_mtime(entry, home_mtime=pre_lock_home_mtime)
            if effective_now - newest_mtime > ORPHAN_TTL_SECONDS:
                return 'ORPHAN_EXPIRED'
            return 'ORPHAN_RECENT'
        except Exception:
            return 'OPEN'

    if read_error or task_record is None:
        return 'OPEN'

    try:
        derived = derive_state(task_record)
        if derived in ('COMPLETED', 'ABANDONED'):
            return 'ENDED'
        return 'OPEN'
    except Exception:
        return 'OPEN'


def classify_homes(store, now=None):
    """List and classify gw-agent-sessions homes without altering disk state."""
    try:
        if store is None or not hasattr(store, 'root') or store.root is None:
            return []
        sessions_dir = Path(store.root).parent / _SESSION_HOME_PARENT_DIR
        try:
            if not sessions_dir.is_dir() or sessions_dir.is_symlink():
                return []
            entries = sorted(sessions_dir.iterdir(), key=lambda p: p.name)
        except OSError:
            return []

        results = []
        effective_now = (
            datetime.now(timezone.utc).timestamp()
            if now is None
            else float(now)
        )

        for entry in entries:
            executor_task_id = entry.name
            try:
                state = _classify_single_home(entry, store, effective_now, check_lock=True)
                results.append({'executor_task_id': executor_task_id, 'state': state})
            except Exception:
                results.append({'executor_task_id': executor_task_id, 'state': 'OPEN'})

        return results

    except Exception:
        return []


def _remove_home(home: Path, sessions_dir: Path, session_id: str | None = None, lock: GwAgentSessionLock | None = None) -> None:
    """Safely remove a session home, unlinking sensitive session files first."""
    try:
        # Check boundary: home must be directly inside sessions_dir.
        if home.parent.resolve() != sessions_dir.resolve():
            return
    except OSError:
        return

    # A symlink home is unlinked as a link and never resolved.
    if os.path.islink(home):
        try:
            if lock is not None:
                lock.release()
            os.unlink(home)
        except OSError:
            pass
        return

    # 1. Unlink sessions/<id>/history.json and meta.json first (history is sensitive).
    if session_id:
        sess_dir = home / 'sessions' / session_id
        for name in ('history.json', 'meta.json'):
            target = sess_dir / name
            try:
                if os.path.lexists(target):
                    os.unlink(target)
            except OSError:
                pass

    # Check any sessions subdirectories for history.json and meta.json.
    sessions_sub = home / 'sessions'
    if sessions_sub.is_dir() and not sessions_sub.is_symlink():
        try:
            with os.scandir(sessions_sub) as sess_scanner:
                for sess_entry in sess_scanner:
                    if sess_entry.is_dir(follow_symlinks=False):
                        for name in ('history.json', 'meta.json'):
                            target = Path(sess_entry.path) / name
                            try:
                                if os.path.lexists(target):
                                    os.unlink(target)
                            except OSError:
                                pass
        except OSError:
            pass

    for name in ('history.json', 'meta.json'):
        target = home / name
        try:
            if os.path.lexists(target):
                os.unlink(target)
        except OSError:
            pass

    # 2. Walk depth at most 8 and 10000 entries, unlinking entries bottom-up.
    lock_file = home / _SESSION_LOCK_FILE
    entries_visited = 0
    stack = [(home, 0)]
    all_subdirs = []

    while stack and entries_visited < _MAX_TRAVERSAL_ENTRIES:
        cur_dir, depth = stack.pop()
        if depth >= _MAX_TRAVERSAL_LEVELS:
            continue
        try:
            with os.scandir(cur_dir) as scanner:
                for entry in scanner:
                    entries_visited += 1
                    if entries_visited > _MAX_TRAVERSAL_ENTRIES:
                        break
                    entry_path = Path(entry.path)
                    try:
                        if not entry_path.is_relative_to(sessions_dir):
                            continue
                    except AttributeError:
                        if sessions_dir not in entry_path.parents:
                            continue

                    try:
                        if entry.is_symlink():
                            os.unlink(entry_path)
                        elif entry.is_dir(follow_symlinks=False):
                            all_subdirs.append(entry_path)
                            stack.append((entry_path, depth + 1))
                        else:
                            if entry_path != lock_file:
                                os.unlink(entry_path)
                    except OSError:
                        continue
        except OSError:
            continue

    # Remove subdirectories bottom-up.
    for subdir in reversed(all_subdirs):
        try:
            os.rmdir(subdir)
        except OSError:
            pass

    # Release lock before unlinking lock file and removing home directory.
    if lock is not None:
        try:
            lock.release()
        except Exception:
            pass

    if os.path.lexists(lock_file):
        try:
            os.unlink(lock_file)
        except OSError:
            pass

    try:
        os.rmdir(home)
    except OSError:
        pass


def sweep(store, now=None, *, runner=None, package_path=None):
    """Sweep gw-agent-sessions homes: end and remove ENDED homes, remove expired orphans."""
    try:
        if store is None or not hasattr(store, 'root') or store.root is None:
            return []
        sessions_dir = Path(store.root).parent / _SESSION_HOME_PARENT_DIR
        try:
            if not sessions_dir.is_dir() or sessions_dir.is_symlink():
                return []
        except OSError:
            return []

        effective_now = (
            datetime.now(timezone.utc).timestamp()
            if now is None
            else float(now)
        )

        classified = classify_homes(store, now=effective_now)
        results = []

        for item in classified:
            executor_task_id = item['executor_task_id']
            initial_state = item['state']
            home = sessions_dir / executor_task_id

            if initial_state == 'SKIPPED_UNSAFE':
                results.append({'executor_task_id': executor_task_id, 'outcome': 'SKIPPED_UNSAFE'})
                continue

            if initial_state == 'LOCKED':
                results.append({'executor_task_id': executor_task_id, 'outcome': 'SKIPPED_LOCKED'})
                continue

            if initial_state in ('OPEN', 'ORPHAN_RECENT'):
                results.append({'executor_task_id': executor_task_id, 'outcome': 'SKIPPED_OPEN'})
                continue

            if initial_state not in ('ENDED', 'ORPHAN_EXPIRED'):
                results.append({'executor_task_id': executor_task_id, 'outcome': 'SKIPPED_OPEN'})
                continue

            # Measure home mtime before taking lock so lock creation does not make orphans recent.
            pre_lock_home_mtime = None
            try:
                pre_lock_home_mtime = home.lstat().st_mtime
            except OSError:
                pass

            # Hold lock for the whole end-and-remove; non-blocking.
            lock = GwAgentSessionLock(home)
            try:
                lock.acquire()
            except RouteError as exc:
                if exc.code == 'GW_AGENT_SESSION_LOCKED':
                    results.append({'executor_task_id': executor_task_id, 'outcome': 'SKIPPED_LOCKED'})
                else:
                    results.append({'executor_task_id': executor_task_id, 'outcome': 'SKIPPED_UNSAFE'})
                continue
            except Exception:
                results.append({'executor_task_id': executor_task_id, 'outcome': 'SKIPPED_UNSAFE'})
                continue

            try:
                # Re-check state under lock.
                rechecked = _classify_single_home(
                    home,
                    store,
                    effective_now,
                    check_lock=False,
                    pre_lock_home_mtime=pre_lock_home_mtime,
                )
                if rechecked not in ('ENDED', 'ORPHAN_EXPIRED'):
                    lock.release()
                    if rechecked == 'SKIPPED_UNSAFE':
                        results.append({'executor_task_id': executor_task_id, 'outcome': 'SKIPPED_UNSAFE'})
                    else:
                        results.append({'executor_task_id': executor_task_id, 'outcome': 'SKIPPED_OPEN'})
                    continue

                session_id = None
                try:
                    binding = read_session_binding(home, executor_task_id=executor_task_id)
                    if isinstance(binding, dict):
                        session_id = binding.get('session_id')
                except Exception:
                    pass

                if rechecked == 'ENDED':
                    end_failed = False
                    if session_id:
                        cmd_args = ['end', session_id, '--reason', 'task_terminal', '--state-dir', str(home)]
                        if _HAS_SAFE_PATH_FLAG:
                            argv = [sys.executable, '-B', '-P', '-m', 'api_agent_gateway'] + cmd_args
                        else:
                            argv = [sys.executable, '-B', '-c', _SAFE_PATH_LAUNCHER] + cmd_args

                        env = {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG') if k in os.environ}
                        if package_path is not None:
                            env['PYTHONPATH'] = str(package_path)

                        try:
                            if runner is None:
                                res = subprocess.run(
                                    argv,
                                    env=env,
                                    stdin=subprocess.DEVNULL,
                                    timeout=30,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                )
                            else:
                                res = runner(argv, env=env, stdin=subprocess.DEVNULL, timeout=30)
                            if hasattr(res, 'returncode') and res.returncode != 0:
                                end_failed = True
                            elif isinstance(res, int) and res != 0:
                                end_failed = True
                        except Exception:
                            end_failed = True
                    else:
                        end_failed = True

                    _remove_home(home, sessions_dir, session_id=session_id, lock=lock)
                    outcome = 'REMOVED_AFTER_END_FAILURE' if end_failed else 'ENDED'
                    results.append({'executor_task_id': executor_task_id, 'outcome': outcome})

                elif rechecked == 'ORPHAN_EXPIRED':
                    _remove_home(home, sessions_dir, session_id=session_id, lock=lock)
                    results.append({'executor_task_id': executor_task_id, 'outcome': 'ORPHAN_REMOVED'})

            except Exception:
                try:
                    lock.release()
                except Exception:
                    pass
                results.append({'executor_task_id': executor_task_id, 'outcome': 'SKIPPED_OPEN'})

        return results

    except Exception:
        return []
