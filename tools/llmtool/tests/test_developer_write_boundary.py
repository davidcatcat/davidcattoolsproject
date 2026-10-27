"""Policy construction and verification tests for direct route boundaries."""

import unittest
import tempfile
from pathlib import Path
import subprocess
import sys
from unittest import mock

from llmtool.developer_routes.write_boundary import (
    SandboxExecBoundary, UnsupportedBoundary, _sbpl_quote, for_platform,
)


class DeveloperWriteBoundaryTests(unittest.TestCase):
    def test_profile_uses_exact_escaped_path_allows(self):
        profile = SandboxExecBoundary._profile(['/tmp/a"b'])
        self.assertIn('(deny file-write*)', profile)
        self.assertIn('(allow file-write* (literal "/tmp/a\\"b"))', profile)
        self.assertIn('(allow file-write* (literal "/dev/null"))', profile)
        self.assertIn('(allow file-write* (literal "/dev/tty"))', profile)

    def test_real_git_repository_prepares_with_git_metadata_excluded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            root.mkdir()
            subprocess.run(['/usr/bin/git', 'init', str(root)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            boundary = SandboxExecBoundary(executable=sys.executable, tmpdir=temp)
            prefix, digest = boundary.prepare(root, ['out.txt'])
            self.assertEqual(prefix[0], sys.executable)
            self.assertEqual(len(digest), 64)
            git_dir = Path(subprocess.check_output(
                ['/usr/bin/git', '-C', str(root), 'rev-parse', '--path-format=absolute', '--git-dir'],
                text=True).strip())
            common_dir = Path(subprocess.check_output(
                ['/usr/bin/git', '-C', str(root), 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                text=True).strip())
            self.assertTrue(git_dir.exists())
            self.assertEqual(git_dir, common_dir)
            with self.assertRaises(ValueError):
                boundary._policy(root, ['.git/config'], [])

    def test_denied_dirs_are_home_relative_and_reject_unsafe_names(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            root = home / 'repo'
            root.mkdir()
            subprocess.run(['/usr/bin/git', 'init', str(root)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            boundary = SandboxExecBoundary(executable='/bin/true', denied_dirs=('repo/blocked',))
            with mock.patch('pathlib.Path.home', return_value=home):
                with self.assertRaises(ValueError):
                    boundary._policy(root, ['blocked/out.txt'], [])
                bad = SandboxExecBoundary(executable='/bin/true', denied_dirs=('../outside',))
                with self.assertRaises(ValueError):
                    bad._policy(root, ['out.txt'], [])
                absolute = SandboxExecBoundary(executable='/bin/true', denied_dirs=('/tmp/private',))
                with self.assertRaises(ValueError):
                    absolute._policy(root, ['out.txt'], [])

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
        with mock.patch.object(boundary, '_policy', return_value=(Path('/tmp'),
                           ('/tmp/out',), ())):
            calls = iter(((0, b'', b''), (1, b'', b''), (0, b'', b''), (0, b'', b'')))
            result = boundary.verify('/tmp', ['out'], (), runner=lambda *args: next(calls))
        self.assertFalse(result['verified'])

    def test_verification_probes_dev_null_write(self):
        boundary = SandboxExecBoundary(executable='/bin/true', tmpdir='/tmp')
        boundary._prepared = ('/tmp', ('out',), (), ('/tmp/out',), (),
                              SandboxExecBoundary._profile(['/tmp/out']), 'a' * 64)
        with mock.patch.object(boundary, '_policy', return_value=(Path('/tmp'),
                           ('/tmp/out',), ())):
            calls = []
            def runner(argv, *args):
                calls.append(argv)
                if 'Path("/dev/null").write_bytes' in argv[-1]:
                    return 0, b'', b''
                return (0 if len(calls) == 1 else 1), b'', b''
            result = boundary.verify('/tmp', ['out'], (), runner=runner)
        self.assertTrue(result['verified'])
        self.assertIn('Path("/dev/null").write_bytes', calls[-1][-1])

    def test_non_darwin_platform_is_unsupported(self):
        self.assertIsInstance(for_platform('linux'), UnsupportedBoundary)
        self.assertFalse(for_platform('linux').verify('/', [], (), runner=lambda *args: None)['verified'])

    def test_quote_rejects_control_characters(self):
        with self.assertRaises(ValueError):
            _sbpl_quote('/tmp/bad\npath')


if __name__ == '__main__':
    unittest.main()
