"""B1/B4/B6: public output/status regressions, including real buffered OS pipes."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import test_diagnostics_ask_persistence as fixture
from llmtool import cli, diagnostics as d
from llmtool.diagnostics_cli import _runs_text
from llmtool.diagnostics_reader import DiagnosticsReader
from llmtool.diagnostics_settings import DiagnosticsSettings
from llmtool.diagnostics_store import DiagnosticsStore, persistence_supported
from llmtool.errors import coded_error

ROOT = Path(__file__).resolve().parents[3]
CHILD = '''
import sys
from unittest.mock import Mock, patch
from llmtool import cli
from llmtool.providers.base import Reply
client = Mock()
client.complete.return_value = Reply('small synthetic output', {'model':'test-model'})
with patch('llmtool.cli.create_client', return_value=client), patch('llmtool.cli.api_key', return_value=None):
    result = cli.main(sys.argv[1:])
if sys.argv[1] == 'ask':
    assert client.complete.call_count == 1
else:
    assert client.complete.call_count == 0
raise SystemExit(result)
'''


@unittest.skipUnless(persistence_supported(), 'requires POSIX diagnostics backend')
class CLIStatusRegressions(unittest.TestCase):
    setUp = fixture.PersistedAskTests.setUp
    set_config = fixture.PersistedAskTests.set_config
    invoke = fixture.PersistedAskTests.invoke

    def child(self, argv, broken=True):
        import yaml
        # System Python's user-site dependency lives under the real HOME;
        # preserve only its package location while isolating config in HOME.
        packages = os.pathsep.join((str(ROOT / 'tools/llmtool'),
                                    str(Path(yaml.__file__).resolve().parent.parent)))
        env = {'HOME': str(self.home), 'LLMTOOL_HOME': str(self.state),
               'PYTHONPATH': packages, 'PATH': '/usr/bin:/bin',
               'PYTHONDONTWRITEBYTECODE': '1'}
        read, write = os.pipe()
        if broken:
            os.close(read)
        try:
            result = subprocess.run([sys.executable, '-c', CHILD, *argv], cwd=ROOT,
                                    env=env, stdin=subprocess.DEVNULL, stdout=write,
                                    stderr=subprocess.PIPE, timeout=15)
        finally:
            os.close(write)
        if broken:
            return result.returncode, result.stderr.decode(), b''
        try:
            content = os.read(read, 8192)
        finally:
            os.close(read)
        return result.returncode, result.stderr.decode(), content

    def test_b1_real_buffered_pipe_ask_exit_and_saved_output_closed(self):
        code, err, _ = self.child(['ask', 'synthetic', '--save-diagnostics'])
        summaries = list((self.state / 'runs').glob('*/summary.json'))
        self.assertEqual(len(summaries), 1)
        summary = json.loads(summaries[0].read_text())
        self.assertEqual((code, summary['status']), (0, 'output_closed'), err)
        self.assertEqual(err, '')

    def test_b1_real_buffered_pipe_offline_commands_exit_zero_without_flush_noise(self):
        self.invoke(['--save-diagnostics'])
        run_id = next((self.state / 'runs').iterdir()).name
        for argv in (['runs'], ['trace', run_id], ['trace', run_id, '--json'], ['doctor']):
            with self.subTest(argv=argv):
                code, err, _ = self.child(argv)
                self.assertEqual((code, err), (0, ''))

    def test_b1_open_pipe_stdout_bytes_and_single_output_unchanged(self):
        self.assertEqual(self.child(['ask', 'synthetic'], broken=False),
                         (0, '', b'small synthetic output\n'))
        self.assertFalse(self.state.exists())

    def test_b4_invalid_settings_explicit_save_reports_requested_unavailable(self):
        self.set_config(extra='diagnostics:\n  max_runs: 0\n')
        result = self.invoke(['--save-diagnostics', '--verbose'])
        self.assertEqual(result[:2], (1, ''))
        self.assertIn('code=CONFIG_INVALID', result[2])
        self.assertIn('trace=unavailable', result[2])
        result[3].complete.assert_not_called()
        result[4].assert_not_called()
        self.assertFalse(self.state.exists())

    def test_b4_explicit_no_save_and_primary_input_error_precedence(self):
        self.set_config(extra='diagnostics:\n  max_runs: 0\n')
        result = self.invoke(['--no-save-diagnostics'])
        self.assertIn('trace=not-saved', result[2])
        self.assertNotIn('code=DIAGNOSTICS_', result[2])
        result[3].complete.assert_not_called()
        result = self.invoke(argv=['ask', 'x', '--skill', 'invalid/skill', '--save-diagnostics'])
        self.assertIn('code=USER_INPUT_ERROR', result[2])
        self.assertIn('trace=unavailable', result[2])
        self.assertNotIn('code=CONFIG_INVALID', result[2])
        result[3].complete.assert_not_called()

    def test_b6_incomplete_final_error_unknown_completed_none_or_actual(self):
        writer = DiagnosticsStore(DiagnosticsSettings(persist_metadata=True), home=self.state).start('c' * 32)
        self.assertIsNotNone(writer)
        from test_diagnostics_store import event
        self.assertTrue(writer.append(event(writer.run_id, 1, d.EventType.RUN_START)))
        self.assertTrue(writer.append(d.DiagnosticEvent(run_id=writer.run_id, event_id=2,
            timestamp=d.utc_timestamp(), type=d.EventType.ERROR, component='provider',
            severity=d.DiagnosticSeverity.ERROR,
            data={'code': 'PROVIDER_TIMEOUT', 'message': 'provider timed out'})))
        writer.close()
        self.invoke(['--save-diagnostics'])
        self.invoke(['--save-diagnostics'], fail=coded_error('provider timed out', 'PROVIDER_TIMEOUT', 'provider'))
        value = DiagnosticsReader(home=self.state).runs()
        rows = {row['status']: row for row in value['runs']}
        rendered = _runs_text(value)
        incomplete = next(line for line in rendered.splitlines() if 'recorded_status=incomplete' in line)
        self.assertIn('final_error_code=unknown', incomplete)
        self.assertIsNone(rows['incomplete']['final_error_code'])
        self.assertIn('final_error_code=none', next(line for line in rendered.splitlines() if 'recorded_status=success' in line))
        self.assertIn('final_error_code=PROVIDER_TIMEOUT', next(line for line in rendered.splitlines() if 'recorded_status=error' in line))
