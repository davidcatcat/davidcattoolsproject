"""Policy construction and verification tests for direct route boundaries."""

import unittest
import tempfile
from pathlib import Path
from unittest import mock

from llmtool.developer_routes.write_boundary import (
    SandboxExecBoundary, UnsupportedBoundary, _sbpl_quote, for_platform,
)


class DeveloperWriteBoundaryTests(unittest.TestCase):
    def test_profile_uses_exact_escaped_path_allows(self):
        profile = SandboxExecBoundary._profile(['/tmp/a"b'])
        self.assertIn('(deny file-write*)', profile)
        self.assertIn('(allow file-write* (literal "/tmp/a\\"b"))', profile)

    def test_rejects_unsafe_relative_and_denied_paths(self):
        boundary = SandboxExecBoundary(executable='/bin/true', tmpdir='/tmp')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / '.git').mkdir()
            with mock.patch('subprocess.check_output', return_value=(str(root / '.git')).encode()):
                with self.assertRaises(ValueError):
                    boundary._policy(root, ['../escape'], [])
                with self.assertRaises(ValueError):
                    boundary._policy(root, ['.git/config'], [])

    def test_any_failed_probe_makes_policy_unverified(self):
        boundary = SandboxExecBoundary(executable='/bin/true', tmpdir='/tmp')
        boundary._prepared = ('/tmp', ('out',), (), ('/tmp/out',), (),
                              SandboxExecBoundary._profile(['/tmp/out']), 'a' * 64)
        with mock.patch.object(boundary, '_policy', return_value=(__import__('pathlib').Path('/tmp'),
                           ['/tmp/out'], [])):
            calls = iter(((0, b'', b''), (1, b'', b''), (0, b'', b'')))
            result = boundary.verify('/tmp', ['out'], (), runner=lambda *args: next(calls))
        self.assertFalse(result['verified'])

    def test_non_darwin_platform_is_unsupported(self):
        self.assertIsInstance(for_platform('linux'), UnsupportedBoundary)
        self.assertFalse(for_platform('linux').verify('/', [], (), runner=lambda *args: None)['verified'])

    def test_quote_rejects_control_characters(self):
        with self.assertRaises(ValueError):
            _sbpl_quote('/tmp/bad\npath')


if __name__ == '__main__':
    unittest.main()
