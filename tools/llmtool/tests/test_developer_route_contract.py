"""Contract tests for provider-neutral direct Developer routes."""

import unittest
from unittest import mock
from pathlib import Path
import subprocess
import tempfile

from llmtool import task_completion
from llmtool.developer_routes.base import (
    DeveloperRoute, RouteError, _git_state_changed, _git_state_difference,
    capture_snapshot, run_route,
)

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
        self.after = {'files': {'out.txt': (0o600, 'old')},
                      'git_state': {'head': 'a' * 40}}

    def run_case(self, *, decision=True, git=None, proof=True, after=None,
                 code=0, stdout=b'output', stderr=b'', verified=_DEFAULT_MODEL):
        calls = []
        runner = lambda *args: (calls.append(args) or (code, stdout, stderr))
        snapshots = iter((self.before, self.after if after is None else after))
        with mock.patch('llmtool.developer_routes.base.task_completion.collect_git_snapshot',
                        return_value=self.git if git is None else git):
            with mock.patch('llmtool.developer_routes.base._git_status_paths', return_value=()):
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

    def test_agy_exit_nonzero_keeps_a_screened_stderr_tail(self):
        # #1606: AGY_EXIT_NONZERO carries the exit code and a bounded,
        # screened one-line stderr tail; other routes keep no text.
        self.route.name = 'agy'
        key = 'q' * 24
        cred = b'api_' + b'key'  # built here so the spec text holds no credential shape
        stderr = b'x' * 4000 + b'\nfatal: ' + cred + b'=' + key.encode() + b' boom\x07\n'
        with self.assertRaises(RouteError) as caught:
            self.run_case(code=3, stderr=stderr)
        reason = caught.exception.blocked_reason
        self.assertEqual(caught.exception.code, 'AGY_EXIT_NONZERO')
        self.assertTrue(reason.startswith('exit_code=3 stderr_tail='))
        self.assertIn('boom', reason)
        self.assertNotIn(key, reason)
        self.assertNotIn('\n', reason)
        self.assertNotIn('\x07', reason)
        self.assertLessEqual(len(reason.encode('utf-8')), 2048)
        self.route.name = 'FAKE'
        with self.assertRaises(RouteError) as caught:
            self.run_case(code=3, stderr=b'boom')
        self.assertIsNone(caught.exception.blocked_reason)

    def test_post_run_git_mutation_classified_before_dirty_state(self):
        changed = {'files': self.after['files'],
                   'git_state': {'head': 'b' * 40}}
        with mock.patch('llmtool.developer_routes.base.task_completion.collect_git_snapshot',
                        side_effect=(self.git, task_completion.GitSnapshot(
                            '/root', '/git', 'task', 'b' * 40, False))):
            with mock.patch('llmtool.developer_routes.base._git_status_paths', return_value=()):
                with self.assertRaises(RouteError) as caught:
                    run_route(self.route, self.request, gate=lambda: True,
                              runner=lambda *args: (0, b'ok', b''), boundary=FakeBoundary(),
                              snapshot=lambda root: self.before if not hasattr(self, '_seen') else changed)
        self.assertEqual(caught.exception.code, 'FAKE_GIT_STATE_CHANGED_IDENTITY')
        self.assertEqual(caught.exception.facts, {'git_state_label': 'IDENTITY'})

    def test_snapshot_and_status_failures_use_snapshot_label(self):
        failure = OSError('private detail')
        for snapshots, statuses in (((self.before,), (failure,)),
                                    ((self.before, failure), ((),)),
                                    ((self.before, self.after), ((), failure))):
            with self.subTest(snapshots=len(snapshots), statuses=len(statuses)):
                with mock.patch('llmtool.developer_routes.base.task_completion.collect_git_snapshot',
                                return_value=self.git):
                    with mock.patch('llmtool.developer_routes.base._git_status_paths',
                                    side_effect=statuses):
                        with self.assertRaises(RouteError) as caught:
                            run_route(self.route, self.request, gate=lambda: True,
                                      runner=lambda *args: (0, b'ok', b''),
                                      boundary=FakeBoundary(),
                                      snapshot=mock.Mock(side_effect=snapshots))
                self.assertEqual(caught.exception.code, 'FAKE_GIT_STATE_CHANGED_SNAPSHOT')
                self.assertEqual(caught.exception.facts, {'git_state_label': 'SNAPSHOT'})

    def test_baseline_snapshot_failure_uses_snapshot_label_and_starts_no_runner(self):
        calls = []
        with mock.patch('llmtool.developer_routes.base.task_completion.collect_git_snapshot',
                        return_value=self.git):
            with self.assertRaises(RouteError) as caught:
                run_route(self.route, self.request, gate=lambda: True,
                          runner=lambda *args: calls.append(args), boundary=FakeBoundary(),
                          snapshot=mock.Mock(side_effect=OSError('private detail')))
        self.assertEqual(caught.exception.code, 'FAKE_GIT_STATE_CHANGED_SNAPSHOT')
        self.assertEqual(caught.exception.facts, {'git_state_label': 'SNAPSHOT'})
        self.assertEqual(calls, [])

    def test_git_state_difference_returns_fixed_labels(self):
        for key, label in (('head', 'HEAD'), ('branch', 'BRANCH'),
                           ('branch_ref', 'BRANCH_REF'), ('git_dir', 'GIT_DIR'),
                           ('index', 'INDEX'), ('stash', 'STASH'), ('pointer', 'POINTER'),
                           ('config_worktree', 'CONFIG_WORKTREE'),
                           ('config_common', 'CONFIG_COMMON')):
            with self.subTest(key=key):
                before = {'git_state': {key: 'before'}}
                after = {'git_state': {key: 'after'}}
                self.assertEqual(_git_state_difference(before, after), label)
                self.assertTrue(_git_state_changed(before, after))
        invalid = {'git_state': {'config_common': 'INVALID:same'}}
        self.assertEqual(_git_state_difference(invalid, invalid), 'CONFIG_COMMON')
        # The first difference wins when several states changed.
        self.assertEqual(_git_state_difference(
            {'git_state': {'head': 'a', 'index': 'a'}},
            {'git_state': {'head': 'b', 'index': 'b'}}), 'HEAD')

    def test_git_state_difference_identity_fallback(self):
        self.assertEqual(_git_state_difference({'head': 'a'}, {'head': 'b'}), 'IDENTITY')
        self.assertEqual(_git_state_difference({'git_state': {'head': 'a'}},
                                               {'git_state': 'not-a-mapping'}), 'IDENTITY')
        self.assertIsNone(_git_state_difference({'head': 'a'}, {'head': 'a'}))
        self.assertFalse(_git_state_changed({'head': 'a'}, {'head': 'a'}))

    def test_other_worktree_branches_come_from_after_snapshot(self):
        head = 'a' * 40
        listed = {'head': head, 'refs': {}, 'worktree_branches': frozenset({'other'})}
        unlisted = {'head': head, 'refs': {}, 'worktree_branches': frozenset()}
        ref = {'refs/heads/other': (head, head)}
        self.assertEqual(_git_state_difference({'git_state': listed},
                                               {'git_state': dict(unlisted, refs=ref)}), 'REFS')
        self.assertIsNone(_git_state_difference({'git_state': unlisted},
                                                {'git_state': dict(listed, refs=ref)}))

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
            with mock.patch('llmtool.developer_routes.base._git_status_paths', return_value=()):
                with self.assertRaises(RouteError) as caught:
                    run_route(self.route, self.request, gate=lambda: True, runner=timeout,
                              boundary=FakeBoundary(), snapshot=lambda root: self.before)
        self.assertEqual(caught.exception.code, 'FAKE_TIMEOUT')

    def _real_git_request(self, root):
        subprocess.run(['/usr/bin/git', 'init', str(root)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['/usr/bin/git', '-C', str(root), 'config', 'user.name', 'Test'], check=True)
        subprocess.run(['/usr/bin/git', '-C', str(root), 'config', 'user.email', 'test@example.invalid'], check=True)
        (root / 'out.txt').write_text('before\n')
        subprocess.run(['/usr/bin/git', '-C', str(root), 'add', 'out.txt'], check=True)
        subprocess.run(['/usr/bin/git', '-C', str(root), 'commit', '-m', 'initial'], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        branch = subprocess.check_output(['/usr/bin/git', '-C', str(root), 'branch', '--show-current'],
                                         text=True).strip()
        head = subprocess.check_output(['/usr/bin/git', '-C', str(root), 'rev-parse', 'HEAD'],
                                       text=True).strip()
        return dict(self.request, root=str(root), expected_branch=branch,
                    expected_head=head, write_paths=['out.txt'])

    def test_real_git_authorized_tracked_edit_succeeds(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            root.mkdir()
            request = self._real_git_request(root)

            def runner(*args):
                (root / 'out.txt').write_text('after\n')
                return 0, b'output', b''

            result = run_route(self.route, request, gate=lambda: True, runner=runner,
                               boundary=FakeBoundary(), snapshot=capture_snapshot)
            self.assertEqual(result.changed_files, ('out.txt',))

    def test_unrelated_branch_refs_do_not_change_git_state(self):
        for create_during_run in (True, False):
            with self.subTest(create_during_run=create_during_run):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp) / 'repo'
                    root.mkdir()
                    request = self._real_git_request(root)
                    branch = request['expected_branch']
                    head = request['expected_head']
                    tree = subprocess.check_output(
                        ['/usr/bin/git', '-C', str(root), 'rev-parse', f'{head}^{{tree}}'],
                        text=True).strip()
                    other_head = subprocess.check_output(
                        ['/usr/bin/git', '-C', str(root), 'commit-tree', tree, '-p', head,
                         '-m', 'unrelated branch update'], text=True).strip()
                    other_ref = 'refs/heads/other'
                    if not create_during_run:
                        subprocess.run(['/usr/bin/git', '-C', str(root), 'update-ref',
                                        other_ref, head], check=True)

                    def runner(*args):
                        if create_during_run:
                            subprocess.run(['/usr/bin/git', '-C', str(root), 'update-ref',
                                            other_ref, head], check=True)
                        subprocess.run(['/usr/bin/git', '-C', str(root), 'update-ref',
                                        other_ref, other_head], check=True)
                        (root / 'out.txt').write_text('after\n')
                        return 0, b'output', b''

                    result = run_route(self.route, request, gate=lambda: True, runner=runner,
                                       boundary=FakeBoundary(), snapshot=capture_snapshot)
                    self.assertEqual(result.changed_files, ('out.txt',))

    def test_other_worktree_commit_and_branch_do_not_change_git_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            other = Path(temp) / 'other-worktree'
            root.mkdir()
            request = self._real_git_request(root)

            def runner(*args):
                subprocess.run(['/usr/bin/git', '-C', str(root), 'worktree', 'add',
                                '--detach', str(other), request['expected_head']],
                               check=True, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
                (other / 'external.txt').write_text('external commit\n')
                subprocess.run(['/usr/bin/git', '-C', str(other), 'add', 'external.txt'],
                               check=True)
                subprocess.run(['/usr/bin/git', '-C', str(other), 'commit', '-m',
                                'other worktree commit'], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(['/usr/bin/git', '-C', str(other), 'branch',
                                'other-worktree-tip'], check=True)
                (root / 'out.txt').write_text('after\n')
                return 0, b'output', b''

            result = run_route(self.route, request, gate=lambda: True, runner=runner,
                               boundary=FakeBoundary(), snapshot=capture_snapshot)
            self.assertEqual(result.changed_files, ('out.txt',))

    def test_other_worktree_new_branch_at_d6_head_is_not_owned(self):
        """#1109: another D6 branching from this head must not fail this run."""
        for with_tracking_ref in (False, True):
            with self.subTest(with_tracking_ref=with_tracking_ref):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp) / 'repo'
                    other = Path(temp) / 'other-worktree'
                    root.mkdir()
                    request = self._real_git_request(root)
                    head = request['expected_head']

                    def runner(*args):
                        subprocess.run(['/usr/bin/git', '-C', str(root), 'worktree', 'add',
                                        '-b', 'team/other-task', str(other), head],
                                       check=True, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
                        if with_tracking_ref:
                            subprocess.run(['/usr/bin/git', '-C', str(root), 'update-ref',
                                            'refs/remotes/origin/team/other-task', head],
                                           check=True)
                        (root / 'out.txt').write_text('after\n')
                        return 0, b'output', b''

                    result = run_route(self.route, request, gate=lambda: True, runner=runner,
                                       boundary=FakeBoundary(), snapshot=capture_snapshot)
                    self.assertEqual(result.changed_files, ('out.txt',))

    def test_refs_at_d6_head_outside_other_worktree_branches_are_detected(self):
        for template in ('refs/remotes/origin/{current}', 'refs/tags/other-task',
                         'refs/notes/other-task', 'refs/heads/other-task-2',
                         'refs/remotes/other-task',
                         'refs/remotes/origin/nested/other-task'):
            with self.subTest(template=template):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp) / 'repo'
                    linked = Path(temp) / 'linked'
                    root.mkdir()
                    request = self._real_git_request(root)
                    head = request['expected_head']
                    subprocess.run(['/usr/bin/git', '-C', str(root), 'worktree', 'add',
                                    '-b', 'other-task', str(linked), head],
                                   check=True, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
                    ref = template.format(current=request['expected_branch'])

                    def runner(*args):
                        subprocess.run(['/usr/bin/git', '-C', str(root), 'update-ref', ref, head],
                                       check=True)
                        return 0, b'output', b''

                    with self.assertRaises(RouteError) as caught:
                        run_route(self.route, request, gate=lambda: True, runner=runner,
                                  boundary=FakeBoundary(), snapshot=capture_snapshot)
                    self.assertEqual(caught.exception.code, 'FAKE_GIT_STATE_CHANGED_REFS')

    def test_capture_snapshot_lists_only_other_worktree_branches(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            linked = Path(temp) / 'linked'
            detached = Path(temp) / 'detached'
            root.mkdir()
            request = self._real_git_request(root)
            head = request['expected_head']
            for args in (('-b', 'team/other-task', str(linked), head),
                         ('--detach', str(detached), head)):
                subprocess.run(['/usr/bin/git', '-C', str(root), 'worktree', 'add', *args],
                               check=True, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            with mock.patch('llmtool.developer_routes.base.task_completion._git',
                            wraps=task_completion._git) as git:
                snapshot = capture_snapshot(root)
            self.assertEqual(snapshot['git_state']['worktree_branches'], {'team/other-task'})
            self.assertEqual(capture_snapshot(linked)['git_state']['worktree_branches'],
                             {request['expected_branch']})
            call = next(item for item in git.call_args_list
                        if item.args[1] == ('worktree', 'list', '--porcelain'))
            self.assertGreaterEqual(call.kwargs['limit'], 1024 * 1024)

    def test_git_state_refusal_carries_no_values(self):
        config_value = 'Private config value'
        for label, command in (
                ('REFS', ('tag', 'private-ref-name')),
                ('CONFIG_COMMON', ('config', 'user.name', config_value))):
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp) / 'repo'
                    root.mkdir()
                    request = self._real_git_request(root)

                    def runner(*args):
                        (root / 'out.txt').write_text('after\n')
                        subprocess.run(['/usr/bin/git', '-C', str(root), *command], check=True)
                        return 0, b'output', b''

                    with self.assertRaises(RouteError) as caught:
                        run_route(self.route, request, gate=lambda: True, runner=runner,
                                  boundary=FakeBoundary(), snapshot=capture_snapshot)
                    error = caught.exception
                    self.assertEqual(error.code, f'FAKE_GIT_STATE_CHANGED_{label}')
                    self.assertEqual(error.facts, {'git_state_label': label})
                    self.assertIsNone(error.blocked_reason)
                    rendered = ' '.join((error.code, repr(error.facts), str(error)))
                    for value in ('private-ref-name', config_value, 'out.txt', str(root),
                                  request['expected_head'], request['expected_branch']):
                        self.assertNotIn(value, rendered)

    def test_owned_git_state_changes_are_detected(self):
        for mutation, label in (('branch', 'HEAD'), ('commit', 'HEAD'),
                                ('checkout', 'BRANCH'), ('index', 'INDEX'),
                                ('stash', 'STASH')):
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp) / 'repo'
                    root.mkdir()
                    request = self._real_git_request(root)
                    branch_ref = f"refs/heads/{request['expected_branch']}"
                    head = request['expected_head']
                    tree = subprocess.check_output(
                        ['/usr/bin/git', '-C', str(root), 'rev-parse', f'{head}^{{tree}}'],
                        text=True).strip()
                    new_head = subprocess.check_output(
                        ['/usr/bin/git', '-C', str(root), 'commit-tree', tree, '-p', head,
                         '-m', 'D6 branch update'], text=True).strip()

                    def runner(*args):
                        (root / 'out.txt').write_text('after\n')
                        if mutation == 'branch':
                            subprocess.run(['/usr/bin/git', '-C', str(root), 'update-ref',
                                            branch_ref, new_head], check=True)
                        elif mutation == 'commit':
                            subprocess.run(['/usr/bin/git', '-C', str(root), 'add', 'out.txt'],
                                           check=True)
                            subprocess.run(['/usr/bin/git', '-C', str(root), 'commit', '-m',
                                            'D6 commit'], check=True, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL)
                        elif mutation == 'checkout':
                            subprocess.run(['/usr/bin/git', '-C', str(root), 'checkout', '-b',
                                            'd6-checkout'], check=True,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        elif mutation == 'index':
                            subprocess.run(['/usr/bin/git', '-C', str(root), 'add', 'out.txt'],
                                           check=True)
                        else:
                            subprocess.run(['/usr/bin/git', '-C', str(root), 'stash', 'push',
                                            '-m', 'D6 stash'], check=True,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        return 0, b'output', b''

                    with self.assertRaises(RouteError) as caught:
                        run_route(self.route, request, gate=lambda: True, runner=runner,
                                  boundary=FakeBoundary(), snapshot=capture_snapshot)
                    self.assertEqual(caught.exception.code,
                                     f'FAKE_GIT_STATE_CHANGED_{label}')

    def test_refs_created_from_d6_are_detected(self):
        for mutation in ('branch', 'lightweight_tag', 'annotated_tag', 'notes'):
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp) / 'repo'
                    root.mkdir()
                    request = self._real_git_request(root)

                    def runner(*args):
                        (root / 'out.txt').write_text('after\n')
                        if mutation == 'branch':
                            command = ['branch', 'd6-created']
                        elif mutation == 'lightweight_tag':
                            command = ['tag', 'd6-lightweight']
                        elif mutation == 'annotated_tag':
                            command = ['tag', '-a', 'd6-annotated', '-m', 'D6 tag']
                        else:
                            command = ['notes', 'add', '-m', 'D6 note']
                        subprocess.run(['/usr/bin/git', '-C', str(root), *command],
                                       check=True, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
                        return 0, b'output', b''

                    with self.assertRaises(RouteError) as caught:
                        run_route(self.route, request, gate=lambda: True, runner=runner,
                                  boundary=FakeBoundary(), snapshot=capture_snapshot)
                    self.assertEqual(caught.exception.code, 'FAKE_GIT_STATE_CHANGED_REFS')

    def test_git_config_changes_are_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            root.mkdir()
            request = self._real_git_request(root)

            def runner(*args):
                (root / 'out.txt').write_text('after\n')
                subprocess.run(['/usr/bin/git', '-C', str(root), 'config', 'user.name',
                                'Changed during D6'], check=True)
                return 0, b'output', b''

            with self.assertRaises(RouteError) as caught:
                run_route(self.route, request, gate=lambda: True, runner=runner,
                          boundary=FakeBoundary(), snapshot=capture_snapshot)
            self.assertEqual(caught.exception.code, 'FAKE_GIT_STATE_CHANGED_CONFIG_COMMON')

    def test_worktree_git_config_changes_are_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            root.mkdir()
            request = self._real_git_request(root)
            subprocess.run(['/usr/bin/git', '-C', str(root), 'config',
                            'extensions.worktreeConfig', 'true'], check=True)

            def runner(*args):
                (root / 'out.txt').write_text('after\n')
                subprocess.run(['/usr/bin/git', '-C', str(root), 'config', '--worktree',
                                'user.name', 'Changed during D6'], check=True)
                return 0, b'output', b''

            with self.assertRaises(RouteError) as caught:
                run_route(self.route, request, gate=lambda: True, runner=runner,
                          boundary=FakeBoundary(), snapshot=capture_snapshot)
            self.assertEqual(caught.exception.code, 'FAKE_GIT_STATE_CHANGED_CONFIG_WORKTREE')

    def test_other_branch_tracking_config_is_ignored_with_linked_worktree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            linked = Path(temp) / 'linked'
            root.mkdir()
            request = self._real_git_request(root)
            subprocess.run(['/usr/bin/git', '-C', str(root), 'worktree', 'add',
                            '-b', 'other-task', str(linked), request['expected_head']],
                           check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            before = capture_snapshot(root)
            subprocess.run(['/usr/bin/git', '-C', str(root), 'config',
                            'branch.other-task.remote', 'origin'], check=True)
            subprocess.run(['/usr/bin/git', '-C', str(root), 'config',
                            'branch.other-task.merge', 'refs/heads/other-task'], check=True)
            after = capture_snapshot(root)
            self.assertFalse(_git_state_changed(before, after))

    def test_other_branch_non_tracking_and_default_branch_config_is_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            linked = Path(temp) / 'linked'
            root.mkdir()
            request = self._real_git_request(root)
            subprocess.run(['/usr/bin/git', '-C', str(root), 'worktree', 'add',
                            '-b', 'other-task', str(linked), request['expected_head']],
                           check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            for key, value in (
                    ('branch.other-task.pushRemote', 'changed'),
                    ('branch.other-task.rebase', 'true'),
                    ('branch.main.pushRemote', 'changed'),
                    ('branch.master.remote', 'changed'),
                    ('branch.default.remote', 'changed')):
                with self.subTest(key=key):
                    before = capture_snapshot(root)
                    subprocess.run(['/usr/bin/git', '-C', str(root), 'config', key, value],
                                   check=True)
                    after = capture_snapshot(root)
                    self.assertTrue(_git_state_changed(before, after))

    def test_worktree_common_config_changes_are_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            root.mkdir()
            self._real_git_request(root)
            before = capture_snapshot(root)
            subprocess.run(['/usr/bin/git', '-C', str(root), 'config',
                            'worktree.guessRemote', 'true'], check=True)
            after = capture_snapshot(root)
            self.assertTrue(_git_state_changed(before, after))

    def test_valueless_config_keys_are_normalized_as_true(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            root.mkdir()
            self._real_git_request(root)
            before = capture_snapshot(root)
            with (root / '.git' / 'config').open('a') as config:
                config.write('\n[route-test]\nflag\n')
            after = capture_snapshot(root)
            self.assertNotEqual(before['git_state']['config_common'],
                                after['git_state']['config_common'])

    def test_current_branch_and_security_config_changes_are_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            root.mkdir()
            request = self._real_git_request(root)
            config = root / '.git' / 'config'
            hooks = root / 'hooks'
            hooks.mkdir()
            include = root / 'included.conf'
            include.write_text('[route-test]\nvalue = included\n')
            conditional_include = root / 'conditional.conf'
            conditional_include.write_text('[route-test]\nconditional = included\n')
            mutations = (
                ('core.hooksPath', str(hooks)),
                ('include.path', str(include)),
                (f"includeIf.onbranch:{request['expected_branch']}.path",
                 str(conditional_include)),
                ('filter.test.clean', 'clean-command'),
                ('alias.test', '!custom-command'),
                ('remote.origin.url', 'https://example.invalid/repo'),
                ('credential.helper', 'custom-helper'),
            )
            branch_key = f"branch.{request['expected_branch']}.remote"
            mutations = ((branch_key, 'origin'),) + mutations
            for key, value in mutations:
                with self.subTest(key=key):
                    before = capture_snapshot(root)
                    subprocess.run(['/usr/bin/git', '-C', str(root), 'config', key, value],
                                   check=True)
                    after = capture_snapshot(root)
                    self.assertTrue(_git_state_changed(before, after))
            before = capture_snapshot(root)
            config.write_text('[broken\n')
            # An unparsable common config can make even read-only Git identity
            # commands fail before a second snapshot is available. That is a
            # fail-closed outcome and is the expected behavior here.
            with self.assertRaises(RouteError):
                capture_snapshot(root)

    def test_preexisting_ignored_directory_and_in_scope_edit_succeed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            root.mkdir()
            request = self._real_git_request(root)
            ignored = root / 'ignored'
            ignored.mkdir()
            (ignored / 'existing.txt').write_text('ignored\n')
            exclude = root / '.git' / 'info' / 'exclude'
            exclude.write_text(exclude.read_text() + '\nignored/\n')

            def runner(*args):
                (root / 'out.txt').write_text('after\n')
                return 0, b'output', b''

            result = run_route(self.route, request, gate=lambda: True, runner=runner,
                               boundary=FakeBoundary(), snapshot=capture_snapshot)
            self.assertEqual(result.changed_files, ('out.txt',))

    def test_real_git_untracked_out_of_scope_write_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            root.mkdir()
            request = self._real_git_request(root)

            def runner(*args):
                (root / 'other.txt').write_text('outside\n')
                return 0, b'output', b''

            with self.assertRaises(RouteError) as caught:
                run_route(self.route, request, gate=lambda: True, runner=runner,
                           boundary=FakeBoundary(), snapshot=capture_snapshot)
            self.assertEqual(caught.exception.code, 'FAKE_WRITE_SCOPE_VIOLATION')

    def test_request_state_dirs_must_be_private_temp_descendants(self):
        with tempfile.TemporaryDirectory() as outside:
            request = dict(self.request, state_dirs=[outside])
            with mock.patch('llmtool.developer_routes.base.task_completion.collect_git_snapshot',
                            return_value=self.git):
                with mock.patch('llmtool.developer_routes.base._git_status_paths', return_value=()):
                    with self.assertRaises(RouteError) as caught:
                        run_route(self.route, request, gate=lambda: True,
                                  runner=lambda *args: self.fail('runner must not start'),
                                  boundary=FakeBoundary(), snapshot=lambda root: self.before)
            self.assertEqual(caught.exception.code, 'FAKE_REQUEST_INVALID')

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
