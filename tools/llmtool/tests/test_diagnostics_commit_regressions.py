"""B7: resource-release faults cannot undo durable publication."""
import errno
import os
import unittest
from unittest.mock import patch
import test_diagnostics_ask_persistence as fixture
from llmtool.diagnostics_reader import DiagnosticsReader
from llmtool.diagnostics_store import persistence_supported
from llmtool.errors import coded_error


@unittest.skipUnless(persistence_supported(), 'requires POSIX diagnostics backend')
class DurableCommitRegressions(unittest.TestCase):
    setUp = fixture.PersistedAskTests.setUp
    set_config = fixture.PersistedAskTests.set_config
    invoke = fixture.PersistedAskTests.invoke

    def committed_close_fault(self, fail=None):
        original = os.close
        faults = []
        def close(fd):
            original(fd)
            if self.writers and fd == self.writers[-1].run_lock and self.writers[-1].persisted:
                faults.append(fd)
                raise OSError(errno.EIO, 'synthetic close fault')
        with patch('llmtool.diagnostics_store.os.close', side_effect=close):
            result = self.invoke(['--save-diagnostics', '--debug'], fail=fail)
        self.assertEqual(len(faults), 1)
        self.assertTrue(self.writers[-1].persisted)
        self.assertTrue(self.runs[-1].persisted, result[2])
        self.assertIn('diagnostics_persisted=true', result[2])
        self.assertEqual(result[2].count('code=DIAGNOSTICS_WRITE_FAILED'), 1)
        self.assertIn('saved metadata retained', result[2])
        run_id = self.runs[-1].run_id
        self.assertEqual(DiagnosticsReader(home=self.state).trace(run_id)['state'], 'complete')
        result[3].complete.assert_called_once()
        for fd in self.writers[-1]._fds:
            with self.assertRaises(OSError):
                os.fstat(fd)
        return result

    def test_b7_success_remains_saved_after_durable_close_fault(self):
        self.assertEqual(self.committed_close_fault()[:2], (0, fixture.CONTENT + '\n'))

    def test_b7_primary_error_reports_saved_after_durable_close_fault(self):
        result = self.committed_close_fault(coded_error('provider timed out', 'PROVIDER_TIMEOUT', 'provider'))
        self.assertEqual(result[:2], (1, ''))
        self.assertIn('code=PROVIDER_TIMEOUT', result[2])
        self.assertIn('trace=saved', result[2])

    def test_b7_prepublication_fsync_failure_still_unsaved(self):
        with patch('llmtool.diagnostics_store.os.fsync', side_effect=OSError(errno.EIO, 'synthetic fsync')):
            result = self.invoke(['--save-diagnostics', '--verbose'])
        self.assertEqual(result[:2], (0, fixture.CONTENT + '\n'))
        self.assertIn('diagnostics_persisted=false', result[2])
        self.assertFalse(self.runs[-1].persisted)

    def test_b7_summary_publication_failure_still_unsaved(self):
        from llmtool import diagnostics_store as store
        original = store._publish
        def publish(parent, source, target):
            if target == 'summary.json':
                raise OSError(errno.EIO, 'synthetic publication failure')
            return original(parent, source, target)
        with patch.object(store, '_publish', side_effect=publish):
            result = self.invoke(['--save-diagnostics', '--debug'])
        self.assertEqual(result[:2], (0, fixture.CONTENT + '\n'))
        self.assertIn('diagnostics_persisted=false', result[2])
        self.assertFalse(self.runs[-1].persisted)
