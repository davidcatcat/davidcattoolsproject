"""Read-only classification of gw-agent-sessions homes."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import stat

from .developer_routes import RouteError
from .developer_routes.gw_agent import (
    GwAgentSessionLock,
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


def _orphan_newest_mtime(home: Path) -> float:
    """Return the newest mtime among home and its files without following symlinks."""
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
                # 1. Safety check: name, symlink, directory, and private permissions.
                if _TASK_ID.fullmatch(executor_task_id) is None:
                    results.append({'executor_task_id': executor_task_id, 'state': 'SKIPPED_UNSAFE'})
                    continue
                if not _is_private_dir(entry):
                    results.append({'executor_task_id': executor_task_id, 'state': 'SKIPPED_UNSAFE'})
                    continue

                # 2. Lock check: try lock without creating lock file if missing.
                lock_file = entry / _SESSION_LOCK_FILE
                is_locked = False
                if lock_file.lexists():
                    lock = GwAgentSessionLock(entry)
                    try:
                        lock.acquire()
                    except RouteError as exc:
                        if exc.code == 'GW_AGENT_SESSION_LOCKED':
                            is_locked = True
                    finally:
                        lock.release()

                if is_locked:
                    results.append({'executor_task_id': executor_task_id, 'state': 'LOCKED'})
                    continue

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
                        newest_mtime = _orphan_newest_mtime(entry)
                        if effective_now - newest_mtime > ORPHAN_TTL_SECONDS:
                            state = 'ORPHAN_EXPIRED'
                        else:
                            state = 'ORPHAN_RECENT'
                    except Exception:
                        state = 'OPEN'
                else:
                    if read_error or task_record is None:
                        state = 'OPEN'
                    else:
                        try:
                            derived = derive_state(task_record)
                            if derived in ('COMPLETED', 'ABANDONED'):
                                state = 'ENDED'
                            else:
                                state = 'OPEN'
                        except Exception:
                            state = 'OPEN'

                results.append({'executor_task_id': executor_task_id, 'state': state})

            except Exception:
                results.append({'executor_task_id': executor_task_id, 'state': 'OPEN'})

        return results

    except Exception:
        return []
