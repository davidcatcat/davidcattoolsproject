"""Fake-runner tests for the Claude provider-neutral Developer route."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import uuid

from llmtool.developer_routes import ClaudeRoute, RouteError, load_route_config
from llmtool.developer_routes.claude import MAX_PROMPT_BYTES
from llmtool.developer_routes.base import capture_snapshot, run_route


class Boundary:
    def __init__(self, verified=True):
        self.verified = verified

    def prepare(self, root, write_paths, state_dirs=()):
        return ['/verified-boundary'], 'a' * 64

    def verify(self, root, write_paths, state_dirs=(), *, runner):
        return {'verified': self.verified, 'policy_sha256': 'a' * 64}


class ClaudeRouteTests(unittest.TestCase):
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
        self.session = str(uuid.uuid4())
        self.route = ClaudeRoute({'executable': 'claude'})

    def tearDown(self):
        self.temp.cleanup()

    def request(self, prompt='bounded prompt'):
        return {'root': str(self.root), 'prompt': prompt, 'model': 'claude-opus-5-5',
                'effort': 'high', 'expected_branch': self.branch,
                'expected_head': self.head, 'write_paths': ['source.py'],
                'executable': '/usr/bin/claude'}

    def envelope(self, **updates):
        value = {'type': 'result', 'subtype': 'success', 'is_error': False,
                 'session_id': self.session, 'result': 'done',
                 'modelUsage': {'claude-opus-5-5': {'outputTokens': 2}},
                 'usage': {'input_tokens': 3, 'output_tokens': 2,
                           'cache_read_input_tokens': 5,
                           'cache_creation_input_tokens': 7}}
        value.update(updates)
        return json.dumps(value).encode()

    def run_case(self, *, gate=True, verified=True, stdout=None, stderr=b'', code=0,
                 outside=False):
        (self.root / 'source.py').write_text('original\n')
        calls = []
        raw = self.envelope() if stdout is None else stdout

        def runner(argv, cwd, env, timeout):
            calls.append((argv, cwd, env, timeout))
            (self.root / 'source.py').write_text('changed\n')
            if outside:
                (self.root / 'other.py').write_text('outside\n')
            return code, raw, stderr

        try:
            result = run_route(self.route, self.request(), gate=lambda: gate,
                               runner=runner, boundary=Boundary(verified),
                               snapshot=capture_snapshot)
            return result, calls
        finally:
            (self.root / 'source.py').write_text('original\n')
            (self.root / 'other.py').unlink(missing_ok=True)

    def test_success_exact_argv_usage_and_prompt_delimiter(self):
        result, calls = self.run_case()
        self.assertEqual(result.status, 'DONE')
        self.assertEqual(result.session_id, self.session)
        self.assertEqual(result.model, 'claude-opus-5-5')
        self.assertEqual(result.usage, {'prompt_tokens': 15, 'completion_tokens': 2,
                                        'total_tokens': 17, 'cached_prompt_tokens': 5})
        self.assertEqual(result.changed_files, ('source.py',))
        self.assertEqual(calls[0][0], ['/verified-boundary'] + self.route.build_argv(
            '/usr/bin/claude', self.request()))
        request = self.request(prompt='--please interpret as prompt')
        expected = ['/usr/bin/claude', '-p', '--output-format', 'json', '--model',
                    'claude-opus-5-5', '--effort', 'high', '--no-session-persistence',
                    '--restricted', '--tools', 'Read,Edit,Write,Glob,Grep',
                    '--permission-mode', 'acceptEdits', '--permission-prompts', 'none',
                    '--strict-mcp-config', '--', request['prompt']]
        self.assertEqual(self.route.build_argv(request['executable'], request), expected)
        for forbidden in ('--bg', '--resume', '--continue', '--fallback-model', '--bare',
                          '--dangerously-skip-permissions', '--add-dir'):
            self.assertNotIn(forbidden, expected)

    def test_prompt_profile_and_config_validation(self):
        self.route.validate_request(self.request('x' * MAX_PROMPT_BYTES))
        with self.assertRaises(RouteError) as caught:
            self.route.validate_request(self.request('x' * (MAX_PROMPT_BYTES + 1)))
        self.assertEqual(caught.exception.code, 'CLAUDE_REQUEST_INVALID')
        for model, effort in (('claude', 'high'), ('opus', 'high'),
                              ('claude-opus-5-5', 'default')):
            with self.assertRaises(RouteError) as caught:
                self.route.validate_profile(model, effort)
            self.assertEqual(caught.exception.code, 'CLAUDE_PROFILE_INVALID')
        route = ClaudeRoute({'executable': 'claude', 'config_dir': 'relative'})
        with self.assertRaises(RouteError) as caught:
            route.validate_request(self.request())
        self.assertEqual(caught.exception.code, 'CLAUDE_CONFIG_INVALID')

    def test_environment_allowlist_and_bad_values(self):
        route = ClaudeRoute({'executable': 'claude', 'config_dir': '/tmp/claude-config'})
        with mock.patch.dict('os.environ', {'ANTHROPIC_MODEL': 'secret-model',
                                            'UNRELATED': 'secret-value',
                                            'HOME': '/tmp/home'}, clear=True):
            env = route.build_env('/tmp/private')
        self.assertNotIn('ANTHROPIC_MODEL', env)
        self.assertNotIn('UNRELATED', env)
        self.assertEqual(env['CLAUDE_CONFIG_DIR'], '/tmp/claude-config')
        self.assertEqual(env['TMPDIR'], '/tmp/private')
        for config in ({'executable': 'claude', 'config_dir': '/bad\npath'},
                       {'executable': 'claude', 'config_dir': '/ok'}):
            route = ClaudeRoute(config)
            with mock.patch.dict('os.environ', {'HOME': 'relative'}, clear=True):
                with self.assertRaises(RouteError) as caught:
                    route.build_env('/tmp/private')
            self.assertEqual(caught.exception.code, 'CLAUDE_ENV_INVALID')

    def test_invalid_envelopes_duplicate_and_quota_parse(self):
        invalid = [self.envelope(is_error=1), self.envelope(session_id='not-a-uuid'),
                   b'{"type":"result","type":"result"}', b'not-json']
        missing = json.loads(self.envelope())
        missing.pop('session_id')
        invalid.append(json.dumps(missing).encode())
        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaises(RouteError) as caught:
                    self.route.parse_output(raw, b'', '')
                self.assertEqual(caught.exception.code, 'CLAUDE_OUTPUT_INVALID')
        parsed = self.route.parse_output(b'not-json', b'rate limit reached', '')
        self.assertEqual(parsed, {'status': 'error', 'summary': '', 'session_id': None,
                                  'usage': None, 'raw': {}})
        with self.subTest(subtype='error_max_turns'):
            with self.assertRaises(RouteError) as caught:
                self.run_case(stdout=self.envelope(is_error=True, subtype='error_max_turns'))
            self.assertEqual(caught.exception.code, 'CLAUDE_STATUS_FAILED')

    def test_model_usage_and_status_cases(self):
        cases = [({'claude-opus-5-5': {'outputTokens': 1},
                   'claude-helper': {'outputTokens': 3}}, 'claude-opus-5-5'),
                 ({'claude-other': {'outputTokens': 4}}, 'claude-other'),
                 ({'other': {'outputTokens': 2}}, '\0'), ({}, None),
                 ({'claude-opus-5-5': {'outputTokens': 0}}, None)]
        for models, expected in cases:
            parsed = self.route.parse_output(self.envelope(modelUsage=models), b'', '')
            self.assertEqual(self.route.verified_model('', 'claude-opus-5-5'), expected)
            if parsed['status'] != 'DONE':
                with self.assertRaises(RouteError) as caught:
                    self.route.validate_status(parsed)
                self.assertEqual(caught.exception.code, 'CLAUDE_STATUS_FAILED')
        parsed = self.route.parse_output(self.envelope(modelUsage={}), b'', '')
        self.assertIsNone(self.route.verified_model('ignored', 'claude-opus-5-5'))
        self.route.parse_output(self.envelope(), b'', '')
        self.assertEqual(self.route.verified_model('ignored', 'claude-opus-5-5'),
                         'claude-opus-5-5')

    def test_model_verification_failures_are_typed_by_run_route(self):
        for models, expected in (({'claude-other': {'outputTokens': 4}},
                                  'CLAUDE_MODEL_MISMATCH'),
                                 ({'other': {'outputTokens': 4}},
                                  'CLAUDE_MODEL_MISMATCH'),
                                 ({}, 'CLAUDE_MODEL_UNVERIFIED'),
                                 ({'claude-opus-5-5': {'outputTokens': 0}},
                                  'CLAUDE_MODEL_UNVERIFIED')):
            raw = self.envelope(modelUsage=models)
            with self.subTest(models=models):
                with self.assertRaises(RouteError) as caught:
                    self.run_case(stdout=raw)
                self.assertEqual(caught.exception.code, expected)

    def test_run_route_exit_scope_gate_and_boundary(self):
        for kwargs, expected in (({'code': 1, 'outside': True},
                                  'CLAUDE_WRITE_SCOPE_VIOLATION'),
                                 ({'code': 1}, 'CLAUDE_EXIT_NONZERO')):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(RouteError) as caught:
                    self.run_case(**kwargs)
                self.assertEqual(caught.exception.code, expected)
        for gate, verified, expected in ((False, True, 'CLAUDE_DISPATCH_DENIED'),
                                         (True, False, 'BLOCKED_WRITE_ISOLATION')):
            calls = []
            with self.assertRaises(RouteError) as caught:
                run_route(self.route, self.request(), gate=lambda: gate,
                    runner=lambda *args: calls.append(args), boundary=Boundary(verified),
                    snapshot=capture_snapshot)
            self.assertEqual(caught.exception.code, expected)
            self.assertEqual(calls, [])

    def test_leftover_claude_temp_file_is_out_of_scope(self):
        temp_path = self.root / 'source.py.tmp.123.0123456789ab'

        def runner(argv, cwd, env, timeout):
            (self.root / 'source.py').write_text('changed\n')
            temp_path.write_text('left behind')
            return 0, self.envelope(), b''

        try:
            with self.assertRaises(RouteError) as caught:
                run_route(self.route, self.request(), gate=lambda: True, runner=runner,
                          boundary=Boundary(), snapshot=capture_snapshot)
            self.assertEqual(caught.exception.code, 'CLAUDE_WRITE_SCOPE_VIOLATION')
        finally:
            temp_path.unlink(missing_ok=True)
            (self.root / 'source.py').write_text('original\n')

    def test_run_route_classifies_nonzero_provider_failures_before_exit(self):
        cases = (
            (self.envelope(is_error=True, subtype='error_api',
                           result='rate limit reached'), b'',
             'CLAUDE_QUOTA_EXHAUSTED'),
            (self.envelope(is_error=True, subtype='error_api', result='request failed'), b'',
             'CLAUDE_STATUS_FAILED'),
            (self.envelope(), b'', 'CLAUDE_EXIT_NONZERO'),
        )
        for stdout, stderr, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(RouteError) as caught:
                    self.run_case(code=1, stdout=stdout, stderr=stderr)
                self.assertEqual(caught.exception.code, expected)

    def test_quota_only_for_failed_runs(self):
        failed = self.route.parse_output(self.envelope(is_error=True, subtype='error_api',
                                                       result='Rate-limit exceeded'), b'', '')
        self.assertTrue(self.route.quota_exhausted(failed, b''))
        successful = self.route.parse_output(self.envelope(result='quota is available'), b'', '')
        self.assertFalse(self.route.quota_exhausted(successful, b''))
        failed = self.route.parse_output(self.envelope(is_error=True, subtype='error_api',
                                                       api_error_status=429), b'', '')
        self.assertTrue(self.route.quota_exhausted(failed, b''))
        with self.assertRaises(RouteError) as caught:
            self.run_case(code=1, stdout=b'invalid JSON', stderr=b'rate-limit response')
        self.assertEqual(caught.exception.code, 'CLAUDE_QUOTA_EXHAUSTED')
        result, _calls = self.run_case(stdout=self.envelope(result='quota mentioned, success'))
        self.assertEqual(result.status, 'DONE')

    def test_route_config_accepts_optional_claude_and_rejects_bad_entries(self):
        config = load_route_config()
        self.assertEqual(config['routes']['claude']['default_model'], 'claude-opus-5-5')
        self.assertEqual(config['routes']['claude']['default_effort'], 'high')
        routes = config['routes']
        for changes in ({'executable': '/usr/bin/claude'}, {'extra': True},
                        {'default_model': 'opus'}, {'default_effort': 'default'}):
            bad = dict(routes['claude'])
            bad.update(changes)
            data = {'schema_version': 1, 'routes': {'agy': routes['agy'], 'claude': bad}}
            path = self.root / 'bad.json'
            path.write_text(json.dumps(data))
            with self.assertRaises(RouteError) as caught:
                load_route_config(path)
            self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')
        without = {'schema_version': 1, 'routes': {'agy': routes['agy']}}
        path = self.root / 'without.json'
        path.write_text(json.dumps(without))
        self.assertNotIn('claude', load_route_config(path)['routes'])


if __name__ == '__main__':
    unittest.main()
