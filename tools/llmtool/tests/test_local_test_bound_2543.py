"""#2543: one local test run is bounded as a whole, not only per module."""

import os
import tempfile
import time
import unittest
from unittest.mock import patch

from llmtool import task_completion
from llmtool.errors import LLMToolError


class LocalTestRunTotalBoundTests(unittest.TestCase):
    def setUp(self):
        self._root = tempfile.TemporaryDirectory()
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._root.cleanup)
        self.addCleanup(self._home.cleanup)
        self.root, self.home = self._root.name, self._home.name
        for name, body in (('quiet.py', 'pass\n'),
                           ('nap.py', 'import time\ntime.sleep(0.6)\n')):
            with open(os.path.join(self.root, name), 'w', encoding='utf-8') as handle:
                handle.write(body)

    def _run(self, eligible):
        return task_completion._run_registered_tests(
            self.root, 'a' * 40, eligible, home=self.home)

    def test_the_whole_sequence_stops_at_the_total_bound(self):
        # Each module fits the per-module cap; together they pass the total bound.
        started = time.monotonic()
        with patch.object(task_completion, '_TEST_TIMEOUT', 5), \
                patch.object(task_completion, '_TEST_TOTAL_TIMEOUT', 1.0):
            with self.assertRaises(LLMToolError) as caught:
                self._run([('nap.py', 'direct'), ('nap.py', 'direct'),
                           ('nap.py', 'direct'), ('quiet.py', 'direct')])
        self.assertEqual(caught.exception.completion_code, 'TEST_RUNNER_TIMEOUT')
        # Stopped near the total bound, not after every module ran.
        self.assertLess(time.monotonic() - started, 2.0)

    def test_a_run_inside_the_total_bound_still_passes(self):
        with patch.object(task_completion, '_TEST_TIMEOUT', 5), \
                patch.object(task_completion, '_TEST_TOTAL_TIMEOUT', 5):
            evidence = self._run([('quiet.py', 'direct'), ('quiet.py', 'direct')])
        self.assertEqual([item['status'] for item in evidence], ['PASSED', 'PASSED'])

    def test_the_total_bound_is_finite_and_not_below_one_module(self):
        self.assertGreaterEqual(task_completion._TEST_TOTAL_TIMEOUT,
                                task_completion._TEST_TIMEOUT)
        self.assertLessEqual(task_completion._TEST_TOTAL_TIMEOUT, 3600)


if __name__ == '__main__':
    unittest.main()
