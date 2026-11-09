"""Tests for private gw-agent session home helpers."""

import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from llmtool.developer_routes import RouteError
from llmtool.developer_routes.gw_agent import (
    GwAgentSessionLock,
    open_session_home,
    private_read,
    private_write,
    read_session_binding,
    session_binding_exists,
    write_session_binding,
)


class GwAgentSessionHomeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.base = Path(self.temp_dir.name)
        self.store_root = self.base / 'store' / 'task-store'
        self.store_root.parent.mkdir(parents=True, mode=0o700)
        self.valid_root_id = 'task-root-synthetic-01'
        self.valid_executor_id = 'task-exec-synthetic-01'
        self.valid_session_id = '0123456789abcdef0123456789abcdef'
        self.valid_data_class = 'synthetic'
        self.valid_scope_digest = 'a' * 64

    def test_home_created_0700_under_store_parent(self):
        home = open_session_home(self.store_root, self.valid_executor_id)
        expected_parent = self.store_root.parent / 'gw-agent-sessions'
        expected_home = expected_parent / self.valid_executor_id

        self.assertEqual(home, expected_home)
        self.assertTrue(home.is_dir())

        parent_stat = expected_parent.stat()
        home_stat = home.stat()
        self.assertEqual(stat.S_IMODE(parent_stat.st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(home_stat.st_mode), 0o700)

        # Existing home is verified on second call without modifying permissions.
        reopened = open_session_home(self.store_root, self.valid_executor_id)
        self.assertEqual(reopened, home)

    def test_home_refused_when_symlink(self):
        parent = self.store_root.parent / 'gw-agent-sessions'
        parent.mkdir(mode=0o700)
        real_home = self.base / 'real-home'
        real_home.mkdir(mode=0o700)

        symlink_home = parent / self.valid_executor_id
        os.symlink(real_home, symlink_home)

        with self.assertRaises(RouteError) as cm:
            open_session_home(self.store_root, self.valid_executor_id)
        self.assertEqual(cm.exception.code, 'GW_AGENT_SESSION_HOME_UNSAFE')

        # Parent directory as a symlink is also refused.
        symlink_home.unlink()
        parent.rmdir()
        real_parent = self.base / 'real-parent'
        real_parent.mkdir(mode=0o700)
        os.symlink(real_parent, parent)

        with self.assertRaises(RouteError) as cm2:
            open_session_home(self.store_root, self.valid_executor_id)
        self.assertEqual(cm2.exception.code, 'GW_AGENT_SESSION_HOME_UNSAFE')

    def test_home_refused_when_group_readable(self):
        parent = self.store_root.parent / 'gw-agent-sessions'
        parent.mkdir(mode=0o750)
        os.chmod(parent, 0o750)

        with self.assertRaises(RouteError) as cm:
            open_session_home(self.store_root, self.valid_executor_id)
        self.assertEqual(cm.exception.code, 'GW_AGENT_SESSION_HOME_UNSAFE')

        # Home directory with group permissions is also refused.
        os.chmod(parent, 0o700)
        home = parent / self.valid_executor_id
        home.mkdir(mode=0o750)
        os.chmod(home, 0o750)

        with self.assertRaises(RouteError) as cm2:
            open_session_home(self.store_root, self.valid_executor_id)
        self.assertEqual(cm2.exception.code, 'GW_AGENT_SESSION_HOME_UNSAFE')

    def test_home_refused_when_inside_folder_holding_git(self):
        git_dir = self.base / '.git'
        git_dir.mkdir()

        with self.assertRaises(RouteError) as cm:
            open_session_home(self.store_root, self.valid_executor_id)
        self.assertEqual(cm.exception.code, 'GW_AGENT_SESSION_HOME_UNSAFE')

    def test_private_write_and_read(self):
        home = open_session_home(self.store_root, self.valid_executor_id)
        target = home / 'data.txt'
        payload = b'synthetic-content-line\n'

        written_path = private_write(target, payload)
        self.assertEqual(written_path, target)

        file_stat = target.stat()
        self.assertEqual(stat.S_IMODE(file_stat.st_mode), 0o600)
        self.assertEqual(file_stat.st_nlink, 1)

        # No leftover temporary files in directory.
        entries = list(home.iterdir())
        self.assertEqual(entries, [target])

        # Private read returns exact bytes.
        data = private_read(target)
        self.assertEqual(data, payload)

        # Private read enforces size limit.
        with self.assertRaises(ValueError):
            private_read(target, max_bytes=5)

    def test_lock_acquire_and_contention(self):
        home = open_session_home(self.store_root, self.valid_executor_id)

        lock1 = GwAgentSessionLock(home)
        lock1.acquire()

        lock_path = home / 'lock'
        self.assertTrue(lock_path.exists())
        lock_stat = lock_path.stat()
        self.assertEqual(stat.S_IMODE(lock_stat.st_mode), 0o600)
        self.assertEqual(lock_stat.st_nlink, 1)

        # Second acquire on same object refuses LOCKED.
        with self.assertRaises(RouteError) as cm_same:
            lock1.acquire()
        self.assertEqual(cm_same.exception.code, 'GW_AGENT_SESSION_LOCKED')

        # Second lock object on same home refuses LOCKED.
        lock2 = GwAgentSessionLock(home)
        with self.assertRaises(RouteError) as cm_other:
            lock2.acquire()
        self.assertEqual(cm_other.exception.code, 'GW_AGENT_SESSION_LOCKED')

        # Release is safe to call twice.
        lock1.release()
        lock1.release()

        # After release, another lock object can acquire.
        lock2.acquire()
        lock2.release()

        # Classmethod acquire usage works and can be released.
        lock3 = GwAgentSessionLock.acquire(home)
        with self.assertRaises(RouteError):
            GwAgentSessionLock.acquire(home)
        lock3.release()

    def test_binding_round_trip(self):
        home = open_session_home(self.store_root, self.valid_executor_id)
        self.assertFalse(session_binding_exists(home))

        write_session_binding(
            home,
            root_task_id=self.valid_root_id,
            executor_task_id=self.valid_executor_id,
            session_id=self.valid_session_id,
            data_class=self.valid_data_class,
            scope_digest=self.valid_scope_digest,
        )

        self.assertTrue(session_binding_exists(home))

        binding = read_session_binding(home, executor_task_id=self.valid_executor_id)
        expected = {
            'data_class': self.valid_data_class,
            'executor_task_id': self.valid_executor_id,
            'root_task_id': self.valid_root_id,
            'schema_version': 1,
            'scope_digest': self.valid_scope_digest,
            'session_id': self.valid_session_id,
        }
        self.assertEqual(binding, expected)

    def test_read_binding_refuses_unavailable(self):
        home = open_session_home(self.store_root, self.valid_executor_id)

        # Missing binding.json.
        with self.assertRaises(RouteError) as cm:
            read_session_binding(home, executor_task_id=self.valid_executor_id)
        self.assertEqual(cm.exception.code, 'GW_AGENT_SESSION_UNAVAILABLE')

        write_session_binding(
            home,
            root_task_id=self.valid_root_id,
            executor_task_id=self.valid_executor_id,
            session_id=self.valid_session_id,
            data_class=self.valid_data_class,
            scope_digest=self.valid_scope_digest,
        )

        # Wrong executor task ID.
        with self.assertRaises(RouteError) as cm_wrong:
            read_session_binding(home, executor_task_id='task-exec-other-02')
        self.assertEqual(cm_wrong.exception.code, 'GW_AGENT_SESSION_UNAVAILABLE')

        binding_file = home / 'binding.json'

        # Extra field in binding.json.
        valid_data = json.loads(binding_file.read_text())
        with_extra = dict(valid_data, extra_field='unexpected')
        binding_file.write_text(json.dumps(with_extra) + '\n')
        with self.assertRaises(RouteError) as cm_extra:
            read_session_binding(home, executor_task_id=self.valid_executor_id)
        self.assertEqual(cm_extra.exception.code, 'GW_AGENT_SESSION_UNAVAILABLE')

        # Bad session ID (e.g. uppercase or incorrect length).
        bad_session = dict(valid_data, session_id='UPPERCASE0123456789abcdef0123456')
        binding_file.write_text(json.dumps(bad_session) + '\n')
        with self.assertRaises(RouteError) as cm_bad_sess:
            read_session_binding(home, executor_task_id=self.valid_executor_id)
        self.assertEqual(cm_bad_sess.exception.code, 'GW_AGENT_SESSION_UNAVAILABLE')

        # Restore valid binding.
        binding_file.write_text(json.dumps(valid_data) + '\n')
        os.chmod(binding_file, 0o600)

        # Group-readable binding file.
        os.chmod(binding_file, 0o640)
        with self.assertRaises(RouteError) as cm_mode:
            read_session_binding(home, executor_task_id=self.valid_executor_id)
        self.assertEqual(cm_mode.exception.code, 'GW_AGENT_SESSION_UNAVAILABLE')

        # Symlinked binding file.
        binding_file.unlink()
        external_file = self.base / 'external-binding.json'
        external_file.write_text(json.dumps(valid_data) + '\n')
        os.chmod(external_file, 0o600)
        os.symlink(external_file, binding_file)
        with self.assertRaises(RouteError) as cm_sym:
            read_session_binding(home, executor_task_id=self.valid_executor_id)
        self.assertEqual(cm_sym.exception.code, 'GW_AGENT_SESSION_UNAVAILABLE')

    def test_write_binding_refuses_binding_failed(self):
        home = open_session_home(self.store_root, self.valid_executor_id)

        invalid_cases = [
            ('bad-root-id', '', self.valid_executor_id, self.valid_session_id, self.valid_data_class, self.valid_scope_digest),
            ('bad-exec-id', self.valid_root_id, '??invalid??', self.valid_session_id, self.valid_data_class, self.valid_scope_digest),
            ('short-session-id', self.valid_root_id, self.valid_executor_id, 'short123', self.valid_data_class, self.valid_scope_digest),
            ('empty-data-class', self.valid_root_id, self.valid_executor_id, self.valid_session_id, '', self.valid_scope_digest),
            ('bad-scope-digest', self.valid_root_id, self.valid_executor_id, self.valid_session_id, self.valid_data_class, 'not-64-hex'),
        ]

        for label, root_id, exec_id, sess_id, data_cls, digest in invalid_cases:
            with self.subTest(case=label):
                with self.assertRaises(RouteError) as cm:
                    write_session_binding(
                        home,
                        root_task_id=root_id,
                        executor_task_id=exec_id,
                        session_id=sess_id,
                        data_class=data_cls,
                        scope_digest=digest,
                    )
                self.assertEqual(cm.exception.code, 'GW_AGENT_SESSION_BINDING_FAILED')

        # Dict with extra field refuses BINDING_FAILED.
        extra_dict = {
            'root_task_id': self.valid_root_id,
            'executor_task_id': self.valid_executor_id,
            'session_id': self.valid_session_id,
            'data_class': self.valid_data_class,
            'scope_digest': self.valid_scope_digest,
            'extra': 'not-allowed',
        }
        with self.assertRaises(RouteError) as cm_extra:
            write_session_binding(home, extra_dict)
        self.assertEqual(cm_extra.exception.code, 'GW_AGENT_SESSION_BINDING_FAILED')


if __name__ == '__main__':
    unittest.main()
