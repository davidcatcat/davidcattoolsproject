"""#1230 refusal-code pin: trust_cli repository and registry codes. Synthetic paths only."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from llmtool import trust_cli
from llmtool.trust import TrustError


class TrustCliCodeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir='/private/tmp' if sys.platform == 'darwin' else None)
        self.addCleanup(tmp.cleanup)
        self.top = Path(tmp.name).resolve()
        (self.top/'.git').mkdir()
        (self.top/'other').mkdir()

    def code(self, call):
        with self.assertRaises(TrustError) as caught:
            call()
        return caught.exception.code

    def test_git_failure_is_repository_invalid(self):
        for error in (subprocess.CalledProcessError(128, ['git']), FileNotFoundError('git')):
            with self.subTest(error=type(error).__name__), \
                    mock.patch.object(trust_cli.subprocess, 'run', side_effect=error):
                self.assertEqual(self.code(lambda: trust_cli._git('status')), 'TRUST_INIT_REPOSITORY_INVALID')

    def test_checkout_layout_and_origin_are_repository_invalid(self):
        git_dir = str(self.top/'.git')
        for label, answers in (
                ('common dir elsewhere', [str(self.top/'other'), git_dir]),
                ('origin not github', [git_dir, git_dir, 'https://example.invalid/owner/repo.git']),
                ('origin dot repo', [git_dir, git_dir, 'https://github.com/owner/...git'])):
            with self.subTest(label=label), \
                    mock.patch.object(trust_cli, '_checkout_top', return_value=self.top), \
                    mock.patch.object(trust_cli, '_git', side_effect=answers):
                self.assertEqual(self.code(lambda: trust_cli._checkout_values(self.top)),
                                 'TRUST_INIT_REPOSITORY_INVALID')

    def test_unreadable_registry_location_is_registry_unavailable(self):
        for error in (TrustError('TRUST_INIT_REPOSITORY_INVALID'), OSError('synthetic')):
            with self.subTest(error=type(error).__name__), \
                    mock.patch.object(trust_cli, '_git', side_effect=error):
                self.assertEqual(self.code(lambda: trust_cli._affected_active_records(str(self.top), 'b'*64)),
                                 'TRUST_REGISTRY_UNAVAILABLE')
        with mock.patch.object(trust_cli, '_git', return_value=str(self.top/'.git')):
            self.assertEqual(trust_cli._affected_active_records(str(self.top), 'b'*64), 0)


if __name__ == '__main__':
    unittest.main()
