"""Contract tests for provider-neutral direct Developer routes."""

import unittest
from unittest import mock

from llmtool import task_completion
from llmtool.developer_routes.base import DeveloperRoute, RouteError, run_route

_DEFAULT_MODEL = object()


class FakeRoute(DeveloperRoute):
    name = 'FAKE'
    worker_key = 'fake'
    family = 'test-family'

    def validate_profile(self, model, effort):
        if (model, effort) != ('fake-model', 'low'):
            raise RouteError('FAKE_PROFILE_INVALID')
        return model, effort

    def build_argv(self, executable, request):
        return [executable, 'run']

    def build_env(self, tmpdir):
        return {'TMPDIR': tmpdir}

    def parse_output(self, stdout, stderr, log_text):
        return {'status': 'DONE', 'summary': 'ok', 'session_id': 'session_1234',
                'usage': {'total_tokens': 3}}

    def verified_model(self, log_text, requested):
        return log_text or requested

    def quota_exhausted(self, parsed, stderr):
        return stderr == b'quota'


class FakeBoundary:
    def __init__(self, verified=True):
        self.verified = verified

    def prepare(self, root, write_paths, state_dirs=()):
        return ['/boundary'], 'a' * 64

    def verify(self, root, write_paths, state_dirs=(), *, runner):
        return {'verified': self.verified, 'policy_sha256': 'a' * 64, 'probes': []}


