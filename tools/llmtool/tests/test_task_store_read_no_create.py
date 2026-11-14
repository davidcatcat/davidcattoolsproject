"""#1837: read-only TaskStore calls never create the store root."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import task_store  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402

TASK_ID = '0123456789abcdef0123456789abcdef'


@unittest.skipUnless(os.name == 'posix', 'TaskStore needs POSIX')
class ReadNoCreateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / 'state' / 'tasks'
        self.store = task_store.TaskStore(self.root)

    def test_reads_of_an_absent_root_create_nothing(self):
        self.assertEqual(self.store.task_ids(), [])
        self.assertEqual(self.store.completion_d6_task_ids('member-one'), [])
        with self.assertRaises(LLMToolError):
            self.store.get(TASK_ID)
        self.assertFalse((self.base / 'state').exists())

    def test_reads_of_an_existing_empty_root_still_work(self):
        self.root.mkdir(mode=0o700, parents=True)
        os.chmod(self.root, 0o700)
        self.assertEqual(self.store.task_ids(), [])
        with self.assertRaises(LLMToolError):
            self.store.get(TASK_ID)

    def test_write_path_still_creates_the_root(self):
        os.close(self.store._dirfd())
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)

    def test_symlinked_root_is_still_refused(self):
        target = self.base / 'elsewhere'
        target.mkdir(mode=0o700)
        self.root.parent.mkdir(mode=0o700)
        self.root.symlink_to(target)
        with self.assertRaises(LLMToolError) as caught:
            self.store.task_ids()
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()
