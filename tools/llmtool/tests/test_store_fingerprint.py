"""#1019: fingerprint_directory on a real temporary directory: the same value
for an unchanged directory, a different value after an entry is added, the
same value when only a symbolic link target's contents change, and the
fixed module error for a missing path. macOS temporary directories are
reached through a symbolic link, so the resolved temporary path is used."""
import os
import tempfile
import unittest

from llmtool.store_fingerprint import (StoreFingerprintError,
                                       fingerprint_directory)


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # Resolved path: the macOS temporary base is a symbolic link, and
        # the module opens the directory with O_NOFOLLOW.
        self.tmp = os.path.realpath(self._tmp.name)

    def test_unchanged_state_is_stable_and_added_entry_differs(self):
        first = fingerprint_directory(self.tmp)
        self.assertIsInstance(first, str)
        self.assertTrue(first)
        self.assertEqual(first, fingerprint_directory(self.tmp))
        with open(os.path.join(self.tmp, 'entry-a.txt'), 'wb') as fh:
            fh.write(b'a')
        self.assertNotEqual(first, fingerprint_directory(self.tmp))

    def test_link_target_content_change_keeps_fingerprint(self):
        with tempfile.TemporaryDirectory() as base:
            target = os.path.join(os.path.realpath(base), 'target.txt')
            with open(target, 'wb') as fh:
                fh.write(b'one')
            link = os.path.join(self.tmp, 'link-to-target')
            os.symlink(target, link)
            first = fingerprint_directory(self.tmp)
            with open(target, 'wb') as fh:
                fh.write(b'two')
            self.assertEqual(first, fingerprint_directory(self.tmp))


class MissingPathTests(unittest.TestCase):
    def test_missing_path_raises_module_error_with_fixed_code(self):
        with tempfile.TemporaryDirectory() as base:
            path = os.path.join(os.path.realpath(base), 'absent')
            with self.assertRaises(StoreFingerprintError) as caught:
                fingerprint_directory(path)
        self.assertEqual(caught.exception.code,
                         'STORE_FINGERPRINT_INVALID')
        self.assertEqual(str(caught.exception),
                         'store directory fingerprint invalid')


if __name__ == '__main__':
    unittest.main()
