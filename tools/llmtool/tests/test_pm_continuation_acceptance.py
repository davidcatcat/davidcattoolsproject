"""PM continuation acceptance: availability layers and blocked-task isolation.

These tests drive the real ``task submit`` path, TaskStore and the
openai-compatible provider over loopback HTTP. Only the profile resolver is
replaced so the profile points at a local server; no external endpoint is used.
"""
import copy
import io
import json
import os
import socket
import tempfile
import threading
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from llmtool.config import DEFAULTS
from llmtool.task_store import TaskStore
from llmtool.task_submission_cli import run_task_submit

PRIVATE_BODY = 'PRIVATE_GATEWAY_BODY_MARKER'


class Gateway(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        self.server.calls += 1
        status, raw = self.server.reply
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def _ok_body():
    return json.dumps({'model': 'gw-test', 'choices': [{
        'message': {'role': 'assistant', 'content': 'PM accepted the bounded request.'},
        'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': 5, 'completion_tokens': 4, 'total_tokens': 9}}).encode()


class PMContinuationAcceptanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.store = TaskStore(self.base / 'tasks')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Gateway)
        self.server.calls, self.server.reply = 0, (200, _ok_body())
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()

        def stop():
            self.server.shutdown()
            self.server.server_close()
            thread.join(timeout=5)
        self.addCleanup(stop)

    def _config(self, port):
        value = copy.deepcopy(DEFAULTS)
        value['providers']['default'].update(
            base_url='http://127.0.0.1:%d' % port, model='gw-test',
            api_key='fake-test-key', timeout_seconds=5)
        return value

    def _submit(self, port, words):
        out = io.StringIO()
        with patch.dict(os.environ, {'HOME': str(self.base)}), \
                patch('llmtool.task_submission_cli.resolve_profile_config',
                      return_value=self._config(port)), \
                redirect_stdout(out):
            code = run_task_submit(Namespace(provider='company-gw', request=words), store=self.store)
        return code, json.loads(out.getvalue())

    def _closed_port(self):
        probe = socket.socket()
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
        probe.close()
        return port

    def test_availability_layers_are_classified_separately(self):
        port = self.server.server_port
        # Layer 1 fails: no listener, so no API or inference evidence exists.
        code, unreachable = self._submit(self._closed_port(), ['Plan', 'a', 'bounded', 'fix'])
        self.assertEqual((code, unreachable['state']), (1, 'PM_UNAVAILABLE'))
        self.assertEqual(unreachable['error_code'], 'NETWORK_PROVIDER_UNREACHABLE')
        self.assertEqual(self.server.calls, 0)
        # Layer 1 passes, layer 2 fails: TCP and HTTP reached the gateway, auth rejected.
        self.server.reply = (401, PRIVATE_BODY.encode())
        code, rejected = self._submit(port, ['Plan', 'a', 'bounded', 'fix'])
        self.assertEqual((code, rejected['state']), (1, 'PM_UNAVAILABLE'))
        self.assertEqual(rejected['error_code'], 'AUTH_PROVIDER_REJECTED')
        self.assertEqual(self.server.calls, 1)
        # Layer 2 passes, layer 3 fails: an accepted call without a valid inference result.
        self.server.reply = (200, b'not-json')
        code, invalid = self._submit(port, ['Plan', 'a', 'bounded', 'fix'])
        self.assertEqual((code, invalid['state']), (1, 'PM_UNAVAILABLE'))
        self.assertEqual(invalid['error_code'], 'MODEL_RESPONSE_INVALID')
        self.assertEqual(self.server.calls, 2)
        # Positive control: inference succeeds. Task capability (layer 4) is still
        # unproven here; it needs task-specific acceptance evidence.
        self.server.reply = (200, _ok_body())
        code, responded = self._submit(port, ['Plan', 'a', 'bounded', 'fix'])
        self.assertEqual((code, responded['state']), (0, 'PM_RESPONDED'))
        self.assertEqual(self.server.calls, 3)
        for record in (unreachable, rejected, invalid, responded):
            stored = json.dumps(self.store.get(record['task_id']))
            self.assertNotIn(PRIVATE_BODY, stored)
            self.assertNotIn('fake-test-key', stored)

    def test_blocked_task_does_not_block_an_independent_task(self):
        port = self.server.server_port
        self.server.reply = (401, PRIVATE_BODY.encode())
        _, blocked = self._submit(port, ['Task', 'A'])
        before = self.store.get(blocked['task_id'])
        self.server.reply = (200, _ok_body())
        code, independent = self._submit(port, ['Task', 'B'])
        self.assertEqual((code, independent['state']), (0, 'PM_RESPONDED'))
        self.assertNotEqual(blocked['task_id'], independent['task_id'])
        after = self.store.get(blocked['task_id'])
        self.assertEqual(after, before)
        self.assertEqual(after['state'], 'PM_UNAVAILABLE')
        self.assertEqual(after['error_code'], 'AUTH_PROVIDER_REJECTED')
        self.assertEqual(self.server.calls, 2)


if __name__ == '__main__':
    unittest.main()
