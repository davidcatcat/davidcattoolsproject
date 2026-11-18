import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool.opencode_direct_developer import DirectOpenCodeError, run
from llmtool.task_submission_cli import run_task_opencode_delegate

GRANT_PATH = 'tools/llmtool/workflow-grants.json'
WORKFLOW = '.github/workflows/ci.yml'
ISSUE = 2281
OWNER_RECORD = ('https://github.com/davidcatcat/davidcattoolsproject/'
                'issues/2281#issuecomment-1')


class _Store:
    def get(self, task_id):
        return {'task_id': task_id, 'run_id': 'b' * 32}


class _DelegateStore:
    def __init__(self, record):
        self.record = json.loads(json.dumps(record))

    def get(self, task_id):
        return json.loads(json.dumps(self.record))

    def d6_bind_compare_and_update(self, task_id, executor_task_id, expected,
                                   developer, completion):
        self.record['developer'] = json.loads(json.dumps(developer))
        self.record['completion'] = json.loads(json.dumps(completion))
        return json.loads(json.dumps(self.record))

    def update(self, task_id, developer=None, **kwargs):
        if developer is not None:
            self.record['developer'] = json.loads(json.dumps(developer))
        for k, v in kwargs.items():
            self.record[k] = json.loads(json.dumps(v))
        return json.loads(json.dumps(self.record))

    def record_opencode_launch(self, task_id, assignment_id, pid, start_time, pgid):
        if self.record.get('developer'):
            self.record['developer']['execution'] = {
                'child_pid': pid, 'child_start_time': start_time, 'child_pgid': pgid,
            }
        return json.loads(json.dumps(self.record))

    def compare_and_update(self, task_id, expected, **kwargs):
        for k, v in kwargs.items():
            self.record[k] = json.loads(json.dumps(v))
        return json.loads(json.dumps(self.record))


class _DelegateArgs:
    def __init__(self, task_id, *, read_paths, write_paths):
        self.task_id = task_id
        self.selection_source = 'PM'
        self.acceptance = ['test acceptance item']
        self.read_path = list(read_paths)
        self.write_path = list(write_paths)
        self.egress_basis = 'test egress basis'
        self.timeout = None
        self.context_stdin = False
        self.repair_packet = None


class OpenCodeWorkflowGrantTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='llmtool-opencode-grant-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self._git('init', '-q')
        workflow = self.root / WORKFLOW
        workflow.parent.mkdir(parents=True)
        workflow.write_text('name: ci\n', encoding='utf-8')
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
        self._git('add', '--', GRANT_PATH)
        self._git('commit', '-q', '-m', 'fixture')
        return self._git('rev-parse', 'HEAD').stdout.decode('ascii').strip()

    def _fake_cli(self, *, expect_read=WORKFLOW, expect_edit=False, mutate_path=None):
        cli = self.root / 'opencode-fixture'
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
        if mutate_path is not None:
            script += (
                f"with open({str(self.root / mutate_path)!r}, 'a', encoding='utf-8') as f:\n"
                "    f.write('# updated\\n')\n"
            )
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

    def _run_delegate(self, *, read_paths, write_paths, base_commit, issue=ISSUE):
        task = {
            'schema_version': 2,
            'task_id': 'a' * 32,
            'run_id': 'b' * 32,
            'pm_session_id': 'sess-' + 'c' * 27,
            'pm_provider_profile': 'opencode',
            'pm_provider_type': 'opencode-cli',
            'pm_model': 'opencode/deepseek-v4.1-flash-free',
            'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
            'native_session_id': 'ses_Abc123',
            'state': 'PM_RESPONDED',
            'created_at': '2026-11-18T00:00:00Z',
            'updated_at': '2026-11-18T00:00:00Z',
            'task_contract': {'schema_version': 1, 'task_id': 'a' * 32, 'issue': issue},
            'task_binding': {'contract_digest': 'd' * 64},
            'request': 'bounded test goal',
            'request_sha256': 'e' * 64,
            'response': json.dumps({'decision': 'CONTINUE_INTEGRATION_GATES', 'summary': 'ok'}),
            'usage': None,
            'error_code': None,
            'record_sha256': 'f' * 64,
            'developer': None,
            'completion': None,
            'pm_assignment': {
                'route': 'opencode',
                'risk': 'LOW',
                'goal': 'bounded test goal',
                'acceptance': ['test acceptance item'],
            },
            'issue': issue,
            'base_commit': base_commit,
        }
        store = _DelegateStore(task)
        args = _DelegateArgs(task['task_id'], read_paths=read_paths, write_paths=write_paths)
        head_commit = base_commit if (base_commit and len(base_commit) == 40) else '0' * 40
        registration = SimpleNamespace(
            task_id='d6-' + '1' * 29,
            trusted_agent_id='opencode-developer',
            root=str(self.root),
            common_dir=str(self.root),
            branch='main',
            session_ref='sess-1',
            native_session_id='ses_Abc123',
            result_sha=None,
            source_sha=head_commit,
        )
        snapshot = SimpleNamespace(
            head=head_commit,
            root=str(self.root),
            common_dir=str(self.root),
            branch='main',
            clean=True,
        )
        with patch('llmtool.task_submission_cli._developer_card_prefix',
                   return_value=('prefix\n', '1' * 64)), \
             patch('llmtool.task_submission_cli._bootstrap_registered_d6'), \
             patch('llmtool.task_submission_cli._refresh_d6_binding_after_bootstrap',
                   side_effect=lambda reg, *a, **k: reg), \
             patch('llmtool.task_submission_cli._delegate_chain_gate',
                   side_effect=lambda a, s, b, cb: cb()), \
             patch('llmtool.task_submission_cli.task_completion.bind_executor_task',
                   return_value=(registration, snapshot)), \
             patch('llmtool.task_submission_cli._record_skill_delivery'), \
             patch('llmtool.task_submission_cli.run_task_direct_result_event',
                   return_value=0), \
             patch('llmtool.task_submission_cli._opencode_developer_profile',
                   return_value=('opencode/deepseek-v4.1-flash-free', 10)), \
             patch('llmtool.opencode_direct_developer.worker_dispatch.authorize_dispatch'), \
             patch('llmtool.opencode_direct_developer.VERSION_TIMEOUT_SECONDS', 120), \
             patch('shutil.which', return_value=str(self.cli)), \
             patch('sys.stdout', new_callable=io.StringIO):
            exit_code = run_task_opencode_delegate(
                args, store=store, d6_binding=(registration, snapshot))
        return exit_code, store.get(task['task_id'])

    def test_delegate_entry_granted_workflow_is_accepted(self):
        commit = self._commit([self._entry('read'), self._entry('write')])
        self.cli = self._fake_cli(expect_edit=True, mutate_path=WORKFLOW)
        exit_code, saved = self._run_delegate(
            read_paths=[WORKFLOW], write_paths=[WORKFLOW], base_commit=commit)
        self.assertEqual(exit_code, 0)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_RESPONDED')
        self.assertIsNone(saved['developer']['error_code'])

    def test_delegate_entry_granted_read_workflow_is_accepted(self):
        (self.root / 'source.py').write_text('VALUE = 1\n', encoding='utf-8')
        commit = self._commit([self._entry('read')])
        self.cli = self._fake_cli(expect_read=WORKFLOW, expect_edit=False,
                                  mutate_path='source.py')
        exit_code, saved = self._run_delegate(
            read_paths=[WORKFLOW, 'source.py'], write_paths=['source.py'],
            base_commit=commit)
        self.assertEqual(exit_code, 0)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_RESPONDED')
        self.assertIsNone(saved['developer']['error_code'])

    def test_delegate_entry_ungranted_workflow_is_refused(self):
        commit = self._commit([])
        self.cli = self._fake_cli(expect_edit=True)
        exit_code, saved = self._run_delegate(
            read_paths=[WORKFLOW], write_paths=[WORKFLOW], base_commit=commit)
        self.assertEqual(exit_code, 1)
        self.assertEqual(saved['developer']['error_code'], 'OPENCODE_SCOPE_INVALID')

    def test_delegate_entry_mismatched_issue_is_refused(self):
        commit = self._commit([self._entry('read'), self._entry('write')])
        self.cli = self._fake_cli(expect_edit=True)
        exit_code, saved = self._run_delegate(
            read_paths=[WORKFLOW], write_paths=[WORKFLOW], base_commit=commit,
            issue=ISSUE + 1)
        self.assertEqual(exit_code, 1)
        self.assertEqual(saved['developer']['error_code'], 'OPENCODE_SCOPE_INVALID')


if __name__ == '__main__':
    unittest.main()
