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
                with self.assertRaises(ValueError):
                    boundary._policy(root, ['.GIT/x'], [])
                with self.assertRaises(ValueError):
                    boundary._policy(root, ['.Git/sub/x'], [])

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

    def test_claude_temp_profile_is_exact_and_opt_in(self):
        path = '/tmp/a b.c/file name.py'
        second = '/tmp/other.py'
        profile = SandboxExecBoundary._profile([path, second],
                                              claude_tmp_paths=[path, second])
        rule = ('(allow file-write* (regex #"^/tmp/a[ ]b[.]c/file[ ]name[.]py'
                '[.]tmp[.][0-9]+[.]' + '[0-9a-f]' * 12 + '$"))')
        self.assertEqual(profile.count(rule), 1)
        self.assertEqual(profile.count('(allow file-write* (regex #'), 2)
        self.assertNotIn('(subpath "/tmp/a[ ]b[.]c', profile)
        self.assertNotIn('{12}', profile)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            root.mkdir()
            subprocess.run(['/usr/bin/git', 'init', str(root)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            plain = SandboxExecBoundary(executable='/bin/true')
            source_path = str(root / 'source.py')
            opted_out = plain._profile([source_path])
            expected = ('(version 1)\n(deny default)\n(allow process*)\n'
                        '(allow sysctl-read)\n(allow mach*)\n(allow ipc*)\n'
                        '(allow signal)\n(allow network*)\n(allow file-read*)\n'
                        '(deny file-write*)\n(allow file-write* (literal "' + source_path + '"))\n'
                        '(allow file-write* (literal "/dev/null"))\n'
                        '(allow file-write* (literal "/dev/tty"))')
            opted_in = SandboxExecBoundary._profile([source_path],
                                                    claude_tmp_paths=[source_path])
            self.assertEqual(opted_out, expected)
            self.assertNotEqual(opted_in, opted_out)
            prepared_opt_in = SandboxExecBoundary(
                executable=sys.executable, allow_claude_tmp=True).prepare(
                    str(root), ['source.py'])[0][2]
            prepared_default = SandboxExecBoundary(executable=sys.executable).prepare(
                str(root), ['source.py'])[0][2]
            self.assertIn('(allow file-write* (regex #', prepared_opt_in)
            self.assertNotIn('(allow file-write* (regex #', prepared_default)
            with self.assertRaises(ValueError):
                SandboxExecBoundary(allow_claude_tmp=1)

    def test_claude_probe_wrong_result_fails_verification(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            root.mkdir()
            subprocess.run(['/usr/bin/git', 'init', str(root)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            boundary = SandboxExecBoundary(executable='/bin/true', tmpdir=temp,
                                           allow_claude_tmp=True)
            policy_root, paths, states = boundary._policy(root, ['src/file.py'], [])
            profile = boundary._profile(paths, states, (), paths)
            boundary._prepared = (str(policy_root), ('src/file.py',), (), paths, states,
                                  profile, hashlib.sha256(profile.encode()).hexdigest())
            programs = []

            def runner(argv, *_args):
                code = argv[-1]
                programs.append(code)
                if 'os.replace' in code:
                    return 1, b'', b''
                if 'sys.exit(3)' in code:
                    return 3, b'', b''
                if 'llmtool-claude-hardlink-' in code and 'os.link' in code:
                    return 3, b'', b''
                if 'os.link' in code:
                    return 0, b'', b''
                if 'Path("/dev/null").write_bytes' in code:
                    return 0, b'', b''
                return (0 if 'llmtool' not in code else 1), b'', b''

            proof = boundary.verify(root, ['src/file.py'], (), runner=runner)
            self.assertFalse(proof['verified'])
            self.assertTrue(programs)
            for code in programs:
                compile(code, 'probe', 'exec')
            self.assertTrue(any(probe.get('operation') == 'claude_tmp_rename' and
                                not probe['passed'] for probe in proof['probes']))

    def test_claude_denial_probes_require_permission_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            (root / 'src').mkdir(parents=True)
            subprocess.run(['/usr/bin/git', 'init', str(root)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            boundary = SandboxExecBoundary(executable='/bin/true', tmpdir=temp,
                                           allow_claude_tmp=True)
            policy_root, paths, states = boundary._policy(root, ['src/file.py'], [])
            profile = boundary._profile(paths, states, (), paths)
            boundary._prepared = (str(policy_root), ('src/file.py',), (), paths, states,
                                  profile, hashlib.sha256(profile.encode()).hexdigest())

            for operation in ('claude_tmp_name', 'claude_tmp_hardlink'):
                for wrong_rc in (1, 0, 4):
                    with self.subTest(operation=operation, rc=wrong_rc):
                        def runner(argv, *_args):
                            code = argv[-1]
                            if 'os.replace' in code:
                                return 0, b'', b''
                            if 'llmtool-claude-hardlink-' in code and 'os.link' in code:
                                return (wrong_rc if operation == 'claude_tmp_hardlink' else 3), b'', b''
                            if 'sys.exit(3)' in code:
                                return (wrong_rc if operation == 'claude_tmp_name' else 3), b'', b''
                            if 'os.link' in code:
                                return 0, b'', b''
                            if 'Path("/dev/null").write_bytes' in code:
                                return 0, b'', b''
                            return (0 if 'llmtool' not in code else 1), b'', b''

                        proof = boundary.verify(root, ['src/file.py'], (), runner=runner)
                        self.assertFalse(proof['verified'])
                        self.assertTrue(any(probe.get('operation') == operation and
                                            not probe['passed'] for probe in proof['probes']))

    def test_non_darwin_platform_is_unsupported(self):
        self.assertIsInstance(for_platform('linux'), UnsupportedBoundary)
        self.assertFalse(for_platform('linux').verify('/', [], (), runner=lambda *args: None)['verified'])

    def test_quote_rejects_control_characters(self):
        with self.assertRaises(ValueError):
            _sbpl_quote('/tmp/bad\npath')

    def test_gw_agent_tmp_copy_is_unprepared_and_leaves_the_original_closed(self):
        original = SandboxExecBoundary(executable='/bin/true', denied_dirs=('blocked',),
                                       tmpdir='/tmp')
        original._prepared = ('stale',)
        copy = original.with_gw_agent_tmp()
        self.assertTrue(copy.allow_gw_agent_tmp)
        self.assertIsNone(copy._prepared)
        self.assertEqual((copy.executable, copy.denied_dirs, copy.tmpdir),
                         ('/bin/true', ('blocked',), '/tmp'))
        self.assertFalse(original.allow_gw_agent_tmp)

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

    @unittest.skipUnless(sys.platform == 'darwin', 'requires real macOS sandbox-exec')
    def test_real_sandbox_claude_temp_rename_and_denials(self):
        executable = '/usr/bin/sandbox-exec'
        if not os.path.isfile(executable) or not os.access(executable, os.X_OK):
            self.skipTest('sandbox-exec unavailable')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            (root / 'src').mkdir(parents=True)
            subprocess.run(['/usr/bin/git', 'init', str(root)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            target = root / 'src' / 'file.py'
            target.write_bytes(b'original')
            target.chmod(0o640)
            boundary = SandboxExecBoundary(executable=executable, tmpdir=temp,
                                           allow_claude_tmp=True)
            boundary.prepare(root, ['src/file.py'])

            def runner(argv, cwd, env, timeout):
                result = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        timeout=timeout)
                return result.returncode, result.stdout, result.stderr

            proof = boundary.verify(root, ['src/file.py'], (), runner=runner)
            self.assertTrue(proof['verified'], proof['probes'])
            self.assertEqual(target.read_bytes(), b'original')
            self.assertEqual(target.stat().st_mode & 0o777, 0o640)
            source = Path(root).parent / 'claude-hardlink-control-source'
            control_link = target.with_name(target.name + '.tmp.123.0123456789ab')
            source.write_bytes(b'control')
            os.link(source, control_link)
            self.assertEqual(control_link.read_bytes(), b'control')
            control_link.unlink()
            source.unlink()
            unwrapped_probe = target.with_name(target.name + '.tmp.x.0123456789ab')
            unwrapped_probe.write_bytes(b'probe')
            self.assertEqual(unwrapped_probe.read_bytes(), b'probe')
            unwrapped_probe.unlink()
            claude_probes = [probe for probe in proof['probes']
                             if probe.get('operation', '').startswith('claude_tmp')]
            self.assertEqual(len(claude_probes), 7)
            self.assertEqual({probe['operation'] for probe in claude_probes},
                             {'claude_tmp_rename', 'claude_tmp_name',
                              'claude_tmp_hardlink'})
            self.assertEqual([probe['operation'] for probe in claude_probes].count(
                'claude_tmp_rename'), 1)
            self.assertEqual([probe['operation'] for probe in claude_probes].count(
                'claude_tmp_name'), 5)
            self.assertEqual([probe['operation'] for probe in claude_probes].count(
                'claude_tmp_hardlink'), 1)
            self.assertTrue(all(probe['passed'] for probe in claude_probes))


if __name__ == '__main__':
    unittest.main()
