"""Tests for classify_homes and sweep in gw_agent_sessions."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

from llmtool.developer_routes.gw_agent import (
    GwAgentSessionLock,
    open_session_home,
    write_session_binding,
)
from llmtool.gw_agent_sessions import (
    ORPHAN_TTL_SECONDS,
    _remove_home,
    classify_homes,
    sweep,
)

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

    def _create_completed_home(self, exec_id, root_id):
        self.store.put_synthetic_task({
            'task_id': root_id,
            'state': 'COMPLETED',
            'completion_events': [{
                'source': 'GITHUB',
                'marker_type': 'merge',
                'marker_status': 'MERGED',
            }],
        })
        home = self._create_home_with_binding(exec_id, root_id)
        sess_dir = home / 'sessions' / self.valid_session_id
        sess_dir.mkdir(parents=True, mode=0o700)
        (sess_dir / 'history.json').write_text('{"messages": ["synthetic_msg"]}\n')
        (sess_dir / 'meta.json').write_text('{"session_id": "synthetic_meta"}\n')
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

    def test_sweep_completed_task_ends_then_removes(self):
        root_id = 'task-root-synthetic-comp-10'
        exec_id = 'task-exec-synthetic-comp-10'
        home = self._create_completed_home(exec_id, root_id)

        captured = []
        def fake_runner(cmd, env=None, stdin=None, timeout=None):
            captured.append({'cmd': cmd, 'env': env, 'stdin': stdin, 'timeout': timeout})
            return subprocess.CompletedProcess(cmd, returncode=0)

        results = sweep(self.store, runner=fake_runner)

        self.assertEqual(results, [{'executor_task_id': exec_id, 'outcome': 'ENDED'}])
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]['timeout'], 30)
        self.assertEqual(captured[0]['stdin'], subprocess.DEVNULL)
        cmd = captured[0]['cmd']
        self.assertEqual(cmd[0], sys.executable)
        self.assertIn('end', cmd)
        self.assertIn(self.valid_session_id, cmd)
        self.assertIn('--reason', cmd)
        self.assertIn('task_terminal', cmd)
        self.assertIn('--state-dir', cmd)
        self.assertIn(str(home), cmd)
        self.assertFalse(home.exists())

    def test_sweep_abandoned_task_removes(self):
        root_id = 'task-root-synthetic-aban-10'
        exec_id = 'task-exec-synthetic-aban-10'
        self.store.put_synthetic_task({
            'task_id': root_id,
            'abandonment': {'reason': 'synthetic-abandonment'},
        })
        home = self._create_home_with_binding(exec_id, root_id)
        sess_dir = home / 'sessions' / self.valid_session_id
        sess_dir.mkdir(parents=True, mode=0o700)
        (sess_dir / 'history.json').write_text('{"messages": ["synthetic_msg"]}\n')

        captured = []
        def fake_runner(cmd, env=None, stdin=None, timeout=None):
            captured.append({'cmd': cmd, 'env': env, 'stdin': stdin, 'timeout': timeout})
            return subprocess.CompletedProcess(cmd, returncode=0)

        results = sweep(self.store, runner=fake_runner)

        self.assertEqual(results, [{'executor_task_id': exec_id, 'outcome': 'ENDED'}])
        self.assertEqual(len(captured), 1)
        self.assertFalse(home.exists())

    def test_sweep_open_task_kept_at_any_age(self):
        root_id = 'task-root-synthetic-open-10'
        exec_id = 'task-exec-synthetic-open-10'
        self.store.put_synthetic_task({
            'task_id': root_id,
            'state': 'PM_PENDING',
        })
        home = self._create_home_with_binding(exec_id, root_id)

        fixed_now = 2_000_000.0
        old_time = fixed_now - 45 * 86400
        os.utime(home, (old_time, old_time))
        for child in home.iterdir():
            os.utime(child, (old_time, old_time))

        captured = []
        def fake_runner(cmd, env=None, stdin=None, timeout=None):
            captured.append(cmd)
            return subprocess.CompletedProcess(cmd, returncode=0)

        results = sweep(self.store, now=fixed_now, runner=fake_runner)

        self.assertEqual(results, [{'executor_task_id': exec_id, 'outcome': 'SKIPPED_OPEN'}])
        self.assertEqual(len(captured), 0)
        self.assertTrue(home.exists())

    def test_sweep_busy_lock_skipped(self):
        root_id = 'task-root-synthetic-lock-10'
        exec_id = 'task-exec-synthetic-lock-10'
        home = self._create_completed_home(exec_id, root_id)

        lock = GwAgentSessionLock.acquire(home)
        captured = []
        def fake_runner(cmd, env=None, stdin=None, timeout=None):
            captured.append(cmd)
            return subprocess.CompletedProcess(cmd, returncode=0)

        try:
            results = sweep(self.store, runner=fake_runner)
            self.assertEqual(results, [{'executor_task_id': exec_id, 'outcome': 'SKIPPED_LOCKED'}])
            self.assertEqual(len(captured), 0)
            self.assertTrue(home.exists())
        finally:
            lock.release()

    def test_sweep_expired_orphan_removed_and_recent_kept(self):
        fixed_now = 2_000_000.0

        # Expired orphan: 8 days old
        exec_exp = 'task-exec-synthetic-orphan-exp-10'
        home_exp = open_session_home(self.store_root, exec_exp)
        exp_time = fixed_now - 8 * 86400
        os.utime(home_exp, (exp_time, exp_time))

        # Recent orphan: 1 day old
        exec_rec = 'task-exec-synthetic-orphan-rec-10'
        home_rec = open_session_home(self.store_root, exec_rec)
        rec_time = fixed_now - 1 * 86400
        os.utime(home_rec, (rec_time, rec_time))

        captured = []
        def fake_runner(cmd, env=None, stdin=None, timeout=None):
            captured.append(cmd)
            return subprocess.CompletedProcess(cmd, returncode=0)

        results = sweep(self.store, now=fixed_now, runner=fake_runner)
        by_id = {item['executor_task_id']: item['outcome'] for item in results}

        self.assertEqual(by_id.get(exec_exp), 'ORPHAN_REMOVED')
        self.assertEqual(by_id.get(exec_rec), 'SKIPPED_OPEN')
        self.assertEqual(len(captured), 0)
        self.assertFalse(home_exp.exists())
        self.assertTrue(home_rec.exists())

    def test_sweep_end_failure_and_timeout_still_remove_history(self):
        # 1. End command fails with non-zero exit code
        root_fail = 'task-root-synthetic-fail-10'
        exec_fail = 'task-exec-synthetic-fail-10'
        home_fail = self._create_completed_home(exec_fail, root_fail)

        def failing_runner(cmd, env=None, stdin=None, timeout=None):
            return subprocess.CompletedProcess(cmd, returncode=1)

        results_fail = sweep(self.store, runner=failing_runner)
        self.assertEqual(results_fail, [{'executor_task_id': exec_fail, 'outcome': 'REMOVED_AFTER_END_FAILURE'}])
        self.assertFalse(home_fail.exists())

        # 2. End command times out
        root_timeout = 'task-root-synthetic-timeout-10'
        exec_timeout = 'task-exec-synthetic-timeout-10'
        home_timeout = self._create_completed_home(exec_timeout, root_timeout)

        def timing_out_runner(cmd, env=None, stdin=None, timeout=None):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=30)

        results_timeout = sweep(self.store, runner=timing_out_runner)
        self.assertEqual(results_timeout, [{'executor_task_id': exec_timeout, 'outcome': 'REMOVED_AFTER_END_FAILURE'}])
        self.assertFalse(home_timeout.exists())

    def test_sweep_symlinks_unlinked_targets_stay(self):
        # Subtest 1: Symlinked history.json inside home is unlinked without removing target
        sensitive_target = self.base / 'target_sensitive_history.json'
        sensitive_target.write_text('{"synthetic_payload": "do_not_delete"}\n')

        root_id = 'task-root-synthetic-symhist-10'
        exec_id = 'task-exec-synthetic-symhist-10'
        self.store.put_synthetic_task({
            'task_id': root_id,
            'state': 'COMPLETED',
            'completion_events': [{
                'source': 'GITHUB',
                'marker_type': 'merge',
                'marker_status': 'MERGED',
            }],
        })
        home = self._create_home_with_binding(exec_id, root_id)
        sess_dir = home / 'sessions' / self.valid_session_id
        sess_dir.mkdir(parents=True, mode=0o700)
        os.symlink(sensitive_target, sess_dir / 'history.json')
        (sess_dir / 'meta.json').write_text('{"session_id": "synthetic_meta"}\n')

        def fake_runner(cmd, env=None, stdin=None, timeout=None):
            return subprocess.CompletedProcess(cmd, returncode=0)

        results = sweep(self.store, runner=fake_runner)
        self.assertEqual(results, [{'executor_task_id': exec_id, 'outcome': 'ENDED'}])
        self.assertFalse(home.exists())
        self.assertTrue(sensitive_target.exists())
        self.assertEqual(sensitive_target.read_text(), '{"synthetic_payload": "do_not_delete"}\n')

        # Subtest 2: Symlinked home unlinked as link, target stays
        target_home_dir = self.base / 'target_home_dir_synthetic'
        target_home_dir.mkdir(parents=True, mode=0o700)
        target_payload = target_home_dir / 'synthetic_data.txt'
        target_payload.write_text('synthetic_content\n')

        sym_home = self.sessions_dir / 'task-exec-synthetic-symhome-10'
        os.symlink(target_home_dir, sym_home)

        _remove_home(sym_home, self.sessions_dir)
        self.assertFalse(os.path.lexists(sym_home))
        self.assertTrue(target_home_dir.exists())
        self.assertTrue(target_payload.exists())
        self.assertEqual(target_payload.read_text(), 'synthetic_content\n')

    def test_sweep_runner_env_has_only_path_home_lang(self):
        root_id = 'task-root-synthetic-env-10'
        exec_id = 'task-exec-synthetic-env-10'
        home = self._create_completed_home(exec_id, root_id)

        captured_env = {}
        def fake_runner(cmd, env=None, stdin=None, timeout=None):
            captured_env.update(env or {})
            return subprocess.CompletedProcess(cmd, returncode=0)

        os.environ['SYNTHETIC_UNRELATED_VARIABLE'] = 'synthetic_val'
        self.addCleanup(os.environ.pop, 'SYNTHETIC_UNRELATED_VARIABLE', None)

        results = sweep(self.store, runner=fake_runner)
        self.assertEqual(results, [{'executor_task_id': exec_id, 'outcome': 'ENDED'}])

        allowed_keys = {'PATH', 'HOME', 'LANG'}
        self.assertTrue(set(captured_env.keys()).issubset(allowed_keys))
        self.assertNotIn('SYNTHETIC_UNRELATED_VARIABLE', captured_env)
        self.assertNotIn('PYTHONPATH', captured_env)
        self.assertNotIn('GW_AGENT_API_KEY', captured_env)
        self.assertNotIn('TMPDIR', captured_env)

        captured_env.clear()
        root_pkg = 'task-root-synthetic-pkg-10'
        exec_pkg = 'task-exec-synthetic-pkg-10'
        self._create_completed_home(exec_pkg, root_pkg)

        results_pkg = sweep(self.store, runner=fake_runner, package_path='/synthetic/package/path')
        self.assertEqual(results_pkg, [{'executor_task_id': exec_pkg, 'outcome': 'ENDED'}])
        self.assertEqual(captured_env.get('PYTHONPATH'), '/synthetic/package/path')
        self.assertTrue(set(captured_env.keys()).issubset({'PATH', 'HOME', 'LANG', 'PYTHONPATH'}))

    def test_sweep_results_hold_no_session_content(self):
        root_id = 'task-root-synthetic-leak-10'
        exec_id = 'task-exec-synthetic-leak-10'
        home = self._create_completed_home(exec_id, root_id)

        marker = 'synthetic_unique_history_marker_98765'
        sess_dir = home / 'sessions' / self.valid_session_id
        (sess_dir / 'history.json').write_text(f'{{"content": "{marker}"}}\n')

        def fake_runner(cmd, env=None, stdin=None, timeout=None):
            return subprocess.CompletedProcess(cmd, returncode=0)

        results = sweep(self.store, runner=fake_runner)
        self.assertEqual(len(results), 1)
        for item in results:
            self.assertEqual(set(item.keys()), {'executor_task_id', 'outcome'})
            self.assertNotIn(marker, str(item))
            self.assertNotIn('session_id', item)

    def test_sweep_empty_or_invalid_store_returns_empty_list(self):
        self.assertEqual(sweep(None), [])
        self.assertEqual(sweep(object()), [])
        empty_root = self.base / 'other' / 'store'
        empty_root.mkdir(parents=True, mode=0o700)
        self.assertEqual(sweep(TaskStore(empty_root)), [])


if __name__ == '__main__':
    unittest.main()
