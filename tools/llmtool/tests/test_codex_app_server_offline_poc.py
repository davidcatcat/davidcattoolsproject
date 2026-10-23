"""Socket-level offline proof for a bounded loopback Responses intermediary.

Both HTTP servers bind only to 127.0.0.1 on ephemeral ports. This demonstrates
the mediator behavior a future App-server route would need; the retained R4
adapter is not connected to this mediator.
"""

import http.client
import json
import os
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from llmtool.run_budget import (
    BudgetCode,
    BudgetDimension,
    CallPhase,
    CallUsage,
    RunBudget,
    UsageLedger,
    assess_budget,
    admit_next,
)


SYNTHETIC_AUTH = 'Bearer synthetic-offline-only'
MAX_REQUEST_BYTES = 64 * 1024
UPSTREAM_TIMEOUT_SECONDS = 0.15


def _reply(handler, status, value, headers=None):
    raw = json.dumps(value, separators=(',', ':')).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Content-Length', str(len(raw)))
    handler.send_header('Connection', 'close')
    for name, val in (headers or {}).items():
        handler.send_header(name, val)
    handler.end_headers()
    try:
        handler.wfile.write(raw)
    except OSError:
        pass
    handler.close_connection = True


class _Server(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = False

    def record_usage(self, phase, input_tokens, output_tokens):
        self.ledger = UsageLedger(
            self.ledger.calls + (CallUsage(phase, input_tokens, output_tokens),),
            self.ledger.repair_attempts,
            self.ledger.elapsed_ms,
        )

    def record_unknown(self, phase):
        self.ledger = UsageLedger(
            self.ledger.calls + (CallUsage(phase, None, None),),
            self.ledger.repair_attempts,
            self.ledger.elapsed_ms,
        )

    def record_tool_rejection(self, tools_present, tool_count):
        # Keep only a sanitized presence/count signal, never tool descriptors.
        with self.admission_lock:
            self.tool_rejections.append({
                'tools_present': bool(tools_present),
                'tool_count': tool_count,
            })


class _ProviderHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        raw_body = self.rfile.read(length)
        with self.server.capture_lock:
            self.server.captured.append({
                'method': 'POST',
                'path': self.path,
                'authorization': self.headers.get('Authorization'),
                'content_type': self.headers.get('Content-Type'),
                'body': raw_body,
            })

        if self.server.behavior == 'disconnect':
            self.close_connection = True
            try:
                self.connection.shutdown(2)
            except OSError:
                pass
            self.connection.close()
            return
        if self.server.behavior == 'timeout':
            time.sleep(UPSTREAM_TIMEOUT_SECONDS * 3)
        if self.server.behavior == 'redirect':
            _reply(self, 302, {'error': 'redirect-test'}, {
                'Location': self.server.redirect_location,
            })
            return

        response = {
            'id': 'resp-local-only',
            'usage': {'input_tokens': 3, 'output_tokens': 2},
            'received_model': json.loads(raw_body.decode('utf-8')).get('model'),
        }
        _reply(self, 200, response)

    def do_GET(self):
        _reply(self, 404, {'error': 'METHOD_NOT_ALLOWED'})

    def log_message(self, *_):
        pass


class _ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def do_POST(self):
        server = self.server
        if self.path != '/v1/responses':
            _reply(self, 404, {'error': 'ENDPOINT_NOT_ALLOWED'})
            return
        if self.headers.get('Upgrade') or 'upgrade' in self.headers.get('Connection', '').lower():
            _reply(self, 426, {'error': 'UPGRADE_NOT_ALLOWED'})
            return
        if self.headers.get('Authorization') != SYNTHETIC_AUTH:
            _reply(self, 401, {'error': 'SYNTHETIC_AUTH_REQUIRED'})
            return

        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            _reply(self, 400, {'error': 'REQUEST_INVALID'})
            return
        if length <= 0 or length > MAX_REQUEST_BYTES:
            _reply(self, 400, {'error': 'REQUEST_INVALID'})
            return
        raw_body = self.rfile.read(length)
        request_id = self.headers.get('X-Request-ID')
        if not request_id or len(request_id) > 128:
            _reply(self, 400, {'error': 'REQUEST_ID_INVALID'})
            return
        try:
            body = json.loads(raw_body.decode('utf-8'))
        except (UnicodeError, ValueError):
            _reply(self, 400, {'error': 'REQUEST_INVALID'})
            return
        if type(body) is not dict or body.get('stream') is not True:
            _reply(self, 400, {'error': 'REQUEST_INVALID'})
            return
        tools = body.get('tools', [])
        if type(tools) is not list:
            _reply(self, 400, {'error': 'TOOLS_INVALID'})
            return
        if tools:
            server.record_tool_rejection(True, len(tools))
            _reply(self, 403, {'error': 'TOOLS_NOT_ALLOWED'})
            return
        requested_cap = body.get('max_output_tokens')
        if requested_cap is not None and (type(requested_cap) is not int or requested_cap <= 0):
            _reply(self, 400, {'error': 'OUTPUT_CAP_INVALID'})
            return

        with server.admission_lock:
            retries = server.retries_by_request.get(request_id, 0)
            if retries > 1:
                _reply(self, 429, {'error': 'REQUEST_RETRY_LIMIT'})
                return
            phase = CallPhase.PRIMARY if retries == 0 else CallPhase.RETRY
            decision = admit_next(server.budget, server.ledger, phase)
            if decision is not BudgetCode.WITHIN_BUDGET:
                _reply(self, 429, {'error': decision.value})
                return
            output_dimension = assess_budget(server.budget, server.ledger).dimension(
                BudgetDimension.OUTPUT_TOKENS)
            remaining_output = server.budget.max_output_tokens - output_dimension.used
            body['max_output_tokens'] = min(
                requested_cap or remaining_output,
                remaining_output,
            )
            server.retries_by_request[request_id] = retries + 1
            self._forward_locked(server, phase, body)

    def _forward_locked(self, server, phase, body):
        """Serialize admission through response accounting; no in-flight bypass."""
        payload = json.dumps(body, separators=(',', ':')).encode('utf-8')
        connection = http.client.HTTPConnection(
            '127.0.0.1', server.provider_port, timeout=UPSTREAM_TIMEOUT_SECONDS)
        try:
            connection.request(
                'POST', '/v1/responses', body=payload,
                headers={
                    'Authorization': SYNTHETIC_AUTH,
                    'Content-Type': 'application/json',
                    'Content-Length': str(len(payload)),
                    'Connection': 'close',
                },
            )
            upstream = connection.getresponse()
            upstream_status = upstream.status
            upstream_body = upstream.read(MAX_REQUEST_BYTES + 1)
            if len(upstream_body) > MAX_REQUEST_BYTES:
                raise OSError('upstream response too large')
        except (OSError, TimeoutError, http.client.HTTPException):
            server.record_unknown(phase)
            _reply(self, 502, {'error': 'UPSTREAM_OUTCOME_UNKNOWN'})
            return
        finally:
            connection.close()

        if 300 <= upstream_status < 400:
            server.record_unknown(phase)
            _reply(self, 502, {'error': 'UPSTREAM_REDIRECT_BLOCKED'})
            return
        if upstream_status < 200 or upstream_status >= 300:
            server.record_unknown(phase)
            _reply(self, 502, {'error': 'UPSTREAM_FAILED'})
            return
        try:
            response = json.loads(upstream_body.decode('utf-8'))
            usage = response.get('usage')
            input_tokens = usage.get('input_tokens') if type(usage) is dict else None
            output_tokens = usage.get('output_tokens') if type(usage) is dict else None
            if type(input_tokens) is not int or type(output_tokens) is not int:
                raise ValueError
        except (UnicodeError, ValueError, AttributeError):
            server.record_unknown(phase)
            _reply(self, 502, {'error': 'UPSTREAM_USAGE_UNKNOWN'})
            return
        server.record_usage(phase, input_tokens, output_tokens)
        _reply(self, 200, response)

    def do_GET(self):
        if self.headers.get('Upgrade') or 'upgrade' in self.headers.get('Connection', '').lower():
            _reply(self, 426, {'error': 'UPGRADE_NOT_ALLOWED'})
        else:
            _reply(self, 404, {'error': 'METHOD_NOT_ALLOWED'})

    def do_PUT(self):
        _reply(self, 404, {'error': 'METHOD_NOT_ALLOWED'})

    def log_message(self, *_):
        pass


class OfflineResponsesStack:
    """Own two loopback-only servers and join their serving threads on exit."""

    def __init__(self, budget=None):
        self.budget = budget or _budget()
        self.provider = None
        self.proxy = None
        self.provider_thread = None
        self.proxy_thread = None

    def __enter__(self):
        self.provider = _Server(('127.0.0.1', 0), _ProviderHandler)
        self.provider.behavior = 'success'
        self.provider.captured = []
        self.provider.capture_lock = threading.Lock()
        self.provider.redirect_location = ''
        self.provider_port = self.provider.server_address[1]
        self.provider_thread = threading.Thread(
            target=self.provider.serve_forever, name='offline-mock-responses', daemon=False)
        self.provider_thread.start()

        self.proxy = _Server(('127.0.0.1', 0), _ProxyHandler)
        self.proxy.budget = self.budget
        self.proxy.provider_port = self.provider_port
        self.proxy.ledger = UsageLedger((), 0, 0)
        self.proxy.retries_by_request = {}
        self.proxy.admission_lock = threading.Lock()
        self.proxy.tool_rejections = []
        self.proxy_port = self.proxy.server_address[1]
        self.proxy_thread = threading.Thread(
            target=self.proxy.serve_forever, name='offline-responses-middleware', daemon=False)
        self.proxy_thread.start()
        return self

    def request(self, method='POST', path='/v1/responses', body=None, *,
                auth=SYNTHETIC_AUTH, request_id='run-1', headers=None):
        encoded = _body() if body is None else body
        connection = http.client.HTTPConnection(
            '127.0.0.1', self.proxy_port, timeout=2)
        request_headers = {
            'Authorization': auth,
            'Content-Type': 'application/json',
            'X-Request-ID': request_id,
            'Connection': 'close',
        }
        request_headers.update(headers or {})
        try:
            connection.request(method, path, body=encoded, headers=request_headers)
            response = connection.getresponse()
            return response.status, response.getheaders(), response.read()
        finally:
            connection.close()

    def close(self):
        for server in (self.proxy, self.provider):
            if server is not None:
                server.shutdown()
                server.server_close()
        for thread in (self.proxy_thread, self.provider_thread):
            if thread is not None:
                thread.join(timeout=3)

    def __exit__(self, *_):
        self.close()
        return False


def _budget():
    return RunBudget(
        schema_version=1,
        max_provider_calls=2,
        max_repair_attempts=0,
        wall_clock_ms=60_000,
        max_input_tokens=128,
        max_output_tokens=8,
    )


def _body(output_cap=None, tools=None):
    value = {'model': 'offline-mock', 'stream': True, 'input': 'synthetic'}
    if output_cap is not None:
        value['max_output_tokens'] = output_cap
    if tools is not None:
        value['tools'] = tools
    return json.dumps(value, separators=(',', ':')).encode('utf-8')


def _provider_captures(stack):
    with stack.provider.capture_lock:
        return list(stack.provider.captured)


class OfflineResponsesMiddlewareTests(unittest.TestCase):
    def test_inserted_and_clamped_caps_are_forwarded_over_loopback(self):
        with OfflineResponsesStack() as stack:
            first_status, _, _ = stack.request(body=_body(), request_id='first')
            second_status, _, _ = stack.request(
                body=_body(output_cap=64), request_id='second')
            captured = _provider_captures(stack)
            self.assertEqual(stack.proxy.server_address[0], '127.0.0.1')
            self.assertEqual(stack.provider.server_address[0], '127.0.0.1')
            self.assertEqual((first_status, second_status), (200, 200))
            self.assertEqual(len(captured), 2)
            self.assertEqual([item['path'] for item in captured],
                             ['/v1/responses', '/v1/responses'])
            self.assertEqual([item['authorization'] for item in captured],
                             [SYNTHETIC_AUTH, SYNTHETIC_AUTH])
            forwarded = [json.loads(item['body']) for item in captured]
            self.assertEqual([item['max_output_tokens'] for item in forwarded], [8, 6])
            self.assertTrue(all(item['content_type'] == 'application/json' for item in captured))
            self.assertEqual(len(stack.proxy.ledger.calls), 2)
        self.assertFalse(stack.proxy_thread.is_alive())
        self.assertFalse(stack.provider_thread.is_alive())

    def test_smaller_caller_output_cap_is_preserved(self):
        with OfflineResponsesStack() as stack:
            status, _, _ = stack.request(body=_body(output_cap=4), request_id='smaller-cap')
            captured = _provider_captures(stack)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(captured[0]['body'])['max_output_tokens'], 4)
        self.assertFalse(stack.proxy_thread.is_alive())
        self.assertFalse(stack.provider_thread.is_alive())

    def test_retry_reconnect_posts_and_runbudget_reject_before_forward(self):
        with OfflineResponsesStack() as stack:
            results = [
                stack.request(request_id='same-request'),
                stack.request(request_id='same-request'),
                stack.request(request_id='third-request'),
                stack.request(request_id='same-request'),
            ]
            self.assertEqual([status for status, _, _ in results], [200, 200, 429, 429])
            self.assertEqual(json.loads(results[2][2])['error'],
                             BudgetCode.CALL_LIMIT_REACHED.value)
            self.assertEqual(json.loads(results[3][2])['error'], 'REQUEST_RETRY_LIMIT')
            self.assertEqual(len(_provider_captures(stack)), 2)
            self.assertEqual(len(stack.proxy.ledger.calls), _budget().max_provider_calls)
        self.assertFalse(stack.proxy_thread.is_alive())
        self.assertFalse(stack.provider_thread.is_alive())

    def test_concurrent_posts_are_serialized_against_runbudget(self):
        with OfflineResponsesStack() as stack:
            with ThreadPoolExecutor(max_workers=5) as pool:
                results = list(pool.map(
                    lambda index: stack.request(request_id=f'parallel-{index}'), range(5)))
            self.assertEqual(sorted(status for status, _, _ in results), [200, 200, 429, 429, 429])
            self.assertEqual(len(_provider_captures(stack)), _budget().max_provider_calls)
            self.assertEqual(len(stack.proxy.ledger.calls), _budget().max_provider_calls)
        self.assertFalse(stack.proxy_thread.is_alive())
        self.assertFalse(stack.provider_thread.is_alive())

    def test_redirect_location_is_not_followed(self):
        with OfflineResponsesStack() as stack:
            stack.provider.behavior = 'redirect'
            stack.provider.redirect_location = (
                f'http://127.0.0.1:{stack.provider_port}/v1/redirect-target')
            status, _, raw = stack.request(request_id='redirect')
            captured = _provider_captures(stack)
            self.assertEqual(status, 502)
            self.assertEqual(json.loads(raw)['error'], 'UPSTREAM_REDIRECT_BLOCKED')
            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0]['path'], '/v1/responses')
        self.assertFalse(stack.proxy_thread.is_alive())
        self.assertFalse(stack.provider_thread.is_alive())

    def test_methods_alternate_paths_websockets_and_non_synthetic_auth_do_not_forward(self):
        with OfflineResponsesStack() as stack:
            results = [
                stack.request(path='/v1/chat/completions', request_id='alternate'),
                stack.request(method='GET', request_id='get'),
                stack.request(method='GET', request_id='websocket', headers={
                    'Upgrade': 'websocket', 'Connection': 'Upgrade',
                }),
                stack.request(auth='Bearer not-synthetic', request_id='auth'),
            ]
            self.assertEqual([status for status, _, _ in results], [404, 404, 426, 401])
            self.assertEqual(_provider_captures(stack), [])
            self.assertEqual(stack.proxy.ledger.calls, ())
        self.assertFalse(stack.proxy_thread.is_alive())
        self.assertFalse(stack.provider_thread.is_alive())

    def test_empty_tools_are_allowed_but_any_shell_exec_or_mcp_tool_is_rejected(self):
        with OfflineResponsesStack() as stack:
            absent_status, _, _ = stack.request(body=_body(), request_id='absent-tools')
            empty_status, _, _ = stack.request(
                body=_body(tools=[]), request_id='empty-tools')
            tools = [
                {'type': 'function', 'name': 'shell'},
                {'type': 'function', 'name': 'exec'},
                {'type': 'mcp', 'server': 'synthetic-test'},
            ]
            rejected_status, _, rejected_raw = stack.request(
                body=_body(tools=tools), request_id='tool-attempt')
            captured = _provider_captures(stack)
            sanitized = list(stack.proxy.tool_rejections)
            self.assertEqual((absent_status, empty_status, rejected_status), (200, 200, 403))
            self.assertEqual(json.loads(rejected_raw)['error'], 'TOOLS_NOT_ALLOWED')
            self.assertEqual(len(captured), 2)
            self.assertNotIn('tools', json.loads(captured[0]['body']))
            self.assertEqual(json.loads(captured[1]['body'])['tools'], [])
            self.assertEqual(sanitized, [{'tools_present': True, 'tool_count': 3}])
            self.assertEqual(set(sanitized[0]), {'tools_present', 'tool_count'})
        self.assertFalse(stack.proxy_thread.is_alive())
        self.assertFalse(stack.provider_thread.is_alive())

    def test_timeout_and_disconnect_are_unknown_and_retry_is_rejected_pre_forward(self):
        for behavior in ('timeout', 'disconnect'):
            with self.subTest(behavior=behavior), OfflineResponsesStack() as stack:
                stack.provider.behavior = behavior
                status, _, raw = stack.request(request_id='uncertain')
                self.assertEqual(status, 502)
                self.assertEqual(json.loads(raw)['error'], 'UPSTREAM_OUTCOME_UNKNOWN')
                retry_status, _, retry_raw = stack.request(request_id='uncertain')
                self.assertEqual(retry_status, 429)
                self.assertEqual(json.loads(retry_raw)['error'], BudgetCode.USAGE_UNKNOWN.value)
                self.assertEqual(len(_provider_captures(stack)), 1)
                self.assertEqual(len(stack.proxy.ledger.calls), 1)
            self.assertFalse(stack.proxy_thread.is_alive())
            self.assertFalse(stack.provider_thread.is_alive())


class RetainedR4CandidateDiagnostics(unittest.TestCase):
    def test_retained_candidate_source_gaps_are_bound_to_this_proof(self):
        root = os.environ.get('LLMTOOL_R4_CANDIDATE_ROOT')
        if not root:
            self.skipTest('set LLMTOOL_R4_CANDIDATE_ROOT to the retained R4 tree')
        source = Path(root, 'tools/llmtool/llmtool/codex_app_server.py').read_text(
            encoding='utf-8')
        self.assertIn("'--disable', 'shell_tool'", source)
        self.assertNotIn("'--disable', 'unified_exec'", source)
        self.assertNotIn('max_output_tokens', source)
        self.assertNotIn('RunBudget', source)
        self.assertNotIn('HTTPConnection', source)
        self.assertNotIn('WebSocket', source)


if __name__ == '__main__':
    unittest.main()
