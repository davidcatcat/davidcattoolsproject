"""Offline fixtures for the EC2 runner installer and ownership selftest."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

HERE = Path(__file__).parent
INSTALL = HERE / 'install.sh'
SELFTEST = HERE / 'selftest.sh'


class InstallSelftestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.tree = Path(self.temp.name) / 'actions-runner'
        self.user = subprocess.check_output(['id', '-un'], text=True).strip()
        self.group = subprocess.check_output(['id', '-gn'], text=True).strip()
        for directory, mode in (('', 0o755), ('_work', 0o700), ('_tmp', 0o700),
                                ('_diag', 0o750), ('bin', 0o755)):
            path = self.tree / directory
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(mode)
        for name, mode in (('.runner', 0o600), ('.credentials', 0o600),
                           ('.env', 0o644), ('.path', 0o644),
                           ('bin/Runner.Listener', 0o755),
                           ('_work/job', 0o600), ('_tmp/temp', 0o600),
                           ('_diag/worker.log', 0o640)):
            path = self.tree / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture')
            path.chmod(mode)

    def tearDown(self):
        self.temp.cleanup()

    def run_selftest(self, *extra):
        return subprocess.run(
            ['bash', str(SELFTEST), '--root', str(self.tree), '--account', 'ghrunner',
             '--account-owner', self.user, '--root-owner', self.user, '--root-group', self.group,
             '--account-group', self.group, *extra], text=True, capture_output=True)

    def run_installer(self, *extra):
        return subprocess.run(
            ['bash', str(INSTALL), '--root', str(self.tree), '--dry-run',
             '--account', 'ghrunner', *extra], text=True, capture_output=True)

    def different_owner(self):
        return 'root' if self.user != 'root' else 'nobody'

    def make_version_layout(self):
        bin_dir = self.tree / 'bin.2.338.0'
        (self.tree / 'bin').rename(bin_dir)
        ext_dir = self.tree / 'externals.2.338.0'
        node_dir = ext_dir / 'node20' / 'lib'
        node_dir.mkdir(parents=True)
        payload = node_dir / 'payload.js'
        payload.write_text('fixture')
        payload.chmod(0o644)
        (node_dir / 'alias.js').symlink_to('payload.js')
        (self.tree / 'bin').symlink_to('bin.2.338.0')
        (self.tree / 'externals').symlink_to(ext_dir)
        return bin_dir, ext_dir

    def test_correct_fixture_passes(self):
        result = self.run_selftest()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_wrong_root_owner_fails(self):
        result = self.run_selftest('--root-owner', self.different_owner())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('owner is', result.stderr)

    def test_wrong_account_owner_fails(self):
        result = self.run_selftest('--account-owner', self.different_owner())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('owner is', result.stderr)

    def test_wrong_modes_fail(self):
        for path, mode in ((self.tree / 'bin', 0o775),
                           (self.tree / '.credentials', 0o640),
                           (self.tree / '_work', 0o755),
                           (self.tree / '_tmp', 0o755),
                           (self.tree / '_diag', 0o755)):
            original = path.stat().st_mode & 0o777
            path.chmod(mode)
            result = self.run_selftest()
            self.assertNotEqual(result.returncode, 0, str(path))
            self.assertIn('mode is', result.stderr)
            path.chmod(original)

    def test_missing_writable_directories_fail(self):
        for name in ('_work', '_tmp', '_diag'):
            path = self.tree / name
            path.rename(self.tree / (name + '.saved'))
            result = self.run_selftest()
            self.assertNotEqual(result.returncode, 0, name)
            self.assertIn('missing runner directory ' + name, result.stderr)
            (self.tree / (name + '.saved')).rename(path)

    def test_symlink_is_refused_by_installer_and_selftest(self):
        (self.tree / 'linked').symlink_to(self.tree / '.runner')
        for result in (self.run_installer(), self.run_selftest()):
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('refusing symlink', result.stderr)

    def test_runner_version_symlinks_pass_dry_run_and_selftest(self):
        self.make_version_layout()
        (self.tree / '_work' / '_update').mkdir()
        (self.tree / '_work' / '_update' / 'dangling').symlink_to('missing')
        for result in (self.run_installer(), self.run_selftest()):
            self.assertEqual(result.returncode, 0, result.stderr)
        plan = self.run_installer()
        self.assertNotIn(str(self.tree / 'bin'), plan.stdout)
        self.assertNotIn(str(self.tree / 'externals'), plan.stdout)
        self.assertNotIn(str(self.tree / '_work' / '_update'), plan.stdout)

    def test_invalid_runner_symlink_targets_are_refused(self):
        cases = ('escape', 'dangling', 'other-version', 'elsewhere')
        for case in cases:
            with self.subTest(case=case):
                self.make_version_layout()
                link = self.tree / 'externals.2.338.0' / 'node20' / 'lib' / case
                if case == 'escape':
                    link.symlink_to(self.temp.name)
                elif case == 'dangling':
                    link.symlink_to('missing')
                elif case == 'other-version':
                    other = self.tree / 'externals.2.339.0'
                    other.mkdir()
                    link.symlink_to(other)
                else:
                    link.symlink_to(self.tree / 'bin.2.338.0' / 'Runner.Listener')
                for result in (self.run_installer(), self.run_selftest()):
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('refusing symlink', result.stderr)
                self.tearDown()
                self.setUp()

    def test_special_file_is_refused(self):
        os.mkfifo(self.tree / 'pipe')
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('refusing special file', result.stderr)

    def test_account_parsing_is_restricted(self):
        for account in ('ghrunner1', 'ghrunner0', 'ghrunner10', 'other'):
            result = subprocess.run(['bash', str(INSTALL), '--account', account, '--dry-run',
                                     '--root', str(self.tree)], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0, account)
            self.assertIn('account must be', result.stderr)

    def test_dry_run_matches_plan_without_mutation(self):
        before = {p.relative_to(self.tree): (p.stat().st_mode & 0o777, p.stat().st_uid,
                  p.stat().st_gid, p.read_bytes() if p.is_file() else None)
                  for p in self.tree.rglob('*')}
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'chown -h root:root -- {self.tree}', result.stdout)
        self.assertIn(f'chmod 0755 -- {self.tree}', result.stdout)
        self.assertIn(f'chown -h ghrunner:ghrunner -- {self.tree / "_work"}', result.stdout)
        self.assertIn(f'chmod 0700 -- {self.tree / "_work"}', result.stdout)
        self.assertIn(f'chmod 0750 -- {self.tree / "_diag"}', result.stdout)
        after = {p.relative_to(self.tree): (p.stat().st_mode & 0o777, p.stat().st_uid,
                 p.stat().st_gid, p.read_bytes() if p.is_file() else None)
                 for p in self.tree.rglob('*')}
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
