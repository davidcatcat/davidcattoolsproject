"""Actual typed provider failure sites through ask and secure persisted trace.

No live provider is called: HTTP transports are injected; CLI processes execute
small generated fixture scripts. Store faults remain secondary to the task.
"""

import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from llmtool import cli, diagnostics as d, diagnostics_store as store
from llmtool.diagnostics_reader import DiagnosticsReader
from llmtool.diagnostics_settings import DiagnosticsSettings
from llmtool.diagnostics_store import DiagnosticsStore, StoredRun, persistence_supported
from llmtool.diagnostics_summary import RunSummary
from llmtool.providers import client_type

PROMPT = 'PRIVATE_A11A6_PROMPT'
RAW = 'PRIVATE_A11A6_HTTP_STDERR_REASONING_BODY'
SECRET = 'sk-' + 'b' * 40
RUN_ID = 'c' * 32
CLI_NON_TIMEOUT_SECONDS = 30


class Response(io.BytesIO):
    status = 200


@unittest.skipUnless(persistence_supported(), 'requires POSIX diagnostics backend')
class FailureMatrixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.user = self.base / 'user'
        self.project = self.base / 'project'
        self.project.mkdir()
        self.path = self.user / '.config/llmtool/config.yaml'
        self.path.parent.mkdir(parents=True)
        self.serial = 0

    def configure(self, kind, *, timeout_seconds=0.2):
        fields = '    type: ' + kind + '\n    model: test-model\n    timeout_seconds: ' + str(timeout_seconds) + '\n'
        if kind == 'openai-compatible':
            fields += '    base_url: http://example.invalid\n'
        if kind in ('openai', 'openai-compatible'):
            fields += '    api_key: ' + SECRET + '\n'
        self.path.write_text('providers:\n  default:\n' + fields)

    def invoke(self, kind, code, *, mode='debug', store_fault=None, cause=None, setup=None, timeout_seconds=0.2):
        self.configure(kind, timeout_seconds=timeout_seconds)
        self.serial += 1
        state = self.base / ('state-' + str(self.serial))
        out, err = io.StringIO(), io.StringIO()
        implementation = client_type(kind)
        original_complete, original_append = implementation.complete, StoredRun.append
        def append(writer, event):
            if event.type is d.EventType.ERROR:
                raise OSError(RAW + SECRET)
            return original_append(writer, event)
        with ExitStack() as stack:
            stack.enter_context(patch('llmtool.config.Path.home', return_value=self.user))
            stack.enter_context(patch('llmtool.config.Path.cwd', return_value=self.project))
            stack.enter_context(patch.dict(os.environ, {'LLMTOOL_HOME': str(state)}, clear=False))
            stack.enter_context(patch('llmtool.cli.sys.stdin', None))
            stack.enter_context(redirect_stdout(out))
            stack.enter_context(redirect_stderr(err))
            calls = stack.enter_context(patch.object(implementation, 'complete', autospec=True,
                                                     side_effect=original_complete))
            if store_fault == 'start':
                stack.enter_context(patch.object(DiagnosticsStore, '_capacity', side_effect=PermissionError(RAW + SECRET)))
            elif store_fault == 'append':
                stack.enter_context(patch.object(StoredRun, 'append', new=append))
            elif store_fault == 'finish':
                stack.enter_context(patch('llmtool.diagnostics_store._publish', side_effect=OSError(28, RAW + SECRET)))
            if setup is not None:
                setup(stack)
            status = cli.main(['ask', PROMPT, '--save-diagnostics', *(['--' + mode] if mode else [])])
        self.assertEqual((status, out.getvalue()), (1, ''))
        calls.assert_called_once()
        lines = err.getvalue().splitlines()
        self.assertTrue(lines[0].startswith('llmtool: '))
        self.assertIn('llmtool: code=' + code + ' run_id=', lines[1])
        saved = store_fault is None
        self.assertTrue(lines[1].endswith('trace=saved' if saved else 'trace=unavailable'))
        warnings = [line for line in lines if line.startswith('llmtool: code=DIAGNOSTICS_')]
        if saved:
            self.assertEqual(warnings, [])
            self.assertEqual(len(list((state / 'runs').iterdir())), 1)
            directory = next((state / 'runs').iterdir())
            trace = DiagnosticsReader(home=state).trace(directory.name)
            self.assertEqual(trace['state'], 'complete')
            self.assertEqual(trace['summary']['final_error_code'], code)
            self.assertEqual(trace['summary']['status'], 'incomplete' if code == 'MODEL_OUTPUT_INCOMPLETE' else 'error')
            self.assertEqual([e['type'] for e in trace['events']], ['run.start', 'provider.request', 'error', 'run.end'])
            failure = trace['events'][2]
            self.assertEqual(failure['component'], 'provider.' + kind)
            self.assertFalse(failure['data']['fallback_allowed'])
            self.assertEqual(failure['data'].get('safe_cause', {}), {} if cause is None else cause)
        else:
            self.assertEqual(len(warnings), 1)
            self.assertEqual(lines[2], warnings[0])
            self.assertLess(len(warnings[0].encode()), 256)
            if (state / 'runs').exists():
                for directory in (state / 'runs').iterdir():
                    self.assertEqual(DiagnosticsReader(home=state).trace(directory.name)['state'], 'incomplete')
        persisted = b''.join(path.read_bytes() for path in state.rglob('*') if path.is_file()) if state.exists() else b''
        for marker in (PROMPT, RAW, SECRET, str(self.base), 'Authorization', 'Traceback'):
            self.assertNotIn(marker, err.getvalue())
            self.assertNotIn(marker.encode(), persisted)
        self.assertNotIn('type=retry', err.getvalue())
        self.assertNotIn('type=fallback', err.getvalue())
        self.assertNotIn(b'"type":"retry"', persisted)
        self.assertNotIn(b'"type":"fallback"', persisted)
        return err.getvalue()

    def test_http_status_failures_persisted_and_secondary_store_faults(self):
        for kind in ('openai', 'openai-compatible'):
            for status, code in ((401, 'AUTH_PROVIDER_REJECTED'), (403, 'AUTH_PROVIDER_REJECTED'),
                                 (429, 'PROVIDER_RATE_LIMITED'), (500, 'PROVIDER_HTTP_ERROR'), (503, 'PROVIDER_HTTP_ERROR')):
                for fault in (None, 'start', 'append', 'finish'):
                    def setup(stack):
                        opener = Mock()
                        opener.open.side_effect = HTTPError('https://private.invalid', status, RAW, {}, io.BytesIO((RAW + SECRET).encode()))
                        stack.enter_context(patch('urllib.request.build_opener', return_value=opener))
                        self.addCleanup(opener.open.assert_called_once)
                    with self.subTest(kind=kind, status=status, fault=fault):
                        self.invoke(kind, code, cause={'http_status': status}, store_fault=fault, setup=setup)

    def test_http_client_and_gateway_statuses_redact_retry_after_without_retry(self):
        # A11b.6 row 2: statuses outside the 401/403/429/500/503 matrix above.
        retry_after = 'PRIVATE_A11B6_RETRY_AFTER_' + SECRET
        injected = 'X-A11B6-Injected'
        for kind in ('openai', 'openai-compatible'):
            prefix = 'OpenAI API HTTP ' if kind == 'openai' else 'gateway HTTP '
            for status in (400, 404, 408, 502, 504):
                for mode in ('', 'verbose', 'debug'):
                    body = io.BytesIO((RAW + SECRET + retry_after).encode())
                    def setup(stack, status=status, body=body):
                        headers = {'Retry-After': retry_after, 'Set-Cookie': SECRET,
                                   injected: RAW}
                        opener = Mock()
                        opener.open.side_effect = HTTPError(
                            'https://private.invalid/v1?key=' + SECRET, status,
                            RAW + SECRET + '\r\n' + injected + ': ' + retry_after, headers, body)
                        stack.enter_context(patch('urllib.request.build_opener', return_value=opener))
                        self.addCleanup(opener.open.assert_called_once)
                    with self.subTest(kind=kind, status=status, mode=mode):
                        stderr = self.invoke(kind, 'PROVIDER_HTTP_ERROR', mode=mode,
                                             cause={'http_status': status}, setup=setup)
                        # Reached the provider's HTTPError handler, which closes the body.
                        self.assertEqual(stderr.splitlines()[0], 'llmtool: ' + prefix + str(status) +
                                         '; response body omitted')
                        self.assertTrue(body.closed)
                        persisted = b''.join(path.read_bytes() for path in self.base.glob('state-*/**/*')
                                             if path.is_file())
                        self.assertTrue(persisted)
                        for marker in (retry_after, 'Retry-After', 'Set-Cookie', injected,
                                       'private.invalid', 'key='):
                            self.assertNotIn(marker, stderr)
                            self.assertNotIn(marker.encode(), persisted)

    def test_http_transport_failures_all_output_modes_without_retry(self):
        for kind in ('openai', 'openai-compatible'):
            for failure, code in ((socket.timeout(RAW), 'PROVIDER_TIMEOUT'),
                                  (URLError(socket.timeout(RAW)), 'PROVIDER_TIMEOUT'),
                                  (URLError(RAW + SECRET), 'NETWORK_PROVIDER_UNREACHABLE'),
                                  (OSError(RAW + SECRET), 'PROVIDER_RUNTIME_ERROR')):
                for mode in ('', 'verbose', 'debug'):
                    for fault in (None, 'start', 'append', 'finish'):
                        def setup(stack):
                            opener = Mock()
                            opener.open.side_effect = failure
                            stack.enter_context(patch('urllib.request.build_opener', return_value=opener))
                            self.addCleanup(opener.open.assert_called_once)
                        with self.subTest(kind=kind, code=code, mode=mode, fault=fault):
                            self.invoke(kind, code, mode=mode, store_fault=fault, setup=setup)

    def test_http_model_failures_persisted_and_store_fault_crossproducts(self):
        for kind in ('openai', 'openai-compatible'):
            empty = ({'status': 'completed', 'output': []} if kind == 'openai' else
                     {'choices': [{'finish_reason': 'stop', 'message': {'content': ''}}]})
            tool = ({'status': 'completed', 'output': [{'type': 'function_call', 'arguments': RAW}]} if kind == 'openai' else
                    {'choices': [{'finish_reason': 'tool_calls', 'message': {'tool_calls': [{'arguments': RAW}]}}]})
            incomplete = ({'status': 'incomplete', 'output': []} if kind == 'openai' else
                          {'choices': [{'finish_reason': 'length', 'message': {'content': RAW}}]})
            cases = ((b'{'+RAW.encode(), 'MODEL_RESPONSE_INVALID'),
                     (json.dumps(empty).encode(), 'MODEL_RESPONSE_INVALID'),
                     (json.dumps(tool).encode(), 'MODEL_RESPONSE_INVALID'),
                     (json.dumps(incomplete).encode(), 'MODEL_OUTPUT_INCOMPLETE'),
                     (b'x' * 257, 'MODEL_RESPONSE_TOO_LARGE'))
            for raw, code in cases:
                for fault in (None, 'start', 'append', 'finish'):
                    def setup(stack):
                        opener = Mock()
                        opener.open.return_value = Response(raw)
                        stack.enter_context(patch('urllib.request.build_opener', return_value=opener))
                        module = 'openai' if kind == 'openai' else 'openai_compatible'
                        stack.enter_context(patch('llmtool.providers.' + module + '.RESPONSE_LIMIT', 256))
                        self.addCleanup(opener.open.assert_called_once)
                    with self.subTest(kind=kind, code=code, fault=fault):
                        self.invoke(kind, code, store_fault=fault, setup=setup)

    def test_cli_real_fixture_failures_persisted_and_store_fault_crossproducts(self):
        executable = self.base / 'fake-provider'
        for kind in ('claude-cli', 'codex-cli'):
            module = 'llmtool.providers.' + kind.replace('-', '_')
            for failure, code in (('timeout', 'PROVIDER_TIMEOUT'), ('failure', 'PROVIDER_RUNTIME_ERROR'),
                                  ('large', 'MODEL_RESPONSE_TOO_LARGE'), ('large-stderr', 'MODEL_RESPONSE_TOO_LARGE'),
                                  ('empty', 'MODEL_RESPONSE_INVALID'), ('invalid', 'MODEL_RESPONSE_INVALID'),
                                  ('missing', 'ENV_PROVIDER_RUNTIME_MISSING')):
                executable.write_text('#!' + sys.executable + '\nimport sys,time\nsys.stdin.read()\nmode=' + repr(failure) + '\n'
                    'if mode == "timeout": time.sleep(5)\n'
                    'elif mode == "failure":\n sys.stderr.write(' + repr(RAW + SECRET) + '); sys.exit(7)\n'
                    'elif mode == "large": sys.stdout.write("x" * 4096)\n'
                    'elif mode == "large-stderr": sys.stderr.write("x" * 4096)\n'
                    'elif mode == "invalid": sys.stdout.buffer.write(b"\\xff")\n')
                executable.chmod(0o700)
                for fault in (None, 'start', 'append', 'finish'):
                    spawns = []
                    def setup(stack):
                        stack.enter_context(patch(module + '.shutil.which', return_value=None if failure == 'missing' else str(executable)))
                        stack.enter_context(patch(module + '.OUTPUT_LIMIT', 256))
                        spawns.append(stack.enter_context(patch(module + '.subprocess.Popen', wraps=subprocess.Popen)))
                    with self.subTest(kind=kind, failure=failure, fault=fault):
                        self.invoke(kind, code, store_fault=fault,
                                    cause={'process_status': 7} if failure == 'failure' else None, setup=setup,
                                    timeout_seconds=0.2 if failure == 'timeout' else CLI_NON_TIMEOUT_SECONDS)
                    self.assertEqual(spawns[0].call_count, 0 if failure == 'missing' else 1)

    def test_cli_spawn_permission_error_redacts_sensitive_data(self):
        # A11b.6: the CLI exists but cannot be started (EACCES). The OS error
        # text carries a fake password/token; no real provider is launched.
        executable = self.base / 'fake-provider'
        executable.write_text('#!/bin/sh\nexit 99\n')
        executable.chmod(0o700)
        password = 'PRIVATE_A11B6_PASSWORD_hunter2'
        detail = 'PRIVATE_A11B6_SPAWN_DETAIL'
        for kind in ('claude-cli', 'codex-cli'):
            module = 'llmtool.providers.' + kind.replace('-', '_')
            for mode in ('', 'verbose', 'debug'):
                spawns = []
                def setup(stack):
                    stack.enter_context(patch(module + '.shutil.which', return_value=str(executable)))
                    spawns.append(stack.enter_context(patch(module + '.subprocess.Popen', side_effect=PermissionError(
                        13, detail + ' password=' + password + ' token=' + SECRET, str(executable)))))
                with self.subTest(kind=kind, mode=mode or 'normal'):
                    serial = self.serial + 1
                    stderr = self.invoke(kind, 'PROVIDER_RUNTIME_ERROR', mode=mode, setup=setup,
                                         timeout_seconds=CLI_NON_TIMEOUT_SECONDS)
                    self.assertEqual(spawns[0].call_count, 1)
                    state = self.base / ('state-' + str(serial))
                    persisted = b''.join(path.read_bytes() for path in state.rglob('*') if path.is_file())
                    for marker in (password, detail, SECRET, str(executable), 'PermissionError', 'Errno'):
                        self.assertNotIn(marker, stderr)
                        self.assertNotIn(marker.encode(), persisted)

    def test_concurrent_same_run_writes_serialize_and_invalid_sequence_fails_closed(self):
        for invalid in (False, True):
            state = self.base / ('concurrent-' + str(invalid))
            writer = DiagnosticsStore(DiagnosticsSettings(persist_metadata=True), home=state).start(RUN_ID)
            self.assertIsNotNone(writer)
            self.addCleanup(writer.close)
            first = d.DiagnosticEvent(run_id=RUN_ID, event_id=1, timestamp=d.utc_timestamp(),
                type=d.EventType.RUN_START, component='cli.ask', severity=d.DiagnosticSeverity.INFO,
                data={'command': 'ask', 'mode': 'normal'})
            second = d.DiagnosticEvent(run_id=RUN_ID, event_id=3 if invalid else 2, timestamp=d.utc_timestamp(),
                type=d.EventType.RUN_END, component='cli.ask', severity=d.DiagnosticSeverity.INFO,
                data={'status': 'success', 'duration_ms': 1})
            writing, release, attempting = threading.Event(), threading.Event(), threading.Event()
            original_write = store._write
            results = []
            def blocked_write(fd, raw):
                if b'"type":"run.start"' in raw:
                    writing.set()
                    if not release.wait(5):
                        raise AssertionError('concurrent test timed out')
                original_write(fd, raw)
            def append_second():
                attempting.set()
                results.append(('second', writer.append(second)))
            with patch.object(store, '_write', side_effect=blocked_write):
                one = threading.Thread(target=lambda: results.append(('first', writer.append(first))))
                two = threading.Thread(target=append_second)
                one.start()
                self.assertTrue(writing.wait(5))
                two.start()
                self.assertTrue(attempting.wait(5))
                release.set()
                one.join(5)
                two.join(5)
                self.assertFalse(one.is_alive() or two.is_alive())
            self.assertEqual(dict(results), {'first': True, 'second': not invalid})
            self.assertEqual([event.event_id for event in writer.events], [1] if invalid else [1, 2])
            if invalid:
                self.assertTrue(writer.closed)
                self.assertFalse((state / 'runs' / RUN_ID / 'summary.json').exists())
                self.assertEqual(DiagnosticsReader(home=state).trace(RUN_ID)['state'], 'incomplete')
            else:
                self.assertTrue(writer.finish(RunSummary(run_id=RUN_ID, mode=d.DiagnosticMode.NORMAL,
                    status='success', started_at=first.timestamp, ended_at=second.timestamp,
                    duration_ms=1, event_count=2, dropped_event_count=0, trace_bytes=writer.trace_bytes)))
                self.assertEqual(DiagnosticsReader(home=state).trace(RUN_ID)['state'], 'complete')

    def test_cloud_key_screen_detects_cjk_neighbors_and_allows_longer_ascii_identifier(self):
        cloud_key = 'AKIA' + '0' * 16
        with self.assertRaises(ValueError):
            d.safe_diagnostic_text('北' + cloud_key + '京')
        identifier = 'prefix_' + cloud_key + '_suffix'
        self.assertEqual(d.safe_diagnostic_text(identifier), identifier)


if __name__ == '__main__':
    unittest.main()
