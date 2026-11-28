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
from llmtool.errors import LLMToolError
from llmtool.opencode_direct_developer import DirectResult
from llmtool.task_store import TaskStoreConflict

GRANT_PATH = 'tools/llmtool/workflow-grants.json'
PATH = '.github/workflows/ci.yml'
OTHER = '.github/workflows/other.yml'
RELEASE = '.github/workflows/release.yml'
ISSUE = 2281
OWNER = ('https://github.com/davidcatcat/davidcattoolsproject/'
         'issues/2281#issuecomment-1')
ROUTE_NAMES = ('agy', 'claude', 'gw-agent')


def _grant_entry(paths=None, issue=None, mode='write', owner=OWNER):
    return {
        'issue': ISSUE if issue is None else issue,
        'owner_record_url': owner,
        'mode': mode,
        'paths': list(paths or (PATH,)),
    }


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
        return _grant_entry(paths=paths, issue=issue, mode=mode, owner=owner)

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
    """#2942: all four dispatch callers refuse disallowed workflow paths pre-write.

    Refusing before any durable Task write leaves no Developer assignment on the
    Task and leaves the D6 unchanged, so a corrected re-delegate of the same
    Task is admitted. OpenCode honours Issue-bound grants; AGY, Claude and
    gw-agent refuse every .github/workflows path, granted or not (#2281 W3).
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self._git('init', '-q')
        (self.root / 'src.py').write_text('x = 1\n', encoding='utf-8')
        self._git('add', '--', 'src.py')
        self._git('commit', '-q', '-m', 'fixture')

    def tearDown(self):
        self.temp.cleanup()

    def _git(self, *arguments):
        return subprocess.run(
            ['git', '-C', str(self.root),
             '-c', 'user.name=Workflow Grant Dispatch Test',
             '-c', 'user.email=test@example.invalid',
             '-c', 'commit.gpgsign=false', *arguments],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def _head(self):
        return self._git('rev-parse', 'HEAD').stdout.decode('ascii').strip()

    def _entry(self, paths=None, issue=None, mode='write', owner=OWNER):
        return _grant_entry(paths=paths, issue=issue, mode=mode, owner=owner)

    def _commit_grants(self, entries):
        path = self.root / GRANT_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'schema_version': 1, 'grants': entries}),
                        encoding='utf-8')
        self._git('add', '--', GRANT_PATH)
        self._git('commit', '-q', '-m', 'grant fixture')
        return self._head()

    @staticmethod
    def _task(route, task_id, *, issue=None):
        acceptance = ['record the bounded result']
        record = {
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
        if issue is not None:
            record['issue'] = issue
        return record

    def _run(self, route, *, store=None, read_paths=None, write_paths=None,
             issue=None, head=None):
        task_id = 'a' * 32
        if store is None:
            store = _DispatchStore(self._task(route, task_id, issue=issue))
        store.root = self.root / 'tasks'
        head = head or self._head()
        read_paths = [PATH, 'src.py'] if read_paths is None else list(read_paths)
        write_paths = ['src.py'] if write_paths is None else list(write_paths)
        snapshot = SimpleNamespace(root=str(self.root),
                                   common_dir=str(self.root / '.git'),
                                   branch='task-branch', head=head, clean=True)
        agent_by_route = {
            'agy': 'codex',
            'claude': 'claude-developer',
            'gw-agent': 'codex',
            'opencode': 'opencode-developer',
        }
        registration = SimpleNamespace(
            task_id='executor-d6', root=snapshot.root,
            common_dir=snapshot.common_dir, branch=snapshot.branch,
            source_sha=snapshot.head, result_sha=snapshot.head,
            trusted_agent_id=agent_by_route[route],
            session_ref='session-ref', native_session_id=None)
        args = Namespace(
            task_id=task_id, provider=route,
            mode='proposal' if route == 'opencode' else 'direct',
            background=False,
            owner_ref=None, context_stdin=False, selection_source='PM',
            acceptance=store.task['pm_assignment']['acceptance'],
            egress_basis='Approved non-sensitive Task context.',
            data_class='PUBLIC_REPO',
            executor_task='executor-d6', cwd=snapshot.root,
            read_path=read_paths, write_path=write_paths, repair_packet=None)
        requests, launched = [], []
        model_by_route = {
            'agy': 'gemini-test',
            'claude': 'claude-test-1',
            'gw-agent': 'qwen-authorized',
            'opencode': 'opencode/sample-model-free',
        }
        model = model_by_route[route]

        def route_runner(route_object, request, **_kwargs):
            requests.append(request)
            route_object.validate_request(request)
            launched.append(True)
            return SimpleNamespace(
                session_id='12345678-1234-4234-8234-123456789abc',
                model=model, exit_code=0, summary='updated',
                stderr_excerpt=None, stdout_sha256='e' * 64,
                elapsed_ms=10, cleanup_status='CONFIRMED',
                usage={'prompt_tokens': 1, 'completion_tokens': 1,
                       'total_tokens': 2})

        def opencode_runner(**kwargs):
            requests.append(kwargs)
            launched.append(True)
            return DirectResult(
                0, 'ses_Abc123', 'Updated.', None, 'UNKNOWN', 'a' * 64, 10,
                'CONFIRMED', 'opencode v2.0.32', 10, 1, 'b' * 64)

        boundary = SimpleNamespace(
            prepare=lambda *_args: ((), 'f' * 64),
            verify=lambda *_args, **_kwargs: {'verified': True,
                                              'policy_sha256': 'f' * 64})
        route_entry = {
            'executable': route, 'denied_dirs': ['.' + route],
            'default_model': model, 'allowed_models': [model],
            'default_effort': 'high', 'max_diff_bytes': 64 * 1024,
        }
        if route == 'gw-agent':
            route_entry = {
                'allowed_data_classes': ['synthetic', 'public_repo'],
                'default_model': model, 'allowed_models': [model],
                'default_effort': 'low', 'default_timeout': 10800,
                'max_timeout': 18000, 'package_path': str(self.root),
                'budgets': {
                    'max_turns': 512, 'max_input_tokens': 262144,
                    'max_output_tokens': 65536, 'max_wall_seconds': 10800,
                    'max_tool_result_bytes': 262144,
                    'max_sent_bytes': 16777216,
                },
                'profile': {
                    'data_class_map': {'SYNTHETIC': 'synthetic',
                                       'PUBLIC_REPO': 'public_repo'},
                    'context_cap_tokens': 32768,
                    'max_output_tokens': 8192,
                },
            }
        config = {'routes': {route: route_entry}}
        fake_site = SimpleNamespace(
            is_repair=False,
            route_fields=lambda: {},
            after_result=lambda *_a, **_kw: None,
            __enter__=lambda: fake_site,
            __exit__=lambda *_a: None,
        )
        fake_gw_route = SimpleNamespace(
            validate_profile=lambda *_a: (model, 'low'),
            validate_request=lambda req: (
                task_submission_cli.validate_workflow_grants(req) or req),
            audit_copy_path=str(self.root / 'audit.jsonl'),
            _result={'audit_sha256': 'a' * 64},
        )
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(
                task_submission_cli.worker_dispatch, 'evaluate',
                return_value=SimpleNamespace(
                    allowed=True, reason_code='OWNER_POLICY_ALLOWED')))
            stack.enter_context(patch.object(
                task_submission_cli.worker_dispatch, 'authorize_dispatch'))
            stack.enter_context(patch.object(
                task_submission_cli.task_completion, 'validate_task_binding'))
            stack.enter_context(patch.object(
                task_submission_cli.task_completion, 'bind_executor_task',
                return_value=(registration, snapshot)))
            stack.enter_context(patch.object(
                task_submission_cli.task_completion, 'read_d6_registration',
                return_value=registration))
            stack.enter_context(patch.object(
                task_submission_cli.task_completion, 'collect_git_snapshot',
                return_value=snapshot))
            stack.enter_context(patch.object(
                task_submission_cli.task_completion, '_worktree_diff',
                return_value=(write_paths, b'diff --git a/src.py b/src.py\n')))
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
                task_submission_cli, '_record_skill_delivery'))
            stack.enter_context(patch.object(
                task_submission_cli, '_remote_receipt_test_policy',
                return_value='LOCAL'))
            stack.enter_context(patch.object(
                task_submission_cli, '_direct_result_callback',
                return_value={'state': 'WAITING_EXECUTOR',
                              'executor_task_id': 'executor-d6'}))
            stack.enter_context(patch.object(
                task_submission_cli, '_bind_direct_native_session',
                return_value=False))
            stack.enter_context(patch.object(
                task_submission_cli, '_bind_codex_native_session',
                return_value='SKIPPED'))
            stack.enter_context(patch.object(
                task_submission_cli, 'run_task_direct_result_event',
                return_value=0))
            stack.enter_context(patch.object(task_submission_cli, '_task_view',
                                             return_value={}))
            stack.enter_context(patch.object(task_submission_cli, '_print'))
            stack.enter_context(patch.object(task_submission_cli, 'load_route_config',
                                             return_value=config))
            stack.enter_context(patch.object(task_submission_cli.shutil, 'which',
                                             return_value='/usr/bin/' + route))
            stack.enter_context(patch.object(
                task_submission_cli, '_agy_developer_profile',
                return_value=(model, 'high')))
            stack.enter_context(patch.object(
                task_submission_cli, '_claude_developer_profile',
                return_value=(model, 'high', '/tmp/claude-config')))
            stack.enter_context(patch.object(
                task_submission_cli, '_opencode_developer_profile',
                return_value=(model, 12)))
            stack.enter_context(patch.object(
                task_submission_cli, '_gw_agent_endpoint',
                return_value=({'type': 'openai-compatible', 'model': model,
                               'base_url': 'https://authorized.example'},
                              {'max_tokens': 8192})))
            stack.enter_context(patch.object(
                task_submission_cli, '_gw_agent_developer_profile',
                return_value=(model, 'low')))
            stack.enter_context(patch.object(
                task_submission_cli, 'installed_gateway_stale',
                return_value=False))
            stack.enter_context(patch.object(
                task_submission_cli.repair_policy, 'gw_agent_max_turns',
                return_value=512))
            stack.enter_context(patch.object(
                task_submission_cli.worker_availability, 'gw_agent_refusal',
                return_value=None))
            stack.enter_context(patch('llmtool.gw_agent_sessions.sweep_quietly'))
            stack.enter_context(patch.object(
                task_submission_cli.gw_agent_callsite, 'SessionCallSite',
                return_value=fake_site))
            stack.enter_context(patch.object(
                task_submission_cli, 'GwAgentRoute',
                return_value=fake_gw_route))
            stack.enter_context(patch.object(
                task_submission_cli, 'cleanup_audit_copies'))
            stack.enter_context(patch.object(
                task_submission_cli.skill_ledger, 'record_not_delivered'))
            stack.enter_context(patch.object(
                task_submission_cli, '_bootstrap_registered_d6'))
            stack.enter_context(patch.object(
                task_submission_cli, '_refresh_d6_binding_after_bootstrap',
                return_value=registration))
            stack.enter_context(patch.object(
                task_submission_cli, '_developer_output_rejection',
                return_value=None))
            stack.enter_context(patch.object(
                task_submission_cli.opencode_direct_developer, 'run',
                side_effect=opencode_runner))
            stack.enter_context(patch.object(task_submission_cli, 'for_platform',
                                             return_value=boundary))
            if route == 'agy':
                status = task_submission_cli.run_task_agy_delegate(
                    args, store=store, route_runner=route_runner)
            elif route == 'claude':
                status = task_submission_cli.run_task_claude_delegate(
                    args, store=store, route_runner=route_runner)
            elif route == 'gw-agent':
                status = task_submission_cli.run_task_gw_agent_delegate(
                    args, store=store, route_runner=route_runner)
            else:
                status = task_submission_cli.run_task_opencode_delegate(
                    args, store=store, d6_binding=(registration, snapshot))
        return status, store, requests, launched

    def test_workflow_paths_refused_prewrite_and_corrected_redelegate_admitted(self):
        for route in ('agy', 'opencode', 'gw-agent', 'claude'):
            for write_workflow in (False, True):
                with self.subTest(route=route, write_workflow=write_workflow):
                    store = _DispatchStore(self._task(route, 'a' * 32))
                    bad_read = [PATH, 'src.py']
                    bad_write = [PATH] if write_workflow else ['src.py']
                    with self.assertRaises(LLMToolError) as caught:
                        self._run(route, store=store,
                                  read_paths=bad_read, write_paths=bad_write)
                    self.assertEqual(getattr(caught.exception, 'code', None),
                                     WORKFLOW_GRANT_REFUSED)
                    self.assertIn(WORKFLOW_GRANT_REFUSED, str(caught.exception))
                    self.assertEqual(store.writes, 0)
                    self.assertIsNone(store.task['developer'])
                    self.assertIsNone(store.task['completion'])

                    status, store, requests, launched = self._run(
                        route, store=store,
                        read_paths=['src.py'], write_paths=['src.py'])
                    self.assertEqual(status, 0)
                    self.assertEqual(len(requests), 1)
                    self.assertEqual(requests[0]['read_paths'], ['src.py'])
                    self.assertEqual(requests[0]['write_paths'], ['src.py'])
                    self.assertEqual(launched, [True])
                    self.assertEqual(store.task['developer']['state'],
                                     'DEVELOPER_RESPONDED')

    def test_granted_workflow_paths_admitted_on_opencode_only(self):
        head = self._commit_grants([
            self._entry([PATH, OTHER], issue=ISSUE, mode='read'),
            self._entry([OTHER], issue=ISSUE, mode='write'),
        ])
        status, store, requests, launched = self._run(
            'opencode', read_paths=[PATH, OTHER, 'src.py'],
            write_paths=[OTHER], issue=ISSUE, head=head)
        self.assertEqual(status, 0)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]['read_paths'], [PATH, OTHER, 'src.py'])
        self.assertEqual(requests[0]['write_paths'], [OTHER])
        self.assertEqual(launched, [True])
        self.assertEqual(store.task['developer']['state'],
                         'DEVELOPER_RESPONDED')

    def test_granted_workflow_paths_refused_prewrite_on_agy_claude_and_gw_agent(self):
        head = self._commit_grants([
            self._entry([PATH, OTHER], issue=ISSUE, mode='read'),
            self._entry([OTHER], issue=ISSUE, mode='write'),
        ])
        for route in ('agy', 'claude', 'gw-agent'):
            for mode in ('read', 'write'):
                with self.subTest(route=route, mode=mode):
                    store = _DispatchStore(
                        self._task(route, 'a' * 32, issue=ISSUE))
                    read_paths = ([PATH, 'src.py'] if mode == 'read'
                                  else [OTHER, 'src.py'])
                    write_paths = ([OTHER] if mode == 'write'
                                   else ['src.py'])
                    with self.assertRaises(LLMToolError) as caught:
                        self._run(
                            route, store=store, read_paths=read_paths,
                            write_paths=write_paths, issue=ISSUE, head=head)
                    self.assertEqual(getattr(caught.exception, 'code', None),
                                     WORKFLOW_GRANT_REFUSED)
                    self.assertIn(WORKFLOW_GRANT_REFUSED,
                                  str(caught.exception))
                    self.assertIn('GRANT_ISSUE_INVALID',
                                  str(caught.exception))
                    self.assertEqual(store.writes, 0)
                    self.assertIsNone(store.task['developer'])
                    self.assertIsNone(store.task['completion'])

                    status, store, requests, launched = self._run(
                        route, store=store, read_paths=['src.py'],
                        write_paths=['src.py'], issue=ISSUE, head=head)
                    self.assertEqual(status, 0)
                    self.assertEqual(len(requests), 1)
                    self.assertEqual(requests[0]['read_paths'], ['src.py'])
                    self.assertEqual(requests[0]['write_paths'], ['src.py'])
                    self.assertEqual(launched, [True])
                    self.assertEqual(store.task['developer']['state'],
                                     'DEVELOPER_RESPONDED')


if __name__ == '__main__':
    unittest.main()
