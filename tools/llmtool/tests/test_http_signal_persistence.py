"""Offline subprocess regressions for HTTP ask signal finalization."""

import json
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmtool import cli


PROMPT = 'SYNTHETIC_PRIVATE_PROMPT_181'
RESPONSE = 'SYNTHETIC_PRIVATE_RESPONSE_181'
SETTINGS = ('diagnostics:\n  persist_metadata: true\n  max_runs: 1\n'
            '  max_total_bytes: 1048576\n  max_run_bytes: 65536\n  max_age_days: 14\n')


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class HttpSignalPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / 'home'
        self.state = self.root / 'state'
        config = self.home / '.config/llmtool/config.yaml'
        config.parent.mkdir(parents=True)
        self.received = threading.Event()
        self.release = threading.Event()
        self.large = False

        test = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', '0')))
                test.received.set()
                if not test.release.wait(5):
                    return
                content = RESPONSE if not test.large else 'x' * (4 * 1024 * 1024)
                body = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

        self.server = _Server(('127.0.0.1', 0), Handler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self.stop_server)
        config.write_text(
            'providers:\n  default:\n    type: openai-compatible\n'
            '    base_url: http://127.0.0.1:' + str(self.server.server_port) + '\n'
            '    model: synthetic-model\n    timeout_seconds: 10\n'
            'active_provider: default\n' + SETTINGS)
        self.env = dict(os.environ)
        self.env.update({'HOME': str(self.home), 'LLMTOOL_HOME': str(self.state),
                         'PYTHONPATH': os.pathsep.join((str(Path(__file__).resolve().parents[1]),
                                                      str(Path(yaml.__file__).resolve().parent.parent)))})

    def stop_server(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=3)
        self.assertFalse(self.server_thread.is_alive(), 'HTTP test server did not stop')

    def launch(self):
        python = sys.executable
        return subprocess.Popen([python, '-B', '-m', 'llmtool', 'ask', PROMPT,
                                 '--save-diagnostics'], cwd=str(Path(__file__).resolve().parents[1]), env=self.env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)

    def reader(self, *args):
        return subprocess.run([sys.executable, '-B', '-m', 'llmtool', *args],
                              cwd=str(Path(__file__).resolve().parents[1]), env=self.env,
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=4)

    def assert_finalized(self, process, signum, *, partial_stdout=False):
        out, err = process.communicate(timeout=4)
        self.assertEqual(process.returncode, 128 + signum, err)
        if partial_stdout:
            self.assertTrue(out and set(out) == {ord('x')} and len(out) < 4 * 1024 * 1024)
        else:
            self.assertEqual(out, b'')
        run_dirs = list((self.state / 'runs').iterdir())
        self.assertEqual(len(run_dirs), 1)
        run = run_dirs[0]
        summary = json.loads((run / 'summary.json').read_text())
        events = [json.loads(line) for line in (run / 'trace.jsonl').read_text().splitlines()]
        self.assertEqual(summary['status'], 'interrupted')
        self.assertEqual(events[-1]['type'], 'run.end')
        self.assertEqual(events[-1]['data']['status'], 'interrupted')
        self.assertFalse((run / '.reservation').exists())
        self.assertFalse((run / 'trace.partial.jsonl').exists())
        for path in run.iterdir():
            if path.suffix in ('.json', '.jsonl'):
                self.assertNotIn(PROMPT.encode(), path.read_bytes())
                self.assertNotIn(RESPONSE.encode(), path.read_bytes())
        self.assertNotIn(PROMPT.encode(), out + err)
        self.assertNotIn(RESPONSE.encode(), out + err)
        self.assertIn(b'interrupted', self.reader('runs').stdout)
        trace = self.reader('trace', run.name)
        self.assertEqual(trace.returncode, 0, trace.stderr)
        self.assertIn(b'state=complete', trace.stdout)
        doctor = self.reader('doctor')
        self.assertEqual(doctor.returncode, 0, doctor.stderr)
        self.assertIn(b'active_count=0 unknown_count=0', doctor.stdout)

        # max_runs=1 must be reusable as soon as the interrupted run closes.
        self.release.set()
        self.large = False
        following = self.reader('ask', PROMPT, '--save-diagnostics')
        self.assertEqual((following.returncode, following.stdout), (0, (RESPONSE + '\n').encode()),
                         following.stderr)
        remaining = list((self.state / 'runs').iterdir())
        self.assertEqual(len(remaining), 1)
        summary = json.loads((remaining[0] / 'summary.json').read_text())
        self.assertEqual(summary['status'], 'success')
        self.assertNotIn(PROMPT.encode(), (remaining[0] / 'trace.jsonl').read_bytes())

    def assert_signal_handlers_unchanged(self, callback):
        names = [name for name in ('SIGTERM', 'SIGHUP') if hasattr(signal, name)]
        original = {getattr(signal, name): signal.getsignal(getattr(signal, name)) for name in names}
        try:
            callback(original)
        finally:
            for number, handler in original.items():
                signal.signal(number, handler)

    def test_ignored_custom_and_nested_handlers_are_not_replaced_or_restored_over(self):
        def check(original):
            for name in ('SIGTERM', 'SIGHUP'):
                if not hasattr(signal, name):
                    continue
                number = getattr(signal, name)
                received = []
                def custom(signum, frame):
                    received.append(signum)
                for disposition in (signal.SIG_IGN, custom):
                    signal.signal(number, disposition)
                    scope = cli._AskSignalScope()
                    self.assertIs(signal.getsignal(number), disposition)
                    os.kill(os.getpid(), number)
                    self.assertEqual(received, [] if disposition is signal.SIG_IGN else [number])
                    scope.restore()
                    self.assertIs(signal.getsignal(number), disposition)
                    signal.signal(number, original[number])
            if all(handler is signal.SIG_DFL for handler in original.values()):
                outer = cli._AskSignalScope()
                installed = {number: signal.getsignal(number) for number in original}
                inner = cli._AskSignalScope()
                inner.restore()
                self.assertEqual({number: signal.getsignal(number) for number in original}, installed)
                replacement = lambda *_: None
                first = next(iter(original))
                signal.signal(first, replacement)
                outer.restore()
                self.assertIs(signal.getsignal(first), replacement)

        self.assert_signal_handlers_unchanged(check)

    def test_first_signal_wins_while_diagnostics_mutation_is_critical(self):
        from llmtool.cli import _AskSignalScope, _AskTermination
        scope = _AskSignalScope()
        try:
            scope.begin_critical()
            scope._receive(signal.SIGTERM)
            scope._receive(getattr(signal, 'SIGHUP', signal.SIGTERM))
            scope.end_critical()
            with self.assertRaises(_AskTermination) as caught:
                scope._raise_pending()
            self.assertIs(caught.exception.owner, scope)
            self.assertEqual(caught.exception.signum, signal.SIGTERM)
        finally:
            scope.restore()

    def test_term_during_held_http_request_finalizes(self):
        process = self.launch()
        try:
            if not self.received.wait(3):
                out, err = process.communicate(timeout=3)
                self.fail('synthetic HTTP request was not received: ' + repr((process.returncode, out, err)))
            process.send_signal(signal.SIGTERM)
            self.assert_finalized(process, signal.SIGTERM)
        finally:
            self.release.set()
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=3)

    def test_term_while_stdout_pipe_is_blocked_finalizes(self):
        self.large = True
        self.release.set()
        process = self.launch()
        try:
            if not self.received.wait(3):
                out, err = process.communicate(timeout=3)
                self.fail('synthetic HTTP request was not received: ' + repr((process.returncode, out, err)))
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                import select
                readable, _, _ = select.select([process.stdout], [], [], .05)
                if readable:
                    break
            try:
                process.wait(timeout=.2)
            except subprocess.TimeoutExpired:
                pass
            else:
                out, err = process.communicate(timeout=3)
                self.fail('child completed before pipe blocked: ' + repr((process.returncode, len(out), err)))
            process.send_signal(signal.SIGTERM)
            self.assert_finalized(process, signal.SIGTERM, partial_stdout=True)
        finally:
            self.release.set()
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=3)

    def test_hup_during_held_http_request_finalizes(self):
        if not hasattr(signal, 'SIGHUP'):
            self.skipTest('platform has no SIGHUP')
        process = self.launch()
        try:
            self.assertTrue(self.received.wait(3), 'synthetic HTTP request was not received')
            process.send_signal(signal.SIGHUP)
            self.assert_finalized(process, signal.SIGHUP)
        finally:
            self.release.set()
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=3)

    def test_sigint_keeps_existing_http_ask_behavior(self):
        process = self.launch()
        try:
            self.assertTrue(self.received.wait(3), 'synthetic HTTP request was not received')
            process.send_signal(signal.SIGINT)
            self.assert_finalized(process, signal.SIGINT)
        finally:
            self.release.set()
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=3)

    def test_no_save_diagnostics_does_not_create_store(self):
        self.release.set()
        result = subprocess.run([sys.executable, '-B', '-m', 'llmtool', 'ask', PROMPT,
                                 '--no-save-diagnostics'], cwd=str(Path(__file__).resolve().parents[1]),
                                env=self.env, stdin=subprocess.DEVNULL, capture_output=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (0, (RESPONSE + '\n').encode()), result.stderr)
        self.assertFalse(self.state.exists())

    def test_signal_after_http_before_output_finalizes_interrupted(self):
        from llmtool.providers.openai_compatible import OpenAICompatibleClient
        self.release.set()
        original = OpenAICompatibleClient.complete

        def complete(client, messages):
            result = original(client, messages)
            os.kill(os.getpid(), signal.SIGTERM)
            return result

        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(OpenAICompatibleClient, 'complete', complete), \
                redirect_stdout(out), redirect_stderr(err):
            status = cli.main(['ask', PROMPT, '--save-diagnostics'])
        self.assertEqual(status, 128 + signal.SIGTERM)
        self.assertEqual(out.getvalue(), '')
        self.assertTrue(self.received.is_set())
        run = next((self.state / 'runs').iterdir())
        summary = json.loads((run / 'summary.json').read_text())
        events = [json.loads(line) for line in (run / 'trace.jsonl').read_text().splitlines()]
        self.assertEqual(summary['status'], 'interrupted')
        self.assertEqual(events[-1]['data']['status'], 'interrupted')
        self.assertNotIn(PROMPT, (run / 'trace.jsonl').read_text())

    def test_signal_during_final_diagnostics_write_keeps_completed_summary(self):
        from llmtool.diagnostics_runtime import RunContext
        self.release.set()
        original = RunContext.finish

        def finish(run, status):
            original(run, status)
            if status == 'success':
                os.kill(os.getpid(), signal.SIGTERM)

        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(RunContext, 'finish', finish), \
                redirect_stdout(out), redirect_stderr(err):
            result = cli.main(['ask', PROMPT, '--save-diagnostics'])
        self.assertEqual(result, 128 + signal.SIGTERM)
        self.assertEqual(out.getvalue(), RESPONSE + '\n')
        self.assertTrue(self.received.is_set())
        run = next((self.state / 'runs').iterdir())
        summary = json.loads((run / 'summary.json').read_text())
        self.assertEqual(summary['status'], 'success')
        self.assertEqual(err.getvalue(), '')

    def test_signal_finalization_store_failure_preserves_signal_exit(self):
        self.state.mkdir(mode=0o755)
        self.state.chmod(0o755)
        process = self.launch()
        try:
            self.assertTrue(self.received.wait(3), 'synthetic HTTP request was not received')
            process.send_signal(signal.SIGTERM)
            out, err = process.communicate(timeout=4)
            self.assertEqual((process.returncode, out), (128 + signal.SIGTERM, b''), err)
            self.assertIn(b'diagnostics persistence unavailable', err)
            self.assertEqual(list(self.state.iterdir()), [])
            self.assertNotIn(PROMPT.encode(), err)
            self.assertNotIn(RESPONSE.encode(), err)
        finally:
            self.release.set()
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=3)

    def test_term_during_provider_failure_stderr_delivery_finalizes_without_drain(self):
        script = '''
import os
import fcntl
import sys
from unittest.mock import patch
from llmtool import cli
from llmtool.errors import LLMToolError

flags = fcntl.fcntl(2, fcntl.F_GETFL)
fcntl.fcntl(2, fcntl.F_SETFL, flags | os.O_NONBLOCK)
while True:
    try:
        os.write(2, b'x' * 4096)
    except BlockingIOError:
        break
fcntl.fcntl(2, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)

class Client:
    def complete(self, messages):
        os.write(1, b'READY\\n')
        raise LLMToolError('synthetic provider failure')

with patch.object(cli, 'create_client', lambda config, key: Client()):
    raise SystemExit(cli.main(['ask', 'SYNTHETIC_PRIVATE_PROMPT_181', '--save-diagnostics']))
'''
        process = subprocess.Popen([sys.executable, '-B', '-c', script],
                                   cwd=str(Path(__file__).resolve().parents[1]), env=self.env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        try:
            import select
            readable, _, _ = select.select([process.stdout], [], [], 3)
            self.assertTrue(readable, 'provider failure did not reach synthetic readiness marker')
            self.assertEqual(process.stdout.readline(), b'READY\n')
            time.sleep(.2)
            process.send_signal(signal.SIGTERM)
            hung = False
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                hung = True
            out, err = process.communicate(timeout=3)
            if hung:
                runs = list((self.state / 'runs').iterdir())
                if runs:
                    files = sorted(path.name for path in runs[0].iterdir())
                    trace = runs[0] / 'trace.partial.jsonl'
                    if not trace.exists():
                        trace = runs[0] / 'trace.jsonl'
                    types = ([json.loads(line)['type'] for line in trace.read_text().splitlines()]
                             if trace.exists() else [])
                else:
                    files, types = [], []
                self.fail('TERM left stderr blocked until drained: exit=%r files=%r events=%r' %
                          (process.returncode, files, types))
            self.assertEqual(process.returncode, 128 + signal.SIGTERM, err[-500:])
            self.assertEqual(out, b'')
            runs = list((self.state / 'runs').iterdir())
            self.assertEqual(len(runs), 1)
            run = runs[0]
            summary = json.loads((run / 'summary.json').read_text())
            events = [json.loads(line) for line in (run / 'trace.jsonl').read_text().splitlines()]
            self.assertEqual(summary['status'], 'interrupted')
            self.assertEqual(events[-1]['type'], 'run.end')
            self.assertEqual(events[-1]['data']['status'], 'interrupted')
            self.assertFalse((run / '.reservation').exists())
            self.assertTrue((run / '.run.lock').exists())
            self.assertFalse((run / 'trace.partial.jsonl').exists())
            self.assertNotIn(PROMPT.encode(), (run / 'trace.jsonl').read_bytes())
            self.release.set()
            following = self.reader('ask', PROMPT, '--save-diagnostics')
            self.assertEqual((following.returncode, following.stdout),
                             (0, (RESPONSE + '\n').encode()), following.stderr)
        finally:
            self.release.set()
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=3)


if __name__ == '__main__':
    unittest.main()
