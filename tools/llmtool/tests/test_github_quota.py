import contextlib
import io
import json
import os
import subprocess
import unittest
from unittest.mock import patch

from llmtool import github_quota


def _payload(core_remaining=4500, core_reset=1700000100,
             graphql_remaining=4900, graphql_reset=1700000200):
    return json.dumps({
        'resources': {
            'core': {
                'limit': 5000,
                'used': 500,
                'remaining': core_remaining,
                'reset': core_reset,
            },
            'graphql': {
                'limit': 5000,
                'used': 100,
                'remaining': graphql_remaining,
                'reset': graphql_reset,
            },
        },
    }).encode('utf-8')


class GitHubQuotaTests(unittest.TestCase):
    def setUp(self):
        which = patch('llmtool.github_quota.shutil.which',
                      return_value='/opt/homebrew/bin/gh')
        self.which = which.start()
        self.addCleanup(which.stop)

    def test_read_parses_core_and_graphql_from_clean_gh_call(self):
        observed = []

        def runner(argv, env):
            observed.append((argv, env))
            return 0, _payload(4321, 1700000111, 0, 0)

        with patch.dict(os.environ, {'GH_CONFIG_DIR': '/tmp/gh-cfg',
                                     'UNTRUSTED_ENV': 'ignored'}, clear=False):
            quota = github_quota.read(runner=runner)

        self.which.assert_called_once_with('gh', path=github_quota._GH_PATH)
        self.assertEqual(len(observed), 1)
        argv, env = observed[0]
        self.assertEqual(argv, ['/opt/homebrew/bin/gh', 'api', 'rate_limit'])
        self.assertEqual(set(env), {
            'PATH', 'HOME', 'LC_ALL', 'GH_PROMPT_DISABLED',
            'GH_NO_UPDATE_NOTIFIER', 'NO_COLOR', 'GH_CONFIG_DIR',
        })
        self.assertEqual(env['PATH'], github_quota._GH_PATH)
        self.assertEqual(
            env['GH_CONFIG_DIR'],
            github_quota.github_identity.ai_gh_config_dir(env['HOME']))
        self.assertNotIn('UNTRUSTED_ENV', env)
        self.assertEqual(quota, {
            'core': {'remaining': 4321, 'reset': 1700000111},
            'graphql': {'remaining': 0, 'reset': 0},
        })

    def test_clean_env_uses_ai_config_dir_when_caller_has_none(self):
        # The caller env without GH_CONFIG_DIR still gets the AI config dir.
        with patch.dict(os.environ, clear=False):
            os.environ.pop('GH_CONFIG_DIR', None)
            env = github_quota._clean_env()
        self.assertEqual(
            env['GH_CONFIG_DIR'],
            github_quota.github_identity.ai_gh_config_dir(env['HOME']))
        self.assertTrue(env['GH_CONFIG_DIR'].endswith('/.config/gh-ai'))

    def test_clean_env_overrides_caller_gh_config_dir_and_drops_gh_token(self):
        # The caller's GH_CONFIG_DIR is ignored and no token is passed through.
        with patch.dict(os.environ, {'GH_CONFIG_DIR': '/other',
                                     'GH_TOKEN': 'synthetic-not-a-token'}):
            env = github_quota._clean_env()
        self.assertEqual(
            env['GH_CONFIG_DIR'],
            github_quota.github_identity.ai_gh_config_dir(env['HOME']))
        self.assertNotEqual(env['GH_CONFIG_DIR'], '/other')
        self.assertNotIn('GH_TOKEN', env)

    def test_read_returns_none_when_gh_is_missing_or_invocation_fails(self):
        called = []
        self.which.return_value = None
        self.assertIsNone(github_quota.read(runner=lambda *args: called.append(args)))
        self.assertEqual(called, [])

        self.which.return_value = '/usr/bin/gh'
        failures = [
            lambda _argv, _env: (_ for _ in ()).throw(FileNotFoundError('no gh')),
            lambda _argv, _env: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(cmd=['gh'], timeout=8)),
            lambda _argv, _env: (1, _payload()),
            lambda _argv, _env: (-1, _payload()),
            lambda _argv, _env: (True, _payload()),
            lambda _argv, _env: type('R', (), {'returncode': 2,
                                               'stdout': _payload()})(),
        ]
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            for runner in failures:
                self.assertIsNone(github_quota.read(runner=runner))
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(err.getvalue(), '')

    def test_read_returns_none_for_malformed_json_or_invalid_values(self):
        bad_outputs = [
            b'',
            b'not json',
            b'\xff\xfe',
            b'[]',
            b'123',
            b'null',
            b'{}',
            b'{"resources":[]}',
            b'{"resources":{"core":{}}}',
            b'{"resources":{"graphql":{"remaining":1,"reset":1}}}',
            b'{"resources":{"core":{"remaining":1,"remaining":2,"reset":1},'
            b'"graphql":{"remaining":1,"reset":1}}}',
            b'{"resources":{"core":{"remaining":NaN,"reset":1},'
            b'"graphql":{"remaining":1,"reset":1}}}',
            _payload(core_remaining=-1),
            _payload(core_reset=-1),
            _payload(graphql_remaining=-1),
            _payload(graphql_reset=-1),
            _payload(core_remaining=True),
            _payload(core_reset=False),
            _payload(graphql_remaining=True),
            _payload(graphql_reset=False),
            _payload(core_remaining=10.5),
            _payload(core_reset='1700000100'),
            _payload(graphql_remaining='4900'),
            _payload(graphql_reset=None),
        ]
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            for raw in bad_outputs:
                self.assertIsNone(
                    github_quota.read(runner=lambda _argv, _env, data=raw: (0, data)))
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(err.getvalue(), '')

    def test_low_checks_floor_boundary_and_picks_core_before_graphql(self):
        self.assertIsNone(github_quota.low(None))
        self.assertIsNone(github_quota.low({}))
        self.assertIsNone(github_quota.low({
            'core': {'remaining': 300, 'reset': 100},
            'graphql': {'remaining': 300, 'reset': 200},
        }))
        self.assertEqual(github_quota.low({
            'core': {'remaining': 299, 'reset': 100},
            'graphql': {'remaining': 5000, 'reset': 200},
        }), {'resource': 'core', 'remaining': 299, 'reset': 100})
        self.assertEqual(github_quota.low({
            'core': {'remaining': 300, 'reset': 100},
            'graphql': {'remaining': 299, 'reset': 200},
        }), {'resource': 'graphql', 'remaining': 299, 'reset': 200})
        self.assertEqual(github_quota.low({
            'core': {'remaining': 299, 'reset': 100},
            'graphql': {'remaining': 0, 'reset': 200},
        }), {'resource': 'core', 'remaining': 299, 'reset': 100})
        self.assertEqual(github_quota.low({
            'core': {'remaining': 400, 'reset': 100},
            'graphql': {'remaining': 399, 'reset': 200},
        }, floor=400), {'resource': 'graphql', 'remaining': 399, 'reset': 200})


if __name__ == '__main__':
    unittest.main()
