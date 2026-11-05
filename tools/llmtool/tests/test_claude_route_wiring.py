import contextlib
import unittest
from argparse import ArgumentParser, Namespace
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import task_submission_cli
from llmtool.developer_routes import RouteError
from llmtool.errors import LLMToolError


class ClaudeRouteWiringTests(unittest.TestCase):
    def _setup(self, *, trusted_agent='claude-developer', runner_error=None,
               changed_paths=('src.py',), boundary_digest='f' * 64):
        task_id = 'a' * 32
        executor_id = 'executor-d6'
        task = {
            'schema_version': 2, 'task_id': task_id, 'run_id': 'b' * 32,
            'state': 'PM_RESPONDED', 'response': 'PM response', 'request': 'Claude request',
            'task_binding': {'contract_digest': 'c' * 64},
            'pm_assignment': {'route': 'claude', 'risk': 'LOW', 'goal': 'Claude goal',
                              'acceptance': ['record the bounded result']},
            'pm_route_binding': {'type': 'openai-compatible', 'model': 'pm-model',
                                 'base_url': None},
            'developer': None, 'completion': None,
        }

        class Store:
            def __init__(self):
                self.task = dict(task)
                self.writes = 0

            def get(self, _task_id):
                return dict(self.task)

            def compare_and_update(self, _task_id, *, expected, **updates):
                self.writes += 1
                self.task.update(updates)
                return dict(self.task)

            def update(self, _task_id, **updates):
                self.writes += 1
                self.task.update(updates)
                return dict(self.task)

        store = Store()
        snapshot = SimpleNamespace(root='/registered/root', common_dir='/repo/.git',
                                   branch='task-branch', head='d' * 40, clean=True)
        registration = SimpleNamespace(task_id=executor_id, root=snapshot.root,
            common_dir=snapshot.common_dir, branch=snapshot.branch, result_sha=snapshot.head,
            trusted_agent_id=trusted_agent, session_ref='session-ref', native_session_id=None)
        result = SimpleNamespace(session_id='12345678-1234-4234-8234-123456789abc',
            exit_code=0, stdout_sha256='e' * 64, elapsed_ms=42,
            cleanup_status='CONFIRMED',
            usage={'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5})
        allowed = SimpleNamespace(allowed=True, reason_code='OWNER_ASSIGNMENT_MATCHED')
        evaluations = []
        prompts = []
        requests = []

        def route_runner(_route, request, *, gate, **_kwargs):
            requests.append(request)
            prompts.append(request['prompt'])
            self.assertTrue(gate().allowed)
            if runner_error is not None:
                raise runner_error
            return result

        boundary = SimpleNamespace(
            prepare=lambda *_args: ((), boundary_digest),
            verify=lambda *_args, **_kwargs: {'verified': True,
                                               'policy_sha256': boundary_digest})
        callback = {'state': 'WAITING_EXECUTOR', 'executor_task_id': executor_id}
        args = Namespace(task_id=task_id, provider='claude', mode='direct',
            background=False, owner_ref='https://github.com/davidcatcat/davidcattoolsproject/issues/289#issuecomment-1',
            context_stdin=False, selection_source='PM', acceptance=['record the bounded result'],
            egress_basis='Approved non-sensitive Task context.', executor_task=executor_id,
            cwd=snapshot.root, read_path=['src.py'], write_path=['src.py'], repair_packet=None)

        class PolicyBoundary:
            def prepare(self, root, paths, state_dirs=()):
                return boundary.prepare(root, paths, state_dirs)

            def verify(self, root, paths, state_dirs=(), *, runner):
                return boundary.verify(root, paths, state_dirs, runner=runner)

        def evaluate(*call_args):
            evaluations.append(call_args)
            return allowed

        patches = [
            patch.object(task_submission_cli.worker_dispatch, 'evaluate', side_effect=evaluate),
            patch.object(task_submission_cli.task_completion, 'validate_task_binding'),
            patch.object(task_submission_cli.task_completion, 'collect_git_snapshot', return_value=snapshot),
            patch.object(task_submission_cli.task_completion, 'bind_executor_task',
                         return_value=(registration, snapshot)),
            patch.object(task_submission_cli.task_completion, 'read_d6_registration',
                         return_value=registration),
            patch.object(task_submission_cli.task_completion, '_worktree_diff',
                         return_value=(list(changed_paths), b'diff --git a/src.py b/src.py\n')),
            patch.object(task_submission_cli, '_assignment_paths',
                         return_value=(['src.py'], ['src.py'])),
            patch.object(task_submission_cli, '_direct_result_callback', return_value=callback),
            patch.object(task_submission_cli, '_remote_receipt_test_policy', return_value='LOCAL'),
            patch.object(task_submission_cli, 'load_route_config', return_value={
                'routes': {'claude': {'default_model': 'claude-test-1',
                    'default_effort': 'high', 'denied_dirs': ['.claude']}}}),
            patch.object(task_submission_cli.role_config, 'resolve_role', return_value={
                'model': None, 'effort': None, 'config_dir': '/tmp/claude-config'}),
            patch.object(task_submission_cli.shutil, 'which', return_value='/usr/bin/claude'),
            patch.object(task_submission_cli, 'for_platform', return_value=PolicyBoundary()),
            patch.object(task_submission_cli, '_task_view', return_value={}),
            patch.object(task_submission_cli, 'run_task_direct_result_event', return_value=0),
        ]
        return task_submission_cli, store, args, registration, evaluations, prompts, requests, patches, route_runner

    def test_success_records_cli_result_and_uses_d6_policy_binding(self):
        cli, store, args, registration, evaluations, prompts, requests, patches, runner = self._setup()
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            status = cli.run_task_claude_delegate(args, store=store, route_runner=runner)
        developer = store.task['developer']
        self.assertEqual(status, 0)
        self.assertEqual(developer['provider_type'], 'claude-cli')
        self.assertEqual(developer['backend_session_semantics'], 'CLI_REPORTED_SESSION_ID')
        self.assertEqual(developer['executor_task_id'], registration.task_id)
        self.assertEqual(developer['usage']['total_tokens'], 5)
        self.assertEqual(developer['artifact'], 'diff --git a/src.py b/src.py\n')
        self.assertTrue(all(call[0] == 'claude-developer' and call[2] == registration.task_id
                            for call in evaluations))
        self.assertTrue(all('log_path' not in request for request in requests))
        self.assertIn('Claude Developer', prompts[0])

    def test_bad_d6_identity_refuses_before_store_write(self):
        cli, store, args, _registration, _evaluations, _prompts, _requests, patches, runner = self._setup(
            trusted_agent='codex-developer')
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            with self.assertRaisesRegex(LLMToolError, 'D6_CLAUDE_AGENT_REQUIRED'):
                cli.run_task_claude_delegate(args, store=store, route_runner=runner)
        self.assertEqual(store.writes, 0)

    def test_background_is_refused_by_delegate_wiring(self):
        cli, _store, args, _registration, _evaluations, _prompts, _requests, _patches, _runner = self._setup()
        args.background = True
        args.provider = 'claude'
        args.owner_ref = 'https://github.com/davidcatcat/davidcattoolsproject/issues/289#issuecomment-1'
        with patch.object(cli, '_validate_acceptance_scope'), \
                patch.object(cli, '_check_delegate_repair_cap'), \
                patch.object(cli, 'run_task_claude_delegate', side_effect=AssertionError):
            with self.assertRaises(LLMToolError):
                cli.run_task_delegate(args, store=object())

    def test_parser_accepts_claude_provider(self):
        parser = ArgumentParser()
        actions = parser.add_subparsers(dest='action')
        task_submission_cli.add_task_submission_parsers(actions)
        args = parser.parse_args(['delegate', 'a' * 32, '--provider', 'claude',
            '--mode', 'direct', '--selection-source', 'PM', '--executor-task', 'd6',
            '--read-path', 'src.py', '--write-path', 'src.py', '--acceptance', 'done',
            '--egress-basis', 'approved', '--owner-ref',
            'https://github.com/davidcatcat/davidcattoolsproject/issues/289#issuecomment-1'])
        self.assertEqual(args.provider, 'claude')


if __name__ == '__main__':
    unittest.main()
