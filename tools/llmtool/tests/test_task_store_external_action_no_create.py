"""#1837 P3: check_external_action and advance_lease create no store root."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import task_store
from llmtool.task_store import TaskStore

TASK_ID = '0123456789abcdef0123456789abcdef'


@unittest.skipUnless(os.name == 'posix', 'POSIX ownership and O_NOFOLLOW required')
class ExternalActionNoCreateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'state' / 'tasks'
        self.store = TaskStore(self.root)

    def listing(self):
        return sorted(str(p.relative_to(self.base)) for p in self.base.rglob('*'))

    def enter_lease(self):
        with self.store.advance_lease(TASK_ID):
            pass

    def test_check_external_action_absent_root_creates_nothing(self):
        before = self.listing()
        with self.assertRaises(task_store._TaskNotFound):
            self.store.check_external_action(TASK_ID)
        self.assertEqual(self.listing(), before)
        self.assertFalse(self.root.exists())

    def test_check_external_action_absent_record_creates_no_lock(self):
        self.root.mkdir(parents=True, mode=0o700)
        with self.assertRaises(task_store._TaskNotFound):
            self.store.check_external_action(TASK_ID)
        self.assertEqual(os.listdir(self.root), [])

    def test_advance_lease_absent_root_creates_nothing(self):
        before = self.listing()
        with self.assertRaises(task_store._TaskNotFound):
            self.enter_lease()
        self.assertEqual(self.listing(), before)
        self.assertFalse(self.root.exists())

    def test_advance_lease_keeps_its_lock_in_an_existing_root(self):
        # The lease lock coordinates with a split reservation, so it is still created.
        self.root.mkdir(parents=True, mode=0o700)
        self.enter_lease()
        lease = '.' + TASK_ID + '.json' + task_store.ADVANCE_LEASE_SUFFIX
        self.assertEqual(os.listdir(self.root), [lease])


if __name__ == '__main__':
    unittest.main()
