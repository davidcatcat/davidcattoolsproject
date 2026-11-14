"""#1837 P1: TaskStore reads never create the root; the walk is one fd per component."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import task_store
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore

TASK_ID = '0123456789abcdef0123456789abcdef'


@unittest.skipUnless(os.name == 'posix', 'POSIX ownership and O_NOFOLLOW required')
class ReadNoCreateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'state' / 'tasks'
        self.store = TaskStore(self.root)

    def assertUnavailable(self, read):
        with self.assertRaises(LLMToolError) as caught:
            read()
        self.assertEqual(caught.exception.task_store_error_code,
                         'TASK_STORE_UNAVAILABLE')

    def reads(self):
        return (self.store.task_ids,
                lambda: self.store.get(TASK_ID),
                lambda: self.store.completion_d6_task_ids('member-one'))

    def test_absent_root_creates_nothing(self):
        for label, parent in (('base', self.base), ('state', self.base / 'state')):
            with self.subTest(label=label):
                if label == 'state':
                    parent.mkdir(mode=0o700)
                before = sorted(path.name for path in self.base.iterdir())
                store = TaskStore(parent / 'tasks')
                self.assertEqual(store.task_ids(), [])
                self.assertEqual(store.completion_d6_task_ids('member-one'), [])
                with self.assertRaises(task_store._TaskNotFound):
                    store.get(TASK_ID)
                self.assertEqual(sorted(path.name for path in self.base.iterdir()), before)

    def test_empty_root_reads_are_absent_without_creation(self):
        self.root.mkdir(parents=True, mode=0o700)
        self.assertEqual(self.store.task_ids(), [])
        with self.assertRaises(task_store._TaskNotFound):
            self.store.get(TASK_ID)

    def test_dirfd_creates_the_root_with_owner_only_mode(self):
        self.assertIsNone(os.close(self.store._dirfd()))
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.store._ensure_root()
        self.assertEqual(self.store.task_ids(), [])

    def test_symlinked_parent_or_root_is_unavailable(self):
        target = self.base / 'target'
        target.mkdir(mode=0o700)
        (self.base / 'state').symlink_to(target, target_is_directory=True)
        for read in self.reads():
            self.assertUnavailable(read)
        self.assertEqual(list(target.iterdir()), [])
        (self.base / 'state').unlink()
        (self.base / 'state').mkdir(mode=0o700)
        (self.base / 'state' / 'tasks').symlink_to(target, target_is_directory=True)
        for read in self.reads():
            self.assertUnavailable(read)
        self.assertEqual(list(target.iterdir()), [])

    def test_writable_or_non_directory_parent_is_unavailable(self):
        state = self.base / 'state'
        state.mkdir(mode=0o700)
        os.chmod(state, 0o777)
        for read in self.reads():
            self.assertUnavailable(read)
        state.rmdir()
        state.write_text('not a directory')
        for read in self.reads():
            self.assertUnavailable(read)

    def test_parent_swapped_during_the_walk_is_unavailable_not_absent(self):
        real_open = os.open

        def swap_symlink():
            state = self.base / 'state'
            state.rename(self.base / 'moved')
            state.symlink_to(self.base / 'nowhere', target_is_directory=True)

        def swap_file():
            state = self.base / 'state'
            state.rmdir()
            state.write_text('not a directory')

        for label, swap in (('symlink', swap_symlink), ('file', swap_file)):
            with self.subTest(label=label):
                for name in ('state', 'moved'):
                    path = self.base / name
                    if path.is_symlink() or path.is_file():
                        path.unlink()
                    elif path.is_dir():
                        path.rmdir()
                state = self.base / 'state'
                state.mkdir(mode=0o700)
                swapped = []

                def racing_open(path, flags, *args, _swap=swap, **kwargs):
                    if path == 'tasks' and kwargs.get('dir_fd') is not None and not swapped:
                        swapped.append(True)
                        _swap()
                    return real_open(path, flags, *args, **kwargs)

                with patch.object(task_store.os, 'open', side_effect=racing_open):
                    self.assertUnavailable(self.store.task_ids)
                self.assertEqual(len(swapped), 1)
                self.assertFalse((self.base / 'nowhere').exists())


if __name__ == '__main__':
    unittest.main()
