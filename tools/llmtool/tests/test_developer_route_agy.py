"""Fake-runner tests for the AGY provider-neutral Developer route."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from llmtool.developer_routes import AgyRoute, RouteError, load_route_config
from llmtool.developer_routes.base import run_route
from llmtool import task_completion


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
        self.root = Path(self.temp.name)
        self.log = self.root / 'agy.log'
        self.log.write_text('model_resolver: Resolving model gemini-4-argon-medium\n')
        self.route = AgyRoute({'executable': 'agy'})
        self.git = task_completion.GitSnapshot(str(self.root), str(self.root / '.git'),
                                                'task', 'a' * 40, True)
        self.before = {'files': {'source.py': (0o600, 'old')},
                       'git_state': {'head': 'a' * 40}}
        self.after = {'files': {'source.py': (0o600, 'new')},
                      'git_state': {'head': 'a' * 40}}

    def tearDown(self):
        self.temp.cleanup()

    def request(self):
        return {'root': str(self.root), 'prompt': 'bounded prompt',
                'model': 'gemini-4-argon-medium', 'effort': 'medium',
                'expected_branch': 'task', 'expected_head': 'a' * 40,
                'write_paths': ['source.py'], 'executable': '/usr/bin/agy',
                'log_path': str(self.log)}

    def run_case(self, *, gate=True, verified=True, stdout=None, stderr=b'', code=0):
        argv_seen = []
        raw = stdout or json.dumps({'conversation_id': 'conv_12345678',
            'status': 'completed', 'response': 'done', 'usage': {
                'input_tokens': 3, 'output_tokens': 2, 'ignored': 'x'}}).encode()
        def runner(argv, cwd, env, timeout):
            argv_seen.append(argv)
            return code, raw, stderr
        with mock.patch('llmtool.developer_routes.base.task_completion.collect_git_snapshot',
                        return_value=self.git), \
             mock.patch.object(self.route, 'verified_model',
                               wraps=self.route.verified_model):
            result = run_route(self.route, self.request(), gate=lambda: gate,
                runner=runner, boundary=Boundary(verified),
                snapshot=lambda root: self.before if not argv_seen else self.after)
        return result, argv_seen

    def test_exact_argv_has_boundary_prefix(self):
        result, calls = self.run_case()
        self.assertEqual(calls[0], ['/verified-boundary', '/usr/bin/agy', '-p',
            'bounded prompt', '--model', 'gemini-4-argon-medium', '--effort',
            'medium', '--sandbox', '--output-format', 'json', '--print-timeout',
            '0', '--log-file', str(self.log)])
        self.assertNotIn('--add-dir', calls[0])
        self.assertNotIn('--mode', calls[0])
        self.assertEqual(result.model, 'gemini-4-argon-medium')
        self.assertEqual(result.usage, {'input_tokens': 3, 'output_tokens': 2})
        self.assertEqual(result.changed_files, ('source.py',))

    def test_owner_gate_and_unverified_boundary_start_nothing(self):
        for boundary, gate in ((Boundary(), False), (Boundary(False), True)):
            calls = []
            with mock.patch('llmtool.developer_routes.base.task_completion.collect_git_snapshot',
                            return_value=self.git):
                with self.assertRaises(RouteError):
                    run_route(self.route, self.request(), gate=lambda: gate,
                        runner=lambda *args: calls.append(args), boundary=boundary,
                        snapshot=lambda root: self.before)
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
        self.assertEqual(config['routes']['agy']['default_effort'], 'medium')
        for entry in ({'unexpected': True}, {'executable': '/Users/name/agy'}):
            path = self.root / 'bad.json'
            path.write_text(json.dumps({'schema_version': 1, 'routes': {'agy': entry}}))
            with self.assertRaises(RouteError):
                load_route_config(path)


if __name__ == '__main__':
    unittest.main()
