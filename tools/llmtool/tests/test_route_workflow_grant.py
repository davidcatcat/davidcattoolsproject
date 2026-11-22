"""#2281 W3: workflow grants on the shared Developer route request contract.

The grant source is always the merged W1 module. These tests build a throwaway
Git repository with a reviewed rule-file fixture, so no real grant, credential
or repository file is used.
"""

import contextlib
import copy
import json
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import authorization_workflow_grant, task_submission_cli
from llmtool.developer_routes.base import (
    DeveloperRoute, RouteError, WORKFLOW_GRANT_REFUSED)
from llmtool.task_store import TaskStoreConflict

GRANT_PATH = 'tools/llmtool/workflow-grants.json'
PATH = '.github/workflows/ci.yml'
OTHER = '.github/workflows/other.yml'
RELEASE = '.github/workflows/release.yml'
ISSUE = 2281
OWNER = ('https://github.com/davidcatcat/davidcattoolsproject/'
         'issues/2281#issuecomment-1')
ROUTE_NAMES = ('agy', 'claude', 'gw-agent')


class _TestRoute(DeveloperRoute):
    """Minimal adapter: only the shared request validation is under test."""

    def __init__(self, name):
        super().__init__({})
        self._name = name

    @property
    def name(self):
        return self._name

    @property
    def worker_key(self):
        return self._name

    @property
    def family(self):
        return 'test'

    def validate_profile(self, model, effort):
        if model != 'test-model' or effort != 'test-effort':
            raise RouteError('TEST_PROFILE_INVALID')
        return model, effort

    def build_argv(self, executable, request):
        return []

    def build_env(self, tmpdir):
        return {}

    def parse_output(self, stdout, stderr, log_text):
        return {}

    def verified_model(self, log_text, requested):
        return requested

    def quota_exhausted(self, parsed, stderr):
        return False


class RouteWorkflowGrantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self._git('init', '-q')
        (self.root / 'README.md').write_text('fixture\n', encoding='utf-8')
        self._git('add', '--', 'README.md')
        self._git('commit', '-q', '-m', 'fixture')

    def tearDown(self):
        self.temp.cleanup()

    def _git(self, *arguments):
        return subprocess.run(
            ['git', '-C', str(self.root),
             '-c', 'user.name=Workflow Grant Route Test',
             '-c', 'user.email=test@example.invalid',
             '-c', 'commit.gpgsign=false', *arguments],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def _entry(self, paths=None, issue=None, mode='write', owner=OWNER):
        return {
            'issue': ISSUE if issue is None else issue,
            'owner_record_url': owner,
            'mode': mode,
            'paths': list(paths or (PATH,)),
        }

    def _commit(self, entries):
        path = self.root / GRANT_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'schema_version': 1, 'grants': entries}),
                        encoding='utf-8')
        self._git('add', '--', GRANT_PATH)
        self._git('commit', '-q', '-m', 'grant fixture')
        return self._head()

    def _head(self):
        return self._git('rev-parse', 'HEAD').stdout.decode('ascii').strip()

    def _request(self, write_paths=(), read_paths=None, issue=ISSUE,
                 task=None, head=None):
        value = {
            'root': str(self.root),
            'model': 'test-model',
            'effort': 'test-effort',
            'expected_branch': 'main',
            'expected_head': head or self._head(),
            'write_paths': list(write_paths),
            'executable': '/bin/true',
            'issue': issue,
        }
        if read_paths is not None:
            value['read_paths'] = list(read_paths)
        if task is not None:
            value['task'] = task
        return value

    def _validate(self, name='claude', **kwargs):
        return _TestRoute(name).validate_request(self._request(**kwargs))

    def _assert_refused(self, code=None, name='claude', **kwargs):
        with self.assertRaises(RouteError) as caught:
            self._validate(name=name, **kwargs)
        if code is not None:
            self.assertEqual(caught.exception.code, code)
        return caught.exception

    def _assert_grant_refused(self, facts_code=None, name='claude', **kwargs):
        exc = self._assert_refused(WORKFLOW_GRANT_REFUSED, name=name, **kwargs)
        if facts_code is not None:
            self.assertEqual(exc.observed_facts.get('workflow_grant_code'),
                             facts_code)
        return exc

    def test_granted_workflow_path_is_accepted_for_exact_path_and_mode(self):
        self._commit([
            self._entry(mode='write'),
            self._entry([RELEASE], mode='read'),
        ])
        for name in ROUTE_NAMES:
            with self.subTest(route=name, mode='write'):
                value = self._validate(name, write_paths=[PATH])
                self.assertEqual(value['write_paths'], (PATH,))
            with self.subTest(route=name, mode='read'):
                value = self._validate(name, read_paths=[RELEASE])
                self.assertEqual(value['write_paths'], ())
            with self.subTest(route=name, mode='task-read'):
                value = self._validate(
                    name, task={'read_paths': [RELEASE], 'write_paths': []})
                self.assertEqual(value['write_paths'], ())

    def test_granted_path_is_not_accepted_for_other_path_or_mode(self):
        self._commit([
            self._entry(mode='write'),
            self._entry([RELEASE], mode='read'),
        ])
        cases = (
            ('other write path', {'write_paths': [OTHER]}, 'GRANT_NOT_FOUND'),
            ('read mode on write grant', {'read_paths': [PATH]},
             'GRANT_NOT_FOUND'),
            ('write mode on read grant', {'write_paths': [RELEASE]},
             'GRANT_NOT_FOUND'),
            ('other issue', {'write_paths': [PATH], 'issue': ISSUE + 1},
             'GRANT_NOT_FOUND'),
        )
        for label, kwargs, facts_code in cases:
            with self.subTest(case=label):
                self._assert_grant_refused(facts_code=facts_code, **kwargs)

    def test_ungranted_workflow_paths_are_refused_on_every_route(self):
        self._commit([])
        for name in ROUTE_NAMES:
            for mode in ('write', 'read'):
                with self.subTest(route=name, mode=mode):
                    kwargs = ({'write_paths': [PATH]} if mode == 'write'
                              else {'read_paths': [PATH]})
                    self._assert_grant_refused(
                        facts_code='GRANT_NOT_FOUND', name=name, **kwargs)
            with self.subTest(route=name, mode='task-read'):
                # gw-agent carries the scope in its Task record (#2281 W3).
                self._assert_grant_refused(
                    facts_code='GRANT_NOT_FOUND', name=name,
                    task={'read_paths': [PATH], 'write_paths': []})

    def test_path_confusion_and_cross_path_are_refused(self):
        self._commit([self._entry(mode='write')])
        cases = (
            ('case changed', '.github/workflows/CI.yml'),
            ('dot segment', '.github/workflows/../workflows/ci.yml'),
            ('duplicate separator', '.github//workflows/ci.yml'),
            ('extra component', PATH + '/extra'),
            ('directory only', '.github/workflows'),
            ('cross path', OTHER),
        )
        for label, path in cases:
            with self.subTest(case=label):
                self._assert_refused(write_paths=[path])

    def test_symlinked_workflow_component_is_refused(self):
        self._commit([self._entry(mode='write')])
        target = self.root / 'target'
        (target / 'workflows').mkdir(parents=True)
        (target / 'workflows' / 'ci.yml').write_text(
            'name: ci\n', encoding='utf-8')
        (self.root / '.github').symlink_to(target, target_is_directory=True)
        self._assert_grant_refused(
            facts_code='GRANT_PATH_SYMLINK', write_paths=[PATH])

    def test_forged_or_missing_grant_identity_is_refused(self):
        self._commit([self._entry(mode='write')])
        missing = self._request(write_paths=[PATH])
        del missing['issue']
        with self.assertRaises(RouteError) as caught:
            _TestRoute('agy').validate_request(missing)
        self.assertEqual(caught.exception.code, WORKFLOW_GRANT_REFUSED)
        self.assertEqual(caught.exception.observed_facts.get(
            'workflow_grant_code'), 'GRANT_ISSUE_INVALID')
        self._assert_grant_refused(
            facts_code='GRANT_ISSUE_INVALID',
            write_paths=[PATH], task={'write_paths': [PATH]},
            issue='not-an-issue')
        self._assert_grant_refused(
            facts_code='GRANT_BASE_COMMIT_INVALID', write_paths=[PATH],
            task={'write_paths': [PATH]}, head='0' * 40)

    def test_conflicting_grant_identity_is_refused(self):
        self._commit([self._entry(mode='write')])
        self._assert_grant_refused(
            facts_code='GRANT_ISSUE_INVALID', write_paths=[PATH],
            task={'issue': ISSUE + 1})
        self._assert_grant_refused(
            facts_code='GRANT_BASE_COMMIT_INVALID', write_paths=[PATH],
            task={'base_commit': '0' * 40})

    def test_paths_outside_workflows_behave_as_before(self):
        self._commit([])
        value = self._validate(
            write_paths=['tools/llmtool/x.py'],
            read_paths=['docs/readme.md', '.github/other/p.yml',
                        '.github/workflows-other/ci.yml'])
        self.assertEqual(value['write_paths'], ('tools/llmtool/x.py',))
        self._assert_refused('CLAUDE_REQUEST_INVALID', write_paths=['/tmp/x'])
        self._assert_refused('CLAUDE_REQUEST_INVALID', write_paths=['a/../b'])

    def test_grant_is_read_only_through_the_w1_module(self):
        self._commit([])
        allowed = authorization_workflow_grant.WorkflowGrantDecision(
            True, authorization_workflow_grant.WorkflowGrantCode.GRANTED)
        denied = authorization_workflow_grant.WorkflowGrantDecision(
            False, authorization_workflow_grant.WorkflowGrantCode.GRANT_NOT_FOUND)
        with patch.object(
                authorization_workflow_grant, 'evaluate_workflow_grant',
                return_value=allowed) as evaluate:
            self._validate(write_paths=[PATH], read_paths=[RELEASE])
        self.assertEqual(evaluate.call_count, 2)
        self.assertEqual(
            {call.kwargs['mode'] for call in evaluate.call_args_list},
            {'write', 'read'})
        self.assertEqual(
            {call.kwargs['path'] for call in evaluate.call_args_list},
            {PATH, RELEASE})
        with patch.object(
                authorization_workflow_grant, 'evaluate_workflow_grant',
                return_value=denied):
            self._assert_grant_refused(
                facts_code='GRANT_NOT_FOUND', write_paths=[PATH])
        with patch.object(
                authorization_workflow_grant, 'evaluate_workflow_grant',
                return_value=allowed) as evaluate:
            self._validate(write_paths=['tools/llmtool/x.py'],
                           read_paths=['docs/readme.md'])
        evaluate.assert_not_called()


