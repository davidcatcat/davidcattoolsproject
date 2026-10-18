"""Issue187: feedback retains raw FDs throughout stream construction/use."""
import errno
import _pyio
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from llmtool import feedback_files as files
from llmtool.errors import LLMToolError
from test_feedback import feedback


@unittest.skipUnless(os.name == 'posix', 'feedback physical paths require POSIX')
class FeedbackFDLifetimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / 'capture.json'
        self.path.write_text(feedback().to_json())
        self.original_open, self.original_close = os.open, os.close
        self.sentinel = self.original_open(self.path, os.O_RDONLY)
        self.addCleanup(self.original_close, self.sentinel)
        self.owned = []
        self.addCleanup(self.close_owned_leaks)

    def fingerprint(self, descriptor):
        info = os.fstat(descriptor)
        return info.st_dev, info.st_ino, info.st_mode

    def opened(self, *args, **kwargs):
        descriptor = self.original_open(*args, **kwargs)
        self.owned.append((descriptor, self.fingerprint(descriptor)))
        return descriptor

    def close_owned_leaks(self):
        # Keep a red run contained too. Never enumerate/close unrelated FDs.
        for descriptor, identity in reversed(self.owned):
            try:
                current = self.fingerprint(descriptor)
            except OSError as exc:
                if exc.errno != errno.EBADF:
                    raise
            else:
                if current == identity:
                    self.original_close(descriptor)

    def assert_closed(self):
        self.assertTrue(self.owned)
        for descriptor, _ in self.owned:
            with self.subTest(descriptor=descriptor):
                with self.assertRaises(OSError) as error:
                    os.fstat(descriptor)
                self.assertEqual(error.exception.errno, errno.EBADF)
        self.assertTrue(stat.S_ISREG(os.fstat(self.sentinel).st_mode))

    def test_real_directory_fdopen_rejection_releases_every_owned_fd(self):
        with patch.object(files.os, 'open', side_effect=self.opened):
            with self.assertRaisesRegex(LLMToolError, '^feedback file operation failed; details omitted$'):
                files.read_feedback_text(self.root)
        self.assert_closed()

    def test_read_fdopen_failure_keeps_primary_exception_and_closes(self):
        failure = RuntimeError('synthetic fdopen failure')
        with patch.object(files.os, 'open', side_effect=self.opened), \
                patch.object(files.os, 'fdopen', side_effect=failure):
            with self.assertRaises(RuntimeError) as error:
                files.read_feedback_text(self.path)
        self.assertIs(error.exception, failure)
        self.assert_closed()

    def test_write_fdopen_failure_removes_temporary_and_closes(self):
        failure = RuntimeError('synthetic fdopen failure')
        target = self.root / 'new.json'
        with patch.object(files.os, 'open', side_effect=self.opened), \
                patch.object(files.os, 'fdopen', side_effect=failure):
            with self.assertRaises(RuntimeError) as error:
                files.write_feedback_file(target, feedback())
        self.assertIs(error.exception, failure)
        self.assertFalse(target.exists())
        self.assertEqual(list(self.root.glob('.llmtool-feedback-*')), [])
        self.assert_closed()

    def test_partial_buffer_failure_cannot_close_a_reused_foreign_fd(self):
        failure = MemoryError('synthetic buffer construction failure')
        foreign_path = self.root / 'unrelated.txt'
        foreign_path.write_text('synthetic foreign owner')
        foreign = []
        def fdopen(descriptor, mode, **kwargs):
            try:
                # The stdlib reference open constructs FileIO, then fails while
                # building the buffer. Its own cleanup obeys closefd.
                with patch.object(_pyio, 'BufferedReader', side_effect=failure):
                    return _pyio.open(descriptor, mode, **kwargs)
            except MemoryError:
                other = self.original_open(foreign_path, os.O_RDONLY)
                identity = self.fingerprint(other)
                foreign.append((other, identity))
                def cleanup():
                    try:
                        if self.fingerprint(other) == identity:
                            self.original_close(other)
                    except OSError as exc:
                        if exc.errno != errno.EBADF:
                            raise
                self.addCleanup(cleanup)
                raise
        with patch.object(files.os, 'open', side_effect=self.opened), \
                patch.object(files.os, 'fdopen', side_effect=fdopen):
            with self.assertRaises(MemoryError) as error:
                files.read_feedback_text(self.path)
        self.assertIs(error.exception, failure)
        self.assertEqual(len(foreign), 1)
        self.assertEqual(self.fingerprint(foreign[0][0]), foreign[0][1])
        self.assert_closed()

    def test_secondary_raw_close_error_preserves_primary_failure(self):
        failure = RuntimeError('synthetic primary failure')
        victim = []
        def fdopen(descriptor, mode, **kwargs):
            victim.append(descriptor)
            raise failure
        def close(descriptor):
            self.original_close(descriptor)
            if descriptor in victim:
                raise OSError('synthetic secondary close failure')
        with patch.object(files.os, 'open', side_effect=self.opened), \
                patch.object(files.os, 'fdopen', side_effect=fdopen), \
                patch.object(files.os, 'close', side_effect=close):
            with self.assertRaises(RuntimeError) as error:
                files.read_feedback_text(self.path)
        self.assertIs(error.exception, failure)
        self.assertEqual(len(victim), 1)
        self.assert_closed()

    def test_successful_read_and_write_release_owned_fds(self):
        target = self.root / 'new.json'
        with patch.object(files.os, 'open', side_effect=self.opened):
            self.assertEqual(files.read_feedback_file(self.path), feedback())
            files.write_feedback_file(target, feedback())
            self.assertEqual(files.read_feedback_file(target), feedback())
        self.assert_closed()