class DeveloperRouteContractTests(unittest.TestCase):
    def setUp(self):
        self.route = FakeRoute({})
        self.request = {'root': '.', 'model': 'fake-model', 'effort': 'low',
                        'expected_branch': 'task', 'expected_head': 'a' * 40,
                        'write_paths': ['out.txt'], 'executable': '/fake/cli',
                        'log_path': '/nonexistent/fake-log'}
        self.git = task_completion.GitSnapshot('/root', '/git', 'task', 'a' * 40, True)
        self.before = {'files': {'out.txt': (0o600, 'old')},
                       'git_state': {'head': 'a' * 40}}
        self.after = {'files': {'out.txt': (0o600, 'new')},
                      'git_state': {'head': 'a' * 40}}

    def run_case(self, *, decision=True, git=None, proof=True, after=None,
                 code=0, stdout=b'output', stderr=b'', verified=_DEFAULT_MODEL):
        calls = []
        runner = lambda *args: (calls.append(args) or (code, stdout, stderr))
        snapshots = iter((self.before, self.after if after is None else after))
        with mock.patch('llmtool.developer_routes.base.task_completion.collect_git_snapshot',
                        return_value=self.git if git is None else git):
            with mock.patch.object(self.route, 'verified_model',
                                   side_effect=lambda text, requested:
                                   requested if verified is _DEFAULT_MODEL else verified):
                result = run_route(self.route, self.request,
                               gate=lambda: decision, runner=runner,
                               boundary=FakeBoundary(proof), snapshot=lambda root: next(snapshots))
        return result, calls

    def test_gate_refusal_starts_no_runner(self):
        calls = []
        with mock.patch('llmtool.developer_routes.base.task_completion.collect_git_snapshot') as git:
            with self.assertRaises(RouteError):
                run_route(self.route, self.request, gate=lambda: False,
                          runner=lambda *args: calls.append(args), boundary=FakeBoundary(),
                          snapshot=lambda root: self.before)
        git.assert_not_called()
        self.assertEqual(calls, [])

    def test_dirty_or_wrong_head_starts_no_runner(self):
        for snap in (task_completion.GitSnapshot('/root', '/git', 'task', 'a' * 40, False),
                     task_completion.GitSnapshot('/root', '/git', 'task', 'b' * 40, True)):
            calls = []
            with mock.patch('llmtool.developer_routes.base.task_completion.collect_git_snapshot',
                            return_value=snap):
                with self.assertRaises(RouteError):
                    run_route(self.route, self.request, gate=lambda: True,
                              runner=lambda *args: calls.append(args), boundary=FakeBoundary(),
                              snapshot=lambda root: self.before)
            self.assertEqual(calls, [])

    def test_unverified_boundary_starts_no_runner(self):
        calls = []
        with mock.patch('llmtool.developer_routes.base.task_completion.collect_git_snapshot',
                        return_value=self.git):
            with self.assertRaises(RouteError):
                run_route(self.route, self.request, gate=lambda: True,
                          runner=lambda *args: calls.append(args), boundary=FakeBoundary(False),
                          snapshot=lambda root: self.before)
        self.assertEqual(calls, [])

    def test_success_and_model_quota_exit_failures(self):
        result, calls = self.run_case()
        self.assertEqual(result.route, 'FAKE')
        self.assertEqual(result.session_id, 'session_1234')
        self.assertEqual(len(calls), 1)
        for values, code in (({'verified': None}, 'FAKE_MODEL_UNVERIFIED'),
                             ({'verified': 'other'}, 'FAKE_MODEL_MISMATCH'),
                             ({'stderr': b'quota'}, 'FAKE_QUOTA_EXHAUSTED'),
                             ({'code': 124}, 'FAKE_EXIT_NONZERO')):
            with self.assertRaises(RouteError) as caught:
                self.run_case(**values)
            self.assertEqual(caught.exception.code, code)

    def test_post_run_git_mutation_classified_before_dirty_state(self):
        changed = {'files': self.after['files'],
                   'git_state': {'head': 'b' * 40}}
        with mock.patch('llmtool.developer_routes.base.task_completion.collect_git_snapshot',
                        side_effect=(self.git, task_completion.GitSnapshot(
                            '/root', '/git', 'task', 'b' * 40, False))):
            with self.assertRaises(RouteError) as caught:
                run_route(self.route, self.request, gate=lambda: True,
                          runner=lambda *args: (0, b'ok', b''), boundary=FakeBoundary(),
                          snapshot=lambda root: self.before if not hasattr(self, '_seen') else changed)
        self.assertEqual(caught.exception.code, 'FAKE_GIT_STATE_CHANGED')

    def test_out_of_scope_write_and_timeout_fail_typed(self):
        outside = {'files': {'other.txt': (0o600, 'new')},
                   'git_state': {'head': 'a' * 40}}
        with self.assertRaises(RouteError) as caught:
            self.run_case(after=outside)
        self.assertEqual(caught.exception.code, 'FAKE_WRITE_SCOPE_VIOLATION')
        def timeout(*args):
            raise TimeoutError
        with mock.patch('llmtool.developer_routes.base.task_completion.collect_git_snapshot',
                        return_value=self.git):
            with self.assertRaises(RouteError) as caught:
                run_route(self.route, self.request, gate=lambda: True, runner=timeout,
                          boundary=FakeBoundary(), snapshot=lambda root: self.before)
        self.assertEqual(caught.exception.code, 'FAKE_TIMEOUT')

    def test_request_error_uses_uppercase_route_code(self):
        request = dict(self.request, timeout=0)
        with self.assertRaises(RouteError) as caught:
            run_route(self.route, request, gate=lambda: True, runner=lambda *args: None,
                      boundary=FakeBoundary(), snapshot=lambda root: self.before)
        self.assertEqual(caught.exception.code, 'FAKE_REQUEST_INVALID')

    def test_quota_skips_model_verification_and_keeps_progress(self):
        with self.assertRaises(RouteError) as caught:
            self.run_case(stderr=b'quota', verified=None)
        self.assertEqual(caught.exception.code, 'FAKE_QUOTA_EXHAUSTED')
        self.assertEqual(caught.exception.progress['state'], 'QUOTA_EXHAUSTED')

    def test_write_scope_precedes_quota_and_keeps_quota_progress(self):
        outside = {'files': {'other.txt': (0o600, 'new')},
                   'git_state': {'head': 'a' * 40}}
        with self.assertRaises(RouteError) as caught:
            self.run_case(after=outside, stderr=b'quota', verified=None)
        self.assertEqual(caught.exception.code, 'FAKE_WRITE_SCOPE_VIOLATION')
        self.assertEqual(caught.exception.progress['state'], 'QUOTA_EXHAUSTED')


if __name__ == '__main__':
    unittest.main()
