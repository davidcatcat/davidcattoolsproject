"""B5: unsafe no-follow violations distinguished from absent or failed I/O."""
import errno
import os
import unittest
from unittest.mock import patch
import test_diagnostics_store as fixture
from llmtool import diagnostics as d
from llmtool.diagnostics_reader import DiagnosticsReader, ReaderError
from llmtool.diagnostics_store import persistence_supported


@unittest.skipUnless(persistence_supported(), 'requires POSIX diagnostics backend')
class ClassificationRegressions(unittest.TestCase):
    setUp = fixture.StoreTests.setUp
    store = fixture.StoreTests.store
    start = fixture.StoreTests.start
    complete = fixture.StoreTests.complete

    def test_b5_writer_symlink_and_nondirectory_are_unsafe(self):
        target = self.base / 'target'
        target.mkdir(mode=0o700)
        self.home.symlink_to(target, target_is_directory=True)
        store = self.store()
        self.assertIsNone(store.start())
        self.assertEqual(store.warning.code, d.ErrorCode.DIAGNOSTICS_STORE_UNSAFE)
        self.assertEqual(list(target.iterdir()), [])
        self.home.unlink()
        self.home.write_text('synthetic foreign data')
        self.home.chmod(0o600)
        store = self.store()
        self.assertIsNone(store.start())
        self.assertEqual(store.warning.code, d.ErrorCode.DIAGNOSTICS_STORE_UNSAFE)
        self.assertEqual(self.home.read_text(), 'synthetic foreign data')

    def test_b5_reader_symlink_boundaries_are_unsafe(self):
        for component in ('summary.json', 'trace.jsonl', '.run.lock', 'run', 'runs', '.store.lock'):
            with self.subTest(component=component):
                self.home = self.base / component.replace('.', '_')
                directory = self.complete(self.start(identity='a' * 32))
                path = directory if component == 'run' else self.home / component if component in ('runs', '.store.lock') else directory / component
                target = self.base / ('target-' + component.replace('.', '_'))
                path.rename(target)
                path.symlink_to(target, target_is_directory=target.is_dir())
                with self.assertRaises(ReaderError) as caught:
                    DiagnosticsReader(home=self.home).trace('a' * 32)
                self.assertEqual(caught.exception.code, 'STORE_UNSAFE')
                self.assertTrue(target.exists())

    def test_b5_reader_nondirectory_missing_permissions_and_real_io(self):
        directory = self.complete(self.start(identity='b' * 32))
        reader = DiagnosticsReader(home=self.home)
        self.assertEqual(reader.trace('b' * 32)['state'], 'complete')
        with self.assertRaises(ReaderError) as caught:
            reader.trace('c' * 32)
        self.assertEqual(caught.exception.code, 'NOT_FOUND')
        foreign = self.home / 'runs' / ('c' * 32)
        foreign.write_text('KEEP')
        foreign.chmod(0o600)
        with self.assertRaises(ReaderError) as caught:
            reader.trace('c' * 32)
        self.assertEqual(caught.exception.code, 'STORE_UNSAFE')
        directory.chmod(0o755)
        with self.assertRaises(ReaderError) as caught:
            reader.trace('b' * 32)
        self.assertEqual(caught.exception.code, 'STORE_UNSAFE')
        directory.chmod(0o700)
        original = os.open
        for error in (errno.EIO, errno.EACCES):
            def failed(name, *args, **kwargs):
                if name == 'runs':
                    raise OSError(error, 'synthetic I/O')
                return original(name, *args, **kwargs)
            with patch('llmtool.diagnostics_reader.os.open', side_effect=failed), self.assertRaises(ReaderError) as caught:
                reader.trace('b' * 32)
            self.assertEqual(caught.exception.code, 'STORE_UNAVAILABLE')
            with patch('llmtool.diagnostics_store.os.open', side_effect=failed):
                store = self.store()
                self.assertIsNone(store.start())
            self.assertEqual(store.warning.code, d.ErrorCode.DIAGNOSTICS_STORE_UNAVAILABLE)
