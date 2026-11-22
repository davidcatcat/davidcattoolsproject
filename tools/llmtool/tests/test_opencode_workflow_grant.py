import copy
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import task_submission_cli
from llmtool.opencode_direct_developer import DirectOpenCodeError, run

GRANT_PATH = 'tools/llmtool/workflow-grants.json'
WORKFLOW = '.github/workflows/ci.yml'
ISSUE = 2281
OWNER_RECORD = ('https://github.com/davidcatcat/davidcattoolsproject/'
                'issues/2281#issuecomment-1')


class _Store:
    def get(self, task_id):
        return {'task_id': task_id, 'run_id': 'b' * 32}


class _DispatchStore:
    """The minimal durable Task API the direct OpenCode delegate uses."""

    def __init__(self, task):
        self.task = copy.deepcopy(task)

    def get(self, task_id):
        if task_id != self.task['task_id']:
            raise AssertionError('unexpected Task read')
        return copy.deepcopy(self.task)

    def d6_bind_compare_and_update(self, task_id, *, executor_task_id, expected,
                                   developer, completion, **kwargs):
        self.task['developer'] = copy.deepcopy(developer)
        self.task['completion'] = copy.deepcopy(completion)
        return copy.deepcopy(self.task)

    def prepare_direct_dispatch(self, task_id, *, expected_developer, developer):
        self.task['developer'] = copy.deepcopy(developer)
        return copy.deepcopy(self.task)

    def authorize_direct_spawn(self, task_id, *, expected_developer):
        return copy.deepcopy(self.task)

    def record_opencode_launch(self, task_id, *, assignment_id, pid, start_time, pgid):
        return copy.deepcopy(self.task)

    def compare_and_update(self, task_id, *, expected, **changes):
        for key in ('developer', 'completion'):
            if key in changes:
                self.task[key] = copy.deepcopy(changes[key])
        return copy.deepcopy(self.task)


class OpenCodeWorkflowGrantTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='llmtool-opencode-grant-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self._git('init', '-q')
        workflow = self.root / WORKFLOW
        workflow.parent.mkdir(parents=True)
        workflow.write_text('name: ci\n', encoding='utf-8')
        (self.root / 'source.py').write_text('VALUE = 1\n', encoding='utf-8')
        self.cli_dir = Path(tempfile.mkdtemp(prefix='llmtool-opencode-grant-cli-'))
        self.addCleanup(shutil.rmtree, self.cli_dir, True)
        self.cli = self._fake_cli()

    def _git(self, *arguments):
        return subprocess.run(
            ['git', '-C', str(self.root),
             '-c', 'user.name=Workflow Grant Test',
             '-c', 'user.email=test@example.invalid',
             '-c', 'commit.gpgsign=false', *arguments],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def _entry(self, mode, path=WORKFLOW, issue=ISSUE):
        return {
            'issue': issue,
            'owner_record_url': OWNER_RECORD,
            'mode': mode,
            'paths': [path],
        }

    def _commit(self, entries):
        path = self.root / GRANT_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'schema_version': 1, 'grants': entries}),
                        encoding='utf-8')
        self._git('add', '--', GRANT_PATH, WORKFLOW, 'source.py')
        self._git('commit', '-q', '-m', 'fixture')
        return self._git('rev-parse', 'HEAD').stdout.decode('ascii').strip()

    def _fake_cli(self, *, expect_read=WORKFLOW, expect_edit=False, edit=False):
        cli = self.cli_dir / 'opencode-fixture'
        expected_read = {'action': 'read', 'resource': expect_read,
                         'effect': 'allow'}
        script = (
            '#!/usr/bin/env python3\n'
            'import json, os, sys\n'
            "if sys.argv[1:] == ['--version']:\n"
            "    sys.stdout.write('opencode v2.0.32\\n')\n"
            '    raise SystemExit(0)\n'
            "cfg = json.load(open(os.environ['OPENCODE_CONFIG'], encoding='utf-8'))\n"
            f"assert {expected_read!r} in cfg['permissions']\n"
        )
        if expect_edit:
            expected_edit = {'action': 'edit', 'resource': expect_read,
                             'effect': 'allow'}
            script += f"assert {expected_edit!r} in cfg['permissions']\n"
        if edit:
            script += (f"open({WORKFLOW!r}, 'w', encoding='utf-8')"
                       ".write('name: ci-updated\\n')\n")
        script += (
            'sys.stdin.buffer.read()\n'
            "sys.stdout.write(json.dumps({'type': 'step_start', "
            "'sessionID': 'ses_Abc123'}) + '\\n')\n"
            'raise SystemExit(0)\n'
        )
        cli.write_text(script)
        cli.chmod(0o700)
        return cli

    def _run_route(self, *, read_paths, write_paths, base_commit, issue=ISSUE):
        with patch('llmtool.opencode_direct_developer.worker_dispatch.authorize_dispatch'), \
                patch('llmtool.opencode_direct_developer.VERSION_TIMEOUT_SECONDS', 120):
            return run(
                root=self.root, read_paths=read_paths, write_paths=write_paths,
                prompt='bounded task prompt', prelaunch_validate=lambda: True,
                dispatch_store=_Store(), dispatch_task_id='a' * 32,
                timeout_seconds=5, executable=str(self.cli),
                workflow_grant_base_commit=base_commit,
                workflow_grant_issue=issue)

    def _assert_refused(self, *, read_paths, write_paths, base_commit, issue=ISSUE):
        with self.assertRaises(DirectOpenCodeError) as caught:
            run(
                root=self.root, read_paths=read_paths, write_paths=write_paths,
                prompt='bounded task prompt', prelaunch_validate=lambda: True,
                timeout_seconds=5, executable=str(self.cli),
                workflow_grant_base_commit=base_commit,
                workflow_grant_issue=issue)
        self.assertEqual(caught.exception.direct_opencode_code,
                         'OPENCODE_SCOPE_INVALID')

    def _dispatch_task(self, *, issue=ISSUE):
        task_id, run_id = 'a' * 32, 'b' * 32
        request = 'Bounded workflow-grant dispatch task.'
        task = task_submission_cli._record(
            task_id, run_id, 'pm-' + 'c' * 32, 'pm-profile', request,
            state='PM_RESPONDED', provider_type='codex-cli', model='pm-model',
            backend='EPHEMERAL_SINGLE_CALL',
            request_sha=hashlib.sha256(request.encode('utf-8')).hexdigest())
        assignment = {'schema_version': 1, 'route': 'opencode', 'risk': 'LOW',
                      'goal': 'Update the granted workflow file.',
                      'acceptance': ['bounded']}
        task.update(
            pm_route_binding={'type': 'codex-cli', 'model': 'pm-model',
                              'base_url': None},
            response=json.dumps({'schema_version': 1, 'summary': 'Bounded.',
                                 'developer_assignment': assignment}),
            pm_assignment=assignment)
        if issue is not None:
            task['issue'] = issue
        return task

    def _dispatch(self, task, read_paths, write_paths, commit):
        branch = self._git('rev-parse', '--abbrev-ref', 'HEAD').stdout.decode(
            'ascii').strip()
        snapshot = SimpleNamespace(
            root=str(self.root), common_dir=str(self.root / '.git'),
            branch=branch, head=commit)
        registration = SimpleNamespace(
            task_id='executor-task-1', trusted_agent_id='opencode-developer',
            session_ref='Executor Session 1', native_session_id=None,
            root=str(self.root), common_dir=str(self.root / '.git'),
            branch=branch, source_sha=commit, result_sha=commit)
        store = _DispatchStore(task)
        args = Namespace(
            task_id=task['task_id'], provider='opencode', selection_source='PM',
            executor_task='executor-task-1', read_path=list(read_paths),
            write_path=list(write_paths), acceptance=['bounded'],
            egress_basis='Owner approved synthetic workflow scope', timeout=None)
        with patch.object(task_submission_cli, '_delegate_d6_binding',
                          return_value=(registration, snapshot)), \
                patch.object(task_submission_cli.task_completion, 'bind_executor_task',
                             return_value=(registration, snapshot)), \
                patch.object(task_submission_cli.task_completion, 'collect_git_snapshot',
                             return_value=snapshot), \
                patch.object(task_submission_cli, '_delegate_chain_gate',
                             new=lambda _args, _store, _binding, claim: claim()), \
                patch.object(task_submission_cli, '_developer_card_prefix',
                             return_value=('', 'd' * 64)), \
                patch.object(task_submission_cli, '_opencode_developer_profile',
                             return_value=(
                                 task_submission_cli.opencode_direct_developer.MODEL, 3)), \
                patch.object(task_submission_cli, '_bootstrap_registered_d6'), \
                patch.object(task_submission_cli, '_refresh_d6_binding_after_bootstrap',
                             return_value=registration), \
                patch.object(task_submission_cli, '_record_skill_delivery'), \
                patch.object(task_submission_cli, '_developer_output_rejection',
                             return_value=None), \
                patch.object(task_submission_cli, '_bind_direct_native_session',
                             return_value=False), \
                patch.object(task_submission_cli, 'run_task_direct_result_event',
                             return_value=0), \
                patch.object(task_submission_cli.worker_dispatch, 'authorize_dispatch'), \
                patch.object(task_submission_cli, '_check_delegate_repair_cap'), \
                patch.object(task_submission_cli, '_auto_direct_replacement',
                             new=lambda args, _store: args), \
                patch.object(task_submission_cli, '_with_quota_observation',
                             new=lambda _store, _task_id, call: call()), \
                patch('llmtool.opencode_direct_developer.shutil.which',
                      return_value=str(self.cli)):
            code = task_submission_cli.run_task_delegate(args, store=store)
        return code, store

    def test_dispatch_caller_grants_read_path(self):
        commit = self._commit([self._entry('read')])
        self.cli = self._fake_cli()
        code, store = self._dispatch(
            self._dispatch_task(), [WORKFLOW, 'source.py'], ['source.py'], commit)
        self.assertEqual(code, 0)
        self.assertEqual(store.task['developer']['state'], 'DEVELOPER_RESPONDED')

    def test_dispatch_caller_grants_write_path(self):
        commit = self._commit([self._entry('read'), self._entry('write')])
        self.cli = self._fake_cli(expect_edit=True, edit=True)
        code, store = self._dispatch(
            self._dispatch_task(), [WORKFLOW, 'source.py'], [WORKFLOW], commit)
        self.assertEqual(code, 0)
        self.assertEqual(store.task['developer']['state'], 'DEVELOPER_RESPONDED')
        self.assertIn('ci-updated', (self.root / WORKFLOW).read_text(encoding='utf-8'))

    def test_dispatch_caller_refuses_missing_issue_or_grant(self):
        grant = self._commit([self._entry('read'), self._entry('write')])
        no_grant = self._commit([])
        cases = (
            ('missing issue', grant, None, WORKFLOW),
            ('missing grant', no_grant, ISSUE, WORKFLOW),
            ('case changed', grant, ISSUE, '.github/workflows/CI.yml'),
            ('prefixed', grant, ISSUE, WORKFLOW + '/extra'),
        )
        for label, commit, issue, path in cases:
            with self.subTest(case=label):
                self.cli = self._fake_cli(expect_read=path)
                code, store = self._dispatch(
                    self._dispatch_task(issue=issue), [path, 'source.py'],
                    ['source.py'], commit)
                self.assertEqual(code, 1)
                self.assertEqual(store.task['developer']['state'],
                                 'DEVELOPER_UNAVAILABLE')
                self.assertEqual(store.task['developer']['error_code'],
                                 'OPENCODE_SCOPE_INVALID')

    def test_granted_read_path_is_accepted(self):
        commit = self._commit([self._entry('read')])
        result = self._run_route(read_paths=[WORKFLOW], write_paths=[],
                                 base_commit=commit)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.source_file_count, 1)
        self.assertEqual(result.source_bytes, len(b'name: ci\n'))

    def test_granted_read_and_write_paths_are_accepted(self):
        commit = self._commit([self._entry('read'), self._entry('write')])
        self.cli = self._fake_cli(expect_edit=True)
        result = self._run_route(read_paths=[WORKFLOW], write_paths=[WORKFLOW],
                                 base_commit=commit)
        self.assertEqual(result.exit_code, 0)

    def test_ordinary_path_still_needs_no_grant(self):
        (self.root / 'source.py').write_text('VALUE = 1\n', encoding='utf-8')
        self.cli = self._fake_cli(expect_read='source.py')
        result = self._run_route(read_paths=['source.py'], write_paths=[],
                                 base_commit=None)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.source_bytes, len(b'VALUE = 1\n'))

    def test_read_grant_does_not_authorize_write(self):
        commit = self._commit([self._entry('read')])
        self._assert_refused(read_paths=[WORKFLOW], write_paths=[WORKFLOW],
                             base_commit=commit)

    def test_write_grant_alone_does_not_authorize_read(self):
        commit = self._commit([self._entry('write')])
        self._assert_refused(read_paths=[WORKFLOW], write_paths=[WORKFLOW],
                             base_commit=commit)
        self._assert_refused(read_paths=[WORKFLOW], write_paths=[],
                             base_commit=commit)

    def test_ungranted_and_mismatched_paths_are_refused(self):
        commit = self._commit([self._entry('read'), self._entry('write')])
        no_grants = self._commit([])
        cases = (
            ('no grants', no_grants, WORKFLOW),
            ('other path', commit, '.github/workflows/release.yml'),
            ('case changed', commit, '.github/workflows/CI.yml'),
            ('nested prefix', commit, WORKFLOW + '/extra'),
            ('directory', commit, '.github/workflows'),
        )
        for label, base_commit, path in cases:
            with self.subTest(case=label):
                self._assert_refused(read_paths=[path], write_paths=[],
                                     base_commit=base_commit)
        self._assert_refused(read_paths=[WORKFLOW], write_paths=[],
                             base_commit=commit, issue=ISSUE + 1)

    def test_missing_or_invalid_grant_identity_is_refused(self):
        commit = self._commit([self._entry('read')])
        for base_commit in (None, '0' * 40, 'not-a-commit'):
            with self.subTest(base_commit=base_commit):
                self._assert_refused(read_paths=[WORKFLOW], write_paths=[],
                                     base_commit=base_commit)
        self._assert_refused(read_paths=[WORKFLOW], write_paths=[],
                             base_commit=commit, issue=None)

    def test_symlinked_workflow_directory_is_refused(self):
        commit = self._commit([self._entry('read')])
        target = self.root / 'target'
        (target / 'workflows').mkdir(parents=True)
        (target / 'workflows' / 'ci.yml').write_text('name: ci\n',
                                                     encoding='utf-8')
        shutil.rmtree(self.root / '.github')
        (self.root / '.github').symlink_to(target, target_is_directory=True)
        self._assert_refused(read_paths=[WORKFLOW], write_paths=[],
                             base_commit=commit)


if __name__ == '__main__':
    unittest.main()
