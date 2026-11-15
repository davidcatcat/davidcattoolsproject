"""#1230 refusal-code pin: review_authority host file and pin codes. Synthetic temp files only."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

from llmtool import review_authority as a
from llmtool.errors import LLMToolError


class HostFileCodeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir='/private/tmp' if sys.platform == 'darwin' else None)
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.folder.chmod(0o700)
        self.host = self.folder/'host.json'

    def code(self, call):
        with self.assertRaises(LLMToolError) as caught:
            call()
        return caught.exception.review_code

    def test_unsafe_host_file_codes(self):
        self.assertEqual(self.code(lambda: a.read_host_file(self.host)), 'REVIEW_HOST_FILE_UNAVAILABLE')
        self.host.write_text('{}')
        self.host.chmod(0o644)
        self.assertEqual(self.code(lambda: a.read_host_file(self.host)), 'REVIEW_HOST_FILE_UNSAFE')
        self.host.write_text('')
        self.host.chmod(0o600)
        self.assertEqual(self.code(lambda: a.read_host_file(self.host)), 'REVIEW_HOST_FILE_UNSAFE')
        self.host.write_text('{}')
        self.folder.chmod(0o755)
        self.addCleanup(os.chmod, self.folder, 0o700)
        self.assertEqual(self.code(lambda: a.read_host_file(self.host)), 'REVIEW_HOST_FILE_UNSAFE')

    def test_registration_without_a_host_pin_is_not_pinned(self):
        for pin in (None, str(self.host), {'path': str(self.host)}):
            with self.subTest(pin=type(pin).__name__):
                self.assertEqual(self.code(lambda: a.load_host_registration(pin)), 'REVIEW_HOST_NOT_PINNED')


if __name__ == '__main__':
    unittest.main()
