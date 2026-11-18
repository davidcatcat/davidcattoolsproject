"""Offline tests for the #2310 stale gateway install refusal."""

import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from llmtool.developer_routes import GwAgentRoute, RouteError


REPO_GATEWAY = (Path(__file__).resolve().parents[2] /
                'api-agent-gateway' / 'api_agent_gateway')


class GwAgentInstallCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()

    def _install(self):
        install = Path(self.temp.name) / 'install'
        install.mkdir()
        package = install / 'api-agent-gateway'
        package.mkdir()
        shutil.copytree(REPO_GATEWAY, package / 'api_agent_gateway')
        os.chmod(package, 0o700)
        os.chmod(package / 'api_agent_gateway', 0o700)
        return package

    def _route(self, package):
        return GwAgentRoute({
            'package_path': str(package), 'profile_name': 'fake',
            'profile': {'adapter': 'openai-chat-tools',
                        'base_url': 'http://127.0.0.1:9/v1',
                        'model': 'fake-model', 'auth_env': None,
                        'context_cap_tokens': 10000, 'max_output_tokens': 1000,
                        'allowed_data_classes': ['synthetic']},
            'default_model': 'fake-model', 'default_effort': 'low',
            'authorization_ref': 'pm-task-ref',
            'allowed_data_classes': ['synthetic'],
        })

    def _request(self):
        return {'root': str(self.root), 'task': {
            'task_id': 'task-001', 'read_paths': ['source.py'],
            'write_paths': ['source.py'],
            'egress_basis': 'Synthetic test context.', 'data_class': 'synthetic',
            'budgets': {'max_turns': 4, 'max_input_tokens': 10000,
                        'max_output_tokens': 1000, 'max_wall_seconds': 60,
                        'max_tool_result_bytes': 4096, 'max_sent_bytes': 100000},
        }}

    def test_current_install_is_accepted_and_staged_as_before(self):
        route = self._route(self._install())
        tmpdir = tempfile.mkdtemp(dir=self.temp.name)
        with mock.patch.object(route, '_load_credential', return_value=None):
            route.prepare_files(tmpdir, self._request())
        for name in ('task.json', 'authorization.json', 'profiles.json'):
            self.assertTrue((Path(tmpdir) / name).is_file())

    def test_stale_install_is_refused_before_credentials_or_model_call(self):
        package = self._install()
        cli = package / 'api_agent_gateway' / 'cli.py'
        cli.write_text(cli.read_text(encoding='utf-8') + '\n# stale install\n',
                       encoding='utf-8')
        route = self._route(package)
        tmpdir = tempfile.mkdtemp(dir=self.temp.name)
        with mock.patch.object(route, '_load_credential',
                               side_effect=AssertionError('credential resolved')) as loader:
            with self.assertRaises(RouteError) as caught:
                route.prepare_files(tmpdir, self._request())
        self.assertEqual(caught.exception.code, 'GW_AGENT_INSTALL_STALE')
        loader.assert_not_called()
        self.assertEqual(list(Path(tmpdir).iterdir()), [])
