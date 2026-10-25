import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from llmtool import task_completion
from llmtool.task_store import TaskStore, new_id
from llmtool.task_submission_cli import _record, run_task_codex_delegate


class CodexDelegateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='task-codex-delegate-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / 'repo'
        self.root = self.base / 'executor'
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        self.store = TaskStore(self.base / 'tasks')
        self._registered_worktree()

    def _git(self, *args, cwd=None):
        return subprocess.run(['/usr/bin/git', *args], cwd=cwd or self.root,
            check=True, capture_output=True, text=True).stdout.strip()

    @staticmethod
    def _completion_git(root, args, *, limit=1024 * 1024):
        # Apple Git calls confstr for its temp path; retain the normal Git
        # reader's fixed environment while supplying this test process's valid
        # temporary directory so stderr remains a meaningful failure signal.
        env = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'HOME': os.environ['HOME'],
            'TMPDIR': os.environ['TMPDIR'], 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_ATTR_NOSYSTEM': '1',
            'GIT_OPTIONAL_LOCKS': '0', 'GIT_NO_LAZY_FETCH': '1',
            'GIT_TERMINAL_PROMPT': '0', 'GIT_PAGER': 'cat', 'PAGER': 'cat',
            'GIT_LITERAL_PATHSPECS': '1'}
        safe = ('-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
            '-c', 'core.untrackedCache=false', '-c', 'core.attributesFile=/dev/null',
            '-c', 'core.pager=cat', '-c', 'color.ui=false', '-c', 'protocol.allow=never')
        process = subprocess.run(['/usr/bin/git', '--no-pager', *safe, *args],
            cwd=root, env=env, stdin=subprocess.DEVNULL, capture_output=True, check=False)
        if process.returncode or process.stderr or len(process.stdout) > limit:
            raise AssertionError('fixture Git read failed')
        return process.stdout

    def _registered_worktree(self):
        self.repo.mkdir()
        self._git('init', '-b', 'main', cwd=self.repo)
        self._git('config', 'user.name', 'Fixture', cwd=self.repo)
        self._git('config', 'user.email', 'fixture@example.invalid', cwd=self.repo)
        (self.repo / 'src.py').write_text('VALUE = 1\n')
        llmtool = self.repo / 'tools' / 'llmtool'
        llmtool.mkdir(parents=True)
        (llmtool / '.gitignore').write_text('*.pyc\n/.d6-bootstrap-report.json\n')
        report = {'result': 'PASS', 'task_id': 'executor-task-1',
            'root_realpath': str(self.root), 'head_sha': None,
            'tooling_sha': None, 'cache_fingerprint': 'cpython3.13-darwin-arm64-012345abcdef'}
        report_path = llmtool / '.d6-bootstrap-report.json'
        report_path.write_text('{}\n')
        self._git('add', 'src.py', 'tools/llmtool/.gitignore', cwd=self.repo)
        self._git('commit', '-m', 'fixture base', cwd=self.repo)
        self.source_sha = self._git('rev-parse', 'HEAD', cwd=self.repo)
        self._git('worktree', 'add', '-b', 'feat/codex-delegate', str(self.root),
                  self.source_sha, cwd=self.repo)
        os.chmod(self.root, 0o700)
        # Add the owner-only Env2 report after worktree creation; the bootstrap
        # callback is stubbed, while the registration reader validates its hash.
        report.update(head_sha=self.source_sha, tooling_sha=self.source_sha)
        report_bytes = (json.dumps(report, sort_keys=True, separators=(',', ':')) + '\n').encode()
        report_path = self.root / 'tools' / 'llmtool' / '.d6-bootstrap-report.json'
        report_path.write_bytes(report_bytes)
        report_path.chmod(0o600)
        common_dir = str((self.repo / '.git').resolve())
        registry_dir = Path(common_dir) / 'llmtool-worktrees'
        registry_dir.mkdir(mode=0o700)
        (registry_dir / '.lifecycle.lock').touch(mode=0o600)
        root_stat = self.root.stat()
        registry = {'schema_version': 1, 'task_id': 'executor-task-1',
            'trusted_agent_id': 'executor-agent', 'session_ref': 'executor-session',
            'root_realpath': str(self.root),
            'physical_identity': {'device': root_stat.st_dev, 'inode': root_stat.st_ino,
                                  'uid': root_stat.st_uid},
            'git_common_dir': common_dir, 'branch': 'feat/codex-delegate',
            'base_sha': self.source_sha, 'head_sha': self.source_sha,
            'state': 'ACTIVE', 'lock_state': 'LOCKED', 'native_session_id': 'executor-native',
            'env2_approved_tooling_sha': self.source_sha,
            'env2_pass_marker': {'schema_version': 1, 'result': 'PASS',
                'task_id': 'executor-task-1', 'root_realpath': str(self.root),
                'submitted_head_sha': self.source_sha, 'current_head_sha': self.source_sha,
                'environment_fingerprint': report['cache_fingerprint'],
                'tooling_sha': self.source_sha,
                'report_sha256': hashlib.sha256(report_bytes).hexdigest()}}
        record = registry_dir / 'executor-task-1.json'
        record.write_text(json.dumps(registry), encoding='utf-8')
        record.chmod(0o600)
        self.common_dir = common_dir

    def _task(self):
        request = 'Codex direct fixture task'
        task = _record(new_id(), new_id(), 'pm-' + new_id(), 'pm-profile', request,
            state='PM_RESPONDED', provider_type='openai-compatible', model='pm-model',
            backend='NO_NATIVE_SESSION_ID_EXPOSED',
            request_sha=hashlib.sha256(request.encode()).hexdigest())
        task.update(response='PM bounded task.',
            pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                              'base_url': None},
            pm_assignment={'schema_version': 1, 'route': 'codex', 'risk': 'LOW',
                'goal': 'Edit src.py.', 'acceptance': ['src.py is changed']})
        self.store.create(task)
        return task

    def _fake_codex(self, *, ignored=False):
        final = {'status': 'DONE', 'summary': 'updated', 'tests': [],
                 'self_review': 'checked', 'blocked_reason': None}
        events = '\n'.join((
            json.dumps({'type': 'thread.started',
                        'thread_id': '12345678-1234-4234-8234-123456789abc'}),
            json.dumps({'type': 'item.completed', 'item': {
                'type': 'agent_message', 'text': json.dumps(final)}}),
            json.dumps({'type': 'turn.completed', 'usage': {
                'input_tokens': 2, 'output_tokens': 3}}), ''))
        script = self.bin / 'codex'
        source_path = str(self.root / 'src.py')
        script_body = ('#!' + os.sys.executable + '\nfrom pathlib import Path\nimport sys\n' +
            'Path(' + repr(source_path) + ").write_text('VALUE = 2\\n')\n")
        if ignored:
            ignored_path = str(self.root / 'tools' / 'llmtool' / 'unittest.pyc')
            script_body += 'Path(' + repr(ignored_path) + ").write_bytes(b'forged-bytecode')\n"
        script_body += 'sys.stdout.write(' + repr(events) + ')\nsys.stdout.flush()\n'
        script.write_text(script_body, encoding='utf-8')
        script.chmod(0o700)
        return script

    def _dispatch(self, task, *, ignored=False):
        self._fake_codex(ignored=ignored)
        args = Namespace(task_id=task['task_id'], selection_source='PM',
            acceptance=['src.py is changed'], executor_task='executor-task-1',
            read_path=['src.py'], write_path=['src.py'],
            egress_basis='approved bounded fixture')
        original_cwd = os.getcwd()
        try:
            os.chdir(self.root)
            with patch.dict(os.environ, {'PATH': str(self.bin) + os.pathsep + os.environ['PATH']}), \
                    patch('llmtool.task_submission_cli._codex_developer_profile',
                          return_value=({'model': 'gpt-6-luna'}, 'high')), \
                    patch('llmtool.task_submission_cli._bootstrap_registered_d6'), \
                    patch('llmtool.codex_direct_developer.worker_dispatch.authorize_dispatch'), \
                    patch.object(task_completion, '_git', side_effect=self._completion_git), \
                    patch('llmtool.task_submission_cli.run_task_direct_result_event', return_value=0) as callback:
                result = run_task_codex_delegate(args, store=self.store)
        finally:
            os.chdir(original_cwd)
        return result, self.store.get(task['task_id']), callback

    def test_dirty_registered_worktree_keeps_identity_and_runs_callback(self):
        task = self._task()
        result, saved, callback = self._dispatch(task)
        self.assertEqual(result, 0)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_RESPONDED')
        self.assertIn('VALUE = 2', saved['developer']['artifact'])
        callback.assert_called_once()

    def test_new_ignored_bytecode_is_typed_output_rejection(self):
        task = self._task()
        result, saved, _callback = self._dispatch(task, ignored=True)
        self.assertEqual(result, 1)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_OUTPUT_REJECTED')
        self.assertEqual(saved['developer']['error_code'], 'DEVELOPER_OUTPUT_IGNORED_FILES')


if __name__ == '__main__':
    unittest.main()
