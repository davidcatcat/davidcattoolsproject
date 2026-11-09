"""Tests for classify_homes in gw_agent_sessions."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from llmtool.developer_routes.gw_agent import (
    GwAgentSessionLock,
    open_session_home,
    write_session_binding,
)
from llmtool.gw_agent_sessions import ORPHAN_TTL_SECONDS, classify_homes

try:
    from llmtool.task_store import TaskStore as _BaseTaskStore
except ImportError:
    _BaseTaskStore = object


class TaskStore(_BaseTaskStore):
    """TaskStore for testing with a temp root and synthetic task records."""

    def __init__(self, root):
        self.root = Path(root)
        self._tasks = {}
        try:
            super().__init__(root)
        except Exception:
            try:
                super().__init__()
            except Exception:
                pass

    def get(self, task_id):
        if task_id in self._tasks:
            return self._tasks[task_id]
        if hasattr(super(), 'get'):
            try:
                return super().get(task_id)
            except Exception:
                pass
        return None

    def task_ids(self):
        ids = set(self._tasks.keys())
        if hasattr(super(), 'task_ids'):
            try:
                ids.update(super().task_ids())
            except Exception:
                pass
        return list(ids)

    def put_synthetic_task(self, task_record):
        task_id = task_record['task_id']
        self._tasks[task_id] = dict(task_record)
        path = self.root / f'{task_id}.json'
        try:
            path.write_text(json.dumps(task_record) + '\n')
        except OSError:
            pass


class GwAgentSessionsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.base = Path(self.temp_dir.name)
        self.store_root = self.base / 'store' / 'task-store'
        self.store_root.mkdir(parents=True, mode=0o700)
        self.store = TaskStore(self.store_root)
        self.sessions_dir = self.store_root.parent / 'gw-agent-sessions'
        self.sessions_dir.mkdir(parents=True, mode=0o700)

        self.valid_session_id = '0123456789abcdef0123456789abcdef'
        self.valid_data_class = 'synthetic'
        self.valid_scope_digest = 'a' * 64

    def _create_home_with_binding(self, executor_id, root_id):
        home = open_session_home(self.store_root, executor_id)
        write_session_binding(
            home,
            root_task_id=root_id,
            executor_task_id=executor_id,
            session_id=self.valid_session_id,
            data_class=self.valid_data_class,
            scope_digest=self.valid_scope_digest,
        )
        return home

    def test_completed_and_abandoned_tasks_give_ended(self):
        root_completed = 'task-root-synthetic-comp-01'
        exec_completed = 'task-exec-synthetic-comp-01'
        self.store.put_synthetic_task({
            'task_id': root_completed,
            'state': 'COMPLETED',
            'completion_events': [{
                'source': 'GITHUB',
                'marker_type': 'merge',
                'marker_status': 'MERGED',
            }],
        })
        self._create_home_with_binding(exec_completed, root_completed)

        root_abandoned = 'task-root-synthetic-aban-02'
        exec_abandoned = 'task-exec-synthetic-aban-02'
        self.store.put_synthetic_task({
            'task_id': root_abandoned,
            'abandonment': {'reason': 'synthetic-abandonment'},
        })
        self._create_home_with_binding(exec_abandoned, root_abandoned)

        results = classify_homes(self.store)
        by_id = {item['executor_task_id']: item['state'] for item in results}

        self.assertEqual(by_id.get(exec_completed), 'ENDED')
        self.assertEqual(by_id.get(exec_abandoned), 'ENDED')

    def test_open_task_gives_open_even_when_30_days_old(self):
        root_open = 'task-root-synthetic-open-01'
        exec_open = 'task-exec-synthetic-open-01'
        self.store.put_synthetic_task({
            'task_id': root_open,
            'state': 'PM_PENDING',
        })
        home = self._create_home_with_binding(exec_open, root_open)

        fixed_now = 2_000_000.0
        old_time = fixed_now - 30 * 86400
        os.utime(home, (old_time, old_time))
        for child in home.iterdir():
            os.utime(child, (old_time, old_time))

        results = classify_homes(self.store, now=fixed_now)
        by_id = {item['executor_task_id']: item['state'] for item in results}

        self.assertEqual(by_id.get(exec_open), 'OPEN')

    def test_busy_lock_gives_locked(self):
        exec_locked = 'task-exec-synthetic-locked-01'
        home = open_session_home(self.store_root, exec_locked)

        lock = GwAgentSessionLock.acquire(home)
        try:
            results = classify_homes(self.store)
            by_id = {item['executor_task_id']: item['state'] for item in results}
            self.assertEqual(by_id.get(exec_locked), 'LOCKED')
        finally:
            lock.release()

        # After releasing lock, home is no longer reported as LOCKED.
        results_after = classify_homes(self.store)
        by_id_after = {item['executor_task_id']: item['state'] for item in results_after}
        self.assertNotEqual(by_id_after.get(exec_locked), 'LOCKED')

    def test_orphan_age_expired_and_recent(self):
        fixed_now = 2_000_000.0

        # Orphan 1: older than 7 days (8 days old).
        exec_expired = 'task-exec-synthetic-orphan-exp-01'
        home_expired = open_session_home(self.store_root, exec_expired)
        expired_time = fixed_now - 8 * 86400
        os.utime(home_expired, (expired_time, expired_time))

        # Orphan 2: newer than 7 days (1 day old).
        exec_recent = 'task-exec-synthetic-orphan-rec-02'
        home_recent = open_session_home(self.store_root, exec_recent)
        recent_time = fixed_now - 1 * 86400
        os.utime(home_recent, (recent_time, recent_time))

        results = classify_homes(self.store, now=fixed_now)
        by_id = {item['executor_task_id']: item['state'] for item in results}

        self.assertEqual(by_id.get(exec_expired), 'ORPHAN_EXPIRED')
        self.assertEqual(by_id.get(exec_recent), 'ORPHAN_RECENT')

    def test_symlinked_and_unsafe_homes_give_skipped_unsafe(self):
        # 1. Symlinked home directory.
        target_dir = self.base / 'target-home-synthetic'
        target_dir.mkdir(mode=0o700)
        symlink_name = 'task-exec-synthetic-sym-01'
        os.symlink(target_dir, self.sessions_dir / symlink_name)

        # 2. Non-directory file entry.
        file_name = 'task-exec-synthetic-file-02'
        (self.sessions_dir / file_name).write_text('content\n')

        # 3. Directory with non-private mode.
        group_name = 'task-exec-synthetic-mode-03'
        group_home = self.sessions_dir / group_name
        group_home.mkdir(mode=0o755)
        os.chmod(group_home, 0o755)

        # 4. Name that does not match task ID pattern.
        bad_name = '!invalid-name-04!'
        bad_home = self.sessions_dir / bad_name
        bad_home.mkdir(mode=0o700)

        results = classify_homes(self.store)
        by_id = {item['executor_task_id']: item['state'] for item in results}

        self.assertEqual(by_id.get(symlink_name), 'SKIPPED_UNSAFE')
        self.assertEqual(by_id.get(file_name), 'SKIPPED_UNSAFE')
        self.assertEqual(by_id.get(group_name), 'SKIPPED_UNSAFE')
        self.assertEqual(by_id.get(bad_name), 'SKIPPED_UNSAFE')

    def test_results_hold_no_session_id(self):
        root_completed = 'task-root-synthetic-keys-01'
        exec_completed = 'task-exec-synthetic-keys-01'
        self.store.put_synthetic_task({
            'task_id': root_completed,
            'state': 'COMPLETED',
            'completion_events': [{
                'source': 'GITHUB',
                'marker_type': 'merge',
                'marker_status': 'MERGED',
            }],
        })
        self._create_home_with_binding(exec_completed, root_completed)

        results = classify_homes(self.store)
        self.assertTrue(len(results) > 0)
        for item in results:
            self.assertEqual(set(item.keys()), {'executor_task_id', 'state'})
            self.assertNotIn('session_id', item)

    def test_nothing_on_disk_changes(self):
        # Create a mix of home entries: completed, locked, orphan, unsafe.
        root_task = 'task-root-synthetic-disk-01'
        exec_task = 'task-exec-synthetic-disk-01'
        self.store.put_synthetic_task({
            'task_id': root_task,
            'state': 'COMPLETED',
            'completion_events': [{
                'source': 'GITHUB',
                'marker_type': 'merge',
                'marker_status': 'MERGED',
            }],
        })
        self._create_home_with_binding(exec_task, root_task)

        exec_orphan = 'task-exec-synthetic-disk-orphan'
        open_session_home(self.store_root, exec_orphan)

        exec_locked = 'task-exec-synthetic-disk-locked'
        locked_home = open_session_home(self.store_root, exec_locked)
        # Create an existing lock file.
        lock_file = locked_home / 'lock'
        lock_file.touch(mode=0o600)
        os.chmod(lock_file, 0o600)

        def snapshot_tree(root):
            tree = {}
            for dirpath, dirnames, filenames in os.walk(root):
                for name in dirnames + filenames:
                    p = Path(dirpath) / name
                    info = p.lstat()
                    rel = str(p.relative_to(root))
                    tree[rel] = (stat.S_IFMT(info.st_mode), info.st_size, info.st_mtime_ns)
            return tree

        before = snapshot_tree(self.sessions_dir)
        classify_homes(self.store)
        after = snapshot_tree(self.sessions_dir)

        self.assertEqual(before, after)

    def test_missing_sessions_folder_gives_empty_list(self):
        empty_root = self.base / 'other' / 'store'
        empty_root.mkdir(parents=True, mode=0o700)
        empty_store = TaskStore(empty_root)

        self.assertEqual(classify_homes(empty_store), [])

    def test_binding_with_missing_root_task_is_orphan(self):
        fixed_now = 2_000_000.0
        exec_missing = 'task-exec-synthetic-orphan-notask'
        home = self._create_home_with_binding(exec_missing, 'task-root-not-in-store')

        # Recent orphan (1 day old).
        recent_time = fixed_now - 1 * 86400
        os.utime(home, (recent_time, recent_time))
        for child in home.iterdir():
            os.utime(child, (recent_time, recent_time))

        results_recent = classify_homes(self.store, now=fixed_now)
        by_id_rec = {item['executor_task_id']: item['state'] for item in results_recent}
        self.assertEqual(by_id_rec.get(exec_missing), 'ORPHAN_RECENT')

        # Expired orphan (8 days old).
        expired_time = fixed_now - 8 * 86400
        os.utime(home, (expired_time, expired_time))
        for child in home.iterdir():
            os.utime(child, (expired_time, expired_time))

        results_exp = classify_homes(self.store, now=fixed_now)
        by_id_exp = {item['executor_task_id']: item['state'] for item in results_exp}
        self.assertEqual(by_id_exp.get(exec_missing), 'ORPHAN_EXPIRED')

    def test_invalid_store_never_raises(self):
        self.assertEqual(classify_homes(None), [])
        self.assertEqual(classify_homes(object()), [])


if __name__ == '__main__':
    unittest.main()