class _DispatchStore:
    """One-record fake store for the dispatch caller's claim and failure writes."""

    def __init__(self, task):
        self.task = copy.deepcopy(task)
        self.writes = 0

    def task_ids(self):
        return [self.task['task_id']]

    def get(self, _task_id):
        return copy.deepcopy(self.task)

    def compare_and_update(self, _task_id, *, expected, **updates):
        if any(self.task.get(key) != value for key, value in expected.items()):
            raise TaskStoreConflict()
        self.writes += 1
        self.task.update(copy.deepcopy(updates))
        return copy.deepcopy(self.task)

    def update(self, _task_id, **updates):
        self.writes += 1
        self.task.update(copy.deepcopy(updates))
        return copy.deepcopy(self.task)

    def d6_bind_compare_and_update(self, task_id, *, executor_task_id,
                                   expected, **updates):
        return self.compare_and_update(task_id, expected=expected, **updates)

    def prepare_direct_dispatch(self, task_id, *, expected_developer, developer):
        return self.compare_and_update(
            task_id, expected={'developer': expected_developer},
            developer=dict(developer, dispatch_boundary='PREPARED'))

    def authorize_direct_spawn(self, task_id, *, expected_developer):
        return self.compare_and_update(
            task_id, expected={'developer': expected_developer},
            developer=dict(expected_developer, dispatch_boundary='STARTING'))


