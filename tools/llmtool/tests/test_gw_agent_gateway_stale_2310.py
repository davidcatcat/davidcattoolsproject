"""#2310: a gw-agent dispatch refuses a stale installed gateway before any Task change."""

import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

from llmtool.developer_routes import gw_agent
from llmtool.errors import LLMToolError

import test_gw_agent_route_wiring as wiring


SOURCE = Path(__file__).resolve().parents[2] / 'api-agent-gateway' / 'api_agent_gateway'


def _copy_source(target):
    shutil.copytree(SOURCE, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    return target


class InstalledGatewayStaleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.source = _copy_source(self.base / 'source' / 'api_agent_gateway')
        self.install = self.base / 'install'
        _copy_source(self.install / 'api_agent_gateway')

    def _stale(self, package_path=None):
        return gw_agent.installed_gateway_stale(
            str(self.install) if package_path is None else package_path,
            source_dir=self.source)

    def test_identical_copy_is_not_stale(self):
        self.assertFalse(self._stale())

    def test_changed_file_is_stale(self):
        (self.install / 'api_agent_gateway' / 'contract.py').write_text('# old\n')
        self.assertTrue(self._stale())

    def test_missing_or_extra_file_is_stale(self):
        (self.install / 'api_agent_gateway' / 'extra.py').write_text('x = 1\n')
        self.assertTrue(self._stale())
        (self.install / 'api_agent_gateway' / 'extra.py').unlink()
        (self.install / 'api_agent_gateway' / 'contract.py').unlink()
        self.assertTrue(self._stale())

    def test_bytecode_caches_are_ignored(self):
        cache = self.install / 'api_agent_gateway' / '__pycache__'
        cache.mkdir()
        (cache / 'cli.cpython-313.pyc').write_bytes(b'cache')
        self.assertFalse(self._stale())

    def test_symlink_in_install_counts_as_stale(self):
        link = self.install / 'api_agent_gateway' / 'link.py'
        os.symlink(self.install / 'api_agent_gateway' / 'cli.py', link)
        self.assertTrue(self._stale())

    def test_absent_install_or_source_is_not_checked(self):
        self.assertFalse(self._stale(str(self.base / 'nowhere')))
        self.assertFalse(gw_agent.installed_gateway_stale(
            str(self.install), source_dir=self.base / 'no-source'))
        self.assertFalse(self._stale(''))
        self.assertFalse(self._stale(0))

    def test_tilde_path_expands_from_home(self):
        with patch.dict(os.environ, {'HOME': str(self.base)}):
            self.assertFalse(self._stale('~/install'))
            (self.install / 'api_agent_gateway' / 'cli.py').write_text('# old\n')
            self.assertTrue(self._stale('~/install'))

    def test_shipped_source_dir_is_the_repository_gateway(self):
        self.assertEqual(gw_agent.GATEWAY_SOURCE_DIR, SOURCE)


class GatewayStaleDelegateTests(unittest.TestCase):
    """Real delegate path, real TaskStore and D6 record; only the model is fake."""

    def setUp(self):
        self.harness = wiring.GwAgentRealStoreTests(methodName='_delegate')
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        # The delegate fixture installs the repository gateway; a changed copy
        # stands for the gateway source on main after an unrefreshed merge.
        self.main_source = _copy_source(Path(temp.name) / 'api_agent_gateway')

    def test_stale_install_refuses_before_any_task_change_or_model_call(self):
        (self.main_source / 'contract.py').write_text(
            (self.main_source / 'contract.py').read_text() + '\n# newer on main\n')
        runner = Mock(side_effect=AssertionError('gateway run'))
        task_id = None
        with patch.object(gw_agent, 'GATEWAY_SOURCE_DIR', self.main_source), \
             patch('llmtool.task_submission_cli.GATEWAY_SOURCE_DIR', self.main_source):
            with self.assertRaises(LLMToolError) as raised:
                self.harness._delegate('stale-gateway.md', route_runner=runner)
            task_id = self.harness.task_id
        message = str(raised.exception)
        self.assertTrue(message.startswith('DEVELOPER_GW_AGENT_GATEWAY_STALE: '), message)
        self.assertIn('rsync -a --delete --exclude __pycache__', message)
        self.assertIn(str(self.main_source), message)
        runner.assert_not_called()
        self.assertEqual(self.harness.server.requests, [])
        task = self.harness.store.get(task_id)
        self.assertEqual(task['state'], 'PM_RESPONDED')
        self.assertIsNone(task.get('developer'))

    def test_matching_install_delegates_as_before(self):
        with patch.object(gw_agent, 'GATEWAY_SOURCE_DIR', self.main_source):
            code, task = self.harness._delegate('fresh-gateway.md')
        self.assertEqual(code, 0)
        self.assertEqual(task['developer']['state'], 'DEVELOPER_RESPONDED')
        self.assertEqual(task['developer']['changed_files'], ['fresh-gateway.md'])


if __name__ == '__main__':
    unittest.main()
