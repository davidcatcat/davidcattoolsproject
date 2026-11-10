"""Tests for AI GitHub and Git credential isolation in developer route environments."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from llmtool.developer_routes import AgyRoute, ClaudeRoute, GwAgentRoute, RouteError
from llmtool.developer_routes import gw_agent


class DeveloperEnvAiIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home_dir = Path(self.temp.name) / 'home'
        self.home_dir.mkdir(parents=True, mode=0o700)
        self.tmpdir = Path(self.temp.name) / 'private-tmp'
        self.tmpdir.mkdir(parents=True, mode=0o700)
        self.base_env = {
            'HOME': str(self.home_dir),
            'GH_TOKEN': 'synthetic-not-a-token',
            'GITHUB_TOKEN': 'synthetic-not-a-token',
            'SSH_AUTH_SOCK': '/tmp/synthetic-ssh-sock',
            'GH_CONFIG_DIR': '/other',
        }

    def _assert_common_ai_isolation(self, env):
        self.assertTrue(env['GH_CONFIG_DIR'].endswith('/.config/gh-ai'))
        self.assertEqual(env['GH_CONFIG_DIR'], str(self.home_dir / '.config' / 'gh-ai'))
        self.assertNotIn('GH_TOKEN', env)
        self.assertNotIn('GITHUB_TOKEN', env)
        self.assertNotIn('SSH_AUTH_SOCK', env)
        self.assertEqual(env['GIT_CONFIG_COUNT'], '3')
        self.assertEqual(env['GIT_CONFIG_KEY_0'], 'credential.helper')
        self.assertEqual(env['GIT_CONFIG_VALUE_0'], '')
        self.assertEqual(env['GIT_CONFIG_KEY_1'], 'credential.helper')
        self.assertEqual(env['GIT_CONFIG_VALUE_1'], '!gh auth git-credential')
        self.assertEqual(env['GIT_CONFIG_KEY_2'], 'url.https://github.com/.insteadOf')
        self.assertEqual(env['GIT_CONFIG_VALUE_2'], 'git@github.com:')

    def test_claude_route_applies_ai_identity_and_preserves_builder_keys(self):
        config_dir = Path(self.temp.name) / 'claude-config'
        config_dir.mkdir(parents=True, mode=0o700)
        route = ClaudeRoute({'executable': 'claude', 'config_dir': str(config_dir)})
        with mock.patch.dict(os.environ, self.base_env, clear=True):
            env = route.build_env(str(self.tmpdir))
        self._assert_common_ai_isolation(env)
        self.assertEqual(env['CLAUDE_CONFIG_DIR'], str(config_dir))
        self.assertEqual(env['TMPDIR'], str(self.tmpdir))

    def test_agy_route_applies_ai_identity_and_preserves_builder_keys(self):
        agy_home = Path(self.temp.name) / 'agy-home'
        agy_home.mkdir(parents=True, mode=0o700)
        env_vars = dict(self.base_env, AGY_HOME=str(agy_home))
        route = AgyRoute({'executable': 'agy'})
        with mock.patch.dict(os.environ, env_vars, clear=True):
            env = route.build_env(str(self.tmpdir))
        self._assert_common_ai_isolation(env)
        self.assertEqual(env['AGY_HOME'], str(agy_home))
        self.assertEqual(env['TMPDIR'], str(self.tmpdir))

    def test_gw_agent_route_applies_ai_identity_and_preserves_builder_keys(self):
        package_dir = Path(self.temp.name) / 'pkg'
        gateway_dir = package_dir / 'api_agent_gateway'
        gateway_dir.mkdir(parents=True, mode=0o700)
        os.chmod(package_dir, 0o700)
        os.chmod(gateway_dir, 0o700)
        config = {
            'package_path': str(package_dir),
            'profile_name': 'fake',
            'profile': {
                'adapter': 'openai-chat-tools',
                'base_url': 'http://127.0.0.1:9/v1',
                'model': 'fake-model',
                'auth_env': 'GW_AGENT_API_KEY',
                'context_cap_tokens': 10000,
                'max_output_tokens': 1000,
                'allowed_data_classes': ['synthetic'],
            },
            'default_model': 'fake-model',
            'default_effort': 'low',
            'authorization_ref': 'pm-task-ref',
            'allowed_data_classes': ['synthetic'],
        }
        route = GwAgentRoute(config)
        with mock.patch.object(gw_agent.llm_config, 'resolve_profile_config', return_value={}), \
                mock.patch.object(gw_agent.llm_config, 'api_key', return_value='synthetic-not-a-token'), \
                mock.patch.dict(os.environ, self.base_env, clear=True):
            env = route.build_env(str(self.tmpdir))
        self._assert_common_ai_isolation(env)
        self.assertEqual(env['GW_AGENT_API_KEY'], 'synthetic-not-a-token')
        self.assertEqual(env['PYTHONPATH'], str(package_dir.resolve()))
        self.assertEqual(env['TMPDIR'], str(self.tmpdir))

    def test_fallback_to_user_home_when_home_is_not_in_environ(self):
        route = ClaudeRoute({'executable': 'claude'})
        with mock.patch.dict(os.environ, {'GH_TOKEN': 'synthetic-not-a-token'}, clear=True):
            env = route.build_env(str(self.tmpdir))
        self.assertTrue(env['GH_CONFIG_DIR'].endswith('/.config/gh-ai'))
        self.assertNotIn('GH_TOKEN', env)

    def test_existing_env_validation_runs_before_ai_env(self):
        claude_route = ClaudeRoute({'executable': 'claude'})
        with mock.patch.dict(os.environ, {'HOME': 'relative-path'}, clear=True):
            with self.assertRaises(RouteError) as caught:
                claude_route.build_env(str(self.tmpdir))
        self.assertEqual(caught.exception.code, 'CLAUDE_ENV_INVALID')

        agy_route = AgyRoute({'executable': 'agy'})
        with mock.patch.dict(os.environ, {'HOME': 'relative-path'}, clear=True):
            with self.assertRaises(RouteError) as caught:
                agy_route.build_env(str(self.tmpdir))
        self.assertEqual(caught.exception.code, 'AGY_ENV_INVALID')


if __name__ == '__main__':
    unittest.main()
