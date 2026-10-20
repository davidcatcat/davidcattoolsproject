"""Targeted public new-user and manual recovery journeys, localhost only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

ROOT = Path(__file__).resolve().parents[3]
MARKER = b'QG_QA1_JOURNEY_OK\n'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        self.server.calls += 1
        self.server.received.set()
        if self.server.hold:
            self.server.release.wait(30)
        payload = ({'error': 'PRIVATE_HTTP_BODY PRIVATE_SECRET reasoning'} if self.server.fail else
                   {'choices': [{'message': {'role': 'assistant', 'content': MARKER.decode().strip()},
                                 'finish_reason': 'stop'}], 'model': 'test-model'})
        raw = json.dumps(payload).encode()
        self.send_response(500 if self.server.fail else 200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass


class TargetedJourneyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.state = self.root / 'state'
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.calls, self.server.fail, self.server.hold = 0, False, False
        self.server.received, self.server.release = threading.Event(), threading.Event()
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        def cleanup():
            self.server.release.set()
            self.server.shutdown()
            self.server.server_close()
            thread.join(timeout=5)
        self.addCleanup(cleanup)
        self.env = {'HOME': str(self.home), 'PATH': '/usr/bin:/bin',
            'LLMTOOL_HOME': str(self.state), 'PYTHONDONTWRITEBYTECODE': '1',
            'PYTHONPATH': os.pathsep.join((str(ROOT / 'tools/llmtool'),
                str(Path(yaml.__file__).resolve().parent.parent)))}
        self.ask = ['ask', 'PRIVATE_SYNTHETIC_PROMPT', '--base-url',
                    'http://127.0.0.1:' + str(self.server.server_port), '--model', 'test-model']

    def invoke(self, *args):
        return subprocess.run([sys.executable, '-m', 'llmtool', *args], cwd=ROOT, env=self.env,
            stdin=subprocess.DEVNULL, capture_output=True, timeout=10)

    def test_new_user_ask_and_diagnostics_default_off_then_explicit_save(self):
        self.assertEqual(self.invoke('config', 'path').returncode, 0)
        self.assertEqual(self.invoke('ask', 'synthetic').returncode, 1)
        self.assertEqual(self.server.calls, 0)
        self.assertEqual(self.invoke('config', 'init').returncode, 0)
        config = self.home / '.config/llmtool/config.yaml'
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.invoke('providers').returncode, 0)
        for flags in ((), ('--verbose',), ('--debug',)):
            result = self.invoke(*self.ask, *flags)
            self.assertEqual((result.returncode, result.stdout), (0, MARKER), result.stderr)
            self.assertFalse(self.state.exists())
            if flags:
                self.assertIn(b'diagnostics_persisted=false', result.stderr)
        self.assertEqual(self.server.calls, 3)
        saved = self.invoke(*self.ask, '--save-diagnostics', '--debug')
        self.assertEqual((saved.returncode, saved.stdout), (0, MARKER), saved.stderr)
        self.assertIn(b'diagnostics_persisted=true', saved.stderr)
        self.assertEqual(self.server.calls, 4)
        self.assertEqual(self.invoke('doctor').returncode, 0)
        run = next((self.state / 'runs').iterdir())
        self.assertEqual(self.invoke('trace', run.name).returncode, 0)
        for path in run.iterdir():
            if path.suffix in ('.json', '.jsonl'):
                raw = path.read_bytes()
                for marker in (MARKER.strip(), b'PRIVATE_SYNTHETIC_PROMPT', b'PRIVATE_SECRET', b'reasoning'):
                    self.assertNotIn(marker, raw)

    def test_failure_diagnostics_manual_retry_is_two_explicit_invocations(self):
        self.server.fail = True
        failed = self.invoke(*self.ask, '--save-diagnostics')
        self.assertEqual((failed.returncode, failed.stdout), (1, b''))
        self.assertIn(b'trace=saved', failed.stderr)
        self.assertNotIn(b'PRIVATE_HTTP_BODY', failed.stderr)
        self.assertNotIn(b'PRIVATE_SECRET', failed.stderr)
        self.assertEqual(self.server.calls, 1)
        first = next((self.state / 'runs').iterdir())
        trace = self.invoke('trace', first.name, '--errors')
        self.assertEqual(trace.returncode, 0)
        self.assertIn(b'error', trace.stdout)
        self.server.fail = False  # User manual retry, never internal recovery.
        recovered = self.invoke(*self.ask, '--save-diagnostics')
        self.assertEqual((recovered.returncode, recovered.stdout), (0, MARKER), recovered.stderr)
        self.assertEqual(self.server.calls, 2)
        rows = self.invoke('runs')
        self.assertEqual(rows.returncode, 0)
        self.assertIn(b'recorded_status=error', rows.stdout)
        self.assertIn(b'recorded_status=success', rows.stdout)

    def test_eight_public_writers_hold_three_slots_then_sequential_recovery(self):
        config = self.home / '.config/llmtool/config.yaml'
        config.parent.mkdir(parents=True)
        config.write_text(yaml.safe_dump({'diagnostics': {'persist_metadata': True,
            'max_runs': 3, 'max_total_bytes': 1048576, 'max_run_bytes': 65536}}))
        self.server.hold = True
        processes = []
        try:
            for _ in range(8):
                processes.append(subprocess.Popen([sys.executable, '-m', 'llmtool',
                    *self.ask, '--verbose'], cwd=ROOT, env=self.env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE))
            deadline = time.monotonic() + 20
            while self.server.calls != 8:
                self.assertLess(time.monotonic(), deadline)
                self.assertTrue(all(process.poll() is None for process in processes))
                time.sleep(0.01)  # All eight requests must reach the held server.
            doctor = self.invoke('doctor')
            self.assertEqual(doctor.returncode, 0, doctor.stderr)
            self.assertIn(b'run_count=3 active_count=3', doctor.stdout)
            self.server.release.set()
            persisted = 0
            for process in processes:
                out, err = process.communicate(timeout=10)
                self.assertEqual((process.returncode, out), (0, MARKER), err)
                if b'diagnostics_persisted=true' in err:
                    persisted += 1
                else:
                    self.assertIn(b'code=DIAGNOSTICS_STORE_FULL', err)
            self.assertEqual(persisted, 3)
            self.assertEqual(self.server.calls, 8)
            self.server.hold = False
            for _ in range(3):
                result = self.invoke(*self.ask, '--verbose')
                self.assertEqual((result.returncode, result.stdout), (0, MARKER), result.stderr)
                self.assertIn(b'diagnostics_persisted=true', result.stderr)
            self.assertEqual(self.server.calls, 11)
            runs = list((self.state / 'runs').iterdir())
            self.assertEqual(len(runs), 3)
            self.assertTrue(all((run / 'summary.json').is_file() for run in runs))
        finally:
            self.server.release.set()
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)

    @unittest.skipUnless(os.name == 'posix', 'requires POSIX default signals')
    def test_http_term_returns_signal_status_and_finalizes_interrupted_record(self):
        import signal
        self.server.hold = True
        process = subprocess.Popen([sys.executable, '-m', 'llmtool', *self.ask, '--save-diagnostics'],
            cwd=ROOT, env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertTrue(self.server.received.wait(5))
            process.send_signal(signal.SIGTERM)
            out, err = process.communicate(timeout=5)
            self.assertEqual((process.returncode, out, err), (128 + signal.SIGTERM, b'', b''))
            run = next((self.state / 'runs').iterdir())
            summary = json.loads((run / 'summary.json').read_text())
            events = [json.loads(line) for line in (run / 'trace.jsonl').read_text().splitlines()]
            self.assertEqual(summary['status'], 'interrupted')
            self.assertEqual(events[-1]['type'], 'run.end')
            self.assertEqual(events[-1]['data']['status'], 'interrupted')
            self.assertFalse((run / '.reservation').exists())
            self.assertFalse((run / 'trace.partial.jsonl').exists())
            self.assertEqual(self.server.calls, 1)
        finally:
            self.server.release.set()
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
