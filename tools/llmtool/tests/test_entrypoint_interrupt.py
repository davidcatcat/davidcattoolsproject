"""D-34b: interruption after the product entry boundary, not interpreter site init."""
import builtins
import importlib
import io
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

from llmtool import cli

ROOT = Path(__file__).resolve().parents[3]


def declared_entrypoint():
    text = (ROOT / 'tools/llmtool/pyproject.toml').read_text()
    target = re.search(r'^llmtool = "([^"]+)"$', text, re.M).group(1)
    module, function = target.split(':')
    return getattr(importlib.import_module(module), function)


class EntrypointInterruptTests(unittest.TestCase):
    def safe_interrupt(self, call):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                result = call()
            except KeyboardInterrupt:
                self.fail('product-controlled interruption escaped its public boundary')
        self.assertEqual((result, out.getvalue(), err.getvalue()), (130, '', 'llmtool: interrupted\n'))

    def test_parser_construction_interrupt_has_safe_130(self):
        with patch('llmtool.cli.parser_for_cli', side_effect=KeyboardInterrupt):
            self.safe_interrupt(lambda: cli.main(['providers']))

    def test_parse_args_interrupt_has_safe_130(self):
        with patch.object(cli.SafeParser, 'parse_args', side_effect=KeyboardInterrupt):
            self.safe_interrupt(lambda: cli.main(['providers']))

    def test_declared_entrypoint_delegation_interrupt_has_safe_130(self):
        with patch('llmtool.cli.main', side_effect=KeyboardInterrupt):
            entry = declared_entrypoint()
            self.safe_interrupt(lambda: entry(['providers']))

    def test_declared_entrypoint_lazy_import_interrupt_has_safe_130(self):
        entry = declared_entrypoint()
        original = builtins.__import__
        def load(name, globals=None, locals=None, fromlist=(), level=0):
            if name == 'cli' and level == 1 and globals.get('__package__') == 'llmtool':
                raise KeyboardInterrupt()
            return original(name, globals, locals, fromlist, level)
        with patch('builtins.__import__', side_effect=load):
            self.safe_interrupt(lambda: entry(['providers']))

    def test_system_exit_usage_and_host_termination_are_not_swallowed(self):
        for code in (2, 129, 143):
            with self.subTest(code=code), patch('llmtool.cli.main', side_effect=SystemExit(code)):
                with self.assertRaises(SystemExit) as caught:
                    declared_entrypoint()(['providers'])
                self.assertEqual(caught.exception.code, code)

    @unittest.skipUnless(os.name == 'posix', 'requires actual SIGINT')
    def test_repeated_real_subprocess_startup_import_and_parser_interrupts(self):
        text = (ROOT / 'tools/llmtool/pyproject.toml').read_text()
        module = re.search(r'^llmtool = "([^:"]+):main"$', text, re.M).group(1)
        # #1688: the child must never wait with signal.pause(). CPython's pause() enters
        # pause(2) without checking for an already-tripped signal, so a SIGINT landing
        # between the ready-file write and pause(2) was lost and the child hung forever.
        # 'pending' forces exactly that ordering (SIGINT arrives before the wait begins)
        # on every sample; 'waiting' lets it arrive while the child waits. Both waits
        # cross an interpreter signal check at least every 10ms, so neither can lose it.
        with tempfile.TemporaryDirectory() as directory:
            for phase in ('cli-import', 'parser-build', 'parse-args'):
                for mode, sample in (('pending', 0), ('pending', 1), ('waiting', 0), ('waiting', 1)):
                    with self.subTest(phase=phase, mode=mode, sample=sample):
                        ready = str(Path(directory) / (phase + mode + str(sample)))
                        bootstrap = '''
import importlib, importlib.abc, importlib.util, signal, time
from pathlib import Path
signal.signal(signal.SIGINT, signal.default_int_handler)
phase, mode, ready, target = %r, %r, Path(%r), %r
def block(*args, **kwargs):
    if mode == 'pending':
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
        ready.write_text('product controlled')
        while signal.SIGINT not in signal.sigpending():
            time.sleep(0.01)
        # The real SIGINT is now pending before any wait: the ordering that hung
        # signal.pause(). Unblocking delivers it to default_int_handler here.
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT})
    else:
        ready.write_text('product controlled')
    while True:
        time.sleep(0.01)
class Loader(importlib.abc.Loader):
    def create_module(self, spec): return None
    def exec_module(self, module): block()
class Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'llmtool.cli': return importlib.util.spec_from_loader(fullname, Loader())
if phase == 'cli-import':
    import sys
    sys.meta_path.insert(0, Finder())
else:
    from llmtool import cli
    if phase == 'parser-build': cli.parser_for_cli = block
    else: cli.SafeParser.parse_args = block
raise SystemExit(importlib.import_module(target).main(['providers']))
''' % (phase, mode, ready, module)
                        environment = {'HOME': directory, 'PATH': '/usr/bin:/bin',
                            'PYTHONPATH': str(ROOT / 'tools/llmtool'), 'PYTHONDONTWRITEBYTECODE': '1'}
                        process = subprocess.Popen([sys.executable, '-c', bootstrap], cwd=ROOT,
                            env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
                        try:
                            deadline = time.monotonic() + 10
                            while not Path(ready).exists():
                                self.assertIsNone(process.poll())
                                self.assertLess(time.monotonic(), deadline)
                                time.sleep(0.01)
                            process.send_signal(signal.SIGINT)
                            # This bound protects against a stuck child; it is not a startup/exit latency SLA.
                            # Keep it aligned with the existing 10s real-subprocess startup budget so a busy
                            # full-suite Mac Python run does not turn scheduler delay into a false interrupt failure.
                            out, err = process.communicate(timeout=10)
                            self.assertEqual((process.returncode, out, err),
                                (130, b'', b'llmtool: interrupted\n'))
                        finally:
                            if process.poll() is None:
                                process.kill()
                            process.communicate(timeout=5)

    def test_module_and_direct_launcher_normal_help_version_and_usage(self):
        with tempfile.TemporaryDirectory() as home:
            environment = {'HOME': home, 'PATH': '/usr/bin:/bin',
                'PYTHONPATH': str(ROOT / 'tools/llmtool'), 'PYTHONDONTWRITEBYTECODE': '1'}
            for launcher in (['-m', 'llmtool'], [str(ROOT / 'tools/llmtool/llmtool.py')]):
                for flags, code in ((['--help'], 0), (['--version'], 0), (['invalid'], 2), (['providers'], 0)):
                    with self.subTest(launcher=launcher, flags=flags):
                        result = subprocess.run([sys.executable, *launcher, *flags], cwd=ROOT,
                            env=environment, stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
                        self.assertEqual(result.returncode, code, result.stderr)
                        self.assertNotIn(b'Traceback', result.stderr)
            self.assertEqual(list(Path(home).iterdir()), [])
