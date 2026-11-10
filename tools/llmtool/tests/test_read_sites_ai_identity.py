"""Tests verifying AI GitHub identity and isolated environment configuration."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from llmtool import github_identity, review_dispatch, source_freshness, task_completion
from llmtool.errors import LLMToolError


class ReadSitesAiIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp_home = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_home.cleanup)
        self.home = str(Path(self.temp_home.name).resolve())
        self.expected_gh_config = github_identity.ai_gh_config_dir(self.home)
        self.ai_login = github_identity.ai_login()

    def test_review_dispatch_resolve_login_ignores_caller_gh_config_dir(self):
        result = SimpleNamespace(returncode=0, stdout=f'{self.ai_login}\n'.encode())
        with patch.dict(os.environ, {'HOME': self.home, 'GH_CONFIG_DIR': '/other'}, clear=False), \
                patch.object(review_dispatch.shutil, 'which', return_value='/bin/gh'), \
                patch.object(review_dispatch.subprocess, 'run', return_value=result) as run_mock:
            resolved = review_dispatch._resolve_github_login()
        self.assertEqual(resolved, self.ai_login)
        env = run_mock.call_args.kwargs['env']
        self.assertEqual(env.get('GH_CONFIG_DIR'), self.expected_gh_config)
        self.assertNotEqual(env.get('GH_CONFIG_DIR'), '/other')

    def test_task_completion_github_get_ignores_caller_gh_config_dir(self):
        r, w = os.pipe()
        os.write(w, b'{"id": 1}\n')
        os.close(w)
        fake_stdout = os.fdopen(r, 'rb')
        fake_proc = MagicMock()
        fake_proc.stdout = fake_stdout
        fake_proc.pid = 99999
        fake_proc.poll.return_value = 0
        fake_proc.wait.return_value = 0

        with patch.dict(os.environ, {'HOME': self.home, 'GH_CONFIG_DIR': '/other'}, clear=False), \
                patch.object(task_completion.shutil, 'which', return_value='/bin/gh'), \
                patch.object(task_completion, '_trusted_slug', return_value='org/repo'), \
                patch.object(task_completion.subprocess, 'Popen', return_value=fake_proc) as popen_mock:
            data = task_completion._github_get('pulls/1')

        self.assertEqual(data, {'id': 1})
        env = popen_mock.call_args.kwargs['env']
        self.assertEqual(env.get('GH_CONFIG_DIR'), self.expected_gh_config)
        self.assertNotEqual(env.get('GH_CONFIG_DIR'), '/other')

    def test_resolve_github_login_refuses_mismatched_account(self):
        result = SimpleNamespace(returncode=0, stdout=b'other-login\n')
        with patch.dict(os.environ, {'HOME': self.home}, clear=False), \
                patch.object(review_dispatch.shutil, 'which', return_value='/bin/gh'), \
                patch.object(review_dispatch.subprocess, 'run', return_value=result):
            with self.assertRaises(LLMToolError) as caught:
                review_dispatch._resolve_github_login()
        self.assertEqual(caught.exception.review_dispatch_code,
                         'REVIEWER_GITHUB_LOGIN_MISMATCH')

    def test_resolve_github_login_passes_matching_ai_account(self):
        result = SimpleNamespace(returncode=0, stdout=f'{self.ai_login}\n'.encode())
        with patch.dict(os.environ, {'HOME': self.home}, clear=False), \
                patch.object(review_dispatch.shutil, 'which', return_value='/bin/gh'), \
                patch.object(review_dispatch.subprocess, 'run', return_value=result):
            self.assertEqual(review_dispatch._resolve_github_login(), self.ai_login)

    def test_fetch_origin_main_isolates_env_and_configures_credential_helper(self):
        fake_proc = MagicMock()
        fake_proc.communicate.return_value = ('', '')

        with patch.dict(os.environ, {
                'HOME': self.home,
                'SSH_AUTH_SOCK': '/tmp/agent.sock',
                'GH_TOKEN': 'stub',
                'GH_CONFIG_DIR': '/other',
        }, clear=False), \
                patch.object(source_freshness.subprocess, 'Popen', return_value=fake_proc) as popen_mock:
            source_freshness._fetch_origin_main(Path('/fake/repo'))

        popen_mock.assert_called_once()
        env = popen_mock.call_args.kwargs['env']
        self.assertNotIn('SSH_AUTH_SOCK', env)
        self.assertNotIn('GH_TOKEN', env)
        self.assertEqual(env.get('GH_CONFIG_DIR'), self.expected_gh_config)
        self.assertEqual(env.get('GIT_TERMINAL_PROMPT'), '0')
        self.assertEqual(env.get('GIT_CONFIG_COUNT'), '3')
        self.assertEqual(env.get('GIT_CONFIG_KEY_0'), 'credential.helper')
        self.assertEqual(env.get('GIT_CONFIG_VALUE_0'), '')
        self.assertEqual(env.get('GIT_CONFIG_KEY_1'), 'credential.helper')
        self.assertEqual(env.get('GIT_CONFIG_VALUE_1'), '!gh auth git-credential')
        self.assertEqual(env.get('GIT_CONFIG_KEY_2'), 'url.https://github.com/.insteadOf')
        self.assertEqual(env.get('GIT_CONFIG_VALUE_2'), 'git@github.com:')


if __name__ == '__main__':
    unittest.main()
