"""Fake-runner tests for the AGY provider-neutral Developer route."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from llmtool.developer_routes import AgyRoute, RouteError, load_route_config
from llmtool.developer_routes.agy import MAX_PROMPT_BYTES
from llmtool.developer_routes.base import capture_snapshot, run_route


class Boundary:
    def __init__(self, verified=True):
        self.verified = verified

    def prepare(self, root, write_paths, state_dirs=()):
        return ['/verified-boundary'], 'a' * 64

    def verify(self, root, write_paths, state_dirs=(), *, runner):
        return {'verified': self.verified, 'policy_sha256': 'a' * 64}


class AgyRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp.name)
        self.root = self.temp_root / 'repo'
        self.root.mkdir()
        subprocess.run(['/usr/bin/git', 'init', str(self.root)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['/usr/bin/git', '-C', str(self.root), 'config', 'user.name', 'Test'], check=True)
        subprocess.run(['/usr/bin/git', '-C', str(self.root), 'config', 'user.email',
                        'test@example.invalid'], check=True)
        (self.root / 'source.py').write_text('original\n')
        subprocess.run(['/usr/bin/git', '-C', str(self.root), 'add', 'source.py'], check=True)
        subprocess.run(['/usr/bin/git', '-C', str(self.root), 'commit', '-m', 'initial'], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.branch = subprocess.check_output(
            ['/usr/bin/git', '-C', str(self.root), 'branch', '--show-current'], text=True).strip()
        self.head = subprocess.check_output(
            ['/usr/bin/git', '-C', str(self.root), 'rev-parse', 'HEAD'], text=True).strip()
        self.log = self.temp_root / 'agy.log'
        self.log.write_text('model_resolver: Resolving model gemini-4-argon-medium\n')
        self.route = AgyRoute({'executable': 'agy'})

    def tearDown(self):
        self.temp.cleanup()

    def request(self):
        return {'root': str(self.root), 'prompt': 'bounded prompt',
                'model': 'gemini-4-argon-medium', 'effort': 'medium',
                'expected_branch': self.branch, 'expected_head': self.head,
                'write_paths': ['source.py'], 'executable': '/usr/bin/agy',
                'log_path': 'agy.log'}

    def run_case(self, *, gate=True, verified=True, stdout=None, stderr=b'', code=0):
        (self.root / 'source.py').write_text('original\n')
        argv_seen = []
        raw = stdout or json.dumps({'conversation_id': 'conv_12345678',
            'status': 'completed', 'response': 'done', 'usage': {
                'input_tokens': 3, 'output_tokens': 2, 'ignored': 'x'}}).encode()
        def runner(argv, cwd, env, timeout):
            argv_seen.append(argv)
            Path(argv[argv.index('--log-file') + 1]).write_text(self.log.read_text())
            (self.root / 'source.py').write_text('changed\n')
            return code, raw, stderr
        with mock.patch.object(self.route, 'verified_model',
                               wraps=self.route.verified_model):
            result = run_route(self.route, self.request(), gate=lambda: gate,
                runner=runner, boundary=Boundary(verified),
                snapshot=capture_snapshot)
        return result, argv_seen

    def test_exact_argv_has_boundary_prefix(self):
        result, calls = self.run_case()
        self.assertEqual(calls[0][:13], ['/verified-boundary', '/usr/bin/agy', '-p',
            'bounded prompt', '--model', 'gemini-4-argon-medium', '--effort',
            'medium', '--sandbox', '--output-format', 'json', '--print-timeout',
            '0'])
        self.assertEqual(calls[0][13:15], ['--log-file', calls[0][-1]])
        self.assertTrue(calls[0][-1].endswith('/agy.log'))
        self.assertIn('llmtool-route-', calls[0][-1])
        self.assertNotIn('--add-dir', calls[0])
        self.assertNotIn('--mode', calls[0])
        self.assertEqual(result.model, 'gemini-4-argon-medium')
        self.assertEqual(result.usage, {'input_tokens': 3, 'output_tokens': 2})
        self.assertEqual(result.changed_files, ('source.py',))

    def test_prompt_byte_limit_boundary(self):
        request = self.request()
        request['prompt'] = 'x' * MAX_PROMPT_BYTES
        self.route.validate_request(request)
        request['prompt'] = 'x' * (MAX_PROMPT_BYTES + 1)
        with self.assertRaises(RouteError) as ctx:
            self.route.validate_request(request)
        self.assertEqual(ctx.exception.code, 'AGY_REQUEST_INVALID')

    def test_owner_gate_and_unverified_boundary_start_nothing(self):
        for boundary, gate in ((Boundary(), False), (Boundary(False), True)):
            calls = []
            with self.assertRaises(RouteError):
                run_route(self.route, self.request(), gate=lambda: gate,
                    runner=lambda *args: calls.append(args), boundary=boundary,
                    snapshot=capture_snapshot)
            self.assertEqual(calls, [])

    def test_output_duplicate_invalid_model_and_quota_fail_typed(self):
        for raw, error in ((b'not-json', 'AGY_OUTPUT_INVALID'),
            (b'{"status":"ok","status":"bad"}', 'AGY_OUTPUT_INVALID'),
            (json.dumps({'status': 'x', 'conversation_id': 'conv_12345678',
                         'error': 'RESOURCE_EXHAUSTED'}).encode(), 'AGY_QUOTA_EXHAUSTED')):
            with self.assertRaises(RouteError) as caught:
                self.run_case(stdout=raw)
            self.assertEqual(caught.exception.code, error)
        with self.assertRaises(RouteError) as caught:
            self.run_case(stdout=b'broken', stderr=b'rate limit')
        self.assertEqual(caught.exception.code, 'AGY_QUOTA_EXHAUSTED')

    def test_missing_and_mismatched_model_are_typed(self):
        for log, code in (('', 'AGY_MODEL_UNVERIFIED'),
                          ('model_resolver: Resolving model other\n', 'AGY_MODEL_MISMATCH')):
            self.log.write_text(log)
            with self.assertRaises(RouteError) as caught:
                self.run_case()
            self.assertEqual(caught.exception.code, code)

    def test_config_loader_rejects_unknown_and_personal_values(self):
        config = load_route_config()
        self.assertEqual(config['routes']['agy']['default_model'], 'gemini-3.8-flash-medium')
        self.assertEqual(config['routes']['agy']['default_effort'], 'medium')
        for entry in ({'unexpected': True}, {'executable': '/Users/name/agy'}):
            path = self.root / 'bad.json'
            path.write_text(json.dumps({'schema_version': 1, 'routes': {'agy': entry}}))
            with self.assertRaises(RouteError):
                load_route_config(path)

    def test_log_in_private_temp_is_read_for_model_verification(self):
        self.log.write_text('model_resolver: Resolving model gemini-4-argon-medium\n')
        result, calls = self.run_case()
        self.assertEqual(result.model, 'gemini-4-argon-medium')
        self.assertFalse(Path(calls[0][-1]).exists())


if __name__ == '__main__':
    unittest.main()
