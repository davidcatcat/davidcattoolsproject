"""Policy construction and verification tests for direct route boundaries."""

import hashlib
import os
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

    def test_rejects_state_directory_equal_to_temp_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            root.mkdir()
            subprocess.run(['/usr/bin/git', 'init', str(root)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            boundary = SandboxExecBoundary(executable='/bin/true', tmpdir=temp)
            with self.assertRaises(ValueError):
                boundary._policy(root, ['out.txt'], [temp])

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
                if 'os.link' in argv[-1]:
                    return 0, b'', b''
                return (0 if len(calls) == 1 else 1), b'', b''
            result = boundary.verify('/tmp', ['out'], (), runner=runner)
        self.assertTrue(result['verified'])
        self.assertIn('Path("/dev/null").write_bytes', calls[-1][-1])
        hardlink_probes = [probe for probe in result['probes']
                           if probe.get('operation') == 'hardlink']
        self.assertEqual(len(hardlink_probes), 1)
        self.assertTrue(hardlink_probes[0]['passed'])
        self.assertTrue(any('os.link' in argv[-1] for argv in calls))

    def test_opt_in_gw_agent_temp_allowance_is_narrow_and_probed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            root.mkdir()
            subprocess.run(['/usr/bin/git', 'init', str(root)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            boundary = SandboxExecBoundary(executable='/bin/true', tmpdir=temp,
                                           allow_gw_agent_tmp=True)
            policy_root, paths, states = boundary._policy(root, ['src/file.py'], [])
            profile = boundary._profile(paths, states,
                                        boundary._gw_tmp_parents(policy_root, ['src/file.py']))
            self.assertIn('.gw-agent-tmp-' + '[0-9a-f]' * 16, profile)
            self.assertNotIn('{16}', profile)
            digest = hashlib.sha256(profile.encode()).hexdigest()
            boundary._prepared = (str(policy_root), ('src/file.py',), (), paths, states,
                                  profile, digest)
            calls = []

            def runner(argv, cwd, env, timeout):
                calls.append(argv[-1])
                program = argv[-1]
                if 'gw-agent-tmp-not-hex' in program:
                    return 1, b'', b''
                if 'gw-agent-tmp-0123456789abcdef' in program:
                    return 0, b'', b''
                if 'llmtool-task-parent-probe' in program or 'llmtool-boundary-probe' in program:
                    return 1, b'', b''
                if 'llmtool-hardlink-probe' in program or 'os.link' in program:
                    return 0, b'', b''
                return 0, b'', b''

            result = boundary.verify(root, ['src/file.py'], (), runner=runner)
            self.assertTrue(result['verified'])
            probes = [probe for probe in result['probes']
                      if probe.get('operation') == 'gw_agent_tmp']
            self.assertEqual(len(probes), 2)
            self.assertTrue(all(probe['passed'] for probe in probes))
            self.assertTrue(any('gw-agent-tmp-not-hex' in code for code in calls))
            self.assertTrue(any('llmtool-task-parent-probe' in code for code in calls))

    def test_non_darwin_platform_is_unsupported(self):
        self.assertIsInstance(for_platform('linux'), UnsupportedBoundary)
        self.assertFalse(for_platform('linux').verify('/', [], (), runner=lambda *args: None)['verified'])

    def test_quote_rejects_control_characters(self):
        with self.assertRaises(ValueError):
            _sbpl_quote('/tmp/bad\npath')

    @unittest.skipUnless(sys.platform == 'darwin', 'requires real macOS sandbox-exec')
    def test_real_sandbox_gw_temp_rule_and_out_of_scope_hardlink(self):
        executable = '/usr/bin/sandbox-exec'
        if not os.path.isfile(executable) or not os.access(executable, os.X_OK):
            self.skipTest('sandbox-exec unavailable')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            (root / 'src').mkdir(parents=True)
            subprocess.run(['/usr/bin/git', 'init', str(root)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            source = Path(temp) / 'outside'
            source.write_text('outside')
            boundary = SandboxExecBoundary(executable=executable, tmpdir=temp,
                                           allow_gw_agent_tmp=True)
            boundary.prepare(root, ['src/file.py'])

            def runner(argv, cwd, env, timeout):
                result = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        timeout=timeout)
                return result.returncode, result.stdout, result.stderr

            proof = boundary.verify(root, ['src/file.py'], (), runner=runner)
            self.assertTrue(proof['verified'], proof['probes'])
            temp_probes = [probe for probe in proof['probes']
                           if probe.get('operation') == 'gw_agent_tmp']
            self.assertEqual([probe['passed'] for probe in temp_probes], [True, True])
            hardlink = [probe for probe in proof['probes']
                        if probe.get('operation') == 'hardlink']
            self.assertEqual(len(hardlink), 1)
            self.assertTrue(hardlink[0]['passed'])


if __name__ == '__main__':
    unittest.main()
