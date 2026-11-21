import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool.opencode_direct_developer import DirectOpenCodeError, run

GRANT_PATH = 'tools/llmtool/workflow-grants.json'
WORKFLOW = '.github/workflows/ci.yml'
ISSUE = 2281
OWNER_RECORD = ('https://github.com/davidcatcat/davidcattoolsproject/'
                'issues/2281#issuecomment-1')


class _Store:
    def get(self, task_id):
        return {'task_id': task_id, 'run_id': 'b' * 32}


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

    def _fake_cli(self, *, expect_read=WORKFLOW, expect_edit=False):
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


if __name__ == '__main__':
    unittest.main()
