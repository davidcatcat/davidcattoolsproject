import json
import os
import subprocess
import unittest
from unittest.mock import patch

from llmtool import remote_test
from llmtool.errors import LLMToolError


SHA = 'a' * 40
REF = 'refs/pull/1257/head'
NONCE = '0123456789abcdef0123456789abcdef'


class HostedRemoteTestHelpersTests(unittest.TestCase):
    def make_run(self, **overrides):
        run = {
            'id': 1001,
            'display_title': 'llmtool remote-test ' + NONCE,
            'head_branch': 'main',
            'path': '.github/workflows/llmtool-remote-test.yml',
            'actor': {'login': 'davidcatcat'},
            'triggering_actor': {'login': 'ddavidccatssun'},
        }
        run.update(overrides)
        return run

    def test_pinned_hosted_constants_and_limits(self):
        self.assertEqual(
            remote_test._HOSTED_WORKFLOW_PATH,
            '.github/workflows/llmtool-remote-test.yml')
        self.assertEqual(remote_test._HOSTED_JOB_NAME, 'remote-test')
        self.assertEqual(remote_test._HOSTED_LABELS, ('ubuntu-24.04',))
        self.assertEqual(remote_test._HOSTED_RUNNER_GROUP, 'GitHub Actions')
        self.assertEqual(remote_test._HOSTED_APP_ID, 15368)
        self.assertEqual(
            remote_test._HOSTED_ACTORS, ('davidcatcat', 'ddavidccatssun'))
        self.assertEqual(
            remote_test._HOSTED_TITLE_PREFIX, 'llmtool remote-test ')
        self.assertEqual(
            remote_test._HOSTED_ARTIFACT_PREFIX, 'remote-test-receipt-')
        self.assertEqual(remote_test._HOSTED_ZIP_LIMIT, 65536)
        self.assertEqual(remote_test._HOSTED_MEMBER_LIMIT, 32768)
        self.assertIsNotNone(remote_test._NONCE.fullmatch(NONCE))
        self.assertIsNone(remote_test._NONCE.fullmatch('A' * 32))
        self.assertIsNone(remote_test._NONCE.fullmatch('a' * 31))
        self.assertIsNone(remote_test._NONCE.fullmatch('a' * 33))
        self.assertIsNone(remote_test._NONCE.fullmatch(NONCE + '\n'))

    def test_new_nonce_uses_secrets_and_returns_32_lowercase_hex(self):
        first = remote_test._new_nonce()
        second = remote_test._new_nonce()
        self.assertIsNotNone(remote_test._NONCE.fullmatch(first))
        self.assertIsNotNone(remote_test._NONCE.fullmatch(second))
        self.assertNotEqual(first, second)
        with patch.object(remote_test.secrets, 'token_hex',
                          return_value=NONCE) as token_hex:
            self.assertEqual(remote_test._new_nonce(), NONCE)
            token_hex.assert_called_once_with(16)

    def test_hosted_dispatch_validates_inputs_before_calling_post(self):
        calls = []

        def post(path, body):
            calls.append((path, body))

        bad_cases = (
            ('bad-sha', REF, ('test_one',), NONCE),
            ('A' * 40, REF, ('test_one',), NONCE),
            (None, REF, ('test_one',), NONCE),
            (SHA, '--bad-ref', ('test_one',), NONCE),
            (SHA, '', ('test_one',), NONCE),
            (SHA, None, ('test_one',), NONCE),
            (SHA, REF, (), NONCE),
            (SHA, REF, tuple(f'test_m{i}' for i in range(9)), NONCE),
            (SHA, REF, ('test_b', 'test_a'), NONCE),
            (SHA, REF, ('test_a', 'test_a'), NONCE),
            (SHA, REF, ('not_a_test',), NONCE),
            (SHA, REF, ('test_BadUpper',), NONCE),
            (SHA, REF, 'test_one', NONCE),
            (SHA, REF, {'test_one'}, NONCE),
            (SHA, REF, (None,), NONCE),
            (SHA, REF, ('test_one',), 'bad-nonce'),
            (SHA, REF, ('test_one',), 'A' * 32),
            (SHA, REF, ('test_one',), None),
        )
        for sha, ref, modules, nonce in bad_cases:
            with self.subTest(sha=sha, ref=ref, modules=modules, nonce=nonce):
                with self.assertRaises(ValueError):
                    remote_test._hosted_dispatch(
                        sha, ref, modules, nonce, post=post)
        self.assertEqual(calls, [])

    def test_hosted_dispatch_posts_exact_path_and_body_and_returns_dispatched(self):
        calls = []

        def post(path, body):
            calls.append((path, body))

        outcome = remote_test._hosted_dispatch(
            SHA, REF, ('test_alpha', 'test_beta'), NONCE, post=post)

        self.assertEqual(outcome, 'DISPATCHED')
        self.assertEqual(calls, [(
            'actions/workflows/llmtool-remote-test.yml/dispatches',
            {
                'ref': 'main',
                'inputs': {
                    'sha': SHA,
                    'ref': REF,
                    'modules': 'test_alpha test_beta',
                    'nonce': NONCE,
                },
            },
        )])

    def test_hosted_dispatch_returns_unconfirmed_on_post_failure(self):
        for exc in (LLMToolError('failed'), OSError('offline'), RuntimeError('boom')):
            with self.subTest(exc=type(exc).__name__):
                calls = []

                def post(path, body, error=exc):
                    calls.append((path, body))
                    raise error

                outcome = remote_test._hosted_dispatch(
                    SHA, 'main', ['test_alpha'], NONCE, post=post)
                self.assertEqual(outcome, 'UNCONFIRMED')
                self.assertEqual(len(calls), 1)

    def test_default_post_helper_uses_fixed_gh_argv_env_and_pipe_stdin(self):
        class FakeStdin:
            def __init__(self):
                self.written = b''
                self.closed = False

            def write(self, data):
                self.written += data

            def close(self):
                self.closed = True

        class FakeStdout:
            def fileno(self):
                return 9

            def close(self):
                self.closed = True

        class FakeProcess:
            def __init__(self, argv, **kwargs):
                self.argv = argv
                self.kwargs = kwargs
                self.stdin = FakeStdin()
                self.stdout = FakeStdout()
                self.pid = 4321

            def wait(self, timeout=None):
                return 0

            def poll(self):
                return 0

        class FakeSelector:
            def __init__(self):
                self.registered = False

            def register(self, fileobj, events):
                self.registered = True

            def unregister(self, fileobj):
                self.registered = False

            def get_map(self):
                return {1: 1} if self.registered else {}

            def select(self, timeout=None):
                return [(type('Key', (), {'fileobj': FakeStdout()})(), 1)]

            def close(self):
                pass

        created = []

        def fake_popen(argv, **kwargs):
            proc = FakeProcess(argv, **kwargs)
            created.append(proc)
            return proc

        body = {
            'ref': 'main',
            'inputs': {
                'sha': SHA,
                'ref': REF,
                'modules': 'test_remote_test',
                'nonce': NONCE,
            },
        }
        with patch.object(remote_test.shutil, 'which', return_value='/usr/bin/gh'), \
                patch.object(remote_test, '_trusted_slug',
                             return_value='davidcatcat/davidcattoolsproject'), \
                patch.object(remote_test.subprocess, 'Popen', side_effect=fake_popen), \
                patch.object(remote_test.selectors, 'DefaultSelector', FakeSelector), \
                patch.object(remote_test.os, 'set_blocking'), \
                patch.object(remote_test.os, 'read', side_effect=[b'']), \
                patch.dict(os.environ, {'GH_CONFIG_DIR': '/tmp/gh-cfg',
                                        'UNTRUSTED_VAR': 'ignored'}, clear=False):
            result = remote_test._hosted_post_dispatch(
                remote_test._HOSTED_DISPATCH_PATH, body)

        self.assertEqual(result, b'')
        self.assertEqual(len(created), 1)
        proc = created[0]
        self.assertEqual(proc.argv, [
            '/usr/bin/gh', 'api', '--method', 'POST',
            '-H', 'Accept: application/vnd.github+json',
            '-H', 'X-GitHub-Api-Version: 2022-11-28',
            'repos/davidcatcat/davidcattoolsproject/'
            'actions/workflows/llmtool-remote-test.yml/dispatches',
            '--input', '-',
        ])
        self.assertFalse(proc.kwargs['shell'])
        self.assertTrue(proc.kwargs['close_fds'])
        self.assertTrue(proc.kwargs['start_new_session'])
        self.assertEqual(proc.kwargs['stdin'], subprocess.PIPE)
        self.assertEqual(proc.kwargs['stdout'], subprocess.PIPE)
        self.assertEqual(proc.kwargs['stderr'], subprocess.DEVNULL)
        self.assertEqual(
            set(proc.kwargs['env']),
            {'PATH', 'HOME', 'LC_ALL', 'GH_PROMPT_DISABLED',
             'GH_NO_UPDATE_NOTIFIER', 'NO_COLOR', 'GH_CONFIG_DIR'})
        self.assertNotIn('UNTRUSTED_VAR', proc.kwargs['env'])
        self.assertTrue(proc.stdin.closed)
        self.assertEqual(json.loads(proc.stdin.written.decode('utf-8')), body)

    def test_default_post_helper_rejects_any_other_path(self):
        with patch.object(remote_test.subprocess, 'Popen') as popen:
            for bad_path in (
                    'actions/workflows/other.yml/dispatches',
                    'actions/workflows/llmtool-remote-test.yml/dispatches/extra',
                    'pulls/1',
                    ''):
                with self.subTest(bad_path=bad_path):
                    with self.assertRaises(ValueError):
                        remote_test._hosted_post_dispatch(bad_path, {'ref': 'main'})
            popen.assert_not_called()

    def test_hosted_find_run_validates_nonce_and_queries_expected_endpoint(self):
        for bad_nonce in ('bad', 'A' * 32, 'a' * 31, None):
            with self.subTest(bad_nonce=bad_nonce):
                with self.assertRaises(ValueError):
                    remote_test._hosted_find_run(
                        bad_nonce, get=lambda path: self.fail('get called'))

        paths = []
        expected_run = self.make_run()

        def get(path):
            paths.append(path)
            return {'workflow_runs': [expected_run]}

        found = remote_test._hosted_find_run(NONCE, get=get)
        self.assertIs(found, expected_run)
        self.assertEqual(len(paths), 1)
        self.assertIn('actions/workflows/llmtool-remote-test.yml/runs?', paths[0])
        self.assertIn('event=workflow_dispatch', paths[0])
        self.assertIn('branch=main', paths[0])

    def test_hosted_find_run_filters_title_branch_path_and_actors(self):
        matching_first_actor = self.make_run(
            id=1,
            actor={'login': 'davidcatcat'},
            triggering_actor={'login': 'davidcatcat'})
        matching_second_actor = self.make_run(
            id=2,
            actor={'login': 'ddavidccatssun'},
            triggering_actor={'login': 'ddavidccatssun'})
        non_matching = [
            self.make_run(id=10, display_title='llmtool remote-test ' + 'f' * 32),
            self.make_run(id=11, display_title='llmtool remote-test ' + NONCE + ' extra'),
            self.make_run(id=12, head_branch='feature'),
            self.make_run(id=13, path='.github/workflows/other.yml'),
            self.make_run(id=14, actor={'login': 'outsider'}),
            self.make_run(id=15, triggering_actor={'login': 'outsider'}),
            self.make_run(id=16, actor=None),
            self.make_run(id=17, triggering_actor={}),
        ]

        found_one = remote_test._hosted_find_run(
            NONCE,
            get=lambda path: {'workflow_runs': [*non_matching, matching_first_actor]})
        self.assertEqual(found_one, matching_first_actor)

        found_two = remote_test._hosted_find_run(
            NONCE,
            get=lambda path: {'workflow_runs': [*non_matching, matching_second_actor]})
        self.assertEqual(found_two, matching_second_actor)

        none_found = remote_test._hosted_find_run(
            NONCE,
            get=lambda path: {'workflow_runs': non_matching})
        self.assertIsNone(none_found)

    def test_hosted_find_run_returns_ambiguous_when_multiple_runs_match(self):
        first = self.make_run(id=1)
        second = self.make_run(id=2, actor={'login': 'ddavidccatssun'})
        result = remote_test._hosted_find_run(
            NONCE,
            get=lambda path: {'workflow_runs': [first, second]})
        self.assertEqual(result, 'AMBIGUOUS')

    def test_hosted_find_run_returns_unreadable_on_get_error_or_malformed_payload(self):
        self.assertEqual(
            remote_test._hosted_find_run(
                NONCE, get=lambda path: (_ for _ in ()).throw(LLMToolError('fail'))),
            'UNREADABLE')
        for bad_payload in (
                None,
                [],
                {},
                {'workflow_runs': None},
                {'workflow_runs': 'not-a-list'},
                {'workflow_runs': ['not-a-dict']}):
            with self.subTest(bad_payload=bad_payload):
                self.assertEqual(
                    remote_test._hosted_find_run(
                        NONCE, get=lambda path, value=bad_payload: value),
                    'UNREADABLE')

    def test_hosted_find_run_defaults_to_task_completion_github_get(self):
        expected = self.make_run()
        with patch.object(remote_test, '_github_get',
                          return_value={'workflow_runs': [expected]}) as github_get:
            found = remote_test._hosted_find_run(NONCE)
        self.assertEqual(found, expected)
        github_get.assert_called_once()


if __name__ == '__main__':
    unittest.main()
