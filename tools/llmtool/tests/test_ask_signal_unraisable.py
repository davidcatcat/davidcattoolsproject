"""#1851: a TERM inside a half-built object leaves no finalizer traceback on stderr."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest

import yaml

TOOL = Path(__file__).resolve().parents[1]
SCRIPT = '''
import os
import signal
import sys
from unittest.mock import patch
from llmtool import cli
from llmtool.errors import LLMToolError


class HalfBuilt:
    def __init__(self):
        if sys.argv[1] == 'term':
            os.kill(os.getpid(), signal.SIGTERM)
        self.ready = True if sys.argv[1] == 'term' else self.missing

    def __del__(self):
        self.ready


class Client:
    def complete(self, messages):
        try:
            HalfBuilt()
        except AttributeError:
            raise LLMToolError('synthetic provider failure') from None


with patch.object(cli, 'create_client', lambda config, key: Client()):
    raise SystemExit(cli.main(['ask', 'SYNTHETIC_PROMPT_1851', '--save-diagnostics']))
'''


@unittest.skipUnless(os.name == 'posix', 'requires POSIX default signals')
class AskSignalUnraisableTests(unittest.TestCase):
    def run_ask(self, mode):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / 'home'
            config = home / '.config/llmtool/config.yaml'
            config.parent.mkdir(parents=True)
            config.write_text(
                'providers:\n  default:\n    type: openai-compatible\n'
                '    base_url: http://127.0.0.1:9\n    model: synthetic-model\n'
                'active_provider: default\n'
                'diagnostics:\n  persist_metadata: true\n  max_runs: 1\n'
                '  max_total_bytes: 1048576\n  max_run_bytes: 65536\n  max_age_days: 14\n')
            env = dict(os.environ, HOME=str(home), LLMTOOL_HOME=str(Path(temp) / 'state'),
                       PYTHONPATH=os.pathsep.join((str(TOOL), str(Path(yaml.__file__).resolve().parent.parent))))
            return subprocess.run([sys.executable, '-B', '-c', SCRIPT, mode], cwd=str(TOOL), env=env,
                                  stdin=subprocess.DEVNULL, capture_output=True, timeout=60)

    def test_term_inside_half_built_object_leaves_stderr_empty(self):
        result = self.run_ask('term')
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (128 + signal.SIGTERM, b'', b''))

    def test_non_signal_failure_still_reports_finalizer_errors(self):
        result = self.run_ask('error')
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertIn(b'llmtool: synthetic provider failure', result.stderr)
        self.assertIn(b'Exception ignored in', result.stderr)


if __name__ == '__main__':
    unittest.main()
