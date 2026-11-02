"""Offline fixtures for the EC2 runner ownership and mode selftest."""
from pathlib import Path
import subprocess
import tempfile
import unittest

SELFTEST = Path(__file__).with_name('selftest.sh')


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

    def test_correct_fixture_passes(self):
        result = self.run_selftest()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_wrong_root_owner_fails(self):
        result = self.run_selftest('--root-owner', 'nobody')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('owner is', result.stderr)

    def test_wrong_account_owner_fails(self):
        (self.tree / '.credentials').chmod(0o600)
        result = self.run_selftest('--account', 'ghrunner2', '--account-owner', 'ghrunner2')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('owner is', result.stderr)

    def test_wrong_modes_fail(self):
        for path, mode in ((self.tree / 'bin', 0o775),
                           (self.tree / '.credentials', 0o640),
                           (self.tree / '_work', 0o755),
                           (self.tree / '_diag', 0o755)):
            original = path.stat().st_mode & 0o777
            path.chmod(mode)
            result = self.run_selftest()
            self.assertNotEqual(result.returncode, 0, str(path))
            self.assertIn('mode is', result.stderr)
            path.chmod(original)

    def test_symlink_is_refused(self):
        (self.tree / 'linked').symlink_to(self.tree / '.runner')
        result = self.run_selftest()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('refusing symlink', result.stderr)


if __name__ == '__main__':
    unittest.main()
