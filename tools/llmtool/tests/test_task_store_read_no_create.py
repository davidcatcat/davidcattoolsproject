"""#1837 P1: TaskStore reads never create the root; the walk is one fd per component."""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import task_store
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore

TASK_ID = '0123456789abcdef0123456789abcdef'


@unittest.skipUnless(os.name == 'posix', 'POSIX dirfd walk')
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

    def test_absent_root_reads_are_empty_and_create_nothing(self):
        for label, make_state in (('missing state', False), ('existing state', True)):
            with self.subTest(label=label):
                state = self.base / 'state'
                if state.is_symlink() or state.is_file():
                    state.unlink()
                elif state.is_dir():
                    state.rmdir()
                if make_state:
                    state.mkdir(mode=0o700)
                self.assertEqual(self.store.task_ids(), [])
                self.assertEqual(self.store.completion_d6_task_ids('member-one'), [])
                self.assertUnavailable(lambda: self.store.get(TASK_ID))
                self.assertFalse(self.root.exists())
                self.assertEqual(sorted(path.name for path in self.base.iterdir()),
                                 ['state'] if make_state else [])

    def test_existing_empty_root_reads_are_empty(self):
        (self.base / 'state').mkdir(mode=0o700)
        self.root.mkdir(mode=0o700)
        self.assertEqual(self.store.task_ids(), [])
        self.assertUnavailable(lambda: self.store.get(TASK_ID))

    def test_dirfd_creates_the_root_and_ensure_root_still_works(self):
        os.close(self.store._dirfd())
        self.assertTrue(self.root.is_dir())
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.store._ensure_root()
        self.assertEqual(self.store.task_ids(), [])

    def test_symlinked_parent_or_root_is_unavailable(self):
        target = self.base / 'target'
        target.mkdir(mode=0o700)
        state = self.base / 'state'
        state.symlink_to(target, target_is_directory=True)
        for read in self.reads():
            self.assertUnavailable(read)
        self.assertEqual(list(target.iterdir()), [])
        state.unlink()
        state.mkdir(mode=0o700)
        self.root.symlink_to(target, target_is_directory=True)
        for read in self.reads():
            self.assertUnavailable(read)
        self.assertEqual(list(target.iterdir()), [])

    def test_unsafe_or_non_directory_parent_is_unavailable(self):
        state = self.base / 'state'
        state.mkdir(mode=0o700)
        state.chmod(0o777)
        for read in self.reads():
            self.assertUnavailable(read)
        state.rmdir()
        state.write_text('not a directory', encoding='utf-8')
        for read in self.reads():
            self.assertUnavailable(read)

    def test_parent_swapped_during_the_walk_is_unavailable_not_absent(self):
        state = self.base / 'state'
        moved = self.base / 'moved'
        nowhere = self.base / 'nowhere'

        def remove(path):
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()

        def swap_symlink():
            state.rename(moved)
            state.symlink_to(nowhere, target_is_directory=True)

        def swap_file():
            state.rmdir()
            state.write_text('swapped', encoding='utf-8')

        for label, swap in (('symlink', swap_symlink), ('file', swap_file)):
            with self.subTest(label=label):
                remove(moved)
                remove(state)
                state.mkdir(mode=0o700)
                real_open = os.open
                calls = []

                def racing_open(path, flags, *args, **kwargs):
                    if path == 'tasks' and kwargs.get('dir_fd') is not None and not calls:
                        calls.append(path)
                        swap()
                    return real_open(path, flags, *args, **kwargs)

                with patch.object(task_store.os, 'open', racing_open):
                    self.assertUnavailable(self.store.task_ids)
                self.assertEqual(len(calls), 1)
                self.assertFalse(nowhere.exists())


if __name__ == '__main__':
    unittest.main()