class DispatchCallerWorkflowGrantTests(unittest.TestCase):
    """A0-A2: the dispatch caller passes Task read paths to the route check.

    The AGY and Claude routes carry no Task Issue, so a workflow path stays
    refused (fail closed) even before any grant file is read.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'src.py').write_text('x = 1\n', encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _task(route, task_id):
        acceptance = ['record the bounded result']
        return {
            'schema_version': 2, 'task_id': task_id, 'run_id': 'b' * 32,
            'state': 'PM_RESPONDED', 'response': 'PM response',
            'request': route + ' request',
            'task_binding': {'contract_digest': 'c' * 64},
            'pm_assignment': {'route': route, 'risk': 'LOW',
                              'goal': route + ' goal', 'acceptance': acceptance},
            'pm_route_binding': {'type': 'openai-compatible', 'model': 'pm-model',
                                 'base_url': None},
            'developer': None, 'completion': None,
        }

    def _run(self, route, *, write_workflow=False):
        task_id = 'a' * 32
        task = self._task(route, task_id)
        store = _DispatchStore(task)
        write_paths = [PATH] if write_workflow else ['src.py']
        snapshot = SimpleNamespace(root=str(self.root),
                                   common_dir=str(self.root / '.git'),
                                   branch='task-branch', head='d' * 40, clean=True)
        registration = SimpleNamespace(
            task_id='executor-d6', root=snapshot.root,
            common_dir=snapshot.common_dir, branch=snapshot.branch,
            source_sha=snapshot.head, result_sha=snapshot.head,
            trusted_agent_id=('claude-developer' if route == 'claude' else 'codex'),
            session_ref='session-ref', native_session_id=None)
        args = Namespace(
            task_id=task_id, provider=route, mode='direct', background=False,
            owner_ref=None, context_stdin=False, selection_source='PM',
            acceptance=task['pm_assignment']['acceptance'],
            egress_basis='Approved non-sensitive Task context.',
            executor_task='executor-d6', cwd=snapshot.root,
            read_path=[PATH, 'src.py'], write_path=write_paths, repair_packet=None)
        requests, refusals, launched = [], [], []

        def route_runner(route_object, request, **_kwargs):
            requests.append(request)
            try:
                route_object.validate_request(request)
            except RouteError as exc:
                refusals.append(exc)
                raise
            launched.append(True)
            raise AssertionError('provider launch was reached')

        boundary = SimpleNamespace(
            prepare=lambda *_args: ((), 'f' * 64),
            verify=lambda *_args, **_kwargs: {'verified': True,
                                              'policy_sha256': 'f' * 64})
        config = {'routes': {route: {
            'executable': route, 'denied_dirs': ['.' + route],
            'max_diff_bytes': 64 * 1024}}}
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(
                task_submission_cli.worker_dispatch, 'evaluate',
                return_value=SimpleNamespace(
                    allowed=True, reason_code='OWNER_POLICY_ALLOWED')))
            stack.enter_context(patch.object(
                task_submission_cli.task_completion, 'validate_task_binding'))
            stack.enter_context(patch.object(
                task_submission_cli.task_completion, 'bind_executor_task',
                return_value=(registration, snapshot)))
            # The fake store has no chain history; this test proves the dispatch
            # caller builds the request and the route refuses it before launch.
            stack.enter_context(patch.object(
                task_submission_cli, '_delegate_chain_gate',
                side_effect=lambda _args, _store, _binding, claim: claim()))
            stack.enter_context(patch.object(
                task_submission_cli, '_delegate_d6_binding',
                return_value=(registration, snapshot)))
            stack.enter_context(patch.object(
                task_submission_cli, '_developer_card_prefix',
                return_value=('', 'c' * 64)))
            stack.enter_context(patch.object(
                task_submission_cli, '_remote_receipt_test_policy',
                return_value='LOCAL'))
            stack.enter_context(patch.object(
                task_submission_cli, '_direct_result_callback',
                return_value={'state': 'WAITING_EXECUTOR',
                              'executor_task_id': 'executor-d6'}))
            stack.enter_context(patch.object(task_submission_cli, '_task_view',
                                             return_value={}))
            stack.enter_context(patch.object(task_submission_cli, '_print'))
            stack.enter_context(patch.object(task_submission_cli, 'load_route_config',
                                             return_value=config))
            stack.enter_context(patch.object(task_submission_cli.shutil, 'which',
                                             return_value='/usr/bin/' + route))
            stack.enter_context(patch.object(
                task_submission_cli, '_agy_developer_profile',
                return_value=('test-model', 'high')))
            stack.enter_context(patch.object(
                task_submission_cli, '_claude_developer_profile',
                return_value=('claude-test-1', 'high', '/tmp/claude-config')))
            stack.enter_context(patch.object(task_submission_cli, 'for_platform',
                                             return_value=boundary))
            if route == 'agy':
                status = task_submission_cli.run_task_agy_delegate(
                    args, store=store, route_runner=route_runner)
            else:
                status = task_submission_cli.run_task_claude_delegate(
                    args, store=store, route_runner=route_runner)
        return status, store, requests, refusals, launched

    def test_workflow_paths_are_refused_through_the_dispatch_caller(self):
        for route in ('agy', 'claude'):
            for write_workflow in (False, True):
                with self.subTest(route=route, write_workflow=write_workflow):
                    (status, store, requests, refusals,
                     launched) = self._run(route, write_workflow=write_workflow)
                    self.assertEqual(status, 1)
                    self.assertEqual(launched, [])
                    self.assertEqual(len(requests), 1)
                    self.assertEqual(requests[0]['read_paths'], [PATH, 'src.py'])
                    self.assertEqual(requests[0]['write_paths'],
                                     [PATH] if write_workflow else ['src.py'])
                    self.assertEqual(len(refusals), 1)
                    self.assertEqual(refusals[0].code, WORKFLOW_GRANT_REFUSED)
                    self.assertEqual(
                        refusals[0].observed_facts.get('workflow_grant_code'),
                        'GRANT_ISSUE_INVALID')
                    self.assertEqual(store.task['developer']['error_code'],
                                     WORKFLOW_GRANT_REFUSED)


if __name__ == '__main__':
    unittest.main()
