"""#2532: parallel Developer routes contend for the real TaskStore locks.

These tests hold the real per-Task lock file with flock from another process
(no mock of the lock) and check that a writer waits within the bound, fails
closed with a definite code past it, and that the recorded cause is a code.
"""

import fcntl
import multiprocessing as mp
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool import task_store as task_store_module
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore
from llmtool.task_submission_cli import _error_cause

from test_task_store import record


def _hold_lock(root, name, seconds, ready):
    fd = os.open(os.path.join(root, name), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    ready.set()
    time.sleep(seconds)
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


class LockWaitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.temp.name)) / 'tasks'
        self.store = TaskStore(self.root)
        self.task_id = self._create_task()

    def tearDown(self):
        self.temp.cleanup()

    def _create_task(self):
        task = record()
        self.store.create(task)
        return task['task_id']

    def _lock_name(self):
        return '.' + TaskStore._name(self.task_id) + '.lock'

    def _hold(self, seconds):
        ready = mp.Event()
        proc = mp.Process(target=_hold_lock,
                          args=(str(self.root), self._lock_name(), seconds, ready))
        proc.start()
        self.assertTrue(ready.wait(10))
        return proc

    def test_default_bound_matches_the_lineage_lock_bound(self):
        self.assertEqual(task_store_module.TASK_UPDATE_LOCK_TIMEOUT_SECONDS,
                         task_store_module.LINEAGE_LOCK_TIMEOUT_SECONDS)
        self.assertGreater(task_store_module.TASK_UPDATE_LOCK_TIMEOUT_SECONDS, 2.0)

    def test_writer_waits_past_the_old_two_second_bound_and_succeeds(self):
        proc = self._hold(3.0)
        try:
            started = time.monotonic()
            self.store.update(self.task_id, error_code=None)
            waited = time.monotonic() - started
        finally:
            proc.join(10)
        self.assertGreaterEqual(waited, 2.0)
        self.assertLess(waited, task_store_module.TASK_UPDATE_LOCK_TIMEOUT_SECONDS)

    def test_writer_past_the_bound_fails_closed_busy_with_the_code_shown(self):
        proc = self._hold(2.0)
        try:
            with patch.object(task_store_module, 'TASK_UPDATE_LOCK_TIMEOUT_SECONDS', 0.3), \
                    self.assertRaises(LLMToolError) as caught:
                self.store.update(self.task_id, error_code=None)
        finally:
            proc.join(10)
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_BUSY')
        self.assertIn('TASK_STORE_BUSY', str(caught.exception))
        self.assertEqual(_error_cause(caught.exception), 'TASK_STORE_BUSY')

    def test_error_cause_is_a_code_never_text(self):
        self.assertEqual(_error_cause(RuntimeError('secret /path')), 'UNCLASSIFIED')
        self.assertIsNone(task_store_module._ERROR_CAUSE.fullmatch('free text'))
        self.assertIsNone(task_store_module._ERROR_CAUSE.fullmatch('A' * 70))
        self.assertIsNotNone(task_store_module._ERROR_CAUSE.fullmatch('TASK_STORE_BUSY'))


if __name__ == '__main__':
    unittest.main()
