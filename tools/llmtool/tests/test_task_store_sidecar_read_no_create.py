"""#1837 P2: sidecar reads of TaskStore create no root and no lock file."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool import task_store
from llmtool.task_store import TaskStore


@unittest.skipUnless(os.name == 'posix', 'POSIX ownership and O_NOFOLLOW required')
class SidecarReadNoCreateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / 'state' / 'tasks'
        self.store = TaskStore(self.root)

    def names(self):
        return sorted(str(p.relative_to(self.base)) for p in self.base.rglob('*'))

    def test_absent_root_reads_empty_and_creates_nothing(self):
        observations = self.store.get_worker_observations()
        self.assertEqual(self.store.get_developer_assignments(), [])
        self.assertEqual(self.names(), [])
        self.assertTrue(observations)
        self.root.mkdir(parents=True, mode=0o700)
        self.assertEqual(self.store.get_worker_observations(), observations)
        self.assertEqual(self.store.get_developer_assignments(), [])
        self.assertEqual(self.names(), ['state', 'state/tasks'])

    def test_existing_lock_is_still_taken_for_a_read(self):
        self.root.mkdir(parents=True, mode=0o700)
        for name in (task_store.WORKER_AVAILABILITY_LOCK_NAME, task_store.ASSIGNMENTS_LOCK_NAME):
            fd = os.open(self.root / name, os.O_RDWR | os.O_CREAT, 0o600)
            os.close(fd)
        before = self.names()
        self.store.get_worker_observations()
        self.assertEqual(self.store.get_developer_assignments(), [])
        self.assertEqual(self.names(), before)

    def test_read_lock_open_never_creates_and_a_vanished_lock_is_empty(self):
        # Security v91 F1 (CWE-367): no O_CREAT on the read path, so a lock removed
        # between any check and the open is never recreated by a read.
        self.root.mkdir(parents=True, mode=0o700)
        real_open, flags = os.open, []

        def vanished(name, flag, *args, **kwargs):
            if str(name).endswith('.lock'):
                flags.append(flag)
                raise FileNotFoundError(name)
            return real_open(name, flag, *args, **kwargs)

        with patch('llmtool.task_store.os.open', side_effect=vanished):
            self.store.get_worker_observations()
            self.assertEqual(self.store.get_developer_assignments(), [])
        self.assertEqual(len(flags), 2)
        self.assertFalse(any(flag & os.O_CREAT for flag in flags))
        self.assertEqual(self.names(), ['state', 'state/tasks'])


if __name__ == '__main__':
    unittest.main()
