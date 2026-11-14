"""#1851: a TERM mutes only the finalizer error of the object being released."""
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
import builtins
import os
import signal
import sys
import threading
from unittest.mock import patch
from llmtool import cli
from llmtool.errors import LLMToolError


PROBE = [None]


class HalfBuilt:
    def __init__(self):
        probe = object()
        PROBE[0] = id(probe)
        if sys.argv[1].startswith('term'):
            os.kill(os.getpid(), signal.SIGTERM)
        self.ready = True if sys.argv[1].startswith('term') else self.missing

    def __del__(self):
        if sys.argv[1] == 'term-thread':
            worker = threading.Thread(target=Unrelated)
            worker.start()
            worker.join()
        if sys.argv[1] == 'term-reuse':
            Unrelated()
        self.ready


class Unrelated:
    def __del__(self):
        raise RuntimeError('UNRELATED_FINALIZER_1851')


KEEP = Unrelated() if sys.argv[1] == 'term-unrelated' else None


class Client:
    def complete(self, messages):
        try:
            HalfBuilt()
        except AttributeError:
            raise LLMToolError('synthetic provider failure') from None


def reused_id(value):
    return PROBE[0] if type(value) is Unrelated else builtins.id(value)


with patch.object(cli, 'create_client', lambda config, key: Client()), \
        patch.object(cli, 'id', reused_id, create=True):
    code = cli.main(['ask', 'SYNTHETIC_PROMPT_1851', '--save-diagnostics'])
del KEEP
raise SystemExit(code)
'''


@unittest.skipUnless(os.name == 'posix', 'requires POSIX default signals')
class AskSignalUnraisableTests(unittest.TestCase):
    def run_ask(self, mode):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp).resolve()  # the store refuses the macOS /var symlink
            home = temp / 'home'
            config = home / '.config/llmtool/config.yaml'
            config.parent.mkdir(parents=True)
            config.write_text(
                'providers:\n  default:\n    type: openai-compatible\n'
                '    base_url: http://127.0.0.1:9\n    model: synthetic-model\n'
                'active_provider: default\n'
                'diagnostics:\n  persist_metadata: true\n  max_runs: 1\n'
                '  max_total_bytes: 1048576\n  max_run_bytes: 65536\n  max_age_days: 14\n'
            )
            env = dict(os.environ, HOME=str(home), LLMTOOL_HOME=str(temp / 'state'),
                       PYTHONPATH=os.pathsep.join((str(TOOL), str(Path(yaml.__file__).resolve().parent.parent))))
            return subprocess.run([sys.executable, '-B', '-c', SCRIPT, mode], cwd=str(TOOL), env=env,
                                  stdin=subprocess.DEVNULL, capture_output=True, timeout=60)

    def test_term_inside_half_built_object_leaves_stderr_empty(self):
        result = self.run_ask('term')
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (128 + signal.SIGTERM, b'', b''))

    def test_unrelated_finalizer_error_after_term_still_reports(self):
        result = self.run_ask('term-unrelated')
        self.assertEqual((result.returncode, result.stdout), (128 + signal.SIGTERM, b''))
        self.assertIn(b'UNRELATED_FINALIZER_1851', result.stderr)

    def test_cleanup_reports_other_thread_and_reused_id_errors(self):
        for mode in ('term-thread', 'term-reuse'):
            with self.subTest(mode=mode):
                result = self.run_ask(mode)
                self.assertEqual((result.returncode, result.stdout), (128 + signal.SIGTERM, b''))
                self.assertIn(b'UNRELATED_FINALIZER_1851', result.stderr)
                self.assertNotIn(b'HalfBuilt', result.stderr)

    def test_non_signal_failure_still_reports_finalizer_errors(self):
        result = self.run_ask('error')
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertIn(b'llmtool: synthetic provider failure', result.stderr)
        self.assertIn(b'Exception ignored', result.stderr)


if __name__ == '__main__':
    unittest.main()
